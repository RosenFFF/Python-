"""健康时序数据、预警规则、预警及其事件。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core import clock
from app.db.base import Base, TimestampMixin
from app.domain.enums import AlertStatus
from app.models.elder import Elder


class HealthRecord(Base):
    """窄表时序：一行一个指标。新增指标不用改表，规则引擎也能统一按指标取序列。"""
    __tablename__ = "health_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elders.id"))
    metric: Mapped[str] = mapped_column(String(16))
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))
    measured_at: Mapped[datetime] = mapped_column(DateTime)
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("service_orders.id"))
    device_serial: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(255))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)

    __table_args__ = (
        # 设备重传幂等
        UniqueConstraint("elder_id", "metric", "measured_at", "source", name="uq_health_point"),
        Index("ix_health_elder_metric_time", "elder_id", "metric", "measured_at"),
        Index("ix_health_elder_time", "elder_id", "measured_at"),
    )


class AlertRule(Base, TimestampMixin):
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    rule_type: Mapped[str] = mapped_column(String(16))
    metric: Mapped[str | None] = mapped_column(String(16))
    op: Mapped[str | None] = mapped_column(String(2))
    threshold: Mapped[float | None] = mapped_column(Float)
    window: Mapped[int | None] = mapped_column(Integer)
    hours: Mapped[float | None] = mapped_column(Float)
    level: Mapped[str] = mapped_column(String(8))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_no: Mapped[str] = mapped_column(String(32), unique=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elders.id"), index=True)
    grid_id: Mapped[int] = mapped_column(ForeignKey("grids.id"), index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    # 规则快照：规则调整后，历史预警的判定依据不变
    rule_snapshot: Mapped[dict] = mapped_column(JSON)
    level: Mapped[str] = mapped_column(String(8), index=True)
    title: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    trigger_value: Mapped[float | None] = mapped_column(Float)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default=AlertStatus.OPEN, index=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, index=True)
    last_triggered_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime)
    responded_by: Mapped[str | None] = mapped_column(String(64))
    response_note: Mapped[str | None] = mapped_column(Text)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_by: Mapped[str | None] = mapped_column(String(64))
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    handle_minutes: Mapped[int | None] = mapped_column(Integer)
    conclusion: Mapped[str | None] = mapped_column(String(16))
    measures: Mapped[str | None] = mapped_column(Text)
    false_reason: Mapped[str | None] = mapped_column(String(255))
    hospital: Mapped[str | None] = mapped_column(String(128))
    referred_at: Mapped[datetime | None] = mapped_column(DateTime)

    elder: Mapped[Elder] = relationship()
    rule: Mapped[AlertRule] = relationship()
    events: Mapped[list[AlertEvent]] = relationship(back_populates="alert", order_by="AlertEvent.id")


class AlertEvent(Base):
    """预警时间线：触发、推送、响应、升级、闭环的全过程，用于计算响应时效。"""
    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(64), default="系统")
    detail: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)

    alert: Mapped[Alert] = relationship(back_populates="events")
