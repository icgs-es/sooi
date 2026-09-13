from django.test import SimpleTestCase

from .adaptive_planner import MARKET_COMPLETENESS_DISCLAIMER, classify_coverage_result, coverage_plan
from .search_quality_semantics import evaluate_search_quality_semantics


class SearchQualitySemanticsSR0111Tests(SimpleTestCase):
    def _authoritative_payload(self):
        context = {
            "min_price": 100, "max_price": 200, "bedrooms": 3,
            "property_types": ["house"], "location_scope": "municipality",
            "search_locations": ["Target"],
        }
        candidates = []
        # 35 above max, including 14 also below the bedroom minimum.
        for index in range(35):
            candidates.append({
                "url": f"https://source.invalid/high/{index}", "price": 201,
                "bedrooms": 2 if index < 14 else 3, "classification": "discarded",
            })
        # Five below minimum; the final rejected candidate violates bedrooms only.
        for index in range(5):
            candidates.append({
                "url": f"https://source.invalid/low/{index}", "price": 99,
                "bedrooms": 3, "classification": "discarded",
            })
        candidates.append({
            "url": "https://source.invalid/bedroom", "price": 150, "bedrooms": 2,
            "property_type": "house", "municipality": "Target", "classification": "discarded",
        })
        candidates.extend([
            {"url": "https://source.invalid/ok/1", "price": 150, "bedrooms": 3,
             "property_type": "house", "municipality": "Target", "classification": "verified"},
            {"url": "https://source.invalid/ok/2", "price": 175, "bedrooms": 4,
             "property_type": "house", "municipality": "Target", "classification": "reviewable"},
        ])
        return {"context": context, "source_coverage": [{
            "attempted": True, "provider_outcome": "SUCCESS", "candidates": candidates,
            "stage_counters": {"format_noncompliance_count": 0},
        }]}

    def test_authoritative_raw_rejected_accepted_and_violation_semantics(self):
        quality = evaluate_search_quality_semantics(self._authoritative_payload())
        self.assertEqual(quality["raw_candidates"], 43)
        self.assertEqual(quality["rejected_by_hard_constraints"], 41)
        self.assertEqual(quality["accepted_clean"], 2)
        self.assertEqual(quality["hard_constraint_violations_raw"], 55)
        self.assertEqual(quality["hard_constraint_violations_accepted"], 0)
        self.assertEqual(quality["hard_constraint_violation_type_counts"], {
            "bedrooms_below_min": 15, "price_above_max": 35, "price_below_min": 5,
        })
        self.assertTrue(quality["accepted_candidate_safety_passed"])
        self.assertAlmostEqual(quality["acceptance_yield_pct"], 4.65)

    def test_unknown_required_attributes_are_completeness_not_violations(self):
        quality = evaluate_search_quality_semantics(self._authoritative_payload())
        self.assertEqual(quality["unknown_required_attribute_counts"], {
            "location": 40, "property_type": 40,
        })
        self.assertNotIn("unknown", quality["hard_constraint_violation_type_counts"])

    def test_accepted_candidate_with_hard_violation_fails_safety(self):
        payload = {"context": {"max_price": 100}, "candidates": [
            {"price": 101, "classification": "reviewable"},
        ]}
        quality = evaluate_search_quality_semantics(payload)
        self.assertEqual(quality["accepted_with_hard_constraint_violations"], 1)
        self.assertFalse(quality["accepted_candidate_safety_passed"])

    def test_planner_limit_is_not_provider_failure(self):
        quality = evaluate_search_quality_semantics({"source_coverage": [{
            "attempted": True, "provider_outcome": "RETRYABLE_FAILURE",
            "provider_reason": "planner_limit",
            "error": "PRE_PROVIDER_PLANNER_BUDGET_GATE",
        }]})
        self.assertEqual(quality["actual_provider_failure_count"], 0)
        self.assertEqual(quality["planner_limited_count"], 1)

    def test_invoked_timeout_rate_limit_and_provider_error_are_failures(self):
        rows = [{"attempted": True, "provider_outcome": "RETRYABLE_FAILURE", "error": reason}
                for reason in ("timeout", "rate_limit", "provider error")]
        quality = evaluate_search_quality_semantics({"source_coverage": rows})
        self.assertEqual(quality["actual_provider_failure_count"], 3)

    def test_zero_raw_has_safe_yield(self):
        quality = evaluate_search_quality_semantics({})
        self.assertEqual((quality["raw_candidates"], quality["acceptance_yield_pct"]), (0, 0.0))

    def test_sr010_contract_is_compatible_and_never_claims_market_completeness(self):
        plan = coverage_plan(["A"], 1, planned_calls=1)
        before_keys = set(classify_coverage_result(
            plan, executed_units=1, accepted_candidates=0,
        ))
        payload = dict(plan)
        payload.update(classify_coverage_result(plan, executed_units=1, accepted_candidates=0))
        payload["quality_semantics"] = evaluate_search_quality_semantics({})
        self.assertTrue(before_keys.issubset(payload))
        self.assertEqual(payload["result_scope"], "FULL_SOOI_PLAN")
        self.assertIn("no garantiza cubrir todo el mercado", MARKET_COMPLETENESS_DISCLAIMER)
        self.assertFalse(any("market" in key or "mercado" in key for key in payload["quality_semantics"]))
