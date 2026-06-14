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


# ============================================================
# 多轮对话测试
# ============================================================

from src.agent.conversation import (
    create_conversation,
    add_message,
    format_context_for_llm,
    build_result_summary,
    get_last_n_messages,
)


class TestCreateConversation:
    """测试 create_conversation"""

    def test_creates_with_expected_keys(self):
        conv = create_conversation("各区域的销售额排名", "sales_data")
        assert conv["id"].startswith("conv_")
        assert conv["title"] == "各区域的销售额排名"
        assert conv["table"] == "sales_data"
        assert "created_at" in conv
        assert "updated_at" in conv
        assert conv["messages"] == []

    def test_truncates_long_title(self):
        title = "这是一个" + "非常" * 30 + "长的标题用来测试截断功能"
        conv = create_conversation(title, "test")
        assert len(conv["title"]) <= 53  # 50 + "..."
        assert conv["title"].endswith("...")

    def test_short_title_not_truncated(self):
        conv = create_conversation("短标题", "test")
        assert conv["title"] == "短标题"
        assert "..." not in conv["title"]


class TestAddMessage:
    """测试 add_message"""

    def test_adds_user_message(self):
        conv = create_conversation("测试", "test")
        add_message(conv, "user", "各区域的销售额")
        assert len(conv["messages"]) == 1
        assert conv["messages"][0]["role"] == "user"
        assert conv["messages"][0]["content"] == "各区域的销售额"
        assert "time" in conv["messages"][0]

    def test_adds_assistant_message_with_extras(self):
        conv = create_conversation("测试", "test")
        add_message(conv, "assistant", "查询完成", sql="SELECT * FROM t", chart_type="bar")
        assert len(conv["messages"]) == 1
        msg = conv["messages"][0]
        assert msg["role"] == "assistant"
        assert msg["sql"] == "SELECT * FROM t"
        assert msg["chart_type"] == "bar"

    def test_updates_updated_at(self):
        conv = create_conversation("测试", "test")
        # Add a message with a small delay to guarantee timestamp difference
        import time
        time.sleep(1.1)
        old_updated = conv["updated_at"]
        add_message(conv, "user", "新问题")
        assert conv["updated_at"] > old_updated  # 时间应比之前新

    def test_appends_multiple_messages(self):
        conv = create_conversation("测试", "test")
        add_message(conv, "user", "问题1")
        add_message(conv, "assistant", "回答1")
        add_message(conv, "user", "问题2")
        assert len(conv["messages"]) == 3
        assert conv["messages"][0]["content"] == "问题1"
        assert conv["messages"][2]["content"] == "问题2"


class TestBuildResultSummary:
    """测试 build_result_summary"""

    def test_non_empty_result(self):
        data = [
            {"region": "华东", "total": 50000},
            {"region": "华北", "total": 30000},
            {"region": "华南", "total": 20000},
            {"region": "西部", "total": 10000},
        ]
        summary = build_result_summary(data)
        assert "4 行" in summary
        assert "region" in summary
        assert "total" in summary

    def test_empty_result(self):
        assert "空结果集" in build_result_summary([])
        assert "空结果集" in build_result_summary(None)

    def test_truncates_long_values(self):
        long_val = "A" * 50
        data = [{"col": long_val}]
        summary = build_result_summary(data)
        assert "..." in summary
        assert len(summary) < 200  # 应该被截断


class TestFormatContextForLlm:
    """测试 format_context_for_llm"""

    def test_empty_messages(self):
        assert format_context_for_llm([]) == ""
        assert format_context_for_llm(None) == ""

    def test_formats_single_qa_pair(self):
        msgs = [
            {"role": "user", "content": "各区域的销售额排名"},
            {"role": "assistant", "content": "查到了",
             "sql": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region",
             "query_data": [{"region": "华东", "total": 50000}],
             "insight": "华东区域销售额最高"},
        ]
        ctx = format_context_for_llm(msgs)
        assert "对话上下文" in ctx
        assert "各区域的销售额排名" in ctx
        assert "SELECT region" in ctx
        assert "华东区域销售额最高" in ctx
        # format_context_for_llm 只构建历史上下文，不应包含分隔的"## 当前问题"段落
        assert "## 当前问题" not in ctx

    def test_excludes_messages_without_sql(self):
        """没有 SQL 的 assistant 消息应被跳过（错误回复）"""
        msgs = [
            {"role": "user", "content": "问题1"},
            {"role": "assistant", "content": "失败了", "sql": ""},  # 无 SQL
            {"role": "user", "content": "问题2"},
            {"role": "assistant", "content": "成功",
             "sql": "SELECT * FROM t", "query_data": [{"x": 1}]},
        ]
        ctx = format_context_for_llm(msgs)
        assert "问题2" in ctx
        assert "问题1" not in ctx  # 第一个 Q&A 对没有有效 SQL，不应出现

    def test_respects_max_turns(self):
        """只包含最后 max_turns 轮对话"""
        msgs = []
        for i in range(5):
            msgs.append({"role": "user", "content": f"问题{i}"})
            msgs.append({"role": "assistant", "content": f"回答{i}",
                          "sql": f"SELECT {i} FROM t", "query_data": [{"x": i}]})
        ctx = format_context_for_llm(msgs, max_turns=2)
        assert "问题3" in ctx
        assert "问题4" in ctx
        assert "问题0" not in ctx  # 超出 max_turns=2，不应出现


class TestGetLastNMessages:
    """测试 get_last_n_messages"""

    def test_returns_last_n(self):
        msgs = [
            {"role": "user", "content": "m1", "query_data": [{"a": 1}] * 100},
            {"role": "assistant", "content": "m2", "query_data": [{"b": 2}] * 100},
            {"role": "user", "content": "m3", "query_data": [{"c": 3}] * 100},
            {"role": "assistant", "content": "m4", "query_data": [{"d": 4}] * 100},
        ]
        result = get_last_n_messages(msgs, 2)
        assert len(result) == 2
        assert result[0]["content"] == "m3"
        assert result[1]["content"] == "m4"

    def test_returns_all_if_less_than_n(self):
        msgs = [{"role": "user", "content": "m1"}]
        result = get_last_n_messages(msgs, 10)
        assert len(result) == 1

    def test_truncates_query_data(self):
        """query_data 超过 5 行应被截断"""
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a",
             "query_data": [{"x": i} for i in range(100)]},
        ]
        result = get_last_n_messages(msgs, 2)
        assert len(result[1].get("query_data", [])) <= 5


class TestConversationHistoryInQuery:
    """测试 query() 方法接收 conversation_history 参数"""

    def test_query_with_conversation_history(self, agent):
        """带对话历史的查询不应报错"""
        history = [
            {"role": "user", "content": "各区域的销售额排名"},
            {"role": "assistant", "content": "结果是...",
             "sql": "SELECT region, SUM(amount) FROM sales GROUP BY region",
             "query_data": [{"region": "华东", "total": 50000}]},
        ]
        result = agent.query(
            "只看前3个",
            current_table="test_users",
            conversation_history=history,
        )
        assert result["success"] is True
        assert result["answer"] is not None

    def test_query_without_history_still_works(self, agent):
        """不带对话历史的查询应保持兼容"""
        result = agent.query(
            "统计用户数量",
            current_table="test_users",
        )
        assert result["success"] is True
