#!/usr/bin/env bash
# Linux / macOS 一键启动：./start.sh [--no-seed]
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -q -r backend/requirements.txt
fi

cd backend
../.venv/bin/python -m app.cli init-env
if [ "${1:-}" = "--no-seed" ]; then
    ../.venv/bin/python -m app.cli migrate
else
    ../.venv/bin/python -m app.cli seed
fi
[ -f static/index.html ] || echo "未找到前端构建产物，请执行：cd frontend && npm install && npm run build"
echo "打开 http://127.0.0.1:8000  （演示账号密码 Demo@2026）"
exec ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
