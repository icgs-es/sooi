"""Focused offline contracts for SR0.15D; no network/provider calls."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase

from .adaptive_planner import (
    AdaptivePlanner,
    actionable_sufficient_coverage,
    coverage_plan,
)
from .commercial_policy import billable_consumption
from .provider_efficiency_planner import (
    MINIMUM_HISTORY_SAMPLES,
    history_sample_from_row,
    provider_efficiency_score,
)
from .provider_query_provenance import query_provenance
from .search_availability_semantics import classify_listing_availability
from .search_quality_semantics import result_actionability
from .services_hybrid_coverage_v261 import (
    SOURCE_SPECS,
    _candidate_constraint_violations,
)
from .services_portal_extractors import extract_habitaclia_candidates


NOW = datetime(2026, 8, 16, tzinfo=timezone.utc)
BASELINE = ["fotocasa", "habitaclia", "idealista", "pisos.com"]
TIERS = {
    "fotocasa": "deterministic", "habitaclia": "deterministic",
    "idealista": "ai_efficient", "pisos.com": "ai_efficient",
}


def samples(provider, actionable, hard_clean, count=5, *, start=0):
    return [
        {
            "provider": provider,
            "run_id": start + index + 1,
            "finished_at": NOW - timedelta(days=index % 3),
            "candidate_count": 10.0,
            "hard_clean_count": float(hard_clean),
            "actionable_count": float(actionable),
            "review_required_count": float(max(0, hard_clean - actionable)),
            "hard_rejected_count": float(10 - hard_clean),
            "failed": False,
        }
        for index in range(count)
    ]


def actionable_rows(count=3):
    return [{
        "source": "fotocasa", "method": "deterministic",
        "provider_efficiency": {
            "actionable_count": count, "hard_clean_count": count,
            "review_required_count": 0,
        },
        "candidates": [
            {
                "url": f"https://example.test/{index}",
                "classification": "verified",
                "availability_evidence": {"state": "confirmed"},
            }
            for index in range(count)
        ],
    }]


class SearchCreditEfficiencyPlannerSR015DTests(TestCase):
    def test_insufficient_history_preserves_current_order(self):
        result = provider_efficiency_score([], baseline_order=BASELINE, provider_tiers=TIERS, now=NOW)
        self.assertEqual(result["scoring_mode"], "insufficient_history")
        self.assertEqual(result["ordered_providers"], BASELINE)
        self.assertTrue(result["fail_open"])

    def test_one_zero_yield_run_does_not_demote(self):
        result = provider_efficiency_score(
            samples("idealista", 0, 0, count=1), baseline_order=BASELINE,
            provider_tiers=TIERS, now=NOW,
        )
        self.assertEqual(result["ordered_providers"], BASELINE)

    def test_minimum_history_gate_required(self):
        result = provider_efficiency_score(
            samples("idealista", 5, 8, count=MINIMUM_HISTORY_SAMPLES - 1),
            baseline_order=BASELINE, provider_tiers=TIERS, now=NOW,
        )
        idealista = next(row for row in result["provider_priorities"] if row["provider"] == "idealista")
        self.assertFalse(idealista["eligible"])

    def test_multi_sample_high_yield_can_prioritize(self):
        history = samples("idealista", 5, 8) + samples("pisos.com", 1, 3, start=20)
        result = provider_efficiency_score(history, baseline_order=BASELINE, provider_tiers=TIERS, now=NOW)
        self.assertEqual(result["scoring_mode"], "historical_soft_priority")
        self.assertLess(result["ordered_providers"].index("idealista"), result["ordered_providers"].index("pisos.com"))

    def test_multi_sample_low_yield_can_deprioritize_without_disable(self):
        history = samples("idealista", 0, 1) + samples("pisos.com", 4, 7, start=20)
        result = provider_efficiency_score(history, baseline_order=BASELINE, provider_tiers=TIERS, now=NOW)
        self.assertLess(result["ordered_providers"].index("pisos.com"), result["ordered_providers"].index("idealista"))
        self.assertEqual(result["provider_disabled"], [])
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, efficiency_decision=result)
        ai = [spec.slug for spec in planner.allowed if spec.method == "openai_web_search"]
        self.assertEqual(ai[:2], ["pisos.com", "idealista"])

    def test_deprioritized_provider_remains_eligible(self):
        history = samples("idealista", 0, 1) + samples("pisos.com", 4, 7, start=20)
        result = provider_efficiency_score(history, baseline_order=BASELINE, provider_tiers=TIERS, now=NOW)
        idealista = next(row for row in result["provider_priorities"] if row["provider"] == "idealista")
        self.assertTrue(idealista["eligible"])
        self.assertEqual(idealista["priority"], "low")

    def test_legacy_run_without_provider_efficiency_is_ignored(self):
        row = {"source": "idealista", "attempted": True, "candidate_count": 20}
        self.assertIsNone(history_sample_from_row(row, run_id=1, finished_at=NOW))

    def test_recency_window_allows_recovery(self):
        stale = samples("idealista", 0, 0)
        for row in stale:
            row["finished_at"] = NOW - timedelta(days=91)
        recent = samples("idealista", 5, 8, start=20) + samples("pisos.com", 1, 2, start=40)
        result = provider_efficiency_score(stale + recent, baseline_order=BASELINE, provider_tiers=TIERS, now=NOW)
        idealista = next(row for row in result["provider_priorities"] if row["provider"] == "idealista")
        self.assertEqual(idealista["samples"], 5)
        self.assertEqual(idealista["priority"], "high")

    def test_deterministic_provider_does_not_consume_ai_credit(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1)
        deterministic = next(spec for spec in planner.allowed if spec.method == "deterministic")
        planner.record_executed(deterministic)
        self.assertEqual(planner.consumed, Decimal("0"))

    def test_ai_call_with_zero_remaining_credit_is_no(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, budget_consumed=1)
        self.assertFalse(planner.ai_call_allowed)
        self.assertFalse(planner.before_external_call())

    def test_ai_call_never_exceeds_authorized_max(self):
        planner = AdaptivePlanner("profunda", SOURCE_SPECS, budget_max=2)
        self.assertEqual(sum(planner.before_external_call() for _ in range(10)), 2)
        self.assertLessEqual(planner.consumed, planner.budget_max)

    def test_ai_call_allowed_is_not_ai_call_needed(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1)
        planner.observe_deterministic_coverage(actionable_rows(3))
        observation = planner.efficiency_observability()
        self.assertTrue(observation["ai_call_allowed"])
        self.assertFalse(observation["ai_call_needed"])

    def test_raw_candidates_do_not_imply_ai_skip(self):
        rows = [{"source": "fotocasa", "candidates": [
            {"url": f"https://example.test/{index}", "classification": "discarded"}
            for index in range(20)
        ]}]
        self.assertFalse(actionable_sufficient_coverage("eco", rows))

    def test_hard_clean_only_does_not_imply_ai_skip(self):
        rows = [{"source": "fotocasa", "candidates": [
            {"url": f"https://example.test/{index}", "classification": "reviewable", "hard_violations": []}
            for index in range(3)
        ]}]
        self.assertFalse(actionable_sufficient_coverage("eco", rows))

    def test_review_required_does_not_imply_ai_skip(self):
        candidate = {
            "classification": "verified", "availability_evidence": {"state": "unknown"},
        }
        rows = [{"source": "fotocasa", "candidates": [
            {**candidate, "url": f"https://example.test/{index}"} for index in range(3)
        ]}]
        self.assertFalse(actionable_sufficient_coverage("eco", rows))

    def test_no_provider_disabled(self):
        history = samples("idealista", 0, 0) + samples("pisos.com", 5, 8, start=20)
        result = provider_efficiency_score(history, baseline_order=BASELINE, provider_tiers=TIERS, now=NOW)
        self.assertEqual(result["provider_disabled"], [])
        self.assertCountEqual(result["ordered_providers"], BASELINE)

    def test_provider_order_fail_open(self):
        decision = provider_efficiency_score([], baseline_order=BASELINE, provider_tiers=TIERS, now=NOW)
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, efficiency_decision=decision)
        self.assertEqual([spec.slug for spec in planner.specs[:3]], ["fotocasa", "habitaclia", "idealista"])

    def test_sr015a_provenance_unchanged(self):
        url = "https://www.habitaclia.com/x?hab=3&pmax=1000"
        self.assertEqual(query_provenance("habitaclia", url, url)["executed"]["safe_query"], {"hab": "3", "pmax": "1000"})

    def test_sr015b_card_precision_unchanged(self):
        html = (
            '<article><a href="/alquiler-piso-a-i111.htm">Piso A</a><span>900 €</span></article>'
            '<article><a href="/alquiler-casa-b-i222.htm">Casa B</a><span>1.400 €</span></article>'
        )
        rows = extract_habitaclia_candidates(html, "https://www.habitaclia.com/alquiler-x.htm")["candidates"]
        self.assertEqual(sorted(row["price"] for row in rows), [900, 1400])

    def test_sr015c_availability_unchanged(self):
        evidence = classify_listing_availability(
            requested_url="https://example.test/listing/12345", status_code=403,
            final_url="https://example.test/listing/12345", html="",
        )
        self.assertEqual(evidence["state"], "unknown")

    def test_sr014e_actionability_unchanged(self):
        unknown = result_actionability({
            "classification": "verified", "availability_evidence": {"state": "unknown"},
        })
        self.assertFalse(unknown["actionable"])
        self.assertTrue(unknown["review_required"])

    def test_hard_filters_unchanged(self):
        ctx = {"max_price": 1000, "bedrooms": 3}
        self.assertIn("price_above_max:1001>1000", _candidate_constraint_violations(ctx, {"price": 1001}))
        self.assertIn("bedrooms_below_min:2<3", _candidate_constraint_violations(ctx, {"bedrooms": 2}))

    def test_budget_calculation_unchanged(self):
        plan = coverage_plan(["A", "B", "C"], 1, maximum_calls=4, planned_calls=3)
        self.assertEqual(plan["credits_authorized"], 1)
        self.assertEqual(plan["authorized_calls"], 1)

    def test_metering_contract_unchanged(self):
        run = SimpleNamespace(budget_consumed_credits=Decimal("1"))
        self.assertEqual(billable_consumption(run), Decimal("1"))
