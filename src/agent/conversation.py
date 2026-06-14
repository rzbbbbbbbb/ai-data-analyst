"""
多轮对话数据模型与辅助函数

纯函数，不依赖 Streamlit，可直接在 Agent 和测试中使用。

数据模型：
  conversation = {
      "id": "conv_20260614_153022",
      "title": "各区域的销售额排名",       # 第一条用户问题（截断至 50 字）
      "table": "sales_data",
      "created_at": "2026-06-14 15:30:22",
      "updated_at": "2026-06-14 15:35:00",
      "messages": [
          {"role": "user", "content": "...", "time": "15:30:22"},
          {"role": "assistant", "content": "...", "sql": "...",
           "query_data": [...], "insight": "...", "chart_type": "bar",
           "time": "15:30:28"},
          ...
      ],
  }

面试亮点：
  - 上下文窗口管理：只保留最近 N 轮对话，平衡记忆与 token 消耗
  - 结构化上下文注入：将对话历史格式化为 LLM 可理解的文本
  - 数据持久化：对话可跨会话保存，支持断点续聊
"""
from __future__ import annotations
from datetime import datetime


# 上下文注入时最多包含的对话轮次（Q&A 对）
MAX_CONTEXT_TURNS = 3

# 结果概要的最大行数（用于注入 LLM 上下文）
MAX_SUMMARY_ROWS = 3


def create_conversation(title: str, table_name: str) -> dict:
    """
    创建一个新的对话。

    Args:
        title: 对话标题（通常是第一条用户问题，会自动截断）
        table_name: 关联的数据表名

    Returns:
        对话 dict，包含 id、title、table、created_at、updated_at、messages
    """
    now = datetime.now()
    return {
        "id": f"conv_{now.strftime('%Y%m%d_%H%M%S')}",
        "title": title[:50] + ("..." if len(title) > 50 else ""),
        "table": table_name,
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "messages": [],
    }


def add_message(
    conv: dict,
    role: str,
    content: str,
    **kwargs,
) -> dict:
    """
    向对话中添加一条消息。

    Args:
        conv: 对话 dict（会被原地修改）
        role: "user" 或 "assistant"
        content: 消息正文
        **kwargs: 附加字段（sql、query_data、insight、chart_type 等）

    Returns:
        修改后的 conv（同时也是原地修改）
    """
    msg = {
        "role": role,
        "content": content,
        "time": datetime.now().strftime("%H:%M:%S"),
    }
    msg.update(kwargs)
    conv["messages"].append(msg)
    conv["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return conv


def build_result_summary(query_data: list[dict] | None) -> str:
    """
    从查询结果构建一个简洁的文本摘要，用于注入 LLM 上下文。

    不需要 LLM 参与——本地计算行数、列名和前几行样本。

    Args:
        query_data: execute_sql() 返回的 list[dict]

    Returns:
        如 "4 行, 列: region | total_sales | avg_price\n示例行: 华东 | 50000 | 120.5 ..."
    """
    if not query_data:
        return "（空结果集）"

    n_rows = len(query_data)
    columns = list(query_data[0].keys()) if query_data else []

    parts = [f"{n_rows} 行"]
    if columns:
        parts.append(f"列: {' | '.join(columns[:10])}")

    # 附上前几行样本数据（最多 3 行，每行最多 5 列）
    sample_lines = []
    for row in query_data[:MAX_SUMMARY_ROWS]:
        values = []
        for i, (k, v) in enumerate(row.items()):
            if i >= 5:
                break
            # 截断过长的值
            v_str = str(v)
            if len(v_str) > 30:
                v_str = v_str[:27] + "..."
            values.append(v_str)
        sample_lines.append(" | ".join(values))
    if sample_lines:
        parts.append("示例行: " + "  |  ".join(sample_lines))

    return "\n".join(parts)


def format_context_for_llm(
    messages: list[dict],
    max_turns: int = MAX_CONTEXT_TURNS,
) -> str:
    """
    将最近的对话消息格式化为 LLM 上下文文本。

    只取最近 N 轮（Q&A 对），跳过没有 SQL 的 assistant 消息
    （如错误回复），平衡上下文长度和信息密度。

    Args:
        messages: 对话消息列表
        max_turns: 最多包含的 Q&A 轮次

    Returns:
        上下文文本，如果 messages 为空则返回空字符串
    """
    if not messages:
        return ""

    # 过滤出有效的 Q&A 对（assistant 消息必须有 sql）
    pairs = []
    i = 0
    while i < len(messages):
        user_msg = None
        assistant_msg = None

        if messages[i].get("role") == "user":
            user_msg = messages[i]
            # 查找紧随其后的 assistant 消息
            if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
                assistant_msg = messages[i + 1]
                i += 2
            else:
                i += 1
        else:
            i += 1
            continue

        if user_msg and assistant_msg and assistant_msg.get("sql"):
            pairs.append((user_msg, assistant_msg))

    # 只取最近 max_turns 轮
    recent_pairs = pairs[-max_turns:] if len(pairs) > max_turns else pairs

    if not recent_pairs:
        return ""

    lines = ["## 对话上下文"]
    lines.append("（以下是之前的对话历史，请基于上下文理解当前问题的意图）")
    lines.append("")

    for user_msg, asst_msg in recent_pairs:
        lines.append(f"用户: {user_msg['content']}")
        lines.append(f"SQL: {asst_msg.get('sql', '')}")

        # 结果概要
        qd = asst_msg.get("query_data", [])
        summary = build_result_summary(qd)
        lines.append(f"结果概要: {summary}")

        # 如果有 insight，也包含（截断）
        insight = asst_msg.get("insight", "")
        if insight:
            insight_short = insight[:200] + ("..." if len(insight) > 200 else "")
            lines.append(f"洞察: {insight_short}")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def get_last_n_messages(messages: list[dict], n: int) -> list[dict]:
    """
    获取最后 N 条消息（用于传给 Agent 的 conversation_history）。

    Args:
        messages: 对话消息列表
        n: 要获取的消息数

    Returns:
        最后 N 条消息的副本（浅拷贝，不含 query_data 以减少内存）
    """
    subset = messages[-n:] if len(messages) > n else messages

    # 返回浅拷贝，但移除 query_data 中的大量数据以避免 token 浪费
    # query_data 在 format_context_for_llm 中已经转为文本概要
    result = []
    for msg in subset:
        msg_copy = {k: v for k, v in msg.items() if k != "query_data"}
        # 保留 query_data 但只保留前 5 行用于 build_result_summary
        qd = msg.get("query_data", [])
        if qd:
            msg_copy["query_data"] = qd[:5]
        result.append(msg_copy)
    return result
