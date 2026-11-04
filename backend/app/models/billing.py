"""补贴政策、费用明细、月度账单、对账差异、老人缴费单、监管抽查。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core import clock
from app.db.base import Base, TimestampMixin
from app.domain.enums import BillStatus, DiscrepancyStatus, FeeType, PaymentStatus, SampleResult
from app.models.elder import Elder
from app.models.order import ServiceOrder


class SubsidyPolicy(Base, TimestampMixin):
    """补贴政策矩阵：失能等级 × 困难类型。"""
    __tablename__ = "subsidy_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    disability_level: Mapped[str] = mapped_column(String(16))
    difficulty_type: Mapped[str] = mapped_column(String(16))
    gov_ratio: Mapped[float] = mapped_column(Float)
    ltci_ratio: Mapped[float] = mapped_column(Float)
    monthly_cap: Mapped[int] = mapped_column(Integer)  # 单人月度政府补贴上限（分）
    __table_args__ = (UniqueConstraint("disability_level", "difficulty_type"),)


class FeeDetail(Base, TimestampMixin):
    __tablename__ = "fee_details"
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7))
    fee_type: Mapped[str] = mapped_column(String(16), default=FeeType.SERVICE)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elders.id"), index=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"))
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("service_orders.id"), index=True)
    price_version_id: Mapped[int | None] = mapped_column(ForeignKey("price_versions.id"))
    unit_price: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[float] = mapped_column(Float)
    amount: Mapped[int] = mapped_column(Integer)
    gov_amount: Mapped[int] = mapped_column(Integer, default=0)
    ltci_amount: Mapped[int] = mapped_column(Integer, default=0)
    self_amount: Mapped[int] = mapped_column(Integer, default=0)
    gov_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    ltci_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    service_start: Mapped[datetime | None] = mapped_column(DateTime)
    service_end: Mapped[datetime | None] = mapped_column(DateTime)
    actual_minutes: Mapped[int | None] = mapped_column(Integer)
    standard_minutes: Mapped[int | None] = mapped_column(Integer)
    # 被差异拦截或被剔除时为 True，不计入账单合计
    intercepted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    bill_id: Mapped[int | None] = mapped_column(ForeignKey("bills.id"), index=True)
    refund_of_id: Mapped[int | None] = mapped_column(ForeignKey("fee_details.id"))
    note: Mapped[str | None] = mapped_column(String(255))

    elder: Mapped[Elder] = relationship()
    order: Mapped[ServiceOrder | None] = relationship()

    __table_args__ = (Index("ix_fee_month_community", "month", "community_id"),)


class Bill(Base, TimestampMixin):
    __tablename__ = "bills"
    id: Mapped[int] = mapped_column(primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    status: Mapped[str] = mapped_column(String(20), default=BillStatus.DRAFT, index=True)
    total_amount: Mapped[int] = mapped_column(Integer, default=0)
    gov_amount: Mapped[int] = mapped_column(Integer, default=0)
    ltci_amount: Mapped[int] = mapped_column(Integer, default=0)
    self_amount: Mapped[int] = mapped_column(Integer, default=0)
    intercepted_amount: Mapped[int] = mapped_column(Integer, default=0)
    # 差异比例在生成时冻结，人工复核的判定依据不因差异处理而变化
    discrepancy_amount: Mapped[int] = mapped_column(Integer, default=0)
    discrepancy_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_note: Mapped[str | None] = mapped_column(Text)
    grid_confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    grid_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    sample_seed: Mapped[int | None] = mapped_column(Integer)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)

    __table_args__ = (UniqueConstraint("community_id", "month", name="uq_bill_community_month"),)


class BillOrgConfirmation(Base):
    """账单涉及的每家机构都要确认。"""
    __tablename__ = "bill_org_confirmations"
    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (UniqueConstraint("bill_id", "org_id"),)


class Discrepancy(Base):
    __tablename__ = "discrepancies"
    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), index=True)
    fee_id: Mapped[int] = mapped_column(ForeignKey("fee_details.id"), index=True)
    related_fee_id: Mapped[int | None] = mapped_column(ForeignKey("fee_details.id"))
    kind: Mapped[str] = mapped_column(String(24))
    amount: Mapped[int] = mapped_column(Integer)
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=DiscrepancyStatus.OPEN, index=True)
    resolution: Mapped[str | None] = mapped_column(String(16))
    explanation: Mapped[str | None] = mapped_column(Text)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    handled_by_name: Mapped[str | None] = mapped_column(String(64))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime)

    fee: Mapped[FeeDetail] = relationship(foreign_keys=[fee_id])


class ElderStatement(Base):
    """老人个人月度缴费单。账单生效后生成，只含个人自付部分。"""
    __tablename__ = "elder_statements"
    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), index=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elders.id"), index=True)
    month: Mapped[str] = mapped_column(String(7))
    total_amount: Mapped[int] = mapped_column(Integer)
    gov_amount: Mapped[int] = mapped_column(Integer)
    ltci_amount: Mapped[int] = mapped_column(Integer)
    self_amount: Mapped[int] = mapped_column(Integer)
    payment_status: Mapped[str] = mapped_column(String(16), default=PaymentStatus.UNPAID, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    paid_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    payment_method: Mapped[str | None] = mapped_column(String(32))

    elder: Mapped[Elder] = relationship()
    __table_args__ = (UniqueConstraint("bill_id", "elder_id"),)


class SampleCheck(Base):
    __tablename__ = "sample_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"))
    result: Mapped[str] = mapped_column(String(16), default=SampleResult.PENDING, index=True)
    note: Mapped[str | None] = mapped_column(Text)
    checked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    evidence: Mapped[dict | None] = mapped_column(JSON)

    order: Mapped[ServiceOrder] = relationship()
    __table_args__ = (UniqueConstraint("bill_id", "order_id"),)
