"""部署级配置（环境变量 / .env）。业务参数放在 services/config_service.py，可在后台热更新。"""
from __future__ import annotations

import base64
import json
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"), env_prefix="YIHU_", extra="ignore"
    )

    env: Literal["dev", "test", "prod"] = "dev"
    demo_mode: bool = True
    data_dir: Path = PROJECT_DIR / "data"
    # 备份目录必须与生产库分离；生产环境建议挂载到独立磁盘。
    backup_dir: Path = PROJECT_DIR / "backups"
    database_url: str = ""
    host: str = "127.0.0.1"
    port: int = 8000
    static_dir: Path = BACKEND_DIR / "static"

    secret_key: str = ""
    field_key: str = ""  # base64 编码的 32 字节 AES-256 密钥
    index_key: str = ""  # 盲索引 HMAC 密钥
    device_api_key: str = ""  # 可穿戴网关上报密钥

    access_token_minutes: int = 480
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    bcrypt_rounds: int = 12
    max_upload_mb: int = 10

    scheduler_enabled: bool = True
    sms_provider: Literal["mock"] = "mock"

    # AI 辅助：不配置时走本地规则，配置后调用任意 OpenAI 兼容接口。
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    @model_validator(mode="after")
    def _fill_derived(self) -> Settings:
        self.data_dir = Path(self.data_dir)
        self.backup_dir = Path(self.backup_dir)
        if not self.database_url:
            self.database_url = f"sqlite:///{(self.data_dir / 'yihu.db').as_posix()}"
        if not all((self.secret_key, self.field_key, self.index_key, self.device_api_key)):
            if self.env == "prod":
                raise ValueError("生产环境必须通过环境变量显式提供全部密钥")
            self._load_or_create_dev_keys()
        if len(base64.b64decode(self.field_key)) != 32:
            raise ValueError("YIHU_FIELD_KEY 必须是 base64 编码的 32 字节密钥")
        return self

    def _load_or_create_dev_keys(self) -> None:
        """开发环境首次启动时生成随机密钥，避免把默认密钥写进代码仓库。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / "dev-secrets.json"
        if path.exists():
            keys = json.loads(path.read_text(encoding="utf-8"))
        else:
            keys = {
                "secret_key": secrets.token_urlsafe(48),
                "field_key": base64.b64encode(secrets.token_bytes(32)).decode(),
                "index_key": secrets.token_urlsafe(32),
                "device_api_key": secrets.token_urlsafe(24),
            }
            path.write_text(json.dumps(keys, indent=2), encoding="utf-8")
        for name, value in keys.items():
            if not getattr(self, name):
                setattr(self, name, value)

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_path(self) -> Path:
        return Path(self.database_url.removeprefix("sqlite:///"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
