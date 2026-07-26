FROM python:3.11-slim

LABEL description="QB Movie Manager - qBittorrent + TMDB movie deduplication tool"
LABEL maintainer="Jioyzen"

# 安装系统依赖：mediainfo、cifs-utils（SMB挂载）
RUN apt-get update && apt-get install -y --no-install-recommends \
    mediainfo \
    cifs-utils \
    && rm -rf /var/lib/apt/lists/*

# 创建应用目录和配置目录
WORKDIR /app
COPY . .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 创建配置持久化目录
RUN mkdir -p /config

# 默认环境变量
ENV QB_MOVIE_CONFIG=/config/config.json
ENV PYTHONUNBUFFERED=1

EXPOSE 8090

ENTRYPOINT ["/app/entrypoint.sh"]