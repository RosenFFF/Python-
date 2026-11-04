"""上门服务执行：签到、清单、照片、签退、异常上报与处置、时长抽查。"""
from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock, crypto
from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.domain.enums import (
    ExceptionStatus,
    ExceptionType,
    HealthSource,
    OrderStatus,
    SpotCheckStatus,
    label,
)
from app.domain.geo import haversine_m
from app.domain.state_machine import EXCEPTION_FLOW
from app.models import OrderEvent, ServiceException, ServiceExecution, ServiceOrder, ServicePhoto
from app.services import billing_service, config_service, health_service
from app.services import notification_service as notify
from app.services.access import Actor, ensure_order, scope_exceptions
from app.services.order_service import record_event, release_slots, transition

MAX_PHOTO_EDGE = 1280
JPEG_QUALITY = 75
MAX_PIXELS = 40_000_000


def _ensure_my_order(db: Session, actor: Actor, order_id: int) -> ServiceOrder:
    order = ensure_order(db, actor, order_id)
    if actor.caregiver_id is None or order.caregiver_id != actor.caregiver_id:
        raise Forbidden("只有承接本工单的护理员可以操作")
    return order


def check_in(db: Session, actor: Actor, order_id: int, lat: float, lng: float, note: str | None = None,
             occurred_at: datetime | None = None, offline: bool = False) -> ServiceExecution:
    order = _ensure_my_order(db, actor, order_id)
    if order.status != OrderStatus.DISPATCHED:
        raise Conflict(f"工单当前为「{label('OrderStatus', order.status)}」，不能签到")
    at = occurred_at or clock.now()
    distance = haversine_m(lat, lng, order.lat, order.lng)
    limit = config_service.get(db, "exec.checkin_distance_m")
    if distance > limit and not (note and note.strip()):
        raise ValidationFailed(
            f"定位距服务地址 {distance:.0f} 米，超过 {limit:g} 米阈值，请填写说明后继续",
            code="CHECKIN_TOO_FAR", details={"distance_m": round(distance, 1), "limit_m": limit},
        )
    tolerance = config_service.get(db, "exec.late_tolerance_minutes")
    execution = ServiceExecution(
        order_id=order.id, caregiver_id=actor.caregiver_id, checkin_at=at, checkin_lat=lat, checkin_lng=lng,
        checkin_distance_m=distance, checkin_note=note, offline_synced=offline,
        is_on_time=at <= order.scheduled_start + timedelta(minutes=tolerance),
        checklist=[{"name": name, "done": False} for name in (order.item.checklist or [])],
    )
    db.add(execution)
    transition(db, order, OrderStatus.IN_SERVICE, "CHECK_IN", actor,
               f"签到，距服务地址 {distance:.0f} 米" + (f"；说明：{note}" if note else ""))
    # 下一次派单的距离按最新位置计算
    order.caregiver.base_lat, order.caregiver.base_lng = lat, lng
    notify.notify_family(db, order.elder_id, "护理员已到达",
                         f"{crypto.mask_name(order.caregiver.name)} 已到达，开始{order.item.name}服务",
                         ref_type="order", ref_id=order.id)
    db.flush()
    return execution


def update_checklist(db: Session, actor: Actor, order_id: int, items: list[dict]) -> ServiceExecution:
    order = _ensure_my_order(db, actor, order_id)
    if order.status != OrderStatus.IN_SERVICE:
        raise Conflict("只有服务中的工单可以勾选清单")
    execution = order.execution
    done = {i["name"]: bool(i["done"]) for i in items}
    unknown = set(done) - {i["name"] for i in execution.checklist}
    if unknown:
        raise ValidationFailed(f"清单中不存在这些条目：{sorted(unknown)}")
    execution.checklist = [{"name": i["name"], "done": done.get(i["name"], i["done"])} for i in execution.checklist]
    return execution


def _compress_image(content: bytes) -> tuple[bytes, int, int]:
    """压缩并规范化：等比缩到长边 1280、转 JPEG。重编码同时清除 EXIF（含 GPS）和图片里夹带的数据。"""
    try:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        image = Image.open(io.BytesIO(content))
        if image.format not in ("JPEG", "PNG", "WEBP"):
            raise ValidationFailed("仅支持 JPEG、PNG、WEBP 图片")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((MAX_PHOTO_EDGE, MAX_PHOTO_EDGE))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValidationFailed("文件不是有效图片") from exc
    output = io.BytesIO()
    image.save(output, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return output.getvalue(), image.width, image.height


def upload_photo(db: Session, actor: Actor, order_id: int, content: bytes) -> ServicePhoto:
    order = _ensure_my_order(db, actor, order_id)
    if order.status != OrderStatus.IN_SERVICE or order.execution is None:
        raise Conflict("只有服务中的工单可以上传照片")
    settings = get_settings()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise ValidationFailed(f"单张图片不能超过 {settings.max_upload_mb} MB")
    execution = order.execution
    limit = config_service.get(db, "exec.max_photos")
    count = db.execute(select(func.count()).select_from(ServicePhoto)
                       .where(ServicePhoto.execution_id == execution.id)).scalar()
    if count >= limit:
        raise Conflict(f"现场照片不能超过 {limit} 张")
    compressed, width, height = _compress_image(content)
    now = clock.now()
    relative = Path(f"{now:%Y/%m}") / f"{uuid.uuid4().hex}.jpg"
    target = settings.upload_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(compressed)
    photo = ServicePhoto(execution_id=execution.id, file_name=relative.as_posix(), size_bytes=len(compressed),
                         original_bytes=len(content), width=width, height=height,
                         sha256=hashlib.sha256(compressed).hexdigest(), uploaded_at=now)
    db.add(photo)
    db.flush()
    return photo


def read_photo(db: Session, actor: Actor, photo_id: int) -> Path:
    photo = db.get(ServicePhoto, photo_id)
    if photo is None:
        raise NotFound("照片不存在")
    ensure_order(db, actor, photo.execution.order_id)
    path = (get_settings().upload_dir / photo.file_name).resolve()
    if not path.is_relative_to(get_settings().upload_dir.resolve()) or not path.exists():
        raise NotFound("照片文件不存在")
    return path


def check_out(db: Session, actor: Actor, order_id: int, summary: str, metrics: list[dict] | None = None,
              incomplete_reason: str | None = None, occurred_at: datetime | None = None) -> dict:
    order = _ensure_my_order(db, actor, order_id)
    if order.status != OrderStatus.IN_SERVICE or order.execution is None:
        raise Conflict("只有服务中的工单可以签退")
    if not summary or not summary.strip():
        raise ValidationFailed("签退必须填写服务小结")
    execution = order.execution
    undone = [i["name"] for i in execution.checklist if not i["done"]]
    if undone and not (incomplete_reason and incomplete_reason.strip()):
        raise ValidationFailed(f"以下条目未完成，请勾选或说明原因：{'、'.join(undone)}",
                               code="CHECKLIST_INCOMPLETE", details={"undone": undone})
    at = occurred_at or clock.now()
    if at < execution.checkin_at:
        raise ValidationFailed("签退时间不能早于签到时间")
    minutes = int((at - execution.checkin_at).total_seconds() // 60)
    deviation = config_service.get(db, "exec.duration_deviation_minutes")
    abnormal = abs(minutes - order.item.standard_minutes) > deviation
    execution.checkout_at = at
    execution.duration_minutes = minutes
    execution.duration_abnormal = abnormal
    execution.summary = summary + (f"\n未完成说明：{incomplete_reason}" if undone else "")
    if abnormal:
        execution.spot_check_status = SpotCheckStatus.PENDING
        notify.notify_grid(db, order.grid_id, "时长异常待抽查",
                           f"{order.order_no} 实际 {minutes} 分钟，标准 {order.item.standard_minutes} 分钟",
                           ref_type="order", ref_id=order.id, level="WARN")
    transition(db, order, OrderStatus.COMPLETED, "CHECK_OUT", actor,
               f"签退，服务时长 {minutes} 分钟" + ("（时长异常）" if abnormal else ""))
    order.completed_at = at
    order.review_invited_at = clock.now()
    release_slots(db, order.id)
    db.flush()
    billing_service.record_service_fee(db, order, execution)
    notify.notify_family(db, order.elder_id, "服务已完成，请评价",
                         f"{order.item.name} 已完成，用时 {minutes} 分钟，请在 24 小时内评价",
                         ref_type="review", ref_id=order.id)
    alerts: list[int] = []
    if metrics:
        points = [{**m, "measured_at": m.get("measured_at") or at} for m in metrics]
        result = health_service.ingest_points(db, order.elder, points, HealthSource.VISIT, recorded_by=actor.id,
                                              order_id=order.id, note="服务签退时测量")
        alerts = result["alerts"]
        if result["errors"]:
            return {"execution": execution, "alerts": alerts, "metric_errors": result["errors"]}
    return {"execution": execution, "alerts": alerts, "metric_errors": []}


def report_exception(db: Session, actor: Actor, order_id: int, exception_type: str, description: str | None) -> ServiceException:
    order = _ensure_my_order(db, actor, order_id)
    if order.status not in (OrderStatus.DISPATCHED, OrderStatus.IN_SERVICE):
        raise Conflict("只有已派单或服务中的工单可以上报异常")
    if exception_type not in ExceptionType.__members__:
        raise ValidationFailed("异常类型不合法")
    if exception_type == ExceptionType.OTHER and not (description and description.strip()):
        raise ValidationFailed("选择「其他」时必须填写说明")
    row = ServiceException(order_id=order.id, grid_id=order.grid_id, exception_type=exception_type,
                           description=description, reported_by=actor.id, reported_at=clock.now())
    if exception_type == ExceptionType.UNWELL:
        # 现场突发不适直接进入健康预警流程，不等人工转交
        alert = health_service.trigger_manual(db, order.elder, "SITE-UNWELL",
                                              f"护理员现场上报：{description or '老人突发不适'}")
        row.alert_id = alert.id
    db.add(row)
    db.flush()
    record_event(db, order, "EXCEPTION", actor, f"上报异常：{label('ExceptionType', exception_type)}。{description or ''}")
    notify.notify_grid(db, order.grid_id, f"服务异常：{label('ExceptionType', exception_type)}",
                       f"{order.order_no} {description or ''}", ref_type="exception", ref_id=row.id, level="ERROR")
    return row


def _ensure_exception(db: Session, actor: Actor, exception_id: int) -> ServiceException:
    row = db.execute(scope_exceptions(actor, select(ServiceException).where(
        ServiceException.id == exception_id))).scalar()
    if row is None:
        raise NotFound("异常单不存在或不在您的数据范围内")
    return row


def pick_exception(db: Session, actor: Actor, exception_id: int) -> ServiceException:
    row = _ensure_exception(db, actor, exception_id)
    EXCEPTION_FLOW.check(row.status, ExceptionStatus.PROCESSING)
    row.status = ExceptionStatus.PROCESSING
    row.handler_id, row.handler_name, row.picked_at = actor.id, actor.name, clock.now()
    return row


def close_exception(db: Session, actor: Actor, exception_id: int, resolution: str, order_action: str) -> ServiceException:
    """闭环异常并决定工单去向：CONTINUE 继续服务 / NO_SHOW 判爽约 / CANCEL 取消。"""
    if not resolution or not resolution.strip():
        raise ValidationFailed("闭环异常必须填写处置结果")
    if order_action not in ("CONTINUE", "NO_SHOW", "CANCEL"):
        raise ValidationFailed("工单处理方式不合法")
    row = _ensure_exception(db, actor, exception_id)
    EXCEPTION_FLOW.check(row.status, ExceptionStatus.CLOSED)
    now = clock.now()
    row.status = ExceptionStatus.CLOSED
    row.handler_id, row.handler_name = actor.id, actor.name
    row.closed_at = now
    row.handle_minutes = int((now - row.reported_at).total_seconds() // 60)
    row.resolution = resolution
    row.order_action = order_action
    order = row.order
    if order_action == "NO_SHOW":
        transition(db, order, OrderStatus.NO_SHOW, "NO_SHOW", actor, resolution)
        order.cancel_reason = resolution
        order.cancel_chargeable = True
        order.cancelled_at = now
        billing_service.record_cancel_fee(db, order)
        release_slots(db, order.id)
    elif order_action == "CANCEL":
        # 老人突发不适等不可归责于老人的情形，取消不计费
        transition(db, order, OrderStatus.CANCELLED, "CANCEL", actor, resolution)
        order.cancel_reason = resolution
        order.cancelled_at = now
        release_slots(db, order.id)
    else:
        record_event(db, order, "EXCEPTION_CLOSED", actor, f"异常已闭环，继续服务：{resolution}")
    return row


def exception_view(row: ServiceException) -> dict:
    order = row.order
    return {
        "id": row.id, "order_id": row.order_id, "order_no": order.order_no, "elder_name": order.elder.name,
        "item_name": order.item.name, "caregiver_name": order.caregiver.name if order.caregiver else None,
        "exception_type": row.exception_type, "type_label": label("ExceptionType", row.exception_type),
        "description": row.description, "status": row.status, "status_label": label("ExceptionStatus", row.status),
        "reported_at": row.reported_at, "handler_name": row.handler_name, "closed_at": row.closed_at,
        "handle_minutes": row.handle_minutes, "resolution": row.resolution, "order_action": row.order_action,
        "alert_id": row.alert_id, "open_minutes": int((clock.now() - row.reported_at).total_seconds() // 60),
    }


def list_exceptions(db: Session, actor: Actor, status: str | None = None, page: int = 1, size: int = 30) -> dict:
    stmt = scope_exceptions(actor, select(ServiceException))
    if status:
        stmt = stmt.where(ServiceException.status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(ServiceException.reported_at.desc()).limit(size).offset((page - 1) * size)).scalars().all()
    return {"total": total, "page": page, "size": size, "items": [exception_view(r) for r in rows]}


def list_spot_checks(db: Session, actor: Actor, page: int = 1, size: int = 30) -> dict:
    from app.services.access import scope_orders
    stmt = scope_orders(actor, select(ServiceExecution).join(ServiceOrder)).where(
        ServiceExecution.spot_check_status == SpotCheckStatus.PENDING)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(ServiceExecution.checkout_at.desc()).limit(size).offset((page - 1) * size)).scalars().all()
    return {"total": total, "items": [{
        "order_id": e.order_id, "order_no": e.order.order_no, "elder_name": e.order.elder.name,
        "item_name": e.order.item.name, "caregiver_name": e.order.caregiver.name if e.order.caregiver else None,
        "duration_minutes": e.duration_minutes, "standard_minutes": e.order.item.standard_minutes,
        "checkout_at": e.checkout_at, "summary": e.summary,
    } for e in rows]}


def spot_check(db: Session, actor: Actor, order_id: int, passed: bool, note: str | None) -> ServiceExecution:
    order = ensure_order(db, actor, order_id)
    execution = order.execution
    if execution is None or execution.spot_check_status != SpotCheckStatus.PENDING:
        raise Conflict("该工单不在待抽查状态")
    if not passed and not (note and note.strip()):
        raise ValidationFailed("抽查不通过必须填写原因")
    execution.spot_check_status = SpotCheckStatus.PASS if passed else SpotCheckStatus.FAIL
    execution.spot_checked_by = actor.id
    execution.spot_checked_at = clock.now()
    execution.spot_check_note = note
    record_event(db, order, "SPOT_CHECK", actor, f"时长异常抽查：{'通过' if passed else '不通过'}。{note or ''}")
    return execution


def send_due_reminders(db: Session, now: datetime | None = None) -> int:
    """上门前 15 分钟提醒护理员。"""
    now = now or clock.now()
    minutes = config_service.get(db, "exec.reminder_minutes")
    rows = db.execute(select(ServiceOrder).where(
        ServiceOrder.status == OrderStatus.DISPATCHED, ServiceOrder.reminder_sent_at.is_(None),
        ServiceOrder.scheduled_start <= now + timedelta(minutes=minutes),
        ServiceOrder.scheduled_start > now - timedelta(hours=1))).scalars().all()
    for order in rows:
        order.reminder_sent_at = now
        if order.caregiver and order.caregiver.user_id:
            notify.in_app(db, order.caregiver.user_id, "上门提醒",
                          f"{order.item.name} 将于 {order.scheduled_start:%H:%M} 开始，请准时到达",
                          ref_type="order", ref_id=order.id, level="WARN", user_name=order.caregiver.name, role="caregiver")
        record_event(db, order, "REMINDER", None, f"已提前 {minutes} 分钟提醒护理员")
    return len(rows)


def scan_missed_checkins(db: Session, now: datetime | None = None) -> int:
    """超过预约时间仍未签到的工单提醒网格员，避免老人空等。"""
    now = now or clock.now()
    grace = config_service.get(db, "exec.noshow_grace_minutes")
    rows = db.execute(select(ServiceOrder).where(
        ServiceOrder.status == OrderStatus.DISPATCHED,
        ServiceOrder.scheduled_start <= now - timedelta(minutes=grace))).scalars().all()
    count = 0
    for order in rows:
        seen = db.execute(select(OrderEvent.id).where(OrderEvent.order_id == order.id,
                                                      OrderEvent.action == "MISSED_CHECKIN")).first()
        if seen:
            continue
        record_event(db, order, "MISSED_CHECKIN", None, f"超过预约时间 {grace} 分钟仍未签到")
        notify.notify_grid(db, order.grid_id, "护理员超时未签到", f"{order.order_no} 超过预约时间 {grace} 分钟仍未签到",
                           ref_type="order", ref_id=order.id, level="ERROR")
        count += 1
    return count
