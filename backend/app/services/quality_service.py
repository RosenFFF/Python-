"""服务评价、低分回访、投诉处理、护理员绩效。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session, object_session

from app.core import clock
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.domain.enums import (
    ComplaintConclusion,
    ComplaintStatus,
    FollowupOutcome,
    FollowupStatus,
    OrderStatus,
    label,
)
from app.domain.state_machine import COMPLAINT_FLOW
from app.domain.stats_calc import month_key, safe_ratio
from app.models import (
    Caregiver,
    CaregiverExclusion,
    CaregiverPenalty,
    Complaint,
    DispatchOffer,
    FollowupTask,
    Review,
    ServiceException,
    ServiceExecution,
    ServiceOrder,
)
from app.services import audit_service, billing_service, config_service
from app.services import notification_service as notify
from app.services.access import Actor, ensure_complaint, ensure_order, scope_caregivers, scope_complaints, scope_orders
from app.services.order_service import record_event, release_slots, transition

ACTIONS = {"REFUND", "REPLACE", "PENALTY"}
ACTION_LABELS = {"REFUND": "退费（红冲）", "REPLACE": "更换护理员", "PENALTY": "计扣分"}


def submit_review(db: Session, actor: Actor, order_id: int, punctuality: int, attitude: int, professionalism: int,
                  effectiveness: int, comment: str | None = None) -> Review:
    order = ensure_order(db, actor, order_id)
    if order.status != OrderStatus.COMPLETED:
        raise Conflict("只能评价已完成的服务")
    scores = (punctuality, attitude, professionalism, effectiveness)
    if any(not isinstance(s, int) or not 1 <= s <= 5 for s in scores):
        raise ValidationFailed("每一项评分必须是 1 到 5 星")
    if db.execute(select(Review.id).where(Review.order_id == order.id)).first():
        raise Conflict("该工单已评价")
    hours = config_service.get(db, "quality.review_window_hours")
    now = clock.now()
    if order.completed_at and now > order.completed_at + timedelta(hours=hours):
        raise Conflict(f"已超过 {hours} 小时评价时限")
    overall = round(sum(scores) / 4, 2)
    review = Review(order_id=order.id, caregiver_id=order.caregiver_id, punctuality=punctuality, attitude=attitude,
                    professionalism=professionalism, effectiveness=effectiveness, overall=overall, comment=comment,
                    reviewer_id=actor.id, created_at=now)
    db.add(review)
    db.flush()
    record_event(db, order, "REVIEW", actor, f"综合评分 {overall:g} 星")
    if overall <= config_service.get(db, "quality.low_score_threshold"):
        _create_followup(db, order, review, now)
    return review


def _create_followup(db: Session, order: ServiceOrder, review: Review, now: datetime) -> FollowupTask:
    hours = config_service.get(db, "quality.followup_hours")
    task = FollowupTask(order_id=order.id, review_id=review.id, community_id=order.community_id,
                        due_at=now + timedelta(hours=hours), created_at=now)
    db.add(task)
    db.flush()
    notify.notify_community_admins(db, order.community_id, "低分评价待回访",
                                   f"{order.order_no} 综合评分 {review.overall:g} 星，请在 {hours} 小时内回访",
                                   ref_type="followup", ref_id=task.id, level="WARN")
    return task


def followup_view(task: FollowupTask) -> dict:
    order = task.order
    now = clock.now()
    review = task_review(order)
    return {
        "id": task.id, "order_id": order.id, "order_no": order.order_no, "elder_name": order.elder.name,
        "caregiver_name": order.caregiver.name if order.caregiver else None, "item_name": order.item.name,
        "status": task.status, "status_label": label("FollowupStatus", task.status), "due_at": task.due_at,
        "overdue": task.status == FollowupStatus.OPEN and now > task.due_at, "outcome": task.outcome,
        "outcome_label": label("FollowupOutcome", task.outcome), "note": task.note, "handled_at": task.handled_at,
        "overall": review.overall if review else None, "comment": review.comment if review else None,
    }


def task_review(order: ServiceOrder) -> Review | None:
    return object_session(order).execute(select(Review).where(Review.order_id == order.id)).scalar()


def list_followups(db: Session, actor: Actor, status: str | None = None) -> list[dict]:
    stmt = select(FollowupTask).where(FollowupTask.order_id.in_(scope_orders(actor, select(ServiceOrder.id))))
    if status:
        stmt = stmt.where(FollowupTask.status == status)
    return [followup_view(t) for t in db.execute(stmt.order_by(FollowupTask.due_at).limit(200)).scalars()]


def handle_followup(db: Session, actor: Actor, task_id: int, outcome: str, note: str) -> FollowupTask:
    task = db.get(FollowupTask, task_id)
    if task is None:
        raise NotFound("回访任务不存在")
    ensure_order(db, actor, task.order_id)
    if task.status != FollowupStatus.OPEN:
        raise Conflict("回访任务已完成")
    if outcome not in FollowupOutcome.__members__:
        raise ValidationFailed("回访结果必须是服务改进或转投诉")
    if not note or not note.strip():
        raise ValidationFailed("回访必须填写核实情况")
    task.status = FollowupStatus.DONE
    task.outcome = outcome
    task.note = note
    task.handled_by = actor.id
    task.handled_at = clock.now()
    if outcome == FollowupOutcome.TO_COMPLAINT:
        _new_complaint(db, task.order, note, "FOLLOWUP", actor.id)
    return task


def scan_overdue_followups(db: Session, now: datetime | None = None) -> int:
    """回访超时提醒街道，并留痕。"""
    now = now or clock.now()
    rows = db.execute(select(FollowupTask).where(
        FollowupTask.status == FollowupStatus.OPEN, FollowupTask.due_at <= now,
        FollowupTask.overdue_flagged_at.is_(None))).scalars().all()
    for task in rows:
        task.overdue_flagged_at = now
        notify.notify_street(db, "回访任务超时", f"{task.order.order_no} 回访已超时，请督办", ref_type="followup",
                             ref_id=task.id, level="WARN")
        notify.notify_community_admins(db, task.community_id, "回访任务超时", f"{task.order.order_no} 回访已超时",
                                       ref_type="followup", ref_id=task.id, level="ERROR")
        audit_service.write(db, audit_service.SYSTEM, "FOLLOWUP_OVERDUE", "followup", task.id)
    return len(rows)


def _next_complaint_no(db: Session) -> str:
    last = db.execute(select(func.max(Complaint.id))).scalar() or 0
    return f"CP{clock.now():%Y%m%d}{last + 1:05d}"


def _new_complaint(db: Session, order: ServiceOrder, content: str, source: str, user_id: int | None) -> Complaint:
    complaint = Complaint(complaint_no=_next_complaint_no(db), order_id=order.id, caregiver_id=order.caregiver_id,
                          org_id=order.org_id, community_id=order.community_id, source=source, content=content,
                          created_by=user_id, created_at=clock.now())
    db.add(complaint)
    db.flush()
    notify.notify_community_admins(db, order.community_id, "新投诉待指派", f"{order.order_no}：{content[:60]}",
                                   ref_type="complaint", ref_id=complaint.id, level="WARN")
    return complaint


def create_complaint(db: Session, actor: Actor, order_id: int, content: str) -> Complaint:
    order = ensure_order(db, actor, order_id)
    if not content or not content.strip():
        raise ValidationFailed("投诉内容不能为空")
    if order.caregiver_id is None:
        raise Conflict("该工单还没有服务人员，无法投诉")
    return _new_complaint(db, order, content, "FAMILY", actor.id)


def assign_complaint(db: Session, actor: Actor, complaint_id: int) -> Complaint:
    complaint = ensure_complaint(db, actor, complaint_id)
    COMPLAINT_FLOW.check(complaint.status, ComplaintStatus.ASSIGNED)
    complaint.status = ComplaintStatus.ASSIGNED
    if complaint.org_id:
        notify.notify_org_admins(db, complaint.org_id, "投诉待说明", f"{complaint.complaint_no}：请提交情况说明",
                                 ref_type="complaint", ref_id=complaint.id, level="WARN")
    return complaint


def explain_complaint(db: Session, actor: Actor, complaint_id: int, explanation: str) -> Complaint:
    complaint = ensure_complaint(db, actor, complaint_id)
    if not explanation or not explanation.strip():
        raise ValidationFailed("情况说明不能为空")
    COMPLAINT_FLOW.check(complaint.status, ComplaintStatus.EXPLAINED)
    complaint.status = ComplaintStatus.EXPLAINED
    complaint.org_explanation = explanation
    complaint.explained_at = clock.now()
    notify.notify_community_admins(db, complaint.community_id, "投诉待结论", f"{complaint.complaint_no}：机构已提交说明",
                                   ref_type="complaint", ref_id=complaint.id)
    return complaint


def conclude_complaint(db: Session, actor: Actor, complaint_id: int, conclusion: str, basis: str,
                       actions: list[str] | None = None, penalty_points: int | None = None) -> Complaint:
    """社区管理员下结论。结论成立时可选退费、更换护理员、计扣分，结论与依据一并归档。"""
    complaint = ensure_complaint(db, actor, complaint_id)
    COMPLAINT_FLOW.check(complaint.status, ComplaintStatus.CONCLUDED)
    if conclusion not in ComplaintConclusion.__members__:
        raise ValidationFailed("结论必须是投诉成立或不成立")
    if not basis or not basis.strip():
        raise ValidationFailed("必须填写结论依据")
    actions = actions or []
    if set(actions) - ACTIONS:
        raise ValidationFailed("处理措施不合法")
    if conclusion == ComplaintConclusion.REJECTED and actions:
        raise ValidationFailed("投诉不成立时不能附加处理措施")
    now = clock.now()
    complaint.status = ComplaintStatus.CONCLUDED
    complaint.conclusion = conclusion
    complaint.basis = basis
    complaint.actions = actions
    complaint.concluded_by = actor.id
    complaint.concluded_at = now
    order = complaint.order
    if "REFUND" in actions:
        refund = billing_service.create_refund(db, actor, order, f"投诉 {complaint.complaint_no} 成立：{basis}")
        complaint.refund_fee_id = refund.id
        notify.notify_family(db, order.elder_id, "退费已受理", f"{order.order_no} 退费已生成红冲账单，计入本月账单",
                             ref_type="order", ref_id=order.id)
    if "REPLACE" in actions and complaint.caregiver_id:
        _replace_caregiver(db, actor, complaint, basis)
    if "PENALTY" in actions and complaint.caregiver_id:
        if not penalty_points or penalty_points <= 0:
            raise ValidationFailed("计扣分必须填写大于 0 的分值")
        complaint.penalty_points = penalty_points
        db.add(CaregiverPenalty(caregiver_id=complaint.caregiver_id, complaint_id=complaint.id, month=month_key(now),
                                points=penalty_points, reason=basis))
    audit_service.write(db, actor.ctx, "COMPLAINT_CONCLUDE", "complaint", complaint.id,
                        {"conclusion": conclusion, "actions": actions, "basis": basis})
    db.flush()
    if conclusion == ComplaintConclusion.UPHELD and complaint.caregiver_id:
        _recheck_key_attention(db, complaint.caregiver_id, now)
    return complaint


def _replace_caregiver(db: Session, actor: Actor, complaint: Complaint, reason: str) -> None:
    """更换护理员：今后不再给这位老人派该护理员，未开始的工单退回待派单。"""
    order = complaint.order
    exists_already = db.execute(select(CaregiverExclusion.id).where(
        CaregiverExclusion.elder_id == order.elder_id, CaregiverExclusion.caregiver_id == complaint.caregiver_id)).first()
    if not exists_already:
        db.add(CaregiverExclusion(elder_id=order.elder_id, caregiver_id=complaint.caregiver_id, reason=reason))
    pending = db.execute(select(ServiceOrder).where(
        ServiceOrder.elder_id == order.elder_id, ServiceOrder.caregiver_id == complaint.caregiver_id,
        ServiceOrder.status == OrderStatus.DISPATCHED)).scalars().all()
    for item in pending:
        release_slots(db, item.id)
        transition(db, item, OrderStatus.PENDING_DISPATCH, "REPLACE_CAREGIVER", actor, f"投诉成立，更换护理员：{reason}")
        item.caregiver_id = None
        item.org_id = None
        notify.notify_grid(db, item.grid_id, "工单需重新派单", f"{item.order_no} 因投诉成立需更换护理员",
                           ref_type="order", ref_id=item.id, level="WARN")


def _recheck_key_attention(db: Session, caregiver_id: int, now: datetime) -> None:
    """月度累计成立投诉达 3 次，标记重点关注，当月派单降权。"""
    month = month_key(now)
    start = datetime(now.year, now.month, 1)
    count = db.execute(select(func.count()).select_from(Complaint).where(
        Complaint.caregiver_id == caregiver_id, Complaint.conclusion == ComplaintConclusion.UPHELD,
        Complaint.concluded_at >= start)).scalar()
    limit = config_service.get(db, "quality.key_attention_complaints")
    caregiver = db.get(Caregiver, caregiver_id)
    if count >= limit and caregiver.key_attention_month != month:
        caregiver.key_attention_month = month
        notify.notify_org_admins(db, caregiver.org_id, "护理员已列入重点关注",
                                 f"{caregiver.name} 本月累计 {count} 次投诉成立，派单已降权",
                                 ref_type="caregiver", ref_id=caregiver.id, level="ERROR")
        audit_service.write(db, audit_service.SYSTEM, "KEY_ATTENTION", "caregiver", caregiver.id, {"month": month, "count": count})


def complaint_view(complaint: Complaint) -> dict:
    order = complaint.order
    return {
        "id": complaint.id, "complaint_no": complaint.complaint_no, "order_id": order.id, "order_no": order.order_no,
        "elder_name": order.elder.name, "item_name": order.item.name,
        "caregiver_name": complaint.caregiver.name if complaint.caregiver else None, "source": complaint.source,
        "content": complaint.content, "status": complaint.status, "status_label": label("ComplaintStatus", complaint.status),
        "org_explanation": complaint.org_explanation, "explained_at": complaint.explained_at,
        "conclusion": complaint.conclusion, "conclusion_label": label("ComplaintConclusion", complaint.conclusion),
        "basis": complaint.basis, "actions": complaint.actions or [],
        "action_labels": [ACTION_LABELS[a] for a in complaint.actions or []], "penalty_points": complaint.penalty_points,
        "created_at": complaint.created_at, "concluded_at": complaint.concluded_at,
    }


def list_complaints(db: Session, actor: Actor, status: str | None = None) -> list[dict]:
    stmt = scope_complaints(actor, select(Complaint))
    if status:
        stmt = stmt.where(Complaint.status == status)
    return [complaint_view(c) for c in db.execute(stmt.order_by(Complaint.id.desc()).limit(200)).scalars()]


def month_range(month: str) -> tuple[datetime, datetime]:
    year, mon = (int(part) for part in month.split("-"))
    start = datetime(year, mon, 1)
    end = datetime(year + (mon == 12), mon % 12 + 1, 1)
    return start, end


def performance(db: Session, actor: Actor, month: str, sort: str = "completed") -> list[dict]:
    """月度绩效榜。

    准时率 = 准时签到工单数 / 已签到工单数；异常率 = 异常单数 / 完工单数 + 异常单数。
    分母为 0 时指标为空，不当作 0 或 100%。
    """
    start, end = month_range(month)
    caregivers = db.execute(scope_caregivers(actor, select(Caregiver))).scalars().all()
    ids = [c.id for c in caregivers]
    completed = dict(db.execute(select(ServiceOrder.caregiver_id, func.count()).where(
        ServiceOrder.caregiver_id.in_(ids), ServiceOrder.status == OrderStatus.COMPLETED,
        ServiceOrder.completed_at >= start, ServiceOrder.completed_at < end).group_by(ServiceOrder.caregiver_id)).all())
    ratings = {r[0]: (float(r[1]), r[2]) for r in db.execute(select(
        Review.caregiver_id, func.avg(Review.overall), func.count()).where(
        Review.caregiver_id.in_(ids), Review.created_at >= start, Review.created_at < end
    ).group_by(Review.caregiver_id)).all()}
    checkins = {r[0]: (r[1], r[2] or 0) for r in db.execute(select(
        ServiceExecution.caregiver_id, func.count(), func.sum(cast(ServiceExecution.is_on_time, Integer))).where(
        ServiceExecution.caregiver_id.in_(ids), ServiceExecution.checkin_at >= start, ServiceExecution.checkin_at < end
    ).group_by(ServiceExecution.caregiver_id)).all()}
    exceptions = dict(db.execute(select(ServiceOrder.caregiver_id, func.count()).join(
        ServiceException, ServiceException.order_id == ServiceOrder.id).where(
        ServiceOrder.caregiver_id.in_(ids), ServiceException.reported_at >= start, ServiceException.reported_at < end
    ).group_by(ServiceOrder.caregiver_id)).all())
    upheld = dict(db.execute(select(Complaint.caregiver_id, func.count()).where(
        Complaint.caregiver_id.in_(ids), Complaint.conclusion == ComplaintConclusion.UPHELD,
        Complaint.concluded_at >= start, Complaint.concluded_at < end).group_by(Complaint.caregiver_id)).all())
    penalties = dict(db.execute(select(CaregiverPenalty.caregiver_id, func.sum(CaregiverPenalty.points)).where(
        CaregiverPenalty.month == month, CaregiverPenalty.caregiver_id.in_(ids)).group_by(CaregiverPenalty.caregiver_id)).all())
    rejects = dict(db.execute(select(DispatchOffer.caregiver_id, func.count()).where(
        DispatchOffer.caregiver_id.in_(ids), DispatchOffer.reject_reason.is_not(None),
        DispatchOffer.responded_at >= start, DispatchOffer.responded_at < end).group_by(DispatchOffer.caregiver_id)).all())
    rows = []
    for c in caregivers:
        done = completed.get(c.id, 0)
        checked, on_time = checkins.get(c.id, (0, 0))
        exception_count = exceptions.get(c.id, 0)
        avg_rating, rating_count = ratings.get(c.id, (None, 0))
        rows.append({
            "caregiver_id": c.id, "name": c.name, "org_name": c.organization.name, "completed": done,
            "avg_rating": round(avg_rating, 2) if avg_rating is not None else None, "rating_count": rating_count,
            "on_time_rate": safe_ratio(on_time, checked),
            "exception_rate": safe_ratio(exception_count, done + exception_count),
            "exceptions": exception_count, "complaints_upheld": upheld.get(c.id, 0),
            "penalty_points": penalties.get(c.id, 0), "rejects": rejects.get(c.id, 0),
            "key_attention": c.key_attention_month == month,
        })
    keys = {
        "completed": lambda r: (-r["completed"], r["caregiver_id"]),
        "rating": lambda r: (-(r["avg_rating"] or 0), -r["completed"]),
        "on_time": lambda r: (-(r["on_time_rate"] or 0), -r["completed"]),
        "exception": lambda r: (r["exception_rate"] if r["exception_rate"] is not None else 2, -r["completed"]),
    }
    rows.sort(key=keys.get(sort, keys["completed"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows
