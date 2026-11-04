"""测试环境：一次性构建模板库，每个用例复制一份，用例之间完全隔离。"""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="yihu-test-"))
os.environ.update({
    "YIHU_ENV": "test", "YIHU_DEMO_MODE": "true", "YIHU_DATA_DIR": str(_ROOT / "data"),
    "YIHU_BACKUP_DIR": str(_ROOT / "backups"), "YIHU_BCRYPT_ROUNDS": "4", "YIHU_SCHEDULER_ENABLED": "false",
    "YIHU_SECRET_KEY": "test-secret-key-not-for-production-0123456789",
    "YIHU_FIELD_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=", "YIHU_INDEX_KEY": "test-index-key",
    "YIHU_DEVICE_API_KEY": "test-device-key", "YIHU_LLM_BASE_URL": "", "YIHU_LLM_API_KEY": "", "YIHU_LLM_MODEL": "",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import clock  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.bootstrap import init_database  # noqa: E402
from app.db.session import dispose_engine, new_session  # noqa: E402
from app.main import app  # noqa: E402
from tests.factory import World, build_world  # noqa: E402

NOW = datetime(2026, 6, 15, 10, 0, 0)
_TEMPLATE = _ROOT / "template.db"


def _db_path() -> Path:
    return get_settings().sqlite_path


@pytest.fixture(scope="session", autouse=True)
def _template_database():
    clock.freeze(NOW)
    init_database()
    db = new_session()
    build_world(db)
    db.commit()
    db.close()
    dispose_engine()
    shutil.copy(_db_path(), _TEMPLATE)
    yield
    dispose_engine()
    clock.reset()
    shutil.rmtree(_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fresh_database(_template_database):
    dispose_engine()
    for suffix in ("", "-wal", "-shm"):
        Path(str(_db_path()) + suffix).unlink(missing_ok=True)
    shutil.copy(_TEMPLATE, _db_path())
    clock.freeze(NOW)
    clock.set_offset_minutes(0)
    settings = get_settings()
    shutil.rmtree(settings.upload_dir, ignore_errors=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(settings.backup_dir, ignore_errors=True)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    yield
    dispose_engine()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def world() -> World:
    return World.load()


@pytest.fixture
def db():
    # 只读会话，只用于断言落库结果，不占写锁
    session = new_session(read_only=True)
    yield session
    session.rollback()
    session.close()


class Api:
    """带登录态的测试客户端。"""

    def __init__(self, client: TestClient):
        self.client = client
        self._tokens: dict[str, str] = {}

    def token(self, username: str) -> str:
        if username not in self._tokens:
            response = self.client.post("/api/auth/login", json={"username": username, "password": World.PASSWORD})
            assert response.status_code == 200, response.text
            self._tokens[username] = response.json()["access_token"]
        return self._tokens[username]

    def call(self, method: str, url: str, user: str, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token(user)}"
        return self.client.request(method, f"/api{url}", headers=headers, **kwargs)

    def get(self, url: str, user: str, **kwargs):
        return self.call("GET", url, user, **kwargs)

    def post(self, url: str, user: str, json: dict | None = None, **kwargs):
        return self.call("POST", url, user, json={} if json is None else json, **kwargs)

    def ok(self, method: str, url: str, user: str, **kwargs) -> dict:
        response = self.call(method, url, user, **kwargs)
        assert response.status_code < 300, f"{method} {url} -> {response.status_code}: {response.text}"
        return response.json()


@pytest.fixture
def api(client) -> Api:
    return Api(client)
