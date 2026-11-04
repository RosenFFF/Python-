"""系统级：业务配置、审计日志、消息投递、任务运行、离线同步、恢复记录。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core import clock
from app.db.base import Base, EncryptedString


class SystemConfig(Base):
    __tablename__ = "system_configs"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | float | int | str | bool] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)


class AuditLog(Base):
    """审计日志：只增不改。

    防篡改两层：1) 数据库触发器拒绝 UPDATE，以及保留期内的 DELETE；
    2) HMAC 哈希链，每条包含前一条的哈希，绕过触发器直接改库也能被校验发现。
    """
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, index=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, index=True)
    actor_name: Mapped[str] = mapped_column(String(64))
    actor_role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(32), index=True)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class Notification(Base):
    """消息发件箱：站内消息和短信共用一张表，由定时任务投递并记录时间。"""
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(8))  # IN_APP / SMS
    recipient_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    recipient_name: Mapped[str | None] = mapped_column(String(64))
    recipient_role: Mapped[str | None] = mapped_column(String(32))
    phone: Mapped[str | None] = mapped_column(EncryptedString("notification.phone"))
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(8), default="INFO")
    ref_type: Mapped[str | None] = mapped_column(String(32), index=True)
    ref_id: Mapped[int | None] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(8), default="QUEUED", index=True)  # QUEUED / SENT / FAILED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    queued_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(48), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[int] = mapped_column(Integer, default=1)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)


class SyncOp(Base):
    """离线操作幂等表：客户端生成 op_id，网络重试不会让操作重复执行。"""
    __tablename__ = "sync_ops"
    op_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    order_id: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)
    result: Mapped[dict | None] = mapped_column(JSON)
