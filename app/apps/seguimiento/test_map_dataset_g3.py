from decimal import Decimal
from types import SimpleNamespace
import unittest

from apps.seguimiento.map_dataset import (
    DATASET_CONTRACT,
    DATASET_CONTRACT_SHA256,
    GROUPING_CONTRACT,
    build_opportunity_map_dataset,
)


def opportunity(
    *,
    pk,
    canonical_key,
    title,
    status="new",
    price="100000.00",
    source_name="Idealista",
):
    return SimpleNamespace(
        pk=pk,

        geo_canonical_key=(
            canonical_key
        ),

        title=title,
        status=status,
        priority="medium",

        asking_price_current=(
            Decimal(price)
            if price is not None
            else None
        ),

        opportunity_score=None,

        province="Málaga",
        municipality="Málaga",
        zone="",

        source_name=source_name,
    )


def detail_url(pk):
    return (
        f"/app/oportunidades/{pk}/"
    )


class OpportunityMapDatasetV1Tests(
    unittest.TestCase
):
    def test_contract_identity(self):
        self.assertEqual(
            DATASET_CONTRACT,
            "SOOI_G3_OPPORTUNITY_MAP_DATASET_V1",
        )

        self.assertEqual(
            DATASET_CONTRACT_SHA256,
            "f4692829ed6d81c63a55901967a8301f91484de4da363ab81905f0c37c8b7678",
        )

        self.assertEqual(
            GROUPING_CONTRACT,
            "ONE_FEATURE_PER_CANONICAL_MUNICIPALITY",
        )

    def test_one_feature_per_municipality(self):
        rows = [
            opportunity(
                pk=21,
                canonical_key=(
                    "municipality:29067"
                ),
                title="A",
            ),
            opportunity(
                pk=26,
                canonical_key=(
                    "municipality:29067"
                ),
                title="B",
            ),
        ]

        payload = (
            build_opportunity_map_dataset(
                rows,
                detail_url_resolver=(
                    detail_url
                ),
            )
        )

        self.assertEqual(
            len(
                payload["features"]
            ),
            1,
        )

        feature = payload[
            "features"
        ][0]

        self.assertEqual(
            feature[
                "properties"
            ][
                "opportunity_count"
            ],
            2,
        )

        self.assertEqual(
            [
                row["id"]
                for row in (
                    feature[
                        "properties"
                    ][
                        "opportunities"
                    ]
                )
            ],
            [21, 26],
        )

    def test_geometry_is_lon_lat(self):
        row = opportunity(
            pk=21,
            canonical_key=(
                "municipality:29067"
            ),
            title="Málaga",
        )

        payload = (
            build_opportunity_map_dataset(
                [row],
                detail_url_resolver=(
                    detail_url
                ),
            )
        )

        coordinates = (
            payload["features"][0][
                "geometry"
            ][
                "coordinates"
            ]
        )

        self.assertEqual(
            coordinates,
            [
                -4.41498039,
                36.72026192,
            ],
        )

    def test_no_artificial_jitter(self):
        rows = [
            opportunity(
                pk=21,
                canonical_key=(
                    "municipality:29067"
                ),
                title="A",
            ),
            opportunity(
                pk=26,
                canonical_key=(
                    "municipality:29067"
                ),
                title="B",
            ),
        ]

        payload = (
            build_opportunity_map_dataset(
                rows,
                detail_url_resolver=(
                    detail_url
                ),
            )
        )

        self.assertEqual(
            len(
                payload["features"]
            ),
            1,
        )

    def test_non_ready_is_fail_closed(self):
        rows = [
            opportunity(
                pk=31,
                canonical_key=None,
                title="Sin identidad",
            ),
        ]

        payload = (
            build_opportunity_map_dataset(
                rows,
                detail_url_resolver=(
                    detail_url
                ),
            )
        )

        self.assertEqual(
            payload["features"],
            [],
        )

        self.assertEqual(
            payload["meta"][
                "fail_closed"
            ],
            1,
        )

        self.assertEqual(
            payload["meta"][
                "map_ready"
            ],
            0,
        )

    def test_summary_contract(self):
        row = opportunity(
            pk=24,
            canonical_key=(
                "municipality:14003"
            ),
            title=(
                "Casa con terreno"
            ),
            price="36000.00",
            source_name=(
                "Servihabitat"
            ),
        )

        row.opportunity_score = 90
        row.province = "Córdoba"
        row.municipality = (
            "Alcaracejos"
        )
        row.zone = "Entrada norte"
        row.priority = "high"
        row.status = "analysis"

        payload = (
            build_opportunity_map_dataset(
                [row],
                detail_url_resolver=(
                    detail_url
                ),
            )
        )

        summary = (
            payload["features"][0][
                "properties"
            ][
                "opportunities"
            ][0]
        )

        self.assertEqual(
            set(summary),
            {
                "id",
                "title",
                "status",
                "priority",
                "asking_price_current",
                "opportunity_score",
                "province",
                "municipality",
                "zone",
                "source_name",
                "detail_url",
            },
        )

        self.assertEqual(
            summary[
                "asking_price_current"
            ],
            "36000.00",
        )

        self.assertEqual(
            summary[
                "detail_url"
            ],
            "/app/oportunidades/24/",
        )

        forbidden = {
            "owner",
            "assigned_to",
            "main_contact",
            "broker_company",
            "decision_notes",
            "next_action_notes",
            "target_price_internal",
            "max_offer_price",
            "cadastral_reference",
            "source_url",
            "summary",
            "geo_registry_digest",
        }

        self.assertTrue(
            forbidden.isdisjoint(
                summary
            )
        )

    def test_exact_meta_counts(self):
        rows = [
            opportunity(
                pk=7,
                canonical_key=(
                    "municipality:08180"
                ),
                title="A",
            ),
            opportunity(
                pk=8,
                canonical_key=(
                    "municipality:08180"
                ),
                title="B",
            ),
            opportunity(
                pk=31,
                canonical_key=None,
                title="C",
            ),
        ]

        payload = (
            build_opportunity_map_dataset(
                rows,
                detail_url_resolver=(
                    detail_url
                ),
            )
        )

        self.assertEqual(
            payload["meta"],
            {
                "opportunities_total_visible":
                    3,

                "map_ready":
                    2,

                "fail_closed":
                    1,

                "municipality_features":
                    1,
            },
        )

    def test_feature_order_is_deterministic(self):
        rows = [
            opportunity(
                pk=24,
                canonical_key=(
                    "municipality:14003"
                ),
                title="B",
            ),
            opportunity(
                pk=9,
                canonical_key=(
                    "municipality:08061"
                ),
                title="A",
            ),
        ]

        payload = (
            build_opportunity_map_dataset(
                rows,
                detail_url_resolver=(
                    detail_url
                ),
            )
        )

        self.assertEqual(
            [
                feature["id"]
                for feature
                in payload["features"]
            ],
            [
                "municipality:08061",
                "municipality:14003",
            ],
        )


if __name__ == "__main__":
    unittest.main()
