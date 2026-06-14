"""
全局配置管理
负责读取 .env 环境变量，提供统一的配置入口
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """应用配置类，所有配置项从环境变量读取，有合理默认值"""

    # --- 数据库 ---
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/analyst.db")

    # --- LLM ---
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
    LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

    # --- 文件 ---
    UPLOAD_DIR = "data/uploads"
    MAX_FILE_SIZE_MB = 50

    @classmethod
    def validate(cls) -> list[str]:
        """
        验证必要配置项，返回缺失项列表
        返回空列表 = 全部通过
        """
        errors = []
        if not cls.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY 未设置，请在 .env 中配置")
        return errors


# 模块级单例
config = Config()
