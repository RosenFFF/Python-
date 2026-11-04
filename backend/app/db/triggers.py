"""审计日志防篡改触发器。Alembic 迁移和测试建库共用同一份 DDL。"""
from __future__ import annotations

from sqlalchemy.engine import Connection

AUDIT_RETENTION_DAYS = 180

SQLITE_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS audit_logs_no_update
    BEFORE UPDATE ON audit_logs
    BEGIN
        SELECT RAISE(ABORT, 'audit log is append-only');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete_within_retention
    BEFORE DELETE ON audit_logs
    WHEN OLD.at > datetime('now', '+8 hours', '-{AUDIT_RETENTION_DAYS} days')
    BEGIN
        SELECT RAISE(ABORT, 'audit log retention is {AUDIT_RETENTION_DAYS} days');
    END
    """,
]


def install_audit_triggers(conn: Connection) -> None:
    if conn.dialect.name == "sqlite":
        for ddl in SQLITE_TRIGGERS:
            conn.exec_driver_sql(ddl)
