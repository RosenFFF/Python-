"""数据库引擎与会话。

SQLite 事务策略：写请求用 BEGIN IMMEDIATE 拿写锁，避免读事务升级写事务时
拿到 SQLITE_BUSY；GET 请求永不写库，用普通 BEGIN，在 WAL 下不阻塞写者。
"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_write_factory: sessionmaker | None = None
_read_factory: sessionmaker | None = None


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        # 交给 SQLAlchemy 显式发 BEGIN，保证 SAVEPOINT 与事务边界正确
        dbapi_conn.isolation_level = None
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=20000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        immediate = conn.get_execution_options().get("immediate", True)
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


def get_engine() -> Engine:
    global _engine, _write_factory, _read_factory
    if _engine is None:
        settings = get_settings()
        kwargs: dict = {}
        if settings.is_sqlite:
            settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 20}
        _engine = create_engine(settings.database_url, **kwargs)
        if settings.is_sqlite:
            _configure_sqlite(_engine)
        _write_factory = sessionmaker(bind=_engine, autoflush=True, expire_on_commit=False)
        _read_factory = sessionmaker(
            bind=_engine.execution_options(immediate=False), autoflush=True, expire_on_commit=False
        )
    return _engine


def dispose_engine() -> None:
    """释放连接并清空缓存（测试切库、备份恢复替换文件时使用）。"""
    global _engine, _write_factory, _read_factory
    if _engine is not None:
        _engine.dispose()
    _engine = _write_factory = _read_factory = None


def new_session(read_only: bool = False) -> Session:
    get_engine()
    factory = _read_factory if read_only else _write_factory
    return factory()


def get_db(request: Request) -> Iterator[Session]:
    """请求级会话。提交由路由函数显式调用，异常时这里统一回滚并关闭。"""
    session = new_session(read_only=request.method == "GET")
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
