import json
from pathlib import Path

from django.test import SimpleTestCase

from .geography_registry.loader import (
    load_registry,
)

from .geography_registry.resolver import (
    resolve,
)


CHANGES = {
    "municipality:15071": (
        "Porto do Son",
        "Porto do Son, O",
    ),
    "municipality:15902": (
        "Oza-Cesuras",
        "Oza Cesuras",
    ),
    "municipality:27002": (
        "Alfoz",
        "Alfoz do Castrodouro",
    ),
    "municipality:27053": (
        "Ribeira de Piquín",
        "Ribeira de Piquín, A",
    ),
    "municipality:27044": (
        "Pastoriza, A",
        "Pastoriza",
    ),
    "municipality:32023": (
        "Castro Caldelas",
        "Castro de Caldelas, O",
    ),
    "municipality:32071": (
        "Riós",
        "Riós, O",
    ),
    "municipality:36007": (
        "Campo Lameiro",
        "Campo Lameiro, O",
    ),
    "municipality:36008": (
        "Cangas",
        "Cangas de Morrazo",
    ),
    "municipality:36009": (
        "Cañiza, A",
        "Caniza, A",
    ),
    "municipality:36902": (
        "Cerdedo-Cotobade",
        "Cerdedo Cotobade",
    ),
    "municipality:36031": (
        "Mondariz-Balneario",
        "Mondariz Balneario",
    ),
    "municipality:46210": (
        "Ráfol de Salem",
        "Ràfol de Salem, el",
    ),
}


class GeographyRegistryESV3Tests(
    SimpleTestCase
):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.v2 = load_registry("v2")
        cls.v3 = load_registry("v3")

    def test_inventory_and_codes(
        self,
    ):
        self.assertEqual(
            set(
                self.v2.identities
            ),
            set(
                self.v3.identities
            ),
        )

        self.assertEqual(
            len(
                self.v3.identities
            ),
            8202,
        )

        municipality_count = sum(
            identity.type.value
            == "municipality"
            for identity
            in self.v3.identities.values()
        )

        self.assertEqual(
            municipality_count,
            8132,
        )

        for key in self.v2.identities:
            self.assertEqual(
                self.v2.identities[
                    key
                ].official_code,
                self.v3.identities[
                    key
                ].official_code,
                key,
            )

    def test_exact_13_name_delta(
        self,
    ):
        changed = {
            key
            for key
            in self.v2.identities
            if (
                self.v2.identities[
                    key
                ].canonical_name
                != self.v3.identities[
                    key
                ].canonical_name
            )
        }

        self.assertEqual(
            changed,
            set(CHANGES),
        )

    def test_old_and_new_names_resolve(
        self,
    ):
        for (
            key,
            (old, new),
        ) in CHANGES.items():

            identity = (
                self.v3.identities[
                    key
                ]
            )

            province = None

            if identity.province:
                province = (
                    self.v3.identities[
                        identity.province
                    ].canonical_name
                )

            for name in (
                old,
                new,
            ):
                result = resolve(
                    name,
                    province_hint=province,
                    expected_type=(
                        "municipality"
                    ),
                    registry=self.v3,
                )

                self.assertEqual(
                    result.canonical_key,
                    key,
                    name,
                )

    def test_manifest(
        self,
    ):
        root = (
            Path(__file__).parent
            / "geography_registry"
            / "data"
            / "v3"
        )

        manifest = json.loads(
            (
                root
                / "manifest.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            manifest[
                "registry_version"
            ],
            "SOOI_GEOGRAPHY_REGISTRY_ES_V3",
        )

        self.assertEqual(
            manifest[
                "base_registry_version"
            ],
            "SOOI_GEOGRAPHY_REGISTRY_ES_V2",
        )

        self.assertEqual(
            manifest[
                "registry_as_of_date"
            ],
            "2026-09-14",
        )

        self.assertEqual(
            manifest[
                "post_baseline_modifications_applied_count"
            ],
            13,
        )

        self.assertEqual(
            manifest[
                "historical_aliases_added"
            ],
            13,
        )

        self.assertEqual(
            manifest[
                "article_aliases_added"
            ],
            7,
        )

        self.assertEqual(
            manifest[
                "alias_count"
            ],
            521,
        )
