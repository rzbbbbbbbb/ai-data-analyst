"""
LangChain SQL Agent —— 自然语言 → SQL 查询引擎

这是全项目最核心的模块，展示的能力：
1. LangChain 框架的使用（SQL Database Agent）
2. Prompt Engineering（Schema 注入 + Few-shot 示例 + 规则约束）
3. LLM 调用与错误处理

原理：
  用户说 "上个月销售额Top5的商品"
  → Agent 读取表 Schema
  → 生成 SQL → 执行 → 返回结果

面试要点：
  - 为什么用 LangChain？因为 SQL Database Toolkit 封装了 Schema 注入、
    错误重试、工具调用链，比自己调 Function Calling 更稳定
  - 如何保证 SQL 准确率？详尽的 System Prompt + 真实 Schema + Few-shot 示例
"""
from __future__ import annotations
from typing import Optional
from langchain_openai import ChatOpenAI
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent
from langchain.agents.agent_types import AgentType
from src.config import config
from src.database.schema import get_table_schema_for_llm
from src.agent.conversation import format_context_for_llm, get_last_n_messages
from sqlalchemy import create_engine


# ============================================================
# System Prompt 模板
# 注意：这个字符串会被 create_sql_agent 内部调用 .format(dialect=, top_k=)，
# 所以里面不能有 {xxx} 格式的文本（会被当成占位符）。
# 用 <<<SCHEMA_HERE>>> 作为 schema 占位符，在 __init__ 中替换。
# ============================================================
SCHEMA_PROMPT_TEMPLATE = r"""你是一个资深 SQL 数据分析师，精通 PostgreSQL 和 SQLite。你的任务是把用户的自然语言问题转换为准确、高效的 SQL 查询。

## 数据库 Schema（请仔细阅读，这是你写 SQL 的唯一依据）

<<SCHEMA_HERE>>

## SQL 编写规范

### 安全规则（最高优先级）
1. 只能写 SELECT 查询，禁止 INSERT / UPDATE / DELETE / DROP / CREATE / ALTER
2. 避免 SELECT *，明确列出需要的列
3. 表名和列名必须用双引号包裹，例如 "表名"."列名"
4. 所有查询必须加 LIMIT，默认 LIMIT 100

### 高级 SQL 特性（优先使用，展示专业能力）
5. 使用 CTE（WITH 子句）让复杂查询更清晰
6. 窗口函数：ROW_NUMBER、RANK、DENSE_RANK、LAG、LEAD、SUM/AVG OVER
7. 条件聚合：CASE WHEN + SUM/COUNT
8. 多表 JOIN 时注意 JOIN 类型（LEFT/INNER/CROSS）
9. GROUP BY 配合 HAVING 做分组筛选
10. 子查询和 EXISTS / NOT EXISTS

### 编码细节
11. 字符串值用单引号
12. 中文字段用 LIKE '%关键词%' 匹配
13. 日期范围用 BETWEEN 或 >= AND <=
14. 聚合列必须用 AS 给中文别名（方便非技术人员阅读）
15. 用 NULLIF 避免除零错误
16. 用 COALESCE 处理 NULL 值

### 常见分析需求的 SQL 模板

**环比增长率：**
WITH monthly AS (
    SELECT DATE_TRUNC('month', "日期列") AS month,
           SUM("金额列") AS total
    FROM "表名"
    GROUP BY DATE_TRUNC('month', "日期列")
)
SELECT month AS "月份", total AS "当月销售额",
       LAG(total) OVER (ORDER BY month) AS "上月销售额",
       ROUND((total - LAG(total) OVER (ORDER BY month)) * 100.0
             / NULLIF(LAG(total) OVER (ORDER BY month), 0), 2) AS "环比增长率%"
FROM monthly ORDER BY month LIMIT 50;

**Top N 排名：**
SELECT * FROM (
    SELECT "类别列", "数值列",
           RANK() OVER (ORDER BY "数值列" DESC) AS "排名"
    FROM "表名"
) ranked WHERE "排名" <= 10 LIMIT 50;

**分层分析（CASE WHEN 分段）：**
SELECT
    CASE
        WHEN "数值列" < 100 THEN '低'
        WHEN "数值列" < 500 THEN '中'
        ELSE '高'
    END AS "分层",
    COUNT(*) AS "数量",
    ROUND(AVG("数值列"), 2) AS "平均值"
FROM "表名"
GROUP BY "分层" ORDER BY "数量" DESC LIMIT 50;

## 工作流程

1. 先理解用户的业务问题
2. 仔细查看上方 Schema，确认要用的表和列
3. 写出 SQL 并执行
4. 如果报错，分析错误原因并修正
5. 返回最终结果时，用中文解读数据含义

现在，请开始分析用户的提问。"""


class SQLAnalystAgent:
    """自然语言 → SQL 的智能 Agent"""

    def __init__(self):
        # ---- 1. 初始化 LLM ----
        self.llm = ChatOpenAI(
            model=config.LLM_MODEL,
            temperature=0,          # SQL 生成必须确定性，不能有随机性
            openai_api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
            max_tokens=2000,
        )

        # ---- 2. 连接数据库 ----
        self.db = SQLDatabase.from_uri(config.DATABASE_URL)

        # ---- 3. 构建增强 System Prompt ----
        self.system_prefix = self._build_system_prefix()

        # ---- 4. 创建 Agent ----
        self.agent_executor = create_sql_agent(
            llm=self.llm,
            db=self.db,
            agent_type=AgentType.OPENAI_FUNCTIONS,  # 使用 OpenAI Functions 模式
            verbose=False,
            max_iterations=5,               # 最多 5 次尝试（生成→执行→修正）
            max_execution_time=30,           # 超时 30 秒
            handle_parsing_errors=True,
            prefix=self.system_prefix,      # 自定义 System Prompt
            top_k=50,                       # 每次取最多 50 行样本
        )

    def _build_system_prefix(self) -> str:
        """
        构建 System Prompt（Prefix）。
        这是 Prompt Engineering 的核心 —— 把表结构、SQL 规范、示例都写进去。

        面试亮点：
        - Schema 注入：让 LLM 在生成 SQL 前"看到"真实的表结构
        - Few-shot 示例：教 LLM 正确的 SQL 写法
        - 规则约束：防止 LLM 生成危险或不正确的 SQL
        """
        # 获取所有表的结构信息
        tables = self.db.get_usable_table_names()
        schema_lines = []
        for t in tables:
            try:
                info = get_table_schema_for_llm(t)
                schema_lines.append(info)
                schema_lines.append("─" * 50)
            except Exception as e:
                schema_lines.append(f"表: {t}（获取详情失败）")
        schema_text = "\n".join(schema_lines)

        prompt = SCHEMA_PROMPT_TEMPLATE.replace("<<SCHEMA_HERE>>", schema_text)
        return prompt

    # ---------------------------------------------------------------
    # 公开方法
    # ---------------------------------------------------------------

    def query(
        self,
        question: str,
        current_table: str | None = None,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        """
        执行自然语言查询。

        Args:
            question: 用户的自然语言问题，如"上个月各品类的销售额排名"
            current_table: 当前用户选中的表名，用于强调优先使用
            conversation_history: 对话历史消息列表（最近 N 条），用于多轮对话上下文

        Returns:
            {
                "success": bool,
                "question": str,
                "answer": str,        # Agent 的最终回答
                "sql": str | None,    # 生成的 SQL（如果能提取到）
                "error": str | None,
            }
        """
        try:
            # 构建增强问题：表上下文 + 对话历史 + 用户问题
            enhanced_question = question

            # 1. 当前表提示
            if current_table:
                enhanced_question = (
                    f"【当前工作表: \"{current_table}\"，请使用这张表来回答问题】\n{enhanced_question}"
                )

            # 2. 对话历史上下文（多轮对话能力）
            if conversation_history:
                context = format_context_for_llm(conversation_history)
                if context:
                    enhanced_question = (
                        f"{context}\n## 当前问题\n{enhanced_question}"
                    )

            # 调用 Agent
            response = self.agent_executor.invoke({"input": enhanced_question})

            output = response.get("output", "")

            # 尝试从中间步骤提取 SQL
            sql_text = self._extract_sql(response)

            return {
                "success": True,
                "question": question,
                "answer": output,
                "sql": sql_text,
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "question": question,
                "answer": None,
                "sql": None,
                "error": f"查询失败: {str(e)}",
            }

    def _extract_sql(self, response: dict) -> str | None:
        """
        从 Agent 的响应中提取最终执行的 SQL 语句。
        优先从 function calling 的 sql_db_query 工具调用中提取；
        如果模型不支持 function calling（如某些 deepseek 版本），
        则从文本回复的 markdown 代码块中提取。
        """
        # 方案 A: 从 function calling 工具调用中提取
        steps = response.get("intermediate_steps", [])
        for step in reversed(steps):
            if len(step) >= 1:
                action = step[0]
                tool = getattr(action, "tool", "")
                if tool == "sql_db_query":
                    return action.tool_input

        # 方案 B: 从文本回复中提取 SQL 代码块（兼容不支持 function calling 的模型）
        output = response.get("output", "")
        return self._extract_sql_from_text(output)

    def _extract_sql_from_text(self, text: str) -> str | None:
        """从 LLM 文本回复中解析 SQL 语句（支持 SELECT 和 WITH CTE）"""
        import re

        # 1. 优先匹配 markdown sql 代码块
        pattern = r'```sql\s*\n(.*?)\n```'
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
        if matches:
            sql = matches[-1].strip()
            if self._is_valid_sql(sql):
                return sql

        # 2. 尝试匹配任意 markdown 代码块（无语言标记）
        pattern = r'```\s*\n((?:SELECT|WITH)\s+.*?)\n```'
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
        if matches:
            sql = matches[-1].strip()
            if self._is_valid_sql(sql):
                return sql

        # 3. 匹配 WITH ... SELECT 语句（CTE 查询）
        #    WITH 子句包含嵌套括号，用贪婪匹配抓到最后一个 SELECT 及其后续
        pattern = r'(WITH\s+\w+\s+AS\s*\(.*?\)\s*SELECT\s+.+?;)'
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
        if matches:
            sql = matches[-1].strip()
            if self._is_valid_sql(sql):
                return sql

        # 4. 匹配普通 SELECT 语句（以分号或文本结束为界）
        pattern = r'(SELECT\s+.+?(?:;|$))'
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
        if matches:
            sql = matches[-1].strip()
            if self._is_valid_sql(sql):
                return sql

        return None

    @staticmethod
    def _is_valid_sql(sql: str) -> bool:
        """检查字符串是否像一条完整的 SQL 查询"""
        upper = sql.upper()
        has_query = upper.startswith("SELECT") or upper.startswith("WITH")
        has_from = "FROM" in upper
        return has_query and has_from and len(sql) > 20

    def get_available_tables(self) -> list[str]:
        """返回数据库中可用的表名列表"""
        return self.db.get_usable_table_names()


# ================================================================
# 模块级单例（避免重复初始化 Agent，节省 token 和时间）
# ================================================================

_agent_instance: Optional[SQLAnalystAgent] = None


def get_agent() -> SQLAnalystAgent:
    """获取 SQL Analyst Agent 单例"""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = SQLAnalystAgent()
    return _agent_instance
