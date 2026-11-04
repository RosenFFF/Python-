"""结算、统计、系统管理、演示工具。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import schemas as s
from app.api.deps import get_actor, require
from app.core import clock
from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound
from app.core.exporter import file_response
from app.db.session import get_db
from app.domain.enums import label
from app.jobs.tasks import JOB_INDEX, JOBS, run_job
from app.models import AuditLog, JobRun, SubsidyPolicy, SystemConfig
from app.services import (
    audit_service,
    auth_service,
    backup_service,
    billing_service,
    config_service,
    elder_service,
    stats_service,
)
from app.services.access import Actor

router = APIRouter()


# ---------- 结算 ----------
@router.get("/bills", tags=["结算"])
def list_bills(month: str | None = None, status: str | None = None, actor: Actor = Depends(require("billing:read")),
               db: Session = Depends(get_db)):
    return billing_service.list_bills(db, actor, month, status)


@router.post("/bills/generate", tags=["结算"])
def generate_bill(body: s.GenerateBillIn, actor: Actor = Depends(require("billing:generate")),
                  db: Session = Depends(get_db)):
    if actor.scope == "COMMUNITY" and body.community_id != actor.user.community_id:
        raise NotFound("社区不存在或不在您的数据范围内")
    bill = billing_service.generate_bill(db, body.community_id, body.month, actor)
    db.commit()
    return billing_service.bill_view(db, bill)


@router.get("/bills/{bill_id}", tags=["结算"])
def bill_detail(bill_id: int, page: int = Query(1, ge=1), size: int = Query(50, le=200),
                actor: Actor = Depends(require("billing:read")), db: Session = Depends(get_db)):
    return billing_service.bill_detail(db, actor, bill_id, page, size)


@router.post("/bills/{bill_id}/review", tags=["结算"])
def review_bill(bill_id: int, body: s.ReviewBillIn, actor: Actor = Depends(require("billing:review")),
                db: Session = Depends(get_db)):
    bill = billing_service.review_bill(db, actor, bill_id, body.note)
    db.commit()
    return billing_service.bill_view(db, bill)


@router.post("/bills/{bill_id}/grid-confirm", tags=["结算"])
def grid_confirm(bill_id: int, actor: Actor = Depends(require("billing:grid_confirm")), db: Session = Depends(get_db)):
    bill = billing_service.grid_confirm(db, actor, bill_id)
    db.commit()
    return billing_service.bill_view(db, bill)


@router.post("/bills/{bill_id}/org-confirm", tags=["结算"])
def org_confirm(bill_id: int, actor: Actor = Depends(require("billing:org_confirm")), db: Session = Depends(get_db)):
    bill = billing_service.org_confirm(db, actor, bill_id)
    db.commit()
    return billing_service.bill_view(db, bill)


@router.post("/bills/{bill_id}/export", tags=["结算"])
def export_bill(bill_id: int, fmt: str = "xlsx", actor: Actor = Depends(require("billing:export")),
                db: Session = Depends(get_db)):
    bill, headers, rows = billing_service.export_rows(db, actor, bill_id)
    response = file_response(f"账单明细-{bill.month}-{bill.community_id}", fmt, headers, rows)
    db.commit()
    return response


@router.post("/discrepancies/{discrepancy_id}/resolve", tags=["结算"])
def resolve_discrepancy(discrepancy_id: int, body: s.ResolveIn, actor: Actor = Depends(require("billing:discrepancy")),
                        db: Session = Depends(get_db)):
    row = billing_service.resolve_discrepancy(db, actor, discrepancy_id, body.resolution, body.explanation)
    db.commit()
    return billing_service.discrepancy_view(row)


@router.post("/samples/{sample_id}/check", tags=["结算"])
def check_sample(sample_id: int, body: s.SampleIn, actor: Actor = Depends(require("billing:sample")),
                 db: Session = Depends(get_db)):
    bill = billing_service.check_sample(db, actor, sample_id, body.passed, body.note)
    db.commit()
    return billing_service.bill_view(db, bill)


@router.post("/fees/manual", tags=["结算"], status_code=201)
def manual_fee(body: s.ManualFeeIn, actor: Actor = Depends(require("billing:manual_fee")), db: Session = Depends(get_db)):
    fee = billing_service.create_manual_fee(db, actor, body.order_id, body.amount, body.note)
    db.commit()
    return billing_service.fee_view(fee)


@router.get("/statements", tags=["结算"])
def statements(elder_id: int | None = None, actor: Actor = Depends(require("statement:read", "billing:payment")),
               db: Session = Depends(get_db)):
    return billing_service.my_statements(db, actor, elder_id)


@router.post("/statements/{statement_id}/pay", tags=["结算"])
def pay_statement(statement_id: int, body: s.PaymentIn, actor: Actor = Depends(require("billing:payment")),
                  db: Session = Depends(get_db)):
    row = billing_service.register_payment(db, actor, statement_id, body.method)
    db.commit()
    return {"id": row.id, "payment_status": row.payment_status, "paid_at": row.paid_at}


@router.get("/subsidy-policies", tags=["结算"])
def subsidy_policies(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    rows = db.execute(select(SubsidyPolicy).order_by(SubsidyPolicy.id)).scalars().all()
    return [{"id": p.id, "disability_level": p.disability_level, "disability_label": label("DisabilityLevel", p.disability_level),
             "difficulty_type": p.difficulty_type, "difficulty_label": label("DifficultyType", p.difficulty_type),
             "gov_ratio": p.gov_ratio, "ltci_ratio": p.ltci_ratio, "monthly_cap": p.monthly_cap} for p in rows]


@router.patch("/subsidy-policies/{policy_id}", tags=["结算"])
def update_policy(policy_id: int, body: s.PolicyUpdateIn, actor: Actor = Depends(require("config:manage")),
                  db: Session = Depends(get_db)):
    policy = db.get(SubsidyPolicy, policy_id)
    if policy is None:
        raise NotFound("补贴政策不存在")
    before = {"gov": policy.gov_ratio, "ltci": policy.ltci_ratio, "cap": policy.monthly_cap}
    policy.gov_ratio, policy.ltci_ratio, policy.monthly_cap = body.gov_ratio, body.ltci_ratio, body.monthly_cap
    audit_service.write(db, actor.ctx, "POLICY_UPDATE", "subsidy_policy", policy.id, {"before": before, "after": body.model_dump()})
    db.commit()
    return {"ok": True}


# ---------- 统计 ----------
@router.get("/stats/dashboard/{kind}", tags=["统计"])
def dashboard(kind: str, start: date | None = None, end: date | None = None, granularity: str | None = None,
              actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    return stats_service.dashboard(db, actor, kind, stats_service.make_range(start, end, granularity))


@router.post("/stats/dashboard/{kind}/export", tags=["统计"])
def export_dashboard(kind: str, start: date | None = None, end: date | None = None, granularity: str | None = None,
                     fmt: str = "xlsx", actor: Actor = Depends(require("stats:export")), db: Session = Depends(get_db)):
    rng = stats_service.make_range(start, end, granularity)
    data = stats_service.dashboard(db, actor, kind, rng)
    headers, rows = stats_service.flatten_for_export(data)
    audit_service.write(db, actor.ctx, "EXPORT", "dashboard", kind, {
        "start": rng.period.start, "end": rng.period.end, "fmt": fmt, "rows": len(rows)})
    response = file_response(f"统计-{kind}-{rng.period.start}-{rng.period.end}", fmt, headers, rows)
    db.commit()
    return response


# ---------- 用户与权限 ----------
@router.get("/admin/users", tags=["系统"])
def list_users(keyword: str | None = None, role: str | None = None, page: int = Query(1, ge=1),
               size: int = Query(20, le=100), actor: Actor = Depends(require("user:manage")), db: Session = Depends(get_db)):
    return auth_service.list_users(db, keyword=keyword, role=role, page=page, size=size)


@router.post("/admin/users", tags=["系统"], status_code=201)
def create_user(body: s.UserCreateIn, actor: Actor = Depends(require("user:manage")), db: Session = Depends(get_db)):
    user = auth_service.create_user(db, actor, body.model_dump())
    db.commit()
    return auth_service.user_view(user)


@router.patch("/admin/users/{user_id}", tags=["系统"])
def update_user(user_id: int, body: s.UserUpdateIn, actor: Actor = Depends(require("user:manage")),
                db: Session = Depends(get_db)):
    user = auth_service.update_user(db, actor, user_id, body.model_dump(exclude_unset=True))
    db.commit()
    return auth_service.user_view(user)


@router.post("/admin/users/{user_id}/reset-password", tags=["系统"])
def reset_password(user_id: int, body: s.PasswordResetIn, actor: Actor = Depends(require("user:manage")),
                   db: Session = Depends(get_db)):
    auth_service.reset_password(db, actor, user_id, body.new_password)
    db.commit()
    return {"ok": True}


@router.get("/admin/roles", tags=["系统"])
def role_matrix(actor: Actor = Depends(require("role:manage")), db: Session = Depends(get_db)):
    return auth_service.role_matrix(db)


@router.put("/admin/roles/{role_code}/permissions", tags=["系统"])
def update_role(role_code: str, body: s.RolePermissionsIn, actor: Actor = Depends(require("role:manage")),
                db: Session = Depends(get_db)):
    auth_service.update_role_permissions(db, actor, role_code, body.permissions)
    db.commit()
    return {"ok": True}


# ---------- 业务参数 ----------
@router.get("/admin/configs", tags=["系统"])
def list_configs(actor: Actor = Depends(require("config:manage")), db: Session = Depends(get_db)):
    return config_service.list_all(db)


@router.put("/admin/configs/{key}", tags=["系统"])
def update_config(key: str, body: s.ConfigUpdateIn, actor: Actor = Depends(require("config:manage")),
                  db: Session = Depends(get_db)):
    before, after = config_service.set_value(db, key, body.value, actor.id)
    audit_service.write(db, actor.ctx, "CONFIG_UPDATE", "config", key, {"before": before, "after": after})
    db.commit()
    return {"key": key, "value": after}


# ---------- 审计 ----------
def _audit_stmt(action, actor_name, date_from, date_to):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_name:
        stmt = stmt.where(AuditLog.actor_name.contains(actor_name))
    if date_from:
        stmt = stmt.where(AuditLog.at >= date_from)
    if date_to:
        stmt = stmt.where(AuditLog.at < date_to)
    return stmt


@router.get("/audit/logs", tags=["审计"])
def audit_logs(action: str | None = None, actor_name: str | None = None, date_from: datetime | None = None,
               date_to: datetime | None = None, page: int = Query(1, ge=1), size: int = Query(30, le=200),
               actor: Actor = Depends(require("audit:read")), db: Session = Depends(get_db)):
    stmt = _audit_stmt(action, actor_name, date_from, date_to)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(AuditLog.id.desc()).limit(size).offset((page - 1) * size)).scalars().all()
    return {"total": total, "page": page, "size": size, "items": [{
        "id": r.id, "at": r.at, "actor_name": r.actor_name, "actor_role": r.actor_role,
        "actor_role_label": label("RoleCode", r.actor_role), "action": r.action, "target_type": r.target_type,
        "target_id": r.target_id, "detail": r.detail, "ip": r.ip, "hash": r.hash[:12],
    } for r in rows]}


@router.get("/audit/verify", tags=["审计"])
def audit_verify(actor: Actor = Depends(require("audit:read")), db: Session = Depends(get_db)):
    return audit_service.verify_chain(db)


@router.get("/audit/sensitive-access", tags=["审计"])
def sensitive_access(actor: Actor = Depends(require("audit:read")), db: Session = Depends(get_db)):
    return elder_service.access_logs(db, actor)


@router.post("/audit/export", tags=["审计"])
def audit_export(action: str | None = None, actor_name: str | None = None, date_from: datetime | None = None,
                 date_to: datetime | None = None, fmt: str = "xlsx", actor: Actor = Depends(require("audit:read")),
                 db: Session = Depends(get_db)):
    stmt = _audit_stmt(action, actor_name, date_from, date_to).order_by(AuditLog.id.desc()).limit(20000)
    rows = [[r.id, r.at.isoformat(), r.actor_name, r.actor_role, r.action, r.target_type, r.target_id, r.detail, r.ip, r.hash]
            for r in db.execute(stmt).scalars()]
    audit_service.write(db, actor.ctx, "EXPORT", "audit", None, {"rows": len(rows), "fmt": fmt})
    response = file_response("审计日志", fmt, ["ID", "时间", "操作人", "角色", "动作", "对象类型", "对象ID", "详情", "IP", "哈希"], rows)
    db.commit()
    return response


# ---------- 备份恢复 ----------
@router.get("/admin/backups", tags=["系统"])
def list_backups(actor: Actor = Depends(require("backup:manage"))):
    return {"backups": backup_service.list_backups(), "reports": backup_service.list_restore_reports()}


@router.post("/admin/backups", tags=["系统"], status_code=201)
def create_backup(actor: Actor = Depends(require("backup:manage")), db: Session = Depends(get_db)):
    result = backup_service.create_backup(actor.ctx, "MANUAL", db)
    db.commit()
    return result


@router.post("/admin/backups/{name}/verify", tags=["系统"])
def verify_backup(name: str, actor: Actor = Depends(require("backup:manage"))):
    return backup_service.verify_backup(name)


@router.post("/admin/backups/restore/preview", tags=["系统"])
def restore_preview(body: s.RestorePreviewIn, actor: Actor = Depends(require("backup:manage"))):
    return backup_service.preview_restore(actor, body.name, body.target_time)


@router.post("/admin/backups/restore/confirm", tags=["系统"])
def restore_confirm(body: s.RestoreConfirmIn, actor: Actor = Depends(require("backup:manage")),
                    db: Session = Depends(get_db)):
    # 恢复要写主库，必须先结束本请求的事务并释放写锁
    db.commit()
    db.close()
    return backup_service.confirm_restore(actor, body.confirm_token, body.password, body.confirm_word)


# ---------- 定时任务 ----------
@router.get("/admin/jobs", tags=["系统"])
def list_jobs(actor: Actor = Depends(require("job:manage")), db: Session = Depends(get_db)):
    runs = db.execute(select(JobRun).order_by(JobRun.id.desc()).limit(50)).scalars().all()
    return {
        "jobs": [{"name": j.name, "title": j.title, "trigger": dict(j.trigger)} for j in JOBS],
        "runs": [{"job": r.job, "title": JOB_INDEX[r.job].title if r.job in JOB_INDEX else r.job, "started_at": r.started_at,
                  "duration_ms": r.duration_ms, "ok": bool(r.ok), "processed": r.processed, "message": r.message} for r in runs],
    }


@router.post("/admin/jobs/{name}/run", tags=["系统"])
def run_job_now(name: str, actor: Actor = Depends(require("job:manage")), db: Session = Depends(get_db)):
    job = JOB_INDEX.get(name)
    if job is None:
        raise NotFound("任务不存在")
    audit_service.write(db, actor.ctx, "JOB_RUN", "job", name)
    db.commit()
    db.close()
    run = run_job(job)
    return {"job": name, "ok": bool(run.ok), "processed": run.processed, "message": run.message}


# ---------- 演示工具（仅演示模式） ----------
TIME_DRIVEN_JOBS = ["expire_offers", "alert_escalation", "visit_reminder", "missed_checkin", "alert_no_data",
                    "followup_overdue", "flush_outbox"]


def _ensure_demo() -> None:
    if not get_settings().demo_mode:
        raise Forbidden("演示工具仅在演示模式下可用")


@router.get("/demo/clock", tags=["演示"])
def demo_clock(actor: Actor = Depends(get_actor)):
    _ensure_demo()
    return {"now": clock.now(), "offset_minutes": clock.offset_minutes()}


def _persist_offset(db: Session) -> None:
    row = db.get(SystemConfig, "demo.clock_offset_minutes")
    if row is None:
        db.add(SystemConfig(key="demo.clock_offset_minutes", value=clock.offset_minutes()))
    else:
        row.value = clock.offset_minutes()


@router.post("/demo/clock/advance", tags=["演示"])
def demo_advance(body: s.ClockAdvanceIn, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    """快进演示时钟并立刻运行时间驱动的任务，现场展示超时升级，不用真等 30 分钟。"""
    _ensure_demo()
    before = clock.now()
    clock.advance(timedelta(minutes=body.minutes))
    _persist_offset(db)
    audit_service.write(db, actor.ctx, "DEMO_CLOCK", "clock", None, {"from": before, "minutes": body.minutes})
    db.commit()
    db.close()
    results = {name: run_job(JOB_INDEX[name]).processed for name in TIME_DRIVEN_JOBS}
    return {"now": clock.now(), "offset_minutes": clock.offset_minutes(), "jobs": results}


@router.post("/demo/clock/reset", tags=["演示"])
def demo_reset(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    _ensure_demo()
    clock.set_offset_minutes(0)
    _persist_offset(db)
    audit_service.write(db, actor.ctx, "DEMO_CLOCK", "clock", None, {"reset": True})
    db.commit()
    return {"now": clock.now(), "offset_minutes": 0}
