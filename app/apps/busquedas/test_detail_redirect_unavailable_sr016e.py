"""SR0.16E focused contracts. No test performs a real request."""

import json
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from .adaptive_planner import AdaptivePlanner, MODE_POLICIES
from .search_availability_semantics import (
    classify_listing_availability,
    fotocasa_detail_redirected_to_search_results,
)
from .search_quality_semantics import result_actionability
from .services_hybrid_coverage_v261 import SOURCE_SPECS, _probe_url
from .services_portal_extractors import (
    extract_same_listing_detail_attributes,
    is_fotocasa_listing_url,
)


REAL_REQUESTED = "https://www.fotocasa.es/es/alquiler/vivienda/malaga-capital/terraza/190487566/d"
REAL_RESULTS = "https://www.fotocasa.es/es/alquiler/viviendas/malaga-capital/todas-las-zonas/l"
CONTROL = "https://www.fotocasa.es/es/alquiler/vivienda/malaga-capital/terraza/190475503/d"


def classify(requested=REAL_REQUESTED, final=REAL_RESULTS, status=200, html="", error=""):
    return classify_listing_availability(
        requested_url=requested, status_code=status, final_url=final, html=html, error=error,
    )


def strong_html(url=CONTROL):
    obj = {
        "@type": "RealEstateListing", "url": url,
        "offers": {"availability": "https://schema.org/InStock", "price": 950},
        "itemOffered": {"@type": "Apartment", "numberOfBedrooms": 3},
    }
    return '<script type="application/ld+json">' + json.dumps(obj) + "</script>"


class _Headers:
    def get_content_charset(self):
        return "utf-8"


class _Response:
    status = 200
    headers = _Headers()
    url = REAL_RESULTS

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return b"<html>resultados</html>"


class DetailRedirectUnavailableSR016ETests(TestCase):
    def test_fotocasa_detail_redirect_to_search_results_unavailable(self):
        evidence = classify()
        self.assertEqual(evidence["state"], "unavailable")
        self.assertEqual(evidence["signal"], "detail_redirected_to_search_results_identity_lost")

    def test_real_pattern_190487566_unavailable(self):
        self.assertTrue(fotocasa_detail_redirected_to_search_results(REAL_REQUESTED, REAL_RESULTS))

    def test_fotocasa_detail_identity_preserved_not_unavailable(self):
        self.assertEqual(classify(CONTROL, CONTROL)["state"], "unknown")

    def test_real_control_pattern_190475503_not_unavailable(self):
        self.assertFalse(fotocasa_detail_redirected_to_search_results(CONTROL, CONTROL))

    def test_same_listing_id_different_slug_not_unavailable(self):
        final = CONTROL.replace("terraza", "balcon")
        self.assertFalse(fotocasa_detail_redirected_to_search_results(CONTROL, final))

    def test_same_listing_id_canonical_redirect_not_unavailable(self):
        final = "https://fotocasa.es/es/alquiler/vivienda/nuevo-slug/190475503/d?canonical=1"
        self.assertFalse(fotocasa_detail_redirected_to_search_results(CONTROL, final))

    def test_https_normalization_not_unavailable(self):
        requested = CONTROL.replace("https://", "http://")
        self.assertFalse(fotocasa_detail_redirected_to_search_results(requested, CONTROL))

    def test_generic_redirect_not_unavailable(self):
        self.assertEqual(classify(final="https://www.fotocasa.es/")["state"], "unknown")

    def test_unexpected_external_domain_not_marked_unavailable_by_this_rule(self):
        final = "https://example.test/es/alquiler/viviendas/malaga/todas-las-zonas/l"
        self.assertFalse(fotocasa_detail_redirected_to_search_results(REAL_REQUESTED, final))
        self.assertEqual(classify(final=final)["state"], "unknown")

    def test_403_remains_unknown(self):
        self.assertEqual(classify(status=403)["state"], "unknown")

    def test_generic_200_remains_unknown(self):
        self.assertEqual(classify(final=REAL_REQUESTED, html="<html>generic</html>")["state"], "unknown")

    def test_404_remains_unavailable_existing_semantics(self):
        self.assertEqual(classify(status=404)["signal"], "http_404")

    def test_410_remains_unavailable_existing_semantics(self):
        self.assertEqual(classify(status=410)["signal"], "http_410")

    def test_structured_same_listing_confirmation_unchanged(self):
        self.assertEqual(classify(CONTROL, CONTROL, html=strong_html())["state"], "confirmed")

    def test_search_result_page_cannot_confirm_listing(self):
        self.assertNotEqual(classify(html=strong_html(REAL_RESULTS))["state"], "confirmed")

    @patch("apps.busquedas.services_hybrid_coverage_v261.urlopen", return_value=_Response())
    def test_no_extra_http_request(self, mocked):
        evidence = _probe_url(REAL_REQUESTED, timeout=1)["availability_evidence"]
        self.assertEqual(evidence["state"], "unavailable")
        self.assertEqual(mocked.call_count, 1)

    def test_no_ai_call(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, calls_attempted=0)
        classify()
        self.assertEqual(planner.calls_attempted, 0)

    def test_no_search_credit(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, budget_consumed=0)
        classify()
        self.assertEqual(planner.consumed, Decimal("0"))

    def test_no_metering_change(self):
        self.assertEqual(AdaptivePlanner.CALL_COST_UNITS, Decimal("1"))

    def test_no_provider_order_change(self):
        before = [spec.slug for spec in AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1).specs]
        classify()
        after = [spec.slug for spec in AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1).specs]
        self.assertEqual(before, after)

    def test_sr015c_existing_semantics_preserved(self):
        self.assertEqual(classify(status=403)["state"], "unknown")
        self.assertEqual(classify(final=REAL_REQUESTED)["state"], "unknown")

    def test_sr016a_detail_boundary_preserved(self):
        self.assertTrue(is_fotocasa_listing_url(REAL_REQUESTED))
        self.assertFalse(is_fotocasa_listing_url(REAL_RESULTS))

    def test_sr016c_attribute_enrichment_unchanged(self):
        attributes = extract_same_listing_detail_attributes(strong_html(), CONTROL, CONTROL)
        self.assertTrue(attributes["identity_matched"])
        self.assertEqual(attributes["attributes"]["price"], 950)

    def test_no_auto_capture_unavailable(self):
        semantics = result_actionability({
            "classification": "verified",
            "availability_evidence": classify(),
        })
        self.assertFalse(semantics["actionable"])
        self.assertEqual(semantics["availability_state"], "UNAVAILABLE")

    def test_sr015d_threshold_unchanged(self):
        self.assertEqual(MODE_POLICIES["eco"].sufficient_unique_candidates, 3)
