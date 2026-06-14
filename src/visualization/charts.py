"""
数据可视化模块
- 根据查询结果自动推荐图表类型
- 使用 Plotly 生成交互式图表
- 支持柱状图、折线图、饼图、散点图
"""
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from typing import Literal


# 支持的图表类型
ChartType = Literal["bar", "line", "pie", "scatter", "table"]


def result_to_df(result: list[dict]) -> pd.DataFrame:
    """将 execute_sql 返回的 dict 列表转为 DataFrame"""
    if not result:
        return pd.DataFrame()
    return pd.DataFrame(result)


def suggest_chart_type(result: list[dict]) -> ChartType:
    """
    智能推断最合适的图表类型。

    推断逻辑：
    - 无数据 → table
    - 无数值列 → table
    - 仅 1 列数值 + 1 列文本 → bar（柱状图最直观）
    - 含日期/月份列 → line（时序数据用折线图）
    - 2 列数值 → scatter（两个变量关系用散点图）
    - 1 列文本 + 1 列数值 + 文本列唯一值 ≤ 8 → pie
    - 其他 → bar
    """
    df = result_to_df(result)
    if df.empty:
        return "table"

    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    text_cols = [c for c in df.columns if c not in numeric_cols]

    if not numeric_cols:
        return "table"

    # 判断是否为时序数据
    date_keywords = ["date", "日期", "月份", "季度", "年份", "month", "year", "quarter", "time", "时间"]
    is_time_series = any(
        any(kw in col.lower() for kw in date_keywords)
        for col in text_cols
    )

    if is_time_series and "pct" not in str(df.columns).lower():
        return "line"

    # 饼图：少量分类 + 1 个数值列
    if len(text_cols) == 1 and len(numeric_cols) == 1:
        unique_vals = df[text_cols[0]].nunique()
        if 2 <= unique_vals <= 8:
            return "pie"

    # 散点图：2 个数值列
    if len(numeric_cols) >= 2:
        return "scatter"

    # 默认柱状图
    return "bar"


def create_chart(
    result: list[dict],
    chart_type: ChartType = "bar",
    title: str = "",
) -> go.Figure:
    """
    根据查询结果创建 Plotly 图表。

    Args:
        result: execute_sql 返回的数据
        chart_type: 图表类型
        title: 图表标题

    Returns:
        Plotly Figure 对象（直接可用于 st.plotly_chart）
    """
    df = result_to_df(result)
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="暂无数据", showarrow=False,
            font=dict(size=20, color="gray")
        )
        fig.update_layout(height=300)
        return fig

    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    text_cols = [c for c in df.columns if c not in numeric_cols]

    # 确定 X 轴和 Y 轴
    x_col = text_cols[0] if text_cols else df.columns[0]
    y_col = (
        numeric_cols[0] if numeric_cols
        else df.columns[-1]
    )

    try:
        if chart_type == "bar":
            # 取前 20 行，避免柱状图太密
            plot_df = df.head(20)
            fig = px.bar(
                plot_df, x=x_col, y=y_col, title=title,
                text=y_col if len(plot_df) <= 10 else None,
            )
            fig.update_traces(texttemplate="%{text:.1f}", textposition="outside")

        elif chart_type == "line":
            plot_df = df.head(50)
            fig = px.line(
                plot_df, x=x_col, y=y_col, title=title,
                markers=True, line_shape="spline",
            )

        elif chart_type == "pie":
            plot_df = df.head(10)
            fig = px.pie(
                plot_df, names=x_col, values=y_col, title=title,
                hole=0.4,  # 甜甜圈样式更好看
            )
            fig.update_traces(textinfo="label+percent")

        elif chart_type == "scatter":
            # 散点图：用两个数值列
            x_col_scatter = numeric_cols[0] if len(numeric_cols) >= 2 else x_col
            y_col_scatter = numeric_cols[1] if len(numeric_cols) >= 2 else y_col
            plot_df = df.head(100)
            fig = px.scatter(
                plot_df, x=x_col_scatter, y=y_col_scatter, title=title,
                opacity=0.7, size_max=15,
            )

        else:
            # fallback
            plot_df = df.head(20)
            fig = px.bar(plot_df, x=x_col, y=y_col, title=title)

    except Exception:
        # 兜底：出错了就返回一个简单柱状图
        fig = px.bar(df.head(10), x=df.columns[0], y=df.columns[-1], title=title)

    # 统一的样式设置
    fig.update_layout(
        template="plotly_white",
        height=420,
        margin=dict(l=20, r=20, t=50, b=30),
        title_x=0.5,            # 标题居中
        title_font_size=16,
        showlegend=False,       # 单指标通常不需要图例
        dragmode="pan",         # 默认拖拽模式
    )

    return fig


def create_data_table_figure(result: list[dict]) -> go.Figure:
    """
    创建表格形式的图表（用于不想画图纯看数据的场景）。
    使用 Plotly Table 组件，支持排序和滚动。
    """
    df = result_to_df(result)
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="暂无数据", showarrow=False)
        return fig

    # 限制显示行数
    df = df.head(30)

    fig = go.Figure(data=[
        go.Table(
            header=dict(
                values=list(df.columns),
                fill_color="#4A90D9",
                font=dict(color="white", size=12),
                align="center",
            ),
            cells=dict(
                values=[df[col] for col in df.columns],
                fill_color="#FAFAFA",
                align="center",
                font=dict(size=11),
                height=30,
            ),
        )
    ])

    fig.update_layout(
        height=min(400 + len(df) * 30, 800),
        margin=dict(l=10, r=10, t=10, b=10),
    )

    return fig
