"""
共享测试 fixtures：内存数据库、样本 DataFrame、模拟 Agent 响应
"""
import pytest
import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database.connection import get_engine, execute_sql
from src.database.schema import create_table_from_df
from src.config import config


@pytest.fixture(autouse=True)
def override_db_url(monkeypatch):
    """所有测试强制使用 SQLite 内存库，避免污染开发数据库"""
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///:memory:")


@pytest.fixture
def engine():
    """返回内存数据库引擎（每次测试后清理）"""
    eng = get_engine()
    # 创建基础测试表
    df = pd.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "name": ["Alice", "Bob", "Charlie", "Diana", "Eve"],
        "age": [25, 30, 35, 28, 22],
        "score": [88.5, 92.0, 79.5, 95.0, 81.0],
        "is_active": [True, True, False, True, False],
    })
    create_table_from_df(df, "test_users")
    yield eng
    # 清理
    eng.dispose()


@pytest.fixture
def sales_df():
    """样本销售数据 DataFrame（不带时间列，避免 SQLite 绑定问题）"""
    return pd.DataFrame({
        "order_id": [1, 2, 3, 4, 5],
        "customer": ["张三", "李四", "张三", "王五", "李四"],
        "product": ["商品A", "商品B", "商品A", "商品C", "商品B"],
        "quantity": [2, 1, 3, 1, 2],
        "price": [100.0, 200.0, 100.0, 150.0, 200.0],
        "amount": [200.0, 200.0, 300.0, 150.0, 400.0],
    })


@pytest.fixture
def dirty_df():
    """包含脏数据的 DataFrame"""
    df = pd.DataFrame({
        "Name": ["Alice", "Bob", "Alice", None, "Eve"],
        "Age": [25, None, 25, 30, None],
        "City": ["北京", "上海", "北京", "广州", ""],
    })
    # 添加一个完全重复的行
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    return df


@pytest.fixture
def mock_agent_response_select():
    """模拟 LLM 返回的简单 SELECT 查询"""
    return {
        "intermediate_steps": [],
        "output": """以下是查询结果：

```sql
SELECT name, age, score FROM "test_users" WHERE score > 80 ORDER BY score DESC LIMIT 10;
```

共找到 4 条记录，分别为...""",
    }


@pytest.fixture
def mock_agent_response_cte():
    """模拟 LLM 返回的 CTE（WITH）查询"""
    return {
        "intermediate_steps": [],
        "output": """各用户年龄段购买最多的商品分析：

```sql
WITH age_category_sales AS (
    SELECT "age", "category", SUM("quantity") AS total_qty
    FROM "淘宝用户行为"
    GROUP BY "age", "category"
),
ranked_sales AS (
    SELECT "age", "category", total_qty,
        ROW_NUMBER() OVER (PARTITION BY "age" ORDER BY total_qty DESC) AS rn
    FROM age_category_sales
)
SELECT "age" AS "年龄", "category" AS "最常购买的商品类别", total_qty AS "总购买件数"
FROM ranked_sales
WHERE rn = 1
ORDER BY "age"
LIMIT 100;
```""",
    }


@pytest.fixture
def mock_agent_response_no_sql():
    """模拟 LLM 返回的不含 SQL 的响应"""
    return {
        "intermediate_steps": [],
        "output": "抱歉，我无法回答这个问题，因为没有找到相关的数据表。",
    }


@pytest.fixture
def mock_agent_response_function_call():
    """模拟有 function calling 的 Agent 响应"""
    from unittest.mock import MagicMock

    action = MagicMock()
    action.tool = "sql_db_query"
    action.tool_input = 'SELECT name, age, score FROM "test_users" LIMIT 10;'

    return {
        "intermediate_steps": [(action, "observation text")],
        "output": "查询执行成功，返回了 5 条记录...",
    }
