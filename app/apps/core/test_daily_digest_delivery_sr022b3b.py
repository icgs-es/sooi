from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection, IntegrityError, transaction
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty

from .daily_digest_delivery import deliver_daily_digest
from .models import DailyDigestDelivery, NotificationPreference
from .proactive_workflow import DAILY_DIGEST_SUBJECT


User = get_user_model()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="SOOI <no-reply@sooi.io>",
)
class DailyDigestDeliverySR022B3BTests(TransactionTestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 23, 9, 15, tzinfo=ZoneInfo("Europe/Madrid"))
        self.user = User.objects.create_user(
            username="delivery-owner", email="owner@example.test", password="secret"
        )
        self.other = User.objects.create_user(
            username="delivery-other", email="other@example.test", password="secret"
        )
        self.source = Source.objects.create(name="Delivery source", code="delivery-source")

    def enable(self, user=None):
        return NotificationPreference.objects.create(
            owner=user or self.user, daily_digest_enabled=True
        )

    def actionable(self, user=None, suffix="one"):
        owner = user or self.user
        return CapturedProperty.objects.create(
            owner=owner,
            source=self.source,
            source_external_id=f"delivery-{owner.pk}-{suffix}",
            title=f"Casa delivery {suffix}",
            operation_type=CapturedProperty.OperationType.SALE,
            property_type=CapturedProperty.PropertyType.HOUSE,
            status=CapturedProperty.Status.IN_REVIEW,
            review_status=CapturedProperty.ReviewStatus.REVIEWED,
            availability_verification_state=CapturedProperty.AvailabilityVerificationState.UNKNOWN,
        )

    def existing(self, status, *, owner=None, lease_expires_at=None):
        return DailyDigestDelivery.objects.create(
            owner=owner or self.user,
            delivery_type=DailyDigestDelivery.DeliveryType.DAILY_DIGEST,
            channel=DailyDigestDelivery.Channel.EMAIL,
            local_date=self.now.date(),
            status=status,
            lease_expires_at=lease_expires_at,
        )

    @patch("apps.core.daily_digest_delivery.send_transactional_email")
    def test_explicit_opt_in_required_without_auto_creation(self, transport):
        result = deliver_daily_digest(self.user, now=self.now)
        self.assertEqual(result.status, "not_eligible")
        self.assertFalse(NotificationPreference.objects.filter(owner=self.user).exists())
        self.assertEqual(DailyDigestDelivery.objects.count(), 0)
        transport.assert_not_called()

        NotificationPreference.objects.create(owner=self.user, daily_digest_enabled=False)
        result = deliver_daily_digest(self.user, now=self.now)
        self.assertEqual(result.status, "not_eligible")
        transport.assert_not_called()

    @patch("apps.core.daily_digest_delivery.send_transactional_email")
    def test_missing_recipient_is_skipped(self, transport):
        self.enable()
        self.user.email = ""
        self.user.save(update_fields=["email"])
        result = deliver_daily_digest(self.user, now=self.now)
        self.assertEqual((result.status, result.reason_code), ("skipped", "missing_recipient"))
        transport.assert_not_called()

    @patch("apps.core.daily_digest_delivery.send_transactional_email")
    def test_empty_workflow_is_skipped(self, transport):
        self.enable()
        result = deliver_daily_digest(self.user, now=self.now)
        self.assertEqual((result.status, result.reason_code), ("skipped", "empty_workflow"))
        transport.assert_not_called()

    def test_success_commits_processing_and_runs_transport_outside_atomic(self):
        self.enable()
        capture = self.actionable()
        observations = {}

        def inspect_transport(**kwargs):
            delivery = DailyDigestDelivery.objects.get()
            observations.update(
                status=delivery.status,
                attempt_count=delivery.attempt_count,
                attempted_at=delivery.attempted_at,
                claim_token=delivery.claim_token,
                lease_expires_at=delivery.lease_expires_at,
                in_atomic=connection.in_atomic_block,
                kwargs=kwargs,
            )
            return 1

        with patch(
            "apps.core.daily_digest_delivery.send_transactional_email",
            side_effect=inspect_transport,
        ) as transport:
            result = deliver_daily_digest(self.user, now=self.now, base_url="https://example.test")

        delivery = DailyDigestDelivery.objects.get()
        self.assertEqual(observations["status"], DailyDigestDelivery.Status.PROCESSING)
        self.assertEqual(observations["attempt_count"], 1)
        self.assertIsNotNone(observations["attempted_at"])
        self.assertIsNotNone(observations["claim_token"])
        self.assertIsNotNone(observations["lease_expires_at"])
        self.assertFalse(observations["in_atomic"])
        self.assertEqual(observations["kwargs"]["subject"], DAILY_DIGEST_SUBJECT)
        self.assertEqual(observations["kwargs"]["recipient"], self.user.email)
        self.assertIn(capture.title, observations["kwargs"]["text_body"])
        self.assertIn(capture.title, observations["kwargs"]["html_body"])
        self.assertEqual(transport.call_count, 1)
        self.assertEqual((delivery.status, delivery.reason_code), ("sent", "sent"))
        self.assertIsNotNone(delivery.sent_at)
        self.assertEqual(len(delivery.payload_fingerprint), 64)
        self.assertTrue(result.sent)

    @patch("apps.core.daily_digest_delivery.send_transactional_email", return_value=1)
    def test_sent_replay_is_idempotent(self, transport):
        self.enable()
        self.actionable()
        first = deliver_daily_digest(self.user, now=self.now)
        second = deliver_daily_digest(self.user, now=self.now)
        self.assertEqual(first.delivery_id, second.delivery_id)
        self.assertTrue(second.idempotent)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(DailyDigestDelivery.objects.count(), 1)

    @patch("apps.core.daily_digest_delivery.send_transactional_email", side_effect=TimeoutError("secret"))
    def test_transport_exception_is_unknown_and_never_retried(self, transport):
        self.enable()
        self.actionable()
        first = deliver_daily_digest(self.user, now=self.now)
        second = deliver_daily_digest(self.user, now=self.now)
        delivery = DailyDigestDelivery.objects.get()
        self.assertEqual((first.status, first.reason_code), ("unknown", "transport_outcome_ambiguous"))
        self.assertEqual(delivery.last_error_class, "TimeoutError")
        self.assertNotIn("secret", delivery.last_error_class)
        self.assertIsNone(delivery.sent_at)
        self.assertTrue(second.idempotent)
        self.assertEqual(transport.call_count, 1)

    @patch("apps.core.daily_digest_delivery.send_transactional_email", return_value=0)
    def test_transport_zero_is_unknown(self, transport):
        self.enable()
        self.actionable()
        result = deliver_daily_digest(self.user, now=self.now)
        self.assertEqual((result.status, result.reason_code), ("unknown", "transport_zero_ambiguous"))
        self.assertTrue(result.transport_attempted)

    @patch("apps.core.daily_digest_delivery.send_transactional_email")
    def test_active_and_stale_processing_are_not_sent(self, transport):
        self.enable()
        self.actionable()
        active = self.existing("processing", lease_expires_at=self.now + timedelta(minutes=1))
        result = deliver_daily_digest(self.user, now=self.now)
        active.refresh_from_db()
        self.assertEqual(result.reason_code, "already_processing")
        self.assertEqual(active.attempt_count, 0)
        transport.assert_not_called()

        active.delete()
        stale = self.existing("processing", lease_expires_at=self.now - timedelta(seconds=1))
        result = deliver_daily_digest(self.user, now=self.now)
        stale.refresh_from_db()
        self.assertEqual((result.status, result.reason_code), ("unknown", "stale_processing_ambiguous"))
        self.assertEqual(stale.status, "unknown")
        transport.assert_not_called()

    @patch("apps.core.daily_digest_delivery.send_transactional_email")
    def test_failed_and_skipped_are_not_retried(self, transport):
        self.enable()
        self.actionable()
        for status in (DailyDigestDelivery.Status.FAILED, DailyDigestDelivery.Status.SKIPPED):
            delivery = self.existing(status)
            result = deliver_daily_digest(self.user, now=self.now)
            self.assertTrue(result.idempotent)
            delivery.delete()
        transport.assert_not_called()

    @patch("apps.core.daily_digest_delivery.send_transactional_email", return_value=1)
    def test_owner_isolation_and_database_unique_identity(self, transport):
        self.enable(self.other)
        self.actionable(self.other)
        foreign = self.existing("failed")
        result = deliver_daily_digest(self.other, now=self.now)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, "failed")
        self.assertNotEqual(result.delivery_id, foreign.pk)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.existing("scheduled", owner=self.other)

    @patch("apps.core.daily_digest_delivery.send_transactional_email", return_value=1)
    def test_madrid_local_date_uses_explicit_timezone(self, transport):
        self.enable()
        self.actionable()
        utc_instant = datetime(2026, 8, 22, 22, 30, tzinfo=ZoneInfo("UTC"))
        result = deliver_daily_digest(self.user, now=utc_instant)
        self.assertEqual(result.local_date.isoformat(), "2026-08-23")

    @patch("apps.core.daily_digest_delivery.send_transactional_email", return_value=1)
    def test_fingerprint_is_deterministic_and_payload_is_not_persisted(self, transport):
        self.enable()
        self.actionable(suffix="stable")
        first = deliver_daily_digest(self.user, now=self.now)
        delivery = DailyDigestDelivery.objects.get()
        fingerprint = delivery.payload_fingerprint
        delivery.delete()
        second = deliver_daily_digest(self.user, now=self.now)
        self.assertEqual(first.payload_fingerprint, second.payload_fingerprint)
        self.assertEqual(fingerprint, second.payload_fingerprint)
        field_values = [str(getattr(DailyDigestDelivery.objects.get(), field.name)) for field in DailyDigestDelivery._meta.fields]
        self.assertFalse(any("Casa delivery stable" in value for value in field_values))

        DailyDigestDelivery.objects.all().delete()
        self.user.email = "changed@example.test"
        self.user.save(update_fields=["email"])
        changed = deliver_daily_digest(self.user, now=self.now)
        self.assertNotEqual(fingerprint, changed.payload_fingerprint)

    @patch("apps.core.daily_digest_delivery.send_transactional_email")
    def test_preview_regression_has_no_delivery_or_send_controls(self, transport):
        self.client.force_login(self.user)
        response = self.client.get(reverse("daily_digest_preview"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DailyDigestDelivery.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        for label in ("Enviar ahora", "Enviar resumen", "Probar envío"):
            self.assertNotContains(response, label)
        transport.assert_not_called()
