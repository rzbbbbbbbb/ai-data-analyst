"""
AI 智能数据分析助手 — Streamlit 主页面

项目入口文件，将前面所有模块组装成可交互的 Web 应用。
启动方式: streamlit run streamlit_app.py
"""
import streamlit as st
import pandas as pd
import numpy as np
import os
import sys
from datetime import datetime

# 确保 src 目录在 Python 搜索路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import config
from src.data_loader.loader import load_file, save_uploaded_file
from src.data_loader.cleaner import clean_dataframe
from src.database.schema import create_table_from_df, get_data_quality_report
from src.database.connection import execute_sql, get_all_tables
from src.agent.sql_agent import get_agent
from src.insights.generator import InsightsGenerator
from src.visualization.charts import (
    create_chart, suggest_chart_type, result_to_df,
    create_data_table_figure,
)

# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="AI 数据分析助手",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# Session State 初始化
# ============================================================
def _load_history_from_disk():
    """从磁盘加载历史记录（服务器重启后恢复）"""
    import json
    history_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "query_history.json")
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("history_ids", []), data.get("results", {})
        except Exception:
            pass
    return [], {}


def _save_history_to_disk():
    """将历史记录持久化到磁盘"""
    import json
    history_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "query_history.json")
    os.makedirs(os.path.dirname(history_file), exist_ok=True)
    # 每条 query_data 最多保留 500 行，避免文件过大
    results_to_save = {}
    for hid, entry in st.session_state.query_results.items():
        entry_copy = dict(entry)
        qd = entry_copy.get("query_data", [])
        if len(qd) > 500:
            entry_copy["query_data"] = qd[:500]
            entry_copy["_truncated"] = True
        results_to_save[hid] = entry_copy
    try:
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump({
                "history_ids": st.session_state.query_history,
                "results": results_to_save,
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


defaults = {
    "tables": {},               # {table_name: stats_dict}
    "current_table": None,      # 当前选中的表名
    "query_history": [],        # [history_id]  按时间排序的历史 ID 列表
    "query_results": {},        # {history_id: {question, sql, answer, query_data, insight, chart_type, time}}
    "viewing_history_id": None, # 当前正在查看的历史记录 ID
    "last_sql": "",             # 最近一次执行的 SQL（用于自定义 SQL tab 预填）
}
for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val

# 从磁盘恢复历史记录（仅首次加载时）
if "_history_loaded" not in st.session_state:
    st.session_state._history_loaded = True
    saved_ids, saved_results = _load_history_from_disk()
    if saved_ids:
        st.session_state.query_history = saved_ids
        st.session_state.query_results = saved_results


# ============================================================
# 辅助函数
# ============================================================
def create_sample_sales_data():
    """生成销售数据示例"""
    np.random.seed(42)
    n = 500
    df = pd.DataFrame({
        "order_id": range(1, n + 1),
        "order_date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "customer_id": [f"C{np.random.randint(1, 51):03d}" for _ in range(n)],
        "product": np.random.choice(["商品A", "商品B", "商品C", "商品D"], n),
        "category": np.random.choice(["电子产品", "服装", "食品", "家居"], n),
        "quantity": np.random.randint(1, 10, n),
        "unit_price": np.round(np.random.uniform(10, 500, n), 2),
        "region": np.random.choice(["华东", "华北", "华南", "西部"], n),
    })
    df["amount"] = df["quantity"] * df["unit_price"]
    return df


def create_sample_user_data():
    """生成用户行为数据示例"""
    np.random.seed(42)
    n = 300
    df = pd.DataFrame({
        "user_id": [f"U{i:04d}" for i in range(1, n + 1)],
        "register_date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "last_login": pd.date_range("2024-06-01", periods=n, freq="D"),
        "age": np.random.randint(18, 65, n),
        "gender": np.random.choice(["男", "女"], n),
        "city": np.random.choice(["北京", "上海", "广州", "深圳", "杭州"], n),
        "total_orders": np.random.randint(0, 50, n),
        "total_spent": np.round(np.random.uniform(0, 20000, n), 2),
        "is_vip": np.random.choice([True, False], n, p=[0.2, 0.8]),
    })
    return df


def handle_file_upload(uploaded_file):
    """处理上传文件的完整流程：保存 → 加载 → 清洗 → 建表（带进度条）"""
    file_path = save_uploaded_file(uploaded_file)

    # Step 1: 加载文件
    status = st.status("📖 正在读取文件...", expanded=True)
    df_raw = load_file(file_path)
    status.update(label=f"✅ 文件读取完成 ({len(df_raw)} 行 × {len(df_raw.columns)} 列)")

    # Step 2: 清洗数据
    status.update(label="🧹 正在清洗数据...")
    df = clean_dataframe(df_raw)
    status.update(label=f"✅ 清洗完成 ({len(df)} 行)")

    # Step 3: 建表 + 入库（带进度）
    table_name = os.path.splitext(uploaded_file.name)[0]

    progress_bar = st.progress(0, text="🗄️ 正在创建数据库表...")
    status.update(label="🗄️ 正在写入数据库...")

    def on_progress(current, total):
        pct = min(current / total, 1.0)
        progress_bar.progress(pct, text=f"🗄️ 正在写入数据库... {current}/{total} 行")

    stats = create_table_from_df(df, table_name, progress_callback=on_progress)

    progress_bar.progress(1.0, text=f"✅ 入库完成 ({stats['row_count']} 行)")
    status.update(label=f"✅ 数据加载完成: {stats['table_name']}", state="complete")

    st.session_state.tables[table_name] = stats
    st.session_state.current_table = table_name
    return stats


def render_query_result(result: dict):
    """
    渲染一次查询的完整结果：SQL、数据表、图表、AI 洞察。
    可被"新查询"和"查看历史"两个流程复用。
    """
    question = result.get("question", "")
    sql = result.get("sql", "")
    answer = result.get("answer", "")
    query_data = result.get("query_data", [])
    insight = result.get("insight", "")
    chart_type = result.get("chart_type", "bar")

    # ---- 显示问题 ----
    st.caption(f"❓ {question}")

    # ---- 生成的 SQL ----
    st.divider()
    st.subheader("📝 生成的 SQL")
    if sql:
        st.code(sql, language="sql")
        # 一键复制到自定义 SQL tab
        if st.button("📋 复制 SQL 到自定义查询", key=f"copy_sql_{result.get('id', '')}"):
            st.session_state.custom_sql_input = sql
            st.success("✅ 已复制，请切换到「🔍 自定义 SQL」tab 查看")
    else:
        st.caption("（SQL 由 Agent 内部执行）")

    # ---- 数据表格 ----
    st.divider()
    st.subheader("📊 查询结果")

    if query_data:
        df_result = pd.DataFrame(query_data)
        st.dataframe(
            df_result,
            use_container_width=True,
            height=min(400, 35 * len(df_result) + 38),
        )

        # ---- 自动图表 ----
        st.divider()
        st.subheader("📈 数据可视化")

        chart_options = ["bar", "line", "pie", "scatter"]
        default_idx = chart_options.index(chart_type) if chart_type in chart_options else 0
        col_ct1, _, _, _, _ = st.columns(5)
        with col_ct1:
            chart_choice = st.selectbox(
                "图表类型",
                chart_options,
                index=default_idx,
                key=f"chart_type_{result.get('id', 'new')}",
            )

        fig = create_chart(query_data, chart_type=chart_choice)
        st.plotly_chart(fig, use_container_width=True)

        # CSV 导出按钮
        csv = df_result.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 下载结果 CSV",
            csv,
            "query_result.csv",
            "text/csv",
            key=f"dl_{result.get('id', 'new')}",
        )
    elif answer:
        # 没有结构化数据时，显示 Agent 文字回复
        st.markdown(answer)
    else:
        st.info("无返回数据")

    # ---- AI 洞察 ----
    st.divider()
    st.subheader("💡 AI 数据洞察")
    if insight:
        st.markdown(insight)
    else:
        st.info("暂无洞察")


# ============================================================
# 侧边栏
# ============================================================
with st.sidebar:
    st.title("🤖 AI 数据分析助手")

    # API Key 检查
    errors = config.validate()
    if errors:
        for e in errors:
            st.error(f"⚠️ {e}")
        st.stop()

    st.divider()

    # ---- 数据上传 ----
    st.subheader("📤 上传数据")
    uploaded_file = st.file_uploader(
        "支持 CSV / Excel",
        type=["csv", "xlsx", "xls"],
        help=f"最大 {config.MAX_FILE_SIZE_MB}MB",
    )

    if uploaded_file:
        with st.spinner("正在处理数据..."):
            try:
                stats = handle_file_upload(uploaded_file)
                st.success(
                    f"✅ 已加载: {stats['table_name']} "
                    f"({stats['row_count']} 行 × {stats['column_count']} 列)"
                )
                st.rerun()
            except Exception as e:
                st.error(f"❌ 加载失败: {str(e)}")

    st.divider()

    # ---- 数据表选择（带删除按钮） ----
    st.subheader("📊 已加载的数据表")
    all_tables = get_all_tables()
    if all_tables:
        idx = all_tables.index(st.session_state.current_table) \
            if st.session_state.current_table in all_tables else 0

        col_sel, col_del = st.columns([9, 1])
        with col_sel:
            selected = st.selectbox("选择表", all_tables, index=idx, key="table_selector", label_visibility="collapsed")
            if selected != st.session_state.current_table:
                st.session_state.current_table = selected
                st.rerun()
        with col_del:
            if st.button("✕", key="del_table", help=f"删除表: {st.session_state.current_table}"):
                table_to_drop = st.session_state.current_table
                # 1. 从数据库删除
                execute_sql(f'DROP TABLE IF EXISTS "{table_to_drop}"')
                # 2. 从 session 移除
                st.session_state.tables.pop(table_to_drop, None)
                st.session_state.current_table = None
                st.session_state.last_sql = ""
                # 3. 清理该表相关的历史记录
                related_history = [
                    hid for hid, entry in st.session_state.query_results.items()
                    if entry.get("sql", "").find(f'"{table_to_drop}"') != -1
                       or entry.get("answer", "").find(table_to_drop) != -1
                ]
                for hid in related_history:
                    st.session_state.query_results.pop(hid, None)
                    if hid in st.session_state.query_history:
                        st.session_state.query_history.remove(hid)
                if st.session_state.viewing_history_id in related_history:
                    st.session_state.viewing_history_id = None
                _save_history_to_disk()
                st.rerun()
    else:
        st.info("暂无数据，请上传文件")

    st.divider()

    # ---- 查询历史 ----
    if st.session_state.query_history:
        st.subheader("📝 查询历史")

        # 显示最近 20 条，每条是一行：查看按钮 + 删除按钮
        history_ids = list(reversed(st.session_state.query_history[-20:]))
        for hid in history_ids:
            entry = st.session_state.query_results.get(hid, {})
            question_text = entry.get("question", hid)
            time_text = entry.get("time", "")
            label = f"{time_text}  {question_text[:30]}{'...' if len(question_text) > 30 else ''}"

            # 高亮当前正在查看的条目
            is_active = st.session_state.viewing_history_id == hid

            col_hist, col_del = st.columns([9, 1])
            with col_hist:
                btn_type = "secondary" if not is_active else "primary"
                btn_label = ("🔍 " if is_active else "") + label
                if st.button(
                    btn_label,
                    key=f"hist_{hid}",
                    use_container_width=True,
                    type=btn_type,
                    help=question_text,
                ):
                    st.session_state.viewing_history_id = hid
                    st.session_state.user_query = question_text
                    st.rerun()
            with col_del:
                if st.button("✕", key=f"del_{hid}", help=f"删除: {question_text[:30]}"):
                    st.session_state.query_history.remove(hid)
                    st.session_state.query_results.pop(hid, None)
                    if st.session_state.viewing_history_id == hid:
                        st.session_state.viewing_history_id = None
                    _save_history_to_disk()
                    st.rerun()

        # 清空全部历史按钮
        if st.button("🗑️ 清空全部历史", use_container_width=True):
            st.session_state.query_history = []
            st.session_state.query_results = {}
            st.session_state.viewing_history_id = None
            st.session_state.user_query = ""
            _save_history_to_disk()
            st.rerun()

    st.divider()
    st.caption("💡 LangChain + OpenAI + Streamlit")


# ============================================================
# 主区域
# ============================================================
st.title("📊 AI 智能数据分析助手")
# st.caption("用自然语言提问，AI 自动生成 SQL 并给出洞察")

# ---- 无数据时的引导页 ----
if not st.session_state.current_table:
    st.info("👈 请先在左侧上传 CSV/Excel 文件，或点击下方按钮加载示例数据开始体验")

    st.divider()
    st.subheader("📦 快速体验 — 加载示例数据")

    col1, col2, _ = st.columns(3)
    with col1:
        if st.button("📈 载入销售数据 (500行)", use_container_width=True):
            df = create_sample_sales_data()
            stats = create_table_from_df(df, "sales_data")
            st.session_state.tables["sales_data"] = stats
            st.session_state.current_table = "sales_data"
            st.success(f"✅ 已加载销售数据 ({stats['row_count']} 行)")
            st.rerun()

    with col2:
        if st.button("👥 载入用户数据 (300行)", use_container_width=True):
            df = create_sample_user_data()
            stats = create_table_from_df(df, "user_behavior")
            st.session_state.tables["user_behavior"] = stats
            st.session_state.current_table = "user_behavior"
            st.success(f"✅ 已加载用户数据 ({stats['row_count']} 行)")
            st.rerun()

    st.stop()

# ---- 有数据时显示 Tabs ----
tabs = st.tabs(["💬 智能问答", "📋 数据预览", "📊 质量报告", "🔍 自定义 SQL"])

# ============================================================
# Tab 1: 智能问答（核心功能）
# ============================================================
with tabs[0]:
    st.subheader("🤖 用自然语言提问，AI 自动生成 SQL 并执行")

    table_name = st.session_state.current_table

    # 快捷问题推荐
    st.caption("💡 试试这些问题：")

    if "sales" in table_name.lower():
        suggestions = [
            "每个月的销售额趋势和环比增长率",
            "各区域的销售额占比排名",
            "销售额最高的前10个商品",
            "各品类的月度销量对比",
            "分析客单价的变化趋势",
        ]
    elif "user" in table_name.lower():
        suggestions = [
            "各城市的用户数量和消费总额排名",
            "VIP用户和非VIP用户的消费对比",
            "用户年龄分布和消费能力分析",
            "注册时间趋势分析",
            "复购率最高的城市",
        ]
    else:
        suggestions = [
            f"统计{table_name}表的基本信息",
            "按主要维度分组统计数量",
            "分析数值列的分布情况",
            "找出数据的趋势和异常点",
        ]

    cols = st.columns(len(suggestions))
    for i, s in enumerate(suggestions):
        with cols[i]:
            if st.button(s, key=f"sug_{i}", use_container_width=True):
                st.session_state.user_query = s
                st.session_state.viewing_history_id = None  # 切回新建模式
                st.rerun()

    st.divider()

    # 初始化
    if "user_query" not in st.session_state:
        st.session_state.user_query = ""

    # ---- 如果正在查看历史记录，显示"返回最新"按钮 ----
    if st.session_state.viewing_history_id:
        hist_entry = st.session_state.query_results.get(
            st.session_state.viewing_history_id, {}
        )
        col_hist, col_back = st.columns([4, 1])
        with col_hist:
            st.info(f"📜 正在查看历史记录: {hist_entry.get('time', '')} 的查询结果")
        with col_back:
            if st.button("⬅ 返回最新", use_container_width=True):
                st.session_state.viewing_history_id = None
                st.session_state.user_query = ""
                st.rerun()

        # 直接渲染历史结果
        hist_result = {
            "id": st.session_state.viewing_history_id,
            "question": hist_entry.get("question", ""),
            "sql": hist_entry.get("sql", ""),
            "answer": hist_entry.get("answer", ""),
            "query_data": hist_entry.get("query_data", []),
            "insight": hist_entry.get("insight", ""),
            "chart_type": hist_entry.get("chart_type", "bar"),
        }
        render_query_result(hist_result)

    else:
        # ---- 正常模式：输入 + 执行 ----
        st.caption("🔍 输入你的业务问题")
        col_input, col_btn = st.columns([5, 1])
        with col_input:
            question = st.text_input(
                "业务问题",
                key="user_query",
                placeholder="例如：各区域的销售额排名和环比变化趋势",
                label_visibility="collapsed",
            )

        with col_btn:
            go = st.button("🚀 开始分析", type="primary", use_container_width=True)

        if go and question.strip():
            with st.spinner("🤔 AI 正在分析..."):
                try:
                    agent = get_agent()
                    result = agent.query(question, current_table=table_name)

                    if result["success"]:
                        # 生成历史 ID
                        history_id = datetime.now().strftime("%Y%m%d_%H%M%S")

                        # 重新执行 SQL，获取结构化数据
                        query_data = []
                        sql_exec_error = None
                        if result.get("sql"):
                            try:
                                query_data = execute_sql(result["sql"])
                            except Exception as e:
                                sql_exec_error = str(e)

                        # 如果 SQL 重执行失败，显示警告
                        if sql_exec_error:
                            st.warning(f"⚠️ SQL 重执行失败: {sql_exec_error}")
                        elif result.get("sql") and not query_data:
                            st.info("ℹ️ SQL 执行成功，但未返回数据（可能为空结果集）")

                        # AI 洞察（直接基于原始查询数据，不依赖 Agent 文字转述）
                        insight_text = ""
                        try:
                            insights_gen = InsightsGenerator()
                            insight_text = insights_gen.generate(
                                question=question,
                                sql=result.get("sql", ""),
                                query_data=query_data,          # 传入原始数据
                                table_context=table_name,
                            )
                        except Exception:
                            pass

                        # 自动推断图表类型
                        chart_type = "bar"
                        if query_data:
                            chart_type = suggest_chart_type(query_data)

                        # ---- 保存完整结果到 session_state ----
                        history_entry = {
                            "id": history_id,
                            "question": question,
                            "sql": result.get("sql", ""),
                            "answer": result.get("answer", ""),
                            "query_data": query_data,
                            "insight": insight_text,
                            "chart_type": chart_type,
                            "time": datetime.now().strftime("%H:%M:%S"),
                        }
                        st.session_state.query_results[history_id] = history_entry
                        st.session_state.query_history.append(history_id)
                        st.session_state.viewing_history_id = None
                        st.session_state.last_sql = result.get("sql") or ""
                        _save_history_to_disk()  # 持久化到磁盘

                        # 限制历史数量（最多 50 条）
                        if len(st.session_state.query_history) > 50:
                            oldest = st.session_state.query_history.pop(0)
                            st.session_state.query_results.pop(oldest, None)

                        # ---- 渲染结果 ----
                        render_query_result({
                            "id": history_id,
                            "question": question,
                            "sql": result.get("sql", ""),
                            "answer": result.get("answer", ""),
                            "query_data": query_data,
                            "insight": insight_text,
                            "chart_type": chart_type,
                        })

                    else:
                        st.error(f"❌ {result.get('error', '查询失败')}")

                except Exception as e:
                    st.error(f"❌ 出错了: {str(e)}")
                    import traceback
                    with st.expander("查看技术详情"):
                        st.code(traceback.format_exc())

    # 删除冗余的 div，因为 render_query_result 内部已处理

# ============================================================
# Tab 2: 数据预览
# ============================================================
with tabs[1]:
    st.subheader("📋 数据预览")

    table_name = st.session_state.current_table

    col1, col2, col3 = st.columns(3)
    with col1:
        rows = st.slider("显示行数", 5, 200, 20, key="preview_rows")
    with col2:
        sort_options = ["_row_id"] + list(
            st.session_state.tables.get(table_name, {}).get("columns", [])
        )
        sort_by = st.selectbox("排序字段", sort_options, key="preview_sort")
    with col3:
        order = st.radio("排序方向", ["DESC", "ASC"], horizontal=True, key="preview_order")

    try:
        data = execute_sql(
            f'SELECT * FROM "{table_name}" ORDER BY "{sort_by}" {order} LIMIT {rows}'
        )
        if data:
            df = pd.DataFrame(data)
            st.dataframe(df, use_container_width=True, height=400)

            # 数值列统计
            st.divider()
            st.subheader("📈 数值列统计摘要")
            numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
            if numeric_cols:
                st.dataframe(
                    df[numeric_cols].describe().round(2),
                    use_container_width=True,
                )
            else:
                st.info("当前表暂无数值类型列")
        else:
            st.warning("表中无数据")
    except Exception as e:
        st.error(f"预览失败: {str(e)}")

# ============================================================
# Tab 3: 数据质量报告
# ============================================================
with tabs[2]:
    st.subheader("📊 数据质量报告")
    st.caption("逐列检测缺失值、唯一值、最频值等质量指标")

    if st.button("🔍 生成质量报告", type="primary"):
        with st.spinner("正在分析各列数据质量..."):
            try:
                report = get_data_quality_report(st.session_state.current_table)
                if report:
                    report_df = pd.DataFrame(report)
                    st.dataframe(report_df, use_container_width=True)

                    # 可视化缺失率
                    missing_cols = report_df[report_df["缺失率(%)"] > 0]
                    if not missing_cols.empty:
                        st.divider()
                        st.subheader("⚠️ 存在缺失值的列")

                        import plotly.express as px
                        fig = px.bar(
                            missing_cols, x="列名", y="缺失率(%)",
                            title="各列缺失率",
                            text=missing_cols["缺失率(%)"].apply(lambda x: f"{x:.1f}%"),
                            color=missing_cols["缺失率(%)"],
                            color_continuous_scale="Reds",
                        )
                        fig.update_traces(textposition="outside")
                        fig.update_layout(height=350)
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.success("✅ 所有列均无缺失值")
                else:
                    st.info("表中无数据或暂时无法生成报告")

            except Exception as e:
                st.error(f"报告生成失败: {str(e)}")

# ============================================================
# Tab 4: 自定义 SQL
# ============================================================
with tabs[3]:
    st.subheader("🔍 自定义 SQL 查询")
    st.caption(f"当前表: `{st.session_state.current_table}`")

    # 获取列名提示
    cols_hint = st.session_state.tables.get(
        st.session_state.current_table, {}
    ).get("columns", [])
    if cols_hint:
        st.caption(f"可用列: {', '.join(str(c) for c in cols_hint)}")

    # 初始化 custom_sql_input
    if "custom_sql_input" not in st.session_state:
        st.session_state.custom_sql_input = ""

    default_sql = f'SELECT * FROM "{st.session_state.current_table}" LIMIT 10;'

    # 如果用户还没写过自定义 SQL，显示默认模板
    if not st.session_state.custom_sql_input.strip():
        st.session_state.custom_sql_input = default_sql

    custom_sql = st.text_area(
        "输入 SQL 语句（仅支持 SELECT）",
        key="custom_sql_input",
        height=150,
    )

    col_btn1, col_btn2 = st.columns([1, 5])
    with col_btn1:
        run_clicked = st.button("▶️ 执行", type="primary", use_container_width=True)

    if run_clicked and custom_sql.strip():
        # ---- 安全检查 ----
        dangerous_kw = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER",
                         "CREATE", "TRUNCATE", "EXEC", "EXECUTE"]
        upper_sql = custom_sql.strip().upper()

        blocked = False
        for kw in dangerous_kw:
            # 检查每个分号分隔的语句
            for part in upper_sql.split(";"):
                if part.strip().startswith(kw):
                    blocked = True
                    break

        if blocked:
            st.error("🚫 安全限制：仅允许 SELECT 查询。已拦截危险语句。")
        else:
            try:
                result = execute_sql(custom_sql.strip())
                if isinstance(result, list) and result:
                    df = pd.DataFrame(result)
                    st.divider()
                    st.subheader(f"📊 查询结果 ({len(df)} 行)")

                    # 数据表格
                    st.dataframe(df, use_container_width=True)

                    # 自动图表
                    st.divider()
                    st.subheader("📈 自动图表")

                    chart_type = suggest_chart_type(result)
                    col_ct1, col_ct2, _, _, _ = st.columns(5)
                    with col_ct1:
                        chart_choice = st.selectbox(
                            "图表类型",
                            ["bar", "line", "pie", "scatter", "table"],
                            index=["bar", "line", "pie", "scatter", "table"].index(chart_type),
                        )

                    if chart_choice == "table":
                        fig = create_data_table_figure(result)
                    else:
                        fig = create_chart(result, chart_type=chart_choice)
                    st.plotly_chart(fig, use_container_width=True)

                    # CSV 导出
                    csv = df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        "📥 下载 CSV",
                        csv,
                        f"{st.session_state.current_table}_result.csv",
                        "text/csv",
                    )

                elif isinstance(result, list) and not result:
                    st.info("✅ 查询执行成功，但无返回数据")
                else:
                    st.success("✅ 查询执行成功（非查询语句）")

            except Exception as e:
                st.error(f"❌ SQL 执行错误: {str(e)}")

# ============================================================
# 页面底部
# ============================================================
st.divider()
st.caption(
    "🤖 AI 智能数据分析助手 · "
    "Python + LangChain + OpenAI + Streamlit + PostgreSQL/SQLite · "
    "自然语言 → SQL → 洞察 → 可视化，端到端数据智能"
)
