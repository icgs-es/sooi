from unittest.mock import patch
from django.test import SimpleTestCase

from apps.busquedas.services import (
    _sr0_is_fotocasa_generic_unavailable,
    _sr0_is_low_coverage,
)
from apps.busquedas.services_hybrid_coverage_v261 import (
    SourceSpec,
    _candidate_constraint_violations,
    _classify_candidate,
    _postprocess_strict_quality_gate_v261,
    _looks_like_listing_url_v261,
    _sr0_is_known_antibot_403,
    _sr0_postprocess_fotocasa_inconclusive,
    _sr0_repair_search_locations,
)


class SR0SearchRecoveryPureTests(SimpleTestCase):
    def test_explicit_multi_location_is_split(self):
        ctx = {
            "location": (
                "Cartama, Alhaurin de la Torre, Campanillas, Puerto la Torre, "
                "Torremolinos, Estacion de Cartama, Málaga, Churriana, Benalmadena"
            ),
            "location_scope": "municipality",
            "search_locations": [],
        }
        _sr0_repair_search_locations(ctx)
        self.assertEqual(ctx["location_scope"], "multi_location")
        self.assertEqual(ctx["search_locations_count"], 9)
        self.assertIn("Málaga", ctx["search_locations"])
        self.assertIn("Benalmadena", ctx["search_locations"])

    def test_los_pedroches_expands_to_comarca(self):
        ctx = {
            "location": "Los Pedroches",
            "location_scope": "municipality",
            "search_locations": ["Los Pedroches"],
        }
        _sr0_repair_search_locations(ctx)
        self.assertEqual(ctx["location_scope"], "comarca")
        self.assertGreaterEqual(ctx["search_locations_count"], 15)
        for municipality in (
            "Pozoblanco",
            "Hinojosa del Duque",
            "Pedroche",
            "Villanueva de Córdoba",
            "Dos Torres",
        ):
            self.assertIn(municipality, ctx["search_locations"])

    def test_pisos_sale_detail_url_is_not_listing(self):
        url = (
            "https://www.pisos.com/comprar/"
            "piso-pozoblanco_centro_urbano-65028421936_100500/"
        )
        self.assertFalse(_looks_like_listing_url_v261("pisos.com", url))

    def test_pisos_sale_detail_url_with_query_is_not_listing(self):
        url = (
            "https://www.pisos.com/comprar/"
            "piso-pozoblanco_centro_urbano-65028421936_100500/?utm_source=x"
        )
        self.assertFalse(_looks_like_listing_url_v261("pisos.com", url))

    def test_terrenos_detail_with_query_is_not_listing(self):
        url = (
            "https://cordoba.terrenos.es/venta/pedroche/urbano/317138"
            "?is_residential=1&offer_type=1&settlement=6286"
        )
        self.assertFalse(_looks_like_listing_url_v261("terrenos.es", url))

    def test_terrenos_numeric_listing_is_not_assumed_to_be_detail(self):
        url = "https://cordoba.terrenos.es/venta/2?order=price"
        self.assertTrue(_looks_like_listing_url_v261("terrenos.es", url))

    def test_near_zero_coverage_is_not_normal_success(self):
        totals = {"attempted": 9, "failed": 2, "discarded": 12}
        self.assertTrue(_sr0_is_low_coverage(totals, total_candidates=13, total_valid=1))

    def test_clean_zero_result_coverage_is_not_forced_to_error(self):
        totals = {"attempted": 9, "failed": 0, "discarded": 0}
        self.assertFalse(_sr0_is_low_coverage(totals, total_candidates=0, total_valid=0))

    def test_fotocasa_http_200_generic_text_is_inconclusive(self):
        url = "https://www.fotocasa.es/es/venta/vivienda/malaga/x/1/d"
        self.assertTrue(_sr0_is_fotocasa_generic_unavailable(
            url, 200, "este anuncio no está disponible"
        ))
        self.assertFalse(_sr0_is_fotocasa_generic_unavailable(url, 404, "anuncio no disponible"))
        self.assertFalse(_sr0_is_fotocasa_generic_unavailable(
            url, 200, "este anuncio ya no está publicado"
        ))

    def test_known_antibot_403_sources_are_inconclusive(self):
        probe = {
            "http_status": 403,
            "error": "HTTPError: 403",
            "available": False,
        }
        self.assertTrue(_sr0_is_known_antibot_403("idealista", probe))
        self.assertTrue(_sr0_is_known_antibot_403("yaencontre", probe))
        self.assertTrue(_sr0_is_known_antibot_403("terrenos.es", probe))
        self.assertFalse(_sr0_is_known_antibot_403("fotocasa", probe))

    def test_known_hard_constraint_violations_are_rejected(self):
        ctx = {
            "max_price": 60000,
            "bedrooms": 3,
            "min_area": 80,
            "property_types": ["house", "flat"],
            "location_scope": "comarca",
            "search_locations": ["Pedroche", "Hinojosa del Duque"],
        }
        item = {
            "title": "Local en Córdoba capital",
            "location": "Córdoba",
            "municipality": "Córdoba",
            "url": "https://example.test/inmueble/1",
            "price": 55000,
            "bedrooms": 2,
            "area_m2": 70,
            "property_type": "commercial",
        }
        violations = _candidate_constraint_violations(ctx, item)
        self.assertIn("bedrooms_below_min:2<3", violations)
        self.assertIn("area_below_min:70<80", violations)
        self.assertIn("property_type_mismatch:commercial", violations)
        self.assertIn("location_outside_search_scope", violations)

    def test_comarca_candidate_inside_scope_passes_geography(self):
        ctx = {
            "property_types": ["house", "flat"],
            "location_scope": "comarca",
            "search_locations": ["Pedroche", "Hinojosa del Duque"],
        }
        item = {
            "title": "Casa en Hinojosa del Duque",
            "location": "Hinojosa del Duque, Córdoba",
            "municipality": "Hinojosa del Duque",
            "property_type": "casa",
        }
        self.assertEqual(_candidate_constraint_violations(ctx, item), [])

    def test_fotocasa_http_200_generic_unavailable_becomes_reviewable(self):
        coverage = [{
            "source": "fotocasa",
            "candidates": [{
                "url": "https://www.fotocasa.es/es/alquiler/vivienda/cartama/x/1/d",
                "title": "Piso en Cártama",
                "location": "Cártama",
                "price": 775,
                "classification": "discarded",
                "reason": "no está disponible",
                "probe": {
                    "http_status": 200,
                    "available": False,
                    "unavailable_reason": "no está disponible",
                    "error": None,
                },
            }],
        }]
        ctx = {
            "operation": "rent",
            "min_price": 700,
            "max_price": 1000,
            "bedrooms": None,
            "min_area": None,
            "property_types": ["house", "flat"],
            "location": "Cártama",
            "location_scope": "multi_location",
            "search_locations": ["Cártama"],
        }
        _sr0_postprocess_fotocasa_inconclusive(coverage, ctx)
        self.assertEqual(coverage[0]["candidates"][0]["classification"], "reviewable")

    def test_fotocasa_above_max_remains_discarded(self):
        coverage = [{
            "source": "fotocasa",
            "candidates": [{
                "url": "https://www.fotocasa.es/es/alquiler/vivienda/cartama/x/2/d",
                "title": "Piso en Cártama",
                "location": "Cártama",
                "price": 1200,
                "classification": "discarded",
                "reason": "no está disponible",
                "probe": {
                    "http_status": 200,
                    "available": False,
                    "unavailable_reason": "no está disponible",
                    "error": None,
                },
            }],
        }]
        ctx = {
            "operation": "rent",
            "min_price": 700,
            "max_price": 1000,
            "bedrooms": None,
            "min_area": None,
            "property_types": ["house", "flat"],
            "location": "Cártama",
            "location_scope": "multi_location",
            "search_locations": ["Cártama"],
        }
        _sr0_postprocess_fotocasa_inconclusive(coverage, ctx)
        self.assertEqual(coverage[0]["candidates"][0]["classification"], "discarded")

    def test_http200_bare_404_text_is_reviewable_when_hard_constraints_pass(self):
        coverage = [{
            "source": "milanuncios",
            "method": "openai_web_search",
            "candidates": [{
                "url": "https://www.milanuncios.com/alquiler-de-casas/casa-en-campanillas-123456789.htm",
                "title": "Casa en Campanillas",
                "location": "Campanillas, Málaga",
                "municipality": "Campanillas",
                "price": 1000,
                "bedrooms": 3,
                "property_type": "house",
                "provider": "openai_web_search",
                "classification": "discarded",
                "probe": {"http_status": 200, "available": False, "unavailable_reason": "404", "error": None},
            }],
        }]
        ctx = {
            "min_price": 700, "max_price": 1000, "bedrooms": 3,
            "property_types": ["house", "flat"], "location_scope": "multi_location",
            "search_locations": ["Campanillas", "Málaga"],
        }
        _postprocess_strict_quality_gate_v261(coverage, ctx)
        self.assertEqual(coverage[0]["candidates"][0]["classification"], "reviewable")

    def test_http200_bare_404_text_does_not_override_hard_geography_mismatch(self):
        coverage = [{
            "source": "terrenos.es",
            "method": "openai_web_search",
            "candidates": [{
                "url": "https://malaga.terrenos.es/alquiler/frigiliana/urbano/1",
                "title": "Casa en Frigiliana",
                "location": "Frigiliana",
                "municipality": "Frigiliana",
                "price": 1000,
                "bedrooms": 5,
                "property_type": "house",
                "provider": "openai_web_search",
                "classification": "discarded",
                "probe": {"http_status": 200, "available": False, "unavailable_reason": "404", "error": None},
            }],
        }]
        ctx = {
            "min_price": 700, "max_price": 1000, "bedrooms": 3,
            "property_types": ["house", "flat"], "location_scope": "multi_location",
            "search_locations": ["Campanillas", "Málaga"],
        }
        _postprocess_strict_quality_gate_v261(coverage, ctx)
        candidate = coverage[0]["candidates"][0]
        self.assertEqual(candidate["classification"], "discarded")
        self.assertEqual(candidate["reason"], "location_outside_search_scope")

    def test_definitive_http404_and_withdrawn_text_remain_discarded(self):
        base = {
            "url": "https://www.milanuncios.com/alquiler-de-casas/casa-en-campanillas-123456789.htm",
            "title": "Casa en Campanillas", "location": "Campanillas",
            "municipality": "Campanillas", "price": 900,
            "bedrooms": 3, "property_type": "house", "provider": "openai_web_search",
            "classification": "reviewable",
        }
        candidates = [
            {**base, "probe": {"http_status": 404, "available": False, "unavailable_reason": None, "error": "HTTPError: 404"}},
            {**base, "probe": {"http_status": 200, "available": False, "unavailable_reason": "ya no está publicado", "error": None}},
        ]
        coverage = [{"source": "milanuncios", "method": "openai_web_search", "candidates": candidates}]
        ctx = {
            "min_price": 700, "max_price": 1000, "bedrooms": 3,
            "property_types": ["house", "flat"], "location_scope": "multi_location",
            "search_locations": ["Campanillas", "Málaga"],
        }
        _postprocess_strict_quality_gate_v261(coverage, ctx)
        self.assertEqual([item["classification"] for item in candidates], ["discarded", "discarded"])
        self.assertEqual(candidates[1]["reason"], "ai_probe_unavailable:ya no está publicado")

    @patch("apps.busquedas.services_hybrid_coverage_v261._probe_url")
    def test_classification_preserves_search_location_traceability(self, probe):
        probe.return_value = {"http_status": 403, "available": False, "unavailable_reason": None, "error": "HTTPError: 403"}
        item = {
            "source_url": "https://www.idealista.com/inmueble/1/",
            "title": "Piso en Málaga", "location": "Málaga", "price": 950,
            "bedrooms": 3, "property_type": "flat", "provider": "openai_web_search",
            "search_location": "Málaga", "search_location_batch": ["Málaga", "Churriana"],
        }
        verdict = _classify_candidate(
            SourceSpec("idealista", ("idealista.com",), "openai_web_search"),
            item,
            {"min_price": 700, "max_price": 1000, "bedrooms": 3, "property_types": ["house", "flat"]},
            timeout=1,
        )
        self.assertEqual(verdict["search_location"], "Málaga")
        self.assertEqual(verdict["search_location_batch"], ["Málaga", "Churriana"])
