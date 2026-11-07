"""业务流程：预约、派单、执行、健康预警、评价投诉。"""
from __future__ import annotations

import hmac
from datetime import datetime

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api import schemas as s
from app.api.deps import get_actor, require
from app.core.config import get_settings
from app.core.errors import Unauthorized
from app.db.session import get_db
from app.services import (
    ai_service,
    dispatch_service,
    execution_service,
    health_service,
    order_service,
    quality_service,
    sync_service,
)
from app.services.access import Actor

router = APIRouter()


# ---------- 预约工单 ----------
@router.post("/orders", tags=["预约工单"], status_code=201)
def create_order(body: s.OrderCreateIn, actor: Actor = Depends(require("order:create")), db: Session = Depends(get_db)):
    order = order_service.create_order(db, actor, body.elder_id, body.item_id, body.scheduled_start, address=body.address,
                                       lat=body.lat, lng=body.lng, remark=body.remark, source=body.source)
    db.commit()
    return order_service.to_view(order)


@router.get("/orders", tags=["预约工单"])
def list_orders(status: str | None = None, statuses: str | None = None, elder_id: int | None = None,
                needs_manual: bool | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
                keyword: str | None = None, caregiver_id: int | None = None, sort: str = "time",
                page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                actor: Actor = Depends(require("order:read")), db: Session = Depends(get_db)):
    return order_service.list_orders(db, actor, status=status, statuses=statuses.split(",") if statuses else None,
                                     elder_id=elder_id, needs_manual=needs_manual, date_from=date_from, date_to=date_to,
                                     keyword=keyword, caregiver_id=caregiver_id, sort=sort, page=page, size=size)


@router.get("/orders/{order_id}", tags=["预约工单"])
def order_detail(order_id: int, actor: Actor = Depends(require("order:read")), db: Session = Depends(get_db)):
    return order_service.order_detail(db, actor, order_id)


@router.post("/orders/{order_id}/accept", tags=["预约工单"])
def accept_order(order_id: int, body: s.NoteIn, actor: Actor = Depends(require("order:accept")),
                 db: Session = Depends(get_db)):
    order = order_service.accept_order(db, actor, order_id, body.note)
    db.commit()
    return order_service.to_view(order)


@router.get("/orders/{order_id}/cancel-preview", tags=["预约工单"])
def cancel_preview(order_id: int, actor: Actor = Depends(require("order:cancel")), db: Session = Depends(get_db)):
    return order_service.preview_cancel(db, actor, order_id)


@router.post("/orders/{order_id}/cancel", tags=["预约工单"])
def cancel_order(order_id: int, body: s.ReasonIn, actor: Actor = Depends(require("order:cancel")),
                 db: Session = Depends(get_db)):
    order = order_service.cancel_order(db, actor, order_id, body.reason)
    db.commit()
    return order_service.to_view(order)


@router.get("/plans", tags=["预约工单"])
def list_plans(elder_id: int | None = None, actor: Actor = Depends(require("order:read")), db: Session = Depends(get_db)):
    return order_service.list_plans(db, actor, elder_id)


@router.post("/plans", tags=["预约工单"], status_code=201)
def create_plan(body: s.PlanCreateIn, actor: Actor = Depends(require("order:create")), db: Session = Depends(get_db)):
    plan = order_service.create_plan(db, actor, body.elder_id, body.item_id, body.weekday, body.time_of_day,
                                     body.start_date, body.end_date, body.remark)
    db.commit()
    return {"id": plan.id}


@router.delete("/plans/{plan_id}", tags=["预约工单"])
def stop_plan(plan_id: int, actor: Actor = Depends(require("order:create")), db: Session = Depends(get_db)):
    order_service.stop_plan(db, actor, plan_id)
    db.commit()
    return {"ok": True}


# ---------- 派单 ----------
@router.post("/orders/{order_id}/recommend", tags=["派单"])
def recommend(order_id: int, actor: Actor = Depends(require("dispatch:manage")), db: Session = Depends(get_db)):
    result = dispatch_service.recommend(db, actor, order_id)
    db.commit()
    return result


@router.post("/orders/{order_id}/assign", tags=["派单"])
def assign(order_id: int, body: s.AssignIn, actor: Actor = Depends(require("dispatch:manage")),
           db: Session = Depends(get_db)):
    result = dispatch_service.assign(db, actor, order_id, body.caregiver_id, body.round_id)
    db.commit()
    return result


@router.post("/orders/{order_id}/reassign", tags=["派单"])
def reassign(order_id: int, body: s.ReassignIn, actor: Actor = Depends(require("dispatch:manage")),
             db: Session = Depends(get_db)):
    result = dispatch_service.reassign(db, actor, order_id, body.caregiver_id, body.reason)
    db.commit()
    return result


@router.get("/orders/{order_id}/dispatch-history", tags=["派单"])
def dispatch_history(order_id: int, actor: Actor = Depends(require("order:read")), db: Session = Depends(get_db)):
    return dispatch_service.dispatch_history(db, actor, order_id)


@router.get("/offers/mine", tags=["派单"])
def my_offers(actor: Actor = Depends(require("offer:respond")), db: Session = Depends(get_db)):
    return dispatch_service.my_offers(db, actor)


@router.post("/offers/{offer_id}/accept", tags=["派单"])
def accept_offer(offer_id: int, actor: Actor = Depends(require("offer:respond")), db: Session = Depends(get_db)):
    order = dispatch_service.accept_offer(db, actor, offer_id)
    db.commit()
    return order_service.to_view(order)


@router.post("/offers/{offer_id}/reject", tags=["派单"])
def reject_offer(offer_id: int, body: s.ReasonIn, actor: Actor = Depends(require("offer:respond")),
                 db: Session = Depends(get_db)):
    order = dispatch_service.reject_offer(db, actor, offer_id, body.reason)
    db.commit()
    return order_service.to_view(order)


# ---------- 上门执行 ----------
@router.post("/orders/{order_id}/check-in", tags=["上门执行"])
def check_in(order_id: int, body: s.CheckInIn, actor: Actor = Depends(require("exec:perform")),
             db: Session = Depends(get_db)):
    execution = execution_service.check_in(db, actor, order_id, body.lat, body.lng, body.note)
    db.commit()
    return {"checkin_at": execution.checkin_at, "distance_m": round(execution.checkin_distance_m, 1),
            "checklist": execution.checklist}


@router.put("/orders/{order_id}/checklist", tags=["上门执行"])
def update_checklist(order_id: int, body: s.ChecklistIn, actor: Actor = Depends(require("exec:perform")),
                     db: Session = Depends(get_db)):
    execution = execution_service.update_checklist(db, actor, order_id, [i.model_dump() for i in body.items])
    db.commit()
    return {"checklist": execution.checklist}


@router.post("/orders/{order_id}/photos", tags=["上门执行"], status_code=201)
def upload_photo(order_id: int, file: UploadFile = File(...), actor: Actor = Depends(require("exec:perform")),
                 db: Session = Depends(get_db)):
    content = file.file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    photo = execution_service.upload_photo(db, actor, order_id, content)
    db.commit()
    return {"id": photo.id, "size_bytes": photo.size_bytes, "original_bytes": photo.original_bytes,
            "width": photo.width, "height": photo.height}


@router.get("/photos/{photo_id}", tags=["上门执行"])
def get_photo(photo_id: int, actor: Actor = Depends(require("order:read")), db: Session = Depends(get_db)):
    return FileResponse(execution_service.read_photo(db, actor, photo_id), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=300"})


@router.post("/orders/{order_id}/check-out", tags=["上门执行"])
def check_out(order_id: int, body: s.CheckOutIn, actor: Actor = Depends(require("exec:perform")),
              db: Session = Depends(get_db)):
    result = execution_service.check_out(db, actor, order_id, body.summary, [m.model_dump() for m in body.metrics],
                                         body.incomplete_reason)
    db.commit()
    execution = result["execution"]
    return {"duration_minutes": execution.duration_minutes, "duration_abnormal": execution.duration_abnormal,
            "alerts": result["alerts"], "metric_errors": result["metric_errors"]}


@router.post("/orders/{order_id}/exceptions", tags=["上门执行"], status_code=201)
def report_exception(order_id: int, body: s.ExceptionIn, actor: Actor = Depends(require("exec:perform")),
                     db: Session = Depends(get_db)):
    row = execution_service.report_exception(db, actor, order_id, body.exception_type, body.description)
    db.commit()
    return execution_service.exception_view(row)


@router.get("/exceptions", tags=["上门执行"])
def list_exceptions(status: str | None = None, page: int = 1, size: int = Query(30, le=100),
                    actor: Actor = Depends(require("exception:handle")), db: Session = Depends(get_db)):
    return execution_service.list_exceptions(db, actor, status, page, size)


@router.post("/exceptions/{exception_id}/pick", tags=["上门执行"])
def pick_exception(exception_id: int, actor: Actor = Depends(require("exception:handle")), db: Session = Depends(get_db)):
    row = execution_service.pick_exception(db, actor, exception_id)
    db.commit()
    return execution_service.exception_view(row)


@router.post("/exceptions/{exception_id}/close", tags=["上门执行"])
def close_exception(exception_id: int, body: s.ExceptionCloseIn, actor: Actor = Depends(require("exception:handle")),
                    db: Session = Depends(get_db)):
    row = execution_service.close_exception(db, actor, exception_id, body.resolution, body.order_action)
    db.commit()
    return execution_service.exception_view(row)


@router.get("/spot-checks", tags=["上门执行"])
def list_spot_checks(actor: Actor = Depends(require("exec:spotcheck")), db: Session = Depends(get_db)):
    return execution_service.list_spot_checks(db, actor)


@router.post("/orders/{order_id}/spot-check", tags=["上门执行"])
def spot_check(order_id: int, body: s.SpotCheckIn, actor: Actor = Depends(require("exec:spotcheck")),
               db: Session = Depends(get_db)):
    execution_service.spot_check(db, actor, order_id, body.passed, body.note)
    db.commit()
    return {"ok": True}


# ---------- 离线同步 ----------
@router.get("/sync/bootstrap", tags=["离线同步"])
def sync_bootstrap(actor: Actor = Depends(require("exec:perform")), db: Session = Depends(get_db)):
    return sync_service.bootstrap(db, actor)


@router.post("/sync/batch", tags=["离线同步"])
def sync_batch(body: s.SyncBatchIn, actor: Actor = Depends(require("exec:perform")), db: Session = Depends(get_db)):
    result = sync_service.sync_batch(db, actor, [op.model_dump() for op in body.ops])
    db.commit()
    return result


# ---------- 健康预警 ----------
@router.post("/health/wearable/batch", tags=["健康预警"])
def wearable_batch(body: s.WearableBatchIn, x_device_key: str = Header(default=""), db: Session = Depends(get_db)):
    """可穿戴网关批量上报，用设备密钥鉴权，不走用户登录。"""
    if not hmac.compare_digest(x_device_key, get_settings().device_api_key):
        raise Unauthorized("设备密钥无效")
    result = health_service.ingest_wearable_batch(db, [r.model_dump() for r in body.records])
    db.commit()
    return result


@router.post("/elders/{elder_id}/health", tags=["健康预警"], status_code=201)
def record_followup(elder_id: int, body: s.FollowupRecordIn, actor: Actor = Depends(require("health:write")),
                    db: Session = Depends(get_db)):
    result = health_service.ingest_followup(db, actor, elder_id, [p.model_dump() for p in body.points], body.note)
    db.commit()
    return result


@router.get("/elders/{elder_id}/health", tags=["健康预警"])
def health_series(elder_id: int, metric: str | None = None, days: int = Query(30, ge=1, le=365),
                  actor: Actor = Depends(require("health:read")), db: Session = Depends(get_db)):
    return health_service.series(db, actor, elder_id, metric, days)


@router.post("/elders/{elder_id}/devices", tags=["健康预警"], status_code=201)
def bind_device(elder_id: int, body: s.DeviceBindIn, actor: Actor = Depends(require("device:manage")),
                db: Session = Depends(get_db)):
    device = health_service.bind_device(db, actor, elder_id, body.serial, body.kind)
    db.commit()
    return {"id": device.id, "serial": device.serial}


@router.get("/alerts", tags=["健康预警"])
def list_alerts(status: str | None = None, level: str | None = None, only_open: bool = False, escalated: bool = False,
                elder_id: int | None = None, page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                actor: Actor = Depends(require("alert:read")), db: Session = Depends(get_db)):
    return health_service.list_alerts(db, actor, status=status, level=level, only_open=only_open, escalated=escalated,
                                      elder_id=elder_id, page=page, size=size)


@router.get("/alerts/{alert_id}", tags=["健康预警"])
def alert_detail(alert_id: int, actor: Actor = Depends(require("alert:read")), db: Session = Depends(get_db)):
    return health_service.alert_detail(db, actor, alert_id)


@router.post("/alerts/{alert_id}/respond", tags=["健康预警"])
def respond_alert(alert_id: int, body: s.AlertRespondIn, actor: Actor = Depends(require("alert:handle")),
                  db: Session = Depends(get_db)):
    alert = health_service.respond(db, actor, alert_id, body.note)
    db.commit()
    return health_service.alert_view(alert)


@router.post("/alerts/{alert_id}/close", tags=["健康预警"])
def close_alert(alert_id: int, body: s.AlertCloseIn, actor: Actor = Depends(require("alert:handle")),
                db: Session = Depends(get_db)):
    alert = health_service.close(db, actor, alert_id, body.conclusion, measures=body.measures,
                                 false_reason=body.false_reason, hospital=body.hospital, referred_at=body.referred_at)
    db.commit()
    return health_service.alert_view(alert)


@router.post("/sos", tags=["健康预警"], status_code=201)
def sos(body: s.SosIn, actor: Actor = Depends(require("sos:trigger")), db: Session = Depends(get_db)):
    alert = health_service.trigger_sos(db, actor, body.elder_id, body.note)
    db.commit()
    return health_service.alert_view(alert)


@router.get("/alert-rules", tags=["健康预警"])
def list_rules(actor: Actor = Depends(require("alert:read")), db: Session = Depends(get_db)):
    return health_service.list_rules(db)


@router.post("/alert-rules", tags=["健康预警"], status_code=201)
def create_rule(body: s.RuleCreateIn, actor: Actor = Depends(require("rule:manage")), db: Session = Depends(get_db)):
    rule = health_service.create_rule(db, actor, body.model_dump())
    db.commit()
    return health_service.rule_view(rule)


@router.patch("/alert-rules/{rule_id}", tags=["健康预警"])
def update_rule(rule_id: int, body: s.RuleUpdateIn, actor: Actor = Depends(require("rule:manage")),
                db: Session = Depends(get_db)):
    rule = health_service.update_rule(db, actor, rule_id, body.model_dump(exclude_unset=True))
    db.commit()
    return health_service.rule_view(rule)


# ---------- 评价与投诉 ----------
@router.post("/orders/{order_id}/review", tags=["质量"], status_code=201)
def submit_review(order_id: int, body: s.ReviewIn, actor: Actor = Depends(require("review:create")),
                  db: Session = Depends(get_db)):
    review = quality_service.submit_review(db, actor, order_id, body.punctuality, body.attitude, body.professionalism,
                                           body.effectiveness, body.comment)
    db.commit()
    return {"id": review.id, "overall": review.overall}


@router.get("/followups", tags=["质量"])
def list_followups(status: str | None = None, actor: Actor = Depends(require("followup:handle")),
                   db: Session = Depends(get_db)):
    return quality_service.list_followups(db, actor, status)


@router.post("/followups/{task_id}/handle", tags=["质量"])
def handle_followup(task_id: int, body: s.FollowupHandleIn, actor: Actor = Depends(require("followup:handle")),
                    db: Session = Depends(get_db)):
    task = quality_service.handle_followup(db, actor, task_id, body.outcome, body.note)
    db.commit()
    return quality_service.followup_view(task)


@router.post("/complaints", tags=["质量"], status_code=201)
def create_complaint(body: s.ComplaintIn, actor: Actor = Depends(require("complaint:create")),
                     db: Session = Depends(get_db)):
    complaint = quality_service.create_complaint(db, actor, body.order_id, body.content)
    db.commit()
    return quality_service.complaint_view(complaint)


@router.get("/complaints", tags=["质量"])
def list_complaints(status: str | None = None, actor: Actor = Depends(require("complaint:read")),
                    db: Session = Depends(get_db)):
    return quality_service.list_complaints(db, actor, status)


@router.post("/complaints/{complaint_id}/assign", tags=["质量"])
def assign_complaint(complaint_id: int, actor: Actor = Depends(require("complaint:manage")), db: Session = Depends(get_db)):
    complaint = quality_service.assign_complaint(db, actor, complaint_id)
    db.commit()
    return quality_service.complaint_view(complaint)


@router.post("/complaints/{complaint_id}/explain", tags=["质量"])
def explain_complaint(complaint_id: int, body: s.ExplainIn, actor: Actor = Depends(require("complaint:explain")),
                      db: Session = Depends(get_db)):
    complaint = quality_service.explain_complaint(db, actor, complaint_id, body.explanation)
    db.commit()
    return quality_service.complaint_view(complaint)


@router.post("/complaints/{complaint_id}/conclude", tags=["质量"])
def conclude_complaint(complaint_id: int, body: s.ConcludeIn, actor: Actor = Depends(require("complaint:manage")),
                       db: Session = Depends(get_db)):
    complaint = quality_service.conclude_complaint(db, actor, complaint_id, body.conclusion, body.basis, body.actions,
                                                   body.penalty_points)
    db.commit()
    return quality_service.complaint_view(complaint)


@router.get("/performance", tags=["质量"])
def performance(month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), sort: str = "completed",
                actor: Actor = Depends(require("perf:read")), db: Session = Depends(get_db)):
    return quality_service.performance(db, actor, month, sort)


# ---------- 预约解析与健康分析 ----------
@router.post("/ai/parse-booking", tags=["预约解析与健康分析"])
def parse_booking(body: s.ParseBookingIn, actor: Actor = Depends(require("order:create")), db: Session = Depends(get_db)):
    return ai_service.parse_booking(db, body.text)


@router.get("/ai/elders/{elder_id}/summary", tags=["预约解析与健康分析"])
def health_summary(elder_id: int, actor: Actor = Depends(require("health:read")), db: Session = Depends(get_db)):
    return ai_service.health_summary(db, actor, elder_id)


@router.get("/ai/top-risk", tags=["预约解析与健康分析"])
def top_risk(limit: int = Query(10, le=50), actor: Actor = Depends(require("alert:handle")), db: Session = Depends(get_db)):
    return ai_service.top_risk(db, actor, limit)


@router.get("/ai/status", tags=["预约解析与健康分析"])
def ai_status(actor: Actor = Depends(get_actor)):
    return {"llm_enabled": ai_service.llm_enabled()}
