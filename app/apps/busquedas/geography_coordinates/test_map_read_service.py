import unittest
from types import SimpleNamespace

from .loader import (
    load_coordinate_registry,
)
from .map_read_service import (
    MAP_READ_SERVICE_CONTRACT,
    STATUS_NO_CANONICAL_IDENTITY,
    STATUS_NO_COORDINATE_RECORD,
    STATUS_READY,
    STATUS_UNSAFE_COORDINATE,
    map_points_for_opportunities,
    map_read_summary,
    resolve_opportunity_map_point,
    resolve_opportunity_map_points,
)
from .models import CoordinatePoint


class FakeUnsafeRegistry:
    def __init__(self):
        self.manifest = {
            "registry_version":
                "TEST_REGISTRY",

            "identity_authority":
                "TEST_IDENTITY",

            "source": {
                "attribution":
                    "Test attribution",
            },
        }

        self._point = CoordinatePoint(
            canonical_key=(
                "municipality:00001"
            ),

            official_code="00001",

            latitude=40.0,
            longitude=-3.0,

            precision_class=(
                "MUNICIPALITY_POPULATION_CENTROID"
            ),

            source_quality_class=(
                "APPROXIMATE_SOURCE"
            ),

            source_coordinate_origin=(
                "En estudio"
            ),

            source_name="TEST",
            source_version="1",
            source_reference="test",

            coordinate_reference_system=(
                "ETRS89"
            ),

            source_record_identity=(
                "00001000000"
            ),

            content_digest=(
                "sha256:test"
            ),
        )

    def get(
        self,
        canonical_key,
    ):
        if (
            canonical_key
            == self._point.canonical_key
        ):
            return self._point

        return None


class MapReadServiceV1Tests(
    unittest.TestCase
):
    def test_contract_name(self):
        self.assertEqual(
            MAP_READ_SERVICE_CONTRACT,
            "SOOI_G3_MAP_READ_SERVICE_V1",
        )

    def test_mapping_input_ready(self):
        result = (
            resolve_opportunity_map_point({
                "pk": 18,
                "geo_canonical_key":
                    "municipality:29067",
            })
        )

        self.assertEqual(
            result.status,
            STATUS_READY,
        )

        self.assertTrue(
            result.ready
        )

        self.assertIsNotNone(
            result.point
        )

        self.assertEqual(
            result.point.opportunity_id,
            18,
        )

        self.assertEqual(
            result.point.canonical_key,
            "municipality:29067",
        )

        self.assertAlmostEqual(
            result.point.latitude,
            36.72026192,
            places=8,
        )

        self.assertAlmostEqual(
            result.point.longitude,
            -4.41498039,
            places=8,
        )

        self.assertEqual(
            result.point.precision_class,
            "MUNICIPALITY_POPULATION_CENTROID",
        )

        self.assertEqual(
            result.point.approximation_label_es,
            "Ubicación aproximada "
            "a nivel municipal",
        )

        self.assertIn(
            "NGMEP",
            result.point.attribution,
        )

    def test_object_input_ready(self):
        opportunity = SimpleNamespace(
            pk=5,
            geo_canonical_key=(
                "municipality:14054"
            ),
        )

        result = (
            resolve_opportunity_map_point(
                opportunity
            )
        )

        self.assertEqual(
            result.status,
            STATUS_READY,
        )

        self.assertEqual(
            result.point.opportunity_id,
            5,
        )

        self.assertEqual(
            result.point.lon_lat,
            (
                -4.848193101,
                38.37721089,
            ),
        )

    def test_no_canonical_identity_fails_closed(
        self,
    ):
        result = (
            resolve_opportunity_map_point({
                "pk": 31,
                "geo_canonical_key": None,
            })
        )

        self.assertEqual(
            result.status,
            STATUS_NO_CANONICAL_IDENTITY,
        )

        self.assertFalse(
            result.ready
        )

        self.assertIsNone(
            result.point
        )

    def test_unknown_canonical_key_fails_closed(
        self,
    ):
        result = (
            resolve_opportunity_map_point({
                "pk": 999,
                "geo_canonical_key":
                    "municipality:99999",
            })
        )

        self.assertEqual(
            result.status,
            STATUS_NO_COORDINATE_RECORD,
        )

        self.assertIsNone(
            result.point
        )

    def test_unsafe_coordinate_fails_closed(
        self,
    ):
        registry = FakeUnsafeRegistry()

        result = (
            resolve_opportunity_map_point(
                {
                    "pk": 1,
                    "geo_canonical_key":
                        "municipality:00001",
                },
                registry=registry,
            )
        )

        self.assertEqual(
            result.status,
            STATUS_UNSAFE_COORDINATE,
        )

        self.assertFalse(
            result.ready
        )

        self.assertIsNone(
            result.point
        )

    def test_bulk_resolutions_preserve_input_order(
        self,
    ):
        rows = [
            {
                "pk": 5,
                "geo_canonical_key":
                    "municipality:14054",
            },
            {
                "pk": 31,
                "geo_canonical_key":
                    None,
            },
            {
                "pk": 18,
                "geo_canonical_key":
                    "municipality:29067",
            },
        ]

        results = (
            resolve_opportunity_map_points(
                rows
            )
        )

        self.assertEqual(
            [
                row.opportunity_id
                for row in results
            ],
            [5, 31, 18],
        )

        self.assertEqual(
            [
                row.status
                for row in results
            ],
            [
                STATUS_READY,
                STATUS_NO_CANONICAL_IDENTITY,
                STATUS_READY,
            ],
        )

    def test_bulk_points_exclude_failed_closed(
        self,
    ):
        rows = [
            {
                "pk": 5,
                "geo_canonical_key":
                    "municipality:14054",
            },
            {
                "pk": 31,
                "geo_canonical_key":
                    None,
            },
            {
                "pk": 18,
                "geo_canonical_key":
                    "municipality:29067",
            },
        ]

        points = (
            map_points_for_opportunities(
                rows
            )
        )

        self.assertEqual(
            [
                point.opportunity_id
                for point in points
            ],
            [5, 18],
        )

    def test_summary(self):
        rows = [
            {
                "pk": 5,
                "geo_canonical_key":
                    "municipality:14054",
            },
            {
                "pk": 31,
                "geo_canonical_key":
                    None,
            },
        ]

        summary = map_read_summary(
            rows
        )

        self.assertEqual(
            summary["TOTAL"],
            2,
        )

        self.assertEqual(
            summary[STATUS_READY],
            1,
        )

        self.assertEqual(
            summary[
                STATUS_NO_CANONICAL_IDENTITY
            ],
            1,
        )

    def test_all_registry_points_retain_municipal_precision(
        self,
    ):
        registry = (
            load_coordinate_registry()
        )

        for point in registry.values():
            self.assertEqual(
                point.precision_class,
                "MUNICIPALITY_POPULATION_CENTROID",
            )

    def test_ready_point_serialization_is_explicit(
        self,
    ):
        result = (
            resolve_opportunity_map_point({
                "pk": 18,
                "geo_canonical_key":
                    "municipality:29067",
            })
        )

        payload = result.as_dict()

        self.assertEqual(
            payload["status"],
            STATUS_READY,
        )

        point = payload["point"]

        self.assertIn(
            "latitude",
            point,
        )

        self.assertIn(
            "longitude",
            point,
        )

        self.assertIn(
            "precision_class",
            point,
        )

        self.assertIn(
            "approximation_label_es",
            point,
        )

        self.assertNotIn(
            "property_latitude",
            point,
        )

        self.assertNotIn(
            "property_longitude",
            point,
        )


if __name__ == "__main__":
    unittest.main()
