"""
数据清洗模块
提供双重清洗能力：
1. Python/pandas 层面：去重、去空行/列、列名清理
2. SQL 层面：缺失值统计（在 schema.py 的 get_data_quality_report 中）
"""
import pandas as pd


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    对 DataFrame 做基础清洗，返回清洗后的副本（不修改原数据）。

    清洗步骤：
    1. 去除全空行和全空列
    2. 去除完全重复的行
    3. 去除首尾空格的列名
    4. 去除空列名
    5. 将列名中的特殊字符替换为下划线（方便 SQL 使用）
    """
    df = df.copy()
    initial_shape = df.shape

    # 1. 去除全空行和全空列
    df = df.dropna(how="all")
    df = df.dropna(axis=1, how="all")

    # 2. 去除完全重复的行
    before_dedup = len(df)
    df = df.drop_duplicates()
    after_dedup = len(df)

    # 3. 清理列名：去首尾空格
    df.columns = [str(c).strip() for c in df.columns]

    # 4. 去除空列名
    df = df.loc[:, df.columns != ""]

    # 5. 替换列名中的特殊字符（SQL 友好）
    import re
    new_cols = []
    for c in df.columns:
        # 保留中文、英文、数字、下划线
        clean = re.sub(r"[^\w一-鿿]", "_", c)
        clean = re.sub(r"_+", "_", clean).strip("_")
        new_cols.append(clean if clean else f"col_{len(new_cols)}")
    df.columns = new_cols

    final_shape = df.shape

    # 打印清洗日志（调试用）
    if initial_shape != final_shape:
        print(f"[Cleaner] 清洗完成: {initial_shape} → {final_shape} "
              f"(去重 {before_dedup - after_dedup} 行)")

    return df


def get_basic_stats(df: pd.DataFrame) -> dict:
    """
    在入库前快速了解数据概况。
    返回行数、列数、缺失值总数、重复行数等。
    """
    return {
        "rows": len(df),
        "columns": len(df.columns),
        "total_missing": int(df.isna().sum().sum()),
        "duplicate_rows": int(df.duplicated().sum()),
        "memory_mb": round(df.memory_usage(deep=True).sum() / 1024 / 1024, 2),
    }
