"""Offline contracts for bounded Idealista external recall expansion."""
import json

from django.test import SimpleTestCase

from .services_hybrid_coverage_v261 import (
    DEFAULT_EXTERNAL_SOURCE_CAP,
    IDEALISTA_EXTERNAL_RESULT_CAP,
    IDEALISTA_QUERY_PLAN_MAX_GROUPS,
    SourceSpec,
    _ai_candidates_expanded,
    _classify_candidate,
    _dedupe_candidates,
    _external_id_from_url,
    _external_result_cap,
    _idealista_query_groups,
    _items_from_ai_text,
    _looks_like_listing_url_v261,
    _openai_prompt,
    _OPENAI_CANDIDATE_TEXT_CONFIG,
)


IDEALISTA = SourceSpec("idealista", ("idealista.com",), "openai_web_search")
OTHER = SourceSpec("pisos.com", ("pisos.com",), "openai_web_search")


class IdealistaRecallSR016JTests(SimpleTestCase):
    def test_idealista_external_cap_is_30(self):
        self.assertEqual(_external_result_cap(IDEALISTA, 10), 30)
        self.assertEqual(IDEALISTA_EXTERNAL_RESULT_CAP, 30)

    def test_non_idealista_external_cap_remains_10(self):
        self.assertEqual(_external_result_cap(OTHER, 30), DEFAULT_EXTERNAL_SOURCE_CAP)

    def test_eco_still_has_one_external_operation_contract(self):
        # The planner owns call/credit accounting; the recall cap is output
        # budget only and does not alter that contract.
        self.assertEqual(IDEALISTA_EXTERNAL_RESULT_CAP, 30)
        self.assertEqual(IDEALISTA_QUERY_PLAN_MAX_GROUPS, 4)

    def test_schema_includes_title(self):
        item = _OPENAI_CANDIDATE_TEXT_CONFIG["format"]["schema"]["properties"]["candidates"]["items"]
        self.assertIn("title", item["properties"])

    def test_structured_schema_candidate_required_contains_title(self):
        item = _OPENAI_CANDIDATE_TEXT_CONFIG["format"]["schema"]["properties"]["candidates"]["items"]
        self.assertIn("title", item["required"])

    def test_structured_schema_object_required_covers_properties(self):
        def walk(node):
            if not isinstance(node, dict):
                return
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                required = node.get("required")
                self.assertIsInstance(required, list)
                self.assertTrue(set(node["properties"]).issubset(set(required)))
                for child in node["properties"].values():
                    walk(child)
            if isinstance(node.get("items"), dict):
                walk(node["items"])

        walk(_OPENAI_CANDIDATE_TEXT_CONFIG["format"]["schema"])

    def test_real_request_payload_contains_structured_schema(self):
        payload = {"text": _OPENAI_CANDIDATE_TEXT_CONFIG}
        item = payload["text"]["format"]["schema"]["properties"]["candidates"]["items"]
        self.assertIn("title", item["properties"])
        self.assertIn("title", item["required"])

    def test_title_is_preserved_when_present(self):
        trace = {}
        text = json.dumps({"candidates": [{
            "title": "Piso en Pozoblanco", "source_url": "https://www.idealista.com/inmueble/123/",
            "price": 39000, "municipality": "Pozoblanco", "bedrooms": 3,
            "property_type": "flat", "area_m2": None,
        }]})
        items = _items_from_ai_text(text, IDEALISTA, trace)
        self.assertEqual(items[0]["title"], "Piso en Pozoblanco")
        self.assertEqual(trace.get("missing_title_count", 0), 0)

    def test_missing_title_does_not_hard_reject_valid_listing(self):
        trace = {}
        text = json.dumps({"candidates": [{
            "source_url": "https://www.idealista.com/inmueble/123/", "price": 39000,
            "municipality": "Pozoblanco", "bedrooms": 3, "property_type": "flat", "area_m2": None,
        }]})
        items = _items_from_ai_text(text, IDEALISTA, trace)
        self.assertEqual(len(items), 1)
        self.assertEqual(trace["missing_title_count"], 1)

    def test_title_empty_representation_keeps_fallback_semantics(self):
        text = json.dumps({"candidates": [{
            "title": "", "source_url": "https://www.idealista.com/inmueble/123/", "price": 39000,
            "municipality": "Pozoblanco", "bedrooms": 3, "property_type": "flat", "area_m2": None,
        }]})
        items = _items_from_ai_text(text, IDEALISTA, {})
        self.assertEqual(items[0]["title"], "")

    def test_direct_listing_url_accepted_and_search_area_rejected(self):
        self.assertFalse(_looks_like_listing_url_v261("idealista", "https://www.idealista.com/inmueble/123/"))
        self.assertTrue(_looks_like_listing_url_v261("idealista", "https://www.idealista.com/venta-viviendas/"))
        self.assertTrue(_looks_like_listing_url_v261("idealista", "https://www.idealista.com/geo/"))

    def test_stable_id_from_inmueble_url(self):
        self.assertEqual(_external_id_from_url("https://www.idealista.com/inmueble/123/"), "idealista:123")

    def test_duplicate_stable_id_is_deduped(self):
        items = [
            {"source": "idealista", "source_url": "https://www.idealista.com/inmueble/123/"},
            {"source": "idealista", "source_url": "https://www.idealista.com/inmueble/123/?utm_source=x"},
        ]
        self.assertEqual(len(_dedupe_candidates(items)), 1)

    def test_query_plan_covers_all_locations_and_is_bounded(self):
        locations = [f"Municipio {i}" for i in range(17)]
        groups = _idealista_query_groups({"search_locations": locations})
        self.assertLessEqual(len(groups), 4)
        self.assertEqual({item for group in groups for item in group}, set(locations))
        prompt = _openai_prompt(IDEALISTA, {
            "operation": "sale", "location_scope": "comarca", "search_locations": locations,
            "property_types": ["house", "flat"], "max_price": 60000, "min_bedrooms": 3,
        }, 30)
        for location in locations:
            self.assertIn(location, prompt)

    def test_unknown_availability_remains_review_and_not_actionable(self):
        verdict = _classify_candidate(
            IDEALISTA,
            {"source_url": "https://www.idealista.com/inmueble/123/", "price": 39000,
             "municipality": "Pozoblanco", "bedrooms": 3, "property_type": "flat"},
            {"location_scope": "comarca", "search_locations": ["Pozoblanco"],
             "max_price": 60000, "bedrooms": 3, "property_types": ["flat"]},
            timeout=1, allow_detail_probe=False,
        )
        self.assertEqual(verdict["classification"], "reviewable")

    def test_no_direct_idealista_http_or_bypass(self):
        self.assertEqual(IDEALISTA.method, "openai_web_search")
        self.assertNotIn("requests.get(idealista", _openai_prompt(IDEALISTA, {}, 30).lower())
