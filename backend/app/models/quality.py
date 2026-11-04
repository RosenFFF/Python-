"""服务评价、回访任务、投诉、护理员扣分。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core import clock
from app.db.base import Base
from app.domain.enums import ComplaintStatus, FollowupStatus
from app.models.order import ServiceOrder
from app.models.org import Caregiver


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"), unique=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), index=True)
    punctuality: Mapped[int] = mapped_column(Integer)
    attitude: Mapped[int] = mapped_column(Integer)
    professionalism: Mapped[int] = mapped_column(Integer)
    effectiveness: Mapped[int] = mapped_column(Integer)
    overall: Mapped[float] = mapped_column(Float, index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, index=True)

    order: Mapped[ServiceOrder] = relationship()


class FollowupTask(Base):
    __tablename__ = "followup_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"), index=True)
    review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id"))
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=FollowupStatus.OPEN, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    overdue_flagged_at: Mapped[datetime | None] = mapped_column(DateTime)
    outcome: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)

    order: Mapped[ServiceOrder] = relationship()


class Complaint(Base):
    __tablename__ = "complaints"
    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_no: Mapped[str] = mapped_column(String(32), unique=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"), index=True)
    caregiver_id: Mapped[int | None] = mapped_column(ForeignKey("caregivers.id"), index=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    source: Mapped[str] = mapped_column(String(16), default="FAMILY")  # FAMILY / FOLLOWUP
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=ComplaintStatus.OPEN, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, index=True)
    org_explanation: Mapped[str | None] = mapped_column(Text)
    explained_at: Mapped[datetime | None] = mapped_column(DateTime)
    conclusion: Mapped[str | None] = mapped_column(String(16))
    basis: Mapped[str | None] = mapped_column(Text)
    actions: Mapped[list] = mapped_column(JSON, default=list)  # REFUND / REPLACE / PENALTY
    penalty_points: Mapped[int | None] = mapped_column(Integer)
    refund_fee_id: Mapped[int | None] = mapped_column(ForeignKey("fee_details.id"))
    concluded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    concluded_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    order: Mapped[ServiceOrder] = relationship()
    caregiver: Mapped[Caregiver | None] = relationship()


class CaregiverPenalty(Base):
    __tablename__ = "caregiver_penalties"
    id: Mapped[int] = mapped_column(primary_key=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), index=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"))
    month: Mapped[str] = mapped_column(String(7))
    points: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)
