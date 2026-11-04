"""ORM 基类与公共列类型。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core import clock, crypto


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=clock.now, onupdate=clock.now)


class EncryptedString(TypeDecorator):
    """透明加解密的字符串列：ORM 层读写明文，数据库里只有 AES-GCM 密文。"""

    impl = String
    cache_ok = True

    def __init__(self, purpose: str, length: int = 512):
        super().__init__(length)
        self.purpose = purpose

    def process_bind_param(self, value, dialect):
        return None if value is None else crypto.encrypt(value, self.purpose)

    def process_result_value(self, value, dialect):
        return None if value is None else crypto.decrypt(value, self.purpose)
