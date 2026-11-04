# 第一阶段：构建前端
FROM node:22-alpine AS web
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN mkdir -p /build/backend && npm run build

# 第二阶段：运行环境
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai \
    YIHU_DATA_DIR=/app/data YIHU_BACKUP_DIR=/app/backups YIHU_HOST=0.0.0.0
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/scripts ./scripts
COPY --from=web /build/backend/static ./static
COPY deploy/docker-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && useradd --system --uid 10001 --home /app yihu \
    && mkdir -p /app/data /app/backups && chown -R yihu:yihu /app
USER yihu
VOLUME ["/app/data", "/app/backups"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health-check', timeout=3)"
ENTRYPOINT ["entrypoint.sh"]
