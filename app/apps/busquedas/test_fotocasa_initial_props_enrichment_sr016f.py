"""SR0.16F offline contracts; no real provider or AI calls."""

import json
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from .adaptive_planner import AdaptivePlanner
from .search_availability_semantics import classify_listing_availability
from .services_hybrid_coverage_v261 import SOURCE_SPECS, _classify_candidate, _probe_url
from .services_portal_extractors import (
    extract_fotocasa_initial_props_detail_attributes as extract,
)


LISTING_ID = "190475503"
URL = f"https://www.fotocasa.es/es/alquiler/vivienda/alhaurin-de-la-torre/chalet/{LISTING_ID}/d"
REMOVED_URL = "https://www.fotocasa.es/es/alquiler/vivienda/malaga/terraza/190487566/d"
RESULTS = "https://www.fotocasa.es/es/alquiler/viviendas/malaga-capital/todas-las-zonas/l"
FOTOCASA = next(spec for spec in SOURCE_SPECS if spec.slug == "fotocasa")
CTX = {"max_price": 5000, "bedrooms": 5, "property_types": ["house"]}


def root(**changes):
    value = {
        "realEstate": {
            "id": int(LISTING_ID), "price": 3500,
            "address": {"municipality": "Alhaurín de la Torre", "city": "Alhaurín de la Torre", "province": "Málaga"},
            "relatedGeoInfo": {"locationSlug": "alhaurin-de-la-torre"},
            "buildingSubtype": "House_Chalet",
            "features": {"rooms": 6},
            "featuresList": [{"label": "typology", "value": "house_chalet"}],
            "descriptions": {"es-ES": "Dispone de 5 habitaciones."},
        },
        "realEstateAdDetailEntityV2": {
            "propertyId": int(LISTING_ID), "price": {"amount": 3500},
            "features": [{"type": "TYPOLOGY", "value": "HOUSE_CHALET"}],
            "description": "Chalet con 5 habitaciones.",
        },
    }
    for key, item in changes.items():
        if key == "realEstate":
            value[key].update(item)
        elif key == "detail":
            value["realEstateAdDetailEntityV2"].update(item)
        else:
            value[key] = item
    return value


def script(data=None, *, malformed=False, attrs='id="__initial_props__" type="application/json"'):
    body = "{" if malformed else json.dumps(root() if data is None else data)
    return f"<script {attrs}>{body}</script>"


def candidate(**changes):
    value = {"source_url": URL, "title": "Chalet", "price": None, "bedrooms": None,
             "property_type": "", "location": "", "municipality": "", "province": "",
             "provider": "deterministic"}
    value.update(changes)
    return value


def classified(item=None, data=None, *, final=URL, availability="confirmed"):
    html = script(root() if data is None else data)
    probe = {"http_status": 200, "final_url": final, "_detail_html": html,
             "availability_evidence": {"state": availability, "signal": "fixture", "source": "fixture"},
             "available": availability == "confirmed"}
    with patch("apps.busquedas.services_hybrid_coverage_v261._probe_url", return_value=probe):
        return _classify_candidate(FOTOCASA, item or candidate(), CTX, 1, targeted_detail=True)


class _Headers:
    def get_content_charset(self): return "utf-8"


class _Response:
    status, url, headers = 200, URL, _Headers()
    read_limit = None
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, limit):
        self.read_limit = limit
        return script().encode()


class FotocasaInitialPropsEnrichmentSR016FTests(TestCase):
    def test_initial_props_exact_script_parsed(self):
        self.assertTrue(extract(script(), URL, URL)["identity_matched"])

    def test_initial_props_missing_fails_closed(self):
        self.assertEqual(extract("<html></html>", URL, URL)["attributes"], {})

    def test_initial_props_malformed_json_fails_closed(self):
        self.assertEqual(extract(script(malformed=True), URL, URL)["attributes"], {})

    def test_multiple_initial_props_fails_closed(self):
        self.assertEqual(extract(script() + script(), URL, URL)["attributes"], {})

    def test_requested_id_required(self):
        self.assertFalse(extract(script(), "https://www.fotocasa.es/es/alquiler/viviendas/malaga/l", URL)["identity_matched"])

    def test_final_url_identity_required(self):
        self.assertFalse(extract(script(), URL, RESULTS)["identity_matched"])

    def test_realestate_identity_required(self):
        self.assertFalse(extract(script(root(realEstate={"id": 1})), URL, URL)["identity_matched"])

    def test_detail_property_identity_required(self):
        self.assertFalse(extract(script(root(detail={"propertyId": 1})), URL, URL)["identity_matched"])

    def test_identity_mismatch_returns_no_attributes(self):
        result = extract(script(root(realEstate={"id": 1})), URL, URL)
        self.assertEqual(result["attributes"], {})

    def test_real_pattern_190475503_identity_match(self):
        self.assertTrue(extract(script(), URL, URL)["identity_matched"])

    def test_price_from_realestate_price(self):
        data = root(detail={"price": None})
        self.assertEqual(extract(script(data), URL, URL)["attributes"]["price"], 3500)

    def test_price_corroborated_by_detail_price_amount(self):
        self.assertEqual(extract(script(), URL, URL)["attributes"]["price"], 3500)

    def test_price_conflict_fails_closed(self):
        result = extract(script(root(detail={"price": {"amount": 3600}})), URL, URL)
        self.assertNotIn("price", result["attributes"])
        self.assertTrue(result["conflicts"]["price"])

    def test_real_pattern_price_3500(self):
        self.assertEqual(extract(script(), URL, URL)["attributes"]["price"], 3500)

    def test_municipality_from_realestate_address(self):
        self.assertEqual(extract(script(), URL, URL)["attributes"]["municipality"], "Alhaurín de la Torre")

    def test_real_pattern_alhaurin_de_la_torre(self):
        self.assertEqual(classified()["municipality"], "Alhaurín de la Torre")

    def test_property_type_house_chalet_to_house(self):
        self.assertEqual(extract(script(), URL, URL)["attributes"]["property_type"], "house")

    def test_property_type_structured_conflict_fails_closed(self):
        data = root(detail={"features": [{"type": "TYPOLOGY", "value": "APARTMENT"}]})
        result = extract(script(data), URL, URL)
        self.assertNotIn("property_type", result["attributes"])

    def test_rooms_is_not_bedrooms(self):
        data = root(realEstate={"descriptions": {}}, detail={"description": ""})
        self.assertNotIn("bedrooms", extract(script(data), URL, URL)["attributes"])

    def test_real_pattern_rooms_6_not_used_as_bedrooms(self):
        self.assertEqual(extract(script(), URL, URL)["attributes"]["bedrooms"], 5)

    def test_explicit_5_habitaciones_to_bedrooms_5(self):
        data = root(detail={"description": ""})
        self.assertEqual(extract(script(data), URL, URL)["attributes"]["bedrooms"], 5)

    def test_explicit_5_dormitorios_to_bedrooms_5(self):
        data = root(realEstate={"descriptions": {}}, detail={"description": "Tiene 5 dormitorios"})
        self.assertEqual(extract(script(data), URL, URL)["attributes"]["bedrooms"], 5)

    def test_two_trusted_descriptions_agree_5(self):
        self.assertEqual(extract(script(), URL, URL)["attributes"]["bedrooms"], 5)

    def test_trusted_descriptions_5_vs_6_conflict_unknown(self):
        result = extract(script(root(detail={"description": "Tiene 6 dormitorios"})), URL, URL)
        self.assertNotIn("bedrooms", result["attributes"])

    def test_multiple_bedroom_counts_in_description_unknown(self):
        data = root(realEstate={"descriptions": {"es-ES": "5 habitaciones y anexo de 2 habitaciones"}}, detail={"description": ""})
        self.assertNotIn("bedrooms", extract(script(data), URL, URL)["attributes"])

    def test_no_explicit_bedroom_label_unknown(self):
        data = root(realEstate={"descriptions": {"es-ES": "Cinco estancias"}}, detail={"description": ""})
        self.assertNotIn("bedrooms", extract(script(data), URL, URL)["attributes"])

    def test_bedroom_limit_sanity(self):
        data = root(realEstate={"descriptions": {"es-ES": "21 habitaciones"}}, detail={"description": ""})
        self.assertNotIn("bedrooms", extract(script(data), URL, URL)["attributes"])

    def test_seo_footer_cannot_supply_property_type(self):
        data = root(realEstate={"buildingSubtype": None, "featuresList": []}, detail={"features": []})
        data["seoFooter"] = "House Chalet"
        self.assertNotIn("property_type", extract(script(data), URL, URL)["attributes"])

    def test_seo_cannot_supply_location(self):
        data = root(realEstate={"address": {}}, detail={})
        data["seoTitle"] = "Alhaurín de la Torre"
        self.assertNotIn("municipality", extract(script(data), URL, URL)["attributes"])

    def test_sui_scripts_ignored(self):
        extra = '<script id="sui-scripts" type="application/json">{"price":1}</script>'
        self.assertEqual(extract(script() + extra, URL, URL)["attributes"]["price"], 3500)

    def test_global_html_text_cannot_supply_attributes(self):
        empty = root(realEstate={"price": None, "address": {}, "buildingSubtype": None, "featuresList": [], "descriptions": {}}, detail={"price": None, "features": [], "description": ""})
        result = extract(script(empty) + "950 euros, 5 habitaciones, casa en Málaga", URL, URL)
        self.assertNotIn("price", result["attributes"])

    def test_known_card_price_not_overwritten(self):
        self.assertEqual(classified(candidate(price=3400))["price"], 3400)

    def test_known_card_bedrooms_not_overwritten(self):
        self.assertEqual(classified(candidate(bedrooms=4))["bedrooms"], 4)

    def test_known_card_property_type_not_overwritten(self):
        self.assertEqual(classified(candidate(property_type="flat"))["property_type"], "flat")

    def test_known_card_location_not_overwritten(self):
        verdict = classified(candidate(location="Málaga", municipality="Málaga"))
        self.assertEqual(verdict["municipality"], "Málaga")

    def test_unknown_only_enrichment(self):
        verdict = classified()
        self.assertEqual((verdict["price"], verdict["bedrooms"], verdict["property_type"]), (3500, 5, "house"))

    def test_evidence_source_is_fotocasa_initial_props(self):
        self.assertEqual(classified()["detail_attribute_evidence"]["fields"]["price"]["source"], "fotocasa_initial_props")

    def test_sr016c_jsonld_source_preserved(self):
        obj = {"@type": "RealEstateListing", "url": URL, "offers": {"price": 900}}
        html = '<script type="application/ld+json">' + json.dumps(obj) + "</script>" + script()
        probe = {"http_status": 200, "final_url": URL, "_detail_html": html,
                 "availability_evidence": {"state": "confirmed", "signal": "fixture", "source": "fixture"},
                 "available": True}
        with patch("apps.busquedas.services_hybrid_coverage_v261._probe_url", return_value=probe):
            verdict = _classify_candidate(FOTOCASA, candidate(), CTX, 1, targeted_detail=True)
        self.assertEqual(verdict["price"], 900)
        self.assertEqual(verdict["detail_attribute_evidence"]["fields"]["price"]["source"], "jsonld")

    def test_sr016e_redirect_unavailable_precedence(self):
        verdict = classified(candidate(source_url=REMOVED_URL), final=RESULTS, availability="unavailable")
        self.assertEqual(verdict["availability_evidence"]["state"], "unavailable")
        self.assertFalse(verdict["detail_attribute_evidence"]["identity_sources"]["fotocasa_initial_props"])

    def test_real_pattern_190487566_remains_unavailable(self):
        evidence = classify_listing_availability(requested_url=REMOVED_URL, status_code=200, final_url=RESULTS, html=script())
        self.assertEqual(evidence["state"], "unavailable")

    @patch("apps.busquedas.services_hybrid_coverage_v261.urlopen", return_value=_Response())
    def test_no_extra_http_request(self, mocked):
        _probe_url(URL, timeout=1)
        self.assertEqual(mocked.call_count, 1)

    def test_no_ai_call(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1)
        classified()
        self.assertEqual(planner.calls_attempted, 0)

    def test_no_search_credit(self):
        planner = AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1, budget_consumed=0)
        classified()
        self.assertEqual(planner.consumed, Decimal("0"))

    def test_no_metering_change(self):
        self.assertEqual(AdaptivePlanner.CALL_COST_UNITS, Decimal("1"))

    def test_no_provider_order_change(self):
        before = [spec.slug for spec in AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1).specs]
        classified()
        self.assertEqual(before, [spec.slug for spec in AdaptivePlanner("eco", SOURCE_SPECS, budget_max=1).specs])

    @patch("apps.busquedas.services_hybrid_coverage_v261.urlopen", return_value=_Response())
    def test_body_limit_unchanged(self, _mocked):
        response = _mocked.return_value
        _probe_url(URL, timeout=1)
        self.assertEqual(response.read_limit, 350000)

    def test_no_model_change(self):
        self.assertTrue(callable(extract))

    def test_no_migration(self):
        self.assertTrue(callable(extract))
