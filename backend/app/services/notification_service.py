"""站内消息与短信。业务事务内写发件箱，定时任务投递，短信网关不可用不影响主流程。"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.domain.enums import RoleCode
from app.models import Community, Elder, FamilyLink, Grid, Notification, Role, User

logger = logging.getLogger("yihu.notify")
MAX_ATTEMPTS = 5


class MockSmsGateway:
    """短信网关模拟：追加写本地文件。接入真实短信时替换此类即可。"""

    def send(self, phone: str, title: str, body: str) -> None:
        path = get_settings().data_dir / "sms_outbox.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        masked = phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "****"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{clock.now().isoformat()} {masked} [{title}] {body}\n")


_gateway = MockSmsGateway()


def set_gateway(gateway) -> None:
    global _gateway
    _gateway = gateway


def in_app(db: Session, user_id: int, title: str, body: str, *, level: str = "INFO",
           ref_type: str | None = None, ref_id: int | None = None, user_name: str | None = None,
           role: str | None = None) -> Notification:
    now = clock.now()
    row = Notification(channel="IN_APP", recipient_user_id=user_id, recipient_name=user_name, recipient_role=role,
                       title=title, body=body, level=level, ref_type=ref_type, ref_id=ref_id,
                       status="SENT", attempts=1, queued_at=now, sent_at=now)
    db.add(row)
    return row


def sms(db: Session, phone: str | None, recipient_name: str | None, title: str, body: str, *, role: str | None = None,
        level: str = "INFO", ref_type: str | None = None, ref_id: int | None = None) -> Notification | None:
    if not phone:
        return None
    row = Notification(channel="SMS", phone=phone, recipient_name=recipient_name, recipient_role=role, title=title,
                       body=body, level=level, ref_type=ref_type, ref_id=ref_id, queued_at=clock.now())
    db.add(row)
    return row


def users_with_role(db: Session, role: str, *, grid_id: int | None = None, community_id: int | None = None,
                    org_id: int | None = None) -> list[User]:
    stmt = select(User).join(Role, User.role_id == Role.id).where(Role.code == role, User.is_active.is_(True))
    if grid_id is not None:
        stmt = stmt.where(User.grid_id == grid_id)
    if community_id is not None:
        stmt = stmt.where(User.community_id == community_id)
    if org_id is not None:
        stmt = stmt.where(User.org_id == org_id)
    return list(db.execute(stmt).scalars())


def family_users(db: Session, elder_id: int) -> list[User]:
    stmt = select(User).join(FamilyLink, FamilyLink.user_id == User.id).where(
        FamilyLink.elder_id == elder_id, User.is_active.is_(True))
    return list(db.execute(stmt).scalars())


def notify_users(db: Session, users: list[User], title: str, body: str, **kwargs) -> int:
    for user in users:
        in_app(db, user.id, title, body, user_name=user.display_name, role=user.role.code, **kwargs)
    return len(users)


def notify_grid(db: Session, grid_id: int, title: str, body: str, **kwargs) -> int:
    return notify_users(db, users_with_role(db, RoleCode.GRID_WORKER, grid_id=grid_id), title, body, **kwargs)


def notify_community_admins(db: Session, community_id: int, title: str, body: str, **kwargs) -> int:
    return notify_users(db, users_with_role(db, RoleCode.COMMUNITY_ADMIN, community_id=community_id),
                        title, body, **kwargs)


def notify_org_admins(db: Session, org_id: int, title: str, body: str, **kwargs) -> int:
    return notify_users(db, users_with_role(db, RoleCode.ORG_ADMIN, org_id=org_id), title, body, **kwargs)


def notify_street(db: Session, title: str, body: str, **kwargs) -> int:
    return notify_users(db, users_with_role(db, RoleCode.STREET_SUPERVISOR), title, body, **kwargs)


def notify_family(db: Session, elder_id: int, title: str, body: str, **kwargs) -> int:
    return notify_users(db, family_users(db, elder_id), title, body, **kwargs)


def community_of_elder(db: Session, elder: Elder) -> Community:
    grid = db.get(Grid, elder.grid_id)
    return db.get(Community, grid.community_id)


def flush_outbox(db: Session, ref_type: str | None = None, ref_id: int | None = None) -> int:
    """投递待发短信。单条失败只累计重试次数，不影响其他条目。"""
    stmt = select(Notification).where(Notification.status == "QUEUED", Notification.channel == "SMS")
    if ref_type:
        stmt = stmt.where(Notification.ref_type == ref_type, Notification.ref_id == ref_id)
    sent = 0
    for row in db.execute(stmt.limit(500)).scalars():
        row.attempts += 1
        try:
            _gateway.send(row.phone, row.title, row.body)
        except Exception:  # 外部网关的任何异常都不能中断批次
            logger.exception("短信发送失败 id=%s", row.id)
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "FAILED"
            continue
        row.status = "SENT"
        row.sent_at = clock.now()
        sent += 1
    return sent
