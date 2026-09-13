"""Focused acceptance tests for SR0.14B; no network or provider calls."""

from types import SimpleNamespace
from unittest import TestCase

from .models import SearchRun
from .search_budget_ux import PARTIAL_COMPLETION_LABEL, customer_run_status
from .search_quality_semantics import evaluate_search_quality_semantics
from .services_hybrid_coverage_v261 import (
    SourceSpec,
    _candidate_constraint_violations,
    _deterministic_url,
    _is_pre_provider_gate_reason,
    _openai_prompt,
)
from .services_portal_extractors import candidate_from_context


RUN_212_CONTEXT = {
    "operation": "rent",
    "min_price": 700,
    "max_price": 1000,
    "bedrooms": 3,
    "min_bedrooms": 3,
    "property_types": ["house", "flat"],
    "location": "Málaga",
    "province": "Málaga",
    "location_scope": "multi_location",
    "search_locations": ["Cártama", "Alhaurín de la Torre", "Málaga"],
}


class ProviderQueryPrecisionSR014BTests(TestCase):
    def test_deterministic_contract_preserves_supported_constraints(self):
        habitaclia = _deterministic_url(
            SourceSpec("habitaclia", ("habitaclia.com",), "deterministic"),
            {**RUN_212_CONTEXT, "location": "Cártama"},
        )
        fotocasa = _deterministic_url(
            SourceSpec("fotocasa", ("fotocasa.es",), "deterministic"),
            {**RUN_212_CONTEXT, "location": "Cártama"},
        )
        self.assertIn("/alquiler-cartama.htm", habitaclia)
        self.assertIn("hab=3", habitaclia)
        self.assertIn("pmax=1000", habitaclia)
        self.assertIn("/es/alquiler/viviendas/cartama/", fotocasa)
        self.assertIn("minRooms=3", fotocasa)
        # No unverified portal-specific min-price/type parameters are invented.
        self.assertNotIn("700", habitaclia + fotocasa)
        self.assertNotIn("house", habitaclia + fotocasa)

    def test_text_discovery_contract_preserves_all_frozen_constraints(self):
        prompt = _openai_prompt(
            SourceSpec("idealista", ("idealista.com",), "openai_web_search"),
            RUN_212_CONTEXT,
            10,
        )
        for expected in (
            "operation=rent", "min_price=700", "max_price=1000",
            "min_bedrooms=3", "property_types=house,flat",
            "bounded_municipalities=Cártama|Alhaurín de la Torre|Málaga",
        ):
            self.assertIn(expected, prompt)

    def test_hard_filter_remains_authoritative_after_pushdown(self):
        self.assertIn(
            "price_above_max:1001>1000",
            _candidate_constraint_violations(RUN_212_CONTEXT, {"price": 1001, "bedrooms": 3}),
        )
        self.assertIn(
            "bedrooms_below_min:2<3",
            _candidate_constraint_violations(RUN_212_CONTEXT, {"price": 900, "bedrooms": 2}),
        )

    def test_requested_intent_is_not_candidate_evidence(self):
        candidate = candidate_from_context(
            "fotocasa", "deterministic_fotocasa",
            "https://www.fotocasa.es/es/alquiler/vivienda/sin-datos/1234567/d",
            "Anuncio disponible", "900 € · 3 habitaciones", "Málaga",
        )
        self.assertIsNone(candidate.municipality)
        self.assertIsNone(candidate.property_type)

    def test_listing_url_can_supply_property_type_evidence(self):
        candidate = candidate_from_context(
            "habitaclia", "deterministic_habitaclia",
            "https://www.habitaclia.com/alquiler-piso-centro-i123.htm",
            "", "900 € · 3 habitaciones", "Málaga",
        )
        self.assertEqual(candidate.property_type, "flat")
        self.assertIsNone(candidate.municipality)

    def test_budget_limited_completion_has_partial_customer_label(self):
        run = SimpleNamespace(
            raw_response={"quality_semantics": {"actual_provider_failure_count": 0}},
            status=SearchRun.Status.COMPLETED_WITH_ERRORS,
            error_message="", coverage_status=SearchRun.CoverageStatus.LIMITED_BY_BUDGET,
            stop_reason=SearchRun.StopReason.BUDGET_EXHAUSTED,
            get_status_display=lambda: "Completada con errores",
        )
        self.assertEqual(customer_run_status(run)["label"], PARTIAL_COMPLETION_LABEL)

    def test_lightweight_run_without_status_keeps_partial_semantics(self):
        run = SimpleNamespace(
            coverage_status=SearchRun.CoverageStatus.LIMITED_BY_BUDGET,
            stop_reason=SearchRun.StopReason.BUDGET_EXHAUSTED,
        )
        self.assertEqual(customer_run_status(run), {
            "label": PARTIAL_COMPLETION_LABEL, "kind": "partial",
        })

    def test_real_provider_failure_keeps_error_presentation(self):
        run = SimpleNamespace(
            raw_response={"quality_semantics": {"actual_provider_failure_count": 1}},
            status=SearchRun.Status.COMPLETED_WITH_ERRORS,
            error_message="", coverage_status=SearchRun.CoverageStatus.DEGRADED_PROVIDER,
            stop_reason=SearchRun.StopReason.PROVIDER_HARD_FAILURE,
            get_status_display=lambda: "Completada con errores",
        )
        self.assertEqual(customer_run_status(run), {
            "label": "Completada con errores", "kind": "error",
        })

    def test_planner_limit_is_pre_provider_not_provider_failure(self):
        self.assertTrue(_is_pre_provider_gate_reason("Málaga:planner_limit"))
        quality = evaluate_search_quality_semantics({"source_coverage": [{
            "attempted": False, "status": "omitted", "provider_outcome": "SKIPPED_CIRCUIT",
            "provider_reason": "planner_limit", "candidates": [],
        }]})
        self.assertEqual(quality["actual_provider_failure_count"], 0)
        self.assertEqual(quality["planner_limited_count"], 1)
