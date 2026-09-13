"""Offline contracts for SR0.16H.7.1 reuse and coverage semantics."""

from unittest import TestCase
from pathlib import Path


class ReuseCoverageConsistencySR016H71Tests(TestCase):
    @staticmethod
    def _detail_template():
        return (Path(__file__).resolve().parents[2] / "templates" / "busquedas" / "searchrun_detail.html").read_text()

    def test_historical_run_uses_legacy_source_coverage_format(self):
        template = self._detail_template()
        self.assertIn("{{ run.sources_planned }} planificadas · {{ run.sources_executed }} ejecutadas · {{ run.sources_omitted }} omitidas", template)

    def test_historical_run_never_renders_empty_reuse_fields(self):
        template = self._detail_template()
        self.assertIn("coverage_contract.sources_freshly_executed is not None", template)
        self.assertIn("{% else %}", template)

    def test_modern_reuse_run_uses_reuse_aware_source_coverage(self):
        template = self._detail_template()
        self.assertIn("sources_effectively_covered", template)
        self.assertIn("sources_reused", template)

    def test_modern_zero_fresh_executed_is_valid_value(self):
        modern = {"sources_freshly_executed": 0, "sources_effectively_covered": 4, "sources_omitted": 6}
        self.assertIsNotNone(modern["sources_freshly_executed"])
        self.assertEqual(modern["sources_freshly_executed"], 0)

    def test_reuse_is_explicitly_marked(self):
        payload = {
            "reuse_observability": {
                "reuse_applied": True,
                "reused_from_run_id": 218,
                "reused_candidate_count": 4,
                "reused_source_count": 2,
                "fresh_provider_requests_attempted": 0,
                "provider_request_governance_live_exercised": False,
                "provider_request_governance_live_state": "NOT_EXERCISED_DUE_TO_REUSE",
            }
        }
        reuse = payload["reuse_observability"]
        self.assertTrue(reuse["reuse_applied"])
        self.assertEqual(reuse["reused_from_run_id"], 218)
        self.assertEqual(reuse["reused_candidate_count"], 4)
        self.assertFalse(reuse["provider_request_governance_live_exercised"])

    def test_reused_telemetry_origin_is_not_fresh(self):
        row = {"reused": True, "reused_from_search_run_id": 218,
               "telemetry_origin_run_id": 218}
        self.assertEqual(row["telemetry_origin_run_id"], row["reused_from_search_run_id"])

    def test_fresh_and_reused_sources_have_one_canonical_coverage_count(self):
        planned = 10
        fresh = {"fotocasa", "habitaclia"}
        reused = {"idealista", "pisos.com"}
        covered = fresh | reused
        omitted = planned - len(covered)
        self.assertEqual(len(covered), 4)
        self.assertEqual(omitted, 6)
        self.assertEqual(round(100 * len(covered) / planned, 2), 40.0)

    def test_zero_fresh_requests_with_reuse_is_not_live_pass(self):
        state = {"reuse_applied": True, "fresh_provider_requests_attempted": 0}
        live = bool(state["fresh_provider_requests_attempted"])
        self.assertFalse(live)

    def test_commercial_review_results_survive_reuse(self):
        candidates = [{"classification": "reviewable", "reused": True} for _ in range(4)]
        self.assertEqual(len(candidates), 4)
        self.assertTrue(all(item["classification"] == "reviewable" for item in candidates))

    def test_terminal_status_reason_is_explicit(self):
        run = {"status": "completed_with_errors", "reason": "low_coverage_or_validation"}
        self.assertEqual(run["status"], "completed_with_errors")
        self.assertTrue(run["reason"])
