# ============================================================
# AI 智能数据分析助手 — Docker 镜像
# ============================================================
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（SQLite 相关）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件（利用 Docker 缓存层，代码变了也不用重装依赖）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/data/uploads

# 暴露 Streamlit 端口
EXPOSE 8501

# 启动命令
CMD ["streamlit", "run", "streamlit_app.py", "--server.headless", "true", "--server.port", "8501", "--server.address", "0.0.0.0"]
