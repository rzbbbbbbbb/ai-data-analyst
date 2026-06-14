"""
SQL 提取测试 —— 验证从 LLM 响应中解析 SQL 的正则逻辑

这是项目最关键的测试：如果 SQL 提取错误，后续所有功能都白做。
覆盖场景：
1. markdown 代码块中的 SELECT
2. markdown 代码块中的 CTE (WITH)
3. 无代码块的纯文本 SELECT
4. 无代码块的 WITH ... SELECT
5. function calling 的 sql_db_query 调用
6. 不含 SQL 的响应（应返回 None）
7. 无效 SQL（不含 FROM 等）
"""
import pytest
from src.agent.sql_agent import SQLAnalystAgent


@pytest.fixture
def agent():
    """创建 Agent 实例用于测试（会自动连接内存库）"""
    return SQLAnalystAgent()


class TestExtractSqlFromText:
    """测试 _extract_sql_from_text —— 从文本中提取 SQL"""

    def test_extract_markdown_sql_block(self, agent):
        """提取 ```sql ... ``` 代码块中的 SELECT"""
        text = """以下是分析结果：
```sql
SELECT name, age FROM "test_users" WHERE score > 80 LIMIT 10;
```
共 4 条记录。"""
        result = agent._extract_sql_from_text(text)
        assert result is not None
        assert "SELECT" in result.upper()
        assert "test_users" in result
        assert "LIMIT 10" in result

    def test_extract_markdown_sql_block_with_cte(self, agent):
        """提取 ```sql ... ``` 代码块中的 WITH CTE"""
        text = """```sql
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (ORDER BY score DESC) AS rn
    FROM "test_users"
)
SELECT * FROM ranked WHERE rn <= 3 LIMIT 10;
```
排名结果如上。"""
        result = agent._extract_sql_from_text(text)
        assert result is not None
        assert result.upper().startswith("WITH")
        assert "ROW_NUMBER()" in result
        assert "ranked" in result

    def test_extract_generic_code_block(self, agent):
        """提取无语言标记的 ``` 代码块"""
        text = """执行以下 SQL：
```
SELECT name, score FROM "test_users" ORDER BY score DESC LIMIT 5;
```"""
        result = agent._extract_sql_from_text(text)
        assert result is not None
        assert "SELECT" in result.upper()
        assert "ORDER BY" in result

    def test_extract_raw_select_in_text(self, agent):
        """提取嵌在普通文本中的 SELECT 语句"""
        text = """查询完成。使用的 SQL：
SELECT customer, SUM(amount) AS total FROM sales GROUP BY customer ORDER BY total DESC;
结果显示张三消费最高。"""
        result = agent._extract_sql_from_text(text)
        assert result is not None
        assert "SUM(amount)" in result
        assert "GROUP BY" in result

    def test_extract_cte_with_multiple_subqueries(self, agent):
        """提取包含多个子查询的 CTE"""
        text = """分析每个年龄段的 Top 商品：
WITH age_sales AS (
    SELECT age, category, SUM(quantity) AS qty
    FROM users GROUP BY age, category
),
ranked AS (
    SELECT *, RANK() OVER (PARTITION BY age ORDER BY qty DESC) AS rk
    FROM age_sales
)
SELECT age, category, qty FROM ranked WHERE rk = 1 ORDER BY age LIMIT 100;
"""
        result = agent._extract_sql_from_text(text)
        assert result is not None
        # 应该捕获完整的 WITH ... SELECT ... ; 语句
        upper = result.upper()
        assert "WITH" in upper
        assert "SELECT" in upper
        assert "RANK()" in result
        # 至少包含两个 CTE 子句关键字
        assert "age_sales" in result
        assert "ranked" in result

    def test_no_sql_in_text(self, agent):
        """文本中不含 SQL，应返回 None"""
        text = "抱歉，没有找到相关数据，请检查数据表是否存在。"
        result = agent._extract_sql_from_text(text)
        assert result is None

    def test_invalid_sql_no_from(self, agent):
        """不含 FROM 的语句不应被当作有效 SQL"""
        text = "SELECT 1 + 1;"
        result = agent._extract_sql_from_text(text)
        # SELECT 1+1 不含 FROM，不是有效的查询 SQL
        assert result is None

    def test_picks_last_sql_block(self, agent):
        """多个 SQL 块时，取最后一个（通常是最终查询）"""
        text = """先试试：
```sql
SELECT * FROM "test_users";
```
不对，应该是：
```sql
SELECT name, age FROM "test_users" WHERE is_active = TRUE LIMIT 10;
```"""
        result = agent._extract_sql_from_text(text)
        assert "is_active" in result
        assert "WHERE" in result


class TestExtractSql:
    """测试 _extract_sql —— 整合 function calling + 文本提取"""

    def test_from_function_calling(self, agent, mock_agent_response_function_call):
        """优先从 function calling 提取"""
        result = agent._extract_sql(mock_agent_response_function_call)
        assert result == 'SELECT name, age, score FROM "test_users" LIMIT 10;'

    def test_fallback_to_text_when_no_function_call(
        self, agent, mock_agent_response_select
    ):
        """无 function calling 时降级到文本提取"""
        result = agent._extract_sql(mock_agent_response_select)
        assert result is not None
        assert "score > 80" in result

    def test_no_sql_anywhere(self, agent, mock_agent_response_no_sql):
        """两种方式都找不到 SQL"""
        result = agent._extract_sql(mock_agent_response_no_sql)
        assert result is None


class TestIsValidSql:
    """测试 _is_valid_sql 校验方法"""

    def test_valid_select(self, agent):
        assert agent._is_valid_sql('SELECT * FROM "users" LIMIT 10;')

    def test_valid_with_cte(self, agent):
        assert agent._is_valid_sql(
            "WITH t AS (SELECT * FROM users) SELECT * FROM t LIMIT 10;"
        )

    def test_no_from(self, agent):
        assert not agent._is_valid_sql("SELECT 1+1;")

    def test_too_short(self, agent):
        assert not agent._is_valid_sql("SEL")

    def test_update_rejected(self, agent):
        # UPDATE 应该被 _is_valid_sql 拒绝（不以 SELECT/WITH 开头）
        assert not agent._is_valid_sql("UPDATE users SET name='x';")


class TestQueryMethod:
    """测试 query() 公开方法"""

    def test_query_basic_table(self, agent, engine):
        """查询已存在的表（使用内存库中的 test_users 表）"""
        result = agent.query("查询年龄大于 25 岁的用户", current_table="test_users")
        assert result["success"] is True
        assert result["answer"] is not None
        # SQL 中应引用当前表
        if result["sql"]:
            assert "test_users" in result["sql"]

    def test_query_enhances_with_current_table(self, agent):
        """current_table 参数应注入问题上下文"""
        # 直接调用，验证没有崩溃
        result = agent.query(
            "统计用户数量",
            current_table="test_users",
        )
        assert result["success"] is True
