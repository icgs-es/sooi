import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .provider_gateway import ProviderOutcome
from .services_hybrid_coverage_v261 import (
    SourceSpec,
    _OPENAI_CANDIDATE_TEXT_CONFIG,
    _call_openai_web_search,
    _classify_candidate,
    _items_from_ai_text,
    _openai_prompt,
    _profile_context,
    _refresh_safe_stage_counters,
)


IDEALISTA = SourceSpec("idealista", ("idealista.com",), "openai_web_search")
MALAGA = [
    "Málaga", "Marbella", "Vélez-Málaga", "Estepona", "Mijas", "Fuengirola",
    "Torremolinos", "Benalmádena", "Rincón de la Victoria",
]
LOS_PEDROCHES = [
    "Pozoblanco", "Alcaracejos", "Añora", "Belalcázar", "Cardeña", "Conquista",
    "Dos Torres", "El Guijo", "El Viso", "Fuente La Lancha", "Hinojosa del Duque",
    "Pedroche", "Santa Eufemia", "Torrecampo", "Villanueva de Córdoba",
    "Villanueva del Duque", "Villaralto",
]


def candidate_text(rows):
    return json.dumps({"candidates": rows})


class StructuredResponseContractTests(SimpleTestCase):
    def test_profile_context_preserves_min_bedrooms_and_property_types(self):
        profile = SimpleNamespace(
            _meta=SimpleNamespace(fields=[]), operation_type="rent", province="Málaga",
            min_price=700, max_price=1000, min_bedrooms=3,
            property_types=["house", "flat"],
        )
        context = _profile_context(profile)
        self.assertEqual(context["min_bedrooms"], 3)
        self.assertEqual(context["bedrooms"], 3)
        self.assertEqual(context["property_types"], ["house", "flat"])

    def test_malaga_provider_prompt_has_exact_constraint_block(self):
        prompt = _openai_prompt(IDEALISTA, {
            "operation": "rent", "min_price": 700, "max_price": 1000,
            "min_bedrooms": 3, "bedrooms": 3,
            "property_types": ["house", "flat"], "location": "Málaga",
            "search_locations": MALAGA,
        }, 10)
        expected = "\n".join((
            "SOOI_SEARCH_CONSTRAINTS_V1", "operation=rent", "min_price=700",
            "max_price=1000", "min_bedrooms=3", "property_types=house,flat",
            f"bounded_municipalities={'|'.join(MALAGA)}",
            "END_SOOI_SEARCH_CONSTRAINTS_V1",
        ))
        self.assertIn(expected, prompt)

    def test_los_pedroches_provider_prompt_has_all_current_filters(self):
        prompt = _openai_prompt(IDEALISTA, {
            "operation": "sale", "min_price": None, "max_price": 60000,
            "min_bedrooms": 3, "bedrooms": 3, "min_area": 80,
            "property_types": ["house", "flat"], "location": "Los Pedroches",
            "search_locations": LOS_PEDROCHES,
        }, 10)
        for line in (
            "operation=sale", "min_price=null", "max_price=60000", "min_bedrooms=3",
            "property_types=house,flat", f"bounded_municipalities={'|'.join(LOS_PEDROCHES)}",
        ):
            self.assertIn(line, prompt)
        self.assertIn("superficie mínima 80 m2", prompt)

    def test_provider_input_uses_schema_web_search_and_disables_sdk_retries(self):
        response = SimpleNamespace(output_text='{"candidates": []}')
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response)))
        constructor = Mock(return_value=client)
        with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=constructor)}), \
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}, clear=False):
            result = _call_openai_web_search("min_bedrooms=3")
        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        constructor.assert_called_once_with(api_key="test-only", max_retries=0)
        kwargs = client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["input"], "min_bedrooms=3")
        self.assertEqual(kwargs["tools"], [{"type": "web_search"}])
        self.assertEqual(kwargs["text"], _OPENAI_CANDIDATE_TEXT_CONFIG)
        self.assertEqual(client.responses.create.call_count, 1)

    def test_only_tool_variant_compatibility_can_make_second_transport(self):
        response = SimpleNamespace(output_text='{"candidates": []}')
        create = Mock(side_effect=[Exception("invalid value: 'web_search'"), response])
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Mock(return_value=client))}), \
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}, clear=False):
            result = _call_openai_web_search("operation=rent")
        self.assertEqual(result.outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(create.call_count, 2)
        self.assertEqual(create.call_args_list[0].kwargs["tools"], [{"type": "web_search"}])
        self.assertEqual(create.call_args_list[1].kwargs["tools"], [{"type": "web_search_preview"}])

    def test_transient_provider_failure_has_no_adapter_retry(self):
        create = Mock(side_effect=TimeoutError("timed out"))
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Mock(return_value=client))}), \
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}, clear=False):
            result = _call_openai_web_search("operation=rent")
        self.assertEqual(result.outcome, ProviderOutcome.RETRYABLE_FAILURE)
        self.assertEqual(create.call_count, 1)

    def test_valid_list_and_documented_wrapper_extract_deterministically(self):
        row = {
            "source_url": "https://idealista.com/inmueble/123/", "price": 900,
            "municipality": "Málaga", "bedrooms": 3, "property_type": "flat",
            "area_m2": 80,
        }
        self.assertEqual(len(_items_from_ai_text(json.dumps([row]), IDEALISTA)), 1)
        self.assertEqual(len(_items_from_ai_text(candidate_text([row]), IDEALISTA)), 1)

    def test_strict_empty_list_is_zero_candidates_without_format_failure(self):
        for text in ("[]", candidate_text([])):
            trace = {}
            self.assertEqual(_items_from_ai_text(text, IDEALISTA, trace), [])
            self.assertEqual(trace.get("format_noncompliance_count", 0), 0)
            self.assertEqual(trace["provider_raw_item_count"], 0)

    def test_non_json_and_prose_with_listing_are_format_noncompliance(self):
        for text in ("No hay ninguno.", "Found https://idealista.com/inmueble/1/ for 900 EUR"):
            trace = {}
            self.assertEqual(_items_from_ai_text(text, IDEALISTA, trace), [])
            self.assertEqual(trace["format_noncompliance_count"], 1)
            self.assertEqual(trace["extracted_candidate_count"], 0)

    def test_valid_candidate_can_be_accepted_and_counters_remain_coherent(self):
        row = {
            "source_url": "https://idealista.com/inmueble/123/", "price": 900,
            "municipality": "Málaga", "bedrooms": 3, "property_type": "flat",
            "area_m2": 80,
        }
        trace = {}
        extracted = _items_from_ai_text(candidate_text([row]), IDEALISTA, trace)
        context = {
            "operation": "rent", "min_price": 700, "max_price": 1000,
            "bedrooms": 3, "property_types": ["house", "flat"],
            "location_scope": "municipality", "search_locations": ["Málaga"],
        }
        probe = {"http_status": 200, "available": True, "error": None}
        with patch("apps.busquedas.services_hybrid_coverage_v261._probe_url", return_value=probe):
            verdict = _classify_candidate(IDEALISTA, extracted[0], context, 1)
        counters = {
            "provider_response_present": True,
            "provider_raw_item_count": 1,
            "extracted_candidate_count": 1,
            "format_noncompliance_count": 0,
        }
        aggregate = {"candidate_count": 1, "candidates": [verdict], "stage_counters": counters}
        _refresh_safe_stage_counters(aggregate)
        self.assertIn(verdict["classification"], {"verified", "reviewable"})
        self.assertLessEqual(counters["accepted_candidate_count"], counters["normalized_or_deduped_candidate_count"])
        self.assertLessEqual(counters["normalized_or_deduped_candidate_count"], counters["extracted_candidate_count"])
        self.assertNotIn("source_url", counters)
        self.assertNotIn("response_body", counters)
        self.assertNotIn("contact", counters)
        self.assertTrue(all(not isinstance(value, (dict, list)) for value in counters.values()))

    def test_format_noncompliance_projects_safe_aggregate_reason(self):
        trace = {}
        _items_from_ai_text("123456789012345", IDEALISTA, trace)
        aggregate = {"candidate_count": 0, "candidates": [], "stage_counters": trace}
        _refresh_safe_stage_counters(aggregate)
        self.assertEqual(trace["zero_stage"], "format_noncompliance")
        self.assertEqual(trace["format_noncompliance_count"], 1)
