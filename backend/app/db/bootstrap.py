"""数据库初始化：执行 Alembic 迁移，同步权限和内置规则。"""
from __future__ import annotations

from alembic import command
from alembic.config import Config

from app.core import clock
from app.core.config import BACKEND_DIR, get_settings
from app.db.session import new_session
from app.models import SystemConfig
from app.services.rbac_defaults import sync_rbac
from app.services.system_defaults import sync_builtin_rules, sync_subsidy_policies


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # alembic 配置值里 % 是插值符
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    return config


def init_database() -> None:
    command.upgrade(alembic_config(), "head")
    sync_reference_data()


def sync_reference_data() -> None:
    db = new_session()
    try:
        sync_rbac(db)
        sync_builtin_rules(db)
        sync_subsidy_policies(db)
        db.commit()
    finally:
        db.close()


def load_demo_clock() -> None:
    """重启后恢复演示时钟偏移。"""
    if not get_settings().demo_mode:
        clock.set_offset_minutes(0)
        return
    db = new_session(read_only=True)
    try:
        row = db.get(SystemConfig, "demo.clock_offset_minutes")
        clock.set_offset_minutes(int(row.value) if row else 0)
    finally:
        db.close()
