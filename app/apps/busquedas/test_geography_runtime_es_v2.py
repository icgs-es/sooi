import os
import unittest
from types import SimpleNamespace
from unittest import mock

from .geography_runtime import resolve_profile_geography


class SpainGeographyRuntimeV2Tests(unittest.TestCase):
    def test_raw_location_is_preserved_and_v2_is_resolved(self):
        profile = SimpleNamespace(
            province="Córdoba", geography_scope="municipality",
            canonical_search_locations=lambda: ["Pozoblanco"],
        )
        with mock.patch.dict(os.environ, {"SOOI_GEOGRAPHY_REGISTRY_VERSION": "v2"}):
            unit = resolve_profile_geography(profile).units[0]
        self.assertEqual(unit.raw_value, "Pozoblanco")
        self.assertEqual(unit.resolution_status, "EXACT")
        self.assertFalse(unit.legacy_fallback)
        self.assertEqual(unit.province, "Córdoba")
        self.assertEqual(unit.autonomous_community, "Andalucía")
        self.assertEqual(unit.query_label, "Pozoblanco, Córdoba")
