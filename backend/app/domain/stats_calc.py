"""统计口径（纯函数）：分桶、环比、同比、中位数、分布。"""
from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta


def month_key(d: date | datetime) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def bucket_key(d: date | datetime, granularity: str) -> str:
    day = d.date() if isinstance(d, datetime) else d
    if granularity == "day":
        return day.isoformat()
    if granularity == "week":
        monday = day - timedelta(days=day.weekday())
        return monday.isoformat()
    if granularity == "month":
        return month_key(day)
    raise ValueError(f"不支持的统计粒度: {granularity}")


def bucket_range(start: date, end: date, granularity: str) -> list[str]:
    """区间内的全部桶，没数据的桶也保留，趋势线不会把断档连成斜线。"""
    keys: list[str] = []
    current = start
    while current <= end:
        key = bucket_key(current, granularity)
        if not keys or keys[-1] != key:
            keys.append(key)
        current += timedelta(days=1)
    return keys


def add_months(d: date, months: int) -> date:
    """加减月份，日期超出目标月末时取月末（3 月 31 日环比到 2 月 28 日）。"""
    index = d.year * 12 + d.month - 1 + months
    year, month = index // 12, index % 12 + 1
    next_first = date(year + (month == 12), month % 12 + 1, 1)
    return min(d.replace(year=year, month=month, day=1) + timedelta(days=d.day - 1),
               next_first - timedelta(days=1))


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Period:
        """环比基期。整自然月对比上一整自然月；其余取等长的紧邻前一段。"""
        if self.start.day == 1 and (self.end + timedelta(days=1)).day == 1:
            months = (self.end.year - self.start.year) * 12 + self.end.month - self.start.month + 1
            prev_start = add_months(self.start, -months)
            return Period(prev_start, self.start - timedelta(days=1))
        return Period(self.start - timedelta(days=self.days), self.start - timedelta(days=1))

    def year_ago(self) -> Period:
        return Period(add_months(self.start, -12), add_months(self.end, -12))


def growth(current: float, base: float | None) -> float | None:
    """增长率。基期为 0 或缺失时返回 None（界面显示"—"），不返回无穷大或 100%。"""
    if base is None or base == 0:
        return None
    return round((current - base) / base, 4)


def median_or_none(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(statistics.median(items), 2) if items else None


def percentile(values: Iterable[float], q: float) -> float | None:
    """线性插值分位数，q ∈ [0, 1]。"""
    items = sorted(values)
    if not items:
        return None
    position = (len(items) - 1) * q
    low = int(position)
    high = min(low + 1, len(items) - 1)
    return round(items[low] + (items[high] - items[low]) * (position - low), 2)


def histogram(values: Iterable[float], edges: list[float], labels: list[str]) -> list[dict]:
    """按 [edges[i], edges[i+1]) 分布计数，最后一档含上界。"""
    if len(labels) != len(edges) - 1:
        raise ValueError("labels 数量必须等于分箱数")
    counts = [0] * len(labels)
    for value in values:
        for i in range(len(labels)):
            last = i == len(labels) - 1
            if edges[i] <= value < edges[i + 1] or (last and value == edges[i + 1]):
                counts[i] += 1
                break
    return [{"name": name, "value": count} for name, count in zip(labels, counts)]


def safe_ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def with_share(rows: list[dict], key: str = "value") -> list[dict]:
    total = sum(row[key] for row in rows)
    return [{**row, "share": round(row[key] / total, 4) if total else 0.0} for row in rows]
