"""定时任务定义。业务函数接受 now，任务逻辑可在测试中用任意时间驱动。"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core import clock
from app.db.session import new_session
from app.models import AuditLog, JobRun
from app.services import (
    audit_service,
    backup_service,
    billing_service,
    config_service,
    dispatch_service,
    execution_service,
    health_service,
    notification_service,
    order_service,
    quality_service,
)

logger = logging.getLogger("yihu.jobs")


def purge_expired_audit(db: Session) -> int:
    """清理超过保留期的审计日志。触发器保证保留期内的记录删不掉。"""
    days = config_service.get(db, "audit.retention_days")
    cutoff = clock.now() - timedelta(days=days)
    return db.execute(delete(AuditLog).where(AuditLog.at < cutoff)).rowcount or 0


def _backup(db: Session) -> int:
    backup_service.create_backup(audit_service.SYSTEM, "AUTO", db)
    return 1


@dataclass(frozen=True)
class Job:
    name: str
    title: str
    func: Callable[[Session], int]
    trigger: dict


JOBS: list[Job] = [
    Job("expire_offers", "接单超时顺延", dispatch_service.expire_offers, {"trigger": "interval", "seconds": 60}),
    Job("alert_escalation", "预警超时升级", health_service.scan_escalations, {"trigger": "interval", "seconds": 60}),
    Job("alert_no_data", "长时间无数据扫描", health_service.scan_no_data, {"trigger": "interval", "minutes": 10}),
    Job("flush_outbox", "短信发件箱投递", notification_service.flush_outbox, {"trigger": "interval", "seconds": 30}),
    Job("visit_reminder", "上门前 15 分钟提醒", execution_service.send_due_reminders, {"trigger": "interval", "seconds": 60}),
    Job("missed_checkin", "超时未签到扫描", execution_service.scan_missed_checkins, {"trigger": "interval", "minutes": 5}),
    Job("followup_overdue", "回访超时督办", quality_service.scan_overdue_followups, {"trigger": "interval", "minutes": 30}),
    Job("recurring_orders", "周期预约滚动生成", lambda db: order_service.generate_recurring_orders(db, clock.now()),
        {"trigger": "cron", "hour": 0, "minute": 10}),
    Job("monthly_billing", "月度账单生成", billing_service.run_monthly_billing, {"trigger": "cron", "day": 1, "hour": 0, "minute": 30}),
    Job("daily_backup", "每日全量备份", _backup, {"trigger": "cron", "hour": 2, "minute": 0}),
    Job("audit_retention", "审计日志保留期清理", purge_expired_audit, {"trigger": "cron", "hour": 3, "minute": 0}),
]
JOB_INDEX = {job.name: job for job in JOBS}


def run_job(job: Job) -> JobRun:
    """执行一次任务并记录结果。任务失败不向外抛，避免调度线程退出。"""
    started = time.perf_counter()
    started_at = clock.now()
    db = new_session()
    processed, ok, message = 0, 1, None
    try:
        processed = job.func(db) or 0
        db.commit()
    except Exception as exc:  # 调度边界：记录所有异常并继续调度
        db.rollback()
        logger.exception("定时任务失败: %s", job.name)
        ok, message = 0, repr(exc)[:500]
    try:
        run = JobRun(job=job.name, started_at=started_at, duration_ms=int((time.perf_counter() - started) * 1000),
                     ok=ok, processed=int(processed), message=message)
        # 空转不留记录，避免高频任务刷屏；有处理量或失败才写
        if processed or not ok or job.trigger["trigger"] == "cron":
            db.add(run)
            db.commit()
        return run
    finally:
        db.close()
