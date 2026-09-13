from unittest.mock import patch

from django.test import SimpleTestCase

from apps.busquedas.services_hybrid_coverage_v261 import (
    SourceSpec,
    _candidate_constraint_unknowns,
    _candidate_constraint_violations,
    _classify_candidate,
    _items_from_ai_text,
)
from apps.busquedas.services_portal_extractors import candidate_from_context


class CanonicalMunicipalityEvidenceTests(SimpleTestCase):
    def setUp(self):
        self.scope = {
            "location_scope": "multi_location",
            "search_locations": ["Capitalia", "Villa Álamo"],
        }

    def test_province_name_cannot_satisfy_same_named_municipality(self):
        item = {
            "municipality": "Other Town",
            "province": "Capitalia",
            "location": "Other Town, Capitalia",
        }
        self.assertEqual(
            _candidate_constraint_violations(self.scope, item),
            ["location_outside_search_scope"],
        )

    def test_explicit_municipality_matches_despite_district_display(self):
        item = {
            "municipality": "Villa Alamo",
            "location": "Old Quarter, Villa Álamo, Capitalia",
        }
        self.assertEqual(_candidate_constraint_violations(self.scope, item), [])
        self.assertEqual(_candidate_constraint_unknowns(self.scope, item), [])

    def test_comarca_uses_exact_canonical_membership(self):
        scope = {"location_scope": "comarca", "search_locations": ["North Village", "South Village"]}
        self.assertEqual(
            _candidate_constraint_violations(scope, {"municipality": "north village"}), []
        )
        self.assertEqual(
            _candidate_constraint_violations(scope, {"municipality": "West Village"}),
            ["location_outside_search_scope"],
        )

    def test_missing_municipality_is_unknown_even_with_known_province(self):
        item = {"municipality": "", "province": "Capitalia", "location": "Capitalia"}
        self.assertEqual(_candidate_constraint_violations(self.scope, item), [])
        self.assertEqual(
            _candidate_constraint_unknowns(self.scope, item),
            ["unknown_required_attribute:location"],
        )

    def test_province_scope_does_not_reject_an_outside_municipality(self):
        scope = {"location_scope": "province", "province": "Capitalia"}
        item = {"municipality": "Other Town", "province": "Capitalia"}
        self.assertEqual(_candidate_constraint_violations(scope, item), [])
        self.assertEqual(_candidate_constraint_unknowns(scope, item), [])

    def test_query_provenance_and_url_cannot_override_outside_evidence(self):
        item = {
            "municipality": "Other Town",
            "search_location": "Capitalia",
            "search_location_batch": ["Capitalia", "Villa Álamo"],
            "source_url": "https://capitalia.example.test/listing/villa-alamo/1",
        }
        self.assertEqual(
            _candidate_constraint_violations(self.scope, item),
            ["location_outside_search_scope"],
        )

    @patch("apps.busquedas.services_hybrid_coverage_v261._probe_url")
    def test_geography_mismatch_precedes_timeout_and_unknown_price(self, probe):
        probe.return_value = {"http_status": None, "error": "TimeoutError: timed out", "available": False}
        item = {
            "source_url": "https://example.test/listing/1",
            "municipality": "Other Town",
            "price": None,
        }
        result = _classify_candidate(
            SourceSpec("generic", ("example.test",), "openai_web_search"),
            item,
            {**self.scope, "max_price": 1000},
            1,
        )
        self.assertEqual(result["classification"], "discarded")
        self.assertEqual(result["reason"], "location_outside_search_scope")
        self.assertEqual(result["municipality"], "Other Town")

    def test_ai_candidate_preserves_separate_geography_fields(self):
        items = _items_from_ai_text(
            '[{"source_url":"https://example.test/1","location":"District, Town, Province",'
            '"municipality":"Town","province":"Province"}]',
            SourceSpec("generic", ("example.test",), "openai_web_search"),
        )
        self.assertEqual(items[0]["location"], "District, Town, Province")
        self.assertEqual(items[0]["municipality"], "Town")
        self.assertEqual(items[0]["province"], "Province")

    def test_deterministic_url_is_not_municipality_evidence(self):
        candidate = candidate_from_context(
            "generic", "deterministic", "https://example.test/capitalia/1", "Home", "Capitalia", "Capitalia"
        )
        self.assertIsNone(candidate.municipality)
        self.assertFalse(candidate.municipality_match)
