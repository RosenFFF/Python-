"""统一时钟。

平台只服务单一时区（东八区），时间一律按本地时间的 naive datetime 存储。
所有业务代码通过 clock.now() 取时间，从而可以：
- 单元测试冻结时间，断言超时与升级逻辑；
- 演示模式快进时钟，现场展示"30 分钟未处置自动升级"，不用真等。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

_TZ = timezone(timedelta(hours=8))
_frozen: datetime | None = None
_offset = timedelta(0)


def now() -> datetime:
    if _frozen is not None:
        return _frozen
    return datetime.now(_TZ).replace(tzinfo=None, microsecond=0) + _offset


def freeze(moment: datetime | None) -> None:
    global _frozen
    _frozen = moment


def advance(delta: timedelta) -> datetime:
    global _frozen, _offset
    if _frozen is not None:
        _frozen += delta
    else:
        _offset += delta
    return now()


def offset_minutes() -> int:
    return int(_offset.total_seconds() // 60)


def set_offset_minutes(minutes: int) -> None:
    global _offset
    _offset = timedelta(minutes=minutes)


def reset() -> None:
    global _frozen, _offset
    _frozen = None
    _offset = timedelta(0)
