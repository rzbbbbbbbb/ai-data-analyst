"""
文件加载器
支持 CSV（多编码自动检测）和 Excel（.xlsx / .xls）
"""
import pandas as pd
import os
from datetime import datetime
from src.config import config


def load_file(file_path: str) -> pd.DataFrame:
    """
    根据文件扩展名自动选择合适的加载方式。

    支持的格式：
    - .csv  → 自动检测编码（UTF-8 → GBK → GB2312 → latin1）
    - .xlsx → openpyxl 引擎
    - .xls  → xlrd 引擎
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".csv":
        return _load_csv(file_path)
    elif ext in (".xlsx", ".xls"):
        return _load_excel(file_path, ext)
    else:
        raise ValueError(f"不支持的文件格式: {ext}（支持 CSV、Excel）")


def _load_csv(file_path: str) -> pd.DataFrame:
    """加载 CSV，自动尝试多种编码"""
    encodings = ["utf-8", "utf-8-sig", "gbk", "gb2312", "gb18030", "latin1"]

    for encoding in encodings:
        try:
            df = pd.read_csv(file_path, encoding=encoding)
            return df
        except UnicodeDecodeError:
            continue

    raise ValueError("无法解析 CSV 文件编码，请用 UTF-8 重新保存文件")


def _load_excel(file_path: str, ext: str) -> pd.DataFrame:
    """加载 Excel 文件"""
    if ext == ".xlsx":
        return pd.read_excel(file_path, engine="openpyxl")
    else:
        return pd.read_excel(file_path, engine="xlrd")


def save_uploaded_file(uploaded_file) -> str:
    """
    保存 Streamlit 上传的文件到本地，返回文件路径。
    文件名添加时间戳防止冲突。
    """
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)

    filename = uploaded_file.name
    name, ext = os.path.splitext(filename)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = f"{name}_{timestamp}{ext}"
    save_path = os.path.join(config.UPLOAD_DIR, safe_name)

    with open(save_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    return save_path
