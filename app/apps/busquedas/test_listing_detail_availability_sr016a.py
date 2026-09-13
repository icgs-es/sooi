"""Focused offline contracts for SR0.16A; every detail request is mocked."""

from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from .adaptive_planner import AdaptivePlanner, MODE_POLICIES
from .search_availability_semantics import classify_listing_availability
from .search_quality_semantics import result_actionability
from .services_hybrid_coverage_v261 import (
    SOURCE_SPECS,
    _candidate_constraint_violations,
    _classify_candidate,
    _classify_targeted_detail_candidates,
)


FOTOCASA = next(spec for spec in SOURCE_SPECS if spec.slug == "fotocasa")
DETAIL = "https://www.fotocasa.es/es/alquiler/vivienda/cartama/terraza/123456789/d"
SEARCH = "https://www.fotocasa.es/es/alquiler/viviendas/cartama/todas-las-zonas/l"
CTX = {"max_price": 1000, "bedrooms": 3, "property_types": ["flat"]}


def candidate(**changes):
    value = {
        "source_url": DETAIL, "title": "Piso en Cártama", "price": 950,
        "bedrooms": 3, "property_type": "flat", "municipality": "Cártama",
        "provider": "deterministic",
    }
    value.update(changes)
    return value


def probe(status=200, state="unknown", signal="no_strong_positive_signal", final_url=DETAIL):
    return {
        "http_status": status, "final_url": final_url, "available": state == "confirmed",
        "availability_evidence": {"state": state, "signal": signal, "source": "fixture"},
    }


def strong_html(url=DETAIL):
    return f'''<script type="application/ld+json">{{"@type":"RealEstateListing",
      "url":"{url}","offers":{{"availability":"https://schema.org/InStock"}}}}</script>'''


class ListingDetailAvailabilitySR016ATests(TestCase):
    @patch("apps.busquedas.services_hybrid_coverage_v261._probe_url")
    def test_hard_rejected_candidate_not_probed(self, mocked):
        verdict = _classify_candidate(FOTOCASA, candidate(price=1001), CTX, 1, targeted_detail=True)
        mocked.assert_not_called()
        self.assertEqual(verdict["classification"], "discarded")

    @patch("apps.busquedas.services_hybrid_coverage_v261._probe_url", return_value=probe())
    def test_hard_clean_unknown_candidate_eligible(self, mocked):
        verdict = _classify_candidate(FOTOCASA, candidate(), CTX, 1, targeted_detail=True)
        mocked.assert_called_once_with(DETAIL, timeout=1)
        self.assertTrue(verdict["availability_probe"]["attempted"])

    @patch("apps.busquedas.services_hybrid_coverage_v261._probe_url")
    def test_detail_url_required(self, mocked):
        verdict = _classify_candidate(FOTOCASA, candidate(source_url=SEARCH), CTX, 1, targeted_detail=True)
        mocked.assert_not_called()
        self.assertIn("detail_url_required", verdict["reason"])

    def test_search_results_url_not_used_as_listing_confirmation(self):
        evidence = classify_listing_availability(
            requested_url=SEARCH, status_code=200, final_url=SEARCH, html=strong_html(SEARCH),
        )
        self.assertNotEqual(evidence["state"], "confirmed")

    def _verdict(self, response):
        with patch("apps.busquedas.services_hybrid_coverage_v261._probe_url", return_value=response):
            return _classify_candidate(FOTOCASA, candidate(), CTX, 1, targeted_detail=True)

    def test_detail_http_403_unknown(self):
        self.assertEqual(self._verdict(probe(403))["availability_evidence"]["state"], "unknown")

    def test_detail_http_404_unavailable(self):
        self.assertEqual(self._verdict(probe(404, "unavailable", "http_404"))["classification"], "discarded")

    def test_detail_http_410_unavailable(self):
        self.assertEqual(self._verdict(probe(410, "unavailable", "http_410"))["classification"], "discarded")

    def test_detail_generic_200_unknown(self):
        self.assertEqual(self._verdict(probe())["classification"], "reviewable")

    def test_detail_strong_same_listing_jsonld_confirmed(self):
        evidence = classify_listing_availability(
            requested_url=DETAIL, status_code=200, final_url=DETAIL, html=strong_html(),
        )
        self.assertEqual(evidence["state"], "confirmed")

    def test_detail_wrong_listing_id_not_confirmed(self):
        wrong = DETAIL.replace("123456789", "987654321")
        evidence = classify_listing_availability(
            requested_url=DETAIL, status_code=200, final_url=DETAIL, html=strong_html(wrong),
        )
        self.assertEqual(evidence["state"], "unknown")

    def test_detail_redirect_home_unknown(self):
        self.assertEqual(self._verdict(probe(final_url="https://www.fotocasa.es/"))["classification"], "reviewable")

    @patch("apps.busquedas.services_hybrid_coverage_v261._probe_url", return_value=probe())
    def test_detail_request_deduplicated(self, mocked):
        cache = {}
        _classify_candidate(FOTOCASA, candidate(), CTX, 1, probe_cache=cache, targeted_detail=True)
        second = _classify_candidate(FOTOCASA, candidate(), CTX, 1, probe_cache=cache, targeted_detail=True)
        self.assertEqual(mocked.call_count, 1)
        self.assertTrue(second["availability_probe"]["deduplicated"])

    @patch("apps.busquedas.services_hybrid_coverage_v261._probe_url", return_value=probe())
    def test_detail_verification_bounded(self, mocked):
        rows = [candidate(source_url=DETAIL.replace("123456789", str(123456789 + i))) for i in range(4)]
        _classify_targeted_detail_candidates(FOTOCASA, rows, CTX, 1, probe_cache={}, cap=2)
        self.assertEqual(mocked.call_count, 2)

    @patch("apps.busquedas.services_hybrid_coverage_v261._probe_url", return_value=probe())
    def test_detail_verification_does_not_consume_ai_credit(self, _mocked):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, budget_consumed=0)
        before = (planner.calls_attempted, planner.consumed)
        _classify_candidate(FOTOCASA, candidate(), CTX, 1, targeted_detail=True)
        self.assertEqual((planner.calls_attempted, planner.consumed), before)

    def _confirmed_verdict(self):
        return self._verdict(probe(200, "confirmed", "structured_listing_in_stock_same_listing"))

    def test_price_unchanged(self):
        self.assertEqual(self._confirmed_verdict()["price"], candidate()["price"])

    def test_bedrooms_unchanged(self):
        self.assertEqual(self._confirmed_verdict()["bedrooms"], candidate()["bedrooms"])

    def test_property_type_unchanged(self):
        self.assertEqual(self._confirmed_verdict()["property_type"], candidate()["property_type"])

    def test_location_unchanged(self):
        self.assertEqual(self._confirmed_verdict()["municipality"], candidate()["municipality"])

    def test_sr015c_availability_semantics_unchanged(self):
        cases = [(403, "unknown"), (404, "unavailable"), (410, "unavailable"), (200, "unknown")]
        for status, expected in cases:
            evidence = classify_listing_availability(
                requested_url=DETAIL, status_code=status, final_url=DETAIL, html="<html>generic</html>",
            )
            self.assertEqual(evidence["state"], expected)

    def test_sr015d_ai_allowed_needed_contract_unchanged(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, budget_consumed=0)
        self.assertTrue(planner.ai_call_allowed)
        self.assertTrue(planner.efficiency_observability()["ai_call_needed"])
        self.assertEqual(MODE_POLICIES["eco"].sufficient_unique_candidates, 3)

    def test_actionable_only_after_confirmed(self):
        unknown = self._verdict(probe())
        confirmed = self._verdict(probe(200, "confirmed", "structured_listing_in_stock_same_listing"))
        self.assertFalse(result_actionability(unknown)["actionable"])
        self.assertTrue(result_actionability(confirmed)["actionable"])

    def test_unknown_remains_review_required(self):
        self.assertTrue(result_actionability(self._verdict(probe()))["review_required"])

    def test_unavailable_not_actionable(self):
        semantics = result_actionability(self._verdict(probe(404, "unavailable", "http_404")))
        self.assertFalse(semantics["actionable"])

    def test_no_auto_capture_unknown(self):
        semantics = result_actionability(self._verdict(probe()))
        self.assertEqual((semantics["actionable"], semantics["review_required"]), (False, True))

    def test_provider_order_unchanged(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1)
        expected = [spec.slug for spec in sorted(SOURCE_SPECS, key=lambda s: ({"deterministic": 0, "ai_efficient": 1, "ai_expansion": 2}.get(s.tier or s.method, 0), s.priority, s.slug))]
        self.assertEqual([spec.slug for spec in planner.specs], expected)

    def test_credit_calculation_unchanged(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, budget_consumed=0)
        self.assertEqual(planner.CALL_COST_UNITS, Decimal("1"))
        self.assertTrue(planner.before_external_call())
        self.assertFalse(planner.before_external_call())
        self.assertEqual(planner.consumed, Decimal("1"))

    def test_metering_unchanged(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, calls_attempted=0)
        self.assertEqual(planner.calls_attempted, 0)
        _classify_candidate(
            FOTOCASA, candidate(), CTX, 1,
            allow_detail_probe=False, targeted_detail=True,
        )
        self.assertEqual(planner.calls_attempted, 0)

    def test_hard_filters_unchanged(self):
        self.assertIn("price_above_max:1001>1000", _candidate_constraint_violations(CTX, candidate(price=1001)))
