"""业务参数：内置默认值，可在后台修改，保存时校验并写审计。"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import NotFound, ValidationFailed
from app.domain.dispatch_scoring import DEFAULT_WEIGHTS, normalize_weights
from app.models import SystemConfig


def _positive(value: Any) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError("必须是大于 0 的数字")


def _ratio(value: Any) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        raise ValueError("必须是 0 到 1 之间的比例")


def _weights(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("权重必须是对象")
    normalize_weights(value)


def _alert_sla(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"RED", "YELLOW", "BLUE"}:
        raise ValueError("必须为红、黄、蓝三级分别配置")
    for level in value.values():
        _positive(level.get("escalate_minutes"))


# key -> (默认值, 中文说明, 校验函数)
REGISTRY: dict[str, tuple[Any, str, Callable[[Any], None]]] = {
    "dispatch.weights": (dict(DEFAULT_WEIGHTS), "派单权重：技能、距离、负荷、评分", _weights),
    "dispatch.max_distance_km": (3.0, "派单最大直线距离（公里）", _positive),
    "dispatch.max_daily_orders": (6, "护理员单日派单上限", _positive),
    "dispatch.offer_timeout_minutes": (30, "护理员接单时限（分钟）", _positive),
    "dispatch.reject_alert_threshold": (3, "连续拒单提醒阈值", _positive),
    "dispatch.top_n": (3, "推荐候选人数", _positive),
    "dispatch.min_skill_match": (0.5, "最低技能匹配度", _ratio),
    "dispatch.key_attention_factor": (0.8, "重点关注对象降权系数", _ratio),
    "order.duplicate_window_hours": (2, "同一老人重复预约检测窗口（前后小时）", _positive),
    "order.free_cancel_hours": (2, "免费取消提前量（小时）", _positive),
    "order.late_cancel_fee_ratio": (0.3, "迟取消、爽约计费比例", _ratio),
    "exec.checkin_distance_m": (200, "签到定位偏差阈值（米）", _positive),
    "exec.max_photos": (3, "单次服务现场照片上限（张）", _positive),
    "exec.duration_deviation_minutes": (30, "时长异常偏差阈值（分钟）", _positive),
    "exec.reminder_minutes": (15, "上门前提醒提前量（分钟）", _positive),
    "exec.late_tolerance_minutes": (5, "准时到达容忍时间（分钟）", _positive),
    "exec.noshow_grace_minutes": (60, "超时未签到提醒阈值（分钟）", _positive),
    "alert.sla": (
        {
            "RED": {"push_minutes": 5, "escalate_minutes": 30, "escalate_to": "street_supervisor"},
            "YELLOW": {"push_minutes": 30, "escalate_minutes": 240, "escalate_to": "community_admin"},
            "BLUE": {"push_minutes": 60, "escalate_minutes": 1440, "escalate_to": "community_admin"},
        },
        "各级预警的推送时限与升级规则", _alert_sla,
    ),
    "quality.review_window_hours": (24, "评价时限（小时）", _positive),
    "quality.low_score_threshold": (3, "触发回访的综合评分上限（星）", _positive),
    "quality.followup_hours": (48, "回访任务时限（小时）", _positive),
    "quality.key_attention_complaints": (3, "重点关注：月度投诉成立次数", _positive),
    "billing.discrepancy_ratio_limit": (0.01, "差异比例超过此值须人工复核", _ratio),
    "billing.duration_multiple": (3, "时长超过标准时长的倍数视为异常", _positive),
    "billing.sampling_ratio": (0.10, "监管抽查比例", _ratio),
    "backup.retain": (30, "备份保留版本数", _positive),
    "audit.retention_days": (365, "审计日志保留天数（不得低于 180）", lambda v: _audit_days(v)),
}


def _audit_days(value: Any) -> None:
    _positive(value)
    if value < 180:
        raise ValueError("审计日志保留期不得低于 180 天")


def get(db: Session, key: str) -> Any:
    if key not in REGISTRY:
        raise NotFound(f"未知配置项 {key}")
    row = db.get(SystemConfig, key)
    return row.value if row else REGISTRY[key][0]


def list_all(db: Session) -> list[dict]:
    rows = {r.key: r for r in db.query(SystemConfig).all()}
    result = []
    for key, (default, desc, _) in REGISTRY.items():
        row = rows.get(key)
        result.append({
            "key": key, "description": desc, "value": row.value if row else default, "default": default,
            "version": row.version if row else 0, "updated_at": row.updated_at if row else None,
        })
    return result


def set_value(db: Session, key: str, value: Any, user_id: int | None) -> tuple[Any, Any]:
    if key not in REGISTRY:
        raise NotFound(f"未知配置项 {key}")
    _, _, validator = REGISTRY[key]
    try:
        validator(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationFailed(f"配置项 {key} 不合法：{exc}") from exc
    before = get(db, key)
    row = db.get(SystemConfig, key)
    if row is None:
        row = SystemConfig(key=key, value=value, version=1, updated_by=user_id, updated_at=clock.now())
        db.add(row)
    else:
        row.value = value
        row.version += 1
        row.updated_by = user_id
        row.updated_at = clock.now()
    return before, value
