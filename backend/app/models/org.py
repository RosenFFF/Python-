"""组织与账号：街道 > 社区 > 网格；服务机构；角色、权限；护理员、签约医生。"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Table, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EncryptedString, TimestampMixin


class Street(Base):
    __tablename__ = "streets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)


class Community(Base):
    __tablename__ = "communities"
    id: Mapped[int] = mapped_column(primary_key=True)
    street_id: Mapped[int] = mapped_column(ForeignKey("streets.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    center_lat: Mapped[float] = mapped_column(Float)
    center_lng: Mapped[float] = mapped_column(Float)
    service_radius_m: Mapped[int] = mapped_column(Integer, default=3000)

    grids: Mapped[list[Grid]] = relationship(back_populates="community")


class Grid(Base):
    __tablename__ = "grids"
    id: Mapped[int] = mapped_column(primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))

    community: Mapped[Community] = relationship(back_populates="grids")
    __table_args__ = (UniqueConstraint("community_id", "name"),)


community_orgs = Table(
    "community_orgs", Base.metadata,
    Column("community_id", ForeignKey("communities.id"), primary_key=True),
    Column("org_id", ForeignKey("organizations.id"), primary_key=True),
)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    contact: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    communities: Mapped[list[Community]] = relationship(secondary=community_orgs)


role_permissions = Table(
    "role_permissions", Base.metadata,
    Column("role_id", ForeignKey("roles.id"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id"), primary_key=True),
)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    group: Mapped[str] = mapped_column(String(32))


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(32))
    data_scope: Mapped[str] = mapped_column(String(16))

    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(EncryptedString("user.phone"))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    community_id: Mapped[int | None] = mapped_column(ForeignKey("communities.id"), index=True)
    grid_id: Mapped[int | None] = mapped_column(ForeignKey("grids.id"), index=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    # 改密、停用时递增，旧令牌立刻失效
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    role: Mapped[Role] = relationship()
    grid: Mapped[Grid | None] = relationship()
    community: Mapped[Community | None] = relationship()
    organization: Mapped[Organization | None] = relationship()


class Caregiver(Base, TimestampMixin):
    __tablename__ = "caregivers"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(EncryptedString("caregiver.phone"))
    skills: Mapped[list] = mapped_column(JSON, default=list)
    # 派单距离的基准位置，初始为驻点，每次签到后更新
    base_lat: Mapped[float] = mapped_column(Float)
    base_lng: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    consecutive_rejects: Mapped[int] = mapped_column(Integer, default=0)
    # 重点关注月份，如 "2026-11"；当月内派单降权
    key_attention_month: Mapped[str | None] = mapped_column(String(7))
    hired_on: Mapped[date | None] = mapped_column(Date)

    organization: Mapped[Organization] = relationship()
    user: Mapped[User | None] = relationship()


class Doctor(Base):
    __tablename__ = "doctors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    hospital: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(EncryptedString("doctor.phone"))
