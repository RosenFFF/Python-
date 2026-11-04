"""演示数据生成。用固定随机种子，每次得到相同的数据集。

- 历史数据（约 15 个月）直接写库以求速度，金额用与线上相同的计算函数得出，统计看板和同比环比有真实基数；
- 当前待办（预约、派单、预警、投诉、账单差异）尽量走真实服务函数，状态与线上流程一致。
所有人名、证件号、手机号均为随机生成，不对应真实人员。
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock, crypto
from app.core.security import hash_password
from app.domain import billing_calc as calc
from app.domain.enums import (
    AlertConclusion,
    AlertStatus,
    BillStatus,
    ComplaintConclusion,
    ComplaintStatus,
    FeeType,
    HealthSource,
    OrderSource,
    OrderStatus,
    PaymentStatus,
    SpotCheckStatus,
)
from app.domain.stats_calc import month_key
from app.models import (
    Alert,
    AlertEvent,
    AlertRule,
    Bill,
    BillOrgConfirmation,
    Caregiver,
    Community,
    Complaint,
    Device,
    DispatchOffer,
    Doctor,
    Elder,
    ElderStatement,
    FamilyLink,
    FeeDetail,
    FollowupTask,
    Grid,
    HealthRecord,
    OrderEvent,
    Organization,
    PriceVersion,
    Review,
    Role,
    ServiceExecution,
    ServiceItem,
    ServiceOrder,
    Street,
    SubsidyPolicy,
    User,
)
from app.services import (
    audit_service,
    billing_service,
    dispatch_service,
    execution_service,
    health_service,
    order_service,
)
from app.services.access import build_actor
from app.services.rbac_defaults import sync_rbac
from app.services.system_defaults import sync_builtin_rules, sync_subsidy_policies

DEMO_PASSWORD = "Demo@2026"
SEED = 20261103
SURNAMES = "王李张刘陈杨黄赵周吴徐孙马胡朱郭何罗高林"
ELDER_GIVEN = ["秀英", "桂兰", "玉兰", "淑珍", "凤英", "建国", "志强", "国华", "德明", "桂芳", "素华", "明德", "长发", "春梅", "兰英", "福生", "金花", "永康", "翠花", "振华"]
STAFF_GIVEN = ["小红", "丽娟", "海燕", "晓梅", "春燕", "雪芳", "文静", "秋霞", "建军", "伟强", "玉梅", "美玲", "红霞", "志刚", "晓燕", "佳慧", "亚男", "敏华"]
DISEASES = ["高血压", "糖尿病", "冠心病", "慢阻肺", "脑梗后遗症", "骨关节炎", "阿尔茨海默病"]
DRUGS = ["氨氯地平 5mg 每日一次", "二甲双胍 0.5g 每日三次", "阿司匹林 100mg 每日一次", "阿托伐他汀 20mg 每晚", "沙美特罗吸入剂 每日两次"]
ROADS = ["青松路", "和平巷", "康宁大道", "桂花街", "百合路", "银杏路"]

ITEM_DEFS = [
    # 编码, 名称, 计价, 标准分钟, 技能, 适用等级, 纳入补贴, 比例, 单价(分), 清单
    ("SV01", "助餐", "PER_TIME", 30, ["助餐"], ["INTACT", "MILD", "MODERATE", "SEVERE"], True, 0.5, 1500, ["送餐到户", "协助进食", "餐具清洗"]),
    ("SV02", "助浴", "PER_TIME", 60, ["助浴", "失能照护"], ["MILD", "MODERATE", "SEVERE"], True, 0.5, 8000, ["助浴", "擦身", "更衣", "指甲修剪"]),
    ("SV03", "助洁", "PER_HOUR", 120, ["助洁"], ["INTACT", "MILD", "MODERATE", "SEVERE"], True, 0.4, 4000, ["客厅清洁", "卧室清洁", "厨卫清洁", "垃圾清运"]),
    ("SV04", "助医", "PER_TIME", 60, ["助医"], ["INTACT", "MILD", "MODERATE", "SEVERE"], True, 0.4, 3000, ["代取药品", "用药提醒", "血压测量"]),
    ("SV05", "康复护理", "PER_HOUR", 60, ["康复护理", "失能照护"], ["MILD", "MODERATE", "SEVERE"], True, 0.6, 12000, ["评估状况", "肢体训练", "按摩放松", "记录反馈"]),
    ("SV06", "陪同就医", "PER_HOUR", 180, ["陪同就医"], ["INTACT", "MILD", "MODERATE", "SEVERE"], True, 0.3, 6000, ["接送陪同", "协助挂号", "取药缴费", "结果告知家属"]),
    ("SV07", "代购代办", "PER_TIME", 45, ["代购代办"], ["INTACT", "MILD", "MODERATE", "SEVERE"], False, 0.0, 2000, ["确认清单", "代购物品", "票据交接"]),
    ("SV08", "精神慰藉", "PER_HOUR", 60, ["精神慰藉"], ["INTACT", "MILD", "MODERATE", "SEVERE"], True, 0.5, 3000, ["陪伴聊天", "情绪观察", "记录反馈"]),
]
COMMENTS_GOOD = ["服务很细致，老人很满意", "准时到达，态度好", "护理员很有耐心", "做得很专业，下次还约"]
COMMENTS_BAD = ["迟到了将近半小时", "服务有些敷衍", "没有按清单完成", "沟通不太耐心"]


def _id_card(rng: random.Random, birth: date, gender: str) -> str:
    order = rng.randrange(0, 500) * 2 + (1 if gender == "男" else 0)
    body = f"510104{birth:%Y%m%d}{order:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    return body + "10X98765432"[sum(int(c) * w for c, w in zip(body, weights)) % 11]


def _phone(rng: random.Random) -> str:
    return f"1{rng.choice('3589')}{rng.randrange(0, 10**9):09d}"


def _user(db: Session, username: str, display: str, role: Role, hashed: str, rng: random.Random, **kwargs) -> User:
    user = User(username=username, display_name=display, password_hash=hashed, role_id=role.id, phone=_phone(rng),
                created_at=clock.now() - timedelta(days=600), **kwargs)
    db.add(user)
    db.flush()
    return user


def _build_org(db: Session, rng: random.Random) -> dict:
    hashed = hash_password(DEMO_PASSWORD)
    roles = {r.code: r for r in db.execute(select(Role)).scalars()}
    street = Street(name="青松街道")
    db.add(street)
    db.flush()
    communities = [
        Community(street_id=street.id, name="幸福里社区", center_lat=30.6586, center_lng=104.0648, service_radius_m=3000),
        Community(street_id=street.id, name="和平苑社区", center_lat=30.6720, center_lng=104.0850, service_radius_m=3000),
        Community(street_id=street.id, name="康宁社区", center_lat=30.6430, center_lng=104.0480, service_radius_m=3000),
    ]
    db.add_all(communities)
    db.flush()
    grids: list[Grid] = []
    for community in communities:
        for suffix in ("一网格", "二网格"):
            grid = Grid(community_id=community.id, name=community.name.removesuffix("社区") + suffix)
            db.add(grid)
            grids.append(grid)
    db.flush()
    organizations = [Organization(name="颐康居家养老服务中心", contact="028-8000-1001"),
                     Organization(name="安心护理服务站", contact="028-8000-1002")]
    db.add_all(organizations)
    db.flush()
    for org in organizations:
        org.communities = list(communities)
    doctors = [Doctor(name=f"{name}医生", hospital=hospital, phone=_phone(rng))
               for name, hospital in [("周明", "青松社区卫生服务中心"), ("吴芳", "青松社区卫生服务中心"), ("郑涛", "和平苑卫生站"), ("钱蓉", "康宁卫生站")]]
    db.add_all(doctors)
    db.flush()

    admin = _user(db, "admin", "系统管理员", roles["super_admin"], hashed, rng)
    street_user = _user(db, "street_sun", "孙监管", roles["street_supervisor"], hashed, rng)
    grid_workers = {}
    for index, grid in enumerate(grids, start=1):
        names = ["张网格", "陈网格", "杨网格", "黄网格", "赵网格", "徐网格"]
        logins = ["grid_zhang", "grid_chen", "grid_yang", "grid_huang", "grid_zhao", "grid_xu"]
        grid_workers[grid.id] = _user(db, logins[index - 1], names[index - 1], roles["grid_worker"], hashed, rng,
                                      grid_id=grid.id, community_id=grid.community_id)
    for login, name, community in zip(["comm_liu", "comm_he", "comm_luo"], ["刘主任", "何主任", "罗主任"], communities):
        _user(db, login, name, roles["community_admin"], hashed, rng, community_id=community.id)
    org_admins = [_user(db, "org_zhao", "赵站长", roles["org_admin"], hashed, rng, org_id=organizations[0].id),
                  _user(db, "org_gao", "高站长", roles["org_admin"], hashed, rng, org_id=organizations[1].id)]

    all_skills = ["助餐", "助浴", "助洁", "助医", "康复护理", "陪同就医", "代购代办", "精神慰藉", "失能照护", "失智照护"]
    caregivers: list[Caregiver] = []
    for index in range(18):
        org = organizations[index % 2]
        community = communities[index % 3]
        name = SURNAMES[index] + STAFF_GIVEN[index]
        user = _user(db, f"cg{index + 1:02d}", name, roles["caregiver"], hashed, rng, org_id=org.id)
        skills = sorted(set(rng.sample(all_skills, rng.randint(4, 7))) | {"助餐", "助洁"})
        caregiver = Caregiver(user_id=user.id, org_id=org.id, name=name, phone=user.phone, skills=skills,
                              base_lat=community.center_lat + rng.uniform(-0.012, 0.012),
                              base_lng=community.center_lng + rng.uniform(-0.012, 0.012),
                              hired_on=clock.now().date() - timedelta(days=rng.randint(120, 1200)))
        db.add(caregiver)
        caregivers.append(caregiver)
    db.flush()
    # 演示护理员 cg01：技能齐全、驻点靠近幸福里，保证助浴等流程能排到前面
    first = caregivers[0]
    first.skills = sorted(set(first.skills) | {"助浴", "失能照护", "助医", "康复护理"})
    first.base_lat, first.base_lng = communities[0].center_lat + 0.004, communities[0].center_lng + 0.003
    db.flush()
    return {"roles": roles, "communities": communities, "grids": grids, "organizations": organizations, "doctors": doctors,
            "admin": admin, "street": street_user, "grid_workers": grid_workers, "org_admins": org_admins,
            "caregivers": caregivers, "hashed": hashed}


def _build_items(db: Session, rng: random.Random) -> list[ServiceItem]:
    now = clock.now()
    items: list[ServiceItem] = []
    for code, name, mode, minutes, skills, levels, eligible, ratio, price, checklist in ITEM_DEFS:
        item = ServiceItem(code=code, name=name, pricing_mode=mode, standard_minutes=minutes, required_skills=skills,
                           applicable_levels=levels, subsidy_eligible=eligible, checklist=checklist)
        db.add(item)
        db.flush()
        start = now - timedelta(days=720)
        db.add(PriceVersion(item_id=item.id, version_no=1, unit_price=int(price * 0.9), subsidy_ratio=ratio,
                            effective_from=start, effective_to=now - timedelta(days=200), reason="初始定价"))
        db.add(PriceVersion(item_id=item.id, version_no=2, unit_price=price, subsidy_ratio=ratio,
                            effective_from=now - timedelta(days=200), reason="年度调价"))
        items.append(item)
    db.flush()
    return items


def _build_elders(db: Session, rng: random.Random, world: dict) -> list[Elder]:
    now = clock.now()
    today = now.date()
    grids, doctors = world["grids"], world["doctors"]
    elders: list[Elder] = []
    levels = ["INTACT", "MILD", "MODERATE", "SEVERE"]
    for index in range(72):
        grid = grids[index % len(grids)]
        community = next(c for c in world["communities"] if c.id == grid.community_id)
        gender = rng.choice("男女")
        age = rng.randint(62, 94)
        birth = date(today.year - age, rng.randint(1, 12), rng.randint(1, 28))
        tags = sorted(set(rng.sample(["独居", "空巢", "失智", "卧床", "低保", "高龄"], rng.randint(0, 2))))
        if age >= 80 and "高龄" not in tags:
            tags.append("高龄")
        level = rng.choices(levels, weights=[35, 30, 25, 10])[0]
        difficulty = "LOW_INCOME" if "低保" in tags else "EXTREME" if rng.random() < 0.04 else "NONE"
        id_card = _id_card(rng, birth, gender)
        phone = _phone(rng)
        elder = Elder(
            code=f"E2025{index + 1:06d}", name=SURNAMES[index % 20] + ELDER_GIVEN[(index * 7 + index // 20) % 20], gender=gender, birth_date=birth,
            id_card=id_card, id_card_idx=crypto.blind_index(id_card), phone=phone, phone_idx=crypto.blind_index(phone),
            address=f"青松街道{rng.choice(ROADS)}{rng.randint(1, 200)}号{rng.randint(1, 8)}栋{rng.randint(101, 1806)}室",
            lat=community.center_lat + rng.uniform(-0.012, 0.012), lng=community.center_lng + rng.uniform(-0.012, 0.012),
            grid_id=grid.id, emergency_name=SURNAMES[(index + 7) % 20] + "晓明", emergency_relation=rng.choice(["儿子", "女儿", "配偶", "孙女"]),
            emergency_phone=_phone(rng), medical_insurance=rng.choice(["城镇职工医保", "城乡居民医保"]),
            ltc_insurance="职工长护险" if level in ("MODERATE", "SEVERE") and rng.random() < 0.7 else "未参保",
            chronic_diseases=rng.sample(DISEASES, rng.randint(0, 3)), medications=rng.sample(DRUGS, rng.randint(0, 2)),
            allergies=rng.choice([None, None, None, "青霉素", "磺胺类", "海鲜"]), disability_level=level,
            difficulty_type=difficulty, care_tags=tags, doctor_id=rng.choice(doctors).id,
            created_at=now - timedelta(days=rng.randint(480, 700)),
        )
        db.add(elder)
        elders.append(elder)
    db.flush()
    # 演示老人：赛题流程里 78 岁、中度失能、独居
    star = elders[0]
    star.name = "王秀英"
    star.gender = "女"
    star.birth_date = date(today.year - 78, 3, 16)
    star.id_card = _id_card(rng, star.birth_date, "女")
    star.id_card_idx = crypto.blind_index(star.id_card)
    star.disability_level = "MODERATE"
    star.ltc_insurance = "职工长护险"
    star.care_tags = ["独居"]
    star.difficulty_type = "NONE"
    star.chronic_diseases = ["高血压", "骨关节炎"]
    star.medications = ["氨氯地平 5mg 每日一次"]
    community = next(c for c in world["communities"] if c.id == star.grid.community_id)
    star.lat, star.lng = community.center_lat + 0.003, community.center_lng + 0.002
    star.address = "青松街道青松路18号3栋502室"

    roles = world["roles"]
    family = _user(db, "family_wang", "王晓明", roles["elder_family"], world["hashed"], rng)
    db.add(FamilyLink(user_id=family.id, elder_id=star.id, relation="儿子"))
    world["family"] = family
    for index in range(1, 12):
        user = _user(db, f"family{index:02d}", elders[index].emergency_name, roles["elder_family"], world["hashed"], rng)
        db.add(FamilyLink(user_id=user.id, elder_id=elders[index].id, relation=elders[index].emergency_relation))
    for index in range(0, 24):
        db.add(Device(serial=f"WB-{10000 + index}", elder_id=elders[index].id, last_seen_at=now - timedelta(hours=1), battery=rng.randint(30, 100)))
    db.flush()
    return elders


class _OrderNo:
    def __init__(self) -> None:
        self.value = 0

    def next(self, at: datetime) -> str:
        self.value += 1
        return f"SO{at:%Y%m%d}{self.value:06d}"


def _version_at(versions: dict[int, list[PriceVersion]], item_id: int, at: datetime) -> PriceVersion:
    for version in reversed(versions[item_id]):
        if version.effective_from <= at:
            return version
    return versions[item_id][0]


def _build_history(db: Session, rng: random.Random, world: dict, elders: list[Elder], items: list[ServiceItem]) -> None:
    now = clock.now()
    versions: dict[int, list[PriceVersion]] = {}
    for version in db.execute(select(PriceVersion).order_by(PriceVersion.version_no)).scalars():
        versions.setdefault(version.item_id, []).append(version)
    policies = {(p.disability_level, p.difficulty_type): p for p in db.execute(select(SubsidyPolicy)).scalars()}
    caregivers = world["caregivers"]
    grid_to_community = {g.id: g.community_id for g in world["grids"]}
    quality = {c.id: rng.uniform(3.7, 4.9) for c in caregivers}
    numbers = _OrderNo()
    world["numbers"] = numbers
    day = now.date() - timedelta(days=460)
    yesterday = now.date() - timedelta(days=1)
    orders: list[ServiceOrder] = []
    pending_rows: list = []
    while day <= yesterday:
        progress = (day - (now.date() - timedelta(days=460))).days / 460
        daily = rng.randint(3, 6) + int(progress * 5)
        if day.weekday() >= 5:
            daily = max(2, daily - 2)
        used: set[tuple[int, int]] = set()
        for _ in range(daily):
            elder = rng.choice(elders)
            eligible = [i for i in items if elder.disability_level in i.applicable_levels]
            item = rng.choices(eligible, weights=[6 if i.code in ("SV01", "SV03") else 3 if i.code in ("SV02", "SV04", "SV08") else 2 for i in eligible])[0]
            hour = rng.randint(8, 16)
            if (elder.id, hour) in used:
                continue
            used.add((elder.id, hour))
            start = datetime(day.year, day.month, day.day, hour, rng.choice([0, 15, 30, 45]))
            end = start + timedelta(minutes=item.standard_minutes)
            version = _version_at(versions, item.id, start)
            caregiver = rng.choice(caregivers)
            roll = rng.random()
            status = OrderStatus.COMPLETED if roll < 0.9 else OrderStatus.CANCELLED if roll < 0.975 else OrderStatus.NO_SHOW
            order = ServiceOrder(
                order_no=numbers.next(start), elder_id=elder.id, item_id=item.id, grid_id=elder.grid_id,
                community_id=grid_to_community[elder.grid_id], price_version_id=version.id, scheduled_start=start, scheduled_end=end,
                address=elder.address, lat=elder.lat, lng=elder.lng, status=status, source=rng.choice([OrderSource.ONLINE, OrderSource.ONLINE, OrderSource.PHONE, OrderSource.RECURRING]),
                priority=order_service.compute_priority(elder), created_at=start - timedelta(hours=rng.randint(6, 72)),
                caregiver_id=caregiver.id if status != OrderStatus.CANCELLED else None,
                org_id=caregiver.org_id if status != OrderStatus.CANCELLED else None,
                payment_status=PaymentStatus.UNPAID,
            )
            if status != OrderStatus.COMPLETED:
                order.cancel_reason = rng.choice(["家中临时有事", "老人身体不适改期", "家属已自行安排"]) if status == OrderStatus.CANCELLED else "上门时老人不在家"
                order.cancelled_at = start - timedelta(hours=rng.randint(3, 20)) if status == OrderStatus.CANCELLED else start + timedelta(minutes=20)
                order.cancel_chargeable = status == OrderStatus.NO_SHOW
            else:
                order.completed_at = end
            orders.append(order)
            pending_rows.append((order, elder, item, version, caregiver, status, start, end))
        day += timedelta(days=1)
    db.add_all(orders)
    db.flush()

    for order, elder, item, version, caregiver, status, start, end in pending_rows:
        db.add(OrderEvent(order_id=order.id, action="CREATE", to_status=OrderStatus.PENDING_ACCEPT, actor_name="系统", note="历史数据", at=order.created_at))
        if status == OrderStatus.CANCELLED:
            db.add(OrderEvent(order_id=order.id, action="CANCEL", from_status=OrderStatus.PENDING_ACCEPT, to_status=status, actor_name="系统", note=order.cancel_reason, at=order.cancelled_at))
            continue
        if status == OrderStatus.NO_SHOW:
            db.add(OrderEvent(order_id=order.id, action="NO_SHOW", from_status=OrderStatus.DISPATCHED, to_status=status, actor_name="系统", note=order.cancel_reason, at=order.cancelled_at))
            amount = calc.round_cents(calc.Decimal(version.unit_price) * calc.Decimal("0.3"))
            db.add(FeeDetail(month=month_key(start), fee_type=FeeType.CANCEL_FEE, elder_id=elder.id, community_id=order.community_id,
                             org_id=caregiver.org_id, order_id=order.id, price_version_id=version.id, unit_price=version.unit_price,
                             quantity=1, amount=amount, self_amount=amount, service_start=start, service_end=end, note=order.cancel_reason))
            continue
        offset = rng.choice([-10, -5, -3, 0, 2, 4, 8, 15])
        duration = max(10, item.standard_minutes + rng.randint(-12, 15))
        checkin = start + timedelta(minutes=offset)
        checkout = checkin + timedelta(minutes=duration)
        abnormal = abs(duration - item.standard_minutes) > 30
        order.completed_at = checkout
        db.add(ServiceExecution(
            order_id=order.id, caregiver_id=caregiver.id, checkin_at=checkin, checkin_lat=order.lat + rng.uniform(-0.0004, 0.0004),
            checkin_lng=order.lng + rng.uniform(-0.0004, 0.0004), checkin_distance_m=rng.uniform(5, 90), is_on_time=offset <= 5,
            checklist=[{"name": name, "done": True} for name in item.checklist], checkout_at=checkout, duration_minutes=duration,
            duration_abnormal=abnormal, summary="服务完成，老人状态良好", spot_check_status=SpotCheckStatus.PASS if abnormal else SpotCheckStatus.NONE,
        ))
        db.add(OrderEvent(order_id=order.id, action="CHECK_OUT", from_status=OrderStatus.IN_SERVICE, to_status=OrderStatus.COMPLETED, actor_name=caregiver.name, note=f"签退，服务时长 {duration} 分钟", at=checkout))
        policy = policies.get((elder.disability_level, elder.difficulty_type)) or policies[(elder.disability_level, "NONE")]
        quantity, amount = calc.service_amount(item.pricing_mode, version.unit_price, duration)
        split = calc.split_amount(amount, item.subsidy_eligible, version.subsidy_ratio, policy.gov_ratio, policy.ltci_ratio,
                                  elder.ltc_insurance not in (None, "未参保"))
        db.add(FeeDetail(month=month_key(checkout), fee_type=FeeType.SERVICE, elder_id=elder.id, community_id=order.community_id,
                         org_id=caregiver.org_id, order_id=order.id, price_version_id=version.id, unit_price=version.unit_price,
                         quantity=float(quantity), amount=amount, gov_amount=split.gov, ltci_amount=split.ltci, self_amount=split.self_pay,
                         gov_ratio=split.gov_ratio, ltci_ratio=split.ltci_ratio, service_start=checkin, service_end=checkout,
                         actual_minutes=duration, standard_minutes=item.standard_minutes))
        if rng.random() < 0.82:
            base = quality[caregiver.id]
            scores = [max(1, min(5, round(rng.gauss(base, 0.6)))) for _ in range(4)]
            overall = sum(scores) / 4
            db.add(Review(order_id=order.id, caregiver_id=caregiver.id, punctuality=scores[0], attitude=scores[1],
                          professionalism=scores[2], effectiveness=scores[3], overall=round(overall, 2),
                          comment=rng.choice(COMMENTS_GOOD if overall > 3 else COMMENTS_BAD), created_at=checkout + timedelta(hours=rng.randint(1, 20))))
    db.flush()
    _build_settled_bills(db, world)


def _build_settled_bills(db: Session, world: dict) -> None:
    """除上个月外，历史月份账单都已生效、缴费完成。"""
    now = clock.now()
    current = month_key(now)
    last = billing_service.previous_month(now.date())
    rows = db.execute(select(FeeDetail).where(FeeDetail.month != current, FeeDetail.month != last)).scalars().all()
    grouped: dict[tuple[int, str], list[FeeDetail]] = {}
    for fee in rows:
        grouped.setdefault((fee.community_id, fee.month), []).append(fee)
    for (community_id, month), fees in sorted(grouped.items()):
        year, mon = (int(part) for part in month.split("-"))
        effective_at = datetime(year + (mon == 12), mon % 12 + 1, 8, 10, 0)
        bill = Bill(community_id=community_id, month=month, status=BillStatus.EFFECTIVE,
                    total_amount=sum(f.amount for f in fees), gov_amount=sum(f.gov_amount for f in fees),
                    ltci_amount=sum(f.ltci_amount for f in fees), self_amount=sum(f.self_amount for f in fees),
                    generated_at=effective_at - timedelta(days=7), effective_at=effective_at)
        db.add(bill)
        db.flush()
        by_elder: dict[int, list[int]] = {}
        for fee in fees:
            fee.bill_id = bill.id
            totals = by_elder.setdefault(fee.elder_id, [0, 0, 0, 0])
            for i, value in enumerate((fee.amount, fee.gov_amount, fee.ltci_amount, fee.self_amount)):
                totals[i] += value
        for org_id in {f.org_id for f in fees if f.org_id}:
            db.add(BillOrgConfirmation(bill_id=bill.id, org_id=org_id, confirmed_at=effective_at))
        for elder_id, (amount, gov, ltci, self_pay) in by_elder.items():
            db.add(ElderStatement(bill_id=bill.id, elder_id=elder_id, month=month, total_amount=amount, gov_amount=gov,
                                  ltci_amount=ltci, self_amount=self_pay, payment_status=PaymentStatus.PAID,
                                  paid_at=effective_at + timedelta(days=3), payment_method="微信"))
    db.flush()
    paid = {f.order_id for f in rows if f.order_id}
    for order in db.execute(select(ServiceOrder).where(ServiceOrder.id.in_(paid))).scalars():
        order.payment_status = PaymentStatus.PAID
    db.flush()


def _vital_base(elder_id: int) -> dict:
    local = random.Random(elder_id * 97)
    return {"SYSTOLIC": local.randint(118, 148), "DIASTOLIC": local.randint(70, 88), "GLUCOSE": local.uniform(5.2, 8.0),
            "HEART_RATE": local.randint(62, 86), "SPO2": local.randint(95, 98)}


def _build_health(db: Session, rng: random.Random, elders: list[Elder]) -> None:
    now = clock.now()
    silent = {elders[20].id, elders[33].id}  # 这两位 48 小时以上没有数据，由任务生成蓝色预警
    rows: list[HealthRecord] = []
    for index, elder in enumerate(elders):
        base = _vital_base(elder.id)
        last_gap = timedelta(hours=60) if elder.id in silent else timedelta(hours=rng.randint(2, 24))
        for step in range(10):
            at = now - last_gap - timedelta(days=step * 3, minutes=rng.randint(0, 200))
            rows.append(HealthRecord(elder_id=elder.id, metric="SYSTOLIC", value=base["SYSTOLIC"] + rng.randint(-8, 8), source=HealthSource.FOLLOWUP, measured_at=at))
            rows.append(HealthRecord(elder_id=elder.id, metric="DIASTOLIC", value=base["DIASTOLIC"] + rng.randint(-6, 6), source=HealthSource.FOLLOWUP, measured_at=at))
            rows.append(HealthRecord(elder_id=elder.id, metric="GLUCOSE", value=round(base["GLUCOSE"] + rng.uniform(-0.8, 1.0), 1), source=HealthSource.FOLLOWUP, measured_at=at))
        if index < 24 and elder.id not in silent:
            for step in range(84):
                at = now - timedelta(hours=step * 4 + 1, minutes=rng.randint(0, 40))
                rows.append(HealthRecord(elder_id=elder.id, metric="HEART_RATE", value=base["HEART_RATE"] + rng.randint(-6, 6), source=HealthSource.WEARABLE, measured_at=at, device_serial=f"WB-{10000 + index}"))
                rows.append(HealthRecord(elder_id=elder.id, metric="SPO2", value=min(100, base["SPO2"] + rng.randint(-1, 2)), source=HealthSource.WEARABLE, measured_at=at, device_serial=f"WB-{10000 + index}"))
                if step % 6 == 0:
                    rows.append(HealthRecord(elder_id=elder.id, metric="STEPS", value=rng.randint(800, 6000), source=HealthSource.WEARABLE, measured_at=at, device_serial=f"WB-{10000 + index}"))
                rows.append(HealthRecord(elder_id=elder.id, metric="BODY_MOTION", value=rng.randint(5, 120), source=HealthSource.WEARABLE, measured_at=at, device_serial=f"WB-{10000 + index}"))
    db.add_all(rows)
    db.flush()


def _build_alert_history(db: Session, rng: random.Random, world: dict, elders: list[Elder]) -> None:
    now = clock.now()
    rules = {r.code: r for r in db.execute(select(AlertRule)).scalars()}
    choices = [("BP-HIGH-RED", "收缩压：当前值 {v} >= 阈值 180 mmHg", (181, 196)), ("HR-CONSEC-YELLOW", "心率：连续 3 次 > 100 次/分", (102, 118)),
               ("NO-DATA-BLUE", "已 {v} 小时未上报任何指标", (49, 70)), ("SPO2-LOW-YELLOW", "血氧：连续 3 次 < 94 %", (91, 93)), ("GLU-HIGH-RED", "血糖：当前值 {v} >= 阈值 16.7 mmol/L", (17, 21))]
    measures = ["电话确认后上门测量，已复测正常", "联系家属陪同，用药后好转", "上门查看，老人状态平稳"]
    number = 0
    for days in range(440, 0, -1):
        for _ in range(rng.choice([0, 1, 1, 1, 2])):
            number += 1
            code, template, (low, high) = rng.choice(choices)
            rule = rules[code]
            elder = rng.choice(elders)
            value = rng.randint(low, high)
            triggered = now - timedelta(days=days, minutes=rng.randint(0, 800))
            is_red = rule.level == "RED"
            response = rng.randint(2, 22) if is_red else rng.randint(10, 180)
            handle = response + rng.randint(5, 40)
            closed = triggered + timedelta(minutes=handle)
            conclusion = rng.choices([AlertConclusion.HANDLED, AlertConclusion.FALSE_ALARM, AlertConclusion.REFERRED], weights=[70, 20, 10])[0]
            alert = Alert(
                alert_no=f"AL{triggered:%Y%m%d}{number:06d}", elder_id=elder.id, grid_id=elder.grid_id, rule_id=rule.id,
                rule_snapshot={"code": rule.code, "name": rule.name, "rule_type": rule.rule_type, "level": rule.level, "version": 1},
                level=rule.level, title=rule.name, message=template.format(v=value), trigger_value=value, status=AlertStatus.CLOSED,
                triggered_at=triggered, last_triggered_at=triggered, pushed_at=triggered + timedelta(seconds=rng.randint(5, 120)),
                responded_at=triggered + timedelta(minutes=response), responded_by="张网格", response_note="已电话确认",
                closed_at=closed, closed_by="张网格", handle_minutes=handle, conclusion=conclusion,
                measures=rng.choice(measures) if conclusion == AlertConclusion.HANDLED else None,
                false_reason="设备脱落" if conclusion == AlertConclusion.FALSE_ALARM else None,
                hospital="青松社区卫生服务中心" if conclusion == AlertConclusion.REFERRED else None,
                referred_at=closed if conclusion == AlertConclusion.REFERRED else None,
            )
            db.add(alert)
    db.flush()


def _actor_of(db: Session, username: str):
    user = db.execute(select(User).where(User.username == username)).scalar()
    return build_actor(db, user, "127.0.0.1")


def _build_october_discrepancies(db: Session, rng: random.Random, world: dict, elders: list[Elder], items: list[ServiceItem]) -> None:
    """在上个月账单里埋四类差异，演示拦截和逐条处理。"""
    now = clock.now()
    last = billing_service.previous_month(now.date())
    year, mon = (int(part) for part in last.split("-"))
    community = world["communities"][0]
    in_community = [e for e in elders if db.get(Grid, e.grid_id).community_id == community.id]
    org_admin = _actor_of(db, "org_zhao")
    orders = db.execute(select(ServiceOrder).where(
        ServiceOrder.community_id == community.id, ServiceOrder.status == OrderStatus.COMPLETED,
        ServiceOrder.org_id == org_admin.user.org_id,
        ServiceOrder.scheduled_start >= datetime(year, mon, 1), ServiceOrder.scheduled_start < datetime(year, mon, 28)
    ).order_by(ServiceOrder.id)).scalars().all()
    # 同一时段重复计费：同一工单被人工补录一次
    billing_service.create_manual_fee(db, org_admin, orders[0].id, orders[0].price_version.unit_price, "补录遗漏的服务费用")
    # 服务时长明显异常：时长超过标准 3 倍
    target = orders[1]
    execution = target.execution
    execution.duration_minutes = target.item.standard_minutes * 4
    execution.checkout_at = execution.checkin_at + timedelta(minutes=execution.duration_minutes)
    fee = db.execute(select(FeeDetail).where(FeeDetail.order_id == target.id, FeeDetail.fee_type == FeeType.SERVICE)).scalar()
    fee.actual_minutes = execution.duration_minutes
    fee.service_end = execution.checkout_at
    # 工单未完成却计费：给已取消工单补录费用
    cancelled = db.execute(select(ServiceOrder).where(
        ServiceOrder.community_id == community.id, ServiceOrder.status == OrderStatus.CANCELLED,
        ServiceOrder.scheduled_start >= datetime(year, mon, 1), ServiceOrder.scheduled_start < datetime(year, mon, 28)
    ).limit(1)).scalar()
    if cancelled is None:
        victim = orders[2]
        victim.status = OrderStatus.CANCELLED
        cancelled = victim
    admin_comm = _actor_of(db, "comm_liu")
    billing_service.create_manual_fee(db, admin_comm, cancelled.id, 4000, "机构申报的服务费用")
    # 单人月度补贴超限：一位轻度失能老人当月做了很多次康复护理
    rehab = next(i for i in items if i.code == "SV05")
    version = rehab.versions[-1]
    elder = next(e for e in in_community if e.disability_level == "MILD" and e.difficulty_type == "NONE")
    caregiver = world["caregivers"][0]
    numbers = world["numbers"]
    for day in range(2, 14):
        start = datetime(year, mon, day, 19, 0)
        end = start + timedelta(minutes=60)
        order = ServiceOrder(order_no=numbers.next(start), elder_id=elder.id, item_id=rehab.id, grid_id=elder.grid_id,
                             community_id=community.id, price_version_id=version.id, scheduled_start=start, scheduled_end=end,
                             address=elder.address, lat=elder.lat, lng=elder.lng, status=OrderStatus.COMPLETED, caregiver_id=caregiver.id,
                             org_id=caregiver.org_id, completed_at=end, created_at=start - timedelta(days=1))
        db.add(order)
        db.flush()
        execution = ServiceExecution(order_id=order.id, caregiver_id=caregiver.id, checkin_at=start, checkin_lat=order.lat, checkin_lng=order.lng,
                                     checkin_distance_m=20, checklist=[{"name": n, "done": True} for n in rehab.checklist],
                                     checkout_at=end, duration_minutes=60, summary="康复训练完成")
        db.add(execution)
        db.flush()
        billing_service.record_service_fee(db, order, execution)
    db.flush()
    for bill_community in world["communities"]:
        billing_service.generate_bill(db, bill_community.id, last)


def _build_quality_cases(db: Session, rng: random.Random, world: dict) -> None:
    now = clock.now()
    community = world["communities"][0]
    org_id = world["org_admins"][0].org_id
    recent = db.execute(select(ServiceOrder).where(
        ServiceOrder.community_id == community.id, ServiceOrder.org_id == org_id, ServiceOrder.status == OrderStatus.COMPLETED,
        ServiceOrder.completed_at >= now - timedelta(days=20)).order_by(ServiceOrder.id.desc()).limit(20)).scalars().all()
    if len(recent) < 8:
        return
    # 低分评价待回访
    review = db.execute(select(Review).where(Review.order_id == recent[0].id)).scalar()
    if review is None:
        review = Review(order_id=recent[0].id, caregiver_id=recent[0].caregiver_id, punctuality=2, attitude=3, professionalism=3,
                        effectiveness=3, overall=2.75, comment="迟到了将近半小时，服务也比较匆忙", created_at=now - timedelta(hours=5))
        db.add(review)
        db.flush()
    else:
        review.punctuality, review.attitude, review.professionalism, review.effectiveness, review.overall = 2, 3, 3, 3, 2.75
        review.comment = "迟到了将近半小时，服务也比较匆忙"
    db.add(FollowupTask(order_id=recent[0].id, review_id=review.id, community_id=community.id, due_at=now + timedelta(hours=40), created_at=now - timedelta(hours=8)))
    # 三种进度的投诉：待指派、待说明、待结论
    specs = [(recent[1], ComplaintStatus.OPEN, "护理员没有按约定完成擦身项目", None),
             (recent[2], ComplaintStatus.ASSIGNED, "上门时间比约定晚了 40 分钟，也没有提前告知", None),
             (recent[3], ComplaintStatus.EXPLAINED, "服务态度不好，说话不耐烦", "经核实，当日护理员排班过密，已批评教育")]
    for index, (order, status, content, explanation) in enumerate(specs, start=1):
        db.add(Complaint(complaint_no=f"CP{now:%Y%m%d}{index:05d}", order_id=order.id, caregiver_id=order.caregiver_id, org_id=order.org_id,
                         community_id=order.community_id, content=content, status=status, org_explanation=explanation,
                         explained_at=now - timedelta(hours=3) if explanation else None, created_at=now - timedelta(days=1)))
    # 一名护理员本月已有 3 次投诉成立，自动进入重点关注
    caregiver = world["caregivers"][3]
    for index in range(3):
        order = recent[4 + index]
        db.add(Complaint(complaint_no=f"CP{now:%Y%m%d}{10 + index:05d}", order_id=order.id, caregiver_id=caregiver.id, org_id=caregiver.org_id,
                         community_id=order.community_id, content="服务未按清单完成", status=ComplaintStatus.CONCLUDED,
                         conclusion=ComplaintConclusion.UPHELD, basis="现场照片和清单记录不符", actions=["PENALTY"], penalty_points=2,
                         created_at=now - timedelta(days=2), concluded_at=now.replace(day=1, hour=9, minute=0) + timedelta(hours=index)))
    caregiver.key_attention_month = month_key(now)
    db.flush()


def _build_today(db: Session, rng: random.Random, world: dict, elders: list[Elder], items: list[ServiceItem]) -> None:
    """当前时点的待办：工作台一打开就有事可做。"""
    now = clock.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    grid_id = elders[0].grid_id
    grid_elders = [e for e in elders if e.grid_id == grid_id]
    by_code = {i.code: i for i in items}
    family = build_actor(db, world["family"], "127.0.0.1")
    grid_worker = _actor_of(db, "grid_zhang")
    # 待受理：2 张正常，1 张校验未通过（能力等级不匹配）转人工
    light = next(e for e in grid_elders if e.disability_level == "INTACT") if any(e.disability_level == "INTACT" for e in grid_elders) else grid_elders[1]
    order_service.create_order(db, grid_worker, grid_elders[1].id, by_code["SV01"].id, tomorrow.replace(hour=11), source=OrderSource.PHONE, remark="电话代办")
    order_service.create_order(db, grid_worker, grid_elders[2].id, by_code["SV03"].id, tomorrow.replace(hour=9), source=OrderSource.PHONE)
    if light.disability_level == "INTACT":
        order_service.create_order(db, grid_worker, light.id, by_code["SV02"].id, tomorrow.replace(hour=10), remark="家属希望提前安排")
    # 待派单 + 已邀约 cg01
    pending = order_service.create_order(db, grid_worker, grid_elders[3].id, by_code["SV01"].id, tomorrow.replace(hour=15))
    order_service.accept_order(db, grid_worker, pending.id, "确认受理")
    offered = order_service.create_order(db, grid_worker, grid_elders[4].id, by_code["SV04"].id, tomorrow.replace(hour=16))
    order_service.accept_order(db, grid_worker, offered.id, "确认受理")
    dispatch_service.recommend(db, grid_worker, offered.id)
    dispatch_service.assign(db, grid_worker, offered.id)
    # 今天稍后一单：已派给 cg01，等签到；演示执行流程
    cg_actor = _actor_of(db, "cg01")
    start_today = (now + timedelta(minutes=25)).replace(second=0, microsecond=0)
    start_today = start_today.replace(minute=start_today.minute - start_today.minute % 5)
    star = elders[0]
    visit = order_service.create_order(db, family, star.id, by_code["SV02"].id, start_today, remark="老人腿脚不便，请提前联系")
    order_service.accept_order(db, grid_worker, visit.id, "确认受理")
    dispatch_service.recommend(db, grid_worker, visit.id)
    assigned = dispatch_service.assign(db, grid_worker, visit.id, world["caregivers"][0].id)
    dispatch_service.accept_offer(db, cg_actor, assigned["offer_id"])
    # 待评价：刚完成的服务
    done = order_service.create_order(db, family, star.id, by_code["SV01"].id, now + timedelta(days=9))
    done.scheduled_start = now - timedelta(hours=3)
    done.scheduled_end = now - timedelta(hours=2, minutes=30)
    order_service.accept_order(db, grid_worker, done.id, "确认受理")
    dispatch_service.recommend(db, grid_worker, done.id)
    offer = dispatch_service.assign(db, grid_worker, done.id, world["caregivers"][0].id)
    dispatch_service.accept_offer(db, cg_actor, offer["offer_id"])
    execution_service.check_in(db, cg_actor, done.id, done.lat, done.lng, occurred_at=now - timedelta(hours=3))
    execution_service.update_checklist(db, cg_actor, done.id, [{"name": n, "done": True} for n in by_code["SV01"].checklist])
    execution_service.check_out(db, cg_actor, done.id, "老人进餐正常，精神状态良好", occurred_at=now - timedelta(hours=2, minutes=25))
    # 服务异常单：老人不在家
    absent = order_service.create_order(db, grid_worker, grid_elders[5].id, by_code["SV08"].id, now + timedelta(days=3))
    absent.scheduled_start = now - timedelta(minutes=20)
    absent.scheduled_end = now + timedelta(minutes=40)
    order_service.accept_order(db, grid_worker, absent.id, "确认受理")
    dispatch_service.recommend(db, grid_worker, absent.id)
    absent_offer = dispatch_service.assign(db, grid_worker, absent.id)
    actor = build_actor(db, db.get(DispatchOffer, absent_offer["offer_id"]).caregiver.user, "127.0.0.1")
    dispatch_service.accept_offer(db, actor, absent_offer["offer_id"])
    execution_service.report_exception(db, actor, absent.id, "NOT_HOME", "敲门无人应答，电话未接")
    # 时长异常待抽查
    long_order = db.execute(select(ServiceOrder).where(
        ServiceOrder.grid_id == grid_id, ServiceOrder.status == OrderStatus.COMPLETED,
        ServiceOrder.completed_at >= now - timedelta(days=10)).order_by(ServiceOrder.id.desc()).limit(1)).scalar()
    if long_order and long_order.execution:
        long_order.execution.duration_abnormal = True
        long_order.execution.spot_check_status = SpotCheckStatus.PENDING
        long_order.execution.duration_minutes = long_order.item.standard_minutes + 45
    # 周期预约
    order_service.create_plan(db, family, star.id, by_code["SV03"].id, (now.date().weekday() + 2) % 7, "09:30", now.date(), remark="每周固定打扫")
    # 预警：红色刚触发、红色已升级、黄色、蓝色
    red_elder = grid_elders[2]
    health_service.ingest_followup(db, grid_worker, red_elder.id, [{"metric": "SYSTOLIC", "value": 186, "measured_at": now - timedelta(minutes=12)},
                                                                   {"metric": "DIASTOLIC", "value": 104, "measured_at": now - timedelta(minutes=12)}], "随访测量")
    alert = db.execute(select(Alert).where(Alert.elder_id == red_elder.id, Alert.status != AlertStatus.CLOSED).order_by(Alert.id.desc())).scalar()
    if alert:
        alert.triggered_at = alert.last_triggered_at = now - timedelta(minutes=12)
    for offset in (40, 28, 15):
        health_service.ingest_points(db, grid_elders[1], [{"metric": "HEART_RATE", "value": 104 + offset % 7, "measured_at": now - timedelta(minutes=offset)}], HealthSource.WEARABLE, device_serial=None)
    other_grid_elder = next(e for e in elders if e.grid_id != grid_id)
    health_service.ingest_points(db, other_grid_elder, [{"metric": "SPO2", "value": 87, "measured_at": now - timedelta(minutes=50)}], HealthSource.FOLLOWUP)
    escalated = db.execute(select(Alert).where(Alert.elder_id == other_grid_elder.id, Alert.status != AlertStatus.CLOSED)).scalars().first()
    if escalated:
        escalated.triggered_at = escalated.last_triggered_at = now - timedelta(minutes=50)
        escalated.escalated_at = now - timedelta(minutes=20)
        db.add(AlertEvent(alert_id=escalated.id, kind="ESCALATE", detail="已 30 分钟未处置，自动升级至街道监管员", at=now - timedelta(minutes=20)))
    health_service.scan_no_data(db, now)
    db.flush()


def seed_demo(db: Session) -> dict:
    """写入全部演示数据。已有数据时直接退出，避免重复写入。"""
    if db.execute(select(func.count()).select_from(Elder)).scalar():
        return {"seeded": False, "reason": "数据库已有档案数据，跳过"}
    rng = random.Random(SEED)
    sync_rbac(db)
    sync_builtin_rules(db)
    sync_subsidy_policies(db)
    world = _build_org(db, rng)
    items = _build_items(db, rng)
    elders = _build_elders(db, rng, world)
    _build_history(db, rng, world, elders, items)
    _build_health(db, rng, elders)
    _build_alert_history(db, rng, world, elders)
    _build_october_discrepancies(db, rng, world, elders, items)
    _build_quality_cases(db, rng, world)
    _build_today(db, rng, world, elders, items)
    audit_service.write(db, audit_service.SYSTEM, "SEED", "system", None, {"elders": len(elders)})
    db.flush()
    return {
        "seeded": True, "elders": len(elders), "orders": db.execute(select(func.count()).select_from(ServiceOrder)).scalar(),
        "alerts": db.execute(select(func.count()).select_from(Alert)).scalar(),
        "health_records": db.execute(select(func.count()).select_from(HealthRecord)).scalar(),
        "fees": db.execute(select(func.count()).select_from(FeeDetail)).scalar(),
    }

