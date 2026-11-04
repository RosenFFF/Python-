"""统计看板。按角色组合指标，口径集中定义在这里和 domain/stats_calc.py。

口径说明：
- 服务量：统计区间内完成（completed_at 落在区间）的工单数。
- 覆盖率：区间内至少完成 1 单服务的老人数 / 当前有效档案的老人数。
- 环比：对比紧邻的上一周期（整自然月对比上一整月）；同比：对比去年同期。基期为 0 时不计算。
- 预警响应中位数：首次响应时间 − 触发时间（分钟）的中位数；用中位数避免个别极端值拉偏均值。
- 预警及时率：SLA 时限内得到响应的预警占比。未响应且已超时的计入分母。
- 收入：完成工单的费用明细金额合计（含补贴），不含被剔除的明细和退费以外的调整。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import Forbidden, ValidationFailed
from app.domain import stats_calc as sc
from app.domain.enums import AlertLevel, AlertStatus, DataScope, ExceptionStatus, FollowupStatus, OrderStatus, label
from app.models import (
    Alert,
    Caregiver,
    Community,
    Elder,
    FeeDetail,
    FollowupTask,
    Grid,
    Organization,
    Review,
    ServiceException,
    ServiceItem,
    ServiceOrder,
)
from app.services import config_service
from app.services.access import Actor, scope_alerts, scope_elders, scope_orders
from app.services.quality_service import performance


@dataclass(frozen=True)
class Range:
    period: sc.Period
    granularity: str

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.period.start, datetime.min.time())

    @property
    def end_dt(self) -> datetime:
        return datetime.combine(self.period.end + timedelta(days=1), datetime.min.time())


def make_range(start: date | None, end: date | None, granularity: str | None) -> Range:
    today = clock.now().date()
    end = end or today
    start = start or end - timedelta(days=29)
    if start > end:
        raise ValidationFailed("开始日期不能晚于结束日期")
    if (end - start).days > 800:
        raise ValidationFailed("统计区间不能超过 800 天")
    if granularity is None:
        days = (end - start).days
        granularity = "day" if days <= 45 else "week" if days <= 180 else "month"
    if granularity not in ("day", "week", "month"):
        raise ValidationFailed("统计粒度必须是 day、week、month")
    return Range(sc.Period(start, end), granularity)


def _completed_orders(db: Session, actor: Actor, start: datetime, end: datetime):
    stmt = scope_orders(actor, select(ServiceOrder.id, ServiceOrder.elder_id, ServiceOrder.item_id, ServiceOrder.grid_id,
                                      ServiceOrder.community_id, ServiceOrder.org_id, ServiceOrder.caregiver_id,
                                      ServiceOrder.completed_at))
    return db.execute(stmt.where(ServiceOrder.status == OrderStatus.COMPLETED,
                                 ServiceOrder.completed_at >= start, ServiceOrder.completed_at < end)).all()


def _count_completed(db: Session, actor: Actor, period: sc.Period) -> int:
    start = datetime.combine(period.start, datetime.min.time())
    end = datetime.combine(period.end + timedelta(days=1), datetime.min.time())
    stmt = scope_orders(actor, select(func.count()).select_from(ServiceOrder)).where(
        ServiceOrder.status == OrderStatus.COMPLETED, ServiceOrder.completed_at >= start, ServiceOrder.completed_at < end)
    return db.execute(stmt).scalar() or 0


def _kpi(name: str, value, unit: str = "", previous=None, year_ago=None, *, hint: str | None = None,
         lower_better: bool = False) -> dict:
    return {
        "name": name, "value": value, "unit": unit, "mom": sc.growth(value, previous) if value is not None else None,
        "yoy": sc.growth(value, year_ago) if value is not None else None, "previous": previous, "year_ago": year_ago,
        "hint": hint, "lower_better": lower_better,
    }


def _fee_stmt(actor: Actor, start: datetime, end: datetime):
    stmt = select(FeeDetail).join(ServiceOrder, FeeDetail.order_id == ServiceOrder.id)
    stmt = scope_orders(actor, stmt)
    return stmt.where(FeeDetail.excluded.is_(False), FeeDetail.service_start >= start, FeeDetail.service_start < end)


def _fee_totals(db: Session, actor: Actor, period: sc.Period) -> dict[str, int]:
    start = datetime.combine(period.start, datetime.min.time())
    end = datetime.combine(period.end + timedelta(days=1), datetime.min.time())
    stmt = select(func.coalesce(func.sum(FeeDetail.amount), 0), func.coalesce(func.sum(FeeDetail.gov_amount), 0),
                  func.coalesce(func.sum(FeeDetail.ltci_amount), 0), func.coalesce(func.sum(FeeDetail.self_amount), 0)
                  ).select_from(FeeDetail).join(ServiceOrder, FeeDetail.order_id == ServiceOrder.id)
    stmt = scope_orders(actor, stmt).where(FeeDetail.excluded.is_(False), FeeDetail.service_start >= start,
                                           FeeDetail.service_start < end)
    amount, gov, ltci, self_pay = db.execute(stmt).one()
    return {"amount": int(amount), "gov": int(gov), "ltci": int(ltci), "self": int(self_pay)}


def alert_timeliness(db: Session, actor: Actor, period: sc.Period, now: datetime) -> dict:
    start = datetime.combine(period.start, datetime.min.time())
    end = datetime.combine(period.end + timedelta(days=1), datetime.min.time())
    rows = db.execute(scope_alerts(actor, select(
        Alert.level, Alert.triggered_at, Alert.responded_at, Alert.pushed_at, Alert.status, Alert.closed_at,
        Alert.conclusion)).where(Alert.triggered_at >= start, Alert.triggered_at < end)).all()
    sla = config_service.get(db, "alert.sla")
    response_minutes: list[float] = []
    red_minutes: list[float] = []
    on_time = closed = 0
    push_on_time = red_total = 0
    for row in rows:
        limit = timedelta(minutes=sla[row.level]["escalate_minutes"])
        if row.responded_at:
            elapsed = row.responded_at - row.triggered_at
            response_minutes.append(elapsed.total_seconds() / 60)
            if row.level == AlertLevel.RED:
                red_minutes.append(elapsed.total_seconds() / 60)
            on_time += elapsed <= limit
        if row.status == AlertStatus.CLOSED:
            closed += 1
        if row.level == AlertLevel.RED:
            red_total += 1
            if row.pushed_at and row.pushed_at - row.triggered_at <= timedelta(minutes=sla["RED"]["push_minutes"]):
                push_on_time += 1
    judged = sum(1 for r in rows if r.responded_at or now - r.triggered_at > timedelta(
        minutes=sla[r.level]["escalate_minutes"]))
    return {
        "total": len(rows), "closed": closed, "close_rate": sc.safe_ratio(closed, len(rows)),
        "median_response_minutes": sc.median_or_none(response_minutes),
        "red_median_response_minutes": sc.median_or_none(red_minutes),
        "p90_response_minutes": sc.percentile(response_minutes, 0.9),
        "on_time_rate": sc.safe_ratio(on_time, judged), "red_push_on_time_rate": sc.safe_ratio(push_on_time, red_total),
    }


def community_dashboard(db: Session, actor: Actor, rng: Range) -> dict:
    """社区端：服务量、覆盖率、待办工单、预警处置时效。"""
    now = clock.now()
    rows = _completed_orders(db, actor, rng.start_dt, rng.end_dt)
    previous, year_ago = rng.period.previous(), rng.period.year_ago()
    current_count = len(rows)
    elders_total = db.execute(scope_elders(actor, select(func.count()).select_from(Elder)).where(
        Elder.is_deleted.is_(False))).scalar() or 0
    served = len({r.elder_id for r in rows})
    prev_rows = _completed_orders(db, actor, datetime.combine(previous.start, datetime.min.time()),
                                  datetime.combine(previous.end + timedelta(days=1), datetime.min.time()))
    prev_served = len({r.elder_id for r in prev_rows})
    pending = dict(db.execute(scope_orders(actor, select(ServiceOrder.status, func.count())).where(
        ServiceOrder.status.in_((OrderStatus.PENDING_ACCEPT, OrderStatus.PENDING_DISPATCH))
    ).group_by(ServiceOrder.status)).all())
    open_exceptions = db.execute(select(func.count()).select_from(ServiceException).where(
        ServiceException.status != ExceptionStatus.CLOSED,
        ServiceException.order_id.in_(scope_orders(actor, select(ServiceOrder.id))))).scalar() or 0
    open_alerts = dict(db.execute(scope_alerts(actor, select(Alert.level, func.count())).where(
        Alert.status != AlertStatus.CLOSED).group_by(Alert.level)).all())
    open_followups = db.execute(select(func.count()).select_from(FollowupTask).where(
        FollowupTask.status == FollowupStatus.OPEN,
        FollowupTask.order_id.in_(scope_orders(actor, select(ServiceOrder.id))))).scalar() or 0
    timeliness = alert_timeliness(db, actor, rng.period, now)
    prev_time = alert_timeliness(db, actor, previous, now)
    year_time = alert_timeliness(db, actor, year_ago, now)
    coverage = sc.safe_ratio(served, elders_total)
    prev_coverage = sc.safe_ratio(prev_served, elders_total)
    todo = pending.get(OrderStatus.PENDING_ACCEPT, 0) + pending.get(OrderStatus.PENDING_DISPATCH, 0)

    trend_keys = sc.bucket_range(rng.period.start, rng.period.end, rng.granularity)
    trend_counter = Counter(sc.bucket_key(r.completed_at, rng.granularity) for r in rows)
    alert_rows = db.execute(scope_alerts(actor, select(Alert.level, Alert.triggered_at)).where(
        Alert.triggered_at >= rng.start_dt, Alert.triggered_at < rng.end_dt)).all()
    alert_trend = Counter(sc.bucket_key(r.triggered_at, rng.granularity) for r in alert_rows)
    item_names = dict(db.execute(select(ServiceItem.id, ServiceItem.name)).all())
    item_counter = Counter(item_names.get(r.item_id, "未知") for r in rows)
    grid_names = dict(db.execute(select(Grid.id, Grid.name)).all())
    grid_counter = Counter(grid_names.get(r.grid_id, "未知") for r in rows)
    level_counter = Counter(r.level for r in alert_rows)

    return {
        "role": "community", "range": _range_view(rng),
        "kpis": [
            _kpi("服务量", current_count, "单", len(prev_rows), _count_completed(db, actor, year_ago)),
            _kpi("服务覆盖率", coverage, "ratio", prev_coverage, None, hint=f"{served}/{elders_total} 位老人"),
            _kpi("待办工单", todo, "单", hint=f"待受理 {pending.get(OrderStatus.PENDING_ACCEPT, 0)}，"
                                            f"待派单 {pending.get(OrderStatus.PENDING_DISPATCH, 0)}，异常 {open_exceptions}"),
            _kpi("预警响应中位数", timeliness["median_response_minutes"], "分钟",
                 prev_time["median_response_minutes"], year_time["median_response_minutes"], lower_better=True,
                 hint=f"红色 {timeliness['red_median_response_minutes'] or '—'} 分钟"),
            _kpi("预警及时率", timeliness["on_time_rate"], "ratio", prev_time["on_time_rate"], year_time["on_time_rate"]),
            _kpi("未闭环预警", sum(open_alerts.values()), "条",
                 hint=f"红 {open_alerts.get('RED', 0)} 黄 {open_alerts.get('YELLOW', 0)} "
                      f"蓝 {open_alerts.get('BLUE', 0)}，回访 {open_followups}"),
        ],
        "charts": {
            "trend": {"title": "服务量与预警趋势", "x": trend_keys, "series": [
                {"name": "服务量", "data": [trend_counter.get(k, 0) for k in trend_keys]},
                {"name": "预警数", "data": [alert_trend.get(k, 0) for k in trend_keys]},
            ]},
            "share": {"title": "服务项目占比", "data": sc.with_share(
                [{"name": k, "value": v} for k, v in item_counter.most_common()])},
            "ranking": {"title": "网格服务量排行", "data": [{"name": k, "value": v} for k, v in grid_counter.most_common(10)]},
            "distribution": {"title": "预警级别分布", "data": [
                {"name": label("AlertLevel", k), "value": level_counter.get(k, 0), "level": k}
                for k in (AlertLevel.RED, AlertLevel.YELLOW, AlertLevel.BLUE)]},
        },
        "alert_timeliness": timeliness,
    }


def org_dashboard(db: Session, actor: Actor, rng: Range) -> dict:
    """机构端：护理员负荷、绩效、项目收入结构。"""
    rows = _completed_orders(db, actor, rng.start_dt, rng.end_dt)
    previous, year_ago = rng.period.previous(), rng.period.year_ago()
    fees = _fee_totals(db, actor, rng.period)
    prev_fees, year_fees = _fee_totals(db, actor, previous), _fee_totals(db, actor, year_ago)
    fee_rows = db.execute(_fee_stmt(actor, rng.start_dt, rng.end_dt)).scalars().all()
    revenue: dict[str, int] = defaultdict(int)
    revenue_trend: dict[str, int] = defaultdict(int)
    for fee in fee_rows:
        name = fee.order.item.name if fee.order else "其他"
        revenue[name] += fee.amount
        revenue_trend[sc.bucket_key(fee.service_start, rng.granularity)] += fee.amount
    trend_keys = sc.bucket_range(rng.period.start, rng.period.end, rng.granularity)
    caregiver_names = dict(db.execute(select(Caregiver.id, Caregiver.name)).all())
    load = Counter(caregiver_names.get(r.caregiver_id, "未知") for r in rows)
    active_days = max(rng.period.days, 1)
    per_day = [count / active_days for count in load.values()]
    month = sc.month_key(rng.period.end)
    perf = performance(db, actor, month, sort="completed")
    ratings = [p["avg_rating"] for p in perf if p["avg_rating"] is not None]
    on_time = [p["on_time_rate"] for p in perf if p["on_time_rate"] is not None]
    load_values = list(load.values())
    return {
        "role": "org", "range": _range_view(rng),
        "kpis": [
            _kpi("完成工单", len(rows), "单", _count_completed(db, actor, previous), _count_completed(db, actor, year_ago)),
            _kpi("服务收入", fees["amount"], "cents", prev_fees["amount"], year_fees["amount"]),
            _kpi("在岗护理员", len(perf), "人", hint=f"有接单 {len(load)} 人"),
            _kpi("日均负荷", round(sum(per_day) / len(per_day), 2) if per_day else None, "单/人/日",
                 hint=f"负荷最高 {max(load_values) if load_values else 0} 单"),
            _kpi("月度平均评分", round(sum(ratings) / len(ratings), 2) if ratings else None, "星", hint=month),
            _kpi("月度准时率", round(sum(on_time) / len(on_time), 4) if on_time else None, "ratio", hint=month),
        ],
        "charts": {
            "trend": {"title": "收入趋势（元）", "x": trend_keys, "series": [
                {"name": "服务收入", "data": [round(revenue_trend.get(k, 0) / 100, 2) for k in trend_keys]}]},
            "share": {"title": "项目收入结构（元）", "data": sc.with_share(sorted(
                ({"name": k, "value": round(v / 100, 2)} for k, v in revenue.items()),
                key=lambda r: -r["value"]))},
            "ranking": {"title": "护理员负荷排行", "data": [{"name": k, "value": v} for k, v in load.most_common(10)]},
            "distribution": {"title": "护理员负荷分布", "data": sc.histogram(
                load_values, _load_edges(load_values), _load_labels(load_values))},
        },
        "performance": perf[:20],
    }


def _load_edges(values: list[int]) -> list[float]:
    top = max(values) if values else 10
    step = max(top // 5, 1) + (1 if top % 5 else 0)
    return [i * step for i in range(6)]


def _load_labels(values: list[int]) -> list[str]:
    edges = _load_edges(values)
    return [f"{int(edges[i])}-{int(edges[i + 1])}单" for i in range(5)]


def street_dashboard(db: Session, actor: Actor, rng: Range) -> dict:
    """街道端：各社区服务量对比、补贴支出、满意度分布。"""
    if actor.scope != DataScope.STREET:
        raise Forbidden("街道看板只面向全街道数据范围的角色")
    rows = _completed_orders(db, actor, rng.start_dt, rng.end_dt)
    previous, year_ago = rng.period.previous(), rng.period.year_ago()
    fees = _fee_totals(db, actor, rng.period)
    prev_fees, year_fees = _fee_totals(db, actor, previous), _fee_totals(db, actor, year_ago)
    community_names = dict(db.execute(select(Community.id, Community.name)).all())
    org_names = dict(db.execute(select(Organization.id, Organization.name)).all())
    counter = Counter(community_names.get(r.community_id, "未知") for r in rows)
    reviews = db.execute(select(Review.overall, ServiceOrder.community_id).join(
        ServiceOrder, Review.order_id == ServiceOrder.id).where(
        Review.created_at >= rng.start_dt, Review.created_at < rng.end_dt)).all()
    prev_reviews = db.execute(select(func.avg(Review.overall)).where(
        Review.created_at >= datetime.combine(previous.start, datetime.min.time()),
        Review.created_at < rng.start_dt)).scalar()
    year_reviews = db.execute(select(func.avg(Review.overall)).where(
        Review.created_at >= datetime.combine(year_ago.start, datetime.min.time()),
        Review.created_at < datetime.combine(year_ago.end + timedelta(days=1), datetime.min.time()))).scalar()
    scores = [r.overall for r in reviews]
    avg_score = round(sum(scores) / len(scores), 2) if scores else None
    satisfied = sc.safe_ratio(sum(1 for s in scores if s >= 4), len(scores))
    fee_rows = db.execute(_fee_stmt(actor, rng.start_dt, rng.end_dt)).scalars().all()
    subsidy_trend: dict[str, int] = defaultdict(int)
    ltci_trend: dict[str, int] = defaultdict(int)
    by_community: dict[str, int] = defaultdict(int)
    for fee in fee_rows:
        key = sc.bucket_key(fee.service_start, rng.granularity)
        subsidy_trend[key] += fee.gov_amount
        ltci_trend[key] += fee.ltci_amount
        by_community[community_names.get(fee.community_id, "未知")] += fee.gov_amount
    trend_keys = sc.bucket_range(rng.period.start, rng.period.end, rng.granularity)
    timeliness = alert_timeliness(db, actor, rng.period, clock.now())
    community_scores: dict[str, list[float]] = defaultdict(list)
    for review in reviews:
        community_scores[community_names.get(review.community_id, "未知")].append(review.overall)
    org_counter = Counter(org_names.get(r.org_id, "未知") for r in rows)
    return {
        "role": "street", "range": _range_view(rng),
        "kpis": [
            _kpi("全街道服务量", len(rows), "单", _count_completed(db, actor, previous), _count_completed(db, actor, year_ago)),
            _kpi("政府补贴支出", fees["gov"], "cents", prev_fees["gov"], year_fees["gov"]),
            _kpi("长护险支付", fees["ltci"], "cents", prev_fees["ltci"], year_fees["ltci"]),
            _kpi("平均满意度", avg_score, "星", float(prev_reviews) if prev_reviews else None,
                 float(year_reviews) if year_reviews else None, hint=f"{len(scores)} 条评价"),
            _kpi("满意率", satisfied, "ratio", hint="综合评分 ≥ 4 星"),
            _kpi("预警闭环率", timeliness["close_rate"], "ratio", hint=f"共 {timeliness['total']} 条"),
        ],
        "charts": {
            "trend": {"title": "补贴支出趋势（元）", "x": trend_keys, "series": [
                {"name": "政府补贴", "data": [round(subsidy_trend.get(k, 0) / 100, 2) for k in trend_keys]},
                {"name": "长护险", "data": [round(ltci_trend.get(k, 0) / 100, 2) for k in trend_keys]},
            ]},
            "share": {"title": "各社区补贴占比（元）", "data": sc.with_share(sorted(
                ({"name": k, "value": round(v / 100, 2)} for k, v in by_community.items()), key=lambda r: -r["value"]))},
            "ranking": {"title": "各社区服务量对比", "data": [{"name": k, "value": v} for k, v in counter.most_common()]},
            "distribution": {"title": "满意度分布", "data": sc.histogram(
                scores, [1, 2, 3, 4, 4.5, 5], ["1-2星", "2-3星", "3-4星", "4-4.5星", "4.5-5星"])},
        },
        "community_scores": [{"name": k, "value": round(sum(v) / len(v), 2), "count": len(v)}
                             for k, v in sorted(community_scores.items())],
        "org_share": [{"name": k, "value": v} for k, v in org_counter.most_common()],
    }


def _range_view(rng: Range) -> dict:
    previous, year_ago = rng.period.previous(), rng.period.year_ago()
    return {
        "start": rng.period.start, "end": rng.period.end, "granularity": rng.granularity,
        "previous": {"start": previous.start, "end": previous.end},
        "year_ago": {"start": year_ago.start, "end": year_ago.end},
    }


def dashboard(db: Session, actor: Actor, kind: str, rng: Range) -> dict:
    required = {"community": "stats:community", "org": "stats:org", "street": "stats:street"}
    if kind not in required:
        raise ValidationFailed("未知的看板类型")
    actor.require(required[kind])
    return {"community": community_dashboard, "org": org_dashboard, "street": street_dashboard}[kind](db, actor, rng)


def flatten_for_export(data: dict) -> tuple[list[str], list[list]]:
    """把看板数据摊平成明细行。"""
    rows: list[list] = []
    for kpi in data["kpis"]:
        value = kpi["value"]
        if kpi["unit"] == "cents" and value is not None:
            value, unit = round(value / 100, 2), "元"
        elif kpi["unit"] == "ratio" and value is not None:
            value, unit = f"{value:.2%}", ""
        else:
            unit = kpi["unit"]
        rows.append(["指标", kpi["name"], value, unit,
                     f"{kpi['mom']:.2%}" if kpi["mom"] is not None else "—",
                     f"{kpi['yoy']:.2%}" if kpi["yoy"] is not None else "—"])
    for chart in data["charts"].values():
        if "series" in chart:
            for series in chart["series"]:
                for x, y in zip(chart["x"], series["data"]):
                    rows.append([chart["title"], f"{series['name']} {x}", y, "", "", ""])
        else:
            for point in chart["data"]:
                rows.append([chart["title"], point["name"], point["value"], "", "", ""])
    return ["分组", "名称", "数值", "单位", "环比", "同比"], rows
