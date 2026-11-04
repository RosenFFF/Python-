"""纯算法单元测试：派单评分、预警判定、计费、统计、状态机、NLU、脱敏。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.core import crypto
from app.core.errors import InvalidTransition
from app.core.exporter import safe_cell, to_csv
from app.domain import billing_calc as calc
from app.domain import stats_calc as sc
from app.domain.alert_rules import RuleSpec, evaluate_baseline, evaluate_consecutive, evaluate_no_data, evaluate_threshold
from app.domain.dispatch_scoring import (
    CandidateInput,
    ScoringParams,
    exclusion_reason,
    normalize_weights,
    rank_candidates,
    score_candidate,
    smoothed_rating,
)
from app.domain.enums import OrderStatus as O
from app.domain.geo import haversine_m
from app.domain.intake_nlu import cn_to_int, parse_intake
from app.domain.state_machine import ALERT_FLOW, BILL_FLOW, COMPLAINT_FLOW, ORDER_FLOW

REQUIRED = frozenset({"助浴", "失能照护"})


def cand(cid=1, skills=("助浴", "失能照护"), km=1.0, daily=0, rsum=0.0, rcount=0, **kw) -> CandidateInput:
    return CandidateInput(cid, f"护理员{cid}", frozenset(skills), km, daily, rsum, rcount, **kw)


class TestDispatchScoring:
    def test_default_weights_are_spec_values(self):
        params = ScoringParams()
        assert params.weights == {"skill": 0.4, "distance": 0.3, "load": 0.2, "rating": 0.1}

    def test_score_is_weighted_sum_by_hand(self):
        # 技能 1.0、距离 1-1.5/3=0.5、负荷 1-3/6=0.5、评分：(4*5+25*5)... 见下
        c = cand(km=1.5, daily=3, rsum=50.0, rcount=10)
        rating = (smoothed_rating(50.0, 10, ScoringParams()) - 1) / 4
        expected = 0.4 * 1 + 0.3 * 0.5 + 0.2 * 0.5 + 0.1 * rating
        assert score_candidate(c, REQUIRED, ScoringParams())["score"] == pytest.approx(expected, abs=1e-3)

    def test_contributions_sum_to_raw_score(self):
        scored = score_candidate(cand(km=2.0, daily=2, rsum=40, rcount=9), REQUIRED, ScoringParams())
        total = sum(f["contribution"] for f in scored["factors"].values())
        assert total == pytest.approx(scored["raw_score"], abs=1e-3)

    def test_partial_skill_scored_proportionally(self):
        scored = score_candidate(cand(skills=("助浴",)), REQUIRED, ScoringParams())
        assert scored["factors"]["skill"]["normalized"] == 0.5
        assert scored["factors"]["skill"]["missing"] == ["失能照护"]

    def test_no_required_skill_is_full_match(self):
        assert score_candidate(cand(skills=()), frozenset(), ScoringParams())["factors"]["skill"]["normalized"] == 1.0

    def test_bayesian_smoothing_keeps_newcomer_below_veteran(self):
        params = ScoringParams()
        assert smoothed_rating(5.0, 1, params) < smoothed_rating(4.8 * 200, 200, params)

    def test_no_reviews_falls_back_to_prior(self):
        assert smoothed_rating(0, 0, ScoringParams()) == 4.0

    def test_key_attention_lowers_score(self):
        normal = score_candidate(cand(), REQUIRED, ScoringParams())
        flagged = score_candidate(cand(key_attention=True), REQUIRED, ScoringParams())
        assert flagged["score"] == pytest.approx(normal["score"] * 0.8, abs=1e-3)
        assert flagged["penalty_factor"] == 0.8

    @pytest.mark.parametrize("kwargs,fragment", [
        ({"has_conflict": True}, "在途工单"), ({"km": 3.01}, "服务半径"), ({"daily": 6}, "上限"),
        ({"skills": ("助餐",)}, "技能匹配不足"),
    ])
    def test_hard_filters(self, kwargs, fragment):
        assert fragment in exclusion_reason(cand(**kwargs), REQUIRED, ScoringParams())

    def test_boundary_values_pass_hard_filters(self):
        assert exclusion_reason(cand(km=3.0, daily=5), REQUIRED, ScoringParams()) is None

    def test_half_skill_match_passes_minimum(self):
        assert exclusion_reason(cand(skills=("助浴",)), REQUIRED, ScoringParams()) is None

    def test_rank_order_and_deterministic_tiebreak(self):
        ranked, excluded = rank_candidates(
            [cand(3, km=1.0), cand(1, km=1.0), cand(2, km=0.2), cand(4, km=9)], REQUIRED, ScoringParams())
        assert [r["caregiver_id"] for r in ranked] == [2, 1, 3]
        assert [r["rank"] for r in ranked] == [1, 2, 3]
        assert excluded[0]["caregiver_id"] == 4

    def test_weights_configuration_changes_ranking(self):
        close, skilled = cand(1, skills=("助浴",), km=1.0), cand(2, km=2.5)
        default_rank, _ = rank_candidates([close, skilled], REQUIRED, ScoringParams())
        distance_first = ScoringParams(weights={"skill": 0.1, "distance": 0.8, "load": 0.05, "rating": 0.05})
        changed, _ = rank_candidates([close, skilled], REQUIRED, distance_first)
        assert default_rank[0]["caregiver_id"] == 2
        assert changed[0]["caregiver_id"] == 1

    def test_weights_are_normalized(self):
        assert normalize_weights({"skill": 4, "distance": 3, "load": 2, "rating": 1})["skill"] == pytest.approx(0.4)

    @pytest.mark.parametrize("weights", [
        {"skill": 1}, {"skill": -1, "distance": 1, "load": 1, "rating": 1}, {"skill": 0, "distance": 0, "load": 0, "rating": 0},
    ])
    def test_invalid_weights_rejected(self, weights):
        with pytest.raises(ValueError):
            normalize_weights(weights)


class TestAlertRules:
    @pytest.mark.parametrize("value,expected", [(180, True), (179.9, False), (181, True)])
    def test_threshold_ge_boundary(self, value, expected):
        spec = RuleSpec("THRESHOLD", "SYSTOLIC", ">=", 180)
        assert evaluate_threshold(spec, value).triggered is expected

    @pytest.mark.parametrize("value,expected", [(90, True), (91, False)])
    def test_threshold_le_boundary(self, value, expected):
        assert evaluate_threshold(RuleSpec("THRESHOLD", "SYSTOLIC", "<=", 90), value).triggered is expected

    def test_consecutive_needs_full_window(self):
        spec = RuleSpec("CONSECUTIVE", "HEART_RATE", ">", 100, window=3)
        assert not evaluate_consecutive(spec, [120, 130]).triggered

    def test_consecutive_all_over(self):
        spec = RuleSpec("CONSECUTIVE", "HEART_RATE", ">", 100, window=3)
        assert evaluate_consecutive(spec, [105, 110, 101]).triggered

    def test_consecutive_broken_by_normal_value(self):
        spec = RuleSpec("CONSECUTIVE", "HEART_RATE", ">", 100, window=3)
        assert not evaluate_consecutive(spec, [105, 100, 110]).triggered

    def test_consecutive_is_strict_on_boundary(self):
        spec = RuleSpec("CONSECUTIVE", "HEART_RATE", ">", 100, window=3)
        assert not evaluate_consecutive(spec, [100, 100, 100]).triggered

    def test_no_data_boundary(self):
        spec = RuleSpec("NO_DATA", hours=48)
        now = datetime(2026, 6, 15, 12)
        assert evaluate_no_data(spec, now - timedelta(hours=48), now, now).triggered
        assert not evaluate_no_data(spec, now - timedelta(hours=47, minutes=59), now, now).triggered

    def test_no_data_counts_from_creation_when_never_reported(self):
        spec = RuleSpec("NO_DATA", hours=48)
        now = datetime(2026, 6, 15, 12)
        assert evaluate_no_data(spec, None, now - timedelta(hours=49), now).triggered
        assert not evaluate_no_data(spec, None, now - timedelta(hours=1), now).triggered

    def test_baseline_detects_personal_deviation(self):
        spec = RuleSpec("BASELINE", "HEART_RATE", threshold=3.5, window=10)
        history = [60, 62, 61, 59, 60, 63, 61, 60, 62, 61]
        assert evaluate_baseline(spec, 95, history).triggered
        assert not evaluate_baseline(spec, 63, history).triggered

    def test_baseline_needs_enough_samples(self):
        spec = RuleSpec("BASELINE", "HEART_RATE", threshold=3.5, window=10)
        assert not evaluate_baseline(spec, 200, [60] * 9).triggered

    def test_baseline_robust_to_zero_mad(self):
        spec = RuleSpec("BASELINE", "HEART_RATE", threshold=3.5, window=10)
        assert not evaluate_baseline(spec, 62, [60] * 12).triggered
        assert evaluate_baseline(spec, 70, [60] * 12).triggered


class TestBilling:
    def test_three_way_split_conserves_cents(self):
        for amount in (1, 3, 7, 999, 8001, 12345):
            split = calc.split_amount(amount, True, 0.33, 0.5, 0.31, True)
            assert split.gov + split.ltci + split.self_pay == amount
            assert split.self_pay >= 0

    def test_gov_ratio_takes_lower_of_item_and_policy(self):
        split = calc.split_amount(10000, True, 0.5, 0.3, 0.0, False)
        assert split.gov == 3000 and split.self_pay == 7000

    def test_item_not_eligible_means_no_subsidy(self):
        split = calc.split_amount(10000, False, 0.5, 0.5, 0.3, True)
        assert (split.gov, split.ltci, split.self_pay) == (0, 0, 10000)

    def test_ltci_requires_insurance(self):
        assert calc.split_amount(10000, True, 0.3, 0.3, 0.3, False).ltci == 0
        assert calc.split_amount(10000, True, 0.3, 0.3, 0.3, True).ltci == 3000

    def test_ratios_never_exceed_total(self):
        split = calc.split_amount(10000, True, 0.8, 0.8, 0.5, True)
        assert split.gov == 8000 and split.ltci == 2000 and split.self_pay == 0

    def test_half_up_rounding(self):
        assert calc.round_cents(calc.Decimal("2.5")) == 3
        assert calc.round_cents(calc.Decimal("3.5")) == 4

    @pytest.mark.parametrize("minutes,hours", [(1, "0.25"), (15, "0.25"), (16, "0.5"), (60, "1"), (61, "1.25"), (0, "0.25")])
    def test_billable_hours_round_up_to_quarter(self, minutes, hours):
        assert calc.billable_hours(minutes) == calc.Decimal(hours)

    def test_per_time_ignores_duration(self):
        assert calc.service_amount("PER_TIME", 8000, 500) == (calc.Decimal(1), 8000)

    def test_per_hour_uses_duration(self):
        assert calc.service_amount("PER_HOUR", 4000, 90)[1] == 6000

    def test_clip_to_cap_moves_excess_to_self(self):
        split = calc.split_amount(10000, True, 0.5, 0.5, 0, False)
        clipped = calc.clip_to_cap(split, used_before=38000, monthly_cap=40000)
        assert clipped.gov == 2000 and clipped.self_pay == 8000
        assert clipped.gov + clipped.ltci + clipped.self_pay == clipped.amount

    def test_clip_when_cap_already_used(self):
        split = calc.split_amount(10000, True, 0.5, 0.5, 0, False)
        assert calc.clip_to_cap(split, used_before=50000, monthly_cap=40000).gov == 0

    def _fee(self, fee_id, start, minutes=60, amount=100, fee_type="SERVICE", elder=1, status="COMPLETED", actual=60, std=60):
        return calc.FeeView(fee_id, elder, fee_id, fee_type, start, start + timedelta(minutes=minutes), amount, 10, status, actual, std)

    def test_overlap_is_duplicate(self):
        t = datetime(2026, 6, 1, 9)
        pairs = calc.find_duplicates([self._fee(1, t), self._fee(2, t + timedelta(minutes=30))])
        assert [(a.fee_id, b.fee_id) for a, b in pairs] == [(1, 2)]

    def test_adjacent_time_is_not_duplicate(self):
        t = datetime(2026, 6, 1, 9)
        assert not calc.find_duplicates([self._fee(1, t), self._fee(2, t + timedelta(minutes=60))])

    def test_other_elder_is_not_duplicate(self):
        t = datetime(2026, 6, 1, 9)
        assert not calc.find_duplicates([self._fee(1, t), self._fee(2, t, elder=2)])

    def test_refund_and_cancel_fee_not_treated_as_duplicate(self):
        t = datetime(2026, 6, 1, 9)
        assert not calc.find_duplicates([self._fee(1, t), self._fee(2, t, amount=-100, fee_type="REFUND")])
        assert not calc.find_duplicates([self._fee(1, t), self._fee(2, t, fee_type="CANCEL_FEE")])

    def test_cancel_fee_on_cancelled_order_is_legit(self):
        t = datetime(2026, 6, 1, 9)
        assert not calc.is_not_completed(self._fee(1, t, fee_type="CANCEL_FEE", status="CANCELLED"))
        assert calc.is_not_completed(self._fee(1, t, status="CANCELLED"))
        assert calc.is_not_completed(self._fee(1, t, fee_type="MANUAL", status="DISPATCHED"))

    def test_duration_abnormal_uses_strict_multiple(self):
        t = datetime(2026, 6, 1, 9)
        assert not calc.is_duration_abnormal(self._fee(1, t, actual=180, std=60), 3)
        assert calc.is_duration_abnormal(self._fee(1, t, actual=181, std=60), 3)

    @pytest.mark.parametrize("population,size", [(0, 0), (1, 1), (9, 1), (10, 1), (11, 2), (100, 10), (101, 11)])
    def test_sample_size_never_below_ratio(self, population, size):
        assert calc.sample_size(population, 0.10) == size
        if population:
            assert calc.sample_size(population, 0.10) / population >= 0.10


class TestStats:
    def test_growth(self):
        assert sc.growth(120, 100) == 0.2
        assert sc.growth(80, 100) == -0.2

    @pytest.mark.parametrize("base", [0, None])
    def test_growth_without_base_is_none(self, base):
        assert sc.growth(10, base) is None

    def test_full_month_previous_is_previous_full_month(self):
        period = sc.Period(date(2026, 3, 1), date(2026, 3, 31))
        assert period.previous() == sc.Period(date(2026, 2, 1), date(2026, 2, 28))

    def test_partial_range_previous_is_same_length(self):
        period = sc.Period(date(2026, 6, 11), date(2026, 6, 20))
        assert period.previous() == sc.Period(date(2026, 6, 1), date(2026, 6, 10))

    def test_quarter_previous(self):
        period = sc.Period(date(2026, 4, 1), date(2026, 6, 30))
        assert period.previous() == sc.Period(date(2026, 1, 1), date(2026, 3, 31))

    def test_year_ago_handles_leap_day(self):
        assert sc.Period(date(2028, 2, 29), date(2028, 2, 29)).year_ago().start == date(2027, 2, 28)

    def test_add_months_clamps_month_end(self):
        assert sc.add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
        assert sc.add_months(date(2026, 1, 15), -1) == date(2025, 12, 15)
        assert sc.add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)

    def test_week_bucket_starts_on_monday(self):
        assert sc.bucket_key(date(2026, 6, 14), "week") == "2026-06-08"
        assert sc.bucket_key(date(2026, 6, 15), "week") == "2026-06-15"

    def test_bucket_range_keeps_empty_buckets(self):
        assert sc.bucket_range(date(2026, 1, 30), date(2026, 3, 1), "month") == ["2026-01", "2026-02", "2026-03"]
        assert len(sc.bucket_range(date(2026, 6, 1), date(2026, 6, 7), "day")) == 7

    def test_median_even_and_odd(self):
        assert sc.median_or_none([1, 3, 2]) == 2
        assert sc.median_or_none([1, 2, 3, 4]) == 2.5
        assert sc.median_or_none([]) is None

    def test_percentile_interpolates(self):
        assert sc.percentile([1, 2, 3, 4, 5], 0.5) == 3
        assert sc.percentile([1, 2], 0.9) == 1.9
        assert sc.percentile([], 0.5) is None

    def test_histogram_includes_top_edge(self):
        rows = sc.histogram([1, 2, 3, 5, 4.5], [1, 3, 5], ["低", "高"])
        assert rows == [{"name": "低", "value": 2}, {"name": "高", "value": 3}]

    def test_with_share_sums_to_one(self):
        rows = sc.with_share([{"name": "a", "value": 1}, {"name": "b", "value": 3}])
        assert [r["share"] for r in rows] == [0.25, 0.75]

    def test_safe_ratio_zero_denominator(self):
        assert sc.safe_ratio(1, 0) is None


class TestStateMachines:
    @pytest.mark.parametrize("current,target", [
        (O.PENDING_ACCEPT, O.PENDING_DISPATCH), (O.PENDING_ACCEPT, O.DISPATCHED), (O.PENDING_ACCEPT, O.COMPLETED),
        (O.ACCEPTED, O.DISPATCHED), (O.PENDING_DISPATCH, O.IN_SERVICE), (O.DISPATCHED, O.COMPLETED),
        (O.COMPLETED, O.CANCELLED), (O.CANCELLED, O.PENDING_ACCEPT), (O.NO_SHOW, O.COMPLETED), (O.IN_SERVICE, O.PENDING_DISPATCH),
    ])
    def test_skipping_or_reviving_is_illegal(self, current, target):
        with pytest.raises(InvalidTransition):
            ORDER_FLOW.check(current, target)

    def test_happy_path_is_legal(self):
        path = [O.PENDING_ACCEPT, O.ACCEPTED, O.PENDING_DISPATCH, O.DISPATCHED, O.IN_SERVICE, O.COMPLETED]
        for current, target in zip(path, path[1:]):
            ORDER_FLOW.check(current, target)

    @pytest.mark.parametrize("state", [O.COMPLETED, O.CANCELLED, O.NO_SHOW])
    def test_terminal_states(self, state):
        assert ORDER_FLOW.is_terminal(state)

    def test_other_machines_block_skips(self):
        with pytest.raises(InvalidTransition):
            COMPLAINT_FLOW.check("OPEN", "CONCLUDED")
        with pytest.raises(InvalidTransition):
            BILL_FLOW.check("DRAFT", "EFFECTIVE")
        with pytest.raises(InvalidTransition):
            ALERT_FLOW.check("CLOSED", "OPEN")


class TestGeo:
    def test_same_point_is_zero(self):
        assert haversine_m(30, 104, 30, 104) == 0

    def test_one_km_of_latitude(self):
        assert haversine_m(30, 104, 30.009, 104) == pytest.approx(1000.75, abs=2)


class TestIntakeNlu:
    NOW = datetime(2026, 11, 3, 10, 0)  # 周二

    @pytest.mark.parametrize("text,value", [("十", 10), ("十二", 12), ("二十", 20), ("三十五", 35), ("两", 2), ("14", 14)])
    def test_numbers(self, text, value):
        assert cn_to_int(text) == value

    def test_spec_sentence(self):
        parsed = parse_intake("帮我约明天下午两点助浴", self.NOW)
        assert parsed.item_name == "助浴"
        assert parsed.scheduled_at == datetime(2026, 11, 4, 14, 0)
        assert not parsed.missing

    @pytest.mark.parametrize("text,expected", [
        ("后天上午九点半打扫卫生", datetime(2026, 11, 5, 9, 30)),
        ("今天晚上七点送饭", datetime(2026, 11, 3, 19, 0)),
        ("明天下午3点15分陪我去医院看病", datetime(2026, 11, 4, 15, 15)),
        ("下周一上午十点买菜", datetime(2026, 11, 9, 10, 0)),
        ("11月20号下午两点助浴", datetime(2026, 11, 20, 14, 0)),
    ])
    def test_time_expressions(self, text, expected):
        assert parse_intake(text, self.NOW).scheduled_at == expected

    def test_weekly_pattern(self):
        parsed = parse_intake("每周三上午九点打扫卫生", self.NOW)
        assert parsed.weekly and parsed.weekday == 2 and parsed.item_name == "助洁"

    def test_reports_missing_parts(self):
        assert set(parse_intake("我想洗澡", self.NOW).missing) == {"日期", "时间"}
        assert "服务项目" in parse_intake("明天下午两点", self.NOW).missing

    def test_past_time_warns(self):
        assert parse_intake("今天早上八点助浴", self.NOW).hints


class TestCryptoAndMasking:
    def test_round_trip_and_randomized(self):
        a, b = crypto.encrypt("13800001234", "p"), crypto.encrypt("13800001234", "p")
        assert a != b and "13800001234" not in a
        assert crypto.decrypt(a, "p") == "13800001234"

    def test_ciphertext_bound_to_purpose(self):
        token = crypto.encrypt("secret", "elder.phone")
        with pytest.raises(Exception):
            crypto.decrypt(token, "elder.id_card")

    def test_tampered_ciphertext_rejected(self):
        token = crypto.encrypt("secret", "p")
        flipped = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
        with pytest.raises(Exception):
            crypto.decrypt(flipped, "p")

    def test_blind_index_is_stable_and_not_plain(self):
        assert crypto.blind_index("abc") == crypto.blind_index(" ABC ")
        assert crypto.blind_index("abc") != "abc"

    def test_masks(self):
        assert crypto.mask_phone("13812345678") == "138****5678"
        assert crypto.mask_id_card("510104194803160021") == "510***********0021"
        assert "502" not in crypto.mask_address("青松街道青松路18号3栋502室")
        assert crypto.mask_name("王秀英") == "王**"
        assert crypto.mask_phone(None) == ""

    def test_id_card_checksum(self):
        from tests.factory import valid_id_card
        good = valid_id_card(1)
        assert crypto.valid_id_card(good)
        assert not crypto.valid_id_card(good[:-1] + ("0" if good[-1] != "0" else "1"))
        assert not crypto.valid_id_card("123")


class TestExporter:
    @pytest.mark.parametrize("value", ["=1+1", "+cmd", "-1+2", "@SUM(A1)"])
    def test_formula_injection_neutralized(self, value):
        assert safe_cell(value).startswith("'")

    def test_numbers_untouched(self):
        assert safe_cell(-100) == -100

    def test_csv_has_bom_for_excel(self):
        assert to_csv(["名称"], [["助浴"]]).startswith(b"\xef\xbb\xbf")
