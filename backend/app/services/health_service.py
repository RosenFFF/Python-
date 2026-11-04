"""健康数据接入与预警：数据校验入库、规则判定、分级推送、超时升级、闭环。"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock, crypto
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.domain.alert_rules import RuleSpec, evaluate_baseline, evaluate_consecutive, evaluate_no_data, evaluate_threshold
from app.domain.enums import (
    AlertConclusion,
    AlertLevel,
    AlertStatus,
    HealthSource,
    Metric,
    RoleCode,
    RuleType,
    label,
)
from app.domain.state_machine import ALERT_FLOW
from app.models import Alert, AlertEvent, AlertRule, Device, Doctor, Elder, HealthRecord
from app.services import audit_service, config_service
from app.services import notification_service as notify
from app.services.access import Actor, ensure_alert, ensure_elder, scope_alerts

# 生理合理范围。超出的值大概率是设备故障或录入错误，拒收，不触发预警。
VALID_RANGES = {
    Metric.SYSTOLIC: (40, 300), Metric.DIASTOLIC: (20, 200), Metric.GLUCOSE: (1, 40), Metric.HEART_RATE: (20, 250),
    Metric.SPO2: (50, 100), Metric.STEPS: (0, 100000), Metric.BODY_MOTION: (0, 100000), Metric.TEMPERATURE: (30, 45),
}
METRIC_UNITS = {
    Metric.SYSTOLIC: "mmHg", Metric.DIASTOLIC: "mmHg", Metric.GLUCOSE: "mmol/L", Metric.HEART_RATE: "次/分",
    Metric.SPO2: "%", Metric.STEPS: "步", Metric.BODY_MOTION: "次", Metric.TEMPERATURE: "℃",
}
# 累计型指标做基线判定没有意义，只对体征指标启用
BASELINE_METRICS = {Metric.HEART_RATE, Metric.SYSTOLIC, Metric.GLUCOSE, Metric.SPO2}
LEVEL_ORDER = {AlertLevel.RED: 0, AlertLevel.YELLOW: 1, AlertLevel.BLUE: 2}


def validate_point(metric: str, value: float) -> None:
    if metric not in Metric.__members__:
        raise ValidationFailed(f"未知指标：{metric}")
    low, high = VALID_RANGES[Metric(metric)]
    if not low <= value <= high:
        raise ValidationFailed(f"{label('Metric', metric)} 数值 {value:g} 超出合理范围 [{low}, {high}]")


def ingest_points(db: Session, elder: Elder, points: list[dict], source: str, *, recorded_by: int | None = None,
                  order_id: int | None = None, device_serial: str | None = None, note: str | None = None) -> dict:
    """写入一批指标后逐条判定。重复上报（相同老人、指标、时间、来源）幂等跳过。"""
    now = clock.now()
    stored, duplicates, errors, fired = 0, 0, [], []
    for point in points:
        metric, value, measured_at = point["metric"], point["value"], point.get("measured_at") or now
        try:
            validate_point(metric, value)
            if measured_at > now + timedelta(minutes=5):
                raise ValidationFailed("测量时间不能晚于当前时间")
        except ValidationFailed as exc:
            errors.append({"metric": metric, "value": value, "reason": exc.message})
            continue
        try:
            with db.begin_nested():
                db.add(HealthRecord(elder_id=elder.id, metric=metric, value=value, source=source,
                                    measured_at=measured_at, recorded_by=recorded_by, order_id=order_id,
                                    device_serial=device_serial, note=note, received_at=now))
                db.flush()
        except IntegrityError:
            duplicates += 1
            continue
        stored += 1
        fired.extend(_evaluate_point(db, elder, metric, value, measured_at, now))
    return {"stored": stored, "duplicates": duplicates, "errors": errors, "alerts": fired}


def ingest_followup(db: Session, actor: Actor, elder_id: int, points: list[dict], note: str | None) -> dict:
    elder = ensure_elder(db, actor, elder_id)
    return ingest_points(db, elder, points, HealthSource.FOLLOWUP, recorded_by=actor.id, note=note)


def ingest_wearable_batch(db: Session, records: list[dict]) -> dict:
    """可穿戴批量上报：按设备序列号定位老人。未绑定设备的数据拒收。"""
    serials = {r["device_serial"] for r in records}
    devices = {d.serial: d for d in db.execute(select(Device).where(
        Device.serial.in_(serials), Device.is_active.is_(True))).scalars()}
    summary = {"stored": 0, "duplicates": 0, "errors": [], "alerts": []}
    now = clock.now()
    for record in records:
        device = devices.get(record["device_serial"])
        if device is None:
            summary["errors"].append({"device_serial": record["device_serial"], "reason": "设备未绑定或已停用"})
            continue
        elder = db.get(Elder, device.elder_id)
        if elder is None or elder.is_deleted:
            summary["errors"].append({"device_serial": record["device_serial"], "reason": "老人档案已失效"})
            continue
        result = ingest_points(db, elder, [record], HealthSource.WEARABLE, device_serial=device.serial)
        device.last_seen_at = now
        for key in ("stored", "duplicates"):
            summary[key] += result[key]
        summary["errors"].extend(result["errors"])
        summary["alerts"].extend(result["alerts"])
    return summary


def _active_rules(db: Session, metric: str) -> list[AlertRule]:
    return list(db.execute(select(AlertRule).where(AlertRule.is_active.is_(True), AlertRule.metric == metric)).scalars())


def _spec(rule: AlertRule) -> RuleSpec:
    return RuleSpec(rule.rule_type, rule.metric, rule.op, rule.threshold, rule.window, rule.hours)


def _evaluate_point(db: Session, elder: Elder, metric: str, value: float, measured_at: datetime, now: datetime) -> list[int]:
    fired: list[int] = []
    threshold_hit = False
    rules = _active_rules(db, metric)
    # 先判越界，越界已报警时不再叠加基线预警，一次事件不重复打扰
    for rule in sorted(rules, key=lambda r: {RuleType.THRESHOLD: 0, RuleType.CONSECUTIVE: 1}.get(r.rule_type, 2)):
        if rule.rule_type == RuleType.THRESHOLD:
            verdict = evaluate_threshold(_spec(rule), value)
            threshold_hit = threshold_hit or verdict.triggered
        elif rule.rule_type == RuleType.CONSECUTIVE:
            recent = db.execute(select(HealthRecord.value).where(
                HealthRecord.elder_id == elder.id, HealthRecord.metric == metric)
                .order_by(HealthRecord.measured_at.desc()).limit(rule.window or 3)).scalars().all()
            verdict = evaluate_consecutive(_spec(rule), list(recent))
        elif rule.rule_type == RuleType.BASELINE:
            if threshold_hit or metric not in BASELINE_METRICS:
                continue
            history = db.execute(select(HealthRecord.value).where(
                HealthRecord.elder_id == elder.id, HealthRecord.metric == metric,
                HealthRecord.measured_at < measured_at)
                .order_by(HealthRecord.measured_at.desc()).limit(30)).scalars().all()
            verdict = evaluate_baseline(_spec(rule), value, list(history))
        else:
            continue
        if verdict.triggered:
            unit = METRIC_UNITS.get(Metric(metric), "")
            alert = raise_alert(db, elder, rule, f"{label('Metric', metric)}：{verdict.message} {unit}".strip(),
                                verdict.value, now)
            fired.append(alert.id)
    return fired


def _next_alert_no(db: Session, now: datetime) -> str:
    last = db.execute(select(func.max(Alert.id))).scalar() or 0
    return f"AL{now:%Y%m%d}{last + 1:06d}"


def _snapshot(rule: AlertRule) -> dict:
    return {"code": rule.code, "name": rule.name, "rule_type": rule.rule_type, "metric": rule.metric, "op": rule.op,
            "threshold": rule.threshold, "window": rule.window, "hours": rule.hours, "level": rule.level,
            "version": rule.version}


def raise_alert(db: Session, elder: Elder, rule: AlertRule, message: str, value: float | None,
                now: datetime | None = None) -> Alert:
    """生成预警。同一老人同一规则已有未闭环预警时只累加次数，不重复轰炸。"""
    now = now or clock.now()
    existing = db.execute(select(Alert).where(
        Alert.elder_id == elder.id, Alert.rule_id == rule.id, Alert.status != AlertStatus.CLOSED)).scalar()
    if existing:
        existing.occurrences += 1
        existing.last_triggered_at = now
        existing.trigger_value = value
        existing.message = message
        db.add(AlertEvent(alert_id=existing.id, kind="REPEAT", detail=message, at=now))
        return existing
    alert = Alert(alert_no=_next_alert_no(db, now), elder_id=elder.id, grid_id=elder.grid_id, rule_id=rule.id,
                  rule_snapshot=_snapshot(rule), level=rule.level, title=rule.name, message=message,
                  trigger_value=value, triggered_at=now, last_triggered_at=now)
    db.add(alert)
    db.flush()
    db.add(AlertEvent(alert_id=alert.id, kind="TRIGGER", detail=message, at=now))
    _push(db, alert, elder, now)
    return alert


def _push(db: Session, alert: Alert, elder: Elder, now: datetime) -> None:
    """分级推送。红色走站内 + 短信，推送网格员、紧急联系人、签约医生；黄色站内推网格员、家属；蓝色站内推网格员。"""
    level_label = label("AlertLevel", alert.level)
    masked = crypto.mask_name(elder.name)
    title = f"{level_label}预警：{alert.title}"
    body = f"{masked}（{elder.code}）{alert.message}"
    ref = {"ref_type": "alert", "ref_id": alert.id, "level": alert.level}
    grid_users = notify.users_with_role(db, RoleCode.GRID_WORKER, grid_id=elder.grid_id)
    notify.notify_users(db, grid_users, title, body, **ref)
    recipients = [f"网格员 {u.display_name}（站内）" for u in grid_users]
    if alert.level in (AlertLevel.RED, AlertLevel.YELLOW):
        family = notify.family_users(db, elder.id)
        notify.notify_users(db, family, title, f"{masked} {alert.message}，社区正在处理", **ref)
        recipients += [f"家属 {u.display_name}（站内）" for u in family]
    if alert.level == AlertLevel.RED:
        for user in grid_users:
            notify.sms(db, user.phone, user.display_name, title, body, role="grid_worker", **ref)
            recipients.append(f"网格员 {user.display_name}（短信）")
        # 短信不含具体体征数值，降低短信泄露的影响
        family_text = f"您的家人{masked}触发红色健康预警，社区网格员正在处置，请保持电话畅通。"
        if notify.sms(db, elder.emergency_phone, elder.emergency_name, title, family_text, role="emergency_contact", **ref):
            recipients.append(f"紧急联系人 {elder.emergency_name}（短信）")
        doctor = db.get(Doctor, elder.doctor_id) if elder.doctor_id else None
        if doctor and notify.sms(db, doctor.phone, doctor.name, title, body, role="doctor", **ref):
            recipients.append(f"签约医生 {doctor.name}（短信）")
        notify.flush_outbox(db, "alert", alert.id)
    alert.pushed_at = now
    db.add(AlertEvent(alert_id=alert.id, kind="PUSH", detail="已推送：" + "、".join(recipients or ["无可用接收人"]), at=now))


def trigger_manual(db: Session, elder: Elder, rule_code: str, message: str) -> Alert:
    """SOS、现场异常等人工触发。"""
    rule = db.execute(select(AlertRule).where(AlertRule.code == rule_code)).scalar()
    if rule is None:
        raise NotFound(f"预警规则 {rule_code} 不存在")
    return raise_alert(db, elder, rule, message, None)


def trigger_sos(db: Session, actor: Actor, elder_id: int, note: str | None = None) -> Alert:
    elder = ensure_elder(db, actor, elder_id)
    message = f"{actor.name} 一键呼救" + (f"：{note}" if note else "")
    alert = trigger_manual(db, elder, "SOS", message)
    audit_service.write(db, actor.ctx, "SOS", "elder", elder.id, {"alert": alert.alert_no})
    return alert


def scan_no_data(db: Session, now: datetime | None = None) -> int:
    """定时扫描长时间无数据。预警闭环后一个判定周期内不再重复触发。"""
    now = now or clock.now()
    rules = db.execute(select(AlertRule).where(
        AlertRule.is_active.is_(True), AlertRule.rule_type == RuleType.NO_DATA)).scalars().all()
    if not rules:
        return 0
    last_seen = dict(db.execute(select(HealthRecord.elder_id, func.max(HealthRecord.measured_at))
                                .group_by(HealthRecord.elder_id)).all())
    elders = db.execute(select(Elder).where(Elder.is_deleted.is_(False))).scalars().all()
    created = 0
    for rule in rules:
        window = timedelta(hours=rule.hours or 48)
        for elder in elders:
            verdict = evaluate_no_data(_spec(rule), last_seen.get(elder.id), elder.created_at, now)
            if not verdict.triggered:
                continue
            if db.execute(select(exists().where(
                    Alert.elder_id == elder.id, Alert.rule_id == rule.id, Alert.status != AlertStatus.CLOSED))).scalar():
                continue
            if db.execute(select(exists().where(
                    Alert.elder_id == elder.id, Alert.rule_id == rule.id, Alert.closed_at > now - window))).scalar():
                continue
            raise_alert(db, elder, rule, verdict.message, verdict.value, now)
            created += 1
    return created


def scan_escalations(db: Session, now: datetime | None = None) -> int:
    """到时限仍未响应的预警升级并留痕。红色升级到街道，黄色、蓝色升级到社区。"""
    now = now or clock.now()
    sla = config_service.get(db, "alert.sla")
    rows = db.execute(select(Alert).where(Alert.status == AlertStatus.OPEN, Alert.escalated_at.is_(None))).scalars().all()
    count = 0
    for alert in rows:
        policy = sla[alert.level]
        if now - alert.triggered_at < timedelta(minutes=policy["escalate_minutes"]):
            continue
        alert.escalated_at = now
        count += 1
        target = policy["escalate_to"]
        minutes = policy["escalate_minutes"]
        title = f"预警升级：{alert.title}"
        body = f"{alert.alert_no} 已 {minutes} 分钟未处置，自动升级"
        ref = {"ref_type": "alert", "ref_id": alert.id, "level": alert.level}
        if target == RoleCode.STREET_SUPERVISOR:
            notify.notify_street(db, title, body, **ref)
        else:
            notify.notify_community_admins(db, notify.community_of_elder(db, alert.elder).id, title, body, **ref)
        notify.notify_grid(db, alert.grid_id, title, body, **ref)
        db.add(AlertEvent(alert_id=alert.id, kind="ESCALATE", detail=f"{body}，升级至{label('RoleCode', target)}", at=now))
        audit_service.write(db, audit_service.SYSTEM, "ALERT_ESCALATE", "alert", alert.id, {"to": target, "level": alert.level})
    return count


def respond(db: Session, actor: Actor, alert_id: int, note: str) -> Alert:
    if not note or not note.strip():
        raise ValidationFailed("响应预警必须填写处置记录（到场或电话确认情况）")
    alert = ensure_alert(db, actor, alert_id)
    ALERT_FLOW.check(alert.status, AlertStatus.RESPONDED)
    now = clock.now()
    alert.status = AlertStatus.RESPONDED
    alert.responded_at = now
    alert.responded_by = actor.name
    alert.response_note = note
    db.add(AlertEvent(alert_id=alert.id, kind="RESPOND", actor=actor.name, detail=note, at=now))
    return alert


def close(db: Session, actor: Actor, alert_id: int, conclusion: str, *, measures: str | None = None,
          false_reason: str | None = None, hospital: str | None = None, referred_at: datetime | None = None) -> Alert:
    """闭环。三种结论各有必填项。"""
    alert = ensure_alert(db, actor, alert_id)
    ALERT_FLOW.check(alert.status, AlertStatus.CLOSED)
    if conclusion not in AlertConclusion.__members__:
        raise ValidationFailed("闭环结论必须是已处置、误报或已转诊")
    if conclusion == AlertConclusion.HANDLED and not (measures and measures.strip()):
        raise ValidationFailed("结论为「已处置」时必须记录处置措施")
    if conclusion == AlertConclusion.FALSE_ALARM and not (false_reason and false_reason.strip()):
        raise ValidationFailed("结论为「误报」时必须记录误报原因")
    if conclusion == AlertConclusion.REFERRED and not (hospital and hospital.strip() and referred_at):
        raise ValidationFailed("结论为「已转诊」时必须记录转诊医院和时间")
    now = clock.now()
    if alert.responded_at is None:
        alert.responded_at = now
        alert.responded_by = actor.name
    alert.status = AlertStatus.CLOSED
    alert.closed_at = now
    alert.closed_by = actor.name
    alert.closed_by_id = actor.id
    alert.handle_minutes = int((now - alert.triggered_at).total_seconds() // 60)
    alert.conclusion = conclusion
    alert.measures, alert.false_reason, alert.hospital, alert.referred_at = measures, false_reason, hospital, referred_at
    detail = f"结论：{label('AlertConclusion', conclusion)}；" + (measures or false_reason or hospital or "")
    db.add(AlertEvent(alert_id=alert.id, kind="CLOSE", actor=actor.name, detail=detail, at=now))
    return alert


def alert_view(alert: Alert, with_events: bool = False) -> dict:
    now = clock.now()
    sla_open = alert.status == AlertStatus.OPEN
    view = {
        "id": alert.id, "alert_no": alert.alert_no, "elder_id": alert.elder_id, "elder_name": alert.elder.name,
        "elder_code": alert.elder.code, "grid_id": alert.grid_id,
        "grid_name": alert.elder.grid.name if alert.elder.grid else None, "level": alert.level,
        "level_label": label("AlertLevel", alert.level), "title": alert.title, "message": alert.message,
        "rule_type": alert.rule_snapshot.get("rule_type"),
        "rule_type_label": label("RuleType", alert.rule_snapshot.get("rule_type")), "occurrences": alert.occurrences,
        "status": alert.status, "status_label": label("AlertStatus", alert.status), "triggered_at": alert.triggered_at,
        "pushed_at": alert.pushed_at, "responded_at": alert.responded_at, "responded_by": alert.responded_by,
        "response_note": alert.response_note, "escalated_at": alert.escalated_at, "closed_at": alert.closed_at,
        "closed_by": alert.closed_by, "handle_minutes": alert.handle_minutes, "conclusion": alert.conclusion,
        "conclusion_label": label("AlertConclusion", alert.conclusion), "measures": alert.measures,
        "false_reason": alert.false_reason, "hospital": alert.hospital, "referred_at": alert.referred_at,
        "open_minutes": int((now - alert.triggered_at).total_seconds() // 60) if sla_open else None,
    }
    if with_events:
        view["events"] = [{"kind": e.kind, "actor": e.actor, "detail": e.detail, "at": e.at} for e in alert.events]
        view["rule_snapshot"] = alert.rule_snapshot
    return view


def list_alerts(db: Session, actor: Actor, *, status: str | None = None, level: str | None = None,
                only_open: bool = False, escalated: bool = False, elder_id: int | None = None,
                page: int = 1, size: int = 30) -> dict:
    stmt = scope_alerts(actor, select(Alert))
    if status:
        stmt = stmt.where(Alert.status == status)
    if only_open:
        stmt = stmt.where(Alert.status != AlertStatus.CLOSED)
    if level:
        stmt = stmt.where(Alert.level == level)
    if escalated:
        stmt = stmt.where(Alert.escalated_at.is_not(None))
    if elder_id:
        stmt = stmt.where(Alert.elder_id == elder_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    # 红色未闭环置顶，其次黄色、蓝色，已闭环靠后
    from sqlalchemy import case
    open_first = case((Alert.status == AlertStatus.CLOSED, 1), else_=0)
    severity = case((Alert.level == AlertLevel.RED, 0), (Alert.level == AlertLevel.YELLOW, 1), else_=2)
    rows = db.execute(stmt.order_by(open_first, severity, Alert.triggered_at.desc())
                      .limit(size).offset((page - 1) * size)).scalars().all()
    return {"total": total, "page": page, "size": size, "items": [alert_view(a) for a in rows]}


def alert_detail(db: Session, actor: Actor, alert_id: int) -> dict:
    return alert_view(ensure_alert(db, actor, alert_id), with_events=True)


def series(db: Session, actor: Actor, elder_id: int, metric: str | None = None, days: int = 30) -> dict:
    ensure_elder(db, actor, elder_id)
    since = clock.now() - timedelta(days=days)
    stmt = select(HealthRecord).where(HealthRecord.elder_id == elder_id, HealthRecord.measured_at >= since)
    if metric:
        stmt = stmt.where(HealthRecord.metric == metric)
    grouped: dict[str, list[dict]] = {}
    for row in db.execute(stmt.order_by(HealthRecord.measured_at)).scalars():
        grouped.setdefault(row.metric, []).append({"t": row.measured_at, "v": row.value, "source": row.source})
    summary = {}
    for name, points in grouped.items():
        values = [p["v"] for p in points]
        summary[name] = {
            "label": label("Metric", name), "unit": METRIC_UNITS.get(Metric(name), ""), "count": len(values),
            "latest": values[-1], "avg": round(statistics.fmean(values), 1), "min": min(values), "max": max(values),
        }
    return {"series": grouped, "summary": summary}


def rule_view(rule: AlertRule) -> dict:
    return {
        "id": rule.id, "code": rule.code, "name": rule.name, "rule_type": rule.rule_type,
        "rule_type_label": label("RuleType", rule.rule_type), "metric": rule.metric,
        "metric_label": label("Metric", rule.metric), "op": rule.op, "threshold": rule.threshold, "window": rule.window,
        "hours": rule.hours, "level": rule.level, "level_label": label("AlertLevel", rule.level),
        "is_active": rule.is_active, "version": rule.version, "is_system": rule.is_system,
    }


def list_rules(db: Session) -> list[dict]:
    return [rule_view(r) for r in db.execute(select(AlertRule).order_by(AlertRule.id)).scalars()]


def _validate_rule(data: dict) -> None:
    rule_type = data.get("rule_type")
    if rule_type not in RuleType.__members__:
        raise ValidationFailed("规则类型不合法")
    if data.get("level") not in AlertLevel.__members__:
        raise ValidationFailed("预警级别必须是蓝、黄、红之一")
    if rule_type in (RuleType.THRESHOLD, RuleType.CONSECUTIVE):
        if data.get("metric") not in Metric.__members__ or data.get("op") not in (">=", "<=", ">", "<"):
            raise ValidationFailed("阈值类规则必须指定指标和比较符")
        if data.get("threshold") is None:
            raise ValidationFailed("阈值类规则必须指定阈值")
    if rule_type == RuleType.CONSECUTIVE and (data.get("window") or 0) < 2:
        raise ValidationFailed("连续次数不能少于 2")
    if rule_type == RuleType.NO_DATA and (data.get("hours") or 0) <= 0:
        raise ValidationFailed("无数据时长必须大于 0")
    if rule_type == RuleType.BASELINE and data.get("metric") not in Metric.__members__:
        raise ValidationFailed("基线规则必须指定指标")


def create_rule(db: Session, actor: Actor, data: dict) -> AlertRule:
    _validate_rule(data)
    if db.execute(select(AlertRule.id).where(AlertRule.code == data["code"])).first():
        raise ValidationFailed("规则编码已存在")
    rule = AlertRule(**data)
    db.add(rule)
    db.flush()
    audit_service.write(db, actor.ctx, "RULE_CREATE", "alert_rule", rule.id, data)
    return rule


def update_rule(db: Session, actor: Actor, rule_id: int, data: dict) -> AlertRule:
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise NotFound("预警规则不存在")
    merged = {**rule_view(rule), **data}
    _validate_rule(merged)
    before = _snapshot(rule)
    for key, value in data.items():
        setattr(rule, key, value)
    rule.version += 1
    audit_service.write(db, actor.ctx, "RULE_UPDATE", "alert_rule", rule.id, {"before": before, "after": data})
    return rule


def bind_device(db: Session, actor: Actor, elder_id: int, serial: str, kind: str = "智能手环") -> Device:
    ensure_elder(db, actor, elder_id)
    if db.execute(select(Device.id).where(Device.serial == serial)).first():
        raise ValidationFailed("设备序列号已绑定")
    device = Device(serial=serial, elder_id=elder_id, kind=kind)
    db.add(device)
    return device


def assert_can_see_health(actor: Actor) -> None:
    if not actor.has("health:read"):
        raise Forbidden("没有查看健康数据的权限")
