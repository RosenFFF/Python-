"""FastAPI 应用入口。"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_admin, routes_core, routes_flow
from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.bootstrap import init_database, load_demo_clock
from app.services.backup_service import maintenance

logger = logging.getLogger("yihu")

CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; "
       "form-action 'self'; manifest-src 'self'; worker-src 'self'")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    init_database()
    load_demo_clock()
    if settings.scheduler_enabled:
        from app.jobs import scheduler
        scheduler.start()
    yield
    if settings.scheduler_enabled:
        from app.jobs import scheduler
        scheduler.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    docs = settings.env != "prod"
    app = FastAPI(title="颐护通 · 社区智慧养老服务与健康监测平台", version="1.0.0", lifespan=lifespan,
                  docs_url="/api/docs" if docs else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if docs else None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        started = time.perf_counter()
        if maintenance.is_set() and path.startswith("/api") and not path.endswith("/restore/confirm"):
            return JSONResponse({"code": "MAINTENANCE", "message": "系统正在恢复数据，请稍后再试"}, status_code=503)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=(self)"
        if not path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = CSP
        if path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
            elapsed = (time.perf_counter() - started) * 1000
            if elapsed > 1000:
                logger.warning("慢请求 %s %s %.0fms", request.method, path, elapsed)
        return response

    @app.exception_handler(AppError)
    async def on_app_error(_: Request, exc: AppError):
        return JSONResponse({"code": exc.code, "message": exc.message, "details": exc.details}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]), "message": e["msg"]}
                   for e in exc.errors()]
        first = details[0] if details else {"field": "", "message": ""}
        return JSONResponse({"code": "VALIDATION_FAILED", "message": f"参数不正确：{first['field']} {first['message']}",
                             "details": details}, status_code=422)

    @app.exception_handler(Exception)
    async def on_unexpected(request: Request, exc: Exception):
        logger.exception("未处理异常 %s %s", request.method, request.url.path)
        return JSONResponse({"code": "INTERNAL_ERROR", "message": "服务器内部错误，请稍后再试"}, status_code=500)

    @app.get("/api/health-check", tags=["元数据"])
    def health_check():
        return {"status": "ok", "time": clock.now()}

    for module in (routes_core, routes_admin, routes_flow):
        app.include_router(module.router, prefix="/api")

    _mount_frontend(app, settings.static_dir)
    return app


def _mount_frontend(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    if not index.exists():
        return
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
    root = static_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"code": "NOT_FOUND", "message": "接口不存在"}, status_code=404)
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            headers = {"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"} if candidate.name == "sw.js" else None
            return FileResponse(candidate, headers=headers)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
