"""APScheduler 调度器。单进程部署，恢复期间暂停任务。"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.jobs.tasks import JOBS, Job, run_job
from app.services.backup_service import maintenance

logger = logging.getLogger("yihu.jobs")
_scheduler: BackgroundScheduler | None = None


def _guarded(job: Job) -> None:
    if maintenance.is_set():
        logger.info("系统维护中，跳过任务 %s", job.name)
        return
    run_job(job)


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(
        timezone="Asia/Shanghai", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300})
    for job in JOBS:
        _scheduler.add_job(_guarded, args=[job], id=job.name, name=job.title, **job.trigger)
    _scheduler.start()
    logger.info("调度器已启动，共 %d 个任务", len(JOBS))


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
