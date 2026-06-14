"""
数据清洗测试

验证清洗流程：
1. 完全空行/空列移除
2. 重复行去重
3. 列名规范化
4. 基础统计信息
"""
import pandas as pd
import numpy as np
from src.data_loader.cleaner import clean_dataframe, get_basic_stats


class TestCleanDataFrame:
    """clean_dataframe 核心清洗逻辑"""

    def test_removes_fully_empty_rows(self, dirty_df):
        """完全空行应被移除"""
        df = dirty_df.copy()
        # 添加一个完全空行
        df = pd.concat([df, pd.DataFrame([{"Name": None, "Age": None, "City": None}])],
                       ignore_index=True)
        original_len = len(df)
        cleaned = clean_dataframe(df)
        assert len(cleaned) < original_len

    def test_removes_duplicate_rows(self, dirty_df):
        """重复行应被去重"""
        original_len = len(dirty_df)
        cleaned = clean_dataframe(dirty_df)
        # dirty_df 里有第 0 行的重复
        assert len(cleaned) < original_len

    def test_strips_column_names(self):
        """列名首尾空格应被去除"""
        df = pd.DataFrame({
            " name ": [1, 2],
            "age ": [25, 30],
        })
        cleaned = clean_dataframe(df)
        assert " name " not in cleaned.columns
        assert "name" in cleaned.columns
        assert "age" in cleaned.columns

    def test_removes_empty_column_names(self):
        """空列名应被移除"""
        df = pd.DataFrame({
            "": [1, 2],
            "valid": [3, 4],
        })
        cleaned = clean_dataframe(df)
        assert "" not in cleaned.columns
        assert "valid" in cleaned.columns

    def test_replaces_special_chars_in_columns(self):
        """列名中的特殊字符替换为下划线（兼容 SQL）"""
        df = pd.DataFrame({
            "hello world": [1, 2],
            "price ($)": [10, 20],
        })
        cleaned = clean_dataframe(df)
        assert "hello_world" in cleaned.columns
        # "price ($)" → 特殊字符被替换 + 连续下划线合并 → "price_"
        assert "price" in cleaned.columns

    def test_returns_copy_not_view(self):
        """返回的是副本，不修改原始 DataFrame"""
        df = pd.DataFrame({"x": [1, 2, 3]})
        cleaned = clean_dataframe(df)
        cleaned.loc[0, "x"] = 999
        assert df.loc[0, "x"] == 1  # 原始未变

    def test_works_with_numeric_data(self):
        """纯数值数据清洗"""
        df = pd.DataFrame({
            "a": [1.0, 2.0, 3.0, None],
            "b": [10, 20, 30, 40],
        })
        cleaned = clean_dataframe(df)
        assert len(cleaned) == 4
        assert "a" in cleaned.columns

    def test_works_with_chinese_data(self):
        """中文数据清洗"""
        df = pd.DataFrame({
            "姓名": ["张三", "李四", "张三", "王五"],
            "城市": ["北京", "上海", "北京", "广州"],
        })
        cleaned = clean_dataframe(df)
        assert len(cleaned) == 3  # "张三"的第二个重复被去重
        assert "姓名" in cleaned.columns


class TestGetBasicStats:
    """基础统计信息"""

    def test_returns_dict_with_keys(self):
        df = pd.DataFrame({
            "x": [1, 2, 3, None],
            "y": [10, 20, 30, 40],
        })
        stats = get_basic_stats(df)
        assert isinstance(stats, dict)
        assert "rows" in stats
        assert "columns" in stats
        assert "total_missing" in stats
        assert "duplicate_rows" in stats
        assert "memory_mb" in stats

    def test_missing_count(self):
        df = pd.DataFrame({"a": [1, None, None, 4]})
        stats = get_basic_stats(df)
        assert stats["total_missing"] == 2

    def test_row_and_column_count(self):
        df = pd.DataFrame({"a": range(10), "b": range(10), "c": range(10)})
        stats = get_basic_stats(df)
        assert stats["rows"] == 10
        assert stats["columns"] == 3
