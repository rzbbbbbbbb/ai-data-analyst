"""
Schema 自动推断与建表
—— 这是展示 SQL 能力的关键模块

核心能力：
1. 从 pandas DataFrame 自动推断 SQL 类型 → 生成 CREATE TABLE 语句
2. 批量数据入库
3. 获取表 Schema 信息（用于喂给 LLM）
4. 数据质量统计（窗口函数、CTE、聚合查询）
"""
import pandas as pd
import re
from sqlalchemy import text
from src.database.connection import execute_sql, get_engine


# ============================================================
# 类型映射
# ============================================================

TYPE_MAPPING = {
    "int64": "BIGINT",
    "Int64": "BIGINT",      # pandas nullable int
    "int32": "INTEGER",
    "int16": "SMALLINT",
    "float64": "DOUBLE PRECISION",
    "float32": "REAL",
    "object": "TEXT",
    "bool": "BOOLEAN",
    "datetime64[ns]": "TIMESTAMP",
    "datetime64[ns, UTC]": "TIMESTAMP",
    "category": "TEXT",
}


# ============================================================
# Schema 推断 —— 根据列名和数据智能决定 SQL 类型
# ============================================================

def infer_column_type(col_name: str, dtype_str: str, sample_values) -> str:
    """
    智能推断单列 SQL 类型。
    综合考虑：pandas dtype + 列名语义 + 实际数据特征
    """
    col_lower = col_name.lower()

    # --- 第一层：列名语义判断 ---
    # ID 类
    if any(kw in col_lower for kw in ["id", "_id", "编号", "序号"]):
        return "BIGINT"

    # 时间类
    if any(kw in col_lower for kw in ["time", "date", "时间", "日期", "created", "updated", "timestamp"]):
        return "TIMESTAMP"

    # 金额/比率类 → 精度更重要
    if any(kw in col_lower for kw in ["price", "amount", "金额", "价格", "rate", "比率", "salary", "工资"]):
        return "NUMERIC(18, 4)"

    # 布尔类
    if any(kw in col_lower for kw in ["is_", "has_", "是否", "flag", "标记"]):
        return "BOOLEAN"

    # --- 第二层：pandas dtype 判断 ---
    sql_type = TYPE_MAPPING.get(dtype_str.replace("32", "64"), "TEXT")

    # object 类型但实际可能是数字
    if sql_type == "TEXT" and sample_values is not None:
        try:
            numeric_ratio = pd.to_numeric(sample_values, errors="coerce").notna().mean()
            if numeric_ratio > 0.9:
                sql_type = "DOUBLE PRECISION"
        except Exception:
            pass

    return sql_type


def infer_schema(df: pd.DataFrame, table_name: str) -> str:
    """
    根据 DataFrame 生成 CREATE TABLE 语句。
    这是项目的第一个亮点：自动数据建模。

    Args:
        df: pandas DataFrame
        table_name: 目标表名

    Returns:
        完整的 CREATE TABLE SQL 语句
    """
    columns_defs = []

    for col_name, dtype in df.dtypes.items():
        sample = df[col_name].dropna().head(100) if len(df) > 0 else None
        sql_type = infer_column_type(col_name, str(dtype), sample)

        # 空值判断
        nullable = "NULL" if df[col_name].isna().any() else "NOT NULL"

        # 防止 SQL 注入：双重双引号转义
        safe_name = col_name.replace('"', '""')
        columns_defs.append(f'    "{safe_name}" {sql_type} {nullable}')

    # 自增主键（方便后续操作）
    if config_is_postgres():
        columns_defs.insert(0, "    _row_id SERIAL PRIMARY KEY")
    else:
        columns_defs.insert(0, "    _row_id INTEGER PRIMARY KEY AUTOINCREMENT")

    create_sql = f'CREATE TABLE IF NOT EXISTS "{table_name}" (\n'
    create_sql += ",\n".join(columns_defs)
    create_sql += "\n);"

    return create_sql


def config_is_postgres() -> bool:
    """判断当前数据库是否为 PostgreSQL"""
    from src.config import config
    return config.DATABASE_URL.startswith("postgresql")


# ============================================================
# 建表 + 数据入库
# ============================================================

def create_table_from_df(
    df: pd.DataFrame,
    table_name: str,
    progress_callback=None,
) -> dict:
    """
    完整的建表+入库流程：
    1. 清理表名
    2. 删除旧表（覆盖模式）
    3. 生成 CREATE TABLE 并执行
    4. 高性能批量插入（SQLite: PRAGMA 优化 + executemany）
    5. 返回统计信息

    progress_callback(current, total) — 每插入一批调用一次
    """
    # 1. 清理表名
    table_name = _clean_table_name(table_name)

    # 2. 删旧建新
    execute_sql(f'DROP TABLE IF EXISTS "{table_name}"')

    # 3. 生成并执行建表语句
    create_sql = infer_schema(df, table_name)
    execute_sql(create_sql)

    # 4. 高性能批量插入
    engine = get_engine()
    columns = [f'"{c}"' for c in df.columns]
    placeholders = ", ".join([":" + c for c in df.columns])
    insert_sql = f'INSERT INTO "{table_name}" ({", ".join(columns)}) VALUES ({placeholders})'

    # 用 raw_connection + executemany，比 pandas to_sql 快 5-10 倍
    raw_conn = engine.raw_connection()
    try:
        # SQLite 性能优化 PRAGMA（对其他数据库无害，会被忽略）
        if config_is_sqlite():
            raw_conn.execute("PRAGMA synchronous = OFF")
            raw_conn.execute("PRAGMA journal_mode = MEMORY")
            raw_conn.execute("PRAGMA cache_size = 100000")

        cursor = raw_conn.cursor()

        # 将 DataFrame 转为 dict 列表，executemany 批量写入
        batch_size = 5000
        total = len(df)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch = df.iloc[start:end].to_dict("records")
            # 清理 NaN → None（SQLite 用 None 表示 NULL）
            cleaned_batch = [
                {k: (None if pd.isna(v) else v) for k, v in row.items()}
                for row in batch
            ]
            cursor.executemany(insert_sql, cleaned_batch)
            raw_conn.commit()

            if progress_callback:
                progress_callback(end, total)

        cursor.close()
    finally:
        raw_conn.close()

    # 5. 返回统计
    return {
        "table_name": table_name,
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": list(df.columns),
        "create_sql": create_sql,
    }


def config_is_sqlite() -> bool:
    """判断当前数据库是否为 SQLite"""
    from src.config import config
    return config.DATABASE_URL.startswith("sqlite")


def _clean_table_name(name: str) -> str:
    """清理表名，只保留合法字符，长度限制 63（PG 上限）"""
    # 替换非法字符为下划线
    name = re.sub(r"[^a-zA-Z0-9_一-鿿]", "_", name)
    name = re.sub(r"_+", "_", name)  # 合并连续下划线

    # 以数字或下划线开头的话，前缀 t_
    if name[0].isdigit() or name[0] == "_":
        name = "t_" + name

    return name.lower()[:63]


# ============================================================
# Schema 信息获取 —— 这是注入 LLM Prompt 的关键数据
# ============================================================

def get_table_schema_for_llm(table_name: str) -> str:
    """
    获取表结构信息，格式化为 LLM 友好的文本。
    这个函数的输出会直接注入到 SQL Agent 的 System Prompt 中。

    内容包括：列名、类型、约束、行数、数值列统计、前3行样本
    内容越详细，LLM 生成 SQL 的准确率越高。
    """
    parts = []

    # --- 基本信息 ---
    parts.append(f"表名: \"{table_name}\"")

    row_count = execute_sql(f'SELECT COUNT(*) AS cnt FROM "{table_name}"')
    if row_count:
        parts.append(f"总行数: {row_count[0]['cnt']}")

    # --- 列信息 ---
    cols = _get_columns(table_name)
    parts.append("\n列定义:")
    for c in cols:
        parts.append(f"  - \"{c['column_name']}\"  {c['data_type']}  ({c['is_nullable']})")

    # --- 数值列统计 ---
    num_stats = _get_numeric_column_stats(table_name, cols)
    if num_stats.strip():
        parts.append(f"\n数值列统计: {num_stats}")

    # --- 前3行样本 ---
    sample = execute_sql(f'SELECT * FROM "{table_name}" LIMIT 3')
    if sample:
        # 手动格式化避免产生 { }（会被 langchain .format() 误解析）
        sample_lines = []
        for i, row in enumerate(sample):
            row_str = ", ".join(f"{k}={v}" for k, v in row.items())
            sample_lines.append(f"  Row {i+1}: [{row_str}]")
        parts.append(f"\n前3行样本数据:\n" + "\n".join(sample_lines))

    return "\n".join(parts)


def _get_columns(table_name: str) -> list[dict]:
    """获取列信息，兼容 SQLite 和 PostgreSQL"""
    from src.config import config
    if config.DATABASE_URL.startswith("sqlite"):
        result = execute_sql(f'PRAGMA table_info("{table_name}")')
        return [
            {
                "column_name": r["name"],
                "data_type": r["type"],
                "is_nullable": "可空" if not r.get("notnull") else "非空",
            }
            for r in result
        ]
    else:
        return execute_sql(f"""
            SELECT column_name, data_type,
                   CASE WHEN is_nullable = 'YES' THEN '可空' ELSE '非空' END AS is_nullable
            FROM information_schema.columns
            WHERE table_name = '{table_name}'
            ORDER BY ordinal_position
        """)


def _get_numeric_column_stats(table_name: str, columns: list[dict]) -> str:
    """
    获取数值列的 min / max / avg / distinct 统计。
    用一条 SQL 搞定多列的聚合统计，展示 SQL 功底。
    """
    numeric_types = ("integer", "bigint", "numeric", "double precision",
                     "real", "float", "decimal", "smallint", "int", "number")
    numeric_cols = [
        c["column_name"]
        for c in columns
        if any(t in c["data_type"].lower() for t in numeric_types)
    ]

    if not numeric_cols:
        return ""

    # 为每列生成统计查询（最多 5 列，避免太长）
    stat_parts = []
    for col in numeric_cols[:5]:
        sql = f"""
        SELECT
            MIN("{col}") AS min_val,
            MAX("{col}") AS max_val,
            ROUND(AVG(CAST("{col}" AS FLOAT)), 2) AS avg_val,
            COUNT(DISTINCT "{col}") AS distinct_cnt,
            SUM(CASE WHEN "{col}" IS NULL THEN 1 ELSE 0 END) AS null_cnt
        FROM "{table_name}"
        """
        result = execute_sql(sql)
        if result and result[0]:
            # 手动格式化避免 { } 出现（会被 langchain .format() 误解析）
            r = result[0]
            stat_parts.append(
                f"{col}: min={r['min_val']}, max={r['max_val']}, "
                f"avg={r['avg_val']}, distinct={r['distinct_cnt']}, null={r['null_cnt']}"
            )

    return "; ".join(stat_parts)


# ============================================================
# 数据质量报告 —— 用 SQL 一次性分析多维度质量指标
# ============================================================

def get_data_quality_report(table_name: str) -> list[dict]:
    """
    用 SQL 生成数据质量报告。
    每个列返回: 缺失值数、缺失率、唯一值数、最高频值、最高频占比

    这是简历上能写的「数据质量监控」能力。
    """
    cols = _get_columns(table_name)
    total_rows = execute_sql(f'SELECT COUNT(*) AS cnt FROM "{table_name}"')[0]["cnt"]

    if total_rows == 0:
        return []

    report = []
    for col in cols:
        col_name = col["column_name"]

        # 一条 SQL 完成列级别的质量统计
        sql = f"""
        WITH base AS (
            SELECT "{col_name}" AS val FROM "{table_name}"
        ),
        stats AS (
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN val IS NULL THEN 1 ELSE 0 END) AS null_count,
                COUNT(DISTINCT val) AS distinct_count
            FROM base
        ),
        top_val AS (
            SELECT
                val,
                COUNT(*) AS freq
            FROM base
            WHERE val IS NOT NULL
            GROUP BY val
            ORDER BY freq DESC
            LIMIT 1
        )
        SELECT
            s.null_count,
            ROUND(s.null_count * 100.0 / NULLIF(s.total, 0), 2) AS null_rate,
            s.distinct_count,
            tv.val AS top_value,
            tv.freq AS top_value_freq,
            ROUND(tv.freq * 100.0 / NULLIF(s.total, 0), 2) AS top_value_rate
        FROM stats s
        LEFT JOIN top_val tv ON 1=1
        """

        result = execute_sql(sql)
        if result and result[0]:
            row = result[0]
            report.append({
                "列名": col_name,
                "类型": col["data_type"],
                "缺失值": row["null_count"] or 0,
                "缺失率(%)": row["null_rate"] or 0,
                "唯一值数": row["distinct_count"] or 0,
                "最高频值": str(row["top_value"])[:30] if row["top_value"] else "-",
                "最高频占比(%)": row["top_value_rate"] or 0,
            })

    return report
