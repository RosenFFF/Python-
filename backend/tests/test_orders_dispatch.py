"""预约受理与智能派单。"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock
from app.db.session import new_session
from app.jobs.tasks import JOB_INDEX, run_job
from app.main import app
from app.models import CaregiverSlot, DispatchOffer, ServiceOrder
from app.services import dispatch_service, order_service
from tests.factory import KM, LAT, LNG
from tests.helpers import TOMORROW_14, accepted_order, create_order, dispatched_order, offered_order


class TestBookingValidation:
    def test_valid_booking_is_pending_accept(self, api, world):
        order = create_order(api, world)
        assert order["status"] == "PENDING_ACCEPT" and not order["needs_manual"]
        assert order["validation_issues"] == []

    def test_past_time_rejected(self, api, world):
        response = api.post("/orders", "family_a1", json={"elder_id": world.elder_a1, "item_id": world.item_bath, "scheduled_start": "2026-06-15T09:59:00"})
        assert response.status_code == 422

    def test_duplicate_within_two_hours_goes_manual(self, api, world):
        create_order(api, world)
        again = create_order(api, world, start=TOMORROW_14 + timedelta(hours=1))
        assert again["needs_manual"]
        assert again["validation_issues"][0]["code"] == "DUPLICATE_TIME"

    def test_booking_outside_two_hour_window_passes(self, api, world):
        create_order(api, world)  # 14:00-15:00
        later = create_order(api, world, item=world.item_meal, start=TOMORROW_14 + timedelta(hours=3, minutes=1))
        assert not later["needs_manual"]
        earlier = create_order(api, world, item=world.item_meal, start=TOMORROW_14 - timedelta(hours=3))
        assert not earlier["needs_manual"]

    def test_cancelled_order_does_not_block_rebooking(self, api, world):
        order = create_order(api, world)
        api.ok("POST", f"/orders/{order['id']}/cancel", "family_a1", json={"reason": "改期"})
        assert not create_order(api, world)["needs_manual"]

    def test_out_of_range_address_goes_manual(self, api, world):
        order = create_order(api, world, lat=LAT + 4 * KM, lng=LNG, address="很远的地方")
        assert order["validation_issues"][0]["code"] == "OUT_OF_RANGE"

    def test_level_mismatch_goes_manual(self, api, world):
        order = create_order(api, world, elder=world.elder_light, user="grid_a1")
        assert order["validation_issues"][0]["code"] == "LEVEL_MISMATCH"
        assert "能力完好" in order["validation_issues"][0]["message"]

    def test_lat_without_lng_rejected(self, api, world):
        body = {"elder_id": world.elder_a1, "item_id": world.item_bath, "scheduled_start": TOMORROW_14.isoformat(), "lat": LAT}
        assert api.post("/orders", "family_a1", json=body).status_code == 422

    def test_priority_reflects_level_and_living_alone(self, api, world):
        high = create_order(api, world)  # 中度失能 + 独居
        low = create_order(api, world, elder=world.elder_a2, item=world.item_meal, user="grid_a2")
        assert high["priority"] == 50 and low["priority"] == 10

    def test_family_notified_grid_worker(self, api, world):
        create_order(api, world)
        titles = [n["title"] for n in api.ok("GET", "/notifications", "grid_a1")["items"]]
        assert "新预约待受理" in titles


class TestAcceptance:
    def test_accept_passes_through_accepted_to_pending_dispatch(self, api, world):
        order = accepted_order(api, world)
        assert order["status"] == "PENDING_DISPATCH"
        timeline = api.ok("GET", f"/orders/{order['id']}", "grid_a1")["timeline"]
        assert [e["to"] for e in timeline if e["to"]] == ["PENDING_ACCEPT", "ACCEPTED", "PENDING_DISPATCH"]

    def test_manual_order_requires_note(self, api, world):
        order = create_order(api, world, elder=world.elder_light, user="grid_a1")
        assert api.post(f"/orders/{order['id']}/accept", "grid_a1", json={}).status_code == 422
        assert api.post(f"/orders/{order['id']}/accept", "grid_a1", json={"note": "已与家属确认"}).status_code == 200

    def test_cannot_accept_twice(self, api, world):
        order = accepted_order(api, world)
        response = api.post(f"/orders/{order['id']}/accept", "grid_a1", json={})
        assert response.status_code == 409 and response.json()["code"] == "INVALID_TRANSITION"

    def test_family_cannot_accept(self, api, world):
        order = create_order(api, world)
        assert api.post(f"/orders/{order['id']}/accept", "family_a1", json={}).status_code == 403


class TestCancellation:
    def test_reason_required(self, api, world):
        order = create_order(api, world)
        assert api.post(f"/orders/{order['id']}/cancel", "family_a1", json={"reason": ""}).status_code == 422

    def test_more_than_two_hours_free(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14 - timedelta(hours=2, minutes=1))
        body = api.ok("POST", f"/orders/{order['id']}/cancel", "family_a1", json={"reason": "改期"})
        assert body["status"] == "CANCELLED" and not body["cancel_chargeable"]

    def test_exactly_two_hours_is_free(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14 - timedelta(hours=2))
        assert not api.ok("POST", f"/orders/{order['id']}/cancel", "family_a1", json={"reason": "改期"})["cancel_chargeable"]

    def test_within_two_hours_charged(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14 - timedelta(hours=1, minutes=59))
        preview = api.ok("GET", f"/orders/{order['id']}/cancel-preview", "family_a1")
        assert preview["chargeable"] and preview["fee"] == 2400  # 80 元 × 30%
        assert api.ok("POST", f"/orders/{order['id']}/cancel", "family_a1", json={"reason": "临时有事"})["cancel_chargeable"]

    def test_late_cancel_before_dispatch_not_charged(self, api, world):
        order = accepted_order(api, world)
        clock.freeze(TOMORROW_14 - timedelta(hours=1))
        assert not api.ok("POST", f"/orders/{order['id']}/cancel", "family_a1", json={"reason": "不需要了"})["cancel_chargeable"]

    def test_cancel_releases_caregiver_slots(self, api, world, db):
        order = dispatched_order(api, world)
        api.ok("POST", f"/orders/{order['id']}/cancel", "family_a1", json={"reason": "改期"})
        assert db.execute(select(CaregiverSlot).where(CaregiverSlot.order_id == order["id"])).first() is None

    def test_terminal_state_cannot_be_cancelled(self, api, world):
        order = create_order(api, world)
        api.ok("POST", f"/orders/{order['id']}/cancel", "family_a1", json={"reason": "改期"})
        assert api.post(f"/orders/{order['id']}/cancel", "family_a1", json={"reason": "再取消"}).status_code == 409


class TestRecurring:
    def test_plan_generates_occurrences_with_idempotency(self, api, world, db):
        monday = api.ok("POST", "/plans", "family_a1", json={
            "elder_id": world.elder_a1, "item_id": world.item_meal, "weekday": 1, "time_of_day": "09:00", "start_date": "2026-06-15"})
        rows = db.execute(select(ServiceOrder).where(ServiceOrder.plan_id == monday["id"])).scalars().all()
        assert len(rows) == 1 and rows[0].scheduled_start == datetime(2026, 6, 16, 9, 0)
        assert rows[0].source == "RECURRING"
        db.rollback()
        session = new_session()
        assert order_service.generate_recurring_orders(session, clock.now()) == 0
        session.rollback()
        session.close()

    def test_rolling_generation_next_week(self, api, world, db):
        plan = api.ok("POST", "/plans", "family_a1", json={
            "elder_id": world.elder_a1, "item_id": world.item_meal, "weekday": 1, "time_of_day": "09:00", "start_date": "2026-06-15"})
        clock.freeze(datetime(2026, 6, 20, 0, 10))
        session = new_session()
        assert order_service.generate_recurring_orders(session, clock.now()) == 1
        session.commit()
        session.close()
        assert db.execute(select(ServiceOrder).where(ServiceOrder.plan_id == plan["id"], ServiceOrder.scheduled_start == datetime(2026, 6, 23, 9, 0))).first()

    def test_end_date_respected(self, api, world, db):
        plan = api.ok("POST", "/plans", "family_a1", json={
            "elder_id": world.elder_a1, "item_id": world.item_meal, "weekday": 1, "time_of_day": "09:00",
            "start_date": "2026-06-15", "end_date": "2026-06-15"})
        assert db.execute(select(ServiceOrder).where(ServiceOrder.plan_id == plan["id"])).first() is None

    def test_stopped_plan_generates_nothing(self, api, world):
        plan = api.ok("POST", "/plans", "family_a1", json={
            "elder_id": world.elder_a1, "item_id": world.item_meal, "weekday": 1, "time_of_day": "09:00", "start_date": "2026-06-15"})
        api.ok("DELETE", f"/plans/{plan['id']}", "family_a1")
        clock.freeze(datetime(2026, 6, 20, 0, 10))
        session = new_session()
        assert order_service.generate_recurring_orders(session, clock.now()) == 0
        session.close()

    def test_conflict_in_recurring_goes_manual(self, api, world, db):
        create_order(api, world, item=world.item_meal, start=datetime(2026, 6, 16, 9, 30))
        plan = api.ok("POST", "/plans", "family_a1", json={
            "elder_id": world.elder_a1, "item_id": world.item_meal, "weekday": 1, "time_of_day": "09:00", "start_date": "2026-06-15"})
        row = db.execute(select(ServiceOrder).where(ServiceOrder.plan_id == plan["id"])).scalar()
        assert row.needs_manual


class TestRecommendation:
    def test_top3_order_and_explanation(self, api, world):
        order = accepted_order(api, world)
        rec = api.ok("POST", f"/orders/{order['id']}/recommend", "grid_a1")
        assert [c["name"] for c in rec["candidates"]] == ["近护理", "中护理", "远护理"]
        top = rec["candidates"][0]
        assert set(top["factors"]) == {"skill", "distance", "load", "rating"}
        assert top["factors"]["distance"]["raw"].endswith("公里")
        excluded = {e["name"]: e["reason"] for e in rec["excluded"]}
        assert "服务半径" in excluded["外护理"]
        assert "技能匹配" in excluded["缺技能"] or "外护理" in excluded

    def test_only_pending_dispatch_can_be_recommended(self, api, world):
        order = create_order(api, world)
        assert api.post(f"/orders/{order['id']}/recommend", "grid_a1").status_code == 409

    def test_weights_config_takes_effect_and_audited(self, api, world):
        order = accepted_order(api, world)
        api.ok("PUT", "/admin/configs/dispatch.weights", "admin", json={"value": {"skill": 0.1, "distance": 0.1, "load": 0.1, "rating": 0.7}})
        rec = api.ok("POST", f"/orders/{order['id']}/recommend", "grid_a1")
        assert rec["weights"]["rating"] == 0.7
        assert rec["candidates"][0]["factors"]["rating"]["weight"] == 0.7
        assert api.ok("GET", "/audit/logs?action=CONFIG_UPDATE", "admin")["total"] == 1

    def test_invalid_weights_rejected_by_api(self, api):
        assert api.call("PUT", "/admin/configs/dispatch.weights", "admin", json={"value": {"skill": -1, "distance": 1, "load": 1, "rating": 1}}).status_code == 422

    def test_round_snapshot_kept_for_history(self, api, world):
        order = accepted_order(api, world)
        rec = api.ok("POST", f"/orders/{order['id']}/recommend", "grid_a1")
        history = api.ok("GET", f"/orders/{order['id']}/dispatch-history", "grid_a1")
        assert history["rounds"][0]["id"] == rec["round_id"]
        assert history["rounds"][0]["params"]["weights"]["skill"] == 0.4

    def test_load_factor_counts_day_orders(self, api, world):
        dispatched_order(api, world)  # 近护理当日已有 1 单
        order = accepted_order(api, world, elder=world.elder_a2, item=world.item_meal, user="grid_a2", grid="grid_a2",
                               start=TOMORROW_14 + timedelta(hours=4))
        rec = api.ok("POST", f"/orders/{order['id']}/recommend", "grid_a2")
        near = next(c for c in rec["candidates"] if c["name"] == "近护理")
        assert near["factors"]["load"]["raw"] == "1/6 单"

    def test_daily_limit_excludes_caregiver(self, api, world):
        for hour in range(6):
            dispatched_order(api, world, item=world.item_meal, start=datetime(2026, 6, 16, 5 + 3 * hour, 0), elder=world.elder_a1)
        order = accepted_order(api, world, elder=world.elder_a2, item=world.item_meal, user="grid_a2", grid="grid_a2", start=datetime(2026, 6, 16, 22, 0))
        rec = api.ok("POST", f"/orders/{order['id']}/recommend", "grid_a2")
        excluded = {e["name"]: e["reason"] for e in rec["excluded"]}
        assert "上限" in excluded["近护理"]


class TestConflictDetection:
    def test_overlapping_assignment_blocked(self, api, world):
        dispatched_order(api, world)
        other = accepted_order(api, world, elder=world.elder_a2, user="grid_a2", grid="grid_a2", start=TOMORROW_14 + timedelta(minutes=30))
        rec = api.ok("POST", f"/orders/{other['id']}/recommend", "grid_a2")
        assert "在途工单" in {e["name"]: e["reason"] for e in rec["excluded"]}["近护理"]
        response = api.post(f"/orders/{other['id']}/assign", "grid_a2", json={"caregiver_id": world.c_near, "round_id": rec["round_id"]})
        assert response.status_code == 409

    def test_pending_offer_also_holds_slot(self, api, world):
        offered_order(api, world, caregiver=world.c_near)
        other = accepted_order(api, world, elder=world.elder_a2, user="grid_a2", grid="grid_a2", start=TOMORROW_14 + timedelta(minutes=15))
        rec = api.ok("POST", f"/orders/{other['id']}/recommend", "grid_a2")
        assert "近护理" not in [c["name"] for c in rec["candidates"]]

    def test_non_overlapping_same_day_allowed(self, api, world):
        dispatched_order(api, world)
        other = accepted_order(api, world, elder=world.elder_a2, user="grid_a2", grid="grid_a2", start=TOMORROW_14 + timedelta(hours=2))
        rec = api.ok("POST", f"/orders/{other['id']}/recommend", "grid_a2")
        assert "近护理" in [c["name"] for c in rec["candidates"]]

    def test_reassign_to_busy_caregiver_blocked(self, api, world):
        dispatched_order(api, world)
        other = dispatched_order(api, world, elder=world.elder_a2, user="grid_a2", grid="grid_a2", caregiver=world.c_mid, cg_user="cg_mid", start=TOMORROW_14 + timedelta(minutes=30))
        response = api.post(f"/orders/{other['id']}/reassign", "grid_a2", json={"caregiver_id": world.c_near, "reason": "就近安排"})
        assert response.status_code == 409 and response.json()["code"] == "SLOT_CONFLICT"
        # 失败的改派不能留下半截状态
        detail = api.ok("GET", f"/orders/{other['id']}", "grid_a2")
        assert detail["status"] == "DISPATCHED" and detail["caregiver_name"] == "中护理"

    def test_concurrent_assign_only_one_wins(self, world):
        """多线程同时把同一护理员派给两张重叠工单，唯一约束保证只有一张成功。"""
        first_client, second_client = TestClient(app), TestClient(app)
        tokens = {}
        for name, client in (("grid_a1", first_client), ("grid_a2", second_client)):
            tokens[name] = client.post("/api/auth/login", json={"username": name, "password": "Test@2026"}).json()["access_token"]
        auth = {name: {"Authorization": f"Bearer {token}"} for name, token in tokens.items()}

        def make(client, user, elder):
            body = {"elder_id": elder, "item_id": world.item_meal, "scheduled_start": TOMORROW_14.isoformat()}
            order = client.post("/api/orders", json=body, headers=auth[user]).json()
            client.post(f"/api/orders/{order['id']}/accept", json={}, headers=auth[user])
            round_id = client.post(f"/api/orders/{order['id']}/recommend", json={}, headers=auth[user]).json()["round_id"]
            return order["id"], round_id

        one = make(first_client, "grid_a1", world.elder_a1)
        two = make(second_client, "grid_a2", world.elder_a2)
        results: list[int] = []
        barrier = threading.Barrier(2)

        def fire(client, user, order_id, round_id):
            barrier.wait()
            response = client.post(f"/api/orders/{order_id}/assign", json={"caregiver_id": world.c_near, "round_id": round_id}, headers=auth[user])
            results.append(response.status_code)

        threads = [threading.Thread(target=fire, args=(first_client, "grid_a1", *one)),
                   threading.Thread(target=fire, args=(second_client, "grid_a2", *two))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == [200, 409]

    def test_slot_starts_expand_to_15_minutes(self):
        slots = dispatch_service.slot_starts(datetime(2026, 6, 16, 14, 7), datetime(2026, 6, 16, 14, 40))
        assert slots == [datetime(2026, 6, 16, 14, 0), datetime(2026, 6, 16, 14, 15), datetime(2026, 6, 16, 14, 30)]


class TestOfferLifecycle:
    def test_order_stays_pending_until_caregiver_accepts(self, api, world):
        order, _ = offered_order(api, world, caregiver=world.c_near)
        assert api.ok("GET", f"/orders/{order['id']}", "grid_a1")["status"] == "PENDING_DISPATCH"

    def test_accept_dispatches_and_notifies_family(self, api, world):
        order = dispatched_order(api, world)
        detail = api.ok("GET", f"/orders/{order['id']}", "family_a1")
        assert detail["status"] == "DISPATCHED" and detail["caregiver_name"] == "近护理"
        titles = [n["title"] for n in api.ok("GET", "/notifications", "family_a1")["items"]]
        assert "派单成功" in titles

    def test_only_offered_caregiver_can_respond(self, api, world):
        _, assignment = offered_order(api, world, caregiver=world.c_near)
        assert api.post(f"/offers/{assignment['offer_id']}/accept", "cg_mid").status_code == 403

    def test_reject_requires_reason_and_advances(self, api, world):
        order, assignment = offered_order(api, world, caregiver=world.c_near)
        assert api.post(f"/offers/{assignment['offer_id']}/reject", "cg_near", json={"reason": ""}).status_code == 422
        api.ok("POST", f"/offers/{assignment['offer_id']}/reject", "cg_near", json={"reason": "身体不适"})
        history = api.ok("GET", f"/orders/{order['id']}/dispatch-history", "grid_a1")
        assert [o["status"] for o in history["offers"]] == ["REJECTED", "OFFERED"]
        assert history["offers"][1]["caregiver_name"] == "中护理"

    def test_offer_cannot_be_answered_twice(self, api, world):
        _, assignment = offered_order(api, world, caregiver=world.c_near)
        api.ok("POST", f"/offers/{assignment['offer_id']}/accept", "cg_near")
        assert api.post(f"/offers/{assignment['offer_id']}/accept", "cg_near").status_code == 409

    def test_timeout_advances_to_next_candidate(self, api, world):
        order, _ = offered_order(api, world, caregiver=world.c_near)
        clock.freeze(clock.now() + timedelta(minutes=30))
        assert run_job(JOB_INDEX["expire_offers"]).processed == 1
        history = api.ok("GET", f"/orders/{order['id']}/dispatch-history", "grid_a1")
        assert [o["status"] for o in history["offers"]] == ["TIMEOUT", "OFFERED"]
        assert "派单已顺延" in [n["title"] for n in api.ok("GET", "/notifications", "grid_a1")["items"]]

    def test_not_expired_at_29_minutes(self, api, world):
        offered_order(api, world, caregiver=world.c_near)
        clock.freeze(clock.now() + timedelta(minutes=29))
        assert run_job(JOB_INDEX["expire_offers"]).processed == 0

    def test_accept_after_deadline_rejected(self, api, world):
        _, assignment = offered_order(api, world, caregiver=world.c_near)
        clock.freeze(clock.now() + timedelta(minutes=31))
        assert api.post(f"/offers/{assignment['offer_id']}/accept", "cg_near").status_code == 409

    def test_exhausted_candidates_go_manual(self, api, world):
        order, _ = offered_order(api, world, caregiver=world.c_near)
        for _ in range(3):
            clock.freeze(clock.now() + timedelta(minutes=31))
            run_job(JOB_INDEX["expire_offers"])
        detail = api.ok("GET", f"/orders/{order['id']}", "grid_a1")
        assert detail["manual_coordination"] and detail["status"] == "PENDING_DISPATCH"
        assert "派单失败，需人工协调" in [n["title"] for n in api.ok("GET", "/notifications", "grid_a1")["items"]]

    def test_consecutive_rejects_trigger_reminder(self, api, world):
        for index in range(3):
            order, assignment = offered_order(api, world, caregiver=world.c_near, elder=world.elder_a1, item=world.item_meal,
                                              start=TOMORROW_14 + timedelta(days=index))
            api.ok("POST", f"/offers/{assignment['offer_id']}/reject", "cg_near", json={"reason": "排不开"})
        titles = [n["title"] for n in api.ok("GET", "/notifications", "org_x")["items"]]
        assert "连续拒单提醒" in titles

    def test_accept_resets_reject_counter(self, api, world, db):
        order, assignment = offered_order(api, world, caregiver=world.c_near, item=world.item_meal)
        api.ok("POST", f"/offers/{assignment['offer_id']}/reject", "cg_near", json={"reason": "排不开"})
        dispatched_order(api, world, item=world.item_meal, start=TOMORROW_14 + timedelta(days=1))
        cared = api.ok("GET", "/caregivers", "org_x")
        assert next(c for c in cared if c["id"] == world.c_near)["consecutive_rejects"] == 0

    def test_assign_without_caregiver_picks_top1(self, api, world):
        order = accepted_order(api, world)
        result = api.ok("POST", f"/orders/{order['id']}/assign", "grid_a1", json={})
        assert result["caregiver_name"] == "近护理"

    def test_cannot_double_assign(self, api, world):
        order, _ = offered_order(api, world, caregiver=world.c_near)
        assert api.post(f"/orders/{order['id']}/assign", "grid_a1", json={"caregiver_id": world.c_mid}).status_code == 409


class TestReassign:
    def test_reason_required(self, api, world):
        order = dispatched_order(api, world)
        assert api.post(f"/orders/{order['id']}/reassign", "grid_a1", json={"caregiver_id": world.c_mid, "reason": ""}).status_code == 422

    def test_reassign_dispatched_returns_to_offer_and_audits(self, api, world, db):
        order = dispatched_order(api, world)
        result = api.ok("POST", f"/orders/{order['id']}/reassign", "grid_a1", json={"caregiver_id": world.c_mid, "reason": "原护理员请假"})
        detail = api.ok("GET", f"/orders/{order['id']}", "grid_a1")
        assert detail["status"] == "PENDING_DISPATCH" and detail["caregiver_id"] is None
        api.ok("POST", f"/offers/{result['offer_id']}/accept", "cg_mid")
        detail = api.ok("GET", f"/orders/{order['id']}", "grid_a1")
        assert detail["status"] == "DISPATCHED" and detail["caregiver_name"] == "中护理"
        logs = api.ok("GET", "/audit/logs?action=ORDER_REASSIGN", "street")["items"]
        assert "原护理员请假" in logs[0]["detail"]

    def test_old_caregiver_slots_released(self, api, world, db):
        order = dispatched_order(api, world)
        api.ok("POST", f"/orders/{order['id']}/reassign", "grid_a1", json={"caregiver_id": world.c_mid, "reason": "请假"})
        rows = db.execute(select(CaregiverSlot).where(CaregiverSlot.order_id == order["id"])).scalars().all()
        assert {r.caregiver_id for r in rows} == {world.c_mid}

    def test_pending_offer_revoked_on_reassign(self, api, world, db):
        order, assignment = offered_order(api, world, caregiver=world.c_near)
        api.ok("POST", f"/orders/{order['id']}/reassign", "grid_a1", json={"caregiver_id": world.c_mid, "reason": "距离太远"})
        assert db.get(DispatchOffer, assignment["offer_id"]).status == "REVOKED"
        assert api.post(f"/offers/{assignment['offer_id']}/accept", "cg_near").status_code == 409

    def test_in_service_cannot_be_reassigned(self, api, world):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14)
        api.ok("POST", f"/orders/{order['id']}/check-in", "cg_near", json={"lat": LAT, "lng": LNG})
        assert api.post(f"/orders/{order['id']}/reassign", "grid_a1", json={"caregiver_id": world.c_mid, "reason": "x"}).status_code == 409
