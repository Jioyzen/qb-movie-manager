#!/bin/bash
# QB Movie Manager - 容器入口脚本
#
# 容器化部署说明：
#   - /config 目录挂载到宿主机，用于持久化配置（config.json）
#   - /data 目录可选挂载，用于本地路径模式读取视频文件
#   - 环境变量 QB_MOVIE_CONFIG 指向 /config/config.json
#
# 使用示例（docker-compose）见 docker-compose.yml

set -e

# 如果 /config 为空，从默认配置模板初始化
if [ ! -f "$QB_MOVIE_CONFIG" ]; then
    echo "[entrypoint] 初始化配置: $QB_MOVIE_CONFIG"
    if [ -f /app/data/config.example.json ]; then
        cp /app/data/config.example.json "$QB_MOVIE_CONFIG"
        echo "[entrypoint] 已从 config.example.json 创建初始配置"
        echo "[entrypoint] 请访问 WebUI 配置页面完成参数设置"
    else
        # 由 config.py 的 DEFAULTS 自动创建
        echo "[entrypoint] 首次启动，配置将由应用自动创建"
    fi
fi

echo "[entrypoint] 启动 QB Movie Manager..."
exec python3 /app/app.py