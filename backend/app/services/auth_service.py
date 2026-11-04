"""登录、账号管理、角色权限维护。"""
from __future__ import annotations

from datetime import timedelta
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock, crypto
from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound, Unauthorized, ValidationFailed
from app.core.security import check_password_strength, decode_token, hash_password, issue_token, verify_password
from app.domain.enums import DataScope, label
from app.models import Community, Grid, Organization, Permission, Role, User
from app.services import audit_service
from app.services.access import Actor, build_actor
from app.services.audit_service import AuditContext


@lru_cache
def _dummy_hash() -> str:
    """账号不存在时也跑一次 bcrypt，防止通过响应时间枚举账号。"""
    return hash_password("timing-equalizer")


def login(db: Session, username: str, password: str, ip: str | None) -> dict:
    settings = get_settings()
    user = db.execute(select(User).where(User.username == username)).scalar()
    now = clock.now()
    message = "用户名或密码错误"
    if user is None:
        verify_password(password, _dummy_hash())
        audit_service.write(db, AuditContext(None, username[:64], None, ip), "LOGIN_FAILED", "user", None, {"reason": "unknown"})
        db.commit()
        raise Unauthorized(message)
    ctx = AuditContext(user.id, user.display_name, user.role.code, ip)
    if user.locked_until and user.locked_until > now:
        raise Unauthorized(f"连续失败次数过多，账号已锁定至 {user.locked_until:%H:%M}", code="ACCOUNT_LOCKED")
    if not verify_password(password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            user.failed_logins = 0
            audit_service.write(db, ctx, "ACCOUNT_LOCKED", "user", user.id)
        audit_service.write(db, ctx, "LOGIN_FAILED", "user", user.id, {"reason": "password"})
        db.commit()
        raise Unauthorized(message)
    if not user.is_active:
        raise Unauthorized("账号已停用，请联系管理员")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    audit_service.write(db, ctx, "LOGIN", "user", user.id)
    return {"access_token": issue_token(user.id, user.token_version), "token_type": "bearer", "user": me_view(db, user)}


def resolve_token(db: Session, token: str, ip: str | None) -> Actor:
    payload = decode_token(token)
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active or user.token_version != payload.get("ver"):
        raise Unauthorized("登录已失效，请重新登录")
    return build_actor(db, user, ip)


def me_view(db: Session, user: User) -> dict:
    actor = build_actor(db, user)
    return {
        "id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role.code,
        "role_name": user.role.name, "data_scope": user.role.data_scope,
        "data_scope_label": label("DataScope", user.role.data_scope), "permissions": sorted(actor.permissions),
        "grid_id": user.grid_id, "grid_name": user.grid.name if user.grid else None, "community_id": user.community_id,
        "community_name": user.community.name if user.community else None, "org_id": user.org_id,
        "org_name": user.organization.name if user.organization else None, "caregiver_id": actor.caregiver_id,
    }


def change_password(db: Session, actor: Actor, old_password: str, new_password: str) -> None:
    if not verify_password(old_password, actor.user.password_hash):
        raise Forbidden("原密码不正确")
    check_password_strength(new_password)
    actor.user.password_hash = hash_password(new_password)
    actor.user.token_version += 1
    audit_service.write(db, actor.ctx, "PASSWORD_CHANGE", "user", actor.id)


def user_view(user: User) -> dict:
    return {
        "id": user.id, "username": user.username, "display_name": user.display_name,
        "phone": crypto.mask_phone(user.phone), "role": user.role.code, "role_name": user.role.name,
        "grid_id": user.grid_id, "grid_name": user.grid.name if user.grid else None, "community_id": user.community_id,
        "community_name": user.community.name if user.community else None, "org_id": user.org_id,
        "org_name": user.organization.name if user.organization else None, "is_active": user.is_active,
        "last_login_at": user.last_login_at,
    }


def list_users(db: Session, *, keyword: str | None = None, role: str | None = None, page: int = 1, size: int = 20) -> dict:
    stmt = select(User)
    if keyword:
        stmt = stmt.where(User.username.contains(keyword) | User.display_name.contains(keyword))
    if role:
        stmt = stmt.join(Role).where(Role.code == role)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(User.id).limit(size).offset((page - 1) * size)).scalars().all()
    return {"total": total, "page": page, "size": size, "items": [user_view(u) for u in rows]}


def _validate_user_org(db: Session, role: Role, data: dict) -> None:
    """账号的归属必须匹配角色的数据范围，否则数据范围过滤会落空。"""
    if data.get("grid_id"):
        grid = db.get(Grid, data["grid_id"])
        if grid is None:
            raise ValidationFailed("网格不存在")
        data["community_id"] = grid.community_id
    if data.get("community_id") and db.get(Community, data["community_id"]) is None:
        raise ValidationFailed("社区不存在")
    if data.get("org_id") and db.get(Organization, data["org_id"]) is None:
        raise ValidationFailed("机构不存在")
    if role.data_scope == DataScope.GRID and not data.get("grid_id"):
        raise ValidationFailed(f"「{role.name}」必须归属一个网格")
    if role.data_scope == DataScope.COMMUNITY and not data.get("community_id"):
        raise ValidationFailed(f"「{role.name}」必须归属一个社区")
    if role.data_scope == DataScope.ORG and not data.get("org_id"):
        raise ValidationFailed(f"「{role.name}」必须归属一家机构")


def create_user(db: Session, actor: Actor, data: dict) -> User:
    if db.execute(select(User.id).where(User.username == data["username"])).first():
        raise Conflict("用户名已存在")
    role = db.execute(select(Role).where(Role.code == data.pop("role"))).scalar()
    if role is None:
        raise ValidationFailed("角色不存在")
    _validate_user_org(db, role, data)
    password = data.pop("password")
    check_password_strength(password)
    user = User(password_hash=hash_password(password), role_id=role.id, **data)
    db.add(user)
    db.flush()
    audit_service.write(db, actor.ctx, "USER_CREATE", "user", user.id, {"username": user.username, "role": role.code})
    return user


def update_user(db: Session, actor: Actor, user_id: int, data: dict) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("用户不存在")
    new_role = data.pop("role", None)
    role = user.role
    if new_role:
        role = db.execute(select(Role).where(Role.code == new_role)).scalar()
        if role is None:
            raise ValidationFailed("角色不存在")
        user.role_id = role.id
    merged = {"grid_id": user.grid_id, "community_id": user.community_id, "org_id": user.org_id, **data}
    _validate_user_org(db, role, merged)
    if user.id == actor.id and data.get("is_active") is False:
        raise Conflict("不能停用自己的账号")
    for key in ("display_name", "phone", "grid_id", "community_id", "org_id", "is_active"):
        if key in merged and (key in data or key == "community_id"):
            setattr(user, key, merged[key])
    if data.get("is_active") is False or new_role:
        user.token_version += 1
    audit_service.write(db, actor.ctx, "USER_UPDATE", "user", user.id, {**data, "role": new_role})
    return user


def reset_password(db: Session, actor: Actor, user_id: int, new_password: str) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("用户不存在")
    check_password_strength(new_password)
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    user.failed_logins = 0
    user.locked_until = None
    audit_service.write(db, actor.ctx, "PASSWORD_RESET", "user", user.id)


def role_matrix(db: Session) -> dict:
    roles = db.execute(select(Role).order_by(Role.id)).scalars().all()
    permissions = db.execute(select(Permission).order_by(Permission.id)).scalars().all()
    return {
        "roles": [{"code": r.code, "name": r.name, "data_scope": r.data_scope,
                   "data_scope_label": label("DataScope", r.data_scope),
                   "permissions": sorted(p.code for p in r.permissions)} for r in roles],
        "permissions": [{"code": p.code, "name": p.name, "group": p.group} for p in permissions],
    }


def update_role_permissions(db: Session, actor: Actor, role_code: str, permissions: list[str]) -> Role:
    role = db.execute(select(Role).where(Role.code == role_code)).scalar()
    if role is None:
        raise NotFound("角色不存在")
    rows = db.execute(select(Permission).where(Permission.code.in_(permissions))).scalars().all()
    if len(rows) != len(set(permissions)):
        raise ValidationFailed("包含不存在的权限码")
    # 防止把自己锁在外面：超级管理员必须保留角色管理权限
    if role.code == "super_admin" and "role:manage" not in permissions:
        raise Conflict("超级管理员必须保留角色权限管理能力")
    before = sorted(p.code for p in role.permissions)
    role.permissions = list(rows)
    audit_service.write(db, actor.ctx, "ROLE_UPDATE", "role", role.code, {
        "added": sorted(set(permissions) - set(before)), "removed": sorted(set(before) - set(permissions))})
    return role
