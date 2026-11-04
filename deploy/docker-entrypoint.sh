#!/bin/sh
# 容器入口：迁移数据库，按需写入演示数据，然后启动服务。
set -e
python -m app.cli migrate
if [ "${YIHU_SEED_DEMO:-false}" = "true" ]; then
    python -m app.cli seed
fi
exec uvicorn app.main:app --host "${YIHU_HOST:-0.0.0.0}" --port "${YIHU_PORT:-8000}" --no-server-header
