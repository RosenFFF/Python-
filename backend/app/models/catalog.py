"""服务目录与价格版本。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core import clock
from app.db.base import Base, TimestampMixin


class ServiceItem(Base, TimestampMixin):
    __tablename__ = "service_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    pricing_mode: Mapped[str] = mapped_column(String(16))
    standard_minutes: Mapped[int] = mapped_column(Integer)
    required_skills: Mapped[list] = mapped_column(JSON, default=list)
    applicable_levels: Mapped[list] = mapped_column(JSON, default=list)
    subsidy_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    checklist: Mapped[list] = mapped_column(JSON, default=list)
    is_on_shelf: Mapped[bool] = mapped_column(Boolean, default=True)

    versions: Mapped[list[PriceVersion]] = relationship(
        back_populates="item", order_by="PriceVersion.version_no"
    )


class PriceVersion(Base):
    """价格版本：只增不改，有效区间为 [effective_from, effective_to)。

    工单创建时按预约服务时点绑定版本，之后调价不影响该工单和已结算金额。
    """
    __tablename__ = "price_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("service_items.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int] = mapped_column(Integer)  # 分
    subsidy_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    effective_from: Mapped[datetime] = mapped_column(DateTime)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)

    item: Mapped[ServiceItem] = relationship(back_populates="versions")
    __table_args__ = (UniqueConstraint("item_id", "version_no"),)
