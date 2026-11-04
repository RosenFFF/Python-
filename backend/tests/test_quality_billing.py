"""服务评价、投诉、绩效与费用结算。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app.core import clock
from app.db.session import new_session
from app.jobs.tasks import JOB_INDEX, run_job
from app.models import Bill, Caregiver, ElderStatement, FeeDetail, ServiceOrder, SubsidyPolicy
from tests.helpers import TOMORROW_14, completed_order, dispatched_order

GOOD = {"punctuality": 5, "attitude": 5, "professionalism": 4, "effectiveness": 5}
BAD = {"punctuality": 2, "attitude": 3, "professionalism": 3, "effectiveness": 4, "comment": "迟到了"}


class TestReview:
    def test_overall_is_mean_of_four(self, api, world):
        order = completed_order(api, world)
        assert api.ok("POST", f"/orders/{order['id']}/review", "family_a1", json=GOOD)["overall"] == 4.75

    def test_only_completed_order(self, api, world):
        order = dispatched_order(api, world)
        assert api.post(f"/orders/{order['id']}/review", "family_a1", json=GOOD).status_code == 409

    def test_stars_range_validated(self, api, world):
        order = completed_order(api, world)
        assert api.post(f"/orders/{order['id']}/review", "family_a1", json={**GOOD, "attitude": 6}).status_code == 422
        assert api.post(f"/orders/{order['id']}/review", "family_a1", json={**GOOD, "attitude": 0}).status_code == 422

    def test_one_review_per_order(self, api, world):
        order = completed_order(api, world)
        api.ok("POST", f"/orders/{order['id']}/review", "family_a1", json=GOOD)
        assert api.post(f"/orders/{order['id']}/review", "family_a1", json=GOOD).status_code == 409

    def test_24_hour_window(self, api, world):
        order = completed_order(api, world)
        clock.freeze(clock.now() + timedelta(hours=24, minutes=1))
        assert api.post(f"/orders/{order['id']}/review", "family_a1", json=GOOD).status_code == 409

    def test_other_family_cannot_review(self, api, world):
        order = completed_order(api, world)
        assert api.post(f"/orders/{order['id']}/review", "family_b1", json=GOOD).status_code == 404

    def test_score_of_three_creates_followup(self, api, world):
        order = completed_order(api, world)
        api.ok("POST", f"/orders/{order['id']}/review", "family_a1", json={"punctuality": 3, "attitude": 3, "professionalism": 3, "effectiveness": 3})
        tasks = api.ok("GET", "/followups?status=OPEN", "comm_a")
        assert len(tasks) == 1 and tasks[0]["overall"] == 3
        assert datetime.fromisoformat(tasks[0]["due_at"]) == clock.now() + timedelta(hours=48)

    def test_score_above_three_no_followup(self, api, world):
        order = completed_order(api, world)
        api.ok("POST", f"/orders/{order['id']}/review", "family_a1", json={"punctuality": 4, "attitude": 3, "professionalism": 3, "effectiveness": 3})
        assert api.ok("GET", "/followups", "comm_a") == []


class TestFollowup:
    def _task(self, api, world):
        order = completed_order(api, world)
        api.ok("POST", f"/orders/{order['id']}/review", "family_a1", json=BAD)
        return order, api.ok("GET", "/followups?status=OPEN", "comm_a")[0]

    def test_improve_closes_task(self, api, world):
        _, task = self._task(api, world)
        done = api.ok("POST", f"/followups/{task['id']}/handle", "comm_a", json={"outcome": "IMPROVE", "note": "已反馈机构改进"})
        assert done["status"] == "DONE"
        assert api.ok("GET", "/complaints", "comm_a") == []

    def test_to_complaint_creates_complaint(self, api, world):
        order, task = self._task(api, world)
        api.ok("POST", f"/followups/{task['id']}/handle", "comm_a", json={"outcome": "TO_COMPLAINT", "note": "家属坚持投诉"})
        complaints = api.ok("GET", "/complaints", "comm_a")
        assert complaints[0]["order_id"] == order["id"] and complaints[0]["source"] == "FOLLOWUP"

    def test_overdue_flagged_and_escalated_once(self, api, world):
        self._task(api, world)
        clock.freeze(clock.now() + timedelta(hours=47))
        assert run_job(JOB_INDEX["followup_overdue"]).processed == 0
        clock.freeze(clock.now() + timedelta(hours=1, minutes=1))
        assert run_job(JOB_INDEX["followup_overdue"]).processed == 1
        assert run_job(JOB_INDEX["followup_overdue"]).processed == 0
        assert "回访任务超时" in [n["title"] for n in api.ok("GET", "/notifications", "street")["items"]]
        assert api.ok("GET", "/followups", "comm_a")[0]["overdue"] is True

    def test_other_community_cannot_handle(self, api, world):
        _, task = self._task(api, world)
        assert api.post(f"/followups/{task['id']}/handle", "comm_b", json={"outcome": "IMPROVE", "note": "x"}).status_code == 404


class TestComplaint:
    def _at_explained(self, api, world, start=None):
        order = completed_order(api, world, start=start)
        complaint = api.ok("POST", "/complaints", "family_a1", json={"order_id": order["id"], "content": "服务不到位"})
        api.ok("POST", f"/complaints/{complaint['id']}/assign", "comm_a")
        api.ok("POST", f"/complaints/{complaint['id']}/explain", "org_x", json={"explanation": "情况属实"})
        return order, complaint

    def test_flow_cannot_skip_steps(self, api, world):
        order = completed_order(api, world)
        complaint = api.ok("POST", "/complaints", "family_a1", json={"order_id": order["id"], "content": "x"})
        assert api.post(f"/complaints/{complaint['id']}/explain", "org_x", json={"explanation": "y"}).status_code == 409
        assert api.post(f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "UPHELD", "basis": "z"}).status_code == 409

    def test_roles_enforced(self, api, world):
        order = completed_order(api, world)
        complaint = api.ok("POST", "/complaints", "family_a1", json={"order_id": order["id"], "content": "x"})
        assert api.post(f"/complaints/{complaint['id']}/assign", "org_x").status_code == 403
        api.ok("POST", f"/complaints/{complaint['id']}/assign", "comm_a")
        assert api.post(f"/complaints/{complaint['id']}/explain", "comm_a", json={"explanation": "y"}).status_code == 403
        assert api.post(f"/complaints/{complaint['id']}/explain", "org_y", json={"explanation": "y"}).status_code == 404

    def test_refund_generates_red_flush(self, api, world, db):
        order, complaint = self._at_explained(api, world)
        concluded = api.ok("POST", f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "UPHELD", "basis": "现场记录不符", "actions": ["REFUND"]})
        assert concluded["status"] == "CONCLUDED"
        fees = db.execute(select(FeeDetail).where(FeeDetail.order_id == order["id"]).order_by(FeeDetail.id)).scalars().all()
        assert [f.amount for f in fees] == [8000, -8000]
        assert fees[1].fee_type == "REFUND" and fees[1].refund_of_id == fees[0].id
        assert fees[0].gov_amount + fees[1].gov_amount == 0
        assert api.ok("GET", "/audit/logs?action=REFUND", "street")["total"] == 1

    def test_refund_only_once(self, api, world):
        order, complaint = self._at_explained(api, world)
        api.ok("POST", f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "UPHELD", "basis": "x", "actions": ["REFUND"]})
        second = api.ok("POST", "/complaints", "family_a1", json={"order_id": order["id"], "content": "再次投诉"})
        api.ok("POST", f"/complaints/{second['id']}/assign", "comm_a")
        api.ok("POST", f"/complaints/{second['id']}/explain", "org_x", json={"explanation": "y"})
        assert api.post(f"/complaints/{second['id']}/conclude", "comm_a", json={"conclusion": "UPHELD", "basis": "x", "actions": ["REFUND"]}).status_code == 409

    def test_rejected_cannot_have_actions(self, api, world):
        _, complaint = self._at_explained(api, world)
        assert api.post(f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "REJECTED", "basis": "x", "actions": ["REFUND"]}).status_code == 422

    def test_basis_required(self, api, world):
        _, complaint = self._at_explained(api, world)
        assert api.post(f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "UPHELD", "basis": " "}).status_code == 422

    def test_penalty_requires_points(self, api, world):
        _, complaint = self._at_explained(api, world)
        assert api.post(f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "UPHELD", "basis": "x", "actions": ["PENALTY"]}).status_code == 422
        ok = api.ok("POST", f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "UPHELD", "basis": "x", "actions": ["PENALTY"], "penalty_points": 3})
        assert ok["penalty_points"] == 3

    def test_replace_excludes_caregiver_for_elder(self, api, world):
        future = dispatched_order(api, world, item=world.item_meal, start=TOMORROW_14 + timedelta(days=7))
        _, complaint = self._at_explained(api, world)
        api.ok("POST", f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "UPHELD", "basis": "x", "actions": ["REPLACE"]})
        detail = api.ok("GET", f"/orders/{future['id']}", "grid_a1")
        assert detail["status"] == "PENDING_DISPATCH" and detail["caregiver_id"] is None
        rec = api.ok("POST", f"/orders/{future['id']}/recommend", "grid_a1")
        assert "近护理" not in [c["name"] for c in rec["candidates"]]

    def test_three_upheld_in_month_flags_key_attention_and_downranks(self, api, world, db):
        base = TOMORROW_14
        for index in range(3):
            _, complaint = self._at_explained(api, world, start=base + timedelta(days=index))
            api.ok("POST", f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "UPHELD", "basis": "属实"})
        caregiver = db.get(Caregiver, world.c_near)
        assert caregiver.key_attention_month == "2026-06"
        assert "护理员已列入重点关注" in [n["title"] for n in api.ok("GET", "/notifications", "org_x")["items"]]
        order = api.ok("POST", "/orders", "family_a1", json={"elder_id": world.elder_a1, "item_id": world.item_meal, "scheduled_start": (base + timedelta(days=9)).isoformat()})
        api.ok("POST", f"/orders/{order['id']}/accept", "grid_a1", json={})
        rec = api.ok("POST", f"/orders/{order['id']}/recommend", "grid_a1")
        near = next(c for c in rec["candidates"] if c["name"] == "近护理")
        assert near["key_attention"] and near["penalty_factor"] == 0.8

    def test_two_upheld_not_flagged(self, api, world, db):
        for index in range(2):
            _, complaint = self._at_explained(api, world, start=TOMORROW_14 + timedelta(days=index))
            api.ok("POST", f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "UPHELD", "basis": "属实"})
        assert db.get(Caregiver, world.c_near).key_attention_month is None

    def test_rejected_complaints_do_not_count(self, api, world, db):
        for index in range(3):
            _, complaint = self._at_explained(api, world, start=TOMORROW_14 + timedelta(days=index))
            api.ok("POST", f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "REJECTED", "basis": "不属实"})
        assert db.get(Caregiver, world.c_near).key_attention_month is None


class TestPerformance:
    def test_monthly_metrics(self, api, world):
        first = completed_order(api, world)
        api.ok("POST", f"/orders/{first['id']}/review", "family_a1", json=GOOD)
        late_start = TOMORROW_14 + timedelta(days=1)
        order = dispatched_order(api, world, start=late_start, item=world.item_meal)
        clock.freeze(late_start + timedelta(minutes=10))
        api.ok("POST", f"/orders/{order['id']}/check-in", "cg_near", json={"lat": 30.0, "lng": 104.0})
        api.ok("POST", f"/orders/{order['id']}/exceptions", "cg_near", json={"exception_type": "REFUSED", "description": "x"})
        rows = api.ok("GET", "/performance?month=2026-06", "org_x")
        me = next(r for r in rows if r["caregiver_id"] == world.c_near)
        assert me["completed"] == 1 and me["avg_rating"] == 4.75
        assert me["on_time_rate"] == 0.5
        assert me["exception_rate"] == 0.5

    def test_sorting_and_rank(self, api, world):
        completed_order(api, world)
        rows = api.ok("GET", "/performance?month=2026-06&sort=completed", "org_x")
        assert rows[0]["caregiver_id"] == world.c_near and rows[0]["rank"] == 1

    def test_no_data_metrics_are_none(self, api, world):
        rows = api.ok("GET", "/performance?month=2026-06", "org_x")
        assert rows[0]["on_time_rate"] is None and rows[0]["avg_rating"] is None

    def test_org_scope(self, api, world):
        names = {r["name"] for r in api.ok("GET", "/performance?month=2026-06", "org_y")}
        assert names == {"远护理", "外护理", "缺技能"}


def _bill(api, world, month="2026-06", community=None, user="comm_a"):
    return api.ok("POST", "/bills/generate", user, json={"community_id": community or world.community_a, "month": month})


def _fee_of(db, order_id, fee_type="SERVICE"):
    return db.execute(select(FeeDetail).where(FeeDetail.order_id == order_id, FeeDetail.fee_type == fee_type)).scalar()


class TestFees:
    def test_split_with_ltci_and_policy(self, api, world, db):
        # 老人甲：中度失能 + 长护险，无困难。政府 min(0.5, 0.3)=30%，长护 30%，自付 40%
        order = completed_order(api, world)
        fee = _fee_of(db, order["id"])
        assert (fee.amount, fee.gov_amount, fee.ltci_amount, fee.self_amount) == (8000, 2400, 2400, 3200)

    def test_difficulty_type_changes_ratio(self, api, world, db):
        api.ok("PATCH", f"/elders/{world.elder_a1}", "grid_a1", json={"difficulty_type": "LOW_INCOME"})
        order = completed_order(api, world)
        assert _fee_of(db, order["id"]).gov_amount == 4000

    def test_hourly_fee_uses_actual_duration(self, api, world, db):
        order = completed_order(api, world, item=world.item_clean, minutes=135)
        fee = _fee_of(db, order["id"])
        assert fee.quantity == 2.25 and fee.amount == 9000

    def test_not_eligible_item_all_self_paid(self, api, world, db):
        order = completed_order(api, world, item=world.item_errand, minutes=45)
        fee = _fee_of(db, order["id"])
        assert fee.gov_amount == 0 and fee.ltci_amount == 0 and fee.self_amount == 2000

    def test_price_change_does_not_affect_order_fee(self, api, world, db):
        order = dispatched_order(api, world)
        api.ok("POST", f"/catalog/items/{world.item_bath}/price", "org_x", json={"unit_price": 20000, "reason": "调价"})
        clock.freeze(TOMORROW_14)
        api.ok("POST", f"/orders/{order['id']}/check-in", "cg_near", json={"lat": 30.0, "lng": 104.0})
        items = [{"name": n, "done": True} for n in ("助浴", "擦身", "更衣", "指甲修剪")]
        api.ok("PUT", f"/orders/{order['id']}/checklist", "cg_near", json={"items": items})
        clock.freeze(TOMORROW_14 + timedelta(minutes=60))
        api.ok("POST", f"/orders/{order['id']}/check-out", "cg_near", json={"summary": "完成"})
        assert _fee_of(db, order["id"]).unit_price == 8000

    def test_late_cancel_creates_self_paid_fee(self, api, world, db):
        order = dispatched_order(api, world)
        clock.freeze(TOMORROW_14 - timedelta(hours=1))
        api.ok("POST", f"/orders/{order['id']}/cancel", "family_a1", json={"reason": "临时有事"})
        fee = _fee_of(db, order["id"], "CANCEL_FEE")
        assert fee.amount == 2400 and fee.self_amount == 2400 and fee.gov_amount == 0

    def test_free_cancel_no_fee(self, api, world, db):
        order = dispatched_order(api, world)
        api.ok("POST", f"/orders/{order['id']}/cancel", "family_a1", json={"reason": "改期"})
        assert _fee_of(db, order["id"], "CANCEL_FEE") is None


class TestBillGeneration:
    def test_totals_from_clean_month(self, api, world):
        completed_order(api, world)
        bill = _bill(api, world)
        assert bill["status"] == "DRAFT" and bill["total_amount"] == 8000
        assert bill["gov_amount"] + bill["ltci_amount"] + bill["self_amount"] == 8000
        assert bill["discrepancy_ratio"] == 0

    def test_duplicate_intercepted(self, api, world):
        order = completed_order(api, world)
        api.ok("POST", "/fees/manual", "comm_a", json={"order_id": order["id"], "amount": 8000, "note": "重复申报"})
        bill = _bill(api, world)
        detail = api.ok("GET", f"/bills/{bill['id']}", "comm_a")
        kinds = [d["kind"] for d in detail["discrepancies"]]
        assert "DUPLICATE" in kinds
        assert bill["total_amount"] == 8000 and bill["intercepted_amount"] == 8000

    def test_not_completed_intercepted(self, api, world):
        order = dispatched_order(api, world)
        api.ok("POST", "/fees/manual", "comm_a", json={"order_id": order["id"], "amount": 5000, "note": "提前申报"})
        bill = _bill(api, world)
        detail = api.ok("GET", f"/bills/{bill['id']}", "comm_a")
        assert detail["discrepancies"][0]["kind"] == "NOT_COMPLETED"
        assert bill["total_amount"] == 0

    def test_duration_over_three_times_intercepted(self, api, world):
        completed_order(api, world, minutes=181)
        bill = _bill(api, world)
        detail = api.ok("GET", f"/bills/{bill['id']}", "comm_a")
        assert [d["kind"] for d in detail["discrepancies"]] == ["DURATION_ABNORMAL"]

    def test_duration_exactly_three_times_not_intercepted(self, api, world):
        completed_order(api, world, minutes=180)
        assert _bill(api, world)["intercepted_amount"] == 0

    def _lower_cap(self, cap):
        db = new_session()
        policy = db.execute(select(SubsidyPolicy).where(SubsidyPolicy.disability_level == "MODERATE", SubsidyPolicy.difficulty_type == "NONE")).scalar()
        policy.monthly_cap = cap
        db.commit()
        db.close()

    def test_over_cap_intercepted_and_clipped(self, api, world, db):
        self._lower_cap(3000)  # 每单补贴 2400 元分，第二单越线
        first = completed_order(api, world)
        second = completed_order(api, world, start=TOMORROW_14 + timedelta(days=2))
        bill = _bill(api, world)
        detail = api.ok("GET", f"/bills/{bill['id']}", "comm_a")
        over = next(d for d in detail["discrepancies"] if d["kind"] == "OVER_CAP")
        assert over["amount"] == 1800
        api.ok("POST", f"/discrepancies/{over['id']}/resolve", "comm_a", json={"resolution": "CLIP", "explanation": "按上限补贴"})
        db.rollback()
        fee = _fee_of(db, second["id"])
        assert fee.gov_amount == 600 and fee.self_amount == 8000 - 600 - fee.ltci_amount
        assert not fee.intercepted
        refreshed = api.ok("GET", f"/bills/{bill['id']}", "comm_a")
        assert refreshed["gov_amount"] == 3000
        assert first["id"]

    def test_ratio_over_1_percent_needs_review(self, api, world):
        order = completed_order(api, world)
        api.ok("POST", "/fees/manual", "comm_a", json={"order_id": order["id"], "amount": 8000, "note": "重复"})
        bill = _bill(api, world)
        assert bill["needs_review"] and bill["status"] == "NEEDS_REVIEW" and bill["discrepancy_ratio"] > 0.01

    def test_ratio_under_1_percent_no_review(self, api, world, db):
        for index in range(12):
            completed_order(api, world, start=TOMORROW_14 + timedelta(days=index))
        db_session = new_session()
        order = db_session.get(ServiceOrder, 1)
        db_session.close()
        api.ok("POST", "/fees/manual", "comm_a", json={"order_id": order.id, "amount": 100, "note": "小额调整"})
        bill = _bill(api, world)
        assert not bill["needs_review"] and 0 < bill["discrepancy_ratio"] <= 0.01

    def test_regeneration_idempotent_and_keeps_resolved(self, api, world):
        order = completed_order(api, world)
        api.ok("POST", "/fees/manual", "comm_a", json={"order_id": order["id"], "amount": 8000, "note": "重复"})
        bill = _bill(api, world)
        detail = api.ok("GET", f"/bills/{bill['id']}", "comm_a")
        api.ok("POST", f"/discrepancies/{detail['discrepancies'][0]['id']}/resolve", "comm_a", json={"resolution": "EXCLUDE", "explanation": "确属重复"})
        again = _bill(api, world)
        assert again["id"] == bill["id"] and again["total_amount"] == 8000
        assert len(api.ok("GET", f"/bills/{bill['id']}", "comm_a")["discrepancies"]) == 1

    def test_monthly_job_generates_previous_month(self, api, world, db):
        completed_order(api, world)
        clock.freeze(datetime(2026, 7, 1, 0, 30))
        assert run_job(JOB_INDEX["monthly_billing"]).processed == 2
        db.rollback()
        months = {b.month for b in db.execute(select(Bill)).scalars()}
        assert months == {"2026-06"}
        assert run_job(JOB_INDEX["monthly_billing"]).processed == 0

    def test_generate_scope(self, api, world):
        assert api.post("/bills/generate", "comm_b", json={"community_id": world.community_a, "month": "2026-06"}).status_code == 404

    def test_export_csv_and_xlsx_audited(self, api, world):
        completed_order(api, world)
        bill = _bill(api, world)
        csv_response = api.post(f"/bills/{bill['id']}/export?fmt=csv", "comm_a")
        assert csv_response.status_code == 200 and "text/csv" in csv_response.headers["content-type"]
        assert "助浴" in csv_response.content.decode("utf-8-sig")
        xlsx_response = api.post(f"/bills/{bill['id']}/export?fmt=xlsx", "comm_a")
        assert xlsx_response.content[:2] == b"PK"
        assert api.ok("GET", "/audit/logs?action=EXPORT", "street")["total"] == 2


class TestDiscrepancyHandling:
    def _bill_with_duplicate(self, api, world):
        order = completed_order(api, world)
        fee = api.ok("POST", "/fees/manual", "comm_a", json={"order_id": order["id"], "amount": 8000, "note": "重复"})
        bill = _bill(api, world)
        detail = api.ok("GET", f"/bills/{bill['id']}", "comm_a")
        return bill, detail["discrepancies"][0], fee

    def test_explanation_required(self, api, world):
        _, item, _ = self._bill_with_duplicate(api, world)
        assert api.post(f"/discrepancies/{item['id']}/resolve", "comm_a", json={"resolution": "EXCLUDE", "explanation": ""}).status_code == 422

    def test_invalid_resolution_for_kind(self, api, world):
        order = dispatched_order(api, world)
        api.ok("POST", "/fees/manual", "comm_a", json={"order_id": order["id"], "amount": 5000, "note": "x"})
        bill = _bill(api, world)
        item = api.ok("GET", f"/bills/{bill['id']}", "comm_a")["discrepancies"][0]
        assert api.post(f"/discrepancies/{item['id']}/resolve", "comm_a", json={"resolution": "APPROVE", "explanation": "x"}).status_code == 422

    def test_exclude_removes_from_totals(self, api, world):
        bill, item, _ = self._bill_with_duplicate(api, world)
        api.ok("POST", f"/discrepancies/{item['id']}/resolve", "comm_a", json={"resolution": "EXCLUDE", "explanation": "重复"})
        detail = api.ok("GET", f"/bills/{bill['id']}", "comm_a")
        assert detail["total_amount"] == 8000 and detail["intercepted_amount"] == 0

    def test_approve_adds_back_to_totals(self, api, world):
        bill, item, _ = self._bill_with_duplicate(api, world)
        api.ok("POST", f"/discrepancies/{item['id']}/resolve", "comm_a", json={"resolution": "APPROVE", "explanation": "确有两次服务"})
        assert api.ok("GET", f"/bills/{bill['id']}", "comm_a")["total_amount"] == 16000

    def test_cannot_resolve_twice(self, api, world):
        _, item, _ = self._bill_with_duplicate(api, world)
        body = {"resolution": "EXCLUDE", "explanation": "重复"}
        api.ok("POST", f"/discrepancies/{item['id']}/resolve", "comm_a", json=body)
        assert api.post(f"/discrepancies/{item['id']}/resolve", "comm_a", json=body).status_code == 409

    def test_handler_recorded(self, api, world):
        bill, item, _ = self._bill_with_duplicate(api, world)
        api.ok("POST", f"/discrepancies/{item['id']}/resolve", "grid_a1", json={"resolution": "EXCLUDE", "explanation": "重复"})
        resolved = api.ok("GET", f"/bills/{bill['id']}", "comm_a")["discrepancies"][0]
        assert resolved["handled_by"] == "grid_a1" and resolved["explanation"] == "重复"

    def test_open_discrepancy_blocks_submit(self, api, world):
        bill, _, _ = self._bill_with_duplicate(api, world)
        assert api.post(f"/bills/{bill['id']}/grid-confirm", "grid_a1").status_code == 409

    def test_review_required_before_submit_when_ratio_high(self, api, world):
        bill, item, _ = self._bill_with_duplicate(api, world)
        assert api.post(f"/bills/{bill['id']}/review", "comm_a", json={"note": "已核"}).status_code == 409  # 差异未处理
        api.ok("POST", f"/discrepancies/{item['id']}/resolve", "comm_a", json={"resolution": "EXCLUDE", "explanation": "重复"})
        assert api.post(f"/bills/{bill['id']}/grid-confirm", "grid_a1").status_code == 409  # 未复核
        reviewed = api.ok("POST", f"/bills/{bill['id']}/review", "comm_a", json={"note": "差异已逐条核实"})
        assert reviewed["status"] == "DRAFT"
        assert api.ok("POST", f"/bills/{bill['id']}/grid-confirm", "grid_a1")["status"] == "GRID_CONFIRMED"

    def test_frozen_after_confirmation(self, api, world):
        bill, item, _ = self._bill_with_duplicate(api, world)
        api.ok("POST", f"/discrepancies/{item['id']}/resolve", "comm_a", json={"resolution": "EXCLUDE", "explanation": "重复"})
        api.ok("POST", f"/bills/{bill['id']}/review", "comm_a", json={"note": "ok"})
        api.ok("POST", f"/bills/{bill['id']}/grid-confirm", "grid_a1")
        assert api.post("/bills/generate", "comm_a", json={"community_id": world.community_a, "month": "2026-06"}).status_code == 409


class TestBillApproval:
    def _confirmed(self, api, world, orders=12):
        for index in range(orders):
            completed_order(api, world, start=TOMORROW_14 + timedelta(days=index))
        bill = _bill(api, world)
        api.ok("POST", f"/bills/{bill['id']}/grid-confirm", "grid_a1")
        api.ok("POST", f"/bills/{bill['id']}/org-confirm", "org_x")
        return bill

    def test_org_of_other_bill_cannot_confirm(self, api, world):
        completed_order(api, world)
        bill = _bill(api, world)
        api.ok("POST", f"/bills/{bill['id']}/grid-confirm", "grid_a1")
        assert api.post(f"/bills/{bill['id']}/org-confirm", "org_y").status_code == 404

    def test_all_involved_orgs_must_confirm(self, api, world):
        completed_order(api, world)
        completed_order(api, world, cg_user="cg_far", caregiver=world.c_far, start=TOMORROW_14 + timedelta(days=1))
        bill = _bill(api, world)
        api.ok("POST", f"/bills/{bill['id']}/grid-confirm", "grid_a1")
        assert api.ok("POST", f"/bills/{bill['id']}/org-confirm", "org_x")["status"] == "GRID_CONFIRMED"
        assert api.ok("POST", f"/bills/{bill['id']}/org-confirm", "org_y")["status"] == "ORG_CONFIRMED"

    def test_sample_at_least_10_percent_with_seed(self, api, world):
        bill = self._confirmed(api, world)
        detail = api.ok("GET", f"/bills/{bill['id']}", "street")
        assert len(detail["samples"]) == 2  # ceil(12 × 10%)
        assert detail["sample_seed"] is not None
        assert detail["samples"][0]["evidence"]["checkin_at"]

    def test_all_pass_makes_effective_with_statements(self, api, world, db):
        bill = self._confirmed(api, world)
        for sample in api.ok("GET", f"/bills/{bill['id']}", "street")["samples"]:
            result = api.ok("POST", f"/samples/{sample['id']}/check", "street", json={"passed": True})
        assert result["status"] == "EFFECTIVE"
        db.rollback()
        statement = db.execute(select(ElderStatement)).scalars().one()
        assert statement.self_amount == 3200 * 12 and statement.payment_status == "UNPAID"

    def test_failed_sample_returns_bill_and_intercepts(self, api, world):
        bill = self._confirmed(api, world)
        samples = api.ok("GET", f"/bills/{bill['id']}", "street")["samples"]
        api.ok("POST", f"/samples/{samples[0]['id']}/check", "street", json={"passed": True})
        assert api.post(f"/samples/{samples[1]['id']}/check", "street", json={"passed": False}).status_code == 422
        result = api.ok("POST", f"/samples/{samples[1]['id']}/check", "street", json={"passed": False, "note": "照片与清单不符"})
        assert result["status"] == "DRAFT"
        detail = api.ok("GET", f"/bills/{bill['id']}", "comm_a")
        assert detail["discrepancies"][-1]["kind"] == "SAMPLE_FAILED"
        assert detail["samples"] == []
        assert detail["total_amount"] == 8000 * 11

    def test_payment_writes_back_to_orders(self, api, world):
        bill = self._confirmed(api, world, orders=1)
        sample = api.ok("GET", f"/bills/{bill['id']}", "street")["samples"][0]
        api.ok("POST", f"/samples/{sample['id']}/check", "street", json={"passed": True})
        statement = api.ok("GET", "/statements", "family_a1")[0]
        assert statement["payment_status"] == "UNPAID"
        api.ok("POST", f"/statements/{statement['id']}/pay", "comm_a", json={"method": "微信"})
        assert api.ok("GET", "/statements", "family_a1")[0]["payment_status"] == "PAID"
        order = api.ok("GET", "/orders", "family_a1")["items"][0]
        assert order["payment_status"] == "PAID"
        assert api.post(f"/statements/{statement['id']}/pay", "comm_a", json={"method": "微信"}).status_code == 409

    def test_refund_goes_to_current_month_bill(self, api, world, db):
        bill = self._confirmed(api, world, orders=1)
        sample = api.ok("GET", f"/bills/{bill['id']}", "street")["samples"][0]
        api.ok("POST", f"/samples/{sample['id']}/check", "street", json={"passed": True})
        clock.freeze(datetime(2026, 7, 5, 9, 0))
        order_id = api.ok("GET", "/orders", "family_a1")["items"][0]["id"]
        complaint = api.ok("POST", "/complaints", "family_a1", json={"order_id": order_id, "content": "x"})
        api.ok("POST", f"/complaints/{complaint['id']}/assign", "comm_a")
        api.ok("POST", f"/complaints/{complaint['id']}/explain", "org_x", json={"explanation": "y"})
        api.ok("POST", f"/complaints/{complaint['id']}/conclude", "comm_a", json={"conclusion": "UPHELD", "basis": "z", "actions": ["REFUND"]})
        db.rollback()
        refund = _fee_of(db, order_id, "REFUND")
        assert refund.month == "2026-07"
        july = _bill(api, world, month="2026-07")
        assert july["total_amount"] == -8000
        assert api.ok("GET", f"/bills/{bill['id']}", "comm_a")["total_amount"] == 8000

    def test_policy_config(self, api):
        policy = api.ok("GET", "/subsidy-policies", "admin")[0]
        api.ok("PATCH", f"/subsidy-policies/{policy['id']}", "admin", json={"gov_ratio": 0.2, "ltci_ratio": 0.1, "monthly_cap": 5000})
        assert api.ok("GET", "/audit/logs?action=POLICY_UPDATE", "admin")["total"] == 1
        assert api.call("PATCH", f"/subsidy-policies/{policy['id']}", "comm_a", json={"gov_ratio": 0.9, "ltci_ratio": 0, "monthly_cap": 1}).status_code == 403

