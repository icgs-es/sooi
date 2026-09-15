import unittest

from apps.busquedas.geography_municipal_context import (
    CONTRACT,
    load_municipal_context_registry,
)


class MunicipalContextRegistryV1Tests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.registry = load_municipal_context_registry()

    def test_contract_and_count(self):
        self.assertEqual(
            CONTRACT,
            "SOOI_MUNICIPAL_CONTEXT_V1",
        )
        self.assertEqual(len(self.registry), 8132)

    def test_manifest_authorities(self):
        manifest = self.registry.manifest

        self.assertEqual(
            manifest["population_reference_date"],
            "2025-01-01",
        )
        self.assertEqual(
            manifest["surface_source_unit"],
            "hectares",
        )
        self.assertEqual(
            manifest["surface_product_unit"],
            "km2",
        )

    def test_villanueva_del_duque(self):
        row = self.registry.get("municipality:14070")

        self.assertIsNotNone(row)
        self.assertEqual(
            row["municipality"],
            "Villanueva del Duque",
        )
        self.assertEqual(row["province"], "Córdoba")
        self.assertEqual(row["population"], 1409)
        self.assertEqual(row["altitude_m"], 582.0)

    def test_density_and_surface(self):
        row = self.registry.get("municipality:14070")

        self.assertEqual(row["surface_km2"], 137.5494)
        self.assertEqual(row["density_per_km2"], 10.2)

    def test_capital_distance_semantics(self):
        row = self.registry.get("municipality:14070")

        self.assertEqual(
            row["provincial_capital"],
            "Córdoba",
        )
        self.assertEqual(
            row["distance_to_provincial_capital_km"],
            59.8,
        )
        self.assertEqual(
            row["distance_semantics"],
            "STRAIGHT_LINE_APPROX",
        )
