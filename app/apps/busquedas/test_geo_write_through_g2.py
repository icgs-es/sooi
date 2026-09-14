import ast
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import models
from django.test import TestCase
from django.utils import timezone

from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import (
    FollowUpTask,
    PropertyOpportunity,
)

from apps.busquedas.geography_runtime import (
    geography_snapshot_for_profile,
)

from .models import (
    GeographicArea,
    SearchProfile,
)


class G2WriteThroughTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()

        cls.user = (
            User.objects.create_user(
                username="g2-write-through",
                password="x",
            )
        )

        cls.source = cls._create_source()


    @classmethod
    def _create_source(cls):
        kwargs = {}

        for field in Source._meta.fields:
            if (
                field.primary_key
                or field.auto_created
                or field.has_default()
                or field.null
                or field.blank
                or getattr(
                    field,
                    "auto_now",
                    False,
                )
                or getattr(
                    field,
                    "auto_now_add",
                    False,
                )
            ):
                continue

            if field.is_relation:
                raise AssertionError(
                    "Unexpected required Source relation: "
                    + field.name
                )

            if field.choices:
                kwargs[
                    field.name
                ] = field.choices[0][0]

            elif isinstance(
                field,
                models.URLField,
            ):
                kwargs[
                    field.name
                ] = (
                    "https://example.com/"
                )

            elif isinstance(
                field,
                (
                    models.CharField,
                    models.TextField,
                ),
            ):
                value = (
                    "g2-write-through-"
                    + field.name
                )

                max_length = getattr(
                    field,
                    "max_length",
                    None,
                )

                if max_length:
                    value = value[
                        :max_length
                    ]

                kwargs[
                    field.name
                ] = value

            elif isinstance(
                field,
                models.BooleanField,
            ):
                kwargs[
                    field.name
                ] = False

            elif isinstance(
                field,
                models.IntegerField,
            ):
                kwargs[
                    field.name
                ] = 1

            elif isinstance(
                field,
                models.DateTimeField,
            ):
                kwargs[
                    field.name
                ] = timezone.now()

            elif isinstance(
                field,
                models.JSONField,
            ):
                kwargs[
                    field.name
                ] = {}

            else:
                raise AssertionError(
                    "Unsupported required Source field: "
                    f"{field.name} "
                    f"{field.__class__.__name__}"
                )

        return Source.objects.create(
            **kwargs
        )


    def _profile(
        self,
        **overrides,
    ):
        data = {
            "owner": self.user,
            "name": "Perfil G2",
            "province": "Málaga",
            "geography_scope": (
                SearchProfile
                .GeographyScope
                .MUNICIPALITY
            ),
            "municipalities": [
                "Málaga",
            ],
        }

        data.update(
            overrides
        )

        return (
            SearchProfile.objects
            .create(
                **data
            )
        )


    def _capture(
        self,
        **overrides,
    ):
        data = {
            "owner": self.user,
            "source": self.source,
            "title": "Captura G2",
            "property_type": (
                CapturedProperty
                .PropertyType
                .FLAT
            ),
            "province": "Málaga",
            "municipality": "Málaga",
            "zone_text": "",
        }

        data.update(
            overrides
        )

        return (
            CapturedProperty.objects
            .create(
                **data
            )
        )


    def _opportunity(
        self,
        capture,
        **overrides,
    ):
        data = {
            "captured_property": capture,
            "owner": self.user,
            "title": "Oportunidad G2",
            "province": "Málaga",
            "municipality": "Málaga",
            "zone": "",
        }

        data.update(
            overrides
        )

        return (
            PropertyOpportunity.objects
            .create(
                **data
            )
        )


    def _area(
        self,
        **overrides,
    ):
        data = {
            "name": "Área G2",
            "province": "Córdoba",
            "municipalities": [
                "Pozoblanco",
                "Pedroche",
            ],
        }

        field = (
            GeographicArea._meta
            .get_field(
                "area_type"
            )
        )

        if (
            "area_type"
            not in overrides
            and not field.has_default()
        ):
            if field.choices:
                data[
                    "area_type"
                ] = field.choices[0][0]
            else:
                data[
                    "area_type"
                ] = "custom"

        data.update(
            overrides
        )

        return (
            GeographicArea.objects
            .create(
                **data
            )
        )


    def test_01_searchprofile_create_populates_snapshot(
        self,
    ):
        obj = self._profile()

        self.assertIsInstance(
            obj.geography_bridge_snapshot,
            dict,
        )

        self.assertEqual(
            obj.geography_bridge_snapshot,
            geography_snapshot_for_profile(
                obj
            ),
        )


    def test_02_searchprofile_geography_edit_refreshes_snapshot(
        self,
    ):
        obj = self._profile()

        before = (
            obj.geography_bridge_snapshot
        )

        obj.province = "Córdoba"
        obj.municipalities = [
            "Pozoblanco",
        ]
        obj.save()

        obj.refresh_from_db()

        self.assertNotEqual(
            obj.geography_bridge_snapshot,
            before,
        )

        self.assertEqual(
            obj.geography_bridge_snapshot,
            geography_snapshot_for_profile(
                obj
            ),
        )


    def test_03_searchprofile_operational_save_does_not_recompute(
        self,
    ):
        obj = self._profile()

        with patch(
            "apps.busquedas.geography_bridge."
            "geography_snapshot_for_profile"
        ) as mocked:
            obj.notes = "operativo"

            obj.save(
                update_fields=[
                    "notes",
                ]
            )

        mocked.assert_not_called()


    def test_04_searchprofile_geo_update_fields_persists_bridge(
        self,
    ):
        obj = self._profile(
            geography_scope=(
                SearchProfile
                .GeographyScope
                .PROVINCE
            ),
            municipalities=[],
        )

        sentinel = {
            "sentinel": "stale"
        }

        (
            SearchProfile.objects
            .filter(
                pk=obj.pk
            )
            .update(
                geography_bridge_snapshot=sentinel
            )
        )

        obj.refresh_from_db()

        self.assertEqual(
            obj.geography_bridge_snapshot,
            sentinel,
        )

        obj.province = "Córdoba"

        with patch(
            "apps.busquedas.geography_bridge."
            "geography_snapshot_for_profile",
            wraps=geography_snapshot_for_profile,
        ) as mocked:
            obj.save(
                update_fields=[
                    "province",
                ]
            )

            self.assertGreaterEqual(
                mocked.call_count,
                1,
            )

        obj.refresh_from_db()

        self.assertNotEqual(
            obj.geography_bridge_snapshot,
            sentinel,
        )

        self.assertEqual(
            obj.geography_bridge_snapshot,
            geography_snapshot_for_profile(
                obj
            ),
        )


    def test_05_capture_create_exact(
        self,
    ):
        obj = self._capture()

        self.assertEqual(
            obj.geo_canonical_key,
            "municipality:29067",
        )

        self.assertEqual(
            obj.geo_resolution_status,
            "EXACT",
        )

        self.assertEqual(
            obj.geo_resolution_method,
            "REGISTRY_DIRECT",
        )


    def test_06_capture_create_empty(
        self,
    ):
        obj = self._capture(
            municipality="",
        )

        self.assertIsNone(
            obj.geo_canonical_key
        )

        self.assertEqual(
            obj.geo_resolution_status,
            "EMPTY",
        )

        self.assertEqual(
            obj.geo_resolution_method,
            "NONE",
        )

        self.assertTrue(
            obj.geo_registry_version
        )

        self.assertTrue(
            obj.geo_registry_digest
        )


    def test_07_capture_parenthetical(
        self,
    ):
        obj = self._capture(
            municipality=(
                "Centro (Antequera)"
            ),
        )

        self.assertEqual(
            obj.geo_canonical_key,
            "municipality:29015",
        )

        self.assertEqual(
            obj.geo_resolution_method,
            "EXPLICIT_PARENTHETICAL_MUNICIPALITY",
        )


    def test_08_capture_leading_component(
        self,
    ):
        obj = self._capture(
            province="Córdoba",
            municipality=(
                "Pozoblanco, "
                "Los Pedroches, Córdoba"
            ),
        )

        self.assertEqual(
            obj.geo_canonical_key,
            "municipality:14054",
        )

        self.assertEqual(
            obj.geo_resolution_method,
            "EXPLICIT_LEADING_MUNICIPALITY",
        )


    def test_09_capture_operational_save_does_not_recompute(
        self,
    ):
        obj = self._capture()

        with patch(
            "apps.busquedas.geography_bridge."
            "build_captured_property_bridge"
        ) as mocked:
            obj.manual_notes = "nota"

            obj.save(
                update_fields=[
                    "manual_notes",
                ]
            )

        mocked.assert_not_called()


    def test_10_capture_update_or_create_refreshes_geo(
        self,
    ):
        obj = self._capture()

        obj, created = (
            CapturedProperty.objects
            .update_or_create(
                pk=obj.pk,
                defaults={
                    "province": "Málaga",
                    "municipality": (
                        "Antequera"
                    ),
                },
            )
        )

        self.assertFalse(
            created
        )

        self.assertEqual(
            obj.geo_canonical_key,
            "municipality:29015",
        )


    def test_11_opportunity_create_direct(
        self,
    ):
        capture = self._capture()

        obj = self._opportunity(
            capture
        )

        self.assertEqual(
            obj.geo_canonical_key,
            "municipality:29067",
        )

        self.assertEqual(
            obj.geo_resolution_status,
            "EXACT",
        )


    def test_12_opportunity_empty_inherits_capture(
        self,
    ):
        capture = self._capture()

        obj = self._opportunity(
            capture,
            municipality="",
        )

        self.assertEqual(
            obj.geo_canonical_key,
            capture.geo_canonical_key,
        )

        self.assertEqual(
            obj.geo_resolution_status,
            "INHERITED",
        )

        self.assertEqual(
            obj.geo_resolution_method,
            "INHERITED_CAPTURED_PROPERTY",
        )


    def test_13_opportunity_geo_edit_replaces_inheritance(
        self,
    ):
        capture = self._capture()

        obj = self._opportunity(
            capture,
            municipality="",
        )

        obj.province = "Córdoba"
        obj.municipality = "Pozoblanco"

        obj.save()

        obj.refresh_from_db()

        self.assertEqual(
            obj.geo_canonical_key,
            "municipality:14054",
        )

        self.assertEqual(
            obj.geo_resolution_status,
            "EXACT",
        )

        self.assertEqual(
            obj.geo_resolution_method,
            "REGISTRY_DIRECT",
        )


    def test_14_opportunity_operational_save_does_not_recompute(
        self,
    ):
        capture = self._capture()

        obj = self._opportunity(
            capture
        )

        with patch(
            "apps.busquedas.geography_bridge."
            "build_property_opportunity_bridge"
        ) as mocked:
            obj.summary = "operativo"

            obj.save(
                update_fields=[
                    "summary",
                    "updated_at",
                ]
            )

        mocked.assert_not_called()


    def test_15_existing_opportunity_review_task_behavior_remains(
        self,
    ):
        capture = self._capture()

        obj = self._opportunity(
            capture,
            next_review_at=(
                timezone.now()
            ),
        )

        self.assertTrue(
            FollowUpTask.objects
            .filter(
                property_opportunity=obj,
                task_type=(
                    FollowUpTask
                    .TaskType
                    .REVIEW
                ),
            )
            .exists()
        )


    def test_16_capture_geo_change_refreshes_inherited_opportunity(
        self,
    ):
        capture = self._capture()

        opportunity = (
            self._opportunity(
                capture,
                municipality="",
            )
        )

        self.assertEqual(
            opportunity.geo_canonical_key,
            "municipality:29067",
        )

        capture.municipality = (
            "Antequera"
        )

        capture.save(
            update_fields=[
                "municipality",
            ]
        )

        opportunity.refresh_from_db()

        self.assertEqual(
            opportunity.geo_canonical_key,
            "municipality:29015",
        )

        self.assertEqual(
            opportunity.geo_resolution_status,
            "INHERITED",
        )


    def test_17_capture_geo_change_does_not_override_direct_opportunity(
        self,
    ):
        capture = self._capture()

        opportunity = (
            self._opportunity(
                capture,
                province="Córdoba",
                municipality="Pozoblanco",
            )
        )

        before = (
            opportunity.geo_canonical_key
        )

        capture.municipality = (
            "Antequera"
        )

        capture.save(
            update_fields=[
                "municipality",
            ]
        )

        opportunity.refresh_from_db()

        self.assertEqual(
            opportunity.geo_canonical_key,
            before,
        )

        self.assertEqual(
            opportunity.geo_resolution_status,
            "EXACT",
        )


    def test_18_area_municipalities_change_refreshes_named_profile(
        self,
    ):
        area = self._area()

        profile = self._profile(
            province="Córdoba",
            geography_scope=(
                SearchProfile
                .GeographyScope
                .NAMED_AREA
            ),
            municipalities=[],
            geographic_area=area,
        )

        before = (
            profile.geography_bridge_snapshot
        )

        area.municipalities = [
            "Pozoblanco",
            "Pedroche",
            "Añora",
        ]

        area.save()

        profile.refresh_from_db()

        self.assertNotEqual(
            profile.geography_bridge_snapshot,
            before,
        )

        self.assertEqual(
            profile.geography_bridge_snapshot,
            geography_snapshot_for_profile(
                profile
            ),
        )


    def test_19_area_unrelated_change_does_not_refresh_profiles(
        self,
    ):
        area = self._area()

        self._profile(
            province="Córdoba",
            geography_scope=(
                SearchProfile
                .GeographyScope
                .NAMED_AREA
            ),
            municipalities=[],
            geographic_area=area,
        )

        with patch(
            "apps.busquedas.geography_bridge."
            "refresh_search_profiles_for_area"
        ) as mocked:
            area.name = (
                "Área G2 renombrada"
            )

            area.save(
                update_fields=[
                    "name",
                ]
            )

        mocked.assert_not_called()


    def test_20_area_instance_delete_removes_stale_snapshot(
        self,
    ):
        area = self._area()

        profile = self._profile(
            province="Córdoba",
            zone="Los Pedroches",
            geography_scope=(
                SearchProfile
                .GeographyScope
                .NAMED_AREA
            ),
            municipalities=[],
            geographic_area=area,
        )

        before = (
            profile.geography_bridge_snapshot
        )

        area.delete()

        profile.refresh_from_db()

        self.assertIsNone(
            profile.geographic_area_id
        )

        self.assertNotEqual(
            profile.geography_bridge_snapshot,
            before,
        )

        self.assertEqual(
            profile.geography_bridge_snapshot,
            geography_snapshot_for_profile(
                profile
            ),
        )


    def test_21_area_queryset_delete_removes_stale_snapshot(
        self,
    ):
        area = self._area(
            name="Área G2 queryset",
        )

        profile = self._profile(
            province="Córdoba",
            zone="Los Pedroches",
            geography_scope=(
                SearchProfile
                .GeographyScope
                .NAMED_AREA
            ),
            municipalities=[],
            geographic_area=area,
        )

        before = (
            profile.geography_bridge_snapshot
        )

        (
            GeographicArea.objects
            .filter(
                pk=area.pk
            )
            .delete()
        )

        profile.refresh_from_db()

        self.assertIsNone(
            profile.geographic_area_id
        )

        self.assertNotEqual(
            profile.geography_bridge_snapshot,
            before,
        )

        self.assertEqual(
            profile.geography_bridge_snapshot,
            geography_snapshot_for_profile(
                profile
            ),
        )


class G2WriteThroughStaticBypassTests(TestCase):
    @classmethod
    def _production_python_files(
        cls,
    ):
        apps_root = (
            Path(__file__)
            .resolve()
            .parents[1]
        )

        for path in sorted(
            apps_root.rglob(
                "*.py"
            )
        ):
            if "migrations" in path.parts:
                continue

            if (
                path.name.startswith(
                    "test_"
                )
                or path.name
                == "tests.py"
            ):
                continue

            yield path


    def test_22_no_queryset_update_of_geo_source_fields(
        self,
    ):
        geo_fields = {
            "province",
            "zone",
            "geography_scope",
            "municipalities",
            "geographic_area",
            "geographic_area_id",
            "municipality",
            "zone_text",
            "captured_property",
            "captured_property_id",
        }

        hits = []

        for path in (
            self._production_python_files()
        ):
            text = path.read_text(
                encoding="utf-8"
            )

            tree = ast.parse(text)

            for node in ast.walk(
                tree
            ):
                if not isinstance(
                    node,
                    ast.Call,
                ):
                    continue

                if not (
                    isinstance(
                        node.func,
                        ast.Attribute,
                    )
                    and node.func.attr
                    == "update"
                ):
                    continue

                fields = {
                    kw.arg
                    for kw in (
                        node.keywords
                    )
                    if kw.arg
                }

                overlap = (
                    fields
                    & geo_fields
                )

                if overlap:
                    hits.append(
                        (
                            str(path),
                            node.lineno,
                            sorted(
                                overlap
                            ),
                        )
                    )

        self.assertEqual(
            hits,
            [],
        )


    def test_23_no_bulk_update_of_geo_source_fields(
        self,
    ):
        geo_fields = {
            "province",
            "zone",
            "geography_scope",
            "municipalities",
            "geographic_area",
            "municipality",
            "zone_text",
            "captured_property",
        }

        hits = []

        for path in (
            self._production_python_files()
        ):
            text = path.read_text(
                encoding="utf-8"
            )

            tree = ast.parse(text)

            for node in ast.walk(
                tree
            ):
                if not isinstance(
                    node,
                    ast.Call,
                ):
                    continue

                if not (
                    isinstance(
                        node.func,
                        ast.Attribute,
                    )
                    and node.func.attr
                    == "bulk_update"
                ):
                    continue

                fields_node = None

                if len(
                    node.args
                ) >= 2:
                    fields_node = (
                        node.args[1]
                    )

                for kw in (
                    node.keywords
                ):
                    if (
                        kw.arg
                        == "fields"
                    ):
                        fields_node = (
                            kw.value
                        )

                if not isinstance(
                    fields_node,
                    (
                        ast.List,
                        ast.Tuple,
                    ),
                ):
                    continue

                values = []

                for item in (
                    fields_node.elts
                ):
                    if (
                        isinstance(
                            item,
                            ast.Constant,
                        )
                        and isinstance(
                            item.value,
                            str,
                        )
                    ):
                        values.append(
                            item.value
                        )

                overlap = (
                    set(values)
                    & geo_fields
                )

                if overlap:
                    hits.append(
                        (
                            str(path),
                            node.lineno,
                            sorted(
                                overlap
                            ),
                        )
                    )

        self.assertEqual(
            hits,
            [],
        )


    def test_24_no_target_model_bulk_create(
        self,
    ):
        targets = {
            "SearchProfile",
            "CapturedProperty",
            "PropertyOpportunity",
        }

        hits = []

        for path in (
            self._production_python_files()
        ):
            text = path.read_text(
                encoding="utf-8"
            )

            tree = ast.parse(text)

            for node in ast.walk(
                tree
            ):
                if not isinstance(
                    node,
                    ast.Call,
                ):
                    continue

                func = node.func

                if not (
                    isinstance(
                        func,
                        ast.Attribute,
                    )
                    and func.attr
                    == "bulk_create"
                ):
                    continue

                value = func.value

                if not (
                    isinstance(
                        value,
                        ast.Attribute,
                    )
                    and value.attr
                    == "objects"
                    and isinstance(
                        value.value,
                        ast.Name,
                    )
                ):
                    continue

                model_name = (
                    value.value.id
                )

                if model_name in targets:
                    hits.append(
                        (
                            str(path),
                            node.lineno,
                            model_name,
                        )
                    )

        self.assertEqual(
            hits,
            [],
        )
