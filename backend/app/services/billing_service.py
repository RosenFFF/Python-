"""费用结算：费用明细、月度账单、差异校验、复核确认、监管抽查、缴费登记。"""
from __future__ import annotations

import random
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.domain import billing_calc as calc
from app.domain.enums import (
    BillStatus,
    DifficultyType,
    DiscrepancyResolution,
    DiscrepancyStatus,
    DiscrepancyType,
    FeeType,
    PaymentStatus,
    SampleResult,
    label,
)
from app.domain.state_machine import BILL_FLOW
from app.domain.stats_calc import month_key
from app.models import (
    Bill,
    BillOrgConfirmation,
    Community,
    Discrepancy,
    Elder,
    ElderStatement,
    FeeDetail,
    SampleCheck,
    ServiceExecution,
    ServiceOrder,
    SubsidyPolicy,
)
from app.services import audit_service, config_service
from app.services import notification_service as notify
from app.services.access import Actor, ensure_bill, ensure_elder, ensure_order, scope_bills, scope_elders


def policy_for(db: Session, elder: Elder) -> SubsidyPolicy:
    stmt = select(SubsidyPolicy).where(SubsidyPolicy.disability_level == elder.disability_level)
    policy = db.execute(stmt.where(SubsidyPolicy.difficulty_type == elder.difficulty_type)).scalar()
    if policy is None:
        policy = db.execute(stmt.where(SubsidyPolicy.difficulty_type == DifficultyType.NONE)).scalar()
    if policy is None:
        raise ValidationFailed(f"未配置「{label('DisabilityLevel', elder.disability_level)}」的补贴政策")
    return policy


def _has_ltci(elder: Elder) -> bool:
    return bool(elder.ltc_insurance) and elder.ltc_insurance not in ("未参保", "无")


def _split_for_order(db: Session, order: ServiceOrder, amount: int) -> calc.Split:
    """政策按老人当前的失能等级和困难类型取值；价格和项目补贴比例取工单绑定的版本。"""
    version = order.price_version
    policy = policy_for(db, order.elder)
    return calc.split_amount(amount, order.item.subsidy_eligible, version.subsidy_ratio, policy.gov_ratio,
                             policy.ltci_ratio, _has_ltci(order.elder))


def record_service_fee(db: Session, order: ServiceOrder, execution: ServiceExecution) -> FeeDetail:
    version = order.price_version
    minutes = execution.duration_minutes or 0
    quantity, amount = calc.service_amount(order.item.pricing_mode, version.unit_price, minutes)
    split = _split_for_order(db, order, amount)
    fee = FeeDetail(
        month=month_key(execution.checkout_at or order.scheduled_start), fee_type=FeeType.SERVICE,
        elder_id=order.elder_id, community_id=order.community_id, org_id=order.org_id, order_id=order.id,
        price_version_id=version.id, unit_price=version.unit_price, quantity=float(quantity), amount=amount,
        gov_amount=split.gov, ltci_amount=split.ltci, self_amount=split.self_pay, gov_ratio=split.gov_ratio,
        ltci_ratio=split.ltci_ratio, service_start=execution.checkin_at, service_end=execution.checkout_at,
        actual_minutes=minutes, standard_minutes=order.item.standard_minutes,
    )
    db.add(fee)
    db.flush()
    return fee


def cancel_fee_amount(db: Session, order: ServiceOrder) -> int:
    ratio = config_service.get(db, "order.late_cancel_fee_ratio")
    version = order.price_version
    _, base = calc.service_amount(order.item.pricing_mode, version.unit_price, order.item.standard_minutes)
    return calc.round_cents(calc.Decimal(base) * calc.to_decimal(ratio))


def record_cancel_fee(db: Session, order: ServiceOrder) -> FeeDetail | None:
    """迟取消和爽约费用由个人承担，不纳入政府补贴和长护险。"""
    amount = cancel_fee_amount(db, order)
    if amount <= 0:
        return None
    fee = FeeDetail(
        month=month_key(order.scheduled_start), fee_type=FeeType.CANCEL_FEE, elder_id=order.elder_id,
        community_id=order.community_id, org_id=order.org_id, order_id=order.id,
        price_version_id=order.price_version_id, unit_price=order.price_version.unit_price, quantity=1, amount=amount,
        self_amount=amount, service_start=order.scheduled_start, service_end=order.scheduled_end,
        note=order.cancel_reason,
    )
    db.add(fee)
    db.flush()
    return fee


def create_manual_fee(db: Session, actor: Actor, order_id: int, amount: int, note: str) -> FeeDetail:
    """人工补录。同样要过差异校验，补录不是绕过校验的后门。"""
    order = ensure_order(db, actor, order_id)
    if amount <= 0:
        raise ValidationFailed("补录金额必须大于 0")
    if not note or not note.strip():
        raise ValidationFailed("人工补录必须填写说明")
    split = _split_for_order(db, order, amount)
    execution = order.execution
    fee = FeeDetail(
        month=month_key(order.scheduled_start), fee_type=FeeType.MANUAL, elder_id=order.elder_id,
        community_id=order.community_id, org_id=order.org_id, order_id=order.id,
        price_version_id=order.price_version_id, unit_price=order.price_version.unit_price, quantity=1, amount=amount,
        gov_amount=split.gov, ltci_amount=split.ltci, self_amount=split.self_pay, gov_ratio=split.gov_ratio,
        ltci_ratio=split.ltci_ratio,
        service_start=execution.checkin_at if execution else order.scheduled_start,
        service_end=execution.checkout_at if execution and execution.checkout_at else order.scheduled_end,
        actual_minutes=execution.duration_minutes if execution else None,
        standard_minutes=order.item.standard_minutes, note=note,
    )
    db.add(fee)
    db.flush()
    audit_service.write(db, actor.ctx, "FEE_MANUAL", "fee", fee.id, {"order": order.order_no, "amount": amount, "note": note})
    return fee


def create_refund(db: Session, actor: Actor, order: ServiceOrder, reason: str) -> FeeDetail:
    """红冲：新增负数明细，从不修改原记录。计入当前月账单，已生效账单不被回改。"""
    original = db.execute(select(FeeDetail).where(
        FeeDetail.order_id == order.id, FeeDetail.fee_type == FeeType.SERVICE, FeeDetail.excluded.is_(False))).scalar()
    if original is None:
        raise Conflict("该工单没有可退费的费用明细")
    if db.execute(select(FeeDetail.id).where(FeeDetail.refund_of_id == original.id)).first():
        raise Conflict("该工单已退费，不能重复退费")
    now = clock.now()
    refund = FeeDetail(
        month=month_key(now), fee_type=FeeType.REFUND, elder_id=original.elder_id, community_id=original.community_id,
        org_id=original.org_id, order_id=order.id, price_version_id=original.price_version_id,
        unit_price=original.unit_price, quantity=-original.quantity, amount=-original.amount,
        gov_amount=-original.gov_amount, ltci_amount=-original.ltci_amount, self_amount=-original.self_amount,
        gov_ratio=original.gov_ratio, ltci_ratio=original.ltci_ratio, service_start=original.service_start,
        service_end=original.service_end, refund_of_id=original.id, note=reason,
    )
    db.add(refund)
    db.flush()
    audit_service.write(db, actor.ctx, "REFUND", "fee", refund.id, {
        "order": order.order_no, "amount": original.amount, "reason": reason})
    return refund


def _fee_view(fee: FeeDetail, order_status: str | None) -> calc.FeeView:
    return calc.FeeView(fee.id, fee.elder_id, fee.order_id, fee.fee_type, fee.service_start, fee.service_end, fee.amount,
                        fee.gov_amount, order_status, fee.actual_minutes, fee.standard_minutes, fee.excluded)


def _recompute_totals(db: Session, bill: Bill) -> None:
    fees = db.execute(select(FeeDetail).where(FeeDetail.bill_id == bill.id, FeeDetail.excluded.is_(False))).scalars().all()
    effective = [f for f in fees if not f.intercepted]
    bill.total_amount = sum(f.amount for f in effective)
    bill.gov_amount = sum(f.gov_amount for f in effective)
    bill.ltci_amount = sum(f.ltci_amount for f in effective)
    bill.self_amount = sum(f.self_amount for f in effective)
    bill.intercepted_amount = sum(f.amount for f in fees if f.intercepted)


def _add_discrepancy(db: Session, bill: Bill, fee: FeeDetail, kind: str, amount: int, detail: str,
                     related_fee_id: int | None = None) -> None:
    fee.intercepted = True
    db.add(Discrepancy(bill_id=bill.id, fee_id=fee.id, related_fee_id=related_fee_id, kind=kind, amount=amount, detail=detail))


def run_checks(db: Session, bill: Bill, fees: list[FeeDetail]) -> None:
    """四类差异校验。命中的明细被拦截，先不计入账单合计，处理后才放行。"""
    order_ids = {f.order_id for f in fees if f.order_id}
    statuses = dict(db.execute(select(ServiceOrder.id, ServiceOrder.status).where(ServiceOrder.id.in_(order_ids))).all())
    views = {f.id: _fee_view(f, statuses.get(f.order_id)) for f in fees}
    multiple = config_service.get(db, "billing.duration_multiple")
    by_id = {f.id: f for f in fees}

    flagged_dup: set[int] = set()
    for earlier, later in calc.find_duplicates(list(views.values())):
        if later.fee_id in flagged_dup:
            continue
        flagged_dup.add(later.fee_id)
        _add_discrepancy(db, bill, by_id[later.fee_id], DiscrepancyType.DUPLICATE, later.amount,
                         f"与明细 #{earlier.fee_id} 同一老人时段重叠（{later.start:%m-%d %H:%M}）", earlier.fee_id)

    for fee in fees:
        view = views[fee.id]
        if calc.is_not_completed(view):
            status_text = label("OrderStatus", view.order_status) or "无工单"
            _add_discrepancy(db, bill, fee, DiscrepancyType.NOT_COMPLETED, fee.amount, f"关联工单状态为「{status_text}」，未完成却计费")
        if calc.is_duration_abnormal(view, multiple):
            _add_discrepancy(db, bill, fee, DiscrepancyType.DURATION_ABNORMAL, fee.amount,
                             f"服务时长 {fee.actual_minutes} 分钟，超过标准时长 {fee.standard_minutes} 分钟的 {multiple:g} 倍")

    # 月度补贴上限：按服务时间累加，从越线那条开始拦截
    used: dict[int, int] = {}
    elders = {e.id: e for e in db.execute(select(Elder).where(Elder.id.in_({f.elder_id for f in fees}))).scalars()}
    for fee in sorted(fees, key=lambda f: (f.service_start or datetime.min, f.id)):
        if fee.gov_amount == 0 or fee.intercepted:
            continue
        cap = policy_for(db, elders[fee.elder_id]).monthly_cap
        before = used.get(fee.elder_id, 0)
        if before + fee.gov_amount > cap > 0:
            over = before + fee.gov_amount - cap
            _add_discrepancy(db, bill, fee, DiscrepancyType.OVER_CAP, over,
                             f"累计补贴将达 {calc.format_yuan(before + fee.gov_amount)} 元，超出月度上限 "
                             f"{calc.format_yuan(cap)} 元，超出 {calc.format_yuan(over)} 元")
            used[fee.elder_id] = cap
        else:
            used[fee.elder_id] = before + fee.gov_amount


def generate_bill(db: Session, community_id: int, month: str, actor: Actor | None = None) -> Bill:
    """生成社区月度账单。未进入确认流程的账单可重算，把后续新增费用纳入校验。"""
    if db.get(Community, community_id) is None:
        raise NotFound("社区不存在")
    bill = db.execute(select(Bill).where(Bill.community_id == community_id, Bill.month == month)).scalar()
    if bill is not None and bill.status not in (BillStatus.DRAFT, BillStatus.NEEDS_REVIEW):
        raise Conflict(f"该月账单已进入「{label('BillStatus', bill.status)}」，不能重新生成")
    if bill is None:
        bill = Bill(community_id=community_id, month=month, generated_at=clock.now())
        db.add(bill)
        db.flush()
    # 只重置待处理差异；已处理的差异是人工结论，重算时保留
    resolved_fees = set(db.execute(select(Discrepancy.fee_id).where(
        Discrepancy.bill_id == bill.id, Discrepancy.status == DiscrepancyStatus.RESOLVED)).scalars())
    for row in db.execute(select(Discrepancy).where(
            Discrepancy.bill_id == bill.id, Discrepancy.status == DiscrepancyStatus.OPEN)).scalars():
        db.delete(row)
    fees = db.execute(select(FeeDetail).where(
        FeeDetail.month == month, FeeDetail.community_id == community_id, FeeDetail.excluded.is_(False),
        (FeeDetail.bill_id.is_(None)) | (FeeDetail.bill_id == bill.id))).scalars().all()
    for fee in fees:
        fee.bill_id = bill.id
        fee.intercepted = False
    db.flush()
    run_checks(db, bill, [f for f in fees if f.id not in resolved_fees])
    db.flush()
    _recompute_totals(db, bill)
    discrepancies = db.execute(select(Discrepancy).where(Discrepancy.bill_id == bill.id)).scalars().all()
    bill.discrepancy_amount = sum(d.amount for d in discrepancies)
    gross = bill.total_amount + bill.intercepted_amount
    bill.discrepancy_ratio = round(bill.discrepancy_amount / gross, 6) if gross else 0.0
    limit = config_service.get(db, "billing.discrepancy_ratio_limit")
    bill.needs_review = bill.discrepancy_ratio > limit
    bill.status = BillStatus.NEEDS_REVIEW if bill.needs_review and not bill.reviewed_at else BillStatus.DRAFT
    # 涉及的每家机构都要确认
    org_ids = {f.org_id for f in fees if f.org_id}
    existing = set(db.execute(select(BillOrgConfirmation.org_id).where(BillOrgConfirmation.bill_id == bill.id)).scalars())
    for org_id in org_ids - existing:
        db.add(BillOrgConfirmation(bill_id=bill.id, org_id=org_id))
    if actor:
        audit_service.write(db, actor.ctx, "BILL_GENERATE", "bill", bill.id, {"month": month})
    community = db.get(Community, community_id)
    notify.notify_community_admins(db, community_id, "月度账单已生成",
                                   f"{community.name} {month} 账单 {calc.format_yuan(bill.total_amount)} 元，"
                                   f"差异比例 {bill.discrepancy_ratio:.2%}", ref_type="bill", ref_id=bill.id)
    return bill


def generate_all_for_month(db: Session, month: str) -> int:
    count = 0
    for community_id in db.execute(select(Community.id)).scalars():
        bill = db.execute(select(Bill).where(Bill.community_id == community_id, Bill.month == month)).scalar()
        if bill is not None:
            continue
        generate_bill(db, community_id, month)
        count += 1
    return count


def resolve_discrepancy(db: Session, actor: Actor, discrepancy_id: int, resolution: str, explanation: str) -> Discrepancy:
    """差异逐条说明与处理。"""
    row = db.get(Discrepancy, discrepancy_id)
    if row is None:
        raise NotFound("差异条目不存在")
    bill = ensure_bill(db, actor, row.bill_id)
    if bill.status not in (BillStatus.DRAFT, BillStatus.NEEDS_REVIEW):
        raise Conflict("账单已进入确认流程，不能再处理差异")
    if row.status == DiscrepancyStatus.RESOLVED:
        raise Conflict("该差异已处理")
    if not explanation or not explanation.strip():
        raise ValidationFailed("处理差异必须填写说明")
    allowed = {
        DiscrepancyType.DUPLICATE: {DiscrepancyResolution.APPROVE, DiscrepancyResolution.EXCLUDE},
        DiscrepancyType.OVER_CAP: {DiscrepancyResolution.CLIP, DiscrepancyResolution.EXCLUDE},
        DiscrepancyType.NOT_COMPLETED: {DiscrepancyResolution.EXCLUDE},
        DiscrepancyType.DURATION_ABNORMAL: {DiscrepancyResolution.APPROVE, DiscrepancyResolution.EXCLUDE},
        DiscrepancyType.SAMPLE_FAILED: {DiscrepancyResolution.APPROVE, DiscrepancyResolution.EXCLUDE},
    }[row.kind]
    if resolution not in allowed:
        names = "、".join(label("DiscrepancyResolution", r) for r in allowed)
        raise ValidationFailed(f"「{label('DiscrepancyType', row.kind)}」只能选择：{names}")
    fee = row.fee
    if resolution == DiscrepancyResolution.EXCLUDE:
        fee.excluded = True
        fee.intercepted = False
    elif resolution == DiscrepancyResolution.CLIP:
        used = db.execute(select(func.coalesce(func.sum(FeeDetail.gov_amount), 0)).where(
            FeeDetail.bill_id == bill.id, FeeDetail.elder_id == fee.elder_id, FeeDetail.id != fee.id,
            FeeDetail.excluded.is_(False), FeeDetail.intercepted.is_(False))).scalar()
        cap = policy_for(db, fee.elder).monthly_cap
        clipped = calc.clip_to_cap(
            calc.Split(fee.amount, fee.gov_amount, fee.ltci_amount, fee.self_amount, fee.gov_ratio, fee.ltci_ratio),
            used, cap)
        fee.gov_amount, fee.self_amount = clipped.gov, clipped.self_pay
    row.status = DiscrepancyStatus.RESOLVED
    row.resolution = resolution
    row.explanation = explanation
    row.handled_by = actor.id
    row.handled_by_name = actor.name
    row.handled_at = clock.now()
    db.flush()
    # 同一明细可能命中多条差异，全部处理完才放行
    still_open = db.execute(select(Discrepancy.id).where(
        Discrepancy.fee_id == fee.id, Discrepancy.status == DiscrepancyStatus.OPEN)).first()
    if not fee.excluded and not still_open:
        fee.intercepted = False
    db.flush()
    _recompute_totals(db, bill)
    audit_service.write(db, actor.ctx, "DISCREPANCY_RESOLVE", "discrepancy", row.id,
                        {"resolution": resolution, "explanation": explanation, "kind": row.kind})
    return row


def _open_discrepancies(db: Session, bill_id: int) -> int:
    return db.execute(select(func.count()).select_from(Discrepancy).where(
        Discrepancy.bill_id == bill_id, Discrepancy.status == DiscrepancyStatus.OPEN)).scalar()


def review_bill(db: Session, actor: Actor, bill_id: int, note: str) -> Bill:
    """差异超 1% 的账单须人工复核。"""
    bill = ensure_bill(db, actor, bill_id)
    if bill.status != BillStatus.NEEDS_REVIEW:
        raise Conflict("该账单不需要人工复核")
    if not note or not note.strip():
        raise ValidationFailed("人工复核必须填写复核意见")
    if _open_discrepancies(db, bill.id):
        raise Conflict("还有未处理的差异条目，不能完成复核")
    BILL_FLOW.check(bill.status, BillStatus.DRAFT)
    bill.status = BillStatus.DRAFT
    bill.reviewed_by = actor.id
    bill.reviewed_at = clock.now()
    bill.review_note = note
    audit_service.write(db, actor.ctx, "BILL_REVIEW", "bill", bill.id, {"note": note, "ratio": bill.discrepancy_ratio})
    return bill


def grid_confirm(db: Session, actor: Actor, bill_id: int) -> Bill:
    bill = ensure_bill(db, actor, bill_id)
    if _open_discrepancies(db, bill.id):
        raise Conflict("还有未处理的差异条目，不能提交")
    if bill.needs_review and not bill.reviewed_at:
        raise Conflict(f"差异比例 {bill.discrepancy_ratio:.2%} 超过阈值，须先完成人工复核")
    BILL_FLOW.check(bill.status, BillStatus.GRID_CONFIRMED)
    bill.status = BillStatus.GRID_CONFIRMED
    bill.grid_confirmed_by = actor.id
    bill.grid_confirmed_at = clock.now()
    audit_service.write(db, actor.ctx, "BILL_GRID_CONFIRM", "bill", bill.id)
    confirmations = db.execute(select(BillOrgConfirmation).where(BillOrgConfirmation.bill_id == bill.id)).scalars()
    for row in confirmations:
        notify.notify_org_admins(db, row.org_id, "账单待确认", f"{bill.month} 账单已由网格员核对，请确认",
                                 ref_type="bill", ref_id=bill.id)
    return bill


def org_confirm(db: Session, actor: Actor, bill_id: int) -> Bill:
    """机构确认。所有涉及机构都确认后进入监管抽查。"""
    bill = ensure_bill(db, actor, bill_id)
    if bill.status != BillStatus.GRID_CONFIRMED:
        raise Conflict(f"账单当前为「{label('BillStatus', bill.status)}」，不能确认")
    rows = db.execute(select(BillOrgConfirmation).where(BillOrgConfirmation.bill_id == bill.id)).scalars().all()
    if actor.user.org_id is None and rows:
        pending = [r for r in rows if r.confirmed_at is None]
    else:
        pending = [r for r in rows if r.org_id == actor.user.org_id and r.confirmed_at is None]
    if not pending and rows and actor.user.org_id is not None:
        raise Conflict("本机构已确认过该账单")
    now = clock.now()
    for row in pending:
        row.confirmed_by, row.confirmed_at = actor.id, now
    audit_service.write(db, actor.ctx, "BILL_ORG_CONFIRM", "bill", bill.id, {"org_id": actor.user.org_id})
    db.flush()
    if all(r.confirmed_at for r in rows):
        BILL_FLOW.check(bill.status, BillStatus.ORG_CONFIRMED)
        bill.status = BillStatus.ORG_CONFIRMED
        _create_samples(db, bill)
    return bill


def _create_samples(db: Session, bill: Bill) -> None:
    """抽取不低于 10% 的工单。随机种子落库，抽样结果可复核，排除"挑着抽"的嫌疑。"""
    order_ids = sorted(set(db.execute(select(FeeDetail.order_id).where(
        FeeDetail.bill_id == bill.id, FeeDetail.excluded.is_(False), FeeDetail.fee_type == FeeType.SERVICE,
        FeeDetail.intercepted.is_(False))).scalars()))
    ratio = config_service.get(db, "billing.sampling_ratio")
    size = calc.sample_size(len(order_ids), ratio)
    bill.sample_seed = random.SystemRandom().randrange(1, 2**31)
    picked = random.Random(bill.sample_seed).sample(order_ids, size) if size else []
    for order_id in picked:
        db.add(SampleCheck(bill_id=bill.id, order_id=order_id))
    notify.notify_street(db, "账单待抽查", f"{bill.month} 账单已确认，请抽查 {len(picked)} 张工单",
                         ref_type="bill", ref_id=bill.id)
    if not picked:
        _make_effective(db, bill)


def sample_evidence(order: ServiceOrder) -> dict:
    execution = order.execution
    return {
        "order_no": order.order_no, "item": order.item.name, "scheduled_start": order.scheduled_start.isoformat(),
        "checkin_at": execution.checkin_at.isoformat() if execution else None,
        "checkin_distance_m": round(execution.checkin_distance_m, 1) if execution else None,
        "checkout_at": execution.checkout_at.isoformat() if execution and execution.checkout_at else None,
        "duration_minutes": execution.duration_minutes if execution else None,
        "standard_minutes": order.item.standard_minutes, "checklist": execution.checklist if execution else [],
        "summary": execution.summary if execution else None,
        "photo_ids": [p.id for p in execution.photos] if execution else [],
        "caregiver": order.caregiver.name if order.caregiver else None,
    }


def check_sample(db: Session, actor: Actor, sample_id: int, passed: bool, note: str | None) -> Bill:
    sample = db.get(SampleCheck, sample_id)
    if sample is None:
        raise NotFound("抽查条目不存在")
    bill = ensure_bill(db, actor, sample.bill_id)
    if bill.status != BillStatus.ORG_CONFIRMED:
        raise Conflict("账单不在抽查阶段")
    if sample.result != SampleResult.PENDING:
        raise Conflict("该工单已抽查")
    if not passed and not (note and note.strip()):
        raise ValidationFailed("抽查不通过必须填写原因")
    sample.result = SampleResult.PASS if passed else SampleResult.FAIL
    sample.note, sample.checked_by, sample.checked_at = note, actor.id, clock.now()
    sample.evidence = sample_evidence(sample.order)
    db.flush()
    audit_service.write(db, actor.ctx, "SAMPLE_CHECK", "bill", bill.id, {"order_id": sample.order_id, "passed": passed})
    samples = db.execute(select(SampleCheck).where(SampleCheck.bill_id == bill.id)).scalars().all()
    if any(s.result == SampleResult.PENDING for s in samples):
        return bill
    failed = [s for s in samples if s.result == SampleResult.FAIL]
    if failed:
        _send_back(db, bill, failed)
    else:
        _make_effective(db, bill)
    return bill


def _send_back(db: Session, bill: Bill, failed: list[SampleCheck]) -> None:
    """抽查未通过：对应明细转入差异清单，账单退回重走确认流程。"""
    for sample in failed:
        fee = db.execute(select(FeeDetail).where(FeeDetail.bill_id == bill.id, FeeDetail.order_id == sample.order_id,
                                                 FeeDetail.fee_type == FeeType.SERVICE)).scalar()
        if fee:
            _add_discrepancy(db, bill, fee, DiscrepancyType.SAMPLE_FAILED, fee.amount, f"监管抽查不通过：{sample.note}")
    BILL_FLOW.check(bill.status, BillStatus.DRAFT)
    bill.status = BillStatus.DRAFT
    bill.grid_confirmed_by = bill.grid_confirmed_at = None
    for row in db.execute(select(BillOrgConfirmation).where(BillOrgConfirmation.bill_id == bill.id)).scalars():
        row.confirmed_by = row.confirmed_at = None
    for sample in db.execute(select(SampleCheck).where(SampleCheck.bill_id == bill.id)).scalars():
        db.delete(sample)
    db.flush()
    _recompute_totals(db, bill)
    notify.notify_community_admins(db, bill.community_id, "账单被退回", f"{bill.month} 账单抽查未通过，请处理差异后重新提交",
                                   ref_type="bill", ref_id=bill.id, level="ERROR")


def _make_effective(db: Session, bill: Bill) -> None:
    BILL_FLOW.check(bill.status, BillStatus.EFFECTIVE)
    bill.status = BillStatus.EFFECTIVE
    bill.effective_at = clock.now()
    fees = db.execute(select(FeeDetail).where(FeeDetail.bill_id == bill.id, FeeDetail.excluded.is_(False),
                                              FeeDetail.intercepted.is_(False))).scalars().all()
    totals: dict[int, list[int]] = {}
    for fee in fees:
        row = totals.setdefault(fee.elder_id, [0, 0, 0, 0])
        for i, value in enumerate((fee.amount, fee.gov_amount, fee.ltci_amount, fee.self_amount)):
            row[i] += value
    for elder_id, (amount, gov, ltci, self_pay) in totals.items():
        db.add(ElderStatement(bill_id=bill.id, elder_id=elder_id, month=bill.month, total_amount=amount, gov_amount=gov,
                              ltci_amount=ltci, self_amount=self_pay,
                              # 本月无需个人支付（如自付额被退费冲抵为 0）时直接视为已结清
                              payment_status=PaymentStatus.PAID if self_pay <= 0 else PaymentStatus.UNPAID))
        if self_pay > 0:
            notify.notify_family(db, elder_id, "本月账单已生效", f"{bill.month} 个人自付 {calc.format_yuan(self_pay)} 元",
                                 ref_type="statement", ref_id=bill.id)
    audit_service.write(db, audit_service.SYSTEM, "BILL_EFFECTIVE", "bill", bill.id, {"total": bill.total_amount})


def register_payment(db: Session, actor: Actor, statement_id: int, method: str) -> ElderStatement:
    """登记缴费，并把缴费状态回写到该月工单。"""
    statement = db.get(ElderStatement, statement_id)
    if statement is None:
        raise NotFound("缴费单不存在")
    ensure_elder(db, actor, statement.elder_id, include_deleted=True)
    if statement.payment_status == PaymentStatus.PAID:
        raise Conflict("该缴费单已登记缴费")
    statement.payment_status = PaymentStatus.PAID
    statement.paid_at = clock.now()
    statement.paid_by = actor.id
    statement.payment_method = method
    order_ids = db.execute(select(FeeDetail.order_id).where(
        FeeDetail.bill_id == statement.bill_id, FeeDetail.elder_id == statement.elder_id,
        FeeDetail.order_id.is_not(None))).scalars().all()
    for order in db.execute(select(ServiceOrder).where(ServiceOrder.id.in_(order_ids))).scalars():
        order.payment_status = PaymentStatus.PAID
    audit_service.write(db, actor.ctx, "PAYMENT_REGISTER", "statement", statement.id,
                        {"amount": statement.self_amount, "method": method})
    return statement


def bill_view(db: Session, bill: Bill) -> dict:
    community = db.get(Community, bill.community_id)
    open_count = _open_discrepancies(db, bill.id)
    return {
        "id": bill.id, "community_id": bill.community_id, "community_name": community.name, "month": bill.month,
        "status": bill.status, "status_label": label("BillStatus", bill.status), "total_amount": bill.total_amount,
        "gov_amount": bill.gov_amount, "ltci_amount": bill.ltci_amount, "self_amount": bill.self_amount,
        "intercepted_amount": bill.intercepted_amount, "discrepancy_amount": bill.discrepancy_amount,
        "discrepancy_ratio": bill.discrepancy_ratio, "needs_review": bill.needs_review, "reviewed_at": bill.reviewed_at,
        "review_note": bill.review_note, "open_discrepancies": open_count, "generated_at": bill.generated_at,
        "effective_at": bill.effective_at, "sample_seed": bill.sample_seed,
    }


def list_bills(db: Session, actor: Actor, month: str | None = None, status: str | None = None) -> list[dict]:
    stmt = scope_bills(actor, select(Bill))
    if month:
        stmt = stmt.where(Bill.month == month)
    if status:
        stmt = stmt.where(Bill.status == status)
    rows = db.execute(stmt.order_by(Bill.month.desc(), Bill.community_id).limit(200)).scalars().all()
    return [bill_view(db, b) for b in rows]


def fee_view(fee: FeeDetail) -> dict:
    return {
        "id": fee.id, "month": fee.month, "fee_type": fee.fee_type, "fee_type_label": label("FeeType", fee.fee_type),
        "elder_id": fee.elder_id, "elder_name": fee.elder.name, "order_id": fee.order_id,
        "order_no": fee.order.order_no if fee.order else None,
        "item_name": fee.order.item.name if fee.order else None, "unit_price": fee.unit_price, "quantity": fee.quantity,
        "amount": fee.amount, "gov_amount": fee.gov_amount, "ltci_amount": fee.ltci_amount, "self_amount": fee.self_amount,
        "gov_ratio": fee.gov_ratio, "ltci_ratio": fee.ltci_ratio, "service_start": fee.service_start,
        "actual_minutes": fee.actual_minutes, "standard_minutes": fee.standard_minutes, "intercepted": fee.intercepted,
        "excluded": fee.excluded, "note": fee.note,
    }


def discrepancy_view(row: Discrepancy) -> dict:
    return {
        "id": row.id, "bill_id": row.bill_id, "fee_id": row.fee_id, "related_fee_id": row.related_fee_id, "kind": row.kind,
        "kind_label": label("DiscrepancyType", row.kind), "amount": row.amount, "detail": row.detail, "status": row.status,
        "status_label": label("DiscrepancyStatus", row.status), "resolution": row.resolution,
        "resolution_label": label("DiscrepancyResolution", row.resolution), "explanation": row.explanation,
        "handled_by": row.handled_by_name, "handled_at": row.handled_at, "elder_name": row.fee.elder.name,
        "order_no": row.fee.order.order_no if row.fee.order else None,
    }


def bill_detail(db: Session, actor: Actor, bill_id: int, page: int = 1, size: int = 50) -> dict:
    bill = ensure_bill(db, actor, bill_id)
    view = bill_view(db, bill)
    fees_stmt = select(FeeDetail).where(FeeDetail.bill_id == bill.id).order_by(FeeDetail.id)
    view["fee_total"] = db.execute(select(func.count()).select_from(fees_stmt.subquery())).scalar()
    view["fees"] = [fee_view(f) for f in db.execute(fees_stmt.limit(size).offset((page - 1) * size)).scalars()]
    view["discrepancies"] = [discrepancy_view(d) for d in db.execute(
        select(Discrepancy).where(Discrepancy.bill_id == bill.id).order_by(Discrepancy.status, Discrepancy.id)).scalars()]
    view["confirmations"] = [{"org_id": c.org_id, "confirmed_at": c.confirmed_at} for c in db.execute(
        select(BillOrgConfirmation).where(BillOrgConfirmation.bill_id == bill.id)).scalars()]
    view["samples"] = [{
        "id": s.id, "order_id": s.order_id, "result": s.result, "result_label": label("SampleResult", s.result),
        "note": s.note, "checked_at": s.checked_at, "evidence": s.evidence or sample_evidence(s.order),
    } for s in db.execute(select(SampleCheck).where(SampleCheck.bill_id == bill.id).order_by(SampleCheck.id)).scalars()]
    return view


def export_rows(db: Session, actor: Actor, bill_id: int) -> tuple[Bill, list[str], list[list]]:
    bill = ensure_bill(db, actor, bill_id)
    headers = ["明细ID", "工单号", "老人", "类型", "服务项目", "单价(元)", "数量", "金额(元)", "政府补贴(元)",
               "长护险(元)", "个人自付(元)", "状态"]
    rows = []
    for fee in db.execute(select(FeeDetail).where(FeeDetail.bill_id == bill.id).order_by(FeeDetail.id)).scalars():
        status = "已剔除" if fee.excluded else "已拦截" if fee.intercepted else "正常"
        rows.append([fee.id, fee.order.order_no if fee.order else "", fee.elder.name, label("FeeType", fee.fee_type),
                     fee.order.item.name if fee.order else "", calc.format_yuan(fee.unit_price), fee.quantity,
                     calc.format_yuan(fee.amount), calc.format_yuan(fee.gov_amount), calc.format_yuan(fee.ltci_amount),
                     calc.format_yuan(fee.self_amount), status])
    audit_service.write(db, actor.ctx, "EXPORT", "bill", bill.id, {"rows": len(rows)})
    return bill, headers, rows


def my_statements(db: Session, actor: Actor, elder_id: int | None = None) -> list[dict]:
    stmt = select(ElderStatement).where(ElderStatement.elder_id.in_(scope_elders(actor, select(Elder.id))))
    if elder_id:
        stmt = stmt.where(ElderStatement.elder_id == elder_id)
    rows = db.execute(stmt.order_by(ElderStatement.month.desc()).limit(100)).scalars().all()
    return [{
        "id": s.id, "elder_id": s.elder_id, "elder_name": s.elder.name, "month": s.month, "total_amount": s.total_amount,
        "gov_amount": s.gov_amount, "ltci_amount": s.ltci_amount, "self_amount": s.self_amount,
        "payment_status": s.payment_status, "payment_label": label("PaymentStatus", s.payment_status), "paid_at": s.paid_at,
        "payment_method": s.payment_method,
    } for s in rows]


def previous_month(today: date) -> str:
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return f"{year:04d}-{month:02d}"


def run_monthly_billing(db: Session, now: datetime | None = None) -> int:
    """每月 1 日凌晨汇总上月账单。已生成的月份跳过，任务重跑安全。"""
    now = now or clock.now()
    return generate_all_for_month(db, previous_month(now.date()))
