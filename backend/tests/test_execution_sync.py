"""上门执行留痕、异常处置、离线同步。"""
from __future__ import annotations

import io
from datetime import timedelta

from PIL import Image

from app.core import clock
from app.core.config import get_settings
from app.jobs.tasks import JOB_INDEX, run_job
from app.models import ServicePhoto
from tests.factory import KM, LAT, LNG
from tests.helpers import TOMORROW_14, completed_order, dispatched_order, in_service_order, offered_order


def _image(width=2400, height=1800, fmt="JPEG", with_exif=False) -> bytes:
    image = Image.new("RGB", (width, height), (120, 160, 200))
    output = io.BytesIO()
    kwargs = {}
    if with_exif:
        exif = Image.Exif()
        exif[0x010F] = "SecretCamera"  # Make
        kwargs["exif"] = exif
    image.save(output, fmt, **kwargs)
    return output.getvalue()


class TestCheckIn:
    def test_within_200m_ok(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14)
        body = api.ok("POST", f"/orders/{order['id']}/check-in", "cg_near", json={"lat": LAT + 0.001, "lng": LNG})
        assert body["distance_m"] < 200
        assert api.ok("GET", f"/orders/{order['id']}", "family_a1")["status"] == "IN_SERVICE"

    def test_over_200m_requires_note(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14)
        far = {"lat": LAT + 0.5 * KM, "lng": LNG}
        response = api.post(f"/orders/{order['id']}/check-in", "cg_near", json=far)
        assert response.status_code == 422 and response.json()["code"] == "CHECKIN_TOO_FAR"
        assert response.json()["details"]["distance_m"] > 200
        ok = api.post(f"/orders/{order['id']}/check-in", "cg_near", json={**far, "note": "小区门禁，定位偏差"})
        assert ok.status_code == 200

    def test_threshold_is_configurable(self, api, world):
        api.ok("PUT", "/admin/configs/exec.checkin_distance_m", "admin", json={"value": 1000})
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14)
        assert api.post(f"/orders/{order['id']}/check-in", "cg_near", json={"lat": LAT + 0.5 * KM, "lng": LNG}).status_code == 200

    def test_only_assigned_caregiver(self, api, world):
        order = dispatched_order(api, world)
        assert api.post(f"/orders/{order['id']}/check-in", "cg_mid", json={"lat": LAT, "lng": LNG}).status_code == 404

    def test_cannot_check_in_before_dispatch(self, api, world):
        order, _ = offered_order(api, world, caregiver=world.c_near)
        assert api.post(f"/orders/{order['id']}/check-in", "cg_near", json={"lat": LAT, "lng": LNG}).status_code == 403

    def test_on_time_flag(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14 + timedelta(minutes=6))
        api.ok("POST", f"/orders/{order['id']}/check-in", "cg_near", json={"lat": LAT, "lng": LNG})
        assert api.ok("GET", f"/orders/{order['id']}", "cg_near")["execution"]["is_on_time"] is False

    def test_check_in_updates_caregiver_location(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14)
        api.ok("POST", f"/orders/{order['id']}/check-in", "cg_near", json={"lat": LAT, "lng": LNG})
        me = next(c for c in api.ok("GET", "/caregivers", "org_x") if c["id"] == world.c_near)
        assert me["base_lat"] == LAT

    def test_cannot_check_in_twice(self, api, world):
        order = in_service_order(api, world)
        assert api.post(f"/orders/{order['id']}/check-in", "cg_near", json={"lat": LAT, "lng": LNG}).status_code == 409

    def test_caregiver_sees_full_address_family_masked(self, api, world):
        order = dispatched_order(api, world)
        assert "502" in api.ok("GET", f"/orders/{order['id']}", "cg_near")["address"]
        assert "502" not in api.ok("GET", f"/orders/{order['id']}", "grid_a1")["address"]


class TestChecklist:
    def test_update_and_unknown_item(self, api, world):
        order = in_service_order(api, world)
        body = api.ok("PUT", f"/orders/{order['id']}/checklist", "cg_near", json={"items": [{"name": "助浴", "done": True}]})
        assert next(i for i in body["checklist"] if i["name"] == "助浴")["done"] is True
        assert api.call("PUT", f"/orders/{order['id']}/checklist", "cg_near", json={"items": [{"name": "不存在", "done": True}]}).status_code == 422

    def test_checkout_blocked_when_incomplete_without_reason(self, api, world):
        order = in_service_order(api, world)
        response = api.post(f"/orders/{order['id']}/check-out", "cg_near", json={"summary": "完成"})
        assert response.status_code == 422 and response.json()["code"] == "CHECKLIST_INCOMPLETE"
        ok = api.post(f"/orders/{order['id']}/check-out", "cg_near", json={"summary": "完成", "incomplete_reason": "老人拒绝修剪指甲"})
        assert ok.status_code == 200


class TestPhotos:
    def test_compressed_and_exif_stripped(self, api, world, db):
        order = in_service_order(api, world)
        original = _image(with_exif=True)
        body = api.ok("POST", f"/orders/{order['id']}/photos", "cg_near", files={"file": ("a.jpg", original)})
        assert max(body["width"], body["height"]) == 1280
        photo = db.get(ServicePhoto, body["id"])
        saved = get_settings().upload_dir / photo.file_name
        assert saved.stat().st_size == body["size_bytes"]
        with Image.open(saved) as image:
            assert not dict(image.getexif())

    def test_fourth_photo_rejected(self, api, world):
        order = in_service_order(api, world)
        for _ in range(3):
            api.ok("POST", f"/orders/{order['id']}/photos", "cg_near", files={"file": ("a.jpg", _image(200, 200))})
        response = api.post(f"/orders/{order['id']}/photos", "cg_near", json=None, files={"file": ("a.jpg", _image(200, 200))})
        assert response.status_code == 409

    def test_non_image_rejected(self, api, world):
        order = in_service_order(api, world)
        response = api.post(f"/orders/{order['id']}/photos", "cg_near", json=None, files={"file": ("a.jpg", b"<script>alert(1)</script>")})
        assert response.status_code == 422

    def test_oversized_rejected(self, api, world):
        order = in_service_order(api, world)
        too_big = b"\xff" * (get_settings().max_upload_mb * 1024 * 1024 + 2)
        assert api.post(f"/orders/{order['id']}/photos", "cg_near", json=None, files={"file": ("a.jpg", too_big)}).status_code == 422

    def test_photo_access_respects_scope(self, api, world):
        order = in_service_order(api, world)
        photo = api.ok("POST", f"/orders/{order['id']}/photos", "cg_near", files={"file": ("a.jpg", _image(200, 200))})
        assert api.get(f"/photos/{photo['id']}", "family_a1").status_code == 200
        assert api.get(f"/photos/{photo['id']}", "family_b1").status_code == 404
        assert api.get(f"/photos/{photo['id']}", "grid_a2").status_code == 404


class TestCheckOut:
    def test_duration_computed_and_order_completed(self, api, world):
        order = completed_order(api, world, minutes=62)
        detail = api.ok("GET", f"/orders/{order['id']}", "family_a1")
        assert detail["status"] == "COMPLETED"
        assert detail["execution"]["duration_minutes"] == 62
        assert detail["execution"]["duration_abnormal"] is False

    def test_summary_required(self, api, world):
        order = in_service_order(api, world)
        assert api.post(f"/orders/{order['id']}/check-out", "cg_near", json={"summary": " "}).status_code == 422

    def test_deviation_over_30_minutes_flagged_for_spot_check(self, api, world):
        order = completed_order(api, world, minutes=91)
        detail = api.ok("GET", f"/orders/{order['id']}", "grid_a1")
        assert detail["execution"]["duration_abnormal"] is True
        assert detail["execution"]["spot_check_status"] == "PENDING"
        assert api.ok("GET", "/spot-checks", "grid_a1")["total"] == 1

    def test_exactly_30_minutes_not_flagged(self, api, world):
        order = completed_order(api, world, minutes=90)
        assert api.ok("GET", f"/orders/{order['id']}", "grid_a1")["execution"]["duration_abnormal"] is False

    def test_too_short_also_flagged(self, api, world):
        order = completed_order(api, world, minutes=29)
        assert api.ok("GET", f"/orders/{order['id']}", "grid_a1")["execution"]["duration_abnormal"] is True

    def test_spot_check_pass_and_fail_requires_note(self, api, world):
        order = completed_order(api, world, minutes=120)
        assert api.post(f"/orders/{order['id']}/spot-check", "grid_a1", json={"passed": False}).status_code == 422
        api.ok("POST", f"/orders/{order['id']}/spot-check", "grid_a1", json={"passed": True, "note": "电话核实无误"})
        assert api.ok("GET", f"/orders/{order['id']}", "grid_a1")["execution"]["spot_check_status"] == "PASS"
        assert api.post(f"/orders/{order['id']}/spot-check", "grid_a1", json={"passed": True}).status_code == 409

    def test_metrics_written_and_trigger_alert(self, api, world):
        order = completed_order(api, world, metrics=[{"metric": "SYSTOLIC", "value": 152}, {"metric": "DIASTOLIC", "value": 88}])
        series = api.ok("GET", f"/elders/{world.elder_a1}/health?days=1", "grid_a1")
        assert series["summary"]["SYSTOLIC"]["latest"] == 152
        assert order["id"]

    def test_red_metric_on_checkout_raises_alert(self, api, world):
        completed_order(api, world, metrics=[{"metric": "SYSTOLIC", "value": 186}])
        alerts = api.ok("GET", "/alerts?only_open=true", "grid_a1")["items"]
        assert alerts[0]["level"] == "RED"

    def test_slots_released_after_completion(self, api, world):
        completed_order(api, world)
        order, _ = offered_order(api, world, elder=world.elder_a2, grid="grid_a2", user="grid_a2", caregiver=world.c_near,
                                 start=TOMORROW_14 + timedelta(hours=3))
        assert order["id"]

    def test_timeline_covers_whole_flow(self, api, world):
        order = completed_order(api, world)
        actions = [e["action"] for e in api.ok("GET", f"/orders/{order['id']}", "grid_a1")["timeline"]]
        for expected in ("CREATE", "ACCEPT", "QUEUE_DISPATCH", "OFFER", "OFFER_ACCEPT", "CHECK_IN", "CHECK_OUT"):
            assert expected in actions


class TestReminders:
    def test_reminder_sent_once_15_minutes_before(self, api, world):
        dispatched_order(api, world)
        clock.freeze(TOMORROW_14 - timedelta(minutes=16))
        assert run_job(JOB_INDEX["visit_reminder"]).processed == 0
        clock.freeze(TOMORROW_14 - timedelta(minutes=15))
        assert run_job(JOB_INDEX["visit_reminder"]).processed == 1
        assert run_job(JOB_INDEX["visit_reminder"]).processed == 0
        assert "上门提醒" in [n["title"] for n in api.ok("GET", "/notifications", "cg_near")["items"]]

    def test_missed_checkin_flagged_once(self, api, world):
        dispatched_order(api, world)
        clock.freeze(TOMORROW_14 + timedelta(minutes=61))
        assert run_job(JOB_INDEX["missed_checkin"]).processed == 1
        assert run_job(JOB_INDEX["missed_checkin"]).processed == 0


class TestExceptions:
    def test_not_home_goes_to_queue_with_handle_time(self, api, world):
        order = dispatched_order(api, world)
        created = api.ok("POST", f"/orders/{order['id']}/exceptions", "cg_near", json={"exception_type": "NOT_HOME", "description": "敲门无人"})
        queue = api.ok("GET", "/exceptions?status=OPEN", "grid_a1")
        assert queue["items"][0]["id"] == created["id"]
        clock.freeze(clock.now() + timedelta(minutes=25))
        api.ok("POST", f"/exceptions/{created['id']}/pick", "grid_a1")
        closed = api.ok("POST", f"/exceptions/{created['id']}/close", "grid_a1", json={"resolution": "联系家属确认外出", "order_action": "NO_SHOW"})
        assert closed["handle_minutes"] == 25 and closed["status"] == "CLOSED"
        detail = api.ok("GET", f"/orders/{order['id']}", "grid_a1")
        assert detail["status"] == "NO_SHOW" and detail["cancel_chargeable"]

    def test_unwell_triggers_red_alert_immediately(self, api, world):
        order = in_service_order(api, world)
        row = api.ok("POST", f"/orders/{order['id']}/exceptions", "cg_near", json={"exception_type": "UNWELL", "description": "老人精神萎靡，无法唤醒"})
        assert row["alert_id"]
        alert = api.ok("GET", f"/alerts/{row['alert_id']}", "grid_a1")
        assert alert["level"] == "RED" and "无法唤醒" in alert["message"]

    def test_continue_keeps_order_in_service(self, api, world):
        order = in_service_order(api, world)
        row = api.ok("POST", f"/orders/{order['id']}/exceptions", "cg_near", json={"exception_type": "REFUSED", "description": "起初拒绝"})
        api.ok("POST", f"/exceptions/{row['id']}/close", "grid_a1", json={"resolution": "沟通后同意服务", "order_action": "CONTINUE"})
        assert api.ok("GET", f"/orders/{order['id']}", "grid_a1")["status"] == "IN_SERVICE"

    def test_cancel_from_exception_not_charged(self, api, world):
        order = in_service_order(api, world)
        row = api.ok("POST", f"/orders/{order['id']}/exceptions", "cg_near", json={"exception_type": "UNWELL", "description": "不适"})
        api.ok("POST", f"/exceptions/{row['id']}/close", "grid_a1", json={"resolution": "送医，取消服务", "order_action": "CANCEL"})
        detail = api.ok("GET", f"/orders/{order['id']}", "grid_a1")
        assert detail["status"] == "CANCELLED" and not detail["cancel_chargeable"]

    def test_other_type_requires_description(self, api, world):
        order = dispatched_order(api, world)
        assert api.post(f"/orders/{order['id']}/exceptions", "cg_near", json={"exception_type": "OTHER"}).status_code == 422

    def test_closed_exception_cannot_be_closed_again(self, api, world):
        order = dispatched_order(api, world)
        row = api.ok("POST", f"/orders/{order['id']}/exceptions", "cg_near", json={"exception_type": "NOT_HOME"})
        body = {"resolution": "x", "order_action": "CONTINUE"}
        api.ok("POST", f"/exceptions/{row['id']}/close", "grid_a1", json=body)
        assert api.post(f"/exceptions/{row['id']}/close", "grid_a1", json=body).status_code == 409

    def test_other_grid_cannot_handle(self, api, world):
        order = dispatched_order(api, world)
        row = api.ok("POST", f"/orders/{order['id']}/exceptions", "cg_near", json={"exception_type": "NOT_HOME"})
        assert api.post(f"/exceptions/{row['id']}/pick", "grid_a2").status_code == 404


def _op(op_id, kind, order_id, at, **payload):
    return {"op_id": op_id, "kind": kind, "order_id": order_id, "occurred_at": at.isoformat(), "payload": payload}


class TestOfflineSync:
    def test_bootstrap_contains_only_my_orders(self, api, world):
        mine = dispatched_order(api, world)
        dispatched_order(api, world, elder=world.elder_a2, grid="grid_a2", user="grid_a2", caregiver=world.c_mid, cg_user="cg_mid")
        body = api.ok("GET", "/sync/bootstrap", "cg_near")
        assert [o["id"] for o in body["orders"]] == [mine["id"]]
        assert "502" in body["orders"][0]["address"]
        assert body["orders"][0]["checklist"][0]["name"] == "助浴"

    def test_full_offline_flow_uses_actual_times(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14 + timedelta(hours=3))
        checkin_at, checkout_at = TOMORROW_14, TOMORROW_14 + timedelta(minutes=58)
        items = [{"name": n, "done": True} for n in ("助浴", "擦身", "更衣", "指甲修剪")]
        ops = [
            _op("op-checkout-1", "CHECKOUT", order["id"], checkout_at, summary="离线完成", metrics=[{"metric": "SYSTOLIC", "value": 150}]),
            _op("op-checkin-01", "CHECKIN", order["id"], checkin_at, lat=LAT, lng=LNG),
            _op("op-checklist1", "CHECKLIST", order["id"], checkin_at + timedelta(minutes=30), items=items),
        ]
        result = api.ok("POST", "/sync/batch", "cg_near", json={"ops": ops})
        assert result["applied"] == 3, result
        detail = api.ok("GET", f"/orders/{order['id']}", "family_a1")
        assert detail["status"] == "COMPLETED"
        assert detail["execution"]["duration_minutes"] == 58
        assert detail["execution"]["offline_synced"] is True

    def test_same_second_ops_keep_client_order(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14 + timedelta(hours=1))
        items = [{"name": n, "done": True} for n in ("助浴", "擦身", "更衣", "指甲修剪")]
        same_second = TOMORROW_14
        # op_id 的字典序和真实顺序相反，排序只能依靠 seq
        ops = [
            {**_op("zzz-checkin-1", "CHECKIN", order["id"], same_second, lat=LAT, lng=LNG), "seq": 1},
            {**_op("mmm-checklist", "CHECKLIST", order["id"], same_second, items=items), "seq": 2},
            {**_op("aaa-checkout1", "CHECKOUT", order["id"], same_second, summary="完成"), "seq": 3},
        ]
        result = api.ok("POST", "/sync/batch", "cg_near", json={"ops": list(reversed(ops))})
        assert result["applied"] == 3, result

    def test_retry_is_idempotent(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14)
        op = _op("op-retry-0001", "CHECKIN", order["id"], TOMORROW_14, lat=LAT, lng=LNG)
        api.ok("POST", "/sync/batch", "cg_near", json={"ops": [op]})
        second = api.ok("POST", "/sync/batch", "cg_near", json={"ops": [op]})
        assert second["duplicate"] == 1 and second["applied"] == 0
        assert second["results"][0]["result"]["distance_m"] == 0

    def test_reassigned_order_rejected_others_continue(self, api, world):
        taken = dispatched_order(api, world)
        keep = dispatched_order(api, world, elder=world.elder_a2, grid="grid_a2", user="grid_a2", start=TOMORROW_14 + timedelta(hours=4))
        api.ok("POST", f"/orders/{taken['id']}/reassign", "grid_a1", json={"caregiver_id": world.c_mid, "reason": "请假"})
        clock.freeze(TOMORROW_14 + timedelta(hours=4))
        result = api.ok("POST", "/sync/batch", "cg_near", json={"ops": [
            _op("op-taken-0001", "CHECKIN", taken["id"], TOMORROW_14, lat=LAT, lng=LNG),
            _op("op-keep-00001", "CHECKIN", keep["id"], TOMORROW_14 + timedelta(hours=4), lat=LAT, lng=LNG),
        ]})
        assert result["rejected"] == 1 and result["applied"] == 1
        rejected = next(r for r in result["results"] if r["status"] == "rejected")
        assert rejected["op_id"] == "op-taken-0001"

    def test_future_time_rejected(self, api, world):
        order = dispatched_order(api, world)
        result = api.ok("POST", "/sync/batch", "cg_near", json={"ops": [
            _op("op-future-001", "CHECKIN", order["id"], clock.now() + timedelta(hours=1), lat=LAT, lng=LNG)]})
        assert result["rejected"] == 1 and "校准" in result["results"][0]["message"]

    def test_too_old_operation_rejected(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14 + timedelta(hours=73))
        result = api.ok("POST", "/sync/batch", "cg_near", json={"ops": [_op("op-old-000001", "CHECKIN", order["id"], TOMORROW_14, lat=LAT, lng=LNG)]})
        assert result["rejected"] == 1

    def test_failed_op_rolled_back_individually(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14)
        result = api.ok("POST", "/sync/batch", "cg_near", json={"ops": [
            _op("op-bad-000001", "CHECKIN", order["id"], TOMORROW_14, lat=LAT + 1, lng=LNG),
        ]})
        assert result["results"][0]["code"] == "CHECKIN_TOO_FAR"
        assert api.ok("GET", f"/orders/{order['id']}", "cg_near")["status"] == "DISPATCHED"

    def test_non_caregiver_forbidden(self, api):
        assert api.post("/sync/batch", "grid_a1", json={"ops": []}).status_code == 403

    def test_op_id_collision_between_users_rejected(self, api, world):
        order = dispatched_order(api, world)
        other = dispatched_order(api, world, elder=world.elder_a2, grid="grid_a2", user="grid_a2", caregiver=world.c_mid, cg_user="cg_mid")
        clock.freeze(TOMORROW_14)
        api.ok("POST", "/sync/batch", "cg_near", json={"ops": [_op("op-shared-001", "CHECKIN", order["id"], TOMORROW_14, lat=LAT, lng=LNG)]})
        result = api.ok("POST", "/sync/batch", "cg_mid", json={"ops": [_op("op-shared-001", "CHECKIN", other["id"], TOMORROW_14, lat=LAT, lng=LNG)]})
        assert result["rejected"] == 1

