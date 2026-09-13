from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase

from .forms import SearchProfileForm
from .models import GeographicArea, SearchProfile
from .price import format_euro_price, normalize_euro_price
from .services_portal_extractors import extract_price
from .services_hybrid_coverage_v261 import (
    SourceSpec, _ai_candidates_expanded, _apply_auto_location,
    _apply_search_locations_v261, _candidate_constraint_violations,
    _dedupe_candidates, _deterministic_candidates_expanded_v2615,
)


class PriceContractTests(SimpleTestCase):
    def test_spanish_price_normalization(self):
        cases = {
            60000: Decimal("60000"), "60.000 €": Decimal("60000"),
            "60.000": Decimal("60000"), "60,00 €": Decimal("60.00"),
            "1.000 €/mes": Decimal("1000"), "60 €": Decimal("60"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_euro_price(raw), expected)
        self.assertNotEqual(normalize_euro_price("60 €"), normalize_euro_price("60.000 €"))
        self.assertEqual(format_euro_price(60000), "60.000 €")

    def test_portal_price_extraction_uses_the_same_euro_contract(self):
        cases = {
            "Precio 60.000 €": 60000,
            "Precio 60 €": 60,
            "Precio 60,00 €": 60,
            "Alquiler 1.000 €/mes": 1000,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_price(text), expected)

    def test_hard_max_is_exact(self):
        ctx = {"max_price": 60000, "property_types": [], "location_scope": "province"}
        self.assertEqual(_candidate_constraint_violations(ctx, {"price": 60000}), [])
        self.assertIn("price_above_max:60001>60000", _candidate_constraint_violations(ctx, {"price": 60001}))


class GeographyModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="geo", password="x")
        self.area = GeographicArea.objects.create(
            name="Zona norte", area_type="custom", province="Córdoba",
            municipalities=["Pozoblanco", "Pedroche"],
        )

    def profile(self, **kwargs):
        values = dict(name="Búsqueda", owner=self.user, province="Córdoba")
        values.update(kwargs)
        return SearchProfile.objects.create(**values)

    def test_named_area_membership_is_canonical(self):
        profile = self.profile(geography_scope="named_area", geographic_area=self.area, zone="texto antiguo")
        self.assertEqual(profile.canonical_search_locations(), ["Pozoblanco", "Pedroche"])
        ctx = {"province": "Córdoba"}
        _apply_auto_location(profile, ctx)
        _apply_search_locations_v261(ctx)
        self.assertEqual(ctx["location_scope"], "comarca")
        self.assertEqual(ctx["search_locations"], ["Pozoblanco", "Pedroche"])

    def test_legacy_zone_fallback(self):
        profile = self.profile(zone="Pozoblanco, Pedroche")
        self.assertEqual(profile.canonical_search_locations(), ["Pozoblanco", "Pedroche"])

    def test_form_converts_chips_to_canonical_locations_and_preserves_price(self):
        data = {
            "name": "Multi", "operation_type": "sale", "province": "Córdoba",
            "geography_scope": "multi_municipality", "municipalities": "Pozoblanco, Pedroche",
            "property_types": ["house"], "min_price": "60,00 €", "max_price": "60000",
            "min_area_m2": "", "min_bedrooms": "", "ai_prompt": "", "notes": "",
        }
        form = SearchProfileForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        profile = form.save(commit=False)
        self.assertEqual(profile.municipalities, ["Pozoblanco", "Pedroche"])
        self.assertEqual(profile.max_price, Decimal("60000"))


class FanoutTests(SimpleTestCase):
    @patch("apps.busquedas.services_hybrid_coverage_v261._deterministic_candidates")
    def test_deterministic_fanout_and_dedup(self, discover):
        discover.return_value = ([{"source": "fotocasa", "source_url": "https://fotocasa.es/a/1"}], None)
        spec = SourceSpec("fotocasa", ("fotocasa.es",), "deterministic")
        ctx = {"location_scope": "multi_location", "search_locations": ["Pozoblanco", "Pedroche"]}
        items, error = _deterministic_candidates_expanded_v2615(spec, ctx, timeout=1)
        self.assertEqual(discover.call_count, 2)
        self.assertEqual(len(items), 1)
        self.assertIsNone(error)

    def _assert_all_locations_are_batched(self, location_count):
        locations = [f"Municipio {index:02d} fin" for index in range(location_count)]
        spec = SourceSpec("pisos.com", ("pisos.com",), "openai_web_search")
        ctx = {
            "province": "Córdoba", "operation": "sale",
            "location_scope": "multi_location", "search_locations": locations,
        }
        calls = []

        def capture(prompt):
            calls.append(prompt)
            return "[]", None

        with patch(
            "apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search",
            side_effect=capture,
        ):
            items, error = _ai_candidates_expanded(spec, ctx, max_results=10)

        self.assertLessEqual(len(calls), 4)
        self.assertEqual(len(calls), min(location_count, 4))
        all_prompts = "\n".join(calls)
        represented = [location for location in locations if location in all_prompts]
        self.assertEqual(represented, locations)
        for location in locations:
            self.assertEqual(all_prompts.count(location), 1)
        self.assertEqual(items, [])
        self.assertIsNone(error)

    def test_nine_municipalities_are_all_represented_in_at_most_four_calls(self):
        self._assert_all_locations_are_batched(9)

    def test_twenty_municipalities_are_all_represented_in_at_most_four_calls(self):
        self._assert_all_locations_are_batched(20)

    @patch("apps.busquedas.services_hybrid_coverage_v261._call_openai_web_search")
    def test_ai_fanout_preserves_batch_attribution_and_deduplicates(self, call):
        call.return_value = ('[{"url":"https://pisos.com/comprar/piso-x-1_1/","location":"X","price":"60.000 €"}]', None)
        spec = SourceSpec("pisos.com", ("pisos.com",), "openai_web_search")
        locations = [f"M{i}" for i in range(9)]
        ctx = {"province": "Córdoba", "operation": "sale", "location_scope": "multi_location", "search_locations": locations}
        items, error = _ai_candidates_expanded(spec, ctx, max_results=10)
        self.assertEqual(call.call_count, 4)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "pisos.com")
        self.assertEqual(items[0]["search_location"], "M0")
        self.assertEqual(items[0]["search_location_batch"], ["M0", "M1", "M2"])
        self.assertIsNone(error)

    def test_dedupe_uses_source_and_canonical_path(self):
        items = [
            {"source": "pisos.com", "source_url": "https://pisos.com/a/1?utm=x"},
            {"source": "pisos.com", "source_url": "https://pisos.com/a/1?utm=y"},
        ]
        self.assertEqual(len(_dedupe_candidates(items)), 1)
