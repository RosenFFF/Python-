"""服务目录：项目维护、上下架、价格版本。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core import clock
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.domain.enums import DisabilityLevel, PricingMode, label
from app.models import PriceVersion, ServiceItem
from app.services import audit_service
from app.services.access import Actor


def price_version_at(db: Session, item_id: int, at: datetime) -> PriceVersion | None:
    """取 at 时点生效的价格版本，区间为 [effective_from, effective_to)。"""
    stmt = (select(PriceVersion)
            .where(PriceVersion.item_id == item_id, PriceVersion.effective_from <= at)
            .where((PriceVersion.effective_to.is_(None)) | (PriceVersion.effective_to > at))
            .order_by(PriceVersion.version_no.desc()).limit(1))
    return db.execute(stmt).scalar()


def to_view(item: ServiceItem, db: Session | None = None, with_versions: bool = False) -> dict:
    now = clock.now()
    current = next((v for v in reversed(item.versions)
                    if v.effective_from <= now and (v.effective_to is None or v.effective_to > now)), None)
    view = {
        "id": item.id, "code": item.code, "name": item.name, "pricing_mode": item.pricing_mode,
        "pricing_label": label("PricingMode", item.pricing_mode), "standard_minutes": item.standard_minutes,
        "required_skills": item.required_skills, "applicable_levels": item.applicable_levels,
        "subsidy_eligible": item.subsidy_eligible, "checklist": item.checklist, "is_on_shelf": item.is_on_shelf,
        "unit_price": current.unit_price if current else None,
        "subsidy_ratio": current.subsidy_ratio if current else None,
        "current_version": current.version_no if current else None,
    }
    if with_versions:
        view["versions"] = [{
            "id": v.id, "version_no": v.version_no, "unit_price": v.unit_price, "subsidy_ratio": v.subsidy_ratio,
            "effective_from": v.effective_from, "effective_to": v.effective_to, "reason": v.reason,
            "created_at": v.created_at,
        } for v in item.versions]
    return view


def list_items(db: Session, on_shelf_only: bool = False) -> list[dict]:
    stmt = select(ServiceItem).options(selectinload(ServiceItem.versions)).order_by(ServiceItem.code)
    if on_shelf_only:
        stmt = stmt.where(ServiceItem.is_on_shelf.is_(True))
    return [to_view(i, with_versions=not on_shelf_only) for i in db.execute(stmt).scalars()]


def _validate_item(data: dict) -> None:
    if data.get("pricing_mode") and data["pricing_mode"] not in PricingMode.__members__:
        raise ValidationFailed("计价方式不合法")
    if data.get("standard_minutes") is not None and data["standard_minutes"] <= 0:
        raise ValidationFailed("标准时长必须大于 0")
    invalid = set(data.get("applicable_levels") or []) - set(DisabilityLevel.__members__)
    if invalid:
        raise ValidationFailed(f"适用能力等级不合法：{sorted(invalid)}")


def create_item(db: Session, actor: Actor, data: dict) -> ServiceItem:
    _validate_item(data)
    if db.execute(select(ServiceItem.id).where(ServiceItem.code == data["code"])).first():
        raise Conflict("服务项目编码已存在")
    unit_price, subsidy_ratio = data.pop("unit_price"), data.pop("subsidy_ratio", 0.0)
    _validate_price(unit_price, subsidy_ratio)
    item = ServiceItem(**data)
    db.add(item)
    db.flush()
    db.add(PriceVersion(item_id=item.id, version_no=1, unit_price=unit_price, subsidy_ratio=subsidy_ratio,
                        effective_from=clock.now(), reason="初始定价", created_by=actor.id))
    audit_service.write(db, actor.ctx, "ITEM_CREATE", "service_item", item.id, {"code": item.code})
    db.flush()
    db.refresh(item)
    return item


def update_item(db: Session, actor: Actor, item_id: int, data: dict) -> ServiceItem:
    item = db.get(ServiceItem, item_id)
    if item is None:
        raise NotFound("服务项目不存在")
    _validate_item(data)
    for key, value in data.items():
        setattr(item, key, value)
    audit_service.write(db, actor.ctx, "ITEM_UPDATE", "service_item", item.id, data)
    return item


def set_shelf(db: Session, actor: Actor, item_id: int, on_shelf: bool) -> ServiceItem:
    item = db.get(ServiceItem, item_id)
    if item is None:
        raise NotFound("服务项目不存在")
    item.is_on_shelf = on_shelf
    audit_service.write(db, actor.ctx, "ITEM_SHELF", "service_item", item.id, {"on_shelf": on_shelf})
    return item


def _validate_price(unit_price: int, subsidy_ratio: float) -> None:
    if unit_price is None or unit_price <= 0:
        raise ValidationFailed("单价必须大于 0")
    if not 0 <= subsidy_ratio <= 1:
        raise ValidationFailed("补贴比例必须在 0 到 1 之间")


def adjust_price(db: Session, actor: Actor, item_id: int, unit_price: int, subsidy_ratio: float | None,
                 effective_from: datetime | None, reason: str) -> PriceVersion:
    """调价：新增版本并封闭上一版本，从不覆盖旧价。不允许追溯到已生效版本之前。"""
    item = db.get(ServiceItem, item_id)
    if item is None:
        raise NotFound("服务项目不存在")
    if not reason or not reason.strip():
        raise ValidationFailed("调价必须填写原因")
    latest = db.execute(select(PriceVersion).where(PriceVersion.item_id == item_id)
                        .order_by(PriceVersion.version_no.desc()).limit(1)).scalar()
    start = effective_from or clock.now()
    if start < clock.now():
        raise ValidationFailed("新价格的生效时间不能早于当前时间，已发生工单不受调价影响")
    if latest and start <= latest.effective_from:
        raise ValidationFailed("新版本生效时间必须晚于上一版本的生效时间")
    ratio = subsidy_ratio if subsidy_ratio is not None else (latest.subsidy_ratio if latest else 0.0)
    _validate_price(unit_price, ratio)
    if latest:
        latest.effective_to = start
    version = PriceVersion(item_id=item_id, version_no=(latest.version_no + 1) if latest else 1,
                           unit_price=unit_price, subsidy_ratio=ratio, effective_from=start,
                           reason=reason, created_by=actor.id)
    db.add(version)
    audit_service.write(db, actor.ctx, "PRICE_ADJUST", "service_item", item_id, {
        "from_cents": latest.unit_price if latest else None, "to_cents": unit_price,
        "effective_from": start, "reason": reason,
    })
    return version
