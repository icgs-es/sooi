"""Focused, offline contracts for SR0.14E."""

from django.test import SimpleTestCase
from unittest.mock import patch

from .search_quality_semantics import evaluate_search_quality_semantics, result_actionability
from .services_hybrid_coverage_v261 import _build_action_plan, _candidate_constraint_violations


CONTEXT = {
    "max_price": 1000,
    "bedrooms": 3,
    "min_bedrooms": 3,
    "property_types": ["flat"],
}


class ActionableResultSemanticsSR014ETests(SimpleTestCase):
    def test_reviewable_unknown_is_clean_review_required_not_actionable(self):
        candidate = {
            "classification": "reviewable", "reason": "availability_unknown:http_403",
            "hard_violations": [],
        }
        result = result_actionability(candidate)
        self.assertTrue(result["hard_clean"])
        self.assertTrue(result["review_required"])
        self.assertFalse(result["actionable"])
        self.assertEqual(result["availability_state"], "UNKNOWN")
        self.assertEqual(candidate["classification"], "reviewable")

    def test_reviewable_unknown_is_in_review_capture_plan_without_actionability(self):
        candidate = {
            "url": "https://example.invalid/held", "classification": "reviewable",
            "reason": "availability_unknown:http_403", "hard_violations": [],
        }
        quality = evaluate_search_quality_semantics({"candidates": [candidate]})
        self.assertEqual(quality["customer_valid_count"], 0)
        with patch("apps.busquedas.services_hybrid_coverage_v261._existing_capture_id_for_url", return_value=None):
            plan = _build_action_plan([{"source": "idealista", "candidates": [candidate]}])
        self.assertEqual(plan["actions"][0]["action"], "would_create_in_review")
        self.assertEqual(plan["actions"][0]["target_status"], "in_review")
        self.assertEqual(plan["totals"].get("would_create_in_review", 0), 1)
        self.assertEqual(plan["totals"].get("would_create_captured", 0), 0)

    def test_verified_clean_result_is_actionable(self):
        result = result_actionability({
            "classification": "verified", "reason": "deterministic_candidate_probe_available",
            "hard_violations": [],
        })
        self.assertTrue(result["actionable"])
        self.assertEqual(result["availability_state"], "CONFIRMED")

    def test_explicit_unavailable_is_not_actionable(self):
        result = result_actionability({
            "classification": "discarded", "reason": "availability_unavailable:http_404",
            "hard_violations": [],
        })
        self.assertFalse(result["actionable"])
        self.assertEqual(result["availability_state"], "UNAVAILABLE")
        self.assertEqual(result["customer_label"], "No disponible")

    def test_hard_filters_unchanged(self):
        self.assertIn("price_above_max:1001>1000", _candidate_constraint_violations(
            CONTEXT, {"price": 1001, "bedrooms": 3, "property_type": "flat"},
        ))
        self.assertIn("bedrooms_below_min:2<3", _candidate_constraint_violations(
            CONTEXT, {"price": 900, "bedrooms": 2, "property_type": "flat"},
        ))

    def test_run213_synthetic_aggregation(self):
        rejected = [{"classification": "discarded", "price": 1001}] * 40
        reviewable = [{
            "classification": "reviewable", "reason": "availability_unknown:http_403",
            "hard_violations": [],
        }] * 2
        quality = evaluate_search_quality_semantics({
            "context": {"max_price": 1000}, "candidates": rejected + reviewable,
        })
        self.assertEqual(quality["raw_candidates"], 42)
        self.assertEqual(quality["hard_clean_count"], 2)
        self.assertEqual(quality["review_required_count"], 2)
        self.assertEqual(quality["actionable_count"], 0)
        self.assertEqual(quality["customer_valid_count"], 0)
        self.assertEqual(quality["commercial_yield_pct"], 0)
