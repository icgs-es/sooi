import os
from unittest import mock

from django.test import SimpleTestCase

from .geography_registry.loader import (
    authority_registry_version,
    load_authority_registry,
    load_default_registry,
)
from .geography_registry.resolver import (
    _default_registry,
    resolve,
)
from .geography_runtime import (
    resolve_search_geography,
    runtime_search_locations,
)


class GeoNationalAuthorityV1Tests(SimpleTestCase):

    def tearDown(self):
        _default_registry.cache_clear()
        super().tearDown()

    def test_product_authority_defaults_to_v2(self):
        with mock.patch.dict(
            os.environ,
            {"SOOI_GEOGRAPHY_REGISTRY_VERSION": ""},
        ):
            self.assertEqual(
                authority_registry_version(),
                "v2",
            )

            self.assertEqual(
                load_authority_registry().registry_version,
                "SOOI_GEOGRAPHY_REGISTRY_ES_V2",
            )

    def test_historical_default_remains_v1(self):
        self.assertEqual(
            load_default_registry().registry_version,
            "SOOI_GEOGRAPHY_REGISTRY_V1_2026_08_14",
        )

    def test_v1_is_explicit_rollback(self):
        with mock.patch.dict(
            os.environ,
            {"SOOI_GEOGRAPHY_REGISTRY_VERSION": "v1"},
        ):
            self.assertEqual(
                load_authority_registry().registry_version,
                "SOOI_GEOGRAPHY_REGISTRY_V1_2026_08_14",
            )

    def test_implicit_resolver_uses_national_authority(self):
        with mock.patch.dict(
            os.environ,
            {"SOOI_GEOGRAPHY_REGISTRY_VERSION": "v2"},
        ):
            _default_registry.cache_clear()

            result = resolve(
                "Pozoblanco",
                province_hint="Córdoba",
                expected_type="municipality",
            )

            self.assertEqual(
                result.canonical_key,
                "municipality:14054",
            )

            self.assertEqual(
                result.registry_version,
                "SOOI_GEOGRAPHY_REGISTRY_ES_V2",
            )

    def test_exact_names_preserve_transport_contract(self):

        class Profile:
            geography_scope = "multi_municipality"
            province = "Córdoba"

            @staticmethod
            def canonical_search_locations():
                return [
                    "Pozoblanco",
                    "Pedroche",
                ]

        with mock.patch.dict(
            os.environ,
            {
                "SOOI_GEOGRAPHY_REGISTRY_VERSION": "v2",
                "SOOI_GEOGRAPHY_RUNTIME_V1": "1",
            },
        ):
            geo = resolve_search_geography(
                [
                    "Pozoblanco",
                    "Pedroche",
                ],
                scope="multi_municipality",
                province="Córdoba",
            )

            self.assertEqual(
                geo.query_labels,
                [
                    "Pozoblanco, Córdoba",
                    "Pedroche, Córdoba",
                ],
            )

            self.assertEqual(
                runtime_search_locations(Profile()),
                [
                    "Pozoblanco",
                    "Pedroche",
                ],
            )

    def test_alias_transport_uses_canonical_name(self):

        class Profile:
            geography_scope = "municipality"
            province = "Málaga"

            @staticmethod
            def canonical_search_locations():
                return [
                    "Puerto la Torre",
                ]

        with mock.patch.dict(
            os.environ,
            {
                "SOOI_GEOGRAPHY_REGISTRY_VERSION": "v1",
                "SOOI_GEOGRAPHY_RUNTIME_V1": "1",
            },
        ):
            self.assertEqual(
                runtime_search_locations(Profile()),
                [
                    "Puerto de la Torre",
                ],
            )
