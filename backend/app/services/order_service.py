"""预约受理：校验、状态流转、取消计费规则、周期预约。"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock, crypto
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.domain.enums import OfferStatus, OrderSource, OrderStatus, label
from app.domain.geo import haversine_m
from app.domain.state_machine import ORDER_FLOW
from app.models import (
    CaregiverSlot,
    Community,
    DispatchOffer,
    Elder,
    Grid,
    OrderEvent,
    RecurringPlan,
    ServiceItem,
    ServiceOrder,
)
from app.services import billing_service, catalog_service, config_service
from app.services import notification_service as notify
from app.services.access import Actor, ensure_elder, ensure_order, scope_orders

# 这些状态下工单还占用老人的时段，参与重复预约检测
ACTIVE_STATUSES = (
    OrderStatus.PENDING_ACCEPT, OrderStatus.ACCEPTED, OrderStatus.PENDING_DISPATCH,
    OrderStatus.DISPATCHED, OrderStatus.IN_SERVICE,
)
_LEVEL_PRIORITY = {"SEVERE": 40, "MODERATE": 30, "MILD": 10, "INTACT": 0}
_TAG_PRIORITY = {"独居": 20, "失智": 15, "卧床": 15, "低保": 5, "特困": 10, "高龄": 5}


def compute_priority(elder: Elder) -> int:
    """派单优先级：把有限人力优先投向失能等级高、独居等高风险老人。"""
    return _LEVEL_PRIORITY.get(elder.disability_level, 0) + sum(
        _TAG_PRIORITY.get(tag, 0) for tag in (elder.care_tags or []))


def record_event(db: Session, order: ServiceOrder, action: str, actor: Actor | None = None, note: str | None = None,
                 from_status: str | None = None, to_status: str | None = None) -> OrderEvent:
    event = OrderEvent(order_id=order.id, action=action, from_status=from_status, to_status=to_status,
                       actor_id=actor.id if actor else None, actor_name=actor.name if actor else "系统",
                       note=note, at=clock.now())
    db.add(event)
    return event


def transition(db: Session, order: ServiceOrder, target: OrderStatus, action: str, actor: Actor | None = None,
               note: str | None = None) -> None:
    """唯一的状态变更入口：先过状态机，再写事件，不留绕过校验的路径。"""
    ORDER_FLOW.check(order.status, target)
    previous = order.status
    order.status = target
    record_event(db, order, action, actor, note, previous, target)


def release_slots(db: Session, order_id: int) -> None:
    for slot in db.execute(select(CaregiverSlot).where(CaregiverSlot.order_id == order_id)).scalars():
        db.delete(slot)


def validate_booking(db: Session, elder: Elder, item: ServiceItem, start: datetime, end: datetime,
                     lat: float, lng: float, *, exclude_order_id: int | None = None) -> list[dict]:
    """三项校验。不满足时不拒绝预约，而是给出明确提示并转人工受理。"""
    issues: list[dict] = []
    hours = config_service.get(db, "order.duplicate_window_hours")
    window = timedelta(hours=hours)
    stmt = select(ServiceOrder).where(
        ServiceOrder.elder_id == elder.id, ServiceOrder.status.in_(ACTIVE_STATUSES),
        ServiceOrder.scheduled_start < end + window, ServiceOrder.scheduled_end > start - window,
    )
    if exclude_order_id:
        stmt = stmt.where(ServiceOrder.id != exclude_order_id)
    conflict = db.execute(stmt.limit(1)).scalar()
    if conflict:
        issues.append({
            "code": "DUPLICATE_TIME",
            "message": f"该老人在 {conflict.scheduled_start:%m-%d %H:%M} 已有工单 {conflict.order_no}，"
                       f"前后 {hours:g} 小时内不能重复预约",
        })
    community = db.get(Community, db.get(Grid, elder.grid_id).community_id)
    distance = haversine_m(lat, lng, community.center_lat, community.center_lng)
    if distance > community.service_radius_m:
        issues.append({
            "code": "OUT_OF_RANGE",
            "message": f"服务地址距{community.name}服务中心 {distance / 1000:.2f} 公里，"
                       f"超出 {community.service_radius_m / 1000:g} 公里服务范围",
        })
    if item.applicable_levels and elder.disability_level not in item.applicable_levels:
        allowed = "、".join(label("DisabilityLevel", lv) for lv in item.applicable_levels)
        issues.append({
            "code": "LEVEL_MISMATCH",
            "message": f"「{item.name}」适用于{allowed}，该老人为{label('DisabilityLevel', elder.disability_level)}",
        })
    return issues


def _next_order_no(db: Session) -> str:
    today = clock.now().strftime("%Y%m%d")
    last = db.execute(select(func.max(ServiceOrder.id))).scalar() or 0
    return f"SO{today}{last + 1:06d}"


def _build_order(db: Session, elder: Elder, item: ServiceItem, start: datetime, *, address: str | None = None,
                 lat: float | None = None, lng: float | None = None, remark: str | None = None,
                 source: str = OrderSource.ONLINE, created_by: int | None = None,
                 plan_id: int | None = None) -> ServiceOrder:
    if not item.is_on_shelf:
        raise ValidationFailed(f"服务项目「{item.name}」已下架，不能预约")
    # 按服务发生时点绑定价格版本，之后调价不影响这张工单
    version = catalog_service.price_version_at(db, item.id, start)
    if version is None:
        raise ValidationFailed(f"服务项目「{item.name}」在 {start:%Y-%m-%d %H:%M} 没有生效价格")
    end = start + timedelta(minutes=item.standard_minutes)
    target_lat = elder.lat if lat is None else lat
    target_lng = elder.lng if lng is None else lng
    issues = validate_booking(db, elder, item, start, end, target_lat, target_lng)
    grid = db.get(Grid, elder.grid_id)
    order = ServiceOrder(
        order_no=_next_order_no(db), elder_id=elder.id, item_id=item.id, grid_id=elder.grid_id,
        community_id=grid.community_id, price_version_id=version.id, scheduled_start=start, scheduled_end=end,
        address=address or elder.address, lat=target_lat, lng=target_lng, remark=remark, source=source,
        priority=compute_priority(elder), needs_manual=bool(issues), validation_issues=issues,
        created_by=created_by, plan_id=plan_id, status=OrderStatus.PENDING_ACCEPT,
    )
    db.add(order)
    db.flush()
    return order


def _notify_new_order(db: Session, order: ServiceOrder, elder: Elder, item: ServiceItem) -> None:
    prefix = "【需人工受理】" if order.needs_manual else ""
    notify.notify_grid(
        db, order.grid_id, f"{prefix}新预约待受理",
        f"{crypto.mask_name(elder.name)} 预约 {item.name}，{order.scheduled_start:%m-%d %H:%M}",
        ref_type="order", ref_id=order.id, level="WARN" if order.needs_manual else "INFO",
    )


def create_order(db: Session, actor: Actor, elder_id: int, item_id: int, scheduled_start: datetime, *,
                 address: str | None = None, lat: float | None = None, lng: float | None = None,
                 remark: str | None = None, source: str = OrderSource.ONLINE) -> ServiceOrder:
    elder = ensure_elder(db, actor, elder_id)
    item = db.get(ServiceItem, item_id)
    if item is None:
        raise NotFound("服务项目不存在")
    if scheduled_start <= clock.now():
        raise ValidationFailed("期望上门时间必须晚于当前时间")
    if bool(lat is None) != bool(lng is None):
        raise ValidationFailed("服务地址的经纬度必须同时提供")
    order = _build_order(db, elder, item, scheduled_start, address=address, lat=lat, lng=lng, remark=remark,
                         source=source, created_by=actor.id)
    note = "提交预约"
    if order.needs_manual:
        note += "；校验未通过，转人工受理：" + "；".join(i["message"] for i in order.validation_issues)
    record_event(db, order, "CREATE", actor, note, None, OrderStatus.PENDING_ACCEPT)
    _notify_new_order(db, order, elder, item)
    return order


def accept_order(db: Session, actor: Actor, order_id: int, note: str | None = None) -> ServiceOrder:
    """网格员受理：待受理 → 已受理 → 待派单。人工单必须写明受理依据。"""
    order = ensure_order(db, actor, order_id)
    if order.needs_manual and not (note and note.strip()):
        raise ValidationFailed("该预约校验未通过，人工受理必须填写受理依据", details=order.validation_issues)
    transition(db, order, OrderStatus.ACCEPTED, "ACCEPT", actor, note or "网格员确认受理")
    transition(db, order, OrderStatus.PENDING_DISPATCH, "QUEUE_DISPATCH", actor, "进入待派单队列")
    return order


def _cancel_chargeable(db: Session, order: ServiceOrder, now: datetime) -> tuple[bool, str]:
    """迟取消规则：开始前不足 2 小时取消，且护理员已接单（资源已占用）才计费。正好 2 小时不计费。"""
    free_hours = config_service.get(db, "order.free_cancel_hours")
    remaining = order.scheduled_start - now
    if remaining >= timedelta(hours=free_hours):
        return False, f"服务开始前 {free_hours:g} 小时以上取消，不计费"
    if order.status not in (OrderStatus.DISPATCHED, OrderStatus.IN_SERVICE):
        return False, "尚未派单成功，不计费"
    return True, f"服务开始前不足 {free_hours:g} 小时取消，按规则计费"


def preview_cancel(db: Session, actor: Actor, order_id: int) -> dict:
    order = ensure_order(db, actor, order_id)
    chargeable, explanation = _cancel_chargeable(db, order, clock.now())
    fee = billing_service.cancel_fee_amount(db, order) if chargeable else 0
    return {"chargeable": chargeable, "explanation": explanation, "fee": fee}


def cancel_order(db: Session, actor: Actor, order_id: int, reason: str) -> ServiceOrder:
    if not reason or not reason.strip():
        raise ValidationFailed("取消预约必须填写原因")
    order = ensure_order(db, actor, order_id)
    if order.status == OrderStatus.IN_SERVICE:
        raise Conflict("服务进行中不能直接取消，请由护理员上报异常后人工处置")
    now = clock.now()
    chargeable, explanation = _cancel_chargeable(db, order, now)
    transition(db, order, OrderStatus.CANCELLED, "CANCEL", actor, f"{reason}（{explanation}）")
    order.cancel_reason = reason
    order.cancel_chargeable = chargeable
    order.cancelled_at = now
    _close_dispatch_state(db, order)
    if chargeable:
        billing_service.record_cancel_fee(db, order)
    if order.caregiver_id and order.caregiver and order.caregiver.user_id:
        notify.in_app(db, order.caregiver.user_id, "工单已取消", f"{order.order_no} 已取消：{reason}",
                      ref_type="order", ref_id=order.id, user_name=order.caregiver.name, role="caregiver")
    notify.notify_grid(db, order.grid_id, "预约已取消", f"{order.order_no} 已取消：{reason}", ref_type="order", ref_id=order.id)
    return order


def _close_dispatch_state(db: Session, order: ServiceOrder) -> None:
    release_slots(db, order.id)
    offers = db.execute(select(DispatchOffer).where(
        DispatchOffer.order_id == order.id, DispatchOffer.status == OfferStatus.OFFERED)).scalars()
    for offer in offers:
        offer.status = OfferStatus.REVOKED
        offer.responded_at = clock.now()


def to_view(order: ServiceOrder) -> dict:
    elder = order.elder
    return {
        "id": order.id, "order_no": order.order_no, "elder_id": order.elder_id, "elder_name": elder.name,
        "elder_code": elder.code, "disability_level": elder.disability_level,
        "disability_label": label("DisabilityLevel", elder.disability_level), "care_tags": elder.care_tags or [],
        "item_id": order.item_id, "item_name": order.item.name, "grid_id": order.grid_id,
        "grid_name": elder.grid.name if elder.grid else None, "community_id": order.community_id,
        "scheduled_start": order.scheduled_start, "scheduled_end": order.scheduled_end,
        "address": crypto.mask_address(order.address), "lat": order.lat, "lng": order.lng, "remark": order.remark,
        "status": order.status, "status_label": label("OrderStatus", order.status),
        "source": order.source, "source_label": label("OrderSource", order.source), "priority": order.priority,
        "needs_manual": order.needs_manual, "validation_issues": order.validation_issues or [],
        "manual_coordination": order.manual_coordination, "caregiver_id": order.caregiver_id,
        "caregiver_name": order.caregiver.name if order.caregiver else None, "org_id": order.org_id,
        "cancel_reason": order.cancel_reason, "cancel_chargeable": order.cancel_chargeable,
        "unit_price": order.price_version.unit_price, "price_version": order.price_version.version_no,
        "pricing_mode": order.item.pricing_mode, "standard_minutes": order.item.standard_minutes,
        "payment_status": order.payment_status, "payment_label": label("PaymentStatus", order.payment_status),
        "completed_at": order.completed_at, "created_at": order.created_at,
    }


def list_orders(db: Session, actor: Actor, *, status: str | None = None, statuses: list[str] | None = None,
                elder_id: int | None = None, needs_manual: bool | None = None, date_from: datetime | None = None,
                date_to: datetime | None = None, keyword: str | None = None, caregiver_id: int | None = None,
                page: int = 1, size: int = 20, sort: str = "time") -> dict:
    stmt = scope_orders(actor, select(ServiceOrder))
    if status:
        stmt = stmt.where(ServiceOrder.status == status)
    if statuses:
        stmt = stmt.where(ServiceOrder.status.in_(statuses))
    if elder_id:
        stmt = stmt.where(ServiceOrder.elder_id == elder_id)
    if needs_manual is not None:
        stmt = stmt.where(ServiceOrder.needs_manual.is_(needs_manual))
    if date_from:
        stmt = stmt.where(ServiceOrder.scheduled_start >= date_from)
    if date_to:
        stmt = stmt.where(ServiceOrder.scheduled_start < date_to)
    if caregiver_id:
        stmt = stmt.where(ServiceOrder.caregiver_id == caregiver_id)
    if keyword:
        stmt = stmt.where(ServiceOrder.order_no.contains(keyword) | ServiceOrder.elder.has(Elder.name.contains(keyword)))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    if sort == "priority":
        # 优先级高的排前面，同级按预约时间先后
        stmt = stmt.order_by(ServiceOrder.priority.desc(), ServiceOrder.scheduled_start)
    else:
        stmt = stmt.order_by(ServiceOrder.scheduled_start.desc())
    rows = db.execute(stmt.limit(size).offset((page - 1) * size)).scalars().all()
    return {"total": total, "page": page, "size": size, "items": [to_view(o) for o in rows]}


def order_detail(db: Session, actor: Actor, order_id: int) -> dict:
    order = ensure_order(db, actor, order_id)
    view = to_view(order)
    # 护理员上门必须看到完整地址（最小必要）；其他角色默认脱敏
    if actor.caregiver_id and order.caregiver_id == actor.caregiver_id:
        view["address"] = order.address
    view["timeline"] = [{
        "id": e.id, "action": e.action, "from": e.from_status, "to": e.to_status,
        "from_label": label("OrderStatus", e.from_status), "to_label": label("OrderStatus", e.to_status),
        "actor": e.actor_name, "note": e.note, "at": e.at,
    } for e in order.events]
    execution = order.execution
    if execution:
        view["execution"] = {
            "checkin_at": execution.checkin_at, "checkin_distance_m": round(execution.checkin_distance_m, 1),
            "checkin_note": execution.checkin_note, "is_on_time": execution.is_on_time,
            "checklist": execution.checklist, "checkout_at": execution.checkout_at,
            "duration_minutes": execution.duration_minutes, "duration_abnormal": execution.duration_abnormal,
            "summary": execution.summary, "spot_check_status": execution.spot_check_status,
            "spot_check_label": label("SpotCheckStatus", execution.spot_check_status),
            "spot_check_note": execution.spot_check_note, "offline_synced": execution.offline_synced,
            "photos": [{"id": p.id, "size_bytes": p.size_bytes, "original_bytes": p.original_bytes,
                        "width": p.width, "height": p.height, "uploaded_at": p.uploaded_at} for p in execution.photos],
        }
    else:
        view["execution"] = None
    return view


# ---------- 周期预约 ----------

def create_plan(db: Session, actor: Actor, elder_id: int, item_id: int, weekday: int, time_of_day: str,
                start_date, end_date=None, remark: str | None = None) -> RecurringPlan:
    elder = ensure_elder(db, actor, elder_id)
    item = db.get(ServiceItem, item_id)
    if item is None or not item.is_on_shelf:
        raise ValidationFailed("服务项目不存在或已下架")
    if not 0 <= weekday <= 6:
        raise ValidationFailed("星期取值必须在 0 到 6 之间")
    try:
        hour, minute = (int(part) for part in time_of_day.split(":"))
        time(hour, minute)
    except ValueError as exc:
        raise ValidationFailed("时间格式应为 HH:MM") from exc
    if end_date and end_date < start_date:
        raise ValidationFailed("结束日期不能早于开始日期")
    plan = RecurringPlan(elder_id=elder.id, item_id=item.id, weekday=weekday, time_of_day=time_of_day,
                         start_date=start_date, end_date=end_date, remark=remark, created_by=actor.id)
    db.add(plan)
    db.flush()
    generate_recurring_orders(db, clock.now(), plan_id=plan.id)
    return plan


def list_plans(db: Session, actor: Actor, elder_id: int | None = None) -> list[dict]:
    stmt = select(RecurringPlan).where(RecurringPlan.is_active.is_(True))
    if elder_id:
        ensure_elder(db, actor, elder_id)
        stmt = stmt.where(RecurringPlan.elder_id == elder_id)
    else:
        from app.services.access import scope_elders
        stmt = stmt.where(RecurringPlan.elder_id.in_(scope_elders(actor, select(Elder.id))))
    names = "一二三四五六日"
    return [{
        "id": p.id, "elder_id": p.elder_id, "elder_name": p.elder.name, "item_name": p.item.name, "weekday": p.weekday,
        "weekday_label": f"每周{names[p.weekday]}", "time_of_day": p.time_of_day, "start_date": p.start_date,
        "end_date": p.end_date, "remark": p.remark,
    } for p in db.execute(stmt).scalars()]


def stop_plan(db: Session, actor: Actor, plan_id: int) -> None:
    plan = db.get(RecurringPlan, plan_id)
    if plan is None:
        raise NotFound("周期预约不存在")
    ensure_elder(db, actor, plan.elder_id)
    plan.is_active = False


def generate_recurring_orders(db: Session, now: datetime, plan_id: int | None = None, days: int = 7) -> int:
    """滚动生成未来 N 天的周期工单。靠唯一约束保证幂等，任务重跑不会重复生成。"""
    stmt = select(RecurringPlan).where(RecurringPlan.is_active.is_(True))
    if plan_id:
        stmt = stmt.where(RecurringPlan.id == plan_id)
    created = 0
    for plan in db.execute(stmt).scalars():
        hour, minute = (int(part) for part in plan.time_of_day.split(":"))
        for offset in range(days + 1):
            day = now.date() + timedelta(days=offset)
            if day.weekday() != plan.weekday or day < plan.start_date or (plan.end_date and day > plan.end_date):
                continue
            start = datetime(day.year, day.month, day.day, hour, minute)
            already = db.execute(select(exists().where(
                ServiceOrder.plan_id == plan.id, ServiceOrder.scheduled_start == start))).scalar()
            if start <= now or already:
                continue
            try:
                with db.begin_nested():
                    order = _build_order(db, plan.elder, plan.item, start, remark=plan.remark,
                                         source=OrderSource.RECURRING, created_by=plan.created_by, plan_id=plan.id)
                    record_event(db, order, "CREATE", None, f"周期预约自动生成（计划 #{plan.id}）",
                                 None, OrderStatus.PENDING_ACCEPT)
                    _notify_new_order(db, order, plan.elder, plan.item)
                created += 1
            except (ValidationFailed, IntegrityError):
                continue
    return created
