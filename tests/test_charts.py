"""
可视化测试 —— 图表类型推荐 + 图表生成

验证：
1. suggest_chart_type 的推荐逻辑
2. create_chart 不抛异常
3. create_data_table_figure 合法输出
"""
import pytest
import plotly.graph_objects as go
from src.visualization.charts import (
    suggest_chart_type,
    create_chart,
    create_data_table_figure,
    result_to_df,
)


class TestSuggestChartType:
    """图表类型自动推荐"""

    def test_recommends_line_for_time_data(self):
        """包含日期列时应推荐折线图"""
        result = [
            {"日期": "2024-01-01", "销售额": 1000},
            {"日期": "2024-02-01", "销售额": 1200},
            {"日期": "2024-03-01", "销售额": 900},
        ]
        chart_type = suggest_chart_type(result)
        assert chart_type == "line"

    def test_recommends_pie_for_few_categories(self):
        """1 文本列 + 1 数值列 + ≤8 个唯一值 → 饼图"""
        result = [
            {"品类": "电子产品", "销售额": 5000},
            {"品类": "服装", "销售额": 3000},
            {"品类": "食品", "销售额": 2000},
        ]
        chart_type = suggest_chart_type(result)
        assert chart_type == "pie"

    def test_recommends_bar_for_many_categories(self):
        """1 文本列 + 1 数值列 + >8 个唯一值 → 柱状图"""
        result = [
            {"城市": f"城市{i}", "销售额": i * 100}
            for i in range(10)
        ]
        chart_type = suggest_chart_type(result)
        assert chart_type == "bar"

    def test_recommends_scatter_for_two_numeric(self):
        """2 个以上数值列 → 散点图"""
        result = [
            {"身高": 170, "体重": 65},
            {"身高": 175, "体重": 70},
            {"身高": 180, "体重": 75},
        ]
        chart_type = suggest_chart_type(result)
        assert chart_type == "scatter"

    def test_recommends_table_for_no_numeric(self):
        """没有数值列 → 表格"""
        result = [
            {"name": "Alice", "city": "北京"},
            {"name": "Bob", "city": "上海"},
        ]
        chart_type = suggest_chart_type(result)
        assert chart_type == "table"

    def test_empty_result_returns_table(self):
        """空数据返回 table"""
        chart_type = suggest_chart_type([])
        assert chart_type == "table"

    def test_date_column_with_chinese_name(self):
        """中文日期列名识别"""
        result = [
            {"日期": "2024-01", "收入": 1000},
            {"日期": "2024-02", "收入": 2000},
        ]
        chart_type = suggest_chart_type(result)
        assert chart_type == "line"


class TestCreateChart:
    """图表生成"""

    def test_bar_chart_returns_figure(self):
        result = [{"name": "A", "value": 10}, {"name": "B", "value": 20}]
        fig = create_chart(result, "bar", title="Test Bar")
        assert isinstance(fig, go.Figure)

    def test_line_chart_returns_figure(self):
        result = [{"月份": "1月", "sales": 100}, {"月份": "2月", "sales": 200}]
        fig = create_chart(result, "line", title="Test Line")
        assert isinstance(fig, go.Figure)

    def test_pie_chart_returns_figure(self):
        result = [{"类别": "A", "销量": 50}, {"类别": "B", "销量": 30}]
        fig = create_chart(result, "pie", title="Test Pie")
        assert isinstance(fig, go.Figure)

    def test_scatter_chart_returns_figure(self):
        result = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        fig = create_chart(result, "scatter", title="Test Scatter")
        assert isinstance(fig, go.Figure)

    def test_empty_data_returns_figure(self):
        """空数据不应崩溃，应返回 Figure"""
        fig = create_chart([], "bar", title="Empty")
        assert isinstance(fig, go.Figure)

    def test_title_in_figure(self):
        result = [{"x": 1, "y": 2}]
        fig = create_chart(result, "bar", title="自定义标题")
        assert "自定义标题" in fig.layout.title.text


class TestCreateDataTableFigure:
    """表格图表"""

    def test_returns_figure(self):
        result = [{"name": "Alice", "age": 25}, {"name": "Bob", "age": 30}]
        fig = create_data_table_figure(result)
        assert isinstance(fig, go.Figure)

    def test_empty_data_returns_figure(self):
        fig = create_data_table_figure([])
        assert isinstance(fig, go.Figure)


class TestResultToDf:
    """list[dict] → DataFrame 转换"""

    def test_converts_to_dataframe(self):
        result = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        df = result_to_df(result)
        assert len(df) == 2
        assert list(df.columns) == ["a", "b"]
