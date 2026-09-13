import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .provider_gateway import ProviderOutcome, ProviderResult
from .services_hybrid_coverage_v261 import (
    SourceSpec,
    _ai_candidates_expanded,
    _classify_candidate,
    _items_from_ai_text,
    _openai_prompt,
    _profile_context,
    _refresh_safe_stage_counters,
)


IDEALISTA = SourceSpec("idealista", ("idealista.com",), "openai_web_search")


class CommercialPromptTraceTests(SimpleTestCase):
    def test_min_area_m2_reaches_context(self):
        profile = SimpleNamespace(
            _meta=SimpleNamespace(fields=[]), province="Córdoba", operation_type="sale",
            min_area_m2=80,
        )
        self.assertEqual(_profile_context(profile)["min_area"], 80)

    def test_malaga_prompt_has_constraints_and_only_authorized_first_batch(self):
        locations = [
            "Málaga", "Marbella", "Vélez-Málaga", "Estepona", "Mijas",
            "Fuengirola", "Torremolinos", "Benalmádena", "Rincón de la Victoria",
        ]
        ctx = {
            "location": "Málaga", "province": "Málaga", "operation": "rent",
            "min_price": 700, "max_price": 1000, "bedrooms": 3,
            "property_types": ["house", "flat"], "location_scope": "multi_location",
            "search_locations": locations,
        }
        calls = []

        def provider(prompt):
            calls.append(prompt)
            return ProviderResult(ProviderOutcome.SUCCESS, value="[]")

        authorizations = iter([True, False])
        with patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search", provider):
            _ai_candidates_expanded(
                IDEALISTA, ctx, max_results=10, max_locations=4,
                before_call=lambda: next(authorizations),
            )

        self.assertEqual(len(calls), 1)
        prompt = calls[0]
        for value in ("rent", "700", "1000", "3", "house", "flat"):
            self.assertIn(value, prompt)
        for municipality in locations[:3]:
            self.assertIn(municipality, prompt)
        for municipality in locations[3:]:
            self.assertNotIn(municipality, prompt)

    def test_los_pedroches_prompt_has_exact_planner_authorized_batch(self):
        locations = [
            "Pozoblanco", "Alcaracejos", "Añora", "Belalcázar", "Cardeña", "Conquista",
            "Dos Torres", "El Guijo", "El Viso", "Fuente La Lancha", "Hinojosa del Duque",
            "Pedroche", "Santa Eufemia", "Torrecampo", "Villanueva de Córdoba",
            "Villanueva del Duque", "Villaralto",
        ]
        ctx = {
            "location": "Los Pedroches", "province": "Córdoba", "operation": "sale",
            "max_price": 60000, "bedrooms": 3, "min_area": 80,
            "property_types": ["house", "flat"], "location_scope": "comarca",
            "search_locations": locations,
        }
        calls = []

        def provider(prompt):
            calls.append(prompt)
            return ProviderResult(ProviderOutcome.SUCCESS, value="[]")

        authorizations = iter([True, False])
        with patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search", provider):
            _ai_candidates_expanded(
                IDEALISTA, ctx, max_results=10, max_locations=4,
                before_call=lambda: next(authorizations),
            )

        self.assertEqual(len(calls), 1)
        prompt = calls[0]
        for value in ("sale", "60000", "3", "80", "house", "flat"):
            self.assertIn(value, prompt)
        authorized = locations[:5]
        self.assertEqual(
            [municipality for municipality in locations if municipality in prompt], authorized,
        )
        self.assertIn("Pozoblanco", prompt)

    def test_empty_response_distinguishes_provider_presence_and_extraction_zero(self):
        trace = {}
        self.assertEqual(_items_from_ai_text("[]", IDEALISTA, trace), [])
        self.assertTrue(trace["provider_response_present"])
        self.assertEqual(trace["provider_raw_item_count"], 0)
        self.assertEqual(trace["extracted_candidate_count"], 0)

    def test_safe_rejection_and_acceptance_counters(self):
        reasons = [
            ("reviewable", "ai_candidate_passed_strict_review_gate"),
            ("discarded", "location_outside_search_scope"),
            ("discarded", "price_above_max:70000>60000"),
            ("discarded", "bedrooms_below_min:2<3"),
            ("discarded", "property_type_mismatch:commercial"),
            ("discarded", "unknown_required_attribute:location"),
        ]
        row = {
            "candidate_count": len(reasons),
            "candidates": [
                {"classification": classification, "reason": reason}
                for classification, reason in reasons
            ],
            "stage_counters": {},
        }
        _refresh_safe_stage_counters(row)
        counters = row["stage_counters"]
        self.assertEqual(counters["accepted_candidate_count"], 1)
        for key in (
            "rejected_geography_count", "rejected_price_count", "rejected_bedroom_count",
            "rejected_property_type_count", "rejected_missing_evidence_count",
        ):
            self.assertEqual(counters[key], 1)

        serialized = json.dumps(counters)
        self.assertNotIn("description", serialized)
        self.assertNotIn("raw", serialized)
        self.assertNotIn("contact", serialized)

    def test_malaga_sixteen_candidate_flow_is_coherent(self):
        rows = [
            {
                "title": f"Synthetic {index}",
                "source_url": f"https://idealista.com/inmueble/{index}/",
                "price": 1200 if index < 15 else 900,
                "location": "Málaga",
                "municipality": "Málaga",
                "province": "Málaga",
                "bedrooms": 2 if index < 5 else 3,
                "area_m2": 90,
                "property_type": "flat",
            }
            for index in range(16)
        ]
        trace = {}
        extracted = _items_from_ai_text(json.dumps(rows), IDEALISTA, trace)
        ctx = {
            "operation": "rent", "min_price": 700, "max_price": 1000,
            "bedrooms": 3, "property_types": ["house", "flat"],
            "location_scope": "multi_location", "search_locations": ["Málaga"],
        }
        available = {"http_status": 200, "available": True, "error": None}
        with patch("apps.busquedas.services_hybrid_coverage_v261._probe_url", return_value=available):
            verdicts = [_classify_candidate(IDEALISTA, item, ctx, 1) for item in extracted]
        row = {
            "candidate_count": len(extracted), "candidates": verdicts,
            "stage_counters": {
                "provider_response_present": trace["provider_response_present"],
                "provider_raw_item_count": trace["provider_raw_item_count"],
                "extracted_candidate_count": trace["extracted_candidate_count"],
            },
        }
        _refresh_safe_stage_counters(row)
        counters = row["stage_counters"]
        self.assertEqual(counters["provider_raw_item_count"], 16)
        self.assertEqual(counters["extracted_candidate_count"], 16)
        self.assertEqual(counters["normalized_or_deduped_candidate_count"], 16)
        self.assertEqual(counters["rejected_price_count"], 15)
        self.assertEqual(counters["accepted_candidate_count"], 1)
        self.assertLessEqual(
            counters["normalized_or_deduped_candidate_count"],
            counters["extracted_candidate_count"],
        )
