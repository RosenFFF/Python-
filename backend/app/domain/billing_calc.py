"""费用与补贴计算（纯函数）。金额一律用整数「分」，从根上避免浮点误差。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from math import ceil


def round_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_decimal(ratio: float) -> Decimal:
    return Decimal(str(ratio))


@dataclass(frozen=True)
class Split:
    amount: int
    gov: int
    ltci: int
    self_pay: int
    gov_ratio: float
    ltci_ratio: float


def billable_hours(minutes: int) -> Decimal:
    """按小时计价的项目，时长按 15 分钟向上取整。"""
    return Decimal(ceil(max(minutes, 1) / 15)) / Decimal(4)


def service_amount(pricing_mode: str, unit_price: int, actual_minutes: int) -> tuple[Decimal, int]:
    """返回 (数量, 金额分)。"""
    if pricing_mode == "PER_HOUR":
        quantity = billable_hours(actual_minutes)
        return quantity, round_cents(Decimal(unit_price) * quantity)
    return Decimal(1), unit_price


def split_amount(amount: int, item_eligible: bool, item_ratio: float, policy_gov: float, policy_ltci: float,
                 has_ltci: bool) -> Split:
    """拆分三方支付。

    政府补贴比例 = min(项目补贴比例, 政策补贴比例)；项目未纳入补贴时两项支付都为 0。
    长护险比例取政策值，并受 1 − 政府比例 约束，三方相加不超过总额。
    个人自付由总额减前两项得出，不单独舍入，所以三方之和恒等于总额（按分守恒）。
    """
    gov_ratio = min(to_decimal(item_ratio), to_decimal(policy_gov)) if item_eligible else Decimal(0)
    ltci_ratio = to_decimal(policy_ltci) if item_eligible and has_ltci else Decimal(0)
    ltci_ratio = min(ltci_ratio, Decimal(1) - gov_ratio)
    gov = round_cents(Decimal(amount) * gov_ratio)
    ltci = round_cents(Decimal(amount) * ltci_ratio)
    return Split(amount, gov, ltci, amount - gov - ltci, float(gov_ratio), float(ltci_ratio))


def clip_to_cap(split: Split, used_before: int, monthly_cap: int) -> Split:
    """按月度上限截断政府补贴，超出部分转个人自付。"""
    remaining = max(monthly_cap - used_before, 0)
    gov = min(split.gov, remaining)
    return Split(split.amount, gov, split.ltci, split.amount - gov - split.ltci, split.gov_ratio, split.ltci_ratio)


@dataclass(frozen=True)
class FeeView:
    fee_id: int
    elder_id: int
    order_id: int | None
    fee_type: str
    start: datetime | None
    end: datetime | None
    amount: int
    gov: int
    order_status: str | None
    actual_minutes: int | None
    standard_minutes: int | None
    excluded: bool = False


def find_duplicates(fees: list[FeeView]) -> list[tuple[FeeView, FeeView]]:
    """同一老人时间区间重叠的计费条目。后出现的一条标记为重复。退费红冲不参与。"""
    by_elder: dict[int, list[FeeView]] = {}
    for fee in fees:
        if fee.start and fee.end and fee.amount > 0 and fee.fee_type != "CANCEL_FEE":
            by_elder.setdefault(fee.elder_id, []).append(fee)
    pairs: list[tuple[FeeView, FeeView]] = []
    for items in by_elder.values():
        items.sort(key=lambda f: (f.start, f.fee_id))
        for i, earlier in enumerate(items):
            for later in items[i + 1:]:
                if later.start >= earlier.end:
                    break
                pairs.append((earlier, later))
    return pairs


def is_not_completed(fee: FeeView) -> bool:
    """未完成却计费。迟取消、爽约费用本来就发生在未完成工单上，属于合法计费。"""
    if fee.amount <= 0 or fee.fee_type in ("CANCEL_FEE", "REFUND"):
        return False
    return fee.order_status != "COMPLETED"


def is_duration_abnormal(fee: FeeView, multiple: float) -> bool:
    if fee.fee_type not in ("SERVICE", "MANUAL") or not fee.actual_minutes or not fee.standard_minutes:
        return False
    return fee.actual_minutes > fee.standard_minutes * multiple


def sample_size(population: int, ratio: float) -> int:
    """抽查数量向上取整，保证实际比例不低于要求；总体非空时至少抽 1 单。"""
    if population <= 0:
        return 0
    return min(population, max(1, ceil(Decimal(population) * to_decimal(ratio))))


def format_yuan(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f}"
