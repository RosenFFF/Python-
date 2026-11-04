"""命令行工具：python -m app.cli <命令>。"""
from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys

from app.core.config import PROJECT_DIR


def _init_env(_: argparse.Namespace) -> None:
    """生成带随机密钥的 .env。已存在时不覆盖，密钥一旦丢失，加密字段无法解密。"""
    target = PROJECT_DIR / ".env"
    if target.exists():
        print(f"{target} 已存在，未修改")
        return
    template = (PROJECT_DIR / ".env.example").read_text(encoding="utf-8")
    values = {
        "YIHU_SECRET_KEY": secrets.token_urlsafe(48),
        "YIHU_FIELD_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "YIHU_INDEX_KEY": secrets.token_urlsafe(32),
        "YIHU_DEVICE_API_KEY": secrets.token_urlsafe(24),
    }
    lines = []
    for line in template.splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={values[key]}" if key in values else line)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已生成 {target}，请妥善保管其中的密钥")


def _migrate(_: argparse.Namespace) -> None:
    from app.db.bootstrap import init_database
    init_database()
    print("数据库已迁移到最新版本")


def _seed(_: argparse.Namespace) -> None:
    from app.db.bootstrap import init_database
    from app.db.session import new_session
    from app.seed.demo_data import DEMO_PASSWORD, seed_demo
    from app.services import audit_service, backup_service

    init_database()
    db = new_session()
    try:
        result = seed_demo(db)
        db.commit()
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False))
    if result["seeded"]:
        backup_service.create_backup(audit_service.SYSTEM, "SEED")
        print(f"演示账号密码统一为 {DEMO_PASSWORD}")


def _backup(_: argparse.Namespace) -> None:
    from app.services import audit_service, backup_service
    print(json.dumps(backup_service.create_backup(audit_service.SYSTEM, "MANUAL"), ensure_ascii=False, default=str))


def _create_admin(args: argparse.Namespace) -> None:
    from sqlalchemy import select

    from app.core.security import check_password_strength, hash_password
    from app.db.bootstrap import init_database
    from app.db.session import new_session
    from app.models import Role, User

    init_database()
    check_password_strength(args.password)
    db = new_session()
    try:
        role = db.execute(select(Role).where(Role.code == "super_admin")).scalar()
        db.add(User(username=args.username, display_name="系统管理员", password_hash=hash_password(args.password), role_id=role.id))
        db.commit()
    finally:
        db.close()
    print(f"已创建管理员 {args.username}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="颐护通管理命令")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-env", help="生成 .env（含随机密钥）").set_defaults(func=_init_env)
    sub.add_parser("migrate", help="执行数据库迁移并同步基础数据").set_defaults(func=_migrate)
    sub.add_parser("seed", help="写入演示数据").set_defaults(func=_seed)
    sub.add_parser("backup", help="立即备份").set_defaults(func=_backup)
    admin = sub.add_parser("create-admin", help="创建超级管理员")
    admin.add_argument("username")
    admin.add_argument("password")
    admin.set_defaults(func=_create_admin)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    args.func(args)


if __name__ == "__main__":
    main()
