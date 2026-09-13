"""SR0.16C offline contracts; the single detail response is always mocked."""

import json
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from .adaptive_planner import AdaptivePlanner, MODE_POLICIES
from .search_availability_semantics import classify_listing_availability
from .search_quality_semantics import result_actionability
from .services_hybrid_coverage_v261 import SOURCE_SPECS, _classify_candidate
from .services_portal_extractors import extract_same_listing_detail_attributes


FOTOCASA = next(spec for spec in SOURCE_SPECS if spec.slug == "fotocasa")
URL = "https://www.fotocasa.es/es/alquiler/vivienda/cartama/terraza/123456789/d"
CTX = {
    "max_price": 1000, "bedrooms": 3, "property_types": ["flat"],
    "location": "Cártama", "search_locations": ["Cártama"],
    "location_scope": "municipality",
}


def listing_object(*, url=URL, price=950, bedrooms=3, kind="Apartment",
                   municipality="Cártama", province="Málaga", in_stock=True):
    offer = {"@type": "Offer", "price": price}
    if in_stock:
        offer["availability"] = "https://schema.org/InStock"
    return {
        "@type": "RealEstateListing", "url": url, "offers": offer,
        "itemOffered": {
            "@type": kind, "numberOfBedrooms": bedrooms,
            "address": {"addressLocality": municipality, "addressRegion": province},
        },
    }


def html_for(obj, extra=""):
    return '<script type="application/ld+json">' + json.dumps(obj) + "</script>" + extra


def probe_for(obj, *, availability="confirmed"):
    html = html_for(obj)
    return {
        "http_status": 200, "final_url": URL, "html_len": len(html),
        "_detail_html": html, "available": availability == "confirmed",
        "availability_evidence": {
            "state": availability,
            "signal": "structured_listing_in_stock_same_listing" if availability == "confirmed" else "no_strong_positive_signal",
            "source": "structured_data" if availability == "confirmed" else "unknown",
        },
    }


def candidate(**changes):
    value = {
        "source_url": URL, "title": "Ficha", "price": None, "bedrooms": None,
        "property_type": "", "location": "", "municipality": "", "province": "",
        "provider": "deterministic",
    }
    value.update(changes)
    return value


def classify(item=None, obj=None, *, availability="confirmed"):
    response = probe_for(obj or listing_object(), availability=availability)
    with patch("apps.busquedas.services_hybrid_coverage_v261._probe_url", return_value=response):
        return _classify_candidate(
            FOTOCASA, item or candidate(), CTX, 1, targeted_detail=True,
        )


class DetailAttributeEnrichmentSR016CTests(TestCase):
    def test_unknown_price_filled_from_strong_same_listing_detail(self):
        self.assertEqual(classify()["price"], 950)

    def test_unknown_bedrooms_filled_from_strong_same_listing_detail(self):
        self.assertEqual(classify()["bedrooms"], 3)

    def test_unknown_property_type_filled_from_strong_same_listing_detail(self):
        self.assertEqual(classify()["property_type"], "flat")

    def test_unknown_location_filled_from_strong_same_listing_detail(self):
        verdict = classify()
        self.assertEqual((verdict["municipality"], verdict["province"]), ("Cártama", "Málaga"))

    def test_known_price_not_overwritten(self):
        verdict = classify(candidate(price=900), listing_object(price=975))
        self.assertEqual(verdict["price"], 900)
        self.assertTrue(verdict["detail_attribute_evidence"]["fields"]["price"]["conflict"])

    def test_known_bedrooms_not_overwritten(self):
        self.assertEqual(classify(candidate(bedrooms=4), listing_object(bedrooms=3))["bedrooms"], 4)

    def test_known_property_type_not_overwritten(self):
        self.assertEqual(classify(candidate(property_type="house"), listing_object(kind="Apartment"))["property_type"], "house")

    def test_known_location_not_overwritten(self):
        item = candidate(location="Cártama", municipality="Cártama")
        verdict = classify(item, listing_object(municipality="Málaga"))
        self.assertEqual((verdict["location"], verdict["municipality"]), ("Cártama", "Cártama"))

    def test_search_intent_not_attribute_evidence(self):
        empty = listing_object(price=None, bedrooms=None, kind="RealEstateListing", municipality="", province="")
        verdict = classify(candidate(), empty, availability="unknown")
        self.assertIsNone(verdict["price"])
        self.assertIsNone(verdict["bedrooms"])
        self.assertEqual(verdict["municipality"], "")

    def test_wrong_listing_id_no_enrichment(self):
        wrong = listing_object(url=URL.replace("123456789", "987654321"))
        verdict = classify(candidate(), wrong, availability="unknown")
        self.assertFalse(verdict["detail_attribute_evidence"]["identity_matched"])
        self.assertIsNone(verdict["price"])

    def test_recommended_listing_data_not_used(self):
        main = listing_object(price=None, bedrooms=None, kind="RealEstateListing", municipality="", province="")
        main["recommendations"] = [listing_object(url=URL.replace("123456789", "987654321"), price=700)]
        attributes = extract_same_listing_detail_attributes(html_for(main), URL, URL)
        self.assertNotIn("price", attributes["attributes"])

    def test_generic_text_not_used(self):
        html = html_for(listing_object(price=None, bedrooms=None, kind="RealEstateListing", municipality="", province=""), "Precio 500 €, 4 dormitorios, Cártama")
        attributes = extract_same_listing_detail_attributes(html, URL, URL)
        self.assertNotIn("price", attributes["attributes"])
        self.assertNotIn("bedrooms", attributes["attributes"])

    def test_detail_price_above_max_becomes_hard_rejected(self):
        verdict = classify(candidate(), listing_object(price=1500))
        self.assertEqual(verdict["classification"], "discarded")
        self.assertIn("price_above_max", verdict["reason"])

    def test_detail_bedrooms_below_min_becomes_hard_rejected(self):
        verdict = classify(candidate(), listing_object(bedrooms=2))
        self.assertIn("bedrooms_below_min", verdict["reason"])

    def test_detail_property_type_mismatch_becomes_hard_rejected(self):
        verdict = classify(candidate(), listing_object(kind="House"))
        self.assertIn("property_type_mismatch", verdict["reason"])

    def test_detail_location_outside_scope_becomes_hard_rejected(self):
        verdict = classify(candidate(), listing_object(municipality="Málaga"))
        self.assertIn("location_outside_search_scope", verdict["reason"])

    def test_all_required_attributes_resolved_removes_unknowns(self):
        self.assertNotIn("unknown_required_attribute", classify()["reason"])

    def test_attributes_resolved_but_availability_unknown_remains_reviewable(self):
        verdict = classify(availability="unknown")
        self.assertEqual(verdict["classification"], "reviewable")

    def test_attributes_resolved_and_availability_confirmed_can_verify(self):
        self.assertEqual(classify()["classification"], "verified")

    @patch("apps.busquedas.services_hybrid_coverage_v261._probe_url", return_value=probe_for(listing_object()))
    def test_no_second_http_request(self, mocked):
        _classify_candidate(FOTOCASA, candidate(), CTX, 1, targeted_detail=True)
        self.assertEqual(mocked.call_count, 1)

    def test_no_ai_credit(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, budget_consumed=0)
        before = (planner.calls_attempted, planner.consumed)
        classify()
        self.assertEqual((planner.calls_attempted, planner.consumed), before)

    def test_no_metering_change(self):
        self.assertEqual(AdaptivePlanner.CALL_COST_UNITS, Decimal("1"))

    def test_no_provider_order_change(self):
        before = [spec.slug for spec in AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1).specs]
        classify()
        after = [spec.slug for spec in AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1).specs]
        self.assertEqual(before, after)

    def test_sr015b_card_precedence_unchanged(self):
        verdict = classify(
            candidate(price=900, bedrooms=4, property_type="flat", municipality="Cártama", location="Cártama"),
            listing_object(price=950, bedrooms=3, kind="House", municipality="Málaga"),
        )
        self.assertEqual((verdict["price"], verdict["bedrooms"], verdict["property_type"]), (900, 4, "flat"))

    def test_sr015c_availability_semantics_unchanged(self):
        evidence = classify_listing_availability(
            requested_url=URL, status_code=200, final_url=URL, html="<html>generic</html>",
        )
        self.assertEqual(evidence["state"], "unknown")

    def test_sr015d_threshold_unchanged(self):
        self.assertEqual(MODE_POLICIES["eco"].sufficient_unique_candidates, 3)

    def test_no_auto_capture_unknown(self):
        self.assertFalse(result_actionability(classify(availability="unknown"))["actionable"])

    def test_no_auto_capture_hard_rejected_after_enrichment(self):
        self.assertFalse(result_actionability(classify(obj=listing_object(price=1500)))["actionable"])
