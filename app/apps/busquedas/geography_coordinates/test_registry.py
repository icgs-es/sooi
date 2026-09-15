import unittest

from .loader import (
    CoordinateRegistryError,
    IDENTITY_AUTHORITY,
    MAP_DISPLAY_LABEL_ES,
    PRECISION_CLASS,
    REGISTRY_NAME,
    coordinate_for_canonical_key,
    load_coordinate_registry,
)


class CoordinateRegistryV1Tests(
    unittest.TestCase
):

    def test_promoted_registry_manifest_is_active(self):
        from apps.busquedas.geography_coordinates import (
            load_coordinate_registry,
        )

        registry = load_coordinate_registry()

        self.assertEqual(
            registry.manifest.get("status"),
            "ACTIVE",
        )

    def test_registry_identity(self):
        registry = (
            load_coordinate_registry()
        )

        self.assertEqual(
            len(registry),
            8132,
        )

        self.assertEqual(
            registry.manifest[
                "registry_version"
            ],
            REGISTRY_NAME,
        )

        self.assertEqual(
            registry.manifest[
                "identity_authority"
            ],
            IDENTITY_AUTHORITY,
        )

        self.assertEqual(
            registry.manifest[
                "precision_class"
            ],
            PRECISION_CLASS,
        )

        self.assertEqual(
            registry.manifest[
                "display_label_es"
            ],
            MAP_DISPLAY_LABEL_ES,
        )

    def test_all_records_are_map_safe(self):
        registry = (
            load_coordinate_registry()
        )

        values = registry.values()

        self.assertEqual(
            len(values),
            8132,
        )

        self.assertTrue(
            all(
                point.map_safe
                for point in values
            )
        )

    def test_malaga_reference_point(self):
        point = (
            coordinate_for_canonical_key(
                "municipality:29067"
            )
        )

        self.assertIsNotNone(
            point
        )

        self.assertEqual(
            point.official_code,
            "29067",
        )

        self.assertAlmostEqual(
            point.latitude,
            36.72026192,
            places=8,
        )

        self.assertAlmostEqual(
            point.longitude,
            -4.41498039,
            places=8,
        )

        self.assertEqual(
            point.precision_class,
            PRECISION_CLASS,
        )

    def test_pozoblanco_reference_point(self):
        point = (
            coordinate_for_canonical_key(
                "municipality:14054"
            )
        )

        self.assertIsNotNone(
            point
        )

        self.assertAlmostEqual(
            point.latitude,
            38.37721089,
            places=8,
        )

        self.assertAlmostEqual(
            point.longitude,
            -4.848193101,
            places=8,
        )

    def test_current_cartama_reference_point(self):
        point = (
            coordinate_for_canonical_key(
                "municipality:29038"
            )
        )

        self.assertIsNotNone(
            point
        )

        self.assertAlmostEqual(
            point.latitude,
            36.71056767,
            places=8,
        )

        self.assertAlmostEqual(
            point.longitude,
            -4.632378815,
            places=8,
        )

    def test_missing_identity_fails_closed(self):
        self.assertIsNone(
            coordinate_for_canonical_key(
                None
            )
        )

        self.assertIsNone(
            coordinate_for_canonical_key(
                ""
            )
        )

        self.assertIsNone(
            coordinate_for_canonical_key(
                "municipality:99999"
            )
        )

    def test_no_false_zero_coordinates(self):
        registry = (
            load_coordinate_registry()
        )

        for point in registry.values():
            self.assertFalse(
                point.latitude == 0.0
                and point.longitude == 0.0
            )

    def test_coordinate_ranges(self):
        registry = (
            load_coordinate_registry()
        )

        for point in registry.values():
            self.assertGreaterEqual(
                point.latitude,
                -90.0,
            )

            self.assertLessEqual(
                point.latitude,
                90.0,
            )

            self.assertGreaterEqual(
                point.longitude,
                -180.0,
            )

            self.assertLessEqual(
                point.longitude,
                180.0,
            )

    def test_unknown_registry_version_fails_closed(
        self,
    ):
        with self.assertRaises(
            CoordinateRegistryError
        ):
            load_coordinate_registry(
                "v999"
            )

    def test_attribution_is_present(self):
        registry = (
            load_coordinate_registry()
        )

        source = registry.manifest[
            "source"
        ]

        self.assertIn(
            "CC-BY 4.0",
            source["license"],
        )

        self.assertEqual(
            source["attribution"],
            "Obra derivada de NGMEP "
            "CC-BY 4.0 ign.es",
        )

    def test_source_sha_is_locked(self):
        registry = (
            load_coordinate_registry()
        )

        self.assertEqual(
            registry.manifest[
                "source"
            ][
                "file_sha256"
            ],
            "sha256:"
            "496e3079d3b1844e2827d9dfc328fcd2e629e72c2640b46840daa3711b915116",
        )


if __name__ == "__main__":
    unittest.main()
