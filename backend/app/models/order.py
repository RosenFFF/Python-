"""预约工单、状态事件、周期计划、派单记录、时段锁、执行留痕、异常单。"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core import clock
from app.db.base import Base, EncryptedString, TimestampMixin
from app.domain.enums import (
    ExceptionStatus,
    OfferStatus,
    OrderSource,
    OrderStatus,
    PaymentStatus,
    SpotCheckStatus,
)
from app.models.catalog import PriceVersion, ServiceItem
from app.models.elder import Elder
from app.models.org import Caregiver


class RecurringPlan(Base, TimestampMixin):
    """每周固定预约。由定时任务滚动生成未来 7 天的工单。"""
    __tablename__ = "recurring_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elders.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("service_items.id"))
    weekday: Mapped[int] = mapped_column(Integer)  # 0=周一
    time_of_day: Mapped[str] = mapped_column(String(5))  # HH:MM
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    remark: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    elder: Mapped[Elder] = relationship()
    item: Mapped[ServiceItem] = relationship()


class ServiceOrder(Base, TimestampMixin):
    """预约单和工单是同一实体的不同阶段，靠状态机区分。"""
    __tablename__ = "service_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elders.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("service_items.id"), index=True)
    # 冗余网格、社区是数据范围过滤和统计的高频条件；老人搬迁后，历史工单仍归发生时的网格
    grid_id: Mapped[int] = mapped_column(ForeignKey("grids.id"), index=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    price_version_id: Mapped[int] = mapped_column(ForeignKey("price_versions.id"))
    scheduled_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime)
    address: Mapped[str] = mapped_column(EncryptedString("order.address", 1024))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    remark: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.PENDING_ACCEPT, index=True)
    source: Mapped[str] = mapped_column(String(16), default=OrderSource.ONLINE)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    needs_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_issues: Mapped[list] = mapped_column(JSON, default=list)
    manual_coordination: Mapped[bool] = mapped_column(Boolean, default=False)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("recurring_plans.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    caregiver_id: Mapped[int | None] = mapped_column(ForeignKey("caregivers.id"), index=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(255))
    cancel_chargeable: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    payment_status: Mapped[str] = mapped_column(String(16), default=PaymentStatus.UNPAID)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_invited_at: Mapped[datetime | None] = mapped_column(DateTime)

    elder: Mapped[Elder] = relationship()
    item: Mapped[ServiceItem] = relationship()
    price_version: Mapped[PriceVersion] = relationship()
    caregiver: Mapped[Caregiver | None] = relationship()
    events: Mapped[list[OrderEvent]] = relationship(back_populates="order", order_by="OrderEvent.id")
    execution: Mapped[ServiceExecution | None] = relationship(back_populates="order", uselist=False)

    __table_args__ = (
        # 同一周期计划同一时点只生成一张工单，定时任务重跑也不会重复
        UniqueConstraint("plan_id", "scheduled_start", name="uq_plan_occurrence"),
        Index("ix_order_elder_time", "elder_id", "scheduled_start"),
    )


class OrderEvent(Base):
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_name: Mapped[str] = mapped_column(String(64), default="系统")
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)

    order: Mapped[ServiceOrder] = relationship(back_populates="events")


class DispatchRound(Base):
    """一次推荐的完整快照：当时的权重、全部候选的打分明细和被过滤者的原因。
    事后可以回答"当时为什么派给他"，权重调整也不影响历史解释。
    """
    __tablename__ = "dispatch_rounds"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"), index=True)
    round_no: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSON)
    candidates: Mapped[list] = mapped_column(JSON)
    excluded: Mapped[list] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)


class DispatchOffer(Base):
    __tablename__ = "dispatch_offers"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"), index=True)
    round_id: Mapped[int | None] = mapped_column(ForeignKey("dispatch_rounds.id"))
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), index=True)
    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default=OfferStatus.OFFERED, index=True)
    offered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    offered_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)
    deadline_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime)
    reject_reason: Mapped[str | None] = mapped_column(String(255))
    is_reassign: Mapped[bool] = mapped_column(Boolean, default=False)
    reassign_reason: Mapped[str | None] = mapped_column(String(255))

    caregiver: Mapped[Caregiver] = relationship()
    order: Mapped[ServiceOrder] = relationship()


class CaregiverSlot(Base):
    """护理员时段锁：把在途工单切成 15 分钟时间片，用唯一约束防重。

    区间重叠无法用普通唯一索引表达，先查后写在并发下有竞态。切片之后，
    两张重叠工单必然争用同一时间片，由数据库兜底保证同一时段只有一张在途工单。
    """
    __tablename__ = "caregiver_slots"
    id: Mapped[int] = mapped_column(primary_key=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), index=True)
    slot_start: Mapped[datetime] = mapped_column(DateTime)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"), index=True)
    __table_args__ = (UniqueConstraint("caregiver_id", "slot_start", name="uq_caregiver_slot"),)


class CaregiverExclusion(Base):
    """投诉结论为"更换护理员"后写入，此后不再给这位老人派该护理员。"""
    __tablename__ = "caregiver_exclusions"
    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elders.id"), index=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), index=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)
    __table_args__ = (UniqueConstraint("elder_id", "caregiver_id"),)


class ServiceExecution(Base, TimestampMixin):
    __tablename__ = "service_executions"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"), unique=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), index=True)
    checkin_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    checkin_lat: Mapped[float] = mapped_column(Float)
    checkin_lng: Mapped[float] = mapped_column(Float)
    checkin_distance_m: Mapped[float] = mapped_column(Float)
    checkin_note: Mapped[str | None] = mapped_column(String(255))
    is_on_time: Mapped[bool] = mapped_column(Boolean, default=True)
    checklist: Mapped[list] = mapped_column(JSON, default=list)
    checkout_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    duration_abnormal: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str | None] = mapped_column(Text)
    spot_check_status: Mapped[str] = mapped_column(String(16), default=SpotCheckStatus.NONE, index=True)
    spot_checked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    spot_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    spot_check_note: Mapped[str | None] = mapped_column(String(255))
    offline_synced: Mapped[bool] = mapped_column(Boolean, default=False)

    order: Mapped[ServiceOrder] = relationship(back_populates="execution")
    photos: Mapped[list[ServicePhoto]] = relationship(back_populates="execution")


class ServicePhoto(Base):
    __tablename__ = "service_photos"
    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("service_executions.id"), index=True)
    file_name: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    original_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)

    execution: Mapped[ServiceExecution] = relationship(back_populates="photos")


class ServiceException(Base):
    __tablename__ = "service_exceptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"), index=True)
    grid_id: Mapped[int] = mapped_column(ForeignKey("grids.id"), index=True)
    exception_type: Mapped[str] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=ExceptionStatus.OPEN, index=True)
    reported_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reported_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, index=True)
    handler_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    handler_name: Mapped[str | None] = mapped_column(String(64))
    picked_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    handle_minutes: Mapped[int | None] = mapped_column(Integer)
    resolution: Mapped[str | None] = mapped_column(Text)
    order_action: Mapped[str | None] = mapped_column(String(16))  # CONTINUE / NO_SHOW / CANCEL
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"))

    order: Mapped[ServiceOrder] = relationship()
