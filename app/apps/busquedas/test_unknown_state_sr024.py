from unittest.mock import patch

from django.test import SimpleTestCase

from apps.busquedas.services_hybrid_coverage_v261 import (
    SourceSpec,
    _candidate_constraint_unknowns,
    _candidate_constraint_violations,
    _classify_candidate,
    _classify_probe_outcome,
)
from apps.busquedas.services_quality_gate_v26 import evaluate_candidate_v26


class GenericUnknownStatePolicyTests(SimpleTestCase):
    def setUp(self):
        self.spec = SourceSpec("generic", ("example.test",), "deterministic")
        self.ctx = {
            "min_price": 700,
            "max_price": 1000,
            "bedrooms": 3,
            "property_types": ["house", "flat"],
            "location": "Scopeville",
            "municipality": "Scopeville",
            "location_scope": "municipality",
            "search_locations": ["Scopeville"],
        }
        self.item = {
            "source_url": "https://example.test/advert/1",
            "title": "House in Scopeville",
            "location": "Scopeville",
            "municipality": "Scopeville",
            "price": 900,
            "bedrooms": 3,
            "property_type": "house",
            "provider": "deterministic",
        }

    def classify(self, probe, **changes):
        with patch(
            "apps.busquedas.services_hybrid_coverage_v261._probe_url",
            return_value=probe,
        ):
            return _classify_candidate(self.spec, {**self.item, **changes}, self.ctx, 1)

    def test_known_constraint_mismatches_are_discarded(self):
        available = {"http_status": 200, "available": True, "error": None, "unavailable_reason": None}
        for changes, reason in (
            ({"price": 1001}, "price_above_max"),
            ({"bedrooms": 2}, "bedrooms_below_min"),
            ({"title": "House elsewhere", "location": "Elsewhere", "municipality": "Elsewhere"}, "location_outside_search_scope"),
        ):
            with self.subTest(reason=reason):
                result = self.classify(available, **changes)
                self.assertEqual(result["classification"], "discarded")
                self.assertIn(reason, result["reason"])

    def test_definitive_unavailability_is_discarded(self):
        probes = (
            ({"http_status": 404, "available": False}, "http_404"),
            ({"http_status": 410, "available": False}, "http_410"),
            ({"http_status": 200, "available": False, "unavailable_reason": "ya no está publicado"}, "unavailable"),
        )
        for probe, reason in probes:
            with self.subTest(reason=reason):
                result = self.classify(probe)
                self.assertEqual(result["classification"], "discarded")

    def test_transient_or_blocked_probe_is_reviewable(self):
        probes = (
            ({"http_status": None, "available": False, "error": "TimeoutError: timed out"}, "timeout"),
            ({"http_status": None, "available": False, "error": "URLError: connection reset"}, "network"),
            ({"http_status": 403, "available": False, "error": "HTTPError: 403"}, "http_403"),
            ({"http_status": 429, "available": False, "error": "HTTPError: 429"}, "http_429"),
            *[({"http_status": status, "available": False, "error": f"HTTPError: {status}"}, "http_5xx")
              for status in (500, 502, 503)],
        )
        for probe, reason in probes:
            with self.subTest(reason=reason):
                result = self.classify(probe)
                self.assertEqual(result["classification"], "reviewable")
                self.assertIn(f"availability_unknown:{reason}", result["reason"])

    def test_missing_constrained_attributes_are_unknown(self):
        expected = {
            "price": "unknown_required_attribute:price",
            "bedrooms": "unknown_required_attribute:bedrooms",
            "property_type": "unknown_required_attribute:property_type",
            "location": "unknown_required_attribute:location",
        }
        for field, reason in expected.items():
            changes = {field: None}
            if field == "location":
                changes["title"] = "Individual advert"
                changes["municipality"] = None
            candidate = {**self.item, **changes}
            with self.subTest(field=field):
                self.assertEqual(_candidate_constraint_violations(self.ctx, candidate), [])
                self.assertIn(reason, _candidate_constraint_unknowns(self.ctx, candidate))
                result = self.classify(
                    {"http_status": 200, "available": True, "error": None}, **changes
                )
                self.assertEqual(result["classification"], "reviewable")
                self.assertIn(reason, result["reason"])

    def test_known_mismatch_precedes_probe_and_other_unknowns(self):
        result = self.classify(
            {"http_status": 503, "available": False, "error": "HTTPError: 503"},
            price=1001,
            bedrooms=None,
        )
        self.assertEqual(result["classification"], "discarded")
        self.assertIn("price_above_max", result["reason"])

    def test_positive_probe_with_no_unknowns_can_verify_deterministic_candidate(self):
        result = self.classify({"http_status": 200, "available": True, "error": None})
        self.assertEqual(result["classification"], "verified")

    def test_probe_classifier_never_treats_fetch_failure_as_absence(self):
        state, reason = _classify_probe_outcome(
            {"http_status": None, "available": False, "error": "TLS handshake failed"}
        )
        self.assertEqual(state, "UNKNOWN")
        self.assertEqual(reason, "availability_unknown:network")

    def test_deterministic_gate_missing_constrained_price_is_reviewable(self):
        result = evaluate_candidate_v26(
            {
                "portal": "generic",
                "source_url": "https://example.test/advert/1",
                "price": None,
            },
            municipality=None,
            max_price=1000,
        )
        self.assertEqual(result.decision, "reviewable")
        self.assertEqual(result.status_target, "in_review")
        self.assertIn("unknown_required_attribute:price", result.warnings)
