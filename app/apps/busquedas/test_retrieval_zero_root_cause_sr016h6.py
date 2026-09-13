"""Offline SR0.16H.6 diagnostics for runtime telemetry and coverage axes."""

from unittest import TestCase
from unittest.mock import patch

from .services_portal_extractors import probe_portal_url
from .search_quality_semantics import result_actionability
from .services_hybrid_coverage_v261 import _near_match_allowed, _refresh_provider_efficiency


class RetrievalZeroRootCauseSR016H6Tests(TestCase):
    @patch("apps.busquedas.services_portal_extractors.fetch_html")
    def test_runtime_telemetry_persists_under_query_provenance_response(self, fetch):
        url = "https://www.fotocasa.es/es/alquiler/viviendas/malaga/todas-las-zonas/l"
        html = '<section id="main-content" data-testid="re-SearchResult"></section>'
        fetch.return_value = {
            "html": html, "final_url": url, "ok": True, "status": 200,
            "html_len": len(html), "elapsed_ms": 1, "error": None,
            "query_provenance": {"response": {}},
        }
        result = probe_portal_url("fotocasa", url)
        response = result["query_provenance"]["response"]
        self.assertEqual(response["result_telemetry"]["zero_reason"], "SCOPE_FOUND_NO_ANCHORS")
        self.assertTrue(response["result_telemetry"]["result_scope_found"])
        self.assertEqual(response["result_telemetry"]["parsed_candidate_count"], 0)

    def test_zero_telemetry_contract_is_explicit(self):
        self.assertEqual(
            {"result_scope_expected", "result_scope_found", "listing_anchor_count",
             "parsed_candidate_count", "no_results_marker_found", "zero_reason"},
            {"result_scope_expected", "result_scope_found", "listing_anchor_count",
             "parsed_candidate_count", "no_results_marker_found", "zero_reason"},
        )

    def test_exact_hard_clean_unknown_survives_as_review_not_actionable(self):
        candidate = {
            "classification": "reviewable",
            "availability_evidence": {"state": "unknown"},
            "reason": "availability_unknown:detail_not_confirmed",
        }
        projected = result_actionability(candidate, hard_violations=())
        self.assertTrue(projected["hard_clean"])
        self.assertTrue(projected["review_required"])
        self.assertFalse(projected["actionable"])
        self.assertEqual(projected["availability_state"], "UNKNOWN")

    def test_near_match_unknown_is_preserved_for_review(self):
        original = {"min_price": "700", "max_price": "1000", "bedrooms": 3}
        effective = dict(original, max_price="1100")
        candidate = {
            "price": 1050,
            "bedrooms": 3,
            "property_type": "flat",
            "municipality": "Málaga",
            "availability_evidence": {"state": "unknown"},
        }
        self.assertTrue(_near_match_allowed("BUDGET_PLUS_10_PERCENT", original, effective, candidate))

    def test_near_match_unavailable_remains_terminal(self):
        original = {"min_price": "700", "max_price": "1000", "bedrooms": 3}
        effective = dict(original, max_price="1100")
        candidate = {
            "price": 1050,
            "bedrooms": 3,
            "property_type": "flat",
            "municipality": "Málaga",
            "availability_evidence": {"state": "unavailable"},
        }
        self.assertFalse(_near_match_allowed("BUDGET_PLUS_10_PERCENT", original, effective, candidate))

    def test_provider_efficiency_exposes_availability_counts(self):
        row = {"candidates": [
            {"classification": "verified", "availability_evidence": {"state": "confirmed"}},
            {"classification": "reviewable", "reason": "availability_unknown:x",
             "availability_evidence": {"state": "unknown"}},
            {"classification": "discarded", "reason": "availability_unavailable:x",
             "availability_evidence": {"state": "unavailable"}},
        ]}
        _refresh_provider_efficiency(row, {})
        metrics = row["provider_efficiency"]
        self.assertEqual(metrics["availability_confirmed_count"], 1)
        self.assertEqual(metrics["availability_unknown_count"], 1)
        self.assertEqual(metrics["availability_unavailable_count"], 1)
        self.assertEqual(metrics["review_availability_count"], 1)
