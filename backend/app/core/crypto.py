"""字段级加密、盲索引与脱敏。

- 加密：AES-256-GCM，随机 96 位 nonce，AAD 绑定字段用途，密文搬到别的字段无法解密。
- 盲索引：HMAC-SHA256，支持身份证、手机号的精确查重和检索，库里不留明文。
- 脱敏：响应默认只返回脱敏值，完整值必须走二次授权接口。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_PREFIX = "v1:"


def _aes() -> AESGCM:
    return AESGCM(base64.b64decode(get_settings().field_key))


def encrypt(plaintext: str, purpose: str) -> str:
    nonce = os.urandom(12)
    sealed = _aes().encrypt(nonce, plaintext.encode("utf-8"), purpose.encode("utf-8"))
    return _PREFIX + base64.b64encode(nonce + sealed).decode("ascii")


def decrypt(token: str, purpose: str) -> str:
    if not token.startswith(_PREFIX):
        raise ValueError("未知的密文版本")
    raw = base64.b64decode(token[len(_PREFIX):])
    return _aes().decrypt(raw[:12], raw[12:], purpose.encode("utf-8")).decode("utf-8")


def blind_index(value: str) -> str:
    key = get_settings().index_key.encode("utf-8")
    return hmac.new(key, value.strip().upper().encode("utf-8"), hashlib.sha256).hexdigest()


def mask_id_card(value: str | None) -> str:
    if not value:
        return ""
    return value[:3] + "*" * max(len(value) - 7, 0) + value[-4:] if len(value) > 7 else "*" * len(value)


def mask_phone(value: str | None) -> str:
    if not value:
        return ""
    return value[:3] + "****" + value[-4:] if len(value) >= 7 else "*" * len(value)


def mask_address(value: str | None) -> str:
    """保留前 6 个字（区 + 街道级），门牌号不可见。"""
    if not value:
        return ""
    return value[:6] + "****" if len(value) > 6 else value[:2] + "****"


def mask_name(value: str | None) -> str:
    if not value:
        return ""
    return value[0] + "*" * (len(value) - 1)


def valid_id_card(value: str) -> bool:
    """校验 18 位身份证的格式与校验码。"""
    if len(value) != 18 or not value[:17].isdigit():
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    total = sum(int(c) * w for c, w in zip(value[:17], weights))
    return "10X98765432"[total % 11] == value[17].upper()


def valid_phone(value: str) -> bool:
    return len(value) == 11 and value.isdigit() and value[0] == "1"
