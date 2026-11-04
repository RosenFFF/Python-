"""API 依赖：当前用户解析和权限声明。"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import Unauthorized
from app.db.session import get_db
from app.services import auth_service
from app.services.access import Actor


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise Unauthorized("请先登录")
    return auth_service.resolve_token(db, header[7:], client_ip(request))


def require(*permissions: str) -> Callable[..., Actor]:
    """路由级权限声明。多个权限码满足其一即可；数据范围在服务层继续校验。"""

    def checker(actor: Actor = Depends(get_actor)) -> Actor:
        if len(permissions) == 1 or not any(actor.has(p) for p in permissions):
            actor.require(permissions[0])
        return actor

    return checker
