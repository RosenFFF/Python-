"""档案、敏感字段、服务目录与价格版本。"""
from __future__ import annotations

import io
from datetime import timedelta

from openpyxl import Workbook
from sqlalchemy import text

from app.core import clock
from tests.factory import valid_id_card
from tests.helpers import TOMORROW_14, create_order


def _payload(world, seq=100, **extra):
    return {
        "name": "新老人", "gender": "男", "birth_date": "1950-01-02", "id_card": valid_id_card(seq, "19500102"),
        "phone": "13911112222", "address": "青松街道和平巷8号2栋301室", "grid_id": world.grid_a1,
        "disability_level": "MILD", "care_tags": ["独居"], **extra,
    }


class TestElderCrud:
    def test_create_returns_masked_and_generates_code(self, api, world):
        body = api.ok("POST", "/elders", "grid_a1", json=_payload(world))
        assert body["code"].startswith("E2026")
        assert "*" in body["id_card"] and body["phone"] == "139****2222"
        assert "301" not in body["address"]

    def test_sensitive_fields_are_ciphertext_in_database(self, api, world, db):
        body = api.ok("POST", "/elders", "grid_a1", json=_payload(world))
        row = db.execute(text("SELECT id_card, phone, address, emergency_phone FROM elders WHERE id=:i"), {"i": body["id"]}).one()
        assert all(value is None or value.startswith("v1:") for value in row)

    def test_duplicate_id_card_rejected(self, api, world):
        api.ok("POST", "/elders", "grid_a1", json=_payload(world))
        response = api.post("/elders", "grid_a1", json=_payload(world))
        assert response.status_code == 409

    def test_invalid_id_card_rejected(self, api, world):
        response = api.post("/elders", "grid_a1", json=_payload(world, id_card="510104195001020000"))
        assert response.status_code == 422

    def test_invalid_phone_rejected(self, api, world):
        assert api.post("/elders", "grid_a1", json=_payload(world, phone="12345")).status_code == 422

    def test_future_birth_date_rejected(self, api, world):
        assert api.post("/elders", "grid_a1", json=_payload(world, birth_date="2030-01-01")).status_code == 422

    def test_unknown_field_rejected(self, api, world):
        assert api.post("/elders", "grid_a1", json=_payload(world, is_deleted=True)).status_code == 422

    def test_grid_worker_cannot_create_in_other_grid(self, api, world):
        assert api.post("/elders", "grid_a1", json=_payload(world, grid_id=world.grid_a2)).status_code == 403

    def test_community_admin_can_create_across_grids_in_community(self, api, world):
        assert api.post("/elders", "comm_a", json=_payload(world, grid_id=world.grid_a2)).status_code == 201
        assert api.post("/elders", "comm_a", json=_payload(world, seq=101, grid_id=world.grid_b1)).status_code == 403

    def test_change_log_records_before_after_with_mask(self, api, world):
        api.ok("PATCH", f"/elders/{world.elder_a1}", "grid_a1", json={"disability_level": "SEVERE", "phone": "13999998888"})
        logs = api.ok("GET", f"/elders/{world.elder_a1}/change-logs", "grid_a1")
        fields = {log["field"]: log for log in logs}
        assert fields["disability_level"]["before"] == "MODERATE"
        assert fields["disability_level"]["after"] == "SEVERE"
        assert fields["disability_level"]["changed_by"] == "grid_a1"
        assert fields["phone"]["after"] == "139****8888"

    def test_no_op_update_writes_no_log(self, api, world):
        api.ok("PATCH", f"/elders/{world.elder_a1}", "grid_a1", json={"disability_level": "MODERATE"})
        assert api.ok("GET", f"/elders/{world.elder_a1}/change-logs", "grid_a1") == []

    def test_logical_delete_hides_but_keeps_row(self, api, world, db):
        api.ok("DELETE", f"/elders/{world.elder_a2}", "comm_a", json={"reason": "搬离本社区"})
        assert api.get(f"/elders/{world.elder_a2}", "comm_a").status_code == 404
        assert db.execute(text("SELECT is_deleted FROM elders WHERE id=:i"), {"i": world.elder_a2}).scalar() == 1

    def test_delete_requires_reason_and_permission(self, api, world):
        assert api.call("DELETE", f"/elders/{world.elder_a1}", "comm_a", json={"reason": ""}).status_code == 422
        assert api.call("DELETE", f"/elders/{world.elder_a1}", "grid_a1", json={"reason": "x"}).status_code == 403

    def test_deleted_elder_can_be_restored(self, api, world):
        api.ok("DELETE", f"/elders/{world.elder_a2}", "comm_a", json={"reason": "误操作"})
        api.ok("POST", f"/elders/{world.elder_a2}/restore", "comm_a")
        assert api.get(f"/elders/{world.elder_a2}", "comm_a").status_code == 200

    def test_id_number_reusable_after_logical_delete(self, api, world):
        created = api.ok("POST", "/elders", "comm_a", json=_payload(world))
        api.ok("DELETE", f"/elders/{created['id']}", "comm_a", json={"reason": "重复建档"})
        assert api.post("/elders", "comm_a", json=_payload(world)).status_code == 201


class TestElderFilters:
    def test_filter_by_level_tag_grid(self, api, world):
        assert {e["id"] for e in api.ok("GET", "/elders?level=MODERATE", "comm_a")["items"]} == {world.elder_a1}
        assert {e["id"] for e in api.ok("GET", "/elders?tag=独居", "comm_a")["items"]} == {world.elder_a1}
        assert {e["id"] for e in api.ok("GET", f"/elders?grid_id={world.grid_a2}", "comm_a")["items"]} == {world.elder_a2}

    def test_search_phone_uses_blind_index(self, api, world):
        items = api.ok("GET", "/elders?keyword=13800000001", "comm_a")["items"]
        assert [e["id"] for e in items] == [world.elder_a1]

    def test_search_by_full_id_card(self, api, world):
        items = api.ok("GET", f"/elders?keyword={valid_id_card(2)}", "comm_a")["items"]
        assert [e["id"] for e in items] == [world.elder_a2]

    def test_pagination(self, api, world):
        body = api.ok("GET", "/elders?page=1&size=1", "comm_a")
        assert body["total"] == 3 and len(body["items"]) == 1


class TestReveal:
    def _body(self, **extra):
        return {"password": "Test@2026", "reason": "联系紧急联系人", "fields": ["phone", "id_card"], **extra}

    def test_reveal_with_password_returns_plain_and_logs(self, api, world):
        result = api.ok("POST", f"/elders/{world.elder_a1}/reveal", "grid_a1", json=self._body())
        assert result["phone"] == "13800000001"
        logs = api.ok("GET", f"/elders/{world.elder_a1}/access-logs", "grid_a1")
        assert logs[0]["user_name"] == "grid_a1" and logs[0]["reason"] == "联系紧急联系人"
        audit = api.ok("GET", "/audit/logs?action=REVEAL_SENSITIVE", "street")
        assert audit["total"] == 1

    def test_wrong_password_denied_and_audited(self, api, world):
        response = api.post(f"/elders/{world.elder_a1}/reveal", "grid_a1", json=self._body(password="wrong"))
        assert response.status_code == 403 and response.json()["code"] == "REAUTH_FAILED"
        assert api.ok("GET", "/audit/logs?action=REVEAL_DENIED", "street")["total"] == 1

    def test_reason_required(self, api, world):
        assert api.post(f"/elders/{world.elder_a1}/reveal", "grid_a1", json=self._body(reason=" ")).status_code == 422

    def test_only_sensitive_fields_allowed(self, api, world):
        assert api.post(f"/elders/{world.elder_a1}/reveal", "grid_a1", json=self._body(fields=["password_hash"])).status_code == 422

    def test_reveal_respects_data_scope(self, api, world):
        assert api.post(f"/elders/{world.elder_a2}/reveal", "grid_a1", json=self._body()).status_code == 404

    def test_role_without_permission_cannot_reveal(self, api, world):
        for user in ("family_a1", "org_x", "street", "admin", "cg_near"):
            assert api.post(f"/elders/{world.elder_a1}/reveal", user, json=self._body()).status_code in (403, 404), user


class TestImport:
    def _xlsx(self, rows):
        book = Workbook()
        sheet = book.active
        sheet.append(["姓名", "性别", "出生日期", "身份证号", "联系电话", "居住地址", "网格", "失能等级", "照护标签", "慢性病"])
        for row in rows:
            sheet.append(row)
        output = io.BytesIO()
        book.save(output)
        return output.getvalue()

    def _good(self, seq):
        return ["导入老人", "女", "1950-01-02", valid_id_card(seq, "19500102"), "13911112222", "和平巷1号", "A1", "轻度失能", "独居、空巢", "高血压,糖尿病"]

    def test_partial_success_reports_row_errors(self, api):
        bad_card = ["坏数据", "女", "1950-01-02", "123", "13911112222", "和平巷1号", "A1", "轻度失能", "", ""]
        bad_grid = ["坏网格", "女", "1950-01-02", valid_id_card(202, "19500102"), "13911112222", "和平巷1号", "不存在", "", "", ""]
        result = api.ok("POST", "/elders/import", "comm_a", files={"file": ("a.xlsx", self._xlsx([self._good(201), bad_card, bad_grid]))})
        assert result["created"] == 1 and result["failed"] == 2
        assert {e["row"] for e in result["errors"]} == {3, 4}

    def test_dry_run_writes_nothing(self, api):
        result = api.ok("POST", "/elders/import?dry_run=true", "comm_a", files={"file": ("a.xlsx", self._xlsx([self._good(203)]))})
        assert result["created"] == 1 and result["dry_run"]
        assert api.ok("GET", "/elders?keyword=导入老人", "comm_a")["total"] == 0

    def test_csv_import(self, api):
        content = "姓名,性别,出生日期,身份证号,居住地址,网格\n" + f"CSV老人,男,1950/01/02,{valid_id_card(204, '19500102')},和平巷2号,A1\n"
        result = api.ok("POST", "/elders/import", "comm_a", files={"file": ("a.csv", content.encode("utf-8-sig"))})
        assert result["created"] == 1

    def test_import_cannot_write_outside_scope(self, api):
        row = ["越权", "女", "1950-01-02", valid_id_card(205, "19500102"), "13911112222", "和平巷1号", "B1", "", "", ""]
        result = api.ok("POST", "/elders/import", "comm_a", files={"file": ("a.xlsx", self._xlsx([row]))})
        assert result["created"] == 0 and result["failed"] == 1

    def test_unsupported_file_type(self, api):
        assert api.post("/elders/import", "comm_a", json=None, files={"file": ("a.txt", b"x")}).status_code in (422,)


class TestPriceVersions:
    def test_adjust_creates_new_version_and_closes_old(self, api, world):
        future = (clock.now() + timedelta(days=1)).isoformat()
        api.ok("POST", f"/catalog/items/{world.item_bath}/price", "org_x", json={"unit_price": 10000, "effective_from": future, "reason": "成本上涨"})
        items = api.ok("GET", "/catalog/items", "org_x")
        versions = next(i for i in items if i["id"] == world.item_bath)["versions"]
        assert [v["unit_price"] for v in versions] == [8000, 10000]
        assert versions[0]["effective_to"] == versions[1]["effective_from"]
        assert versions[0]["unit_price"] == 8000  # 旧版本未被覆盖

    def test_cannot_backdate_price(self, api, world):
        past = (clock.now() - timedelta(days=1)).isoformat()
        response = api.post(f"/catalog/items/{world.item_bath}/price", "org_x", json={"unit_price": 10000, "effective_from": past, "reason": "x"})
        assert response.status_code == 422

    def test_reason_required(self, api, world):
        assert api.post(f"/catalog/items/{world.item_bath}/price", "org_x", json={"unit_price": 10000, "reason": ""}).status_code == 422

    def test_order_binds_version_by_service_time(self, api, world):
        change_at = TOMORROW_14 + timedelta(days=5)
        api.ok("POST", f"/catalog/items/{world.item_bath}/price", "org_x", json={"unit_price": 10000, "effective_from": change_at.isoformat(), "reason": "调价"})
        before = create_order(api, world, start=change_at - timedelta(days=2))
        after = create_order(api, world, start=change_at + timedelta(days=2))
        assert before["unit_price"] == 8000
        assert after["unit_price"] == 10000

    def test_existing_order_keeps_price_after_adjustment(self, api, world):
        order = create_order(api, world)
        clock.freeze(clock.now() + timedelta(hours=1))
        api.ok("POST", f"/catalog/items/{world.item_bath}/price", "org_x", json={"unit_price": 12000, "reason": "调价"})
        assert api.ok("GET", f"/orders/{order['id']}", "grid_a1")["unit_price"] == 8000

    def test_off_shelf_item_cannot_be_booked(self, api, world):
        api.ok("POST", f"/catalog/items/{world.item_bath}/shelf", "org_x", json={"on_shelf": False})
        response = api.post("/orders", "family_a1", json={"elder_id": world.elder_a1, "item_id": world.item_bath, "scheduled_start": TOMORROW_14.isoformat()})
        assert response.status_code == 422
        names = [i["id"] for i in api.ok("GET", "/catalog/items?on_shelf_only=true", "family_a1")]
        assert world.item_bath not in names

    def test_family_cannot_edit_catalog(self, api, world):
        assert api.post(f"/catalog/items/{world.item_bath}/shelf", "family_a1", json={"on_shelf": False}).status_code == 403

    def test_create_item_with_required_fields(self, api):
        body = {"code": "NEW1", "name": "理发", "pricing_mode": "PER_TIME", "standard_minutes": 30, "required_skills": [],
                "applicable_levels": ["INTACT"], "subsidy_eligible": True, "checklist": ["理发"], "unit_price": 2500, "subsidy_ratio": 0.2}
        item = api.ok("POST", "/catalog/items", "org_x", json=body)
        assert item["current_version"] == 1 and item["unit_price"] == 2500
        assert api.post("/catalog/items", "org_x", json=body).status_code == 409

    def test_invalid_level_rejected(self, api):
        body = {"code": "N2", "name": "x", "pricing_mode": "PER_TIME", "standard_minutes": 30, "applicable_levels": ["BAD"], "unit_price": 100}
        assert api.post("/catalog/items", "org_x", json=body).status_code == 422

