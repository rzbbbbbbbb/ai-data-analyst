"""
Schema 推断与建表测试

核心能力验证：
1. 列类型智能推断（语义 + dtype + 数据采样）
2. CREATE TABLE 语句生成
3. 批量入库 + 数据完整性
4. LLM 友好的 Schema 信息格式化
5. 表名清理
"""
import pytest
import pandas as pd
from src.database.schema import (
    infer_column_type,
    infer_schema,
    create_table_from_df,
    get_table_schema_for_llm,
    _clean_table_name,
)
from src.database.connection import execute_sql


class TestInferColumnType:
    """列类型推断"""

    def test_id_column_maps_to_bigint(self):
        """包含 id 的列名应映射为 BIGINT"""
        result = infer_column_type("user_id", "int64", None)
        assert result == "BIGINT"

    def test_time_column_maps_to_timestamp(self):
        """包含 date/time 的列名应映射为 TIMESTAMP"""
        result = infer_column_type("created_date", "object", None)
        assert result == "TIMESTAMP"

    def test_price_column_maps_to_numeric(self):
        """价格/金额列用高精度 NUMERIC"""
        result = infer_column_type("unit_price", "float64", None)
        assert result == "NUMERIC(18, 4)"

    def test_bool_semantic_column(self):
        """is_/has_ 前缀映射为 BOOLEAN"""
        result = infer_column_type("is_active", "bool", None)
        assert result == "BOOLEAN"

    def test_object_with_numeric_content(self):
        """object 列但实际数据 90%+ 可转数值 → 按数值处理"""
        # 10/11 ≈ 91% > 90% 阈值
        sample = pd.Series(
            ["100", "200", "300", "400", "500", "600", "700", "800", "900",
             "1000", "abc"]
        )
        result = infer_column_type("some_col", "object", sample)
        assert result == "DOUBLE PRECISION"

    def test_plain_text_stays_text(self):
        """普通文本列保持 TEXT"""
        sample = pd.Series(["北京", "上海", "广州", "深圳"])
        result = infer_column_type("city", "object", sample)
        assert result == "TEXT"

    def test_int32_maps_to_bigint(self):
        """int32 类型映射为 BIGINT（替换 32→64 后匹配）"""
        result = infer_column_type("count", "int32", None)
        assert result == "BIGINT"

    def test_float64_maps_to_double(self):
        """float64 类型映射为 DOUBLE PRECISION（需避开语义敏感词）"""
        result = infer_column_type("score", "float64", None)
        assert result == "DOUBLE PRECISION"


class TestCleanTableName:
    """表名清理"""

    def test_keeps_alphanumeric_and_chinese(self):
        assert _clean_table_name("淘宝用户行为123") == "淘宝用户行为123"

    def test_replaces_special_chars(self):
        result = _clean_table_name("hello world!")
        assert "!" not in result
        assert " " not in result

    def test_merges_multiple_underscores(self):
        result = _clean_table_name("hello___world")
        assert "___" not in result

    def test_prefix_digit_start(self):
        result = _clean_table_name("123data")
        assert result.startswith("t_")

    def test_truncates_long_name(self):
        result = _clean_table_name("a" * 100)
        assert len(result) <= 63

    def test_lowercases(self):
        result = _clean_table_name("MyTable")
        assert result == "mytable"


class TestInferSchema:
    """生成 CREATE TABLE 语句"""

    def test_generates_create_table(self, sales_df):
        sql = infer_schema(sales_df, "sales")
        assert sql.upper().startswith("CREATE TABLE")
        assert "sales" in sql.lower()
        # 应包含主键
        assert "_row_id" in sql
        assert "PRIMARY KEY" in sql.upper()

    def test_all_columns_included(self, sales_df):
        sql = infer_schema(sales_df, "sales")
        for col in sales_df.columns:
            assert col in sql, f"列 {col} 应在 CREATE TABLE 中"


class TestCreateTableFromDf:
    """建表 + 入库流程"""

    def test_creates_and_populates_table(self, sales_df):
        stats = create_table_from_df(sales_df, "test_sales")
        assert stats["table_name"] == "test_sales"
        assert stats["row_count"] == len(sales_df)
        assert stats["column_count"] == len(sales_df.columns)

    def test_data_integrity(self, sales_df):
        """入库后数据应与原始 DataFrame 一致"""
        create_table_from_df(sales_df, "test_sales_integrity")
        rows = execute_sql('SELECT * FROM "test_sales_integrity" ORDER BY _row_id')
        assert len(rows) == len(sales_df)
        # 逐列比对第一行
        first_row = rows[0]
        assert first_row["order_id"] == sales_df.iloc[0]["order_id"]

    def test_overwrite_existing_table(self, sales_df):
        """重复建同名表应覆盖旧数据"""
        create_table_from_df(sales_df, "test_overwrite")
        # 创建一个更小的 df
        small_df = sales_df.head(2)
        create_table_from_df(small_df, "test_overwrite")
        rows = execute_sql('SELECT COUNT(*) AS cnt FROM "test_overwrite"')
        assert rows[0]["cnt"] == 2

    def test_handles_nan_values(self):
        """NaN 应正确转为 NULL"""
        df = pd.DataFrame({
            "name": ["Alice", "Bob", None],
            "score": [100.0, None, 80.0],
        })
        create_table_from_df(df, "test_nan")
        rows = execute_sql('SELECT * FROM "test_nan" ORDER BY _row_id')
        assert rows[0]["name"] == "Alice"
        assert rows[2]["name"] is None
        assert rows[1]["score"] is None


class TestGetTableSchemaForLlm:
    """Schema 格式化（喂给 LLM 的文本）"""

    def test_returns_table_info(self):
        df = pd.DataFrame({
            "id": [1, 2, 3],
            "name": ["Alice", "Bob", "Charlie"],
        })
        create_table_from_df(df, "test_llm_schema")
        info = get_table_schema_for_llm("test_llm_schema")
        assert "test_llm_schema" in info
        assert "id" in info
        assert "name" in info

    def test_no_curly_braces(self):
        """输出不能包含 { } 否则 LangChain .format() 会报错"""
        df = pd.DataFrame({"x": [1]})
        create_table_from_df(df, "test_no_brace")
        info = get_table_schema_for_llm("test_no_brace")
        assert "{" not in info
        assert "}" not in info
