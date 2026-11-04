"""权限、审计、统计口径、备份恢复、AI 辅助、端到端流程。"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta

import pytest
from openpyxl import load_workbook
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core import clock
from app.core.config import get_settings
from app.db.session import new_session
from app.jobs.tasks import JOB_INDEX, purge_expired_audit, run_job
from app.models import AuditLog, FeeDetail, User
from app.services import ai_service, backup_service
from app.services.audit_service import verify_chain
from app.services.rbac_defaults import PERMISSIONS, ROLE_DEFAULTS
from tests.factory import LAT, LNG
from tests.helpers import TOMORROW_14, completed_order, create_order, dispatched_order

ALL_USERS = ["admin", "street", "comm_a", "grid_a1", "org_x", "family_a1", "cg_near"]


class TestAuth:
    def test_requires_token(self, client):
        assert client.get("/api/elders").status_code == 401

    def test_garbage_token_rejected(self, client):
        assert client.get("/api/elders", headers={"Authorization": "Bearer abc"}).status_code == 401

    def test_wrong_password_same_message_as_unknown_user(self, client):
        known = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).json()["message"]
        unknown = client.post("/api/auth/login", json={"username": "ghost", "password": "wrong"}).json()["message"]
        assert known == unknown

    def test_lockout_after_five_failures(self, client):
        for _ in range(5):
            assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
        response = client.post("/api/auth/login", json={"username": "admin", "password": "Test@2026"})
        assert response.status_code == 401 and response.json()["code"] == "ACCOUNT_LOCKED"
        clock.freeze(clock.now() + timedelta(minutes=16))
        assert client.post("/api/auth/login", json={"username": "admin", "password": "Test@2026"}).status_code == 200

    def test_password_change_invalidates_token(self, api, client):
        token = api.token("grid_a1")
        api.ok("POST", "/auth/change-password", "grid_a1", json={"old_password": "Test@2026", "new_password": "NewPass@2026"})
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    def test_weak_password_rejected(self, api):
        assert api.post("/auth/change-password", "grid_a1", json={"old_password": "Test@2026", "new_password": "abcdefgh"}).status_code == 422

    def test_disabled_user_token_invalid(self, api, client, db):
        token = api.token("grid_a1")
        user_id = db.execute(select(User.id).where(User.username == "grid_a1")).scalar()
        db.rollback()
        api.ok("PATCH", f"/admin/users/{user_id}", "admin", json={"is_active": False})
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    def test_me_exposes_permissions_not_hash(self, api):
        body = api.ok("GET", "/auth/me", "grid_a1")
        assert "dispatch:manage" in body["permissions"] and "password_hash" not in body

    def test_security_headers(self, client):
        response = client.get("/api/health-check")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "script-src 'self'" in response.headers["Content-Security-Policy"]


FORBIDDEN_MATRIX = [
    ("POST", "/orders/1/accept", ["family_a1", "cg_near", "org_x", "street", "admin"]),
    ("POST", "/orders/1/recommend", ["family_a1", "cg_near", "org_x", "street", "admin"]),
    ("POST", "/orders", ["cg_near", "org_x", "street", "admin"]),
    ("GET", "/admin/users", ["grid_a1", "comm_a", "org_x", "street", "family_a1", "cg_near"]),
    ("GET", "/admin/roles", ["grid_a1", "comm_a", "org_x", "street", "family_a1", "cg_near"]),
    ("GET", "/admin/configs", ["grid_a1", "comm_a", "org_x", "street", "family_a1", "cg_near"]),
    ("GET", "/admin/backups", ["grid_a1", "comm_a", "org_x", "street", "family_a1", "cg_near"]),
    ("GET", "/audit/logs", ["grid_a1", "comm_a", "org_x", "family_a1", "cg_near"]),
    ("POST", "/bills/1/review", ["grid_a1", "org_x", "street", "family_a1", "cg_near", "admin"]),
    ("POST", "/bills/1/org-confirm", ["grid_a1", "comm_a", "street", "family_a1", "cg_near", "admin"]),
    ("POST", "/samples/1/check", ["grid_a1", "comm_a", "org_x", "family_a1", "cg_near", "admin"]),
    ("POST", "/discrepancies/1/resolve", ["org_x", "street", "family_a1", "cg_near", "admin"]),
    ("GET", "/stats/dashboard/street", ["grid_a1", "comm_a", "org_x", "family_a1", "cg_near"]),
    ("GET", "/stats/dashboard/org", ["grid_a1", "comm_a", "family_a1", "cg_near"]),
    ("GET", "/stats/dashboard/community", ["org_x", "family_a1", "cg_near"]),
    ("POST", "/fees/manual", ["grid_a1", "street", "family_a1", "cg_near", "admin"]),
    ("POST", "/orders/1/check-in", ["grid_a1", "comm_a", "org_x", "street", "family_a1", "admin"]),
    ("POST", "/offers/1/accept", ["grid_a1", "comm_a", "org_x", "street", "family_a1", "admin"]),
    ("POST", "/orders/1/review", ["grid_a1", "comm_a", "org_x", "street", "cg_near", "admin"]),
    ("POST", "/admin/jobs/flush_outbox/run", ["grid_a1", "comm_a", "org_x", "street", "family_a1", "cg_near"]),
]
_SAMPLE_BODIES = {
    "/samples/1/check": {"passed": True}, "/discrepancies/1/resolve": {"resolution": "EXCLUDE", "explanation": "x"},
    "/fees/manual": {"order_id": 1, "amount": 1, "note": "x"}, "/orders/1/check-in": {"lat": 1, "lng": 1},
    "/bills/1/review": {"note": "x"}, "/orders/1/review": {"punctuality": 5, "attitude": 5, "professionalism": 5, "effectiveness": 5},
    "/orders": {"elder_id": 1, "item_id": 1, "scheduled_start": "2030-01-01T10:00:00"},
}


class TestRbacMatrix:
    @pytest.mark.parametrize("method,url,users", FORBIDDEN_MATRIX, ids=[f"{m} {u}" for m, u, _ in FORBIDDEN_MATRIX])
    def test_forbidden(self, api, method, url, users):
        for user in users:
            kwargs = {"json": _SAMPLE_BODIES.get(url, {})} if method == "POST" else {}
            response = api.call(method, url, user, **kwargs)
            assert response.status_code == 403, f"{user} {method} {url} -> {response.status_code}"

    def test_every_default_permission_is_defined(self):
        for _, permissions in ROLE_DEFAULTS.values():
            assert permissions <= set(PERMISSIONS)

    def test_revoking_permission_takes_effect_immediately(self, api):
        matrix = api.ok("GET", "/admin/roles", "admin")
        role = next(r for r in matrix["roles"] if r["code"] == "grid_worker")
        assert api.get("/elders", "grid_a1").status_code == 200
        api.ok("PUT", "/admin/roles/grid_worker/permissions", "admin", json={"permissions": [p for p in role["permissions"] if p != "elder:read"]})
        assert api.get("/elders", "grid_a1").status_code == 403
        assert api.ok("GET", "/audit/logs?action=ROLE_UPDATE", "admin")["total"] == 1

    def test_admin_cannot_lock_self_out(self, api):
        assert api.call("PUT", "/admin/roles/super_admin/permissions", "admin", json={"permissions": ["user:manage"]}).status_code == 409

    def test_super_admin_has_no_reveal_by_default(self):
        assert "elder:reveal" not in ROLE_DEFAULTS["super_admin"][1]

    def test_supervisor_is_read_only_on_elders(self, api, world):
        assert api.call("PATCH", f"/elders/{world.elder_a1}", "street", json={"name": "改名"}).status_code == 403


class TestDataScope:
    def test_elder_list_scope(self, api, world):
        ids = lambda user: {e["id"] for e in api.ok("GET", "/elders?size=100", user)["items"]}  # noqa: E731
        assert ids("grid_a1") == {world.elder_a1, world.elder_light}
        assert ids("grid_a2") == {world.elder_a2}
        assert ids("comm_a") == {world.elder_a1, world.elder_light, world.elder_a2}
        assert ids("comm_b") == {world.elder_b1}
        assert ids("family_a1") == {world.elder_a1}
        assert ids("street") == {world.elder_a1, world.elder_light, world.elder_a2, world.elder_b1}
        assert ids("org_x") == set()
        assert ids("cg_near") == set()

    def test_org_sees_elder_only_after_serving(self, api, world):
        dispatched_order(api, world)
        assert {e["id"] for e in api.ok("GET", "/elders", "org_x")["items"]} == {world.elder_a1}
        assert api.ok("GET", "/elders", "org_y")["total"] == 0

    def test_caregiver_sees_elder_only_after_offer(self, api, world):
        dispatched_order(api, world)
        assert {e["id"] for e in api.ok("GET", "/elders", "cg_near")["items"]} == {world.elder_a1}
        assert api.ok("GET", "/elders", "cg_mid")["total"] == 0

    @pytest.mark.parametrize("user", ["grid_a2", "comm_b", "grid_b1", "family_b1", "cg_mid", "org_y"])
    def test_idor_order_detail_blocked(self, api, world, user):
        order = dispatched_order(api, world)
        assert api.get(f"/orders/{order['id']}", user).status_code == 404

    @pytest.mark.parametrize("user", ["grid_a2", "comm_b", "family_b1", "org_y"])
    def test_idor_elder_detail_blocked(self, api, world, user):
        assert api.get(f"/elders/{world.elder_a1}", user).status_code == 404

    def test_idor_elder_update_blocked(self, api, world):
        assert api.call("PATCH", f"/elders/{world.elder_a1}", "grid_a2", json={"allergies": "x"}).status_code == 404

    def test_family_cannot_book_for_stranger(self, api, world):
        body = {"elder_id": world.elder_b1, "item_id": world.item_meal, "scheduled_start": TOMORROW_14.isoformat()}
        assert api.post("/orders", "family_a1", json=body).status_code == 404

    def test_order_list_scope(self, api, world):
        order = create_order(api, world)
        assert api.ok("GET", "/orders", "grid_a1")["total"] == 1
        assert api.ok("GET", "/orders", "grid_a2")["total"] == 0
        assert api.ok("GET", "/orders", "comm_a")["total"] == 1
        assert api.ok("GET", "/orders", "comm_b")["total"] == 0
        assert api.ok("GET", "/orders", "family_b1")["total"] == 0
        assert order["id"]

    def test_alert_scope(self, api, world):
        api.ok("POST", "/sos", "family_a1", json={"elder_id": world.elder_a1})
        assert api.ok("GET", "/alerts", "grid_a1")["total"] == 1
        assert api.ok("GET", "/alerts", "grid_a2")["total"] == 0
        assert api.ok("GET", "/alerts", "family_a1")["total"] == 1
        assert api.ok("GET", "/alerts", "family_b1")["total"] == 0
        assert api.ok("GET", "/alerts", "street")["total"] == 1

    def test_bill_scope(self, api, world):
        completed_order(api, world)
        bill = api.ok("POST", "/bills/generate", "comm_a", json={"community_id": world.community_a, "month": "2026-06"})
        assert api.get(f"/bills/{bill['id']}", "comm_b").status_code == 404
        assert api.get(f"/bills/{bill['id']}", "grid_b1").status_code == 404
        assert api.get(f"/bills/{bill['id']}", "org_y").status_code == 404
        assert api.get(f"/bills/{bill['id']}", "org_x").status_code == 200

    def test_dashboard_scope_differs(self, api, world):
        completed_order(api, world)
        completed_order(api, world, elder=world.elder_a2, user="grid_a2", grid="grid_a2", item=world.item_meal, minutes=30,
                        start=TOMORROW_14 + timedelta(days=1))
        kw = "?start=2026-06-01&end=2026-06-30"
        count = lambda user, kind="community": api.ok("GET", f"/stats/dashboard/{kind}{kw}", user)["kpis"][0]["value"]  # noqa: E731
        assert count("grid_a1") == 1 and count("grid_a2") == 1 and count("comm_a") == 2
        assert count("comm_b") == 0 and count("street", "street") == 2

    def test_notifications_are_private(self, api, world, db):
        create_order(api, world)
        note = api.ok("GET", "/notifications", "grid_a1")["items"][0]
        assert api.post(f"/notifications/{note['id']}/read", "grid_a2").status_code == 404

    def test_user_org_consistency_enforced(self, api):
        body = {"username": "x1", "password": "Abcd@1234", "display_name": "x", "role": "grid_worker"}
        assert api.post("/admin/users", "admin", json=body).status_code == 422
        body = {"username": "x2", "password": "Abcd@1234", "display_name": "x", "role": "org_admin"}
        assert api.post("/admin/users", "admin", json=body).status_code == 422


class TestAudit:
    def test_chain_valid_after_operations(self, api, world, db):
        for _ in range(3):
            api.ok("PATCH", f"/elders/{world.elder_a1}", "grid_a1", json={"allergies": f"过敏{clock.now()}"})
            clock.freeze(clock.now() + timedelta(seconds=1))
        result = api.ok("GET", "/audit/verify", "street")
        assert result["ok"] and result["checked"] >= 3

    def test_update_blocked_by_trigger(self, api, world):
        api.ok("POST", "/sos", "family_a1", json={"elder_id": world.elder_a1})
        with new_session() as session, pytest.raises(IntegrityError, match="append-only"):
            session.execute(text("UPDATE audit_logs SET actor_name='tamper'"))

    def test_delete_within_180_days_blocked(self):
        with new_session() as session:
            session.execute(text("INSERT INTO audit_logs (at, actor_name, action, prev_hash, hash) VALUES (datetime('now','+8 hours'), 's', 'X', 'a', 'b')"))
            session.commit()
            with pytest.raises(IntegrityError, match="retention"):
                session.execute(text("DELETE FROM audit_logs"))
            session.rollback()

    def test_expired_row_can_be_purged(self):
        with new_session() as session:
            session.execute(text("INSERT INTO audit_logs (at, actor_name, action, prev_hash, hash) VALUES (datetime('now','-400 days'), 's', 'OLD', 'a', 'b')"))
            session.commit()
            assert session.execute(text("DELETE FROM audit_logs WHERE action='OLD'")).rowcount == 1
            session.rollback()

    def test_no_api_route_to_modify_audit(self, client):
        paths = client.app.openapi()["paths"]
        methods = [(path, method) for path, ops in paths.items() if "/audit/" in path for method in ops]
        assert all(method in ("get", "post") for _, method in methods)
        assert {path for path, method in methods if method == "post"} == {"/api/audit/export"}

    def test_tampering_detected_when_triggers_bypassed(self, api, world):
        api.ok("PATCH", f"/elders/{world.elder_a1}", "grid_a1", json={"allergies": "青霉素"})
        api.ok("PATCH", f"/elders/{world.elder_a1}", "grid_a1", json={"allergies": "海鲜"})
        session = new_session()
        session.execute(text("DROP TRIGGER audit_logs_no_update"))
        row_id = session.execute(text("SELECT id FROM audit_logs WHERE action='ELDER_UPDATE' ORDER BY id LIMIT 1")).scalar()
        session.execute(text("UPDATE audit_logs SET detail='{\"fields\":[\"伪造\"]}' WHERE id=:i"), {"i": row_id})
        session.commit()
        session.close()
        result = api.ok("GET", "/audit/verify", "street")
        assert not result["ok"] and result["broken_at"] == row_id and "篡改" in result["reason"]

    def test_deleted_row_detected(self, api, world):
        for index in range(3):
            api.ok("PATCH", f"/elders/{world.elder_a1}", "grid_a1", json={"allergies": f"a{index}"})
        session = new_session()
        session.execute(text("DROP TRIGGER audit_logs_no_delete_within_retention"))
        ids = session.execute(text("SELECT id FROM audit_logs WHERE action='ELDER_UPDATE' ORDER BY id")).scalars().all()
        session.execute(text("DELETE FROM audit_logs WHERE id=:i"), {"i": ids[1]})
        session.commit()
        session.close()
        result = api.ok("GET", "/audit/verify", "street")
        assert not result["ok"] and "删除" in result["reason"]

    @pytest.mark.parametrize("action,setup", [("ELDER_UPDATE", "elder"), ("ELDER_DELETE", "delete"), ("ORDER_REASSIGN", "reassign"), ("EXPORT", "export")])
    def test_key_actions_audited(self, api, world, action, setup):
        if setup == "elder":
            api.ok("PATCH", f"/elders/{world.elder_a1}", "grid_a1", json={"allergies": "x"})
        elif setup == "delete":
            api.ok("DELETE", f"/elders/{world.elder_a2}", "comm_a", json={"reason": "搬离"})
        elif setup == "reassign":
            order = dispatched_order(api, world)
            api.ok("POST", f"/orders/{order['id']}/reassign", "grid_a1", json={"caregiver_id": world.c_mid, "reason": "请假"})
        else:
            api.post("/stats/dashboard/community/export", "grid_a1")
        logs = api.ok("GET", f"/audit/logs?action={action}", "street")["items"]
        assert logs and logs[0]["actor_name"] and logs[0]["at"]

    def test_audit_detail_contains_no_plain_sensitive_value(self, api, world):
        api.ok("PATCH", f"/elders/{world.elder_a1}", "grid_a1", json={"phone": "13712345678"})
        logs = api.ok("GET", "/audit/logs?action=ELDER_UPDATE", "street")["items"]
        assert "13712345678" not in logs[0]["detail"]

    def test_retention_job_only_removes_expired(self, api, world):
        api.ok("POST", "/sos", "family_a1", json={"elder_id": world.elder_a1})
        session = new_session()
        before = session.execute(select(AuditLog)).scalars().all()
        assert before
        assert purge_expired_audit(session) == 0
        session.rollback()
        session.close()

    def test_retention_config_below_180_rejected(self, api):
        assert api.call("PUT", "/admin/configs/audit.retention_days", "admin", json={"value": 30}).status_code == 422


class TestStatsCaliber:
    RANGE = "?start=2026-06-01&end=2026-06-30"

    def _kpi(self, data, name):
        return next(k for k in data["kpis"] if k["name"] == name)

    def test_service_count_and_coverage(self, api, world):
        completed_order(api, world)
        data = api.ok("GET", f"/stats/dashboard/community{self.RANGE}", "comm_a")
        assert self._kpi(data, "服务量")["value"] == 1
        assert self._kpi(data, "服务覆盖率")["value"] == pytest.approx(1 / 3, abs=1e-3)

    def test_mom_uses_previous_full_month(self, api, world):
        for offset in (0, 2):
            completed_order(api, world, start=datetime(2026, 7, 10 + offset, 14, 0), item=world.item_meal, minutes=30)
        completed_order(api, world, start=datetime(2026, 8, 10, 14, 0), item=world.item_meal, minutes=30)
        data = api.ok("GET", "/stats/dashboard/community?start=2026-08-01&end=2026-08-31", "comm_a")
        kpi = self._kpi(data, "服务量")
        assert kpi["value"] == 1 and kpi["previous"] == 2 and kpi["mom"] == -0.5

    def test_mom_none_when_no_base(self, api, world):
        completed_order(api, world)
        assert self._kpi(api.ok("GET", f"/stats/dashboard/community{self.RANGE}", "comm_a"), "服务量")["mom"] is None

    def test_yoy_uses_same_period_last_year(self, api, world):
        session = new_session()
        session.commit()
        session.close()
        completed_order(api, world, start=datetime(2026, 6, 20, 14, 0))
        order = completed_order(api, world, start=datetime(2027, 6, 20, 14, 0), item=world.item_meal, minutes=30)
        data = api.ok("GET", "/stats/dashboard/community?start=2027-06-01&end=2027-06-30", "comm_a")
        kpi = self._kpi(data, "服务量")
        assert kpi["year_ago"] == 1 and kpi["yoy"] == 0.0
        assert order["id"]

    def test_uncompleted_not_counted(self, api, world):
        dispatched_order(api, world)
        assert self._kpi(api.ok("GET", f"/stats/dashboard/community{self.RANGE}", "comm_a"), "服务量")["value"] == 0

    def test_todo_counts(self, api, world):
        create_order(api, world)
        assert self._kpi(api.ok("GET", f"/stats/dashboard/community{self.RANGE}", "comm_a"), "待办工单")["value"] == 1

    def test_response_median(self, api, world):
        for delay in (10, 2, 30):
            alert = api.ok("POST", "/sos", "family_a1", json={"elder_id": world.elder_a1})
            clock.freeze(clock.now() + timedelta(minutes=delay))
            api.ok("POST", f"/alerts/{alert['id']}/close", "grid_a1", json={"conclusion": "HANDLED", "measures": "x"})
        data = api.ok("GET", f"/stats/dashboard/community{self.RANGE}", "comm_a")
        assert self._kpi(data, "预警响应中位数")["value"] == 10
        assert data["alert_timeliness"]["on_time_rate"] == 1.0

    def test_four_chart_types_present(self, api, world):
        completed_order(api, world)
        for kind, user in (("community", "comm_a"), ("org", "org_x"), ("street", "street")):
            charts = api.ok("GET", f"/stats/dashboard/{kind}{self.RANGE}", user)["charts"]
            assert set(charts) == {"trend", "share", "ranking", "distribution"}

    def test_day_week_month_granularity(self, api, world):
        for granularity, count in (("day", 30), ("week", 5), ("month", 1)):
            data = api.ok("GET", f"/stats/dashboard/community{self.RANGE}&granularity={granularity}", "comm_a")
            assert len(data["charts"]["trend"]["x"]) == count

    def test_custom_range_validation(self, api):
        assert api.get("/stats/dashboard/community?start=2026-06-30&end=2026-06-01", "comm_a").status_code == 422
        assert api.get("/stats/dashboard/community?granularity=year", "comm_a").status_code == 422

    def test_trend_sums_to_total(self, api, world):
        completed_order(api, world)
        completed_order(api, world, start=TOMORROW_14 + timedelta(days=3))
        data = api.ok("GET", f"/stats/dashboard/community{self.RANGE}&granularity=week", "comm_a")
        assert sum(data["charts"]["trend"]["series"][0]["data"]) == 2

    def test_street_subsidy_matches_fees(self, api, world, db):
        order = completed_order(api, world)
        fee = db.execute(select(FeeDetail).where(FeeDetail.order_id == order["id"])).scalar()
        db.rollback()
        data = api.ok("GET", f"/stats/dashboard/street{self.RANGE}", "street")
        assert self._kpi(data, "政府补贴支出")["value"] == fee.gov_amount
        assert self._kpi(data, "长护险支付")["value"] == fee.ltci_amount

    def test_org_revenue_structure(self, api, world):
        completed_order(api, world)
        completed_order(api, world, item=world.item_meal, minutes=30, start=TOMORROW_14 + timedelta(days=1))
        share = api.ok("GET", f"/stats/dashboard/org{self.RANGE}", "org_x")["charts"]["share"]["data"]
        assert {r["name"]: r["value"] for r in share} == {"助浴": 80.0, "助餐": 15.0}
        assert sum(r["share"] for r in share) == pytest.approx(1, abs=1e-3)

    def test_satisfaction_distribution(self, api, world):
        order = completed_order(api, world)
        api.ok("POST", f"/orders/{order['id']}/review", "family_a1", json={"punctuality": 5, "attitude": 5, "professionalism": 5, "effectiveness": 5})
        data = api.ok("GET", f"/stats/dashboard/street{self.RANGE}", "street")["charts"]["distribution"]["data"]
        assert {r["name"]: r["value"] for r in data}["4.5-5星"] == 1

    def test_export_xlsx_and_csv(self, api, world):
        completed_order(api, world)
        response = api.post(f"/stats/dashboard/community/export{self.RANGE}&fmt=xlsx", "comm_a")
        sheet = load_workbook(io.BytesIO(response.content)).active
        assert [c.value for c in sheet[1]][:2] == ["分组", "名称"]
        assert any(row[1] == "服务量" for row in sheet.iter_rows(values_only=True))
        assert "服务量" in api.post(f"/stats/dashboard/community/export{self.RANGE}&fmt=csv", "comm_a").content.decode("utf-8-sig")


class TestBackupRestore:
    def _backup(self, api):
        return api.ok("POST", "/admin/backups", "admin")["name"]

    def test_backup_contains_db_uploads_and_manifest(self, api, world):
        (get_settings().upload_dir / "x.jpg").write_bytes(b"photo")
        name = self._backup(api)
        with zipfile.ZipFile(get_settings().backup_dir / name) as archive:
            names = archive.namelist()
        assert "database.sqlite3" in names and "uploads/x.jpg" in names and "manifest.json" in names
        assert get_settings().backup_dir.resolve() != get_settings().data_dir.resolve()

    def test_listed_with_metrics(self, api):
        self._backup(api)
        item = api.ok("GET", "/admin/backups", "admin")["backups"][0]
        assert item["kind"] == "MANUAL" and "工单总数" in item["key_metrics"]

    def test_retention_keeps_latest_30(self, api):
        api.ok("PUT", "/admin/configs/backup.retain", "admin", json={"value": 3})
        names = []
        for _ in range(5):
            names.append(self._backup(api))
            clock.freeze(clock.now() + timedelta(days=1))
        kept = [b["name"] for b in api.ok("GET", "/admin/backups", "admin")["backups"]]
        assert kept == list(reversed(names[-3:]))

    def test_default_retention_is_30(self, api):
        config = next(c for c in api.ok("GET", "/admin/configs", "admin") if c["key"] == "backup.retain")
        assert config["value"] == 30

    def test_verify_detects_corruption(self, api):
        name = self._backup(api)
        assert api.ok("POST", f"/admin/backups/{name}/verify", "admin")["ok"]
        path = get_settings().backup_dir / name
        data = bytearray(path.read_bytes())
        data[100] ^= 0xFF
        path.write_bytes(bytes(data))
        result = api.ok("POST", f"/admin/backups/{name}/verify", "admin")
        assert not result["ok"]
        assert api.post("/admin/backups/restore/preview", "admin", json={"name": name}).status_code == 409

    def test_path_traversal_rejected(self, api):
        assert api.post("/admin/backups/restore/preview", "admin", json={"name": "../../etc/passwd"}).status_code == 422

    def test_restore_needs_token_password_and_word(self, api):
        name = self._backup(api)
        preview = api.ok("POST", "/admin/backups/restore/preview", "admin", json={"name": name})
        token = preview["confirm_token"]
        assert api.post("/admin/backups/restore/confirm", "admin", json={"confirm_token": token, "password": "wrong", "confirm_word": "确认恢复"}).status_code == 403
        assert api.post("/admin/backups/restore/confirm", "admin", json={"confirm_token": token, "password": "Test@2026", "confirm_word": "好"}).status_code == 422
        assert api.post("/admin/backups/restore/confirm", "admin", json={"confirm_token": "bad", "password": "Test@2026", "confirm_word": "确认恢复"}).status_code == 409

    def test_preview_shows_impact(self, api, world):
        name = self._backup(api)
        create_order(api, world)
        preview = api.ok("POST", "/admin/backups/restore/preview", "admin", json={"name": name})
        orders = next(i for i in preview["impact"] if i["table"] == "service_orders")
        assert orders == {"table": "service_orders", "current": 1, "after": 0, "delta": -1}

    def test_full_restore_brings_back_data_and_keeps_audit(self, api, world):
        order_before = create_order(api, world)
        name = self._backup(api)
        clock.freeze(clock.now() + timedelta(hours=1))
        api.ok("POST", f"/orders/{order_before['id']}/cancel", "family_a1", json={"reason": "误取消"})
        api.ok("DELETE", f"/elders/{world.elder_a2}", "comm_a", json={"reason": "误删"})
        preview = api.ok("POST", "/admin/backups/restore/preview", "admin", json={"name": name})
        report = api.ok("POST", "/admin/backups/restore/confirm", "admin", json={
            "confirm_token": preview["confirm_token"], "password": "Test@2026", "confirm_word": "确认恢复"})
        assert report["all_match"], report
        assert report["operator"] == "admin"
        assert api.ok("GET", f"/orders/{order_before['id']}", "family_a1")["status"] == "PENDING_ACCEPT"
        assert api.get(f"/elders/{world.elder_a2}", "comm_a").status_code == 200
        # 恢复点之后的审计日志被接回，恢复操作本身也有记录
        assert api.ok("GET", "/audit/logs?action=ELDER_DELETE", "street")["total"] == 1
        assert api.ok("GET", "/audit/logs?action=RESTORE", "street")["items"][0]["actor_name"] == "admin"
        assert api.ok("GET", "/audit/verify", "street")["ok"]
        assert any(b["kind"] == "PRE_RESTORE" for b in api.ok("GET", "/admin/backups", "admin")["backups"])
        assert api.ok("GET", "/admin/backups", "admin")["reports"][0]["backup"] == name

    def test_restore_brings_back_uploads(self, api, world):
        (get_settings().upload_dir / "keep.jpg").write_bytes(b"photo")
        name = self._backup(api)
        (get_settings().upload_dir / "keep.jpg").unlink()
        preview = api.ok("POST", "/admin/backups/restore/preview", "admin", json={"name": name})
        api.ok("POST", "/admin/backups/restore/confirm", "admin", json={"confirm_token": preview["confirm_token"], "password": "Test@2026", "confirm_word": "确认恢复"})
        assert (get_settings().upload_dir / "keep.jpg").read_bytes() == b"photo"

    def test_point_in_time_picks_latest_before_target(self, api):
        first = self._backup(api)
        clock.freeze(clock.now() + timedelta(days=1))
        second = self._backup(api)
        mid = (clock.now() - timedelta(hours=1)).isoformat()
        assert api.ok("POST", "/admin/backups/restore/preview", "admin", json={"target_time": mid})["name"] == first
        assert api.ok("POST", "/admin/backups/restore/preview", "admin", json={"target_time": clock.now().isoformat()})["name"] == second
        too_early = (clock.now() - timedelta(days=30)).isoformat()
        assert api.post("/admin/backups/restore/preview", "admin", json={"target_time": too_early}).status_code == 404

    def test_token_bound_to_operator(self, api, world, db):
        name = self._backup(api)
        token = api.ok("POST", "/admin/backups/restore/preview", "admin", json={"name": name})["confirm_token"]
        session = new_session()
        user = session.execute(select(User).where(User.username == "street")).scalar()
        from app.models import Role
        role = session.execute(select(Role).where(Role.code == "super_admin")).scalar()
        user.role_id = role.id
        session.commit()
        session.close()
        response = api.post("/admin/backups/restore/confirm", "street", json={"confirm_token": token, "password": "Test@2026", "confirm_word": "确认恢复"})
        assert response.status_code == 403

    def test_daily_backup_job(self):
        run = run_job(JOB_INDEX["daily_backup"])
        assert run.ok == 1
        assert backup_service.list_backups()[0]["kind"] == "AUTO"

    def test_maintenance_blocks_api(self, client):
        backup_service.maintenance.set()
        try:
            assert client.get("/api/meta").status_code == 503
        finally:
            backup_service.maintenance.clear()


class TestAi:
    def test_parse_booking_prefills_form(self, api, world):
        body = api.ok("POST", "/ai/parse-booking", "family_a1", json={"text": "帮我约明天下午两点助浴"})
        assert body["item_id"] == world.item_bath
        assert datetime.fromisoformat(body["scheduled_at"]) == TOMORROW_14
        assert body["engine"] == "local"

    def test_parse_missing_parts(self, api):
        assert api.ok("POST", "/ai/parse-booking", "family_a1", json={"text": "你好"})["missing"] == ["服务项目", "日期", "时间"]

    def test_risk_profile_explains_factors(self, api, world):
        rows = api.ok("GET", "/ai/top-risk", "comm_a")
        top = rows[0]
        assert top["elder_id"] == world.elder_b1 or top["score"] >= rows[-1]["score"]
        assert all("points" in f and "detail" in f for f in top["factors"])

    def test_alert_raises_risk(self, api, world):
        before = next(r for r in api.ok("GET", "/ai/top-risk", "grid_a1") if r["elder_id"] == world.elder_a1)["score"]
        api.ok("POST", "/sos", "family_a1", json={"elder_id": world.elder_a1})
        after = next(r for r in api.ok("GET", "/ai/top-risk", "grid_a1") if r["elder_id"] == world.elder_a1)["score"]
        assert after > before

    def test_summary_offline(self, api, world):
        body = api.ok("GET", f"/ai/elders/{world.elder_a1}/summary", "grid_a1")
        assert "没有体征数据" in body["summary"] and body["engine"] == "local"

    def test_summary_detects_rising_trend(self, api, world):
        base = clock.now() - timedelta(days=10)
        points = [{"metric": "SYSTOLIC", "value": 120 + index * 3, "measured_at": (base + timedelta(days=index)).isoformat()} for index in range(8)]
        api.ok("POST", f"/elders/{world.elder_a1}/health", "grid_a1", json={"points": points})
        body = api.ok("GET", f"/ai/elders/{world.elder_a1}/summary", "grid_a1")
        assert next(m for m in body["metrics"] if m["metric"] == "收缩压")["trend"] == "上升"
        assert "收缩压" in body["suggestions"][0]

    def test_llm_failure_falls_back(self, monkeypatch, api, world):
        monkeypatch.setattr(ai_service, "llm_enabled", lambda: True)
        monkeypatch.setattr(ai_service, "_chat", lambda *a: None)
        base = clock.now() - timedelta(days=1)
        api.ok("POST", f"/elders/{world.elder_a1}/health", "grid_a1", json={"points": [{"metric": "SYSTOLIC", "value": 130, "measured_at": base.isoformat()}]})
        assert api.ok("GET", f"/ai/elders/{world.elder_a1}/summary", "grid_a1")["engine"] == "local"

    def test_llm_payload_has_no_identity(self, monkeypatch, api, world):
        captured = {}

        def fake_chat(system, user):
            captured["user"] = user
            return "摘要"

        monkeypatch.setattr(ai_service, "llm_enabled", lambda: True)
        monkeypatch.setattr(ai_service, "_chat", fake_chat)
        base = clock.now() - timedelta(days=1)
        api.ok("POST", f"/elders/{world.elder_a1}/health", "grid_a1", json={"points": [{"metric": "SYSTOLIC", "value": 130, "measured_at": base.isoformat()}]})
        assert api.ok("GET", f"/ai/elders/{world.elder_a1}/summary", "grid_a1")["engine"] == "llm"
        assert "老人甲" not in captured["user"] and "青松路" not in captured["user"]


class TestDemoClock:
    def test_advance_runs_time_jobs(self, api, world):
        clock.freeze(None)
        clock.set_offset_minutes(0)
        alert = api.ok("POST", "/sos", "family_a1", json={"elder_id": world.elder_a1})
        result = api.ok("POST", "/demo/clock/advance", "grid_a1", json={"minutes": 31})
        assert result["jobs"]["alert_escalation"] == 1
        assert api.ok("GET", f"/alerts/{alert['id']}", "grid_a1")["escalated_at"]
        api.ok("POST", "/demo/clock/reset", "grid_a1")

    def test_disabled_outside_demo_mode(self, api, monkeypatch):
        monkeypatch.setattr(get_settings(), "demo_mode", False)
        assert api.post("/demo/clock/advance", "grid_a1", json={"minutes": 1}).status_code == 403


class TestEndToEndSpecFlow:
    """按赛题流程描述完整走一遍：78 岁中度失能老人，次日 14:00 助浴，直到结算生效。"""

    def test_whole_journey(self, api, world, db):
        # 1. 家属预约，三项校验通过，推送网格员
        order = api.ok("POST", "/orders", "family_a1", json={
            "elder_id": world.elder_a1, "item_id": world.item_bath, "scheduled_start": TOMORROW_14.isoformat(), "remark": "腿脚不便"})
        assert order["status"] == "PENDING_ACCEPT" and not order["needs_manual"]
        oid = order["id"]
        assert any(n["title"] == "新预约待受理" for n in api.ok("GET", "/notifications", "grid_a1")["items"])
        # 2. 网格员确认 → 待派单，列出前 3 名
        assert api.ok("POST", f"/orders/{oid}/accept", "grid_a1", json={})["status"] == "PENDING_DISPATCH"
        rec = api.ok("POST", f"/orders/{oid}/recommend", "grid_a1")
        assert len(rec["candidates"]) == 3
        # 3. 派给第一名，30 分钟无人接单，自动顺延；第二名接单
        assignment = api.ok("POST", f"/orders/{oid}/assign", "grid_a1", json={"caregiver_id": rec["candidates"][0]["caregiver_id"], "round_id": rec["round_id"]})
        clock.freeze(clock.now() + timedelta(minutes=30))
        run_job(JOB_INDEX["expire_offers"])
        offers = api.ok("GET", "/offers/mine", "cg_mid")
        assert offers and offers[0]["order_id"] == oid
        api.ok("POST", f"/offers/{offers[0]['id']}/accept", "cg_mid")
        assert api.ok("GET", f"/orders/{oid}", "family_a1")["status"] == "DISPATCHED"
        assert assignment["offer_id"]
        # 4. 服务前 15 分钟提醒
        clock.freeze(TOMORROW_14 - timedelta(minutes=15))
        assert run_job(JOB_INDEX["visit_reminder"]).processed == 1
        # 5. 签到超 200 米须说明；按清单勾选；上传照片；测血压；签退
        clock.freeze(TOMORROW_14 - timedelta(minutes=2))
        far = {"lat": LAT + 0.004, "lng": LNG}
        assert api.post(f"/orders/{oid}/check-in", "cg_mid", json=far).status_code == 422
        api.ok("POST", f"/orders/{oid}/check-in", "cg_mid", json={**far, "note": "小区门禁，地址标注偏差"})
        api.ok("PUT", f"/orders/{oid}/checklist", "cg_mid", json={"items": [{"name": n, "done": True} for n in ("助浴", "擦身", "更衣", "指甲修剪")]})
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (1600, 1200), (200, 100, 50)).save(buffer, "JPEG")
        api.ok("POST", f"/orders/{oid}/photos", "cg_mid", files={"file": ("p.jpg", buffer.getvalue())})
        clock.freeze(TOMORROW_14 + timedelta(minutes=63))
        result = api.ok("POST", f"/orders/{oid}/check-out", "cg_mid", json={"summary": "助浴顺利", "metrics": [{"metric": "SYSTOLIC", "value": 152}, {"metric": "DIASTOLIC", "value": 88}]})
        assert result["duration_minutes"] == 65 and not result["duration_abnormal"]
        # 6. 家属评价（低分）→ 回访 → 转投诉 → 机构说明 → 结论退费
        api.ok("POST", f"/orders/{oid}/review", "family_a1", json={"punctuality": 3, "attitude": 3, "professionalism": 3, "effectiveness": 3, "comment": "一般"})
        task = api.ok("GET", "/followups?status=OPEN", "comm_a")[0]
        api.ok("POST", f"/followups/{task['id']}/handle", "comm_a", json={"outcome": "TO_COMPLAINT", "note": "家属要求投诉"})
        complaint = api.ok("GET", "/complaints", "comm_a")[0]
        api.ok("POST", f"/complaints/{complaint['id']}/assign", "comm_a")
        api.ok("POST", f"/complaints/{complaint['id']}/explain", "org_x", json={"explanation": "已核实"})
        api.ok("POST", f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "REJECTED", "basis": "服务记录完整，投诉不成立"})
        # 7. 服务后血压异常触发红色预警，30 分钟未处置升级，闭环
        red = api.ok("POST", f"/elders/{world.elder_a1}/health", "grid_a1", json={"points": [{"metric": "SYSTOLIC", "value": 186}]})["alerts"][0]
        clock.freeze(clock.now() + timedelta(minutes=30))
        assert run_job(JOB_INDEX["alert_escalation"]).processed == 1
        api.ok("POST", f"/alerts/{red}/respond", "grid_a1", json={"note": "电话确认，正在赶往"})
        closed = api.ok("POST", f"/alerts/{red}/close", "grid_a1", json={"conclusion": "REFERRED", "hospital": "青松社区卫生服务中心", "referred_at": clock.now().isoformat()})
        assert closed["conclusion"] == "REFERRED"
        # 8. 月初自动生成账单 → 网格员 → 机构 → 监管抽查 → 生效 → 缴费回写
        clock.freeze(datetime(2026, 7, 1, 0, 30))
        run_job(JOB_INDEX["monthly_billing"])
        bill = api.ok("GET", "/bills?month=2026-06", "comm_a")[0]
        assert bill["total_amount"] == 8000 and bill["status"] == "DRAFT"
        api.ok("POST", f"/bills/{bill['id']}/grid-confirm", "grid_a1")
        api.ok("POST", f"/bills/{bill['id']}/org-confirm", "org_x")
        detail = api.ok("GET", f"/bills/{bill['id']}", "street")
        assert detail["status"] == "ORG_CONFIRMED" and detail["samples"]
        final = api.ok("POST", f"/samples/{detail['samples'][0]['id']}/check", "street", json={"passed": True})
        assert final["status"] == "EFFECTIVE"
        statement = api.ok("GET", "/statements", "family_a1")[0]
        assert statement["self_amount"] == 3200
        api.ok("POST", f"/statements/{statement['id']}/pay", "comm_a", json={"method": "微信"})
        assert api.ok("GET", f"/orders/{oid}", "family_a1")["payment_status"] == "PAID"
        # 9. 全程审计链完好
        assert api.ok("GET", "/audit/verify", "street")["ok"]


class TestSeedAndMigrations:
    def test_verify_chain_service(self, db):
        assert verify_chain(db)["ok"]

    def test_all_models_have_migration(self, db):
        tables = {r[0] for r in db.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()}
        from app.db.base import Base
        assert set(Base.metadata.tables) <= tables

