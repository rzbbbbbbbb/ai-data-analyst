"""
数据库连接管理
- 单例引擎，避免重复创建连接
- execute_sql() 执行任意 SQL 并返回字典列表
- 支持 SQLite（本地开发）和 PostgreSQL（生产展示）
"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import create_engine, Engine, text, inspect
from sqlalchemy.orm import sessionmaker
from src.config import config
import os

_engine: Optional[Engine] = None
SessionLocal = None


def get_engine() -> Engine:
    """获取数据库引擎（单例模式，整个应用只创建一个连接）"""
    global _engine, SessionLocal
    if _engine is None:
        # SQLite 需要确保目录存在
        if config.DATABASE_URL.startswith("sqlite"):
            db_path = config.DATABASE_URL.replace("sqlite:///", "")
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

        _engine = create_engine(
            config.DATABASE_URL,
            echo=False,          # 不打印 SQL 日志（调试时可改为 True）
            pool_pre_ping=True,  # 连接前检测可用性
        )
        SessionLocal = sessionmaker(bind=_engine)
    return _engine


def get_session():
    """获取一个新的数据库会话"""
    if SessionLocal is None:
        get_engine()
    return SessionLocal()


def execute_sql(sql: str, params: dict | None = None) -> list[dict]:
    """
    执行 SQL 查询并返回结果。
    - SELECT: 返回 dict 列表，每行是一个 dict
    - DDL/DML: 返回空列表，自动 commit

    这是整个项目最基础的方法，所有查询都通过它执行。
    """
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})

        if result.returns_rows:
            columns = list(result.keys())
            rows = result.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        else:
            conn.commit()
            return []


def get_all_tables() -> list[str]:
    """获取数据库中所有用户创建的表的列表"""
    engine = get_engine()
    insp = inspect(engine)
    return insp.get_table_names()


def get_column_info(table_name: str) -> list[dict]:
    """
    获取指定表的列信息（通过 information_schema）
    这是面试中常考的系统表查询
    """
    if config.DATABASE_URL.startswith("sqlite"):
        # SQLite 用 PRAGMA
        result = execute_sql(f'PRAGMA table_info("{table_name}")')
        return [
            {
                "column_name": r["name"],
                "data_type": r["type"],
                "is_nullable": "YES" if not r.get("notnull") else "NO",
            }
            for r in result
        ]
    else:
        # PostgreSQL 用 information_schema
        result = execute_sql(f"""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = '{table_name}'
            ORDER BY ordinal_position
        """)
        return result
