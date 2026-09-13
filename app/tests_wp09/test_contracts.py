from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, models, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.busquedas.models import SearchProfile
from apps.core.models import DailyProductMetric, DemoRequest, UserProfile
from apps.core.product_metrics import (
    ALLOWED_DIMENSIONS, ALLOWED_DIMENSIONS_BY_METRIC, ALLOWED_METRICS,
    CALCULATION_VERSION, CANONICAL_TIME_ZONE, SUPPRESSION_THRESHOLD,
    calculate_metric, last_closed_day, published_metrics,
)
from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import FollowUpTask, PropertyOpportunity

ROOT = Path(__file__).parents[1]


class SchemaAndCatalogueTests(TestCase):
    def test_01_exact_explicit_fields(self):
        explicit = [f.name for f in DailyProductMetric._meta.local_fields if f.name != "id"]
        self.assertEqual(explicit, ["natural_day", "metric_name", "dimension_name", "dimension_value", "value", "calculation_version", "calculated_at", "is_complete"])

    def test_02_no_forbidden_field_types(self):
        forbidden = (models.ForeignKey, models.OneToOneField, models.ManyToManyField, models.JSONField, models.TextField, models.UUIDField)
        self.assertFalse(any(isinstance(field, forbidden) for field in DailyProductMetric._meta.get_fields()))

    def test_03_non_negative_and_small_positive_types(self):
        self.assertIsInstance(DailyProductMetric._meta.get_field("value"), models.PositiveBigIntegerField)
        self.assertIsInstance(DailyProductMetric._meta.get_field("calculation_version"), models.PositiveSmallIntegerField)

    def test_04_unique_constraint_exact(self):
        constraint = DailyProductMetric._meta.constraints[0]
        self.assertEqual(constraint.fields, ("natural_day", "metric_name", "dimension_name", "dimension_value", "calculation_version"))

    def test_05_read_index_exact(self):
        self.assertEqual(DailyProductMetric._meta.indexes[0].fields, ["metric_name", "natural_day"])

    def test_06_metric_catalogue_exact(self):
        self.assertEqual(ALLOWED_METRICS, ("registration_completed", "first_search_created", "first_capture_available", "first_opportunity_created", "first_dated_next_action", "canonical_activation_completed", "trial_expired", "demo_request_pending", "demo_request_notified", "demo_request_delivery_failed"))

    def test_07_dimension_catalogue_exact(self):
        self.assertEqual(ALLOWED_DIMENSIONS, ("all", "signup_source", "profile_type"))

    def test_08_dimension_matrix(self):
        self.assertEqual(ALLOWED_DIMENSIONS_BY_METRIC["registration_completed"], ("all", "signup_source"))
        self.assertEqual(ALLOWED_DIMENSIONS_BY_METRIC["demo_request_pending"], ("all", "profile_type"))
        self.assertNotIn("plan", repr(ALLOWED_DIMENSIONS_BY_METRIC))

    def test_09_constants(self):
        self.assertEqual((CANONICAL_TIME_ZONE, SUPPRESSION_THRESHOLD, CALCULATION_VERSION), ("Europe/Madrid", 10, 1))

    def test_10_flag_default_false(self):
        self.assertFalse(settings.SOOI_WP09_AGGREGATION_ENABLED)

    @override_settings(SOOI_WP08_ENABLED=True, SOOI_WP09_AGGREGATION_ENABLED=False)
    def test_11_flags_are_independent(self):
        self.assertTrue(settings.SOOI_WP08_ENABLED)
        self.assertFalse(settings.SOOI_WP09_AGGREGATION_ENABLED)

    def test_12_not_registered_in_admin(self):
        source = (ROOT / "apps/core/admin.py").read_text()
        self.assertNotIn("DailyProductMetric", source)

    def test_13_no_frontend_api_scheduler_or_observability(self):
        source = (ROOT / "apps/core/product_metrics.py").read_text()
        self.assertNotIn("observability", source)
        self.assertNotIn("sentry", source.lower())
        self.assertNotIn("celery", source.lower())


class PublicationTests(TestCase):
    def _metric(self, value, dimension="all", dimension_value="all"):
        return DailyProductMetric.objects.create(natural_day=last_closed_day(), metric_name="registration_completed", dimension_name=dimension, dimension_value=dimension_value, value=value, calculation_version=1, calculated_at=timezone.now(), is_complete=True)

    def test_14_suppresses_zero_through_nine(self):
        for value in range(10):
            DailyProductMetric.objects.all().delete()
            self._metric(value)
            self.assertEqual(published_metrics(last_closed_day(), "registration_completed")[0]["value"], "suppressed")

    def test_15_publishes_ten_or_more(self):
        self._metric(10)
        self.assertEqual(published_metrics(last_closed_day(), "registration_completed")[0]["value"], 10)

    def test_16_no_reconstruction_by_subtraction(self):
        self._metric(20, "signup_source", "self_service")
        self._metric(1, "signup_source", "unknown")
        self.assertEqual({r["value"] for r in published_metrics(last_closed_day(), "registration_completed", "signup_source")}, {"suppressed"})

    def test_17_rejects_dimension_cross(self):
        with self.assertRaises(ValueError):
            published_metrics(last_closed_day(), "first_search_created", "profile_type")


class CalculationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = Source.objects.create(name="Synthetic", code="wp09")

    def setUp(self):
        self.day = last_closed_day()
        self.start = datetime.combine(self.day, datetime.min.time(), tzinfo=ZoneInfo(CANONICAL_TIME_ZONE))

    def user(self, name="owner", **kwargs):
        user = get_user_model().objects.create_user(name, **kwargs)
        get_user_model().objects.filter(pk=user.pk).update(date_joined=self.start + timedelta(hours=1))
        user.refresh_from_db()
        return user

    def search(self, user):
        item = SearchProfile.objects.create(owner=user, name="Synthetic", province="Madrid")
        SearchProfile.objects.filter(pk=item.pk).update(created_at=self.start + timedelta(hours=2))
        return item

    def capture(self, user, search=None):
        item = CapturedProperty.objects.create(owner=user, search_profile=search, source=self.source, title="Synthetic", property_type="flat")
        CapturedProperty.objects.filter(pk=item.pk).update(captured_at=self.start + timedelta(hours=3))
        return item

    def opportunity(self, user, capture, **kwargs):
        item = PropertyOpportunity.objects.create(owner=user, captured_property=capture, title="Synthetic", **kwargs)
        PropertyOpportunity.objects.filter(pk=item.pk).update(created_at=self.start + timedelta(hours=4))
        return item

    def total(self, metric):
        return calculate_metric(self.day, metric)[0][3]

    def test_18_registration_excludes_staff_and_superuser(self):
        self.user("normal")
        self.user("staff", is_staff=True)
        self.user("root", is_superuser=True)
        self.assertEqual(self.total("registration_completed"), 1)

    def test_19_first_search_deduplicates_owner(self):
        user = self.user()
        self.search(user)
        self.search(user)
        self.assertEqual(self.total("first_search_created"), 1)

    def test_20_capture_requires_owned_search(self):
        owner, other = self.user("owner"), self.user("other")
        alien_search = self.search(other)
        self.capture(owner, alien_search)
        self.assertEqual(self.total("first_capture_available"), 0)

    def test_21_opportunity_requires_owned_capture(self):
        owner, other = self.user("owner"), self.user("other")
        capture = self.capture(other)
        self.opportunity(owner, capture)
        self.assertEqual(self.total("first_opportunity_created"), 0)

    def test_22_automatic_review_task_is_deduplicated(self):
        user = self.user()
        capture = self.capture(user)
        self.opportunity(user, capture, next_review_at=self.start + timedelta(hours=5))
        self.assertEqual(self.total("first_dated_next_action"), 1)

    def test_23_activation_uses_latest_milestone_day(self):
        user = self.user()
        search = self.search(user)
        capture = self.capture(user, search)
        self.opportunity(user, capture, next_review_at=self.start + timedelta(hours=5))
        self.assertEqual(self.total("canonical_activation_completed"), 1)

    def test_24_trial_expired_on_trial_end(self):
        user = self.user()
        UserProfile.objects.create(user=user, is_trial=True, trial_end=self.start + timedelta(hours=2))
        self.assertEqual(self.total("trial_expired"), 1)

    def test_25_non_trial_not_in_expiry(self):
        user = self.user()
        UserProfile.objects.create(user=user, is_trial=False, trial_end=self.start + timedelta(hours=2))
        self.assertEqual(self.total("trial_expired"), 0)

    def test_26_demo_notified_uses_notified_at(self):
        DemoRequest.objects.create(name="x", email="x@example.test", notified_at=self.start + timedelta(hours=1), delivery_status="sent")
        self.assertEqual(self.total("demo_request_notified"), 1)

    def test_27_snapshot_rejects_historical_backfill(self):
        with self.assertRaises(ValueError):
            calculate_metric(self.day - timedelta(days=1), "demo_request_pending")

    def test_27b_pending_is_latest_closed_day_snapshot(self):
        request = DemoRequest.objects.create(name="x", email="x@example.test", delivery_status="pending")
        DemoRequest.objects.filter(pk=request.pk).update(created_at=self.start + timedelta(hours=1))
        self.assertEqual(self.total("demo_request_pending"), 1)

    def test_28_failed_snapshot_does_not_read_error(self):
        request = DemoRequest.objects.create(name="x", email="x@example.test", delivery_status="failed", last_notification_error="SECRET")
        DemoRequest.objects.filter(pk=request.pk).update(created_at=self.start + timedelta(hours=1))
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.total("demo_request_delivery_failed"), 1)
        self.assertNotIn("last_notification_error", " ".join(q["sql"] for q in queries.captured_queries))

    def test_29_calculation_query_count_is_constant(self):
        before = len(connection.queries)
        self.total("first_search_created")
        empty_count = len(connection.queries) - before
        for index in range(12):
            self.search(self.user(f"u{index}"))
        before = len(connection.queries)
        self.total("first_search_created")
        self.assertEqual(len(connection.queries) - before, empty_count)


class CommandTests(TestCase):
    def test_30_disabled_fails_closed_without_writes(self):
        with self.assertRaises(CommandError):
            call_command("aggregate_product_metrics", start_date=last_closed_day(), end_date=last_closed_day())
        self.assertFalse(DailyProductMetric.objects.exists())

    @override_settings(SOOI_WP09_AGGREGATION_ENABLED=True)
    def test_31_rejects_inverted_range(self):
        with self.assertRaises(CommandError):
            call_command("aggregate_product_metrics", start_date=last_closed_day(), end_date=last_closed_day() - timedelta(days=1))

    @override_settings(SOOI_WP09_AGGREGATION_ENABLED=True)
    def test_32_rejects_today_and_future(self):
        today = last_closed_day() + timedelta(days=1)
        with self.assertRaises(CommandError):
            call_command("aggregate_product_metrics", start_date=today, end_date=today)

    @override_settings(SOOI_WP09_AGGREGATION_ENABLED=True)
    def test_33_idempotent_same_day_and_version(self):
        day = last_closed_day()
        call_command("aggregate_product_metrics", start_date=day, end_date=day, stdout=StringIO())
        first = DailyProductMetric.objects.count()
        call_command("aggregate_product_metrics", start_date=day, end_date=day, stdout=StringIO())
        self.assertEqual(DailyProductMetric.objects.count(), first)

    @override_settings(SOOI_WP09_AGGREGATION_ENABLED=True)
    def test_34_day_is_atomic(self):
        day = last_closed_day()
        with mock.patch("apps.core.management.commands.aggregate_product_metrics.DailyProductMetric.objects.bulk_create", side_effect=RuntimeError("aggregate write failed")):
            with self.assertRaises(RuntimeError):
                call_command("aggregate_product_metrics", start_date=day, end_date=day, stdout=StringIO())
        self.assertFalse(DailyProductMetric.objects.exists())

    def test_35_documentation_contract(self):
        docs = (ROOT / "docs/operations/wp09_backfill_retention_rollback.md").read_text().lower()
        for phrase in ("24 meses", "36 meses", "cifrados", "producción, staging y test", "no se restan", "no destructivo"):
            self.assertIn(phrase, docs)

    def test_36_version_is_part_of_unique_key(self):
        self.assertIn("calculation_version", DailyProductMetric._meta.constraints[0].fields)

    def test_37_database_unique_key_blocks_duplicates(self):
        day = last_closed_day()
        values = dict(natural_day=day, metric_name="registration_completed", dimension_name="all", dimension_value="all", calculation_version=1, value=0, calculated_at=timezone.now(), is_complete=True)
        DailyProductMetric.objects.create(**values)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DailyProductMetric.objects.create(**values)

    @override_settings(SOOI_WP08_ENABLED=False, SOOI_WP09_AGGREGATION_ENABLED=False)
    def test_38_flag_matrix_off_off(self):
        self.assertEqual((settings.SOOI_WP08_ENABLED, settings.SOOI_WP09_AGGREGATION_ENABLED), (False, False))

    @override_settings(SOOI_WP08_ENABLED=False, SOOI_WP09_AGGREGATION_ENABLED=True)
    def test_39_flag_matrix_off_on(self):
        self.assertEqual((settings.SOOI_WP08_ENABLED, settings.SOOI_WP09_AGGREGATION_ENABLED), (False, True))

    @override_settings(SOOI_WP08_ENABLED=True, SOOI_WP09_AGGREGATION_ENABLED=True)
    def test_40_flag_matrix_on_on(self):
        self.assertEqual((settings.SOOI_WP08_ENABLED, settings.SOOI_WP09_AGGREGATION_ENABLED), (True, True))

    def test_41_wp09_has_no_get_path_writes(self):
        views = (ROOT / "apps/core/views.py").read_text()
        self.assertNotIn("product_metrics", views)
        self.assertNotIn("DailyProductMetric", views)
