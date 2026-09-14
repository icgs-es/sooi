from django.db import connection, models
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.busquedas.models import SearchProfile
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import PropertyOpportunity


OLD_TARGETS = [
    (
        "busquedas",
        "0012_searchrun_reuse_provenance_sr03a4",
    ),
    (
        "inmuebles",
        "0007_capturedproperty_availability_verification",
    ),
    (
        "seguimiento",
        "0015_propertyopportunity_search_profile_and_more",
    ),
]

NEW_TARGETS = [
    (
        "busquedas",
        "0013_searchprofile_geography_bridge_snapshot",
    ),
    (
        "inmuebles",
        "0008_capturedproperty_geo_bridge",
    ),
    (
        "seguimiento",
        "0016_propertyopportunity_geo_bridge",
    ),
]


TABLE_COLUMNS = {
    "busquedas_searchprofile": {
        "geography_bridge_snapshot",
    },
    "inmuebles_capturedproperty": {
        "geo_canonical_key",
        "geo_resolution_status",
        "geo_resolution_method",
        "geo_registry_version",
        "geo_registry_digest",
    },
    "seguimiento_propertyopportunity": {
        "geo_canonical_key",
        "geo_resolution_status",
        "geo_resolution_method",
        "geo_registry_version",
        "geo_registry_digest",
    },
}


def columns(table):
    with connection.cursor() as cursor:
        description = (
            connection.introspection
            .get_table_description(
                cursor,
                table,
            )
        )

    return {
        column.name
        for column in description
    }


class GeoBridgeSchemaG2Tests(
    TransactionTestCase
):
    reset_sequences = False

    def test_model_contract_is_nullable_and_additive(
        self,
    ):
        snapshot = (
            SearchProfile._meta
            .get_field(
                "geography_bridge_snapshot"
            )
        )

        self.assertIsInstance(
            snapshot,
            models.JSONField,
        )

        self.assertTrue(
            snapshot.null
        )

        self.assertTrue(
            snapshot.blank
        )

        for model in (
            CapturedProperty,
            PropertyOpportunity,
        ):
            canonical = (
                model._meta.get_field(
                    "geo_canonical_key"
                )
            )

            self.assertTrue(
                canonical.null
            )

            self.assertTrue(
                canonical.blank
            )

            self.assertTrue(
                canonical.db_index
            )

            for name in (
                "geo_resolution_status",
                "geo_resolution_method",
                "geo_registry_version",
                "geo_registry_digest",
            ):
                field = (
                    model._meta.get_field(
                        name
                    )
                )

                self.assertTrue(
                    field.null
                )

                self.assertTrue(
                    field.blank
                )

        self.assertFalse(
            any(
                field.name
                == "geo_canonical_name"
                for model in (
                    CapturedProperty,
                    PropertyOpportunity,
                )
                for field
                in model._meta.fields
            )
        )

    def test_schema_forward_reverse_forward(
        self,
    ):
        executor = MigrationExecutor(
            connection
        )

        executor.migrate(
            OLD_TARGETS
        )

        for table, expected in (
            TABLE_COLUMNS.items()
        ):
            self.assertFalse(
                columns(table)
                & expected
            )

        executor = MigrationExecutor(
            connection
        )

        executor.migrate(
            NEW_TARGETS
        )

        for table, expected in (
            TABLE_COLUMNS.items()
        ):
            self.assertTrue(
                expected
                <= columns(table)
            )

        executor = MigrationExecutor(
            connection
        )

        executor.migrate(
            OLD_TARGETS
        )

        for table, expected in (
            TABLE_COLUMNS.items()
        ):
            self.assertFalse(
                columns(table)
                & expected
            )

        executor = MigrationExecutor(
            connection
        )

        executor.migrate(
            NEW_TARGETS
        )

        for table, expected in (
            TABLE_COLUMNS.items()
        ):
            self.assertTrue(
                expected
                <= columns(table)
            )
