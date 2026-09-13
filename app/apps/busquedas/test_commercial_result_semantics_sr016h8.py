"""SR0.16H.8 presentation semantics tests."""

from types import SimpleNamespace
from unittest import TestCase

from .search_budget_ux import run_customer_context


def run_with_quality(*, actionable=0, review=0, near=0, raw=0, empty="PARTIAL_EMPTY_RESULT"):
    return SimpleNamespace(
        raw_response={
            "quality_semantics": {
                "actionable_count": actionable,
                "review_required_count": review,
                "unavailable_count": 0,
                "raw_candidates": raw,
                "commercial_yield_pct": 0.0,
            },
            "recovery_observability": {"near_match_candidate_count": near},
            "coverage_contract": {"empty_result_classification": empty},
        },
        total_valid_candidates=actionable,
        coverage_status="partial",
        stop_reason="budget_exhausted",
        status="completed_with_errors",
        error_message="",
        search_mode="eco",
        budget_max_credits=1,
        budget_consumed_credits=1,
        cache_hits=0,
        reused_from_search_run_id=None,
        governance_enabled=True,
    )


class CommercialResultSemanticsSR016H8Tests(TestCase):
    def test_review_results_do_not_render_zero_results(self):
        context = run_customer_context(run_with_quality(review=3, raw=16))
        self.assertEqual(context["commercial_result_state"], "REVIEW_RESULTS_PRESENT")
        self.assertIn("3 resultados para revisar", context["result_summary"])
        self.assertNotIn("0 resultados", context["result_summary"])

    def test_actionable_results_use_available_headline(self):
        context = run_customer_context(run_with_quality(actionable=2, review=1, raw=3))
        self.assertEqual(context["commercial_result_state"], "ACTIONABLE_RESULTS_PRESENT")
        self.assertEqual(context["commercial_headline"], "2 oportunidades disponibles")

    def test_near_match_prevents_empty_state(self):
        context = run_customer_context(run_with_quality(near=2, raw=2, empty="FULL_PLAN_EMPTY_RESULT"))
        self.assertEqual(context["commercial_result_state"], "NEAR_MATCH_RESULTS_PRESENT")

    def test_empty_only_when_all_commercial_buckets_are_zero(self):
        context = run_customer_context(run_with_quality(raw=0))
        self.assertEqual(context["commercial_result_state"], "NO_ELIGIBLE_RESULTS")

    def test_unknown_availability_is_review_not_invalid(self):
        context = run_customer_context(run_with_quality(review=3, raw=16))
        self.assertEqual(context["commercial_review_count"], 3)
        self.assertNotIn("inválid", context["result_summary"].lower())
