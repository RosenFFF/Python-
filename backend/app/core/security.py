"""口令哈希与访问令牌。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core import clock
from app.core.config import get_settings
from app.core.errors import Unauthorized, ValidationFailed

_ALG = "HS256"


def hash_password(password: str) -> str:
    rounds = get_settings().bcrypt_rounds
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def check_password_strength(password: str) -> None:
    kinds = [any(c.islower() for c in password), any(c.isupper() for c in password),
             any(c.isdigit() for c in password), any(not c.isalnum() for c in password)]
    if len(password) < 8 or sum(kinds) < 3:
        raise ValidationFailed("密码至少 8 位，且包含大写、小写、数字、符号中的三类")


def issue_token(user_id: int, token_version: int) -> str:
    settings = get_settings()
    # exp 用真实时间而不是演示时钟，时钟快进不会让登录失效。
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "exp": datetime.now(UTC) + timedelta(minutes=settings.access_token_minutes),
        "iat": int(clock.now().timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_ALG)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=[_ALG],
                          options={"verify_iat": False})
    except jwt.PyJWTError as exc:
        raise Unauthorized("登录已失效，请重新登录") from exc
