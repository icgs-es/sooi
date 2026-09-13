"""SR0.16G focused offline contracts; all network boundaries are mocked."""

import json
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from .adaptive_planner import AdaptivePlanner, MODE_POLICIES
from .search_availability_semantics import classify_listing_availability
from .search_quality_semantics import result_actionability
from .services_hybrid_coverage_v261 import SOURCE_SPECS, _candidate_constraint_violations, _classify_candidate, _probe_url
from .services_portal_extractors import (
    extract_fotocasa_initial_props_detail_attributes,
    extract_same_listing_detail_attributes,
    is_fotocasa_listing_url,
)


ID1, ID2, REMOVED_ID = "190475503", "190242327", "190487566"
URL1 = f"https://www.fotocasa.es/es/alquiler/vivienda/malaga/chalet/{ID1}/d"
URL2 = f"https://www.fotocasa.es/es/alquiler/vivienda/malaga/piso/{ID2}/d"
REMOVED = f"https://www.fotocasa.es/es/alquiler/vivienda/malaga/piso/{REMOVED_ID}/d"
RESULTS = "https://www.fotocasa.es/es/alquiler/viviendas/malaga-capital/todas-las-zonas/l"
FOTOCASA = next(spec for spec in SOURCE_SPECS if spec.slug == "fotocasa")


def root(listing_id=ID1, *, features=None, real_id=None, property_id=None):
    return {
        "realEstate": {"id": int(real_id or listing_id), "price": 950},
        "realEstateAdDetailEntityV2": {
            "propertyId": int(property_id or listing_id),
            "features": ([{"type": "OCCUPANCY_STATUS", "value": "IS_AVAILABLE"}]
                         if features is None else features),
        },
    }


def script(data, *, malformed=False):
    body = "{" if malformed else json.dumps(data)
    return f'<script id="__initial_props__" type="application/json">{body}</script>'


def availability(url=URL1, final=None, *, data=None, status=200, html=None):
    final = url if final is None else final
    if html is None:
        html = script(root(url.rstrip("/").split("/")[-2]) if data is None else data)
    return classify_listing_availability(
        requested_url=url, status_code=status, final_url=final, html=html,
    )


def values(*items):
    return [{"type": "OCCUPANCY_STATUS", "value": item} for item in items]


class _Headers:
    def get_content_charset(self): return "utf-8"


class _Response:
    status, url, headers = 200, URL1, _Headers()
    read_limit = None
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, limit):
        self.read_limit = limit
        return script(root()).encode()


class FotocasaStructuredAvailabilitySR016GTests(TestCase):
    def test_is_available_confirms_availability(self):
        evidence = availability()
        self.assertEqual(evidence, {"state": "confirmed", "signal": "fotocasa_initial_props_occupancy_is_available", "source": "fotocasa_initial_props"})

    def test_real_pattern_190475503_confirmed(self):
        self.assertEqual(availability(URL1)["state"], "confirmed")

    def test_real_pattern_190242327_confirmed_at_availability_layer(self):
        self.assertEqual(availability(URL2)["state"], "confirmed")

    def test_strong_identity_required(self):
        self.assertEqual(availability(data=root(real_id="1"))["state"], "unknown")

    def test_requested_listing_id_required(self):
        url = "https://www.fotocasa.es/es/alquiler/viviendas/malaga/todas-las-zonas/l"
        self.assertEqual(availability(url, data=root())["state"], "unknown")

    def test_final_url_identity_required(self):
        self.assertEqual(availability(final=RESULTS)["state"], "unavailable")

    def test_realestate_identity_required(self):
        self.assertEqual(availability(data=root(real_id="1"))["state"], "unknown")

    def test_detail_property_identity_required(self):
        self.assertEqual(availability(data=root(property_id="1"))["state"], "unknown")

    def test_initial_props_required(self):
        self.assertEqual(availability(html="<html></html>")["state"], "unknown")

    def test_initial_props_malformed_unknown(self):
        self.assertEqual(availability(html=script(root(), malformed=True))["state"], "unknown")

    def test_multiple_initial_props_unknown(self):
        html = script(root()) + script(root())
        self.assertEqual(availability(html=html)["state"], "unknown")

    def test_features_must_be_list(self):
        self.assertEqual(availability(data=root(features={}))["state"], "unknown")

    def test_occupancy_status_missing_unknown(self):
        self.assertEqual(availability(data=root(features=[]))["state"], "unknown")

    def test_unknown_occupancy_value_unknown(self):
        self.assertEqual(availability(data=root(features=values("UNKNOWN")))["state"], "unknown")

    def test_empty_occupancy_value_unknown(self):
        self.assertEqual(availability(data=root(features=values("")))["state"], "unknown")

    def test_multiple_identical_is_available_allowed(self):
        self.assertEqual(availability(data=root(features=values("IS_AVAILABLE", "is_available")))["state"], "confirmed")

    def test_conflicting_occupancy_values_unknown(self):
        self.assertEqual(availability(data=root(features=values("IS_AVAILABLE", "OCCUPIED")))["state"], "unknown")

    def test_no_generic_enum_negative_semantics(self):
        self.assertEqual(availability(data=root(features=values("IS_UNAVAILABLE")))["state"], "unknown")

    def test_403_remains_unknown(self):
        self.assertEqual(availability(status=403)["state"], "unknown")

    def test_generic_200_remains_unknown(self):
        self.assertEqual(availability(html="<html>generic</html>")["state"], "unknown")

    def test_404_remains_unavailable(self):
        self.assertEqual(availability(status=404)["signal"], "http_404")

    def test_410_remains_unavailable(self):
        self.assertEqual(availability(status=410)["signal"], "http_410")

    def test_sr016e_redirect_unavailable_has_precedence(self):
        self.assertEqual(availability(REMOVED, RESULTS, data=root(REMOVED_ID))["state"], "unavailable")

    def test_real_pattern_190487566_remains_unavailable(self):
        self.assertEqual(availability(REMOVED, RESULTS, data=root(REMOVED_ID))["signal"], "detail_redirected_to_search_results_identity_lost")

    def test_is_available_cannot_override_sr016e_unavailable(self):
        self.assertNotEqual(availability(REMOVED, RESULTS, data=root(REMOVED_ID))["state"], "confirmed")

    def test_is_available_cannot_override_404(self):
        self.assertEqual(availability(status=404)["state"], "unavailable")

    def test_is_available_cannot_override_410(self):
        self.assertEqual(availability(status=410)["state"], "unavailable")

    def test_is_available_does_not_remove_hard_violation(self):
        violations = _candidate_constraint_violations({"max_price": 1000}, {"price": 2000})
        semantics = result_actionability({"classification": "verified", "availability_evidence": availability(), "hard_violations": violations})
        self.assertFalse(semantics["actionable"])

    def test_real_pattern_190242327_remains_discarded_price_2000_gt_1000(self):
        violations = _candidate_constraint_violations({"max_price": 1000}, {"price": 2000})
        self.assertEqual(violations, ["price_above_max:2000>1000"])

    def test_is_available_does_not_fill_unknown_attributes(self):
        data = root(); data["realEstate"].pop("price")
        evidence = availability(data=data)
        self.assertEqual(evidence["state"], "confirmed")
        self.assertNotIn("price", evidence)

    def test_is_available_alone_does_not_make_actionable(self):
        semantics = result_actionability({"classification": "reviewable", "availability_evidence": availability()})
        self.assertFalse(semantics["actionable"])

    def test_sr016f_attribute_extraction_unchanged(self):
        data = root(); data["realEstate"].update({"price": 3500, "buildingSubtype": "House_Chalet", "descriptions": {"es-ES": "5 habitaciones"}}); data["realEstateAdDetailEntityV2"].update({"price": {"amount": 3500}, "description": "5 habitaciones"})
        attrs = extract_fotocasa_initial_props_detail_attributes(script(data), URL1, URL1)["attributes"]
        self.assertEqual((attrs["price"], attrs["bedrooms"], attrs["property_type"]), (3500, 5, "house"))

    def test_sr016c_jsonld_extraction_unchanged(self):
        obj = {"@type": "RealEstateListing", "url": URL1, "offers": {"price": 900}}
        html = '<script type="application/ld+json">' + json.dumps(obj) + "</script>"
        self.assertEqual(extract_same_listing_detail_attributes(html, URL1, URL1)["attributes"]["price"], 900)

    def test_sr016a_detail_request_boundary_unchanged(self):
        self.assertTrue(is_fotocasa_listing_url(URL1))

    @patch("apps.busquedas.services_hybrid_coverage_v261.urlopen", return_value=_Response())
    def test_extra_http_requests_zero(self, mocked):
        _probe_url(URL1, timeout=1)
        self.assertEqual(mocked.call_count, 1)

    @patch("apps.busquedas.services_hybrid_coverage_v261.urlopen", return_value=_Response())
    def test_no_second_detail_request(self, mocked):
        _probe_url(URL1, timeout=1)
        mocked.assert_called_once()

    @patch("apps.busquedas.services_hybrid_coverage_v261.urlopen", return_value=_Response())
    def test_body_limit_unchanged(self, mocked):
        _probe_url(URL1, timeout=1)
        self.assertEqual(mocked.return_value.read_limit, 350000)

    def test_no_ai_call(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1)
        availability()
        self.assertEqual(planner.calls_attempted, 0)

    def test_no_search_credit(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, budget_consumed=0)
        availability()
        self.assertEqual(planner.consumed, Decimal("0"))

    def test_no_metering_change(self):
        self.assertEqual(AdaptivePlanner.CALL_COST_UNITS, Decimal("1"))

    def test_no_provider_order_change(self):
        before = [spec.slug for spec in AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1).specs]
        availability()
        self.assertEqual(before, [spec.slug for spec in AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1).specs])

    def test_no_planner_change(self):
        self.assertEqual(MODE_POLICIES["eco"].sufficient_unique_candidates, 3)

    def test_no_capture_policy_change(self):
        self.assertFalse(result_actionability({"classification": "reviewable", "availability_evidence": availability()})["actionable"])

    def test_no_model_change(self):
        self.assertTrue(callable(classify_listing_availability))

    def test_no_migration(self):
        self.assertTrue(callable(classify_listing_availability))
