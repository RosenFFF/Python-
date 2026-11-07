"""预约解析与健康分析。三项能力都给出依据、只做辅助，不替人下决定：

1. 自然语言预约：一句话解析成预约表单，提交前由用户确认；
2. 风险画像：可解释的加权风险分，帮网格员决定先看谁；
3. 健康摘要：对近 30 天数据做趋势分析并生成文字摘要。

全部有本地实现，离线可用。配置 YIHU_LLM_* 后可用大模型润色摘要、解析复杂语句；
发给模型的内容不含姓名、证件号、地址等身份信息。
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.domain.enums import AlertLevel, AlertStatus, Metric, label
from app.domain.intake_nlu import parse_intake
from app.models import Alert, Elder, HealthRecord, ServiceItem, ServiceOrder
from app.services.access import Actor, ensure_elder, scope_elders
from app.services.elder_service import age_of
from app.services.health_service import METRIC_UNITS

logger = logging.getLogger("yihu.ai")
_LEVEL_POINTS = {"SEVERE": 30, "MODERATE": 20, "MILD": 8, "INTACT": 0}
_TAG_POINTS = {"独居": 12, "失智": 10, "卧床": 10, "空巢": 4, "高龄": 3}


def llm_enabled() -> bool:
    settings = get_settings()
    return bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model)


def _chat(system: str, user: str) -> str | None:
    """调用 OpenAI 兼容接口。任何失败都回退本地结果，不影响业务。"""
    settings = get_settings()
    try:
        response = httpx.post(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={"model": settings.llm_model, "temperature": 0.2,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=12,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        logger.warning("大模型调用失败，回退本地规则", exc_info=True)
        return None


def parse_booking(db: Session, text: str) -> dict:
    """自然语言预约：优先本地解析；解析不全且配置了大模型时，用大模型补全。"""
    now = clock.now()
    items = db.execute(select(ServiceItem).where(ServiceItem.is_on_shelf.is_(True))).scalars().all()
    names = [i.name for i in items]
    parsed = parse_intake(text, now, names)
    engine = "local"
    if parsed.missing and llm_enabled():
        system = ("从老人的预约语句中抽取服务项目和时间。只输出 JSON："
                  '{"item": 项目名或null, "datetime": "YYYY-MM-DD HH:MM"或null}。'
                  f"可选项目：{'、'.join(names)}。当前时间：{now:%Y-%m-%d %H:%M}。")
        content = _chat(system, text[:200])
        if content:
            match = re.search(r"\{.*\}", content, re.S)
            try:
                data = json.loads(match.group(0)) if match else {}
                if data.get("item") in names and parsed.item_name is None:
                    parsed.item_name = data["item"]
                if data.get("datetime") and parsed.scheduled_at is None:
                    from datetime import datetime
                    parsed.scheduled_at = datetime.strptime(data["datetime"], "%Y-%m-%d %H:%M")
                parsed.missing = [m for m in parsed.missing
                                  if not (m == "服务项目" and parsed.item_name)
                                  and not (m in ("日期", "时间") and parsed.scheduled_at)]
                engine = "llm"
            except (ValueError, TypeError):
                logger.warning("大模型返回无法解析")
    item = next((i for i in items if i.name == parsed.item_name), None)
    return {
        "text": text, "item_id": item.id if item else None, "item_name": parsed.item_name,
        "scheduled_at": parsed.scheduled_at, "weekly": parsed.weekly, "weekday": parsed.weekday,
        "missing": parsed.missing, "hints": parsed.hints, "engine": engine,
    }


def _trend_slope(values: list[float]) -> float:
    """最小二乘斜率（每个样本的变化量）。"""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x, mean_y = (n - 1) / 2, statistics.fmean(values)
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    return sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values)) / denominator


def risk_profile(db: Session, elder: Elder) -> dict:
    """风险分（0-100）= 能力等级 + 照护标签 + 高龄 + 慢病 + 未闭环预警 + 30 天预警频次。

    每一项都列出分值和原因。这不是医学诊断，只用于网格员安排巡访的先后。
    """
    now = clock.now()
    factors: list[dict] = []

    def add(name: str, points: float, detail: str) -> None:
        if points:
            factors.append({"name": name, "points": round(points, 1), "detail": detail})

    add("能力等级", _LEVEL_POINTS.get(elder.disability_level, 0), label("DisabilityLevel", elder.disability_level))
    tag_points = {t: _TAG_POINTS[t] for t in elder.care_tags or [] if t in _TAG_POINTS}
    add("照护标签", min(sum(tag_points.values()), 20), "、".join(tag_points))
    age = age_of(elder.birth_date, now.date())
    add("年龄", 10 if age >= 85 else 6 if age >= 75 else 0, f"{age} 岁")
    diseases = elder.chronic_diseases or []
    add("慢性病", min(len(diseases) * 3, 10), "、".join(diseases))
    open_alerts = dict(db.execute(select(Alert.level, func.count()).where(
        Alert.elder_id == elder.id, Alert.status != AlertStatus.CLOSED).group_by(Alert.level)).all())
    red, yellow, blue = (open_alerts.get(level, 0) for level in (AlertLevel.RED, AlertLevel.YELLOW, AlertLevel.BLUE))
    add("未闭环预警", min(red * 20 + yellow * 8 + blue * 3, 25), f"红 {red} 黄 {yellow} 蓝 {blue}")
    recent = db.execute(select(func.count()).select_from(Alert).where(
        Alert.elder_id == elder.id, Alert.triggered_at >= now - timedelta(days=30))).scalar() or 0
    add("近30天预警", min(recent * 2, 10), f"{recent} 次")
    score = min(round(sum(f["points"] for f in factors)), 100)
    tier = "高" if score >= 60 else "中" if score >= 35 else "低"
    return {"elder_id": elder.id, "elder_name": elder.name, "score": score, "tier": tier,
            "factors": sorted(factors, key=lambda f: -f["points"]),
            "disclaimer": "辅助排序参考，不是医学诊断"}


def top_risk(db: Session, actor: Actor, limit: int = 10) -> list[dict]:
    elders = db.execute(scope_elders(actor, select(Elder)).where(Elder.is_deleted.is_(False))).scalars().all()
    profiles = [risk_profile(db, e) for e in elders]
    profiles.sort(key=lambda p: -p["score"])
    return profiles[:limit]


def _metric_lines(db: Session, elder: Elder) -> list[dict]:
    since = clock.now() - timedelta(days=30)
    grouped: dict[str, list[float]] = {}
    for metric, value in db.execute(select(HealthRecord.metric, HealthRecord.value).where(
            HealthRecord.elder_id == elder.id, HealthRecord.measured_at >= since)
            .order_by(HealthRecord.measured_at)).all():
        grouped.setdefault(metric, []).append(value)
    lines = []
    for metric, values in grouped.items():
        if metric in (Metric.STEPS, Metric.BODY_MOTION):
            continue
        slope = _trend_slope(values)
        spread = statistics.pstdev(values) if len(values) > 1 else 0
        # 总变化超过波动幅度才算趋势，否则视作正常波动
        total_change = slope * (len(values) - 1)
        direction = "平稳"
        if abs(total_change) > max(spread, 1e-6) and len(values) >= 5:
            direction = "上升" if total_change > 0 else "下降"
        lines.append({
            "metric": label("Metric", metric), "unit": METRIC_UNITS.get(Metric(metric), ""), "count": len(values),
            "avg": round(statistics.fmean(values), 1), "latest": values[-1], "min": min(values), "max": max(values),
            "trend": direction,
        })
    return lines


def health_summary(db: Session, actor: Actor, elder_id: int) -> dict:
    elder = ensure_elder(db, actor, elder_id)
    lines = _metric_lines(db, elder)
    now = clock.now()
    alerts = db.execute(select(Alert).where(Alert.elder_id == elder.id, Alert.triggered_at >= now - timedelta(days=30))
                        .order_by(Alert.triggered_at.desc())).scalars().all()
    counts = {level: sum(a.level == level for a in alerts) for level in AlertLevel}
    parts: list[str] = []
    if not lines:
        parts.append("近 30 天没有体征数据，建议尽快安排随访测量。")
    else:
        for line in lines:
            parts.append(f"{line['metric']}均值 {line['avg']:g}{line['unit']}，最新 {line['latest']:g}，"
                         f"区间 {line['min']:g}-{line['max']:g}，趋势{line['trend']}。")
    if alerts:
        parts.append(f"近 30 天共 {len(alerts)} 条预警（红 {counts[AlertLevel.RED]}、黄 {counts[AlertLevel.YELLOW]}、"
                     f"蓝 {counts[AlertLevel.BLUE]}）。")
    rising = [line["metric"] for line in lines if line["trend"] == "上升" and line["metric"] in ("收缩压", "血糖", "心率")]
    falling = [line["metric"] for line in lines if line["trend"] == "下降" and line["metric"] == "血氧"]
    suggestions = []
    if rising:
        suggestions.append(f"{'、'.join(rising)}呈上升趋势，建议提醒签约医生关注")
    if falling:
        suggestions.append("血氧呈下降趋势，建议尽快复测并询问症状")
    if not suggestions and lines:
        suggestions.append("指标总体平稳，保持常规随访")
    text = "".join(parts)
    engine = "local"
    if llm_enabled() and lines:
        # 只发统计值，不发姓名、证件号、地址
        polished = _chat("你是社区养老护理助手。把以下老人健康统计改写成给网格员看的简短摘要，"
                         "不超过 120 字，不作诊断，不编造数据。",
                         json.dumps({"lines": lines, "alerts": {str(k): v for k, v in counts.items()}}, ensure_ascii=False))
        if polished:
            text, engine = polished, "llm"
    return {"elder_id": elder.id, "summary": text, "suggestions": suggestions, "metrics": lines, "engine": engine,
            "risk": risk_profile(db, elder), "disclaimer": "由系统自动生成，仅供参考，不构成诊疗意见"}


def recent_orders_for(db: Session, elder_id: int, limit: int = 5) -> list[ServiceOrder]:
    return list(db.execute(select(ServiceOrder).where(ServiceOrder.elder_id == elder_id)
                           .order_by(ServiceOrder.scheduled_start.desc()).limit(limit)).scalars())
