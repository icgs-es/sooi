import os
from types import SimpleNamespace
from unittest import TestCase, mock

from .geography_runtime import (
    GEOGRAPHY_ENGINE_SCOPE, MALAGA_SPECIAL_CASE_LOGIC,
    PROVINCE_SPECIFIC_RUNTIME_BRANCHES, candidate_location_match,
    geography_snapshot_for_profile, resolve_profile_geography,
    runtime_search_locations,
)
from .searchrun_governance import canonical_fingerprint_payload


def profile(*locations, province="Málaga", scope="municipality"):
    return SimpleNamespace(
        province=province, geography_scope=scope,
        canonical_search_locations=lambda: list(locations),
    )


class GeographyRuntimeSR012DTests(TestCase):
    def test_flag_off_is_legacy(self):
        with mock.patch.dict(os.environ, {"SOOI_GEOGRAPHY_RUNTIME_V1": "0"}):
            self.assertEqual(runtime_search_locations(profile("Puerto la Torre")), ["Puerto la Torre"])
            self.assertEqual(geography_snapshot_for_profile(profile("Puerto la Torre")), {})

    def test_alias_is_canonical_for_query_and_raw_is_preserved(self):
        with mock.patch.dict(os.environ, {"SOOI_GEOGRAPHY_RUNTIME_V1": "1"}):
            geography = resolve_profile_geography(profile("Puerto la Torre"))
            unit = geography.units[0]
            self.assertEqual(unit.raw_value, "Puerto la Torre")
            self.assertEqual(unit.query_label, "Puerto de la Torre")
            self.assertEqual(unit.resolution_status, "ALIAS_RESOLVED")
            self.assertEqual(unit.canonical_type, "district")
            self.assertEqual(unit.parent, "Málaga")
            self.assertEqual(unit.province, "Málaga")
            self.assertEqual(unit.territorial_label, "Distrito de Málaga")
            self.assertEqual(runtime_search_locations(profile("Puerto la Torre")), ["Puerto de la Torre"])

    def test_unregistered_passthrough_does_not_block(self):
        with mock.patch.dict(os.environ, {"SOOI_GEOGRAPHY_RUNTIME_V1": "1"}):
            unit = resolve_profile_geography(profile("Pozoblanco", province="Córdoba")).units[0]
            self.assertEqual(unit.resolution_status, "UNRESOLVED")
            self.assertTrue(unit.legacy_fallback)
            self.assertEqual(unit.query_label, "Pozoblanco")

    def test_alias_and_canonical_dedupe_without_extra_unit(self):
        geography = resolve_profile_geography(
            profile("Puerto la Torre", "Puerto de la Torre", scope="multi_municipality")
        )
        self.assertEqual(geography.query_labels, ["Puerto de la Torre"])

    def test_candidate_evidence_and_district_hierarchy(self):
        municipality = resolve_profile_geography(profile("Málaga")).to_snapshot()["intent_resolutions"]
        district = resolve_profile_geography(profile("Puerto la Torre")).to_snapshot()["intent_resolutions"]
        self.assertEqual(candidate_location_match(municipality, "Puerto de la Torre"), "MATCH")
        self.assertEqual(candidate_location_match(district, "Málaga"), "UNKNOWN")
        self.assertEqual(candidate_location_match(district, None), "UNKNOWN")

    def test_unresolved_intent_requests_legacy_comparator(self):
        rows = resolve_profile_geography(profile("Pozoblanco", province="Córdoba")).to_snapshot()["intent_resolutions"]
        self.assertEqual(candidate_location_match(rows, "Pozoblanco"), "LEGACY")

    def test_frozen_snapshot_and_fingerprint_alias_equivalence(self):
        alias = resolve_profile_geography(profile("Puerto la Torre")).to_snapshot()
        canonical = resolve_profile_geography(profile("Puerto de la Torre")).to_snapshot()
        base = {"operation_type": "sale", "geography_scope": "municipality"}
        self.assertEqual(
            canonical_fingerprint_payload({**base, **alias}, "eco"),
            canonical_fingerprint_payload({**base, **canonical}, "eco"),
        )
        self.assertIn("registry_content_digest", alias)
        self.assertEqual(alias["coverage_units"][0]["raw"], "Puerto la Torre")

    def test_spain_generic_contract(self):
        self.assertEqual(GEOGRAPHY_ENGINE_SCOPE, "SPAIN_GENERIC")
        self.assertEqual(MALAGA_SPECIAL_CASE_LOGIC, 0)
        self.assertEqual(PROVINCE_SPECIFIC_RUNTIME_BRANCHES, 0)
        unit = resolve_profile_geography(profile("Villa Sintética", province="Provincia Sintética")).units[0]
        self.assertEqual(unit.query_label, "Villa Sintética")
