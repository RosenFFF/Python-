"""健康数据接入与分级预警。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app.core import clock
from app.db.session import new_session
from app.jobs.tasks import JOB_INDEX, run_job
from app.models import AlertEvent, Device, Notification
from app.services import notification_service


def point(metric, value, at=None):
    item = {"metric": metric, "value": value}
    if at:
        item["measured_at"] = at.isoformat()
    return item


def record(api, world, *points, user="grid_a1", elder=None):
    return api.ok("POST", f"/elders/{elder or world.elder_a1}/health", user, json={"points": list(points)})


def bind_device(world):
    db = new_session()
    db.add(Device(serial="WB-1", elder_id=world.elder_a1))
    db.commit()
    db.close()


class TestIngestion:
    def test_followup_stored_with_source(self, api, world):
        record(api, world, point("SYSTOLIC", 150))
        series = api.ok("GET", f"/elders/{world.elder_a1}/health", "grid_a1")["series"]["SYSTOLIC"]
        assert series[0]["source"] == "FOLLOWUP"

    def test_implausible_value_rejected_not_alerted(self, api, world):
        result = record(api, world, point("SYSTOLIC", 900))
        assert result["stored"] == 0 and "合理范围" in result["errors"][0]["reason"]
        assert api.ok("GET", "/alerts", "grid_a1")["total"] == 0

    def test_unknown_metric_rejected(self, api, world):
        assert record(api, world, point("BMI", 20))["errors"]

    def test_future_measurement_rejected(self, api, world):
        result = record(api, world, point("SYSTOLIC", 120, clock.now() + timedelta(hours=2)))
        assert result["stored"] == 0

    def test_duplicate_point_idempotent(self, api, world):
        at = clock.now() - timedelta(hours=1)
        record(api, world, point("SYSTOLIC", 120, at))
        assert record(api, world, point("SYSTOLIC", 120, at))["duplicates"] == 1

    def test_wearable_batch_requires_device_key(self, client, world):
        bind_device(world)
        body = {"records": [{"device_serial": "WB-1", "metric": "HEART_RATE", "value": 80, "measured_at": clock.now().isoformat()}]}
        assert client.post("/api/health/wearable/batch", json=body).status_code == 401
        assert client.post("/api/health/wearable/batch", json=body, headers={"X-Device-Key": "wrong"}).status_code == 401
        ok = client.post("/api/health/wearable/batch", json=body, headers={"X-Device-Key": "test-device-key"})
        assert ok.status_code == 200 and ok.json()["stored"] == 1

    def test_unbound_device_rejected(self, client):
        body = {"records": [{"device_serial": "GHOST", "metric": "HEART_RATE", "value": 80, "measured_at": clock.now().isoformat()}]}
        result = client.post("/api/health/wearable/batch", json=body, headers={"X-Device-Key": "test-device-key"}).json()
        assert result["stored"] == 0 and result["errors"][0]["reason"] == "设备未绑定或已停用"

    def test_family_cannot_write_health(self, api, world):
        assert api.post(f"/elders/{world.elder_a1}/health", "family_a1", json={"points": [point("SYSTOLIC", 120)]}).status_code == 403

    def test_series_scope(self, api, world):
        assert api.get(f"/elders/{world.elder_a1}/health", "family_a1").status_code == 200
        assert api.get(f"/elders/{world.elder_a1}/health", "family_b1").status_code == 404


class TestThreeRuleTypes:
    def test_threshold_systolic_180_is_red(self, api, world):
        result = record(api, world, point("SYSTOLIC", 180))
        alert = api.ok("GET", f"/alerts/{result['alerts'][0]}", "grid_a1")
        assert alert["level"] == "RED" and alert["rule_type"] == "THRESHOLD"

    def test_threshold_systolic_90_triggers(self, api, world):
        assert record(api, world, point("SYSTOLIC", 90))["alerts"]

    def test_normal_value_no_alert(self, api, world):
        assert not record(api, world, point("SYSTOLIC", 130))["alerts"]

    def test_three_consecutive_high_heart_rate_is_yellow(self, api, world):
        now = clock.now()
        assert not record(api, world, point("HEART_RATE", 105, now - timedelta(minutes=30)))["alerts"]
        assert not record(api, world, point("HEART_RATE", 110, now - timedelta(minutes=20)))["alerts"]
        result = record(api, world, point("HEART_RATE", 108, now - timedelta(minutes=10)))
        alert = api.ok("GET", f"/alerts/{result['alerts'][0]}", "grid_a1")
        assert alert["level"] == "YELLOW" and alert["rule_type"] == "CONSECUTIVE"
        assert "105、110、108" in alert["message"]

    def test_normal_reading_breaks_the_streak(self, api, world):
        now = clock.now()
        record(api, world, point("HEART_RATE", 105, now - timedelta(minutes=30)))
        record(api, world, point("HEART_RATE", 80, now - timedelta(minutes=20)))
        assert not record(api, world, point("HEART_RATE", 108, now - timedelta(minutes=10)))["alerts"]

    def test_no_data_48h_is_blue(self, api, world):
        record(api, world, point("SYSTOLIC", 120))
        clock.freeze(clock.now() + timedelta(hours=47, minutes=59))
        run_job(JOB_INDEX["alert_no_data"])
        assert api.ok("GET", f"/alerts?elder_id={world.elder_a1}", "grid_a1")["total"] == 0
        clock.freeze(clock.now() + timedelta(minutes=1))
        run_job(JOB_INDEX["alert_no_data"])
        alerts = api.ok("GET", f"/alerts?elder_id={world.elder_a1}", "grid_a1")["items"]
        assert alerts[0]["level"] == "BLUE" and alerts[0]["rule_type"] == "NO_DATA"

    def test_no_data_not_repeated_while_open(self, api, world):
        clock.freeze(clock.now() + timedelta(hours=72))
        first = run_job(JOB_INDEX["alert_no_data"]).processed
        assert first > 0 and run_job(JOB_INDEX["alert_no_data"]).processed == 0

    def test_no_data_cooldown_after_close(self, api, world):
        clock.freeze(clock.now() + timedelta(hours=72))
        run_job(JOB_INDEX["alert_no_data"])
        alert = api.ok("GET", f"/alerts?elder_id={world.elder_a1}", "grid_a1")["items"][0]
        api.ok("POST", f"/alerts/{alert['id']}/close", "grid_a1", json={"conclusion": "HANDLED", "measures": "电话确认手环没电"})
        clock.freeze(clock.now() + timedelta(hours=1))
        run_job(JOB_INDEX["alert_no_data"])
        assert api.ok("GET", f"/alerts?elder_id={world.elder_a1}&only_open=true", "grid_a1")["total"] == 0
        clock.freeze(clock.now() + timedelta(hours=48))
        run_job(JOB_INDEX["alert_no_data"])
        assert api.ok("GET", f"/alerts?elder_id={world.elder_a1}&only_open=true", "grid_a1")["total"] == 1

    def test_rules_are_configurable(self, api, world):
        rules = api.ok("GET", "/alert-rules", "comm_a")
        rule = next(r for r in rules if r["code"] == "HR-CONSEC-YELLOW")
        api.ok("PATCH", f"/alert-rules/{rule['id']}", "comm_a", json={"threshold": 90, "window": 2, "level": "RED"})
        now = clock.now()
        record(api, world, point("HEART_RATE", 95, now - timedelta(minutes=20)))
        result = record(api, world, point("HEART_RATE", 96, now - timedelta(minutes=10)))
        assert api.ok("GET", f"/alerts/{result['alerts'][0]}", "grid_a1")["level"] == "RED"

    def test_disabled_rule_does_not_fire(self, api, world):
        rule = next(r for r in api.ok("GET", "/alert-rules", "comm_a") if r["code"] == "BP-HIGH-RED")
        api.ok("PATCH", f"/alert-rules/{rule['id']}", "comm_a", json={"is_active": False})
        assert not record(api, world, point("SYSTOLIC", 185))["alerts"]

    def test_invalid_rule_rejected(self, api):
        bad = {"code": "X", "name": "x", "rule_type": "CONSECUTIVE", "metric": "HEART_RATE", "op": ">", "threshold": 100, "window": 1, "level": "RED"}
        assert api.post("/alert-rules", "comm_a", json=bad).status_code == 422

    def test_rule_snapshot_preserved_after_edit(self, api, world):
        result = record(api, world, point("SYSTOLIC", 185))
        rule = next(r for r in api.ok("GET", "/alert-rules", "comm_a") if r["code"] == "BP-HIGH-RED")
        api.ok("PATCH", f"/alert-rules/{rule['id']}", "comm_a", json={"threshold": 200})
        detail = api.ok("GET", f"/alerts/{result['alerts'][0]}", "grid_a1")
        assert detail["rule_snapshot"]["threshold"] == 180

    def test_grid_worker_cannot_edit_rules(self, api):
        rule = api.ok("GET", "/alert-rules", "grid_a1")[0]
        assert api.call("PATCH", f"/alert-rules/{rule['id']}", "grid_a1", json={"threshold": 1}).status_code == 403

    def test_baseline_rule_catches_personal_deviation(self, api, world):
        base = clock.now() - timedelta(days=5)
        for index in range(12):
            record(api, world, point("HEART_RATE", 60 + index % 3, base + timedelta(hours=index)))
        result = record(api, world, point("HEART_RATE", 95, clock.now() - timedelta(minutes=5)))
        alert = api.ok("GET", f"/alerts/{result['alerts'][0]}", "grid_a1")
        assert alert["rule_type"] == "BASELINE" and "基线" in alert["message"]


class TestDedupAndPush:
    def test_repeated_trigger_merges(self, api, world):
        now = clock.now()
        first = record(api, world, point("SYSTOLIC", 185, now - timedelta(minutes=9)))
        second = record(api, world, point("SYSTOLIC", 190, now - timedelta(minutes=5)))
        assert first["alerts"] == second["alerts"]
        detail = api.ok("GET", f"/alerts/{first['alerts'][0]}", "grid_a1")
        assert detail["occurrences"] == 2 and detail["message"].find("190") >= 0

    def test_new_alert_after_close(self, api, world):
        now = clock.now()
        first = record(api, world, point("SYSTOLIC", 185, now - timedelta(minutes=9)))["alerts"][0]
        api.ok("POST", f"/alerts/{first}/close", "grid_a1", json={"conclusion": "FALSE_ALARM", "false_reason": "袖带松"})
        assert record(api, world, point("SYSTOLIC", 186, now - timedelta(minutes=5)))["alerts"][0] != first

    def test_red_pushed_to_all_parties_within_5_minutes(self, api, world, db):
        result = record(api, world, point("SYSTOLIC", 190))
        alert_id = result["alerts"][0]
        rows = db.execute(select(Notification).where(Notification.ref_type == "alert", Notification.ref_id == alert_id)).scalars().all()
        roles = {(n.channel, n.recipient_role) for n in rows}
        assert ("IN_APP", "grid_worker") in roles and ("SMS", "grid_worker") in roles
        assert ("SMS", "emergency_contact") in roles and ("SMS", "doctor") in roles
        assert ("IN_APP", "elder_family") in roles
        assert all(n.status == "SENT" for n in rows)
        assert all((n.sent_at - n.queued_at) <= timedelta(minutes=5) for n in rows)
        detail = api.ok("GET", f"/alerts/{alert_id}", "grid_a1")
        assert detail["pushed_at"]
        assert any(e["kind"] == "PUSH" and "紧急联系人" in e["detail"] for e in detail["events"])

    def test_sms_to_family_contains_no_vitals(self, api, world, db):
        result = record(api, world, point("SYSTOLIC", 190))
        rows = db.execute(select(Notification).where(Notification.ref_id == result["alerts"][0], Notification.recipient_role == "emergency_contact")).scalars().all()
        assert "190" not in rows[0].body and "老人甲" not in rows[0].body

    def test_blue_only_notifies_grid(self, api, world, db):
        clock.freeze(clock.now() + timedelta(hours=72))
        run_job(JOB_INDEX["alert_no_data"])
        alert = api.ok("GET", f"/alerts?elder_id={world.elder_a1}", "grid_a1")["items"][0]
        rows = db.execute(select(Notification).where(Notification.ref_id == alert["id"], Notification.ref_type == "alert")).scalars().all()
        assert {n.recipient_role for n in rows} == {"grid_worker"}

    def test_failed_sms_retries_then_fails(self, api, world, db):
        class Broken:
            def send(self, *_):
                raise RuntimeError("网关不可用")

        original = notification_service._gateway
        notification_service.set_gateway(Broken())
        try:
            result = record(api, world, point("SYSTOLIC", 190))
            assert result["alerts"]  # 短信失败不影响预警本身
            for _ in range(5):
                run_job(JOB_INDEX["flush_outbox"])
            db.rollback()
            rows = db.execute(select(Notification).where(Notification.channel == "SMS")).scalars().all()
            assert rows and all(n.status == "FAILED" for n in rows)
        finally:
            notification_service.set_gateway(original)

    def test_board_pins_red_on_top(self, api, world):
        now = clock.now()
        for offset, value in ((30, 105), (20, 110), (10, 108)):
            record(api, world, point("HEART_RATE", value, now - timedelta(minutes=offset)))
        record(api, world, point("SYSTOLIC", 190))
        items = api.ok("GET", "/alerts?only_open=true", "grid_a1")["items"]
        assert [a["level"] for a in items] == ["RED", "YELLOW"]

    def test_sos_creates_red_alert(self, api, world):
        alert = api.ok("POST", "/sos", "family_a1", json={"elder_id": world.elder_a1, "note": "摔倒了"})
        assert alert["level"] == "RED"
        assert "摔倒" in alert["message"]

    def test_sos_scope(self, api, world):
        assert api.post("/sos", "family_a1", json={"elder_id": world.elder_b1}).status_code == 404


class TestEscalation:
    def _red(self, api, world):
        return record(api, world, point("SYSTOLIC", 190))["alerts"][0]

    def test_not_escalated_at_29_minutes(self, api, world):
        alert_id = self._red(api, world)
        clock.freeze(clock.now() + timedelta(minutes=29, seconds=59))
        run_job(JOB_INDEX["alert_escalation"])
        assert api.ok("GET", f"/alerts/{alert_id}", "grid_a1")["escalated_at"] is None

    def test_escalated_at_30_minutes_to_street(self, api, world):
        alert_id = self._red(api, world)
        clock.freeze(clock.now() + timedelta(minutes=30))
        assert run_job(JOB_INDEX["alert_escalation"]).processed == 1
        detail = api.ok("GET", f"/alerts/{alert_id}", "street")
        assert detail["escalated_at"]
        assert any(e["kind"] == "ESCALATE" for e in detail["events"])
        assert "预警升级：收缩压过高" in [n["title"] for n in api.ok("GET", "/notifications", "street")["items"]]
        assert api.ok("GET", "/audit/logs?action=ALERT_ESCALATE", "street")["total"] == 1

    def test_escalated_only_once(self, api, world):
        self._red(api, world)
        clock.freeze(clock.now() + timedelta(minutes=31))
        run_job(JOB_INDEX["alert_escalation"])
        assert run_job(JOB_INDEX["alert_escalation"]).processed == 0

    def test_responded_alert_not_escalated(self, api, world):
        alert_id = self._red(api, world)
        clock.freeze(clock.now() + timedelta(minutes=10))
        api.ok("POST", f"/alerts/{alert_id}/respond", "grid_a1", json={"note": "已电话确认，正在赶往"})
        clock.freeze(clock.now() + timedelta(minutes=30))
        assert run_job(JOB_INDEX["alert_escalation"]).processed == 0

    def test_yellow_escalates_to_community_later(self, api, world):
        now = clock.now()
        for offset, value in ((30, 105), (20, 110), (10, 108)):
            record(api, world, point("HEART_RATE", value, now - timedelta(minutes=offset)))
        clock.freeze(now + timedelta(minutes=31))
        assert run_job(JOB_INDEX["alert_escalation"]).processed == 0
        clock.freeze(now + timedelta(minutes=240))
        assert run_job(JOB_INDEX["alert_escalation"]).processed == 1
        assert any("预警升级" in n["title"] for n in api.ok("GET", "/notifications", "comm_a")["items"])

    def test_sla_configurable(self, api, world):
        sla = next(c for c in api.ok("GET", "/admin/configs", "admin") if c["key"] == "alert.sla")["value"]
        sla["RED"]["escalate_minutes"] = 5
        api.ok("PUT", "/admin/configs/alert.sla", "admin", json={"value": sla})
        self._red(api, world)
        clock.freeze(clock.now() + timedelta(minutes=5))
        assert run_job(JOB_INDEX["alert_escalation"]).processed == 1


class TestClosure:
    def _red(self, api, world):
        return record(api, world, point("SYSTOLIC", 190))["alerts"][0]

    def test_handled_requires_measures(self, api, world):
        alert_id = self._red(api, world)
        assert api.post(f"/alerts/{alert_id}/close", "grid_a1", json={"conclusion": "HANDLED"}).status_code == 422

    def test_false_alarm_requires_reason(self, api, world):
        alert_id = self._red(api, world)
        assert api.post(f"/alerts/{alert_id}/close", "grid_a1", json={"conclusion": "FALSE_ALARM"}).status_code == 422

    def test_referred_requires_hospital_and_time(self, api, world):
        alert_id = self._red(api, world)
        assert api.post(f"/alerts/{alert_id}/close", "grid_a1", json={"conclusion": "REFERRED", "hospital": "一院"}).status_code == 422
        body = {"conclusion": "REFERRED", "hospital": "一院", "referred_at": datetime(2026, 6, 15, 10, 30).isoformat()}
        assert api.post(f"/alerts/{alert_id}/close", "grid_a1", json=body).json()["conclusion"] == "REFERRED"

    def test_only_three_conclusions_allowed(self, api, world):
        alert_id = self._red(api, world)
        assert api.post(f"/alerts/{alert_id}/close", "grid_a1", json={"conclusion": "IGNORED"}).status_code == 422

    def test_handle_duration_and_actor_recorded(self, api, world):
        alert_id = self._red(api, world)
        clock.freeze(clock.now() + timedelta(minutes=17))
        closed = api.ok("POST", f"/alerts/{alert_id}/close", "grid_a1", json={"conclusion": "HANDLED", "measures": "用药后复测正常"})
        assert closed["handle_minutes"] == 17 and closed["closed_by"] == "grid_a1"
        assert closed["responded_at"] is not None

    def test_closed_cannot_be_closed_again(self, api, world):
        alert_id = self._red(api, world)
        body = {"conclusion": "HANDLED", "measures": "ok"}
        api.ok("POST", f"/alerts/{alert_id}/close", "grid_a1", json=body)
        assert api.post(f"/alerts/{alert_id}/close", "grid_a1", json=body).status_code == 409

    def test_note_required_to_respond(self, api, world):
        assert api.post(f"/alerts/{self._red(api, world)}/respond", "grid_a1", json={"note": ""}).status_code == 422

    def test_other_grid_cannot_handle(self, api, world):
        alert_id = self._red(api, world)
        assert api.post(f"/alerts/{alert_id}/respond", "grid_a2", json={"note": "x"}).status_code == 404

    def test_full_timeline_preserved(self, api, world, db):
        alert_id = self._red(api, world)
        api.ok("POST", f"/alerts/{alert_id}/respond", "grid_a1", json={"note": "已到场"})
        api.ok("POST", f"/alerts/{alert_id}/close", "grid_a1", json={"conclusion": "HANDLED", "measures": "已处置"})
        kinds = [e.kind for e in db.execute(select(AlertEvent).where(AlertEvent.alert_id == alert_id).order_by(AlertEvent.id)).scalars()]
        assert kinds == ["TRIGGER", "PUSH", "RESPOND", "CLOSE"]

