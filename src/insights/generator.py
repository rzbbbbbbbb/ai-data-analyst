"""
AI 数据洞察生成器
直接分析 SQL 查询返回的原始数据，生成业务分析报告。

与 Agent 的区别：
- Agent 负责：自然语言 → SQL → 执行 → 初步解读
- Insights 负责：基于实际查询数据，生成深度业务分析报告

能力展示：
- LLM API 调用（OpenAI 兼容接口）
- Prompt Engineering：将原始数据 + 业务问题 + 分析框架组合
- 结构化输出：关键发现 → 数据洞察 → 业务建议 → 深挖方向
"""
from __future__ import annotations
from openai import OpenAI
from src.config import config


class InsightsGenerator:
    """
    对 SQL 查询的**原始结果数据**进行 AI 深度解读。
    直接在真实数据上做分析，不依赖 Agent 的文字转述。
    """

    def __init__(self):
        self.client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
        )

    def generate(
        self,
        question: str,
        sql: str,
        query_data: list[dict] | None = None,
        table_context: str = "",
    ) -> str:
        """
        基于原始查询数据，生成深度洞察。

        Args:
            question: 用户原始问题
            sql: 执行的 SQL 语句
            query_data: 实际查询返回的数据（list[dict]），直接基于它做分析
            table_context: 数据表上下文
        """
        # ---- 从原始数据构建精确的摘要（而非依赖 Agent 转述） ----
        data_summary = self._build_data_summary(query_data)

        sql_snippet = sql[:800] if sql else ""

        system_prompt = _build_insight_system_prompt()
        user_prompt = f"""## 用户问题
{question}

## 执行的 SQL
```sql
{sql_snippet}
```

## 实际查询结果（这是你分析的唯一数据依据）
{data_summary}

## 数据表信息
{table_context or "暂无额外上下文"}

⚠️ 重要：请严格基于「实际查询结果」中的数据进行分析，不要编造或猜测数据。如果数据不足以支撑某个结论，请诚实说明。

请开始分析。"""

        try:
            response = self.client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,   # 基于数据的分析应偏向确定性
                max_tokens=1500,
            )
            return response.choices[0].message.content or "未能生成洞察"

        except Exception as e:
            return f"⚠️ 洞察生成失败: {str(e)}"

    def _build_data_summary(self, query_data: list[dict] | None) -> str:
        """
        从原始查询数据构建结构化的数据摘要，直接喂给 LLM。
        不经过 Agent 文字转述，保证数字准确。
        """
        if not query_data:
            return "（无查询结果数据）"

        rows = len(query_data)
        if rows == 0:
            return "（查询返回 0 行）"

        columns = list(query_data[0].keys())

        # 区分数值列和文本列
        numeric_cols = []
        text_cols = []
        for c in columns:
            sample_vals = [r[c] for r in query_data[:20] if r[c] is not None]
            if sample_vals and all(isinstance(v, (int, float)) for v in sample_vals):
                numeric_cols.append(c)
            else:
                text_cols.append(c)

        parts = [f"共 {rows} 条记录，{len(columns)} 列\n"]

        # 数值列统计（精确值，不近似）
        if numeric_cols:
            parts.append("## 数值列精确统计")
            for col in numeric_cols[:8]:
                values = [r[col] for r in query_data if r[col] is not None]
                if not values:
                    parts.append(f"- {col}: 无数据")
                    continue
                total = sum(values)
                avg = total / len(values)
                parts.append(
                    f"- {col}: 总和={total}, 均值={avg:.4f}, "
                    f"最小={min(values)}, 最大={max(values)}, "
                    f"非空记录数={len(values)}"
                )
            parts.append("")

        # 文本列 Top 分布
        if text_cols:
            parts.append("## 分类列分布")
            for col in text_cols[:5]:
                # Count frequencies
                from collections import Counter
                freq = Counter(
                    r[col] for r in query_data if r[col] is not None
                )
                top_items = freq.most_common(10)
                if top_items:
                    items_str = ", ".join(
                        f"{k}:{v}" for k, v in top_items
                    )
                    parts.append(f"- {col} 分布: {items_str}")
            parts.append("")

        # 完整原始数据（前 30 行，这是 LLM 分析的直接依据）
        parts.append("## 原始数据（前 30 行）")
        for i, row in enumerate(query_data[:30]):
            row_str = " | ".join(
                f"{k}={v}" for k, v in row.items()
            )
            parts.append(f"  [{i+1}] {row_str}")

        if rows > 30:
            parts.append(f"  ... 还有 {rows - 30} 行")

        return "\n".join(parts)


def _build_insight_system_prompt() -> str:
    """构建洞察生成的 System Prompt"""
    return """你是一位资深商业数据分析师，拥有 10 年行业经验。你擅长从数据中提炼出有价值的业务洞察，并用通俗易懂的语言讲给业务方听。

## 你的分析框架

请按照以下结构输出分析报告：

### 🔍 关键发现
用 2-3 句话总结最重要的发现。直接点出核心数据，不要铺垫。

### 📊 数据洞察
提供 2-3 条具体的数据洞察，每条必须包含：
- 具体的数据数值作为论据（必须来自上方「实际查询结果」，不要编造）
- 对数据的业务解读
- 可能的业务原因或含义

### 💡 业务建议
基于数据给出 1-2 条可执行的业务建议。建议要：
- 具体可落地（不是"提升用户体验"这种空话）
- 有优先级（先说最重要的）
- 可衡量（能判断是否达成）

### 🧭 值得深挖的方向
提出 1-2 个值得进一步分析的数据维度或问题。

## 语言风格
- 用中文，专业但不晦涩
- 避免"这表明""可以看出"等套话开头
- 数据指标要加粗或标注
- 不要复述原始数据，要提炼结论和观点
- **所有引用的数字必须与实际查询结果一致，严禁编造数据**"""
