"""老人档案：增删改查、批量导入、变更日志、脱敏与二次授权查看。"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock, crypto
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.security import verify_password
from app.domain.enums import DifficultyType, DisabilityLevel, label
from app.models import Doctor, Elder, ElderChangeLog, FamilyLink, Grid, SensitiveAccessLog
from app.services import audit_service
from app.services.access import Actor, ensure_elder, scope_elders

SENSITIVE_FIELDS = {"id_card", "phone", "address", "emergency_phone"}
_MASKERS = {
    "id_card": crypto.mask_id_card, "phone": crypto.mask_phone,
    "address": crypto.mask_address, "emergency_phone": crypto.mask_phone,
}
TRACKED_FIELDS = [
    "name", "gender", "birth_date", "id_card", "phone", "address", "lat", "lng", "grid_id", "emergency_name",
    "emergency_relation", "emergency_phone", "medical_insurance", "ltc_insurance", "chronic_diseases", "medications",
    "allergies", "disability_level", "difficulty_type", "care_tags", "doctor_id",
]
IMPORT_COLUMNS = {
    "姓名": "name", "性别": "gender", "出生日期": "birth_date", "身份证号": "id_card", "联系电话": "phone",
    "居住地址": "address", "纬度": "lat", "经度": "lng", "网格": "grid_name", "紧急联系人": "emergency_name",
    "关系": "emergency_relation", "紧急联系电话": "emergency_phone", "医保类型": "medical_insurance",
    "长护险类型": "ltc_insurance", "慢性病": "chronic_diseases", "用药": "medications", "过敏史": "allergies",
    "失能等级": "disability_level", "困难类型": "difficulty_type", "照护标签": "care_tags",
}


def age_of(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def to_view(elder: Elder, *, today: date | None = None) -> dict:
    """默认视图：敏感字段一律脱敏。"""
    today = today or clock.now().date()
    grid = elder.grid
    return {
        "id": elder.id, "code": elder.code, "name": elder.name, "gender": elder.gender,
        "birth_date": elder.birth_date, "age": age_of(elder.birth_date, today),
        "id_card": crypto.mask_id_card(elder.id_card), "phone": crypto.mask_phone(elder.phone),
        "address": crypto.mask_address(elder.address), "lat": elder.lat, "lng": elder.lng,
        "grid_id": elder.grid_id, "grid_name": grid.name if grid else None,
        "community_id": grid.community_id if grid else None,
        "community_name": grid.community.name if grid else None,
        "emergency_name": elder.emergency_name, "emergency_relation": elder.emergency_relation,
        "emergency_phone": crypto.mask_phone(elder.emergency_phone),
        "medical_insurance": elder.medical_insurance, "ltc_insurance": elder.ltc_insurance,
        "chronic_diseases": elder.chronic_diseases or [], "medications": elder.medications or [],
        "allergies": elder.allergies, "disability_level": elder.disability_level,
        "disability_label": label("DisabilityLevel", elder.disability_level),
        "difficulty_type": elder.difficulty_type, "difficulty_label": label("DifficultyType", elder.difficulty_type),
        "care_tags": elder.care_tags or [], "doctor_id": elder.doctor_id,
        "doctor_name": elder.doctor.name if elder.doctor else None,
        "is_deleted": elder.is_deleted, "updated_at": elder.updated_at,
    }


def _list_filters(stmt, *, keyword=None, grid_id=None, community_id=None, level=None, tag=None, include_deleted=False):
    if not include_deleted:
        stmt = stmt.where(Elder.is_deleted.is_(False))
    if keyword:
        kw = keyword.strip()
        if kw.isdigit() and len(kw) == 11:
            stmt = stmt.where(Elder.phone_idx == crypto.blind_index(kw))
        elif len(kw) == 18:
            stmt = stmt.where(Elder.id_card_idx == crypto.blind_index(kw))
        else:
            stmt = stmt.where((Elder.name.contains(kw)) | (Elder.code.contains(kw)))
    if grid_id:
        stmt = stmt.where(Elder.grid_id == grid_id)
    if community_id:
        stmt = stmt.where(Elder.grid_id.in_(select(Grid.id).where(Grid.community_id == community_id)))
    if level:
        stmt = stmt.where(Elder.disability_level == level)
    return stmt


def list_elders(db: Session, actor: Actor, *, page=1, size=20, tag=None, **filters) -> dict:
    stmt = scope_elders(actor, _list_filters(select(Elder), **filters)).order_by(Elder.id.desc())
    if tag:
        # JSON 数组里的标签由 Python 侧过滤，SQLite 和 PostgreSQL 的 JSON 语法不通用
        rows = [e for e in db.execute(stmt).scalars() if tag in (e.care_tags or [])]
        total = len(rows)
        rows = rows[(page - 1) * size: page * size]
    else:
        total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar()
        rows = db.execute(stmt.limit(size).offset((page - 1) * size)).scalars().all()
    return {"total": total, "page": page, "size": size, "items": [to_view(e) for e in rows]}


def _next_code(db: Session) -> str:
    last = db.execute(select(func.max(Elder.id))).scalar() or 0
    return f"E{clock.now():%Y}{last + 1:06d}"


def _validate_payload(db: Session, data: dict, *, creating: bool, exclude_id: int | None = None) -> None:
    if creating:
        for required in ("name", "gender", "birth_date", "id_card", "address", "grid_id"):
            if not data.get(required):
                raise ValidationFailed(f"缺少必填字段：{required}")
    if data.get("id_card") is not None:
        if not crypto.valid_id_card(data["id_card"]):
            raise ValidationFailed("身份证号格式或校验码不正确")
        stmt = select(Elder.id).where(Elder.id_card_idx == crypto.blind_index(data["id_card"]),
                                      Elder.is_deleted.is_(False))
        if exclude_id:
            stmt = stmt.where(Elder.id != exclude_id)
        if db.execute(stmt).first():
            raise Conflict("该身份证号已存在有效档案")
    for key in ("phone", "emergency_phone"):
        if data.get(key) and not crypto.valid_phone(data[key]):
            raise ValidationFailed(f"手机号格式不正确：{key}")
    if data.get("disability_level") and data["disability_level"] not in DisabilityLevel.__members__:
        raise ValidationFailed("失能等级不合法")
    if data.get("difficulty_type") and data["difficulty_type"] not in DifficultyType.__members__:
        raise ValidationFailed("困难类型不合法")
    birth = data.get("birth_date")
    if birth and birth > clock.now().date():
        raise ValidationFailed("出生日期不能晚于今天")
    if data.get("grid_id") and db.get(Grid, data["grid_id"]) is None:
        raise ValidationFailed("所属网格不存在")
    if data.get("doctor_id") and db.get(Doctor, data["doctor_id"]) is None:
        raise ValidationFailed("签约医生不存在")


def _check_grid_write(actor: Actor, grid: Grid) -> None:
    """写操作的网格必须在操作者的数据范围内，防止把档案写到别的网格。"""
    if actor.scope == "GRID" and grid.id != actor.user.grid_id:
        raise Forbidden("只能在本网格内维护档案")
    if actor.scope == "COMMUNITY" and grid.community_id != actor.user.community_id:
        raise Forbidden("只能在本社区内维护档案")


def _apply_sensitive_index(elder: Elder) -> None:
    elder.id_card_idx = crypto.blind_index(elder.id_card)
    elder.phone_idx = crypto.blind_index(elder.phone) if elder.phone else None


def _log_value(field: str, value: Any) -> str | None:
    if value is None:
        return None
    if field in SENSITIVE_FIELDS:
        return _MASKERS[field](value)
    return str(value)


def create_elder(db: Session, actor: Actor, data: dict) -> Elder:
    _validate_payload(db, data, creating=True)
    grid = db.get(Grid, data["grid_id"])
    _check_grid_write(actor, grid)
    data.setdefault("lat", grid.community.center_lat)
    data.setdefault("lng", grid.community.center_lng)
    elder = Elder(code=_next_code(db), **{k: v for k, v in data.items() if k in TRACKED_FIELDS})
    _apply_sensitive_index(elder)
    db.add(elder)
    db.flush()
    db.add(ElderChangeLog(elder_id=elder.id, field="__create__", before=None, after=elder.code,
                          changed_by=actor.id, changed_by_name=actor.name))
    audit_service.write(db, actor.ctx, "ELDER_CREATE", "elder", elder.id, {"code": elder.code})
    return elder


def update_elder(db: Session, actor: Actor, elder_id: int, data: dict) -> Elder:
    elder = ensure_elder(db, actor, elder_id)
    _validate_payload(db, data, creating=False, exclude_id=elder.id)
    if "grid_id" in data and data["grid_id"] != elder.grid_id:
        _check_grid_write(actor, db.get(Grid, data["grid_id"]))
    changed = []
    now = clock.now()
    for field in TRACKED_FIELDS:
        if field not in data or data[field] == getattr(elder, field):
            continue
        before = getattr(elder, field)
        setattr(elder, field, data[field])
        db.add(ElderChangeLog(elder_id=elder.id, field=field, before=_log_value(field, before),
                              after=_log_value(field, data[field]), changed_by=actor.id,
                              changed_by_name=actor.name, changed_at=now))
        changed.append(field)
    if not changed:
        return elder
    _apply_sensitive_index(elder)
    audit_service.write(db, actor.ctx, "ELDER_UPDATE", "elder", elder.id, {"fields": changed})
    return elder


def delete_elder(db: Session, actor: Actor, elder_id: int, reason: str) -> None:
    """逻辑删除。档案不物理删除，历史工单和账单仍可追溯。"""
    if not reason or not reason.strip():
        raise ValidationFailed("删除档案必须填写原因")
    elder = ensure_elder(db, actor, elder_id)
    elder.is_deleted = True
    elder.deleted_at = clock.now()
    elder.deleted_reason = reason
    db.add(ElderChangeLog(elder_id=elder.id, field="__delete__", before=None, after=reason,
                          changed_by=actor.id, changed_by_name=actor.name))
    audit_service.write(db, actor.ctx, "ELDER_DELETE", "elder", elder.id, {"reason": reason})


def restore_elder(db: Session, actor: Actor, elder_id: int) -> Elder:
    elder = ensure_elder(db, actor, elder_id, include_deleted=True)
    if not elder.is_deleted:
        raise Conflict("该档案未被删除")
    if db.execute(select(Elder.id).where(Elder.id_card_idx == elder.id_card_idx, Elder.is_deleted.is_(False))).first():
        raise Conflict("已存在相同身份证号的有效档案，不能恢复")
    elder.is_deleted = False
    elder.deleted_at = None
    db.add(ElderChangeLog(elder_id=elder.id, field="__restore__", before=None, after=None,
                          changed_by=actor.id, changed_by_name=actor.name))
    audit_service.write(db, actor.ctx, "ELDER_RESTORE", "elder", elder.id)
    return elder


def reveal_sensitive(db: Session, actor: Actor, elder_id: int, password: str, reason: str,
                     fields: list[str]) -> dict:
    """二次授权：校验权限、数据范围、本人口令和查看理由，然后留痕。"""
    actor.require("elder:reveal")
    elder = ensure_elder(db, actor, elder_id)
    if not reason or len(reason.strip()) < 2:
        raise ValidationFailed("查看敏感信息必须填写理由")
    invalid = set(fields) - SENSITIVE_FIELDS
    if invalid or not fields:
        raise ValidationFailed(f"不可查看的字段：{sorted(invalid)}")
    if not verify_password(password, actor.user.password_hash):
        audit_service.write(db, actor.ctx, "REVEAL_DENIED", "elder", elder.id, {"fields": fields})
        db.commit()
        raise Forbidden("二次授权失败：口令不正确", code="REAUTH_FAILED")
    db.add(SensitiveAccessLog(elder_id=elder.id, user_id=actor.id, user_name=actor.name, fields=fields,
                              reason=reason, ip=actor.ip))
    audit_service.write(db, actor.ctx, "REVEAL_SENSITIVE", "elder", elder.id, {"fields": fields, "reason": reason})
    return {field: getattr(elder, field) for field in fields}


def change_logs(db: Session, actor: Actor, elder_id: int) -> list[dict]:
    ensure_elder(db, actor, elder_id, include_deleted=True)
    rows = db.execute(select(ElderChangeLog).where(ElderChangeLog.elder_id == elder_id)
                      .order_by(ElderChangeLog.id.desc()).limit(200)).scalars()
    return [{"id": r.id, "field": r.field, "before": r.before, "after": r.after,
             "changed_by": r.changed_by_name, "changed_at": r.changed_at} for r in rows]


def access_logs(db: Session, actor: Actor, elder_id: int | None = None, limit: int = 200) -> list[dict]:
    stmt = select(SensitiveAccessLog).order_by(SensitiveAccessLog.id.desc()).limit(limit)
    if elder_id:
        ensure_elder(db, actor, elder_id, include_deleted=True)
        stmt = stmt.where(SensitiveAccessLog.elder_id == elder_id)
    return [{"id": r.id, "elder_id": r.elder_id, "user_name": r.user_name, "fields": r.fields, "reason": r.reason,
             "ip": r.ip, "accessed_at": r.accessed_at} for r in db.execute(stmt).scalars()]


def _parse_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    return [part.strip() for part in str(value).replace("，", ",").replace("、", ",").split(",") if part.strip()]


def _reverse_label(enum_name: str, value: Any, default: str) -> str:
    if value in (None, ""):
        return default
    text = str(value).strip()
    from app.domain.enums import LABELS
    labels = LABELS[enum_name]
    if text in labels:
        return text
    for code, name in labels.items():
        if name == text:
            return code
    raise ValueError(f"无法识别「{text}」")


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("/", "-").replace(".", "-")
    return datetime.strptime(text, "%Y-%m-%d").date()


def _read_rows(filename: str, content: bytes) -> list[dict]:
    if filename.lower().endswith((".xlsx", ".xlsm")):
        sheet = load_workbook(io.BytesIO(content), read_only=True, data_only=True).active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(c).strip() if c else "" for c in rows[0]]
        return [dict(zip(headers, row)) for row in rows[1:] if any(cell not in (None, "") for cell in row)]
    if filename.lower().endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("gbk")
        return [row for row in csv.DictReader(io.StringIO(text)) if any(row.values())]
    raise ValidationFailed("仅支持 .xlsx 或 .csv 文件")


def import_elders(db: Session, actor: Actor, filename: str, content: bytes, dry_run: bool = False) -> dict:
    """批量导入：逐行校验，单行失败不影响其他行，返回每个失败行的原因。"""
    rows = _read_rows(filename, content)
    if len(rows) > 5000:
        raise ValidationFailed("单次最多导入 5000 行")
    grids = {g.name: g for g in db.execute(select(Grid)).scalars()}
    created, errors = 0, []
    for index, row in enumerate(rows, start=2):
        try:
            mapped = {IMPORT_COLUMNS[k]: v for k, v in row.items() if k in IMPORT_COLUMNS and v not in (None, "")}
            grid = grids.get(str(mapped.pop("grid_name", "")).strip())
            if grid is None:
                raise ValueError("网格不存在")
            data: dict[str, Any] = {
                **mapped, "grid_id": grid.id, "birth_date": _parse_date(mapped.get("birth_date")),
                "id_card": str(mapped.get("id_card", "")).strip(),
                "chronic_diseases": _parse_list(mapped.get("chronic_diseases")),
                "medications": _parse_list(mapped.get("medications")),
                "care_tags": _parse_list(mapped.get("care_tags")),
                "disability_level": _reverse_label("DisabilityLevel", mapped.get("disability_level"), "INTACT"),
                "difficulty_type": _reverse_label("DifficultyType", mapped.get("difficulty_type"), "NONE"),
            }
            for key in ("phone", "emergency_phone"):
                if key in data:
                    data[key] = str(data[key]).strip()
            for key in ("lat", "lng"):
                if key in data:
                    data[key] = float(data[key])
            with db.begin_nested():
                create_elder(db, actor, data)
                if dry_run:
                    raise _DryRun()
            created += 1
        except _DryRun:
            created += 1
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({"row": index, "reason": str(exc)})
        except (ValidationFailed, Conflict, Forbidden, NotFound) as exc:
            errors.append({"row": index, "reason": exc.message})
    audit_service.write(db, actor.ctx, "ELDER_IMPORT", "elder", None,
                        {"file": filename, "created": created, "failed": len(errors), "dry_run": dry_run})
    return {"total": len(rows), "created": created, "failed": len(errors), "errors": errors[:200], "dry_run": dry_run}


class _DryRun(Exception):
    """试导入：跑完校验后回滚保存点。"""


def link_family(db: Session, actor: Actor, elder_id: int, user_id: int, relation: str) -> FamilyLink:
    ensure_elder(db, actor, elder_id)
    link = FamilyLink(user_id=user_id, elder_id=elder_id, relation=relation)
    db.add(link)
    audit_service.write(db, actor.ctx, "FAMILY_LINK", "elder", elder_id, {"user_id": user_id, "relation": relation})
    return link
