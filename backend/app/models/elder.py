"""老人档案、家属关联、变更日志、敏感访问日志、设备。"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core import clock
from app.db.base import Base, EncryptedString, TimestampMixin
from app.domain.enums import DifficultyType, DisabilityLevel
from app.models.org import Doctor, Grid


class Elder(Base, TimestampMixin):
    __tablename__ = "elders"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    gender: Mapped[str] = mapped_column(String(4))
    birth_date: Mapped[date] = mapped_column(Date)
    # 敏感字段：密文存储；*_idx 为盲索引，用于精确查重和检索
    id_card: Mapped[str] = mapped_column(EncryptedString("elder.id_card"))
    id_card_idx: Mapped[str] = mapped_column(String(64), index=True)
    phone: Mapped[str | None] = mapped_column(EncryptedString("elder.phone"))
    phone_idx: Mapped[str | None] = mapped_column(String(64), index=True)
    address: Mapped[str] = mapped_column(EncryptedString("elder.address", 1024))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    grid_id: Mapped[int] = mapped_column(ForeignKey("grids.id"), index=True)
    emergency_name: Mapped[str | None] = mapped_column(String(64))
    emergency_relation: Mapped[str | None] = mapped_column(String(32))
    emergency_phone: Mapped[str | None] = mapped_column(EncryptedString("elder.emergency_phone"))
    medical_insurance: Mapped[str | None] = mapped_column(String(32))
    ltc_insurance: Mapped[str | None] = mapped_column(String(32))
    chronic_diseases: Mapped[list] = mapped_column(JSON, default=list)
    medications: Mapped[list] = mapped_column(JSON, default=list)
    allergies: Mapped[str | None] = mapped_column(String(255))
    disability_level: Mapped[str] = mapped_column(String(16), default=DisabilityLevel.INTACT, index=True)
    difficulty_type: Mapped[str] = mapped_column(String(16), default=DifficultyType.NONE)
    care_tags: Mapped[list] = mapped_column(JSON, default=list)
    doctor_id: Mapped[int | None] = mapped_column(ForeignKey("doctors.id"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_reason: Mapped[str | None] = mapped_column(String(255))

    grid: Mapped[Grid] = relationship()
    doctor: Mapped[Doctor | None] = relationship()


class FamilyLink(Base):
    """家属账号与老人的绑定关系。老人与家属的"本人"数据范围由此确定。"""
    __tablename__ = "family_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elders.id"), index=True)
    relation: Mapped[str] = mapped_column(String(32), default="本人")
    __table_args__ = (UniqueConstraint("user_id", "elder_id"),)


class ElderChangeLog(Base):
    """档案变更日志。敏感字段的前后值只记脱敏形式，避免日志成为新的泄露面。"""
    __tablename__ = "elder_change_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elders.id"), index=True)
    field: Mapped[str] = mapped_column(String(32))
    before: Mapped[str | None] = mapped_column(Text)
    after: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    changed_by_name: Mapped[str | None] = mapped_column(String(64))
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, index=True)


class SensitiveAccessLog(Base):
    """二次授权查看敏感字段的留痕：谁、何时、看了哪位老人的哪些字段、理由。"""
    __tablename__ = "sensitive_access_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elders.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    user_name: Mapped[str] = mapped_column(String(64))
    fields: Mapped[list] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    accessed_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, index=True)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    serial: Mapped[str] = mapped_column(String(64), unique=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="智能手环")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    bound_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)
    battery: Mapped[int | None] = mapped_column(Integer)
