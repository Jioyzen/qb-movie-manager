FROM python:3.11-slim

LABEL description="QB Movie Manager - qBittorrent + TMDB movie deduplication tool"
LABEL maintainer="Jioyzen"

# 安装系统依赖：mediainfo（深度分析）、cifs-utils（SMB挂载，可选）、sudo
RUN apt-get update && apt-get install -y --no-install-recommends \
    mediainfo \
    cifs-utils \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# 创建应用目录和配置目录
WORKDIR /app
COPY . .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 创建非root用户（用于运行应用，降低权限）
# 但保留 sudo 能力用于 SMB 挂载（需容器以 --privileged 运行）
RUN useradd -m -u 1000 qb && \
    echo "qb ALL=(ALL) NOPASSWD: /usr/sbin/mount.cifs, /usr/sbin/umount.cifs" >> /etc/sudoers

# 将应用目录归 qb 用户所有，确保可读
RUN chown -R qb:qb /app

# 创建配置持久化目录和媒体目录占位
RUN mkdir -p /config && chown qb:qb /config

# 默认环境变量
ENV QB_MOVIE_CONFIG=/config/config.json
ENV PYTHONUNBUFFERED=1

EXPOSE 8090

# 使用非root用户运行
USER qb

ENTRYPOINT ["/app/entrypoint.sh"]