"""智能派单：候选推荐、冲突检测、接拒单、超时顺延、改派。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock, crypto
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.domain.dispatch_scoring import CandidateInput, ScoringParams, rank_candidates
from app.domain.enums import OfferStatus, OrderStatus, label
from app.domain.geo import haversine_m
from app.domain.stats_calc import month_key
from app.models import (
    Caregiver,
    CaregiverExclusion,
    CaregiverSlot,
    DispatchOffer,
    DispatchRound,
    Organization,
    Review,
    ServiceOrder,
)
from app.models.org import community_orgs
from app.services import audit_service, config_service
from app.services import notification_service as notify
from app.services.access import Actor, ensure_order, scope_caregivers
from app.services.order_service import record_event, release_slots, transition

SLOT_MINUTES = 15


def slot_starts(start: datetime, end: datetime) -> list[datetime]:
    """把服务区间展开成 15 分钟时间片，起止都向外取整。"""
    floored = start.replace(minute=start.minute - start.minute % SLOT_MINUTES, second=0, microsecond=0)
    slots, current = [], floored
    while current < end:
        slots.append(current)
        current += timedelta(minutes=SLOT_MINUTES)
    return slots


def scoring_params(db: Session) -> ScoringParams:
    return ScoringParams(
        weights=config_service.get(db, "dispatch.weights"),
        max_distance_km=config_service.get(db, "dispatch.max_distance_km"),
        max_daily_orders=config_service.get(db, "dispatch.max_daily_orders"),
        min_skill_match=config_service.get(db, "dispatch.min_skill_match"),
        key_attention_factor=config_service.get(db, "dispatch.key_attention_factor"),
    )


def _build_candidates(db: Session, order: ServiceOrder, now: datetime) -> list[CandidateInput]:
    org_ids = select(community_orgs.c.org_id).where(community_orgs.c.community_id == order.community_id)
    caregivers = db.execute(select(Caregiver).join(Organization).where(
        Caregiver.is_active.is_(True), Caregiver.org_id.in_(org_ids), Organization.is_active.is_(True))).scalars().all()
    if not caregivers:
        return []
    ids = [c.id for c in caregivers]
    day_start = order.scheduled_start.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    # 当日负荷 = 已接单或已完成的工单 + 未响应的邀约
    assigned = dict(db.execute(
        select(ServiceOrder.caregiver_id, func.count())
        .where(ServiceOrder.caregiver_id.in_(ids), ServiceOrder.scheduled_start >= day_start,
               ServiceOrder.scheduled_start < day_end,
               ServiceOrder.status.in_((OrderStatus.DISPATCHED, OrderStatus.IN_SERVICE, OrderStatus.COMPLETED)))
        .group_by(ServiceOrder.caregiver_id)).all())
    offered = dict(db.execute(
        select(DispatchOffer.caregiver_id, func.count()).join(ServiceOrder, DispatchOffer.order_id == ServiceOrder.id)
        .where(DispatchOffer.status == OfferStatus.OFFERED, ServiceOrder.scheduled_start >= day_start,
               ServiceOrder.scheduled_start < day_end, ServiceOrder.id != order.id)
        .group_by(DispatchOffer.caregiver_id)).all())
    since = now - timedelta(days=30)
    ratings = {row[0]: (float(row[1]), int(row[2])) for row in db.execute(
        select(Review.caregiver_id, func.sum(Review.overall), func.count())
        .where(Review.caregiver_id.in_(ids), Review.created_at >= since).group_by(Review.caregiver_id)).all()}
    slots = slot_starts(order.scheduled_start, order.scheduled_end)
    busy = set(db.execute(select(CaregiverSlot.caregiver_id).where(
        CaregiverSlot.caregiver_id.in_(ids), CaregiverSlot.slot_start.in_(slots),
        CaregiverSlot.order_id != order.id)).scalars())
    # 投诉结论"更换护理员"之后，不再给这位老人派该护理员
    barred = set(db.execute(select(CaregiverExclusion.caregiver_id).where(
        CaregiverExclusion.elder_id == order.elder_id)).scalars())
    month = month_key(now)
    result = []
    for c in caregivers:
        if c.id in barred:
            continue
        rating_sum, rating_count = ratings.get(c.id, (0.0, 0))
        result.append(CandidateInput(
            caregiver_id=c.id, name=c.name, skills=frozenset(c.skills or []),
            distance_km=haversine_m(c.base_lat, c.base_lng, order.lat, order.lng) / 1000,
            daily_orders=assigned.get(c.id, 0) + offered.get(c.id, 0),
            rating_sum=rating_sum, rating_count=rating_count, has_conflict=c.id in busy,
            key_attention=c.key_attention_month == month, org_id=c.org_id, lat=c.base_lat, lng=c.base_lng,
        ))
    return result


def recommend(db: Session, actor: Actor, order_id: int) -> dict:
    order = ensure_order(db, actor, order_id)
    if order.status != OrderStatus.PENDING_DISPATCH:
        raise Conflict(f"工单当前为「{label('OrderStatus', order.status)}」，只有待派单工单可以推荐")
    return _create_round(db, order, actor.id)


def _create_round(db: Session, order: ServiceOrder, user_id: int | None) -> dict:
    now = clock.now()
    params = scoring_params(db)
    ranked, excluded = rank_candidates(
        _build_candidates(db, order, now), frozenset(order.item.required_skills or []), params)
    top_n = config_service.get(db, "dispatch.top_n")
    round_no = (db.execute(select(func.max(DispatchRound.round_no)).where(
        DispatchRound.order_id == order.id)).scalar() or 0) + 1
    snapshot = DispatchRound(
        order_id=order.id, round_no=round_no, candidates=ranked, excluded=excluded, created_by=user_id,
        params={"weights": params.weights, "max_distance_km": params.max_distance_km,
                "max_daily_orders": params.max_daily_orders, "min_skill_match": params.min_skill_match,
                "key_attention_factor": params.key_attention_factor},
    )
    db.add(snapshot)
    db.flush()
    return {
        "round_id": snapshot.id, "round_no": round_no, "weights": params.weights,
        "candidates": ranked[:top_n], "backup_count": max(len(ranked) - top_n, 0), "excluded": excluded,
        "order": {"lat": order.lat, "lng": order.lng, "required_skills": order.item.required_skills,
                  "max_distance_km": params.max_distance_km},
    }


def _lock_slots(db: Session, order: ServiceOrder, caregiver_id: int) -> None:
    """占用时段。唯一约束冲突说明有并发派单抢到同一时段，转成业务冲突返回。"""
    try:
        with db.begin_nested():
            for start in slot_starts(order.scheduled_start, order.scheduled_end):
                db.add(CaregiverSlot(caregiver_id=caregiver_id, slot_start=start, order_id=order.id))
            db.flush()
    except IntegrityError as exc:
        raise Conflict("该护理员在此时段已有在途工单，派单被拦截", code="SLOT_CONFLICT") from exc


def _make_offer(db: Session, order: ServiceOrder, caregiver: Caregiver, *, round_id: int | None, rank: int | None,
                score: float | None, offered_by: int | None, reassign_reason: str | None = None) -> DispatchOffer:
    _lock_slots(db, order, caregiver.id)
    now = clock.now()
    timeout = config_service.get(db, "dispatch.offer_timeout_minutes")
    offer = DispatchOffer(order_id=order.id, round_id=round_id, caregiver_id=caregiver.id, rank=rank, score=score,
                          offered_by=offered_by, offered_at=now, deadline_at=now + timedelta(minutes=timeout),
                          is_reassign=bool(reassign_reason), reassign_reason=reassign_reason)
    db.add(offer)
    db.flush()
    if caregiver.user_id:
        notify.in_app(
            db, caregiver.user_id, "新工单待接单",
            f"{order.item.name}，{order.scheduled_start:%m-%d %H:%M}，请在 {timeout} 分钟内接单",
            ref_type="offer", ref_id=offer.id, user_name=caregiver.name, role="caregiver", level="WARN",
        )
    return offer


def _active_offer(db: Session, order_id: int) -> DispatchOffer | None:
    return db.execute(select(DispatchOffer).where(
        DispatchOffer.order_id == order_id, DispatchOffer.status == OfferStatus.OFFERED)).scalar()


def assign(db: Session, actor: Actor, order_id: int, caregiver_id: int | None = None,
           round_id: int | None = None) -> dict:
    """网格员派单。不传 caregiver_id 即一键派给第 1 名。"""
    order = ensure_order(db, actor, order_id)
    if order.status != OrderStatus.PENDING_DISPATCH:
        raise Conflict(f"工单当前为「{label('OrderStatus', order.status)}」，不能派单")
    if _active_offer(db, order.id):
        raise Conflict("已有待响应的邀约，如需更换请使用改派")
    snapshot = db.get(DispatchRound, round_id) if round_id else None
    if snapshot is None or snapshot.order_id != order.id:
        _create_round(db, order, actor.id)
        snapshot = db.execute(select(DispatchRound).where(DispatchRound.order_id == order.id)
                              .order_by(DispatchRound.id.desc()).limit(1)).scalar()
    candidates = snapshot.candidates or []
    if caregiver_id is None:
        if not candidates:
            order.manual_coordination = True
            raise Conflict("没有符合条件的护理员，已转人工协调", code="NO_CANDIDATE")
        chosen = candidates[0]
    else:
        chosen = next((c for c in candidates if c["caregiver_id"] == caregiver_id), None)
        if chosen is None:
            raise Conflict("该护理员不在本次推荐的合格候选内，如确需指派请使用改派并填写原因")
    caregiver = db.get(Caregiver, chosen["caregiver_id"])
    offer = _make_offer(db, order, caregiver, round_id=snapshot.id, rank=chosen["rank"], score=chosen["score"],
                        offered_by=actor.id)
    order.manual_coordination = False
    record_event(db, order, "OFFER", actor,
                 f"派给 {caregiver.name}（第 {chosen['rank']} 名，推荐分 {chosen['score']:.3f}），等待接单")
    return {"offer_id": offer.id, "caregiver_name": caregiver.name, "deadline_at": offer.deadline_at}


def reassign(db: Session, actor: Actor, order_id: int, caregiver_id: int, reason: str) -> dict:
    """手工改派：强制填写原因并审计。原护理员的时段立刻释放。"""
    if not reason or not reason.strip():
        raise ValidationFailed("改派必须填写原因")
    order = ensure_order(db, actor, order_id)
    if order.status not in (OrderStatus.PENDING_DISPATCH, OrderStatus.DISPATCHED):
        raise Conflict(f"工单当前为「{label('OrderStatus', order.status)}」，不能改派")
    target = db.execute(scope_caregivers(actor, select(Caregiver).where(
        Caregiver.id == caregiver_id, Caregiver.is_active.is_(True)))).scalar()
    if target is None:
        raise NotFound("护理员不存在或不在您的数据范围内")
    if target.id == order.caregiver_id:
        raise Conflict("新护理员与当前护理员相同")
    previous_name = order.caregiver.name if order.caregiver else None
    previous_user = order.caregiver.user_id if order.caregiver else None
    pending = _active_offer(db, order.id)
    if pending:
        previous_name = pending.caregiver.name
        previous_user = pending.caregiver.user_id
        pending.status = OfferStatus.REVOKED
        pending.responded_at = clock.now()
    release_slots(db, order.id)
    db.flush()
    if order.status == OrderStatus.DISPATCHED:
        transition(db, order, OrderStatus.PENDING_DISPATCH, "REASSIGN", actor, f"改派：{reason}")
        order.caregiver_id = None
        order.org_id = None
    offer = _make_offer(db, order, target, round_id=None, rank=None, score=None, offered_by=actor.id,
                        reassign_reason=reason)
    record_event(db, order, "REASSIGN_OFFER", actor, f"由 {previous_name or '无'} 改派给 {target.name}：{reason}")
    if previous_user:
        notify.in_app(db, previous_user, "工单已改派", f"{order.order_no} 已改派：{reason}", ref_type="order", ref_id=order.id)
    audit_service.write(db, actor.ctx, "ORDER_REASSIGN", "order", order.id,
                        {"from": previous_name, "to": target.name, "reason": reason})
    return {"offer_id": offer.id, "caregiver_name": target.name, "deadline_at": offer.deadline_at}


def _ensure_my_offer(db: Session, actor: Actor, offer_id: int) -> DispatchOffer:
    offer = db.get(DispatchOffer, offer_id)
    if offer is None:
        raise NotFound("邀约不存在")
    if actor.caregiver_id is None or offer.caregiver_id != actor.caregiver_id:
        raise Forbidden("只能响应派给自己的工单")
    if offer.status != OfferStatus.OFFERED:
        raise Conflict(f"该邀约已{label('OfferStatus', offer.status)}，不能再响应")
    return offer


def accept_offer(db: Session, actor: Actor, offer_id: int) -> ServiceOrder:
    offer = _ensure_my_offer(db, actor, offer_id)
    now = clock.now()
    if now > offer.deadline_at:
        raise Conflict("已超过接单时限，工单已顺延给下一位候选")
    order = offer.order
    offer.status = OfferStatus.ACCEPTED
    offer.responded_at = now
    caregiver = offer.caregiver
    caregiver.consecutive_rejects = 0
    order.caregiver_id = caregiver.id
    order.org_id = caregiver.org_id
    order.manual_coordination = False
    transition(db, order, OrderStatus.DISPATCHED, "OFFER_ACCEPT", actor, f"{caregiver.name} 已接单，时段已锁定")
    text = f"{order.item.name} {order.scheduled_start:%m-%d %H:%M}，护理员 {crypto.mask_name(caregiver.name)}"
    notify.notify_family(db, order.elder_id, "派单成功", text, ref_type="order", ref_id=order.id)
    notify.notify_grid(db, order.grid_id, "派单成功", f"{order.order_no} {text}", ref_type="order", ref_id=order.id)
    notify.in_app(db, actor.id, "接单成功", text, ref_type="order", ref_id=order.id, user_name=caregiver.name, role="caregiver")
    return order


def reject_offer(db: Session, actor: Actor, offer_id: int, reason: str) -> ServiceOrder:
    if not reason or not reason.strip():
        raise ValidationFailed("拒单必须填写理由")
    offer = _ensure_my_offer(db, actor, offer_id)
    offer.status = OfferStatus.REJECTED
    offer.responded_at = clock.now()
    offer.reject_reason = reason
    caregiver = offer.caregiver
    caregiver.consecutive_rejects += 1
    order = offer.order
    record_event(db, order, "OFFER_REJECT", actor, f"{caregiver.name} 拒单：{reason}")
    release_slots(db, order.id)
    db.flush()
    threshold = config_service.get(db, "dispatch.reject_alert_threshold")
    if caregiver.consecutive_rejects >= threshold:
        text = f"护理员 {caregiver.name} 已连续拒单 {caregiver.consecutive_rejects} 次，请关注"
        notify.notify_grid(db, order.grid_id, "连续拒单提醒", text, level="WARN", ref_type="caregiver", ref_id=caregiver.id)
        notify.notify_org_admins(db, caregiver.org_id, "连续拒单提醒", text, level="WARN", ref_type="caregiver", ref_id=caregiver.id)
    _advance_to_next(db, order, f"{caregiver.name} 拒单")
    return order


def _advance_to_next(db: Session, order: ServiceOrder, why: str) -> DispatchOffer | None:
    """顺延到本轮的下一位候选。全部失败转人工协调。

    顺延时可能出现新的时段占用，所以每位候选都重新尝试加锁，冲突则跳过。
    """
    snapshot = db.execute(select(DispatchRound).where(DispatchRound.order_id == order.id)
                          .order_by(DispatchRound.id.desc()).limit(1)).scalar()
    tried = set(db.execute(select(DispatchOffer.caregiver_id).where(DispatchOffer.order_id == order.id)).scalars())
    for candidate in (snapshot.candidates if snapshot else []):
        if candidate["caregiver_id"] in tried:
            continue
        caregiver = db.get(Caregiver, candidate["caregiver_id"])
        if caregiver is None or not caregiver.is_active:
            continue
        try:
            offer = _make_offer(db, order, caregiver, round_id=snapshot.id, rank=candidate["rank"],
                                score=candidate["score"], offered_by=None)
        except Conflict:
            continue
        record_event(db, order, "OFFER_NEXT", None, f"{why}，自动顺延至第 {candidate['rank']} 名 {caregiver.name}")
        notify.notify_grid(db, order.grid_id, "派单已顺延", f"{order.order_no}：{why}，已顺延至 {caregiver.name}",
                           ref_type="order", ref_id=order.id, level="WARN")
        return offer
    order.manual_coordination = True
    record_event(db, order, "MANUAL_COORD", None, f"{why}，候选已用尽，转人工协调")
    notify.notify_grid(db, order.grid_id, "派单失败，需人工协调", f"{order.order_no}：候选已用尽，请人工协调",
                       ref_type="order", ref_id=order.id, level="ERROR")
    return None


def expire_offers(db: Session, now: datetime | None = None) -> int:
    """定时任务：超过接单时限的邀约标记超时并顺延。"""
    now = now or clock.now()
    rows = db.execute(select(DispatchOffer).where(
        DispatchOffer.status == OfferStatus.OFFERED, DispatchOffer.deadline_at <= now)).scalars().all()
    for offer in rows:
        offer.status = OfferStatus.TIMEOUT
        offer.responded_at = now
        order = offer.order
        name = offer.caregiver.name
        record_event(db, order, "OFFER_TIMEOUT", None, f"{name} 超时未接单")
        release_slots(db, order.id)
        db.flush()
        _advance_to_next(db, order, f"{name} 超时未接单")
    return len(rows)


def dispatch_history(db: Session, actor: Actor, order_id: int) -> dict:
    ensure_order(db, actor, order_id)
    rounds = db.execute(select(DispatchRound).where(DispatchRound.order_id == order_id)
                        .order_by(DispatchRound.id.desc())).scalars().all()
    offers = db.execute(select(DispatchOffer).where(DispatchOffer.order_id == order_id)
                        .order_by(DispatchOffer.id)).scalars().all()
    return {
        "rounds": [{"id": r.id, "round_no": r.round_no, "params": r.params, "candidates": r.candidates,
                    "excluded": r.excluded, "created_at": r.created_at} for r in rounds],
        "offers": [{
            "id": o.id, "caregiver_id": o.caregiver_id, "caregiver_name": o.caregiver.name, "rank": o.rank,
            "score": o.score, "status": o.status, "status_label": label("OfferStatus", o.status),
            "offered_at": o.offered_at, "deadline_at": o.deadline_at, "responded_at": o.responded_at,
            "reject_reason": o.reject_reason, "is_reassign": o.is_reassign, "reassign_reason": o.reassign_reason,
        } for o in offers],
    }


def my_offers(db: Session, actor: Actor) -> list[dict]:
    if actor.caregiver_id is None:
        raise Forbidden("仅护理员可查看")
    rows = db.execute(select(DispatchOffer).where(
        DispatchOffer.caregiver_id == actor.caregiver_id, DispatchOffer.status == OfferStatus.OFFERED)
        .order_by(DispatchOffer.deadline_at)).scalars().all()
    result = []
    for offer in rows:
        order = offer.order
        result.append({
            "id": offer.id, "order_id": order.id, "order_no": order.order_no, "item_name": order.item.name,
            "scheduled_start": order.scheduled_start, "scheduled_end": order.scheduled_end,
            "elder_name": crypto.mask_name(order.elder.name), "address": crypto.mask_address(order.address),
            "deadline_at": offer.deadline_at, "remark": order.remark,
            "disability_label": label("DisabilityLevel", order.elder.disability_level),
            "is_reassign": offer.is_reassign,
        })
    return result
