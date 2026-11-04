"""护理员端离线同步。

现场信号差时，客户端把操作写入本地队列，联网后批量提交。服务端保证：
1. 幂等：op_id 由客户端生成，重试返回首次结果，不重复执行；
2. 保序：同一批按发生时间和客户端序号执行，同一秒内的签到也一定先于清单和签退；
3. 真实时间：服务时长按操作实际发生时间算，而不是同步到达的时间；
4. 隔离：单条失败只影响该条，比如工单已被改派时明确告知冲突，其他操作继续。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import AppError, Forbidden, ValidationFailed
from app.domain.enums import ExceptionType, HealthSource, OrderStatus, label
from app.models import ServiceOrder, SyncOp
from app.services import execution_service, health_service
from app.services.access import Actor, ensure_order

MAX_OFFLINE_AGE = timedelta(hours=72)
CLOCK_SKEW = timedelta(minutes=5)
SUPPORTED_KINDS = {"CHECKIN", "CHECKLIST", "CHECKOUT", "EXCEPTION", "HEALTH"}


def _execute(db: Session, actor: Actor, op: dict, occurred_at: datetime) -> dict:
    kind, order_id, payload = op["kind"], op["order_id"], op.get("payload") or {}
    if kind == "CHECKIN":
        execution = execution_service.check_in(db, actor, order_id, payload["lat"], payload["lng"], payload.get("note"),
                                               occurred_at=occurred_at, offline=True)
        return {"checkin_at": execution.checkin_at.isoformat(), "distance_m": round(execution.checkin_distance_m, 1)}
    if kind == "CHECKLIST":
        execution_service.update_checklist(db, actor, order_id, payload["items"])
        return {}
    if kind == "CHECKOUT":
        result = execution_service.check_out(db, actor, order_id, payload["summary"], payload.get("metrics"),
                                             payload.get("incomplete_reason"), occurred_at=occurred_at)
        return {"duration_minutes": result["execution"].duration_minutes, "alerts": result["alerts"]}
    if kind == "EXCEPTION":
        if payload.get("exception_type") not in ExceptionType.__members__:
            raise ValidationFailed("异常类型不合法")
        row = execution_service.report_exception(db, actor, order_id, payload["exception_type"], payload.get("description"))
        return {"exception_id": row.id}
    order = ensure_order(db, actor, order_id)
    points = [{**point, "measured_at": occurred_at} for point in payload["points"]]
    result = health_service.ingest_points(db, order.elder, points, HealthSource.VISIT, recorded_by=actor.id, order_id=order.id)
    return {"stored": result["stored"], "alerts": result["alerts"]}


def sync_batch(db: Session, actor: Actor, ops: list[dict]) -> dict:
    if actor.caregiver_id is None:
        raise Forbidden("离线同步仅面向护理员")
    if len(ops) > 200:
        raise ValidationFailed("单批最多 200 条操作")
    now = clock.now()
    results: list[dict] = []
    for op in sorted(ops, key=lambda item: (item["occurred_at"], item.get("seq", 0))):
        op_id = op["op_id"]
        previous = db.get(SyncOp, op_id)
        if previous is not None:
            if previous.user_id != actor.id:
                results.append({"op_id": op_id, "status": "rejected", "message": "操作编号冲突"})
                continue
            results.append({"op_id": op_id, "status": "duplicate", "result": previous.result})
            continue
        occurred_at = op["occurred_at"]
        try:
            if op["kind"] not in SUPPORTED_KINDS:
                raise ValidationFailed(f"不支持的操作类型：{op['kind']}")
            if occurred_at > now + CLOCK_SKEW:
                raise ValidationFailed("操作时间晚于服务器时间，请校准手机时钟")
            if occurred_at < now - MAX_OFFLINE_AGE:
                raise ValidationFailed("离线操作已超过 72 小时，请联系网格员人工补录")
            with db.begin_nested():
                result = _execute(db, actor, op, occurred_at)
                db.add(SyncOp(op_id=op_id, user_id=actor.id, kind=op["kind"], order_id=op.get("order_id"),
                              occurred_at=occurred_at, received_at=now, result=result))
            results.append({"op_id": op_id, "status": "applied", "result": result})
        except AppError as exc:
            results.append({"op_id": op_id, "status": "rejected", "code": exc.code, "message": exc.message})
        except (KeyError, TypeError) as exc:
            results.append({"op_id": op_id, "status": "rejected", "code": "BAD_PAYLOAD", "message": f"操作内容不完整：{exc}"})
    return {
        "applied": sum(r["status"] == "applied" for r in results),
        "duplicate": sum(r["status"] == "duplicate" for r in results),
        "rejected": sum(r["status"] == "rejected" for r in results),
        "results": results,
    }


def bootstrap(db: Session, actor: Actor) -> dict:
    """离线包：护理员今明两天的工单和执行所需信息。只含本人工单，最小必要。"""
    if actor.caregiver_id is None:
        raise Forbidden("离线同步仅面向护理员")
    now = clock.now()
    start = now.replace(hour=0, minute=0, second=0)
    orders = db.execute(select(ServiceOrder).where(
        ServiceOrder.caregiver_id == actor.caregiver_id,
        ServiceOrder.status.in_((OrderStatus.DISPATCHED, OrderStatus.IN_SERVICE)),
        ServiceOrder.scheduled_start >= start, ServiceOrder.scheduled_start < start + timedelta(days=2),
    ).order_by(ServiceOrder.scheduled_start)).scalars().all()
    return {
        "generated_at": now, "server_time": now,
        "orders": [{
            "id": o.id, "order_no": o.order_no, "item_name": o.item.name, "elder_name": o.elder.name,
            "address": o.address, "lat": o.lat, "lng": o.lng, "scheduled_start": o.scheduled_start,
            "scheduled_end": o.scheduled_end, "status": o.status, "status_label": label("OrderStatus", o.status),
            "standard_minutes": o.item.standard_minutes, "remark": o.remark, "care_tags": o.elder.care_tags or [],
            "allergies": o.elder.allergies, "chronic_diseases": o.elder.chronic_diseases or [],
            "checklist": o.execution.checklist if o.execution else [
                {"name": name, "done": False} for name in (o.item.checklist or [])],
        } for o in orders],
    }
