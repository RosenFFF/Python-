"""测试世界：两个社区、三个网格、两家机构、覆盖全部角色的账号。

坐标设计让距离可手算：纬度差 0.009° ≈ 1 公里。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock, crypto
from app.core.security import hash_password
from app.db.session import new_session
from app.models import (
    Caregiver,
    Community,
    Doctor,
    Elder,
    FamilyLink,
    Grid,
    Organization,
    PriceVersion,
    Role,
    ServiceItem,
    Street,
    User,
)

LAT, LNG = 30.0, 104.0
KM = 0.009


def valid_id_card(seq: int, birth: str = "19480316") -> str:
    body = f"510104{birth}{seq:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    return body + "10X98765432"[sum(int(c) * w for c, w in zip(body, weights)) % 11]


@dataclass
class World:
    PASSWORD = "Test@2026"
    community_a: int
    community_b: int
    grid_a1: int
    grid_a2: int
    grid_b1: int
    org_x: int
    org_y: int
    elder_a1: int
    elder_a2: int
    elder_b1: int
    elder_light: int
    item_bath: int
    item_meal: int
    item_clean: int
    item_errand: int
    c_near: int
    c_mid: int
    c_far: int
    c_other: int
    c_noskill: int

    @classmethod
    def load(cls) -> World:
        db = new_session(read_only=True)
        try:
            names = {c.name: c.id for c in db.execute(select(Community)).scalars()}
            grids = {g.name: g.id for g in db.execute(select(Grid)).scalars()}
            orgs = {o.name: o.id for o in db.execute(select(Organization)).scalars()}
            elders = {e.name: e.id for e in db.execute(select(Elder)).scalars()}
            items = {i.code: i.id for i in db.execute(select(ServiceItem)).scalars()}
            carers = {c.name: c.id for c in db.execute(select(Caregiver)).scalars()}
        finally:
            db.close()
        return cls(names["社区A"], names["社区B"], grids["A1"], grids["A2"], grids["B1"], orgs["机构X"], orgs["机构Y"],
                   elders["老人甲"], elders["老人乙"], elders["老人丙"], elders["老人丁"], items["BATH"], items["MEAL"],
                   items["CLEAN"], items["ERRAND"], carers["近护理"], carers["中护理"], carers["远护理"], carers["外护理"],
                   carers["缺技能"])


def _elder(db: Session, name: str, seq: int, grid: Grid, level: str, **kwargs) -> Elder:
    id_card = valid_id_card(seq)
    phone = f"1380000{seq:04d}"
    community = grid.community
    elder = Elder(code=f"E{seq:04d}", name=name, gender="女", birth_date=date(1948, 3, 16), id_card=id_card,
                  id_card_idx=crypto.blind_index(id_card), phone=phone, phone_idx=crypto.blind_index(phone),
                  address=f"青松街道青松路{seq}号3栋502室", lat=community.center_lat, lng=community.center_lng, grid_id=grid.id,
                  emergency_name="家属", emergency_relation="儿子", emergency_phone=f"1390000{seq:04d}",
                  ltc_insurance="职工长护险", disability_level=level, created_at=clock.now() - timedelta(days=1), **kwargs)
    db.add(elder)
    db.flush()
    return elder


def _item(db: Session, code: str, name: str, mode: str, minutes: int, price: int, skills: list[str], levels: list[str],
          eligible: bool = True, ratio: float = 0.5, checklist: list[str] | None = None) -> ServiceItem:
    item = ServiceItem(code=code, name=name, pricing_mode=mode, standard_minutes=minutes, required_skills=skills,
                       applicable_levels=levels, subsidy_eligible=eligible, checklist=checklist or ["完成服务"])
    db.add(item)
    db.flush()
    db.add(PriceVersion(item_id=item.id, version_no=1, unit_price=price, subsidy_ratio=ratio,
                        effective_from=datetime(2020, 1, 1), reason="初始定价"))
    return item


def build_world(db: Session) -> None:
    hashed = hash_password(World.PASSWORD)
    roles = {r.code: r for r in db.execute(select(Role)).scalars()}
    street = Street(name="测试街道")
    db.add(street)
    db.flush()
    community_a = Community(street_id=street.id, name="社区A", center_lat=LAT, center_lng=LNG, service_radius_m=3000)
    community_b = Community(street_id=street.id, name="社区B", center_lat=LAT + 0.2, center_lng=LNG + 0.2, service_radius_m=3000)
    db.add_all([community_a, community_b])
    db.flush()
    a1, a2, b1 = Grid(community_id=community_a.id, name="A1"), Grid(community_id=community_a.id, name="A2"), Grid(community_id=community_b.id, name="B1")
    db.add_all([a1, a2, b1])
    db.flush()
    org_x, org_y = Organization(name="机构X"), Organization(name="机构Y")
    db.add_all([org_x, org_y])
    db.flush()
    org_x.communities = [community_a, community_b]
    org_y.communities = [community_a]
    doctor = Doctor(name="周医生", hospital="卫生站", phone="13700000001")
    db.add(doctor)
    db.flush()

    def user(username: str, role: str, **kwargs) -> User:
        row = User(username=username, display_name=username, password_hash=hashed, role_id=roles[role].id,
                   phone="13600000000", **kwargs)
        db.add(row)
        db.flush()
        return row

    user("admin", "super_admin")
    user("street", "street_supervisor")
    user("comm_a", "community_admin", community_id=community_a.id)
    user("comm_b", "community_admin", community_id=community_b.id)
    user("grid_a1", "grid_worker", grid_id=a1.id, community_id=community_a.id)
    user("grid_a2", "grid_worker", grid_id=a2.id, community_id=community_a.id)
    user("grid_b1", "grid_worker", grid_id=b1.id, community_id=community_b.id)
    user("org_x", "org_admin", org_id=org_x.id)
    user("org_y", "org_admin", org_id=org_y.id)
    family = user("family_a1", "elder_family")
    family_b = user("family_b1", "elder_family")

    elder_a1 = _elder(db, "老人甲", 1, a1, "MODERATE", care_tags=["独居"], doctor_id=doctor.id)
    _elder(db, "老人乙", 2, a2, "MILD")
    elder_b1 = _elder(db, "老人丙", 3, b1, "SEVERE")
    _elder(db, "老人丁", 4, a1, "INTACT")
    db.add(FamilyLink(user_id=family.id, elder_id=elder_a1.id, relation="儿子"))
    db.add(FamilyLink(user_id=family_b.id, elder_id=elder_b1.id, relation="女儿"))

    _item(db, "BATH", "助浴", "PER_TIME", 60, 8000, ["助浴", "失能照护"], ["MILD", "MODERATE", "SEVERE"],
          checklist=["助浴", "擦身", "更衣", "指甲修剪"])
    _item(db, "MEAL", "助餐", "PER_TIME", 30, 1500, ["助餐"], ["INTACT", "MILD", "MODERATE", "SEVERE"], ratio=0.5)
    _item(db, "CLEAN", "助洁", "PER_HOUR", 120, 4000, ["助洁"], ["INTACT", "MILD", "MODERATE", "SEVERE"], ratio=0.4)
    _item(db, "ERRAND", "代购代办", "PER_TIME", 45, 2000, ["代购代办"], ["INTACT", "MILD", "MODERATE", "SEVERE"], eligible=False, ratio=0.0)

    def caregiver(login: str, name: str, org: Organization, km: float, skills: list[str]) -> Caregiver:
        account = user(login, "caregiver", org_id=org.id)
        row = Caregiver(user_id=account.id, org_id=org.id, name=name, skills=skills, base_lat=LAT + km * KM, base_lng=LNG)
        db.add(row)
        db.flush()
        return row

    full = ["助浴", "失能照护", "助餐", "助洁", "代购代办"]
    caregiver("cg_near", "近护理", org_x, 0.5, full)
    caregiver("cg_mid", "中护理", org_x, 1.5, full)
    caregiver("cg_far", "远护理", org_y, 2.5, full)
    caregiver("cg_other", "外护理", org_y, 10.0, full)
    caregiver("cg_noskill", "缺技能", org_y, 0.2, ["助餐"])
    db.flush()
