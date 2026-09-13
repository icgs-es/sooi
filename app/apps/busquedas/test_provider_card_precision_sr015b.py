"""Focused offline contracts for SR0.15B; no network/provider calls."""

from unittest import TestCase
from urllib.parse import parse_qs, urlparse

from .provider_query_provenance import query_provenance
from .search_quality_semantics import result_actionability
from .services_hybrid_coverage_v261 import (
    SOURCE_SPECS,
    SourceSpec,
    _candidate_constraint_violations,
    _deterministic_url,
)
from .services_portal_extractors import (
    candidate_from_context,
    extract_fotocasa_candidates,
    extract_habitaclia_candidates,
)


HAB_BASE = "https://www.habitaclia.com/alquiler-cartama.htm"
FOTO_BASE = "https://www.fotocasa.es/es/alquiler/viviendas/cartama/todas-las-zonas/l"


def habitaclia_html(reverse=False):
    cards = [
        '<article class="listing-card"><a href="/alquiler-piso-cartama-i101.htm">Piso en Cártama</a><span>900 €</span><span>3 habitaciones</span></article>',
        '<article class="listing-card"><a href="/alquiler-casa-pizarra-i202.htm">Casa en Pizarra</a><span>1.400 €</span><span>4 habitaciones</span></article>',
    ]
    return "".join(reversed(cards) if reverse else cards)


def fotocasa_html():
    return "".join([
        '<div class="re-card"><a href="/es/alquiler/vivienda/cartama/1234567/d">Piso en Cártama</a><span>950 €</span><span>3 habitaciones</span></div>',
        '<div class="re-card"><a href="/es/alquiler/vivienda/pizarra/7654321/d">Casa en Pizarra</a><span>1.300 €</span><span>2 habitaciones</span></div>',
    ])


def by_id(result):
    return {item["source_url"]: item for item in result["candidates"]}


class ProviderCardPrecisionSR015BTests(TestCase):
    def test_habitaclia_two_cards_no_price_leakage(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                rows = by_id(extract_habitaclia_candidates(habitaclia_html(reverse), HAB_BASE))
                self.assertEqual(rows["https://www.habitaclia.com/alquiler-piso-cartama-i101.htm"]["price"], 900)
                self.assertEqual(rows["https://www.habitaclia.com/alquiler-casa-pizarra-i202.htm"]["price"], 1400)

    def test_habitaclia_two_cards_no_bedroom_leakage(self):
        rows = by_id(extract_habitaclia_candidates(habitaclia_html(), HAB_BASE))
        self.assertEqual(rows["https://www.habitaclia.com/alquiler-piso-cartama-i101.htm"]["bedrooms"], 3)
        self.assertEqual(rows["https://www.habitaclia.com/alquiler-casa-pizarra-i202.htm"]["bedrooms"], 4)

    def test_habitaclia_two_cards_no_type_leakage(self):
        rows = by_id(extract_habitaclia_candidates(habitaclia_html(), HAB_BASE))
        self.assertEqual(rows["https://www.habitaclia.com/alquiler-piso-cartama-i101.htm"]["property_type"], "flat")
        self.assertEqual(rows["https://www.habitaclia.com/alquiler-casa-pizarra-i202.htm"]["property_type"], "house")

    def test_habitaclia_old_and_current_price_remain_card_local(self):
        html = '<article><a href="/alquiler-piso-cartama-i303.htm">Piso en Cártama</a><strong>900 €</strong><del>1.050 €</del></article>'
        item = extract_habitaclia_candidates(html, HAB_BASE)["candidates"][0]
        self.assertEqual(item["price"], 900)

    def test_fotocasa_two_cards_no_price_leakage(self):
        rows = by_id(extract_fotocasa_candidates(fotocasa_html(), FOTO_BASE))
        self.assertEqual(rows["https://www.fotocasa.es/es/alquiler/vivienda/cartama/1234567/d"]["price"], 950)
        self.assertEqual(rows["https://www.fotocasa.es/es/alquiler/vivienda/pizarra/7654321/d"]["price"], 1300)

    def test_fotocasa_two_cards_no_bedroom_leakage(self):
        rows = by_id(extract_fotocasa_candidates(fotocasa_html(), FOTO_BASE))
        self.assertEqual(rows["https://www.fotocasa.es/es/alquiler/vivienda/cartama/1234567/d"]["bedrooms"], 3)
        self.assertEqual(rows["https://www.fotocasa.es/es/alquiler/vivienda/pizarra/7654321/d"]["bedrooms"], 2)

    def test_fotocasa_two_cards_no_type_leakage(self):
        rows = by_id(extract_fotocasa_candidates(fotocasa_html(), FOTO_BASE))
        self.assertEqual(rows["https://www.fotocasa.es/es/alquiler/vivienda/cartama/1234567/d"]["property_type"], "flat")
        self.assertEqual(rows["https://www.fotocasa.es/es/alquiler/vivienda/pizarra/7654321/d"]["property_type"], "house")

    def test_habitaclia_pmax_query_unchanged(self):
        query = self._habitaclia_query()
        self.assertEqual(query["pmax"], ["1000"])

    def test_habitaclia_hab_query_unchanged(self):
        query = self._habitaclia_query()
        self.assertEqual(query["hab"], ["3"])

    def test_fotocasa_minrooms_query_unchanged(self):
        url = _deterministic_url(
            SourceSpec("fotocasa", ("fotocasa.es",), "deterministic"),
            {"operation": "rent", "location": "Cártama", "bedrooms": 3, "max_price": 1000},
        )
        self.assertEqual(parse_qs(urlparse(url).query)["minRooms"], ["3"])

    def test_location_from_candidate_evidence(self):
        item = extract_habitaclia_candidates(habitaclia_html(), HAB_BASE)["candidates"][0]
        self.assertEqual(item["municipality"], "Cártama")
        self.assertEqual(item["candidate_evidence"]["location"], "title")

    def test_location_not_from_intent(self):
        candidate = candidate_from_context(
            "habitaclia", "deterministic_habitaclia",
            "https://www.habitaclia.com/alquiler-inmueble-ref-i999.htm",
            "Anuncio disponible", "900 € · 3 habitaciones", "Cártama",
        )
        self.assertIsNone(candidate.municipality)
        self.assertEqual(candidate.candidate_evidence["location"], "unknown")

    def test_property_type_not_from_intent(self):
        candidate = candidate_from_context(
            "fotocasa", "deterministic_fotocasa",
            "https://www.fotocasa.es/es/alquiler/vivienda/sin-datos/9999999/d",
            "Anuncio disponible", "900 € · 3 habitaciones", "Cártama",
        )
        self.assertIsNone(candidate.property_type)
        self.assertEqual(candidate.candidate_evidence["property_type"], "unknown")

    def test_hard_filters_unchanged(self):
        ctx = {"max_price": 1000, "bedrooms": 3, "property_types": ["flat"]}
        self.assertIn("price_above_max:1001>1000", _candidate_constraint_violations(ctx, {"price": 1001}))
        self.assertIn("bedrooms_below_min:2<3", _candidate_constraint_violations(ctx, {"bedrooms": 2}))

    def test_sr014e_actionability_unchanged(self):
        projected = result_actionability({
            "classification": "reviewable", "reason": "availability_unknown:http_403",
            "hard_violations": [],
        })
        self.assertFalse(projected["actionable"])
        self.assertTrue(projected["review_required"])

    def test_sr015a_provenance_unchanged(self):
        url = "https://www.habitaclia.com/alquiler-cartama.htm?hab=3&pmax=1000"
        value = query_provenance("habitaclia", url, url)
        self.assertEqual(value["executed"]["safe_query"], {"hab": "3", "pmax": "1000"})
        self.assertTrue(value["executed"]["request_fingerprint"].startswith("sha256:"))

    def test_provider_order_unchanged(self):
        self.assertEqual([spec.slug for spec in SOURCE_SPECS[:3]], ["idealista", "fotocasa", "habitaclia"])

    @staticmethod
    def _habitaclia_query():
        url = _deterministic_url(
            SourceSpec("habitaclia", ("habitaclia.com",), "deterministic"),
            {"operation": "rent", "location": "Cártama", "bedrooms": 3, "max_price": 1000},
        )
        return parse_qs(urlparse(url).query)
