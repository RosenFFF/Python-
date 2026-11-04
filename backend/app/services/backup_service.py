"""备份与恢复。

备份：用 SQLite 在线备份 API 得到一致性快照，连同附件打成 zip，放在与生产库分离的目录，
带 manifest（文件哈希、各表行数、关键指标）；只保留最近 N 个版本。

恢复：校验完整性 → 自动做一次恢复前快照 → 二次确认（口令 + 确认词 + 短时令牌）→ 把快照
写回生产库 → 把恢复点之后的审计日志原样接回，恢复操作不能抹掉审计 → 输出核对报告。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import threading
import zipfile
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, Forbidden, NotFound, ValidationFailed
from app.core.security import verify_password
from app.db.session import dispose_engine, new_session
from app.services import audit_service, config_service
from app.services.access import Actor
from app.services.audit_service import AuditContext

CONFIRM_WORD = "确认恢复"
MANIFEST = "manifest.json"
DB_ENTRY = "database.sqlite3"
_NAME_PREFIX = "yihu-backup-"

# 恢复期间拒绝业务请求、暂停定时任务
maintenance = threading.Event()
_lock = threading.Lock()

KEY_METRICS = {
    "工单总数": "SELECT COUNT(*) FROM service_orders",
    "已完成工单": "SELECT COUNT(*) FROM service_orders WHERE status='COMPLETED'",
    "有效老人档案": "SELECT COUNT(*) FROM elders WHERE is_deleted=0",
    "未闭环预警": "SELECT COUNT(*) FROM alerts WHERE status!='CLOSED'",
    "费用明细金额(分)": "SELECT COALESCE(SUM(amount),0) FROM fee_details WHERE excluded=0",
}


def _backup_dir() -> Path:
    path = get_settings().backup_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _reports_dir() -> Path:
    path = _backup_dir() / "restore-reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='alembic_version'")]
    return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in sorted(tables)}


def _key_metrics(conn: sqlite3.Connection) -> dict[str, int]:
    return {name: conn.execute(sql).fetchone()[0] for name, sql in KEY_METRICS.items()}


def create_backup(ctx: AuditContext, kind: str = "AUTO", db: Session | None = None) -> dict:
    """生成一份全量备份。db 用于写审计，不传则自开会话。"""
    settings = get_settings()
    if not settings.is_sqlite:
        raise Conflict("当前实现的备份针对 SQLite；其他数据库请使用数据库原生备份工具")
    now = clock.now()
    name = f"{_NAME_PREFIX}{now:%Y%m%d-%H%M%S}.zip"
    target = _backup_dir() / name
    if target.exists():
        name = f"{_NAME_PREFIX}{now:%Y%m%d-%H%M%S}-{datetime.now().microsecond:06d}.zip"
        target = _backup_dir() / name
    with tempfile.TemporaryDirectory(dir=_backup_dir()) as tmp:
        snapshot = Path(tmp) / DB_ENTRY
        source = sqlite3.connect(settings.sqlite_path)
        dest = sqlite3.connect(snapshot)
        try:
            source.backup(dest)
            counts, metrics = _row_counts(dest), _key_metrics(dest)
            last_audit = dest.execute("SELECT COALESCE(MAX(id),0) FROM audit_logs").fetchone()[0]
        finally:
            dest.close()
            source.close()
        files: dict[str, str] = {}
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, DB_ENTRY)
            files[DB_ENTRY] = _sha256(snapshot)
            upload_dir = settings.upload_dir
            if upload_dir.exists():
                for path in sorted(upload_dir.rglob("*")):
                    if path.is_file():
                        entry = "uploads/" + path.relative_to(upload_dir).as_posix()
                        archive.write(path, entry)
                        files[entry] = _sha256(path)
            manifest = {
                "created_at": now.isoformat(), "kind": kind, "created_by": ctx.actor_name, "files": files,
                "row_counts": counts, "key_metrics": metrics, "last_audit_id": last_audit,
            }
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
    (target.with_suffix(".sha256")).write_text(_sha256(target), encoding="utf-8")
    own = db is None
    session = db or new_session()
    try:
        audit_service.write(session, ctx, "BACKUP", "backup", name, {"kind": kind, "size": target.stat().st_size})
        purged = purge_old(session)
        if own:
            session.commit()
    finally:
        if own:
            session.close()
    return {"name": name, "size": target.stat().st_size, "created_at": now, "kind": kind, "purged": purged}


def purge_old(db: Session) -> int:
    """按时间顺序清理超出保留版本数的备份。"""
    retain = int(config_service.get(db, "backup.retain"))
    files = sorted(_backup_dir().glob(f"{_NAME_PREFIX}*.zip"), key=lambda p: p.name, reverse=True)
    purged = 0
    for path in files[retain:]:
        path.unlink(missing_ok=True)
        path.with_suffix(".sha256").unlink(missing_ok=True)
        purged += 1
    return purged


def _safe_path(name: str) -> Path:
    if Path(name).name != name or not name.startswith(_NAME_PREFIX) or not name.endswith(".zip"):
        raise ValidationFailed("备份文件名不合法")
    path = _backup_dir() / name
    if not path.exists():
        raise NotFound("备份文件不存在")
    return path


def _read_manifest(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read(MANIFEST).decode("utf-8"))


def list_backups() -> list[dict]:
    result = []
    for path in sorted(_backup_dir().glob(f"{_NAME_PREFIX}*.zip"), key=lambda p: p.name, reverse=True):
        try:
            manifest = _read_manifest(path)
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError, zlib.error, EOFError):
            result.append({"name": path.name, "size": path.stat().st_size, "corrupted": True})
            continue
        result.append({
            "name": path.name, "size": path.stat().st_size, "created_at": manifest["created_at"],
            "kind": manifest["kind"], "created_by": manifest["created_by"], "key_metrics": manifest["key_metrics"],
            "file_count": len(manifest["files"]), "corrupted": False,
        })
    return result


def verify_backup(name: str) -> dict:
    """校验：整包哈希、包内逐文件哈希、SQLite integrity_check。"""
    path = _safe_path(name)
    problems: list[str] = []
    sidecar = path.with_suffix(".sha256")
    if not sidecar.exists():
        problems.append("缺少整包校验文件")
    elif sidecar.read_text(encoding="utf-8").strip() != _sha256(path):
        problems.append("整包哈希不一致，文件可能已损坏或被改动")
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
            for entry, expected in manifest["files"].items():
                if hashlib.sha256(archive.read(entry)).hexdigest() != expected:
                    problems.append(f"{entry} 哈希不一致")
            with tempfile.TemporaryDirectory(dir=_backup_dir()) as tmp:
                extracted = archive.extract(DB_ENTRY, tmp)
                conn = sqlite3.connect(extracted)
                try:
                    verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    conn.close()
                if verdict != "ok":
                    problems.append(f"数据库完整性检查失败：{verdict}")
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError, sqlite3.DatabaseError, zlib.error, EOFError, OSError) as exc:
        problems.append(f"备份包无法读取：{exc}")
    return {"name": name, "ok": not problems, "problems": problems}


def find_for_time(target: datetime) -> str:
    """按时间点恢复：取目标时间点之前最近的一份备份。"""
    for item in list_backups():
        if not item.get("corrupted") and datetime.fromisoformat(item["created_at"]) <= target:
            return item["name"]
    raise NotFound(f"{target:%Y-%m-%d %H:%M} 之前没有可用的备份")


def _current_state() -> tuple[dict, dict, int]:
    conn = sqlite3.connect(get_settings().sqlite_path)
    try:
        return _row_counts(conn), _key_metrics(conn), conn.execute("SELECT COALESCE(MAX(id),0) FROM audit_logs").fetchone()[0]
    finally:
        conn.close()


def preview_restore(actor: Actor, name: str | None, target_time: datetime | None) -> dict:
    """第一步：校验备份，展示影响范围，发放 5 分钟有效的确认令牌。"""
    name = name or (find_for_time(target_time) if target_time else None)
    if not name:
        raise ValidationFailed("请指定备份文件或目标时间点")
    verification = verify_backup(name)
    if not verification["ok"]:
        raise Conflict("备份文件校验未通过，禁止恢复", details=verification["problems"])
    manifest = _read_manifest(_safe_path(name))
    counts, metrics, _ = _current_state()
    impact = [
        {"table": table, "current": counts.get(table, 0), "after": manifest["row_counts"].get(table, 0),
         "delta": manifest["row_counts"].get(table, 0) - counts.get(table, 0)}
        for table in sorted(set(counts) | set(manifest["row_counts"]))
        if counts.get(table, 0) != manifest["row_counts"].get(table, 0)
    ]
    token = jwt.encode({"name": name, "uid": actor.id, "purpose": "restore",
                        "exp": datetime.now(UTC) + timedelta(minutes=5)},
                       get_settings().secret_key, algorithm="HS256")
    return {
        "name": name, "created_at": manifest["created_at"], "verification": verification,
        "key_metrics_current": metrics, "key_metrics_backup": manifest["key_metrics"], "impact": impact,
        "confirm_token": token, "confirm_word": CONFIRM_WORD,
    }


def _write_back(snapshot: Path) -> None:
    """用在线备份 API 反向写回。不替换文件，Windows 下也不怕文件句柄被占用，失败时整体回滚。"""
    settings = get_settings()
    source = sqlite3.connect(snapshot)
    dest = sqlite3.connect(settings.sqlite_path, timeout=30)
    try:
        source.backup(dest)
    finally:
        source.close()
        dest.close()


def _restore_uploads(archive: zipfile.ZipFile, stash: Path) -> int:
    upload_dir = get_settings().upload_dir
    if upload_dir.exists():
        shutil.move(str(upload_dir), str(stash))
    upload_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    root = upload_dir.resolve()
    for entry in archive.namelist():
        if not entry.startswith("uploads/") or entry.endswith("/"):
            continue
        target = (upload_dir / entry.removeprefix("uploads/")).resolve()
        if not target.is_relative_to(root):
            raise ValidationFailed(f"备份包含非法路径：{entry}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(entry))
        count += 1
    return count


def _carry_over_audit(rows: list[tuple], columns: list[str]) -> None:
    conn = sqlite3.connect(get_settings().sqlite_path, timeout=30)
    try:
        placeholders = ",".join("?" * len(columns))
        conn.executemany(f"INSERT INTO audit_logs ({','.join(columns)}) VALUES ({placeholders})", rows)
        conn.commit()
    finally:
        conn.close()


def confirm_restore(actor: Actor, token: str, password: str, confirm_word: str) -> dict:
    """第二步：校验确认令牌、本人口令和确认词后执行恢复。全局只允许一个恢复任务。"""
    if not verify_password(password, actor.user.password_hash):
        raise Forbidden("二次确认失败：口令不正确", code="REAUTH_FAILED")
    if confirm_word != CONFIRM_WORD:
        raise ValidationFailed(f"请输入确认词「{CONFIRM_WORD}」")
    try:
        claims = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise Conflict("确认令牌已失效，请重新发起恢复") from exc
    if claims.get("purpose") != "restore" or claims.get("uid") != actor.id:
        raise Forbidden("确认令牌与操作人不符")
    name = claims["name"]
    path = _safe_path(name)
    if not _lock.acquire(blocking=False):
        raise Conflict("已有恢复任务在执行")
    ctx = actor.ctx
    maintenance.set()
    try:
        verification = verify_backup(name)
        if not verification["ok"]:
            raise Conflict("备份文件校验未通过，禁止恢复", details=verification["problems"])
        safety = create_backup(ctx, "PRE_RESTORE")
        before_counts, before_metrics, _ = _current_state()
        manifest = _read_manifest(path)
        last_audit = manifest.get("last_audit_id", 0)
        conn = sqlite3.connect(get_settings().sqlite_path)
        try:
            cursor = conn.execute("SELECT * FROM audit_logs WHERE id > ? ORDER BY id", (last_audit,))
            columns = [c[0] for c in cursor.description]
            carried = cursor.fetchall()
        finally:
            conn.close()
        with tempfile.TemporaryDirectory(dir=_backup_dir()) as tmp, zipfile.ZipFile(path) as archive:
            snapshot = Path(archive.extract(DB_ENTRY, tmp))
            dispose_engine()
            _write_back(snapshot)
            _carry_over_audit(carried, columns)
            stash = Path(tmp) / "uploads-before"
            files_restored = _restore_uploads(archive, stash)
        after_counts, after_metrics, _ = _current_state()
        session = new_session()
        try:
            audit_service.write(session, ctx, "RESTORE", "backup", name, {
                "restored_to": manifest["created_at"], "pre_restore_backup": safety["name"],
                "audit_rows_carried": len(carried), "files_restored": files_restored,
            })
            session.commit()
        finally:
            session.close()
        compare = [
            {"metric": key, "before_restore": before_metrics.get(key), "backup": manifest["key_metrics"].get(key),
             "after_restore": after_metrics.get(key), "match": manifest["key_metrics"].get(key) == after_metrics.get(key)}
            for key in KEY_METRICS
        ]
        tables = [
            {"table": t, "before_restore": before_counts.get(t, 0), "backup": manifest["row_counts"].get(t, 0),
             "after_restore": after_counts.get(t, 0),
             # 审计表会接回恢复点之后的记录，行数比备份多是预期行为
             "match": t == "audit_logs" or manifest["row_counts"].get(t, 0) == after_counts.get(t, 0)}
            for t in sorted(set(before_counts) | set(after_counts))
        ]
        report = {
            "backup": name, "restored_to": manifest["created_at"], "operator": actor.name,
            "executed_at": clock.now().isoformat(), "pre_restore_backup": safety["name"],
            "audit_rows_carried": len(carried), "files_restored": files_restored,
            "tables": tables, "metrics": compare, "all_match": all(r["match"] for r in tables + compare),
        }
        report_path = _reports_dir() / f"restore-{clock.now():%Y%m%d-%H%M%S}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_file"] = report_path.name
        return report
    except AppError:
        raise
    finally:
        maintenance.clear()
        _lock.release()


def list_restore_reports() -> list[dict]:
    result = []
    for path in sorted(_reports_dir().glob("restore-*.json"), reverse=True)[:20]:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["report_file"] = path.name
        result.append(data)
    return result
