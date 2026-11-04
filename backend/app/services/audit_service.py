"""审计日志：写入、查询、哈希链校验。"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.models import AuditLog

GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditContext:
    actor_id: int | None
    actor_name: str
    actor_role: str | None = None
    ip: str | None = None


SYSTEM = AuditContext(None, "系统")


def _digest(prev_hash: str, row: dict) -> str:
    payload = json.dumps(
        [prev_hash, row["at"], row["actor_id"], row["actor_name"], row["actor_role"], row["action"],
         row["target_type"], row["target_id"], row["detail"], row["ip"]],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hmac.new(get_settings().secret_key.encode(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def write(db: Session, ctx: AuditContext, action: str, target_type: str | None = None,
          target_id: object = None, detail: dict | str | None = None) -> AuditLog:
    """追加一条审计。写事务持有写锁，读末条哈希再追加不会分叉。"""
    last_hash = db.execute(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)).scalar()
    prev_hash = last_hash or GENESIS
    detail_text = detail if isinstance(detail, (str, type(None))) else json.dumps(detail, ensure_ascii=False, default=str)
    at = clock.now()
    fields = {
        "at": at.isoformat(), "actor_id": ctx.actor_id, "actor_name": ctx.actor_name, "actor_role": ctx.actor_role,
        "action": action, "target_type": target_type,
        "target_id": None if target_id is None else str(target_id), "detail": detail_text, "ip": ctx.ip,
    }
    row = AuditLog(
        at=at, actor_id=ctx.actor_id, actor_name=ctx.actor_name, actor_role=ctx.actor_role, action=action,
        target_type=target_type, target_id=fields["target_id"], detail=detail_text, ip=ctx.ip,
        prev_hash=prev_hash, hash=_digest(prev_hash, fields),
    )
    db.add(row)
    db.flush()
    return row


def verify_chain(db: Session) -> dict:
    """从头校验哈希链。发现第一处断裂即返回。

    过了保留期的旧日志可能已被清理，所以首条不要求是创世哈希。
    """
    prev: str | None = None
    checked = 0
    rows = db.execute(select(AuditLog).order_by(AuditLog.id)).scalars()
    for row in rows:
        if prev is not None and row.prev_hash != prev:
            return {"ok": False, "checked": checked, "broken_at": row.id, "reason": "链接断裂：记录被删除或插入"}
        fields = {
            "at": row.at.isoformat(), "actor_id": row.actor_id, "actor_name": row.actor_name,
            "actor_role": row.actor_role, "action": row.action, "target_type": row.target_type,
            "target_id": row.target_id, "detail": row.detail, "ip": row.ip,
        }
        if not hmac.compare_digest(_digest(row.prev_hash, fields), row.hash):
            return {"ok": False, "checked": checked, "broken_at": row.id, "reason": "内容哈希不符：记录被篡改"}
        prev = row.hash
        checked += 1
    return {"ok": True, "checked": checked, "broken_at": None, "reason": None}
