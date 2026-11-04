"""认证、元数据、通知、组织、档案、服务目录。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import schemas as s
from app.api.deps import client_ip, get_actor, require
from app.core import clock
from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.db.session import get_db
from app.domain.enums import CARE_TAGS, LABELS, LTC_INSURANCE_TYPES, MEDICAL_INSURANCE_TYPES, SKILLS
from app.models import Caregiver, Community, Doctor, Notification, Organization
from app.services import auth_service, catalog_service, elder_service
from app.services.access import Actor, scope_caregivers

router = APIRouter()


# ---------- 认证 ----------
@router.post("/auth/login", tags=["认证"])
def login(body: s.LoginIn, request: Request, db: Session = Depends(get_db)):
    result = auth_service.login(db, body.username, body.password, client_ip(request))
    db.commit()
    return result


@router.get("/auth/me", tags=["认证"])
def me(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    return auth_service.me_view(db, actor.user)


@router.post("/auth/change-password", tags=["认证"])
def change_password(body: s.PasswordChangeIn, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    auth_service.change_password(db, actor, body.old_password, body.new_password)
    db.commit()
    return {"ok": True}


# ---------- 元数据 ----------
@router.get("/meta", tags=["元数据"])
def meta(db: Session = Depends(get_db)):
    settings = get_settings()
    return {
        "app": "颐护通", "demo_mode": settings.demo_mode, "labels": LABELS, "care_tags": CARE_TAGS, "skills": SKILLS,
        "medical_insurance_types": MEDICAL_INSURANCE_TYPES, "ltc_insurance_types": LTC_INSURANCE_TYPES,
        "server_time": clock.now(), "clock_offset_minutes": clock.offset_minutes(),
    }


@router.get("/org/tree", tags=["组织"])
def org_tree(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    communities = db.execute(select(Community).order_by(Community.id)).scalars().all()
    return {
        "communities": [{
            "id": c.id, "name": c.name, "center_lat": c.center_lat, "center_lng": c.center_lng,
            "service_radius_m": c.service_radius_m,
            "grids": [{"id": g.id, "name": g.name} for g in c.grids],
        } for c in communities],
        "organizations": [{"id": o.id, "name": o.name} for o in db.execute(select(Organization)).scalars()],
        "doctors": [{"id": d.id, "name": d.name, "hospital": d.hospital} for d in db.execute(select(Doctor)).scalars()],
    }


# ---------- 通知 ----------
def _notification_view(n: Notification) -> dict:
    return {"id": n.id, "title": n.title, "body": n.body, "level": n.level, "ref_type": n.ref_type, "ref_id": n.ref_id,
            "created_at": n.queued_at, "read_at": n.read_at}


@router.get("/notifications", tags=["通知"])
def notifications(unread_only: bool = False, limit: int = Query(30, le=100), actor: Actor = Depends(get_actor),
                  db: Session = Depends(get_db)):
    stmt = select(Notification).where(Notification.recipient_user_id == actor.id, Notification.channel == "IN_APP")
    unread = db.execute(select(func.count()).select_from(
        stmt.where(Notification.read_at.is_(None)).subquery())).scalar()
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = db.execute(stmt.order_by(Notification.id.desc()).limit(limit)).scalars().all()
    return {"unread": unread, "items": [_notification_view(n) for n in rows]}


@router.post("/notifications/{notification_id}/read", tags=["通知"])
def mark_read(notification_id: int, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    row = db.get(Notification, notification_id)
    if row is None or row.recipient_user_id != actor.id:
        raise NotFound("消息不存在")
    row.read_at = row.read_at or clock.now()
    db.commit()
    return {"ok": True}


@router.post("/notifications/read-all", tags=["通知"])
def mark_all_read(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    now = clock.now()
    for row in db.execute(select(Notification).where(
            Notification.recipient_user_id == actor.id, Notification.read_at.is_(None))).scalars():
        row.read_at = now
    db.commit()
    return {"ok": True}


# ---------- 档案 ----------
@router.get("/elders", tags=["档案"])
def list_elders(keyword: str | None = None, grid_id: int | None = None, community_id: int | None = None,
                level: str | None = None, tag: str | None = None, include_deleted: bool = False,
                page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                actor: Actor = Depends(require("elder:read")), db: Session = Depends(get_db)):
    return elder_service.list_elders(db, actor, keyword=keyword, grid_id=grid_id, community_id=community_id, level=level,
                                     tag=tag, include_deleted=include_deleted and actor.has("elder:delete"),
                                     page=page, size=size)


@router.post("/elders", tags=["档案"], status_code=201)
def create_elder(body: s.ElderBase, actor: Actor = Depends(require("elder:write")), db: Session = Depends(get_db)):
    elder = elder_service.create_elder(db, actor, body.model_dump(exclude_none=True))
    db.commit()
    return elder_service.to_view(elder)


@router.post("/elders/import", tags=["档案"])
def import_elders(file: UploadFile = File(...), dry_run: bool = False, actor: Actor = Depends(require("elder:import")),
                  db: Session = Depends(get_db)):
    content = file.file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise ValidationFailed("导入文件不能超过 20 MB")
    result = elder_service.import_elders(db, actor, file.filename or "", content, dry_run)
    db.commit()
    return result


@router.get("/elders/{elder_id}", tags=["档案"])
def get_elder(elder_id: int, actor: Actor = Depends(require("elder:read")), db: Session = Depends(get_db)):
    from app.services.access import ensure_elder
    return elder_service.to_view(ensure_elder(db, actor, elder_id))


@router.patch("/elders/{elder_id}", tags=["档案"])
def update_elder(elder_id: int, body: s.ElderBase, actor: Actor = Depends(require("elder:write")),
                 db: Session = Depends(get_db)):
    elder = elder_service.update_elder(db, actor, elder_id, body.model_dump(exclude_unset=True))
    db.commit()
    return elder_service.to_view(elder)


@router.delete("/elders/{elder_id}", tags=["档案"])
def delete_elder(elder_id: int, body: s.ReasonIn, actor: Actor = Depends(require("elder:delete")),
                 db: Session = Depends(get_db)):
    elder_service.delete_elder(db, actor, elder_id, body.reason)
    db.commit()
    return {"ok": True}


@router.post("/elders/{elder_id}/restore", tags=["档案"])
def restore_elder(elder_id: int, actor: Actor = Depends(require("elder:delete")), db: Session = Depends(get_db)):
    elder = elder_service.restore_elder(db, actor, elder_id)
    db.commit()
    return elder_service.to_view(elder)


@router.post("/elders/{elder_id}/reveal", tags=["档案"])
def reveal(elder_id: int, body: s.RevealIn, actor: Actor = Depends(require("elder:reveal")),
           db: Session = Depends(get_db)):
    result = elder_service.reveal_sensitive(db, actor, elder_id, body.password, body.reason, body.fields)
    db.commit()
    return result


@router.get("/elders/{elder_id}/change-logs", tags=["档案"])
def change_logs(elder_id: int, actor: Actor = Depends(require("elder:changelog")), db: Session = Depends(get_db)):
    return elder_service.change_logs(db, actor, elder_id)


@router.get("/elders/{elder_id}/access-logs", tags=["档案"])
def access_logs(elder_id: int, actor: Actor = Depends(require("elder:changelog")), db: Session = Depends(get_db)):
    return elder_service.access_logs(db, actor, elder_id)


@router.post("/elders/{elder_id}/family", tags=["档案"])
def link_family(elder_id: int, body: s.FamilyLinkIn, actor: Actor = Depends(require("elder:write")),
                db: Session = Depends(get_db)):
    link = elder_service.link_family(db, actor, elder_id, body.user_id, body.relation)
    db.commit()
    return {"id": link.id}


# ---------- 服务目录 ----------
@router.get("/catalog/items", tags=["服务目录"])
def list_items(on_shelf_only: bool = False, actor: Actor = Depends(require("catalog:read")),
               db: Session = Depends(get_db)):
    return catalog_service.list_items(db, on_shelf_only)


@router.post("/catalog/items", tags=["服务目录"], status_code=201)
def create_item(body: s.ItemCreateIn, actor: Actor = Depends(require("catalog:write")), db: Session = Depends(get_db)):
    item = catalog_service.create_item(db, actor, body.model_dump())
    db.commit()
    return catalog_service.to_view(item, with_versions=True)


@router.patch("/catalog/items/{item_id}", tags=["服务目录"])
def update_item(item_id: int, body: s.ItemUpdateIn, actor: Actor = Depends(require("catalog:write")),
                db: Session = Depends(get_db)):
    item = catalog_service.update_item(db, actor, item_id, body.model_dump(exclude_unset=True))
    db.commit()
    return catalog_service.to_view(item, with_versions=True)


@router.post("/catalog/items/{item_id}/shelf", tags=["服务目录"])
def set_shelf(item_id: int, body: s.ShelfIn, actor: Actor = Depends(require("catalog:write")),
              db: Session = Depends(get_db)):
    item = catalog_service.set_shelf(db, actor, item_id, body.on_shelf)
    db.commit()
    return catalog_service.to_view(item, with_versions=True)


@router.post("/catalog/items/{item_id}/price", tags=["服务目录"])
def adjust_price(item_id: int, body: s.PriceAdjustIn, actor: Actor = Depends(require("catalog:write")),
                 db: Session = Depends(get_db)):
    version = catalog_service.adjust_price(db, actor, item_id, body.unit_price, body.subsidy_ratio, body.effective_from,
                                           body.reason)
    db.commit()
    return {"version_no": version.version_no, "unit_price": version.unit_price, "effective_from": version.effective_from}


# ---------- 护理员 ----------
@router.get("/caregivers", tags=["组织"])
def list_caregivers(actor: Actor = Depends(require("caregiver:read")), db: Session = Depends(get_db)):
    rows = db.execute(scope_caregivers(actor, select(Caregiver)).order_by(Caregiver.id)).scalars().all()
    return [{"id": c.id, "name": c.name, "org_id": c.org_id, "org_name": c.organization.name, "skills": c.skills,
             "base_lat": c.base_lat, "base_lng": c.base_lng, "is_active": c.is_active,
             "consecutive_rejects": c.consecutive_rejects, "key_attention_month": c.key_attention_month} for c in rows]


@router.patch("/caregivers/{caregiver_id}", tags=["组织"])
def update_caregiver(caregiver_id: int, body: s.CaregiverIn, actor: Actor = Depends(require("caregiver:write")),
                     db: Session = Depends(get_db)):
    caregiver = db.execute(scope_caregivers(actor, select(Caregiver).where(Caregiver.id == caregiver_id))).scalar()
    if caregiver is None:
        raise NotFound("护理员不存在或不在您的数据范围内")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(caregiver, key, value)
    db.commit()
    return {"ok": True}
