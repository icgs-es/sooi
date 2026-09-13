"""Focused, offline contracts for SR0.14D."""

from unittest import TestCase
from urllib.parse import parse_qs, urlparse

from .services_hybrid_coverage_v261 import (
    SourceSpec,
    _candidate_constraint_violations,
    _deterministic_url,
)
from .services_portal_extractors import candidate_from_context
from .templatetags.busquedas_format import (
    result_action_label,
    result_confidence_label,
    result_confidence_message,
)


CONTEXT = {
    "operation": "rent", "location": "Cártama", "province": "Málaga",
    "min_price": 700, "max_price": 1000, "bedrooms": 3,
    "min_bedrooms": 3, "property_types": ["house", "flat"],
}


class ProviderEfficiencyResultConfidenceSR014DTests(TestCase):
    def test_customer_confidence_semantics_do_not_expose_internal_reason(self):
        self.assertEqual(result_confidence_label("verified"), "Verificado")
        self.assertEqual(result_confidence_label("reviewable"), "Para revisar")
        copy = result_confidence_message("availability_unknown:http_403")
        self.assertEqual(copy, "Disponibilidad pendiente de confirmar")
        self.assertNotIn("http_403", copy)
        self.assertNotIn("availability_unknown", copy)
        self.assertEqual(result_action_label("would_create_in_review"), "Revisión necesaria")

    def test_reviewable_classification_is_preserved(self):
        candidate = {"classification": "reviewable", "reason": "availability_unknown:http_403"}
        result_confidence_label(candidate["classification"])
        result_confidence_message(candidate["reason"])
        self.assertEqual(candidate["classification"], "reviewable")

    def test_hard_filters_are_unchanged(self):
        self.assertIn("price_above_max:1001>1000", _candidate_constraint_violations(
            CONTEXT, {"price": 1001, "bedrooms": 3, "property_type": "flat"},
        ))
        self.assertIn("bedrooms_below_min:2<3", _candidate_constraint_violations(
            CONTEXT, {"price": 900, "bedrooms": 2, "property_type": "flat"},
        ))

    def test_intent_is_not_candidate_evidence(self):
        candidate = candidate_from_context(
            "habitaclia", "deterministic_habitaclia",
            "https://www.habitaclia.com/alquiler-inmueble-ref-i123.htm",
            "Anuncio", "900 € · 3 habitaciones", "Cártama",
        )
        self.assertIsNone(candidate.municipality)
        self.assertIsNone(candidate.property_type)

    def test_habitaclia_existing_query_contract_survives_url_build(self):
        url = _deterministic_url(
            SourceSpec("habitaclia", ("habitaclia.com",), "deterministic"), CONTEXT,
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["pmax"], ["1000"])
        self.assertEqual(query["hab"], ["3"])
