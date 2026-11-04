"""预警规则判定（纯函数）。规则本身存数据库、可配置，这里只负责判定。"""
from __future__ import annotations

import operator
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

OPERATORS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}


@dataclass(frozen=True)
class RuleSpec:
    rule_type: str
    metric: str | None = None
    op: str | None = None
    threshold: float | None = None
    window: int | None = None  # 连续次数 / 基线样本数
    hours: float | None = None  # 无数据时长


@dataclass(frozen=True)
class Verdict:
    triggered: bool
    value: float | None = None
    message: str = ""


def _not_triggered() -> Verdict:
    return Verdict(False)


def evaluate_threshold(spec: RuleSpec, latest: float) -> Verdict:
    if OPERATORS[spec.op](latest, spec.threshold):
        return Verdict(True, latest, f"当前值 {latest:g} {spec.op} 阈值 {spec.threshold:g}")
    return _not_triggered()


def evaluate_consecutive(spec: RuleSpec, recent_desc: list[float]) -> Verdict:
    """最近 N 次（新到旧）是否全部越界。不足 N 次不触发。"""
    n = spec.window or 3
    window = recent_desc[:n]
    if len(window) < n:
        return _not_triggered()
    compare = OPERATORS[spec.op]
    if all(compare(v, spec.threshold) for v in window):
        joined = "、".join(f"{v:g}" for v in reversed(window))
        return Verdict(True, window[0], f"连续 {n} 次 {spec.op} {spec.threshold:g}（{joined}）")
    return _not_triggered()


def evaluate_no_data(spec: RuleSpec, last_at: datetime | None, reference_at: datetime, now: datetime) -> Verdict:
    """超过时长未上报。从未上报过的老人，从建档时间起算。"""
    limit = timedelta(hours=spec.hours or 48)
    since = last_at or reference_at
    if now - since >= limit:
        hours = (now - since).total_seconds() / 3600
        return Verdict(True, round(hours, 1), f"已 {hours:.1f} 小时未上报任何指标（上限 {spec.hours:g} 小时）")
    return _not_triggered()


def evaluate_baseline(spec: RuleSpec, latest: float, history: list[float]) -> Verdict:
    """个体基线偏离：用中位数和 MAD 计算稳健 Z 分数。

    同样心率 95，对平时 60 的老人是异常，对平时 90 的老人正常。这是固定阈值覆盖不到的情况。
    中位数和 MAD 不受偶发极值影响，比均值加标准差更适合噪声多的可穿戴数据。
    """
    min_samples = spec.window or 10
    if len(history) < min_samples:
        return _not_triggered()
    median = statistics.median(history)
    mad = statistics.median(abs(v - median) for v in history)
    # MAD 为 0（读数完全不变）时用 1 兜底，否则任何微小波动都会触发
    scale = max(1.4826 * mad, 1.0)
    z = (latest - median) / scale
    limit = spec.threshold or 3.5
    if abs(z) >= limit:
        return Verdict(True, latest, f"当前值 {latest:g} 偏离个人基线 {median:g}（稳健 Z={z:.1f}，阈值 {limit:g}）")
    return _not_triggered()
