from datetime import date, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.busquedas.models import SearchRun
from apps.seguimiento.models import Alert

from .models import DailyDigestDelivery, NotificationPreference, SystemSettings


User = get_user_model()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ProactiveWorkflowSR022B1Tests(TestCase):
    def setUp(self):
        SystemSettings.get_solo()
        self.user = User.objects.create_user(
            username="digest-owner", email="owner@example.test", password="secret"
        )
        self.other = User.objects.create_user(
            username="digest-other", email="other@example.test", password="secret"
        )
        self.url = reverse("notification_preferences")

    def login(self, user=None):
        self.client.force_login(user or self.user)

    def test_notification_settings_requires_auth(self):
        response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('login')}?next={self.url}")

    def test_preference_model_defaults(self):
        preference = NotificationPreference(owner=self.user)
        self.assertIs(preference.daily_digest_enabled, False)
        self.assertEqual(preference.daily_digest_time, time(8, 0))

    def test_get_without_preference_writes_zero_and_creates_no_row(self):
        self.login()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(NotificationPreference.objects.filter(owner=self.user).exists())
        writes = [
            query["sql"] for query in queries.captured_queries
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertEqual(writes, [])
        self.assertFalse(response.context["form"].instance.pk)
        self.assertEqual(response.context["form"].initial["daily_digest_time"], "08:00")

    def test_post_creates_current_user_preference_only(self):
        self.login()
        response = self.client.post(
            self.url,
            {"daily_digest_enabled": "on", "daily_digest_time": "08:30"},
            secure=True,
        )
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        preference = NotificationPreference.objects.get(owner=self.user)
        self.assertTrue(preference.daily_digest_enabled)
        self.assertEqual(preference.daily_digest_time, time(8, 30))
        self.assertFalse(NotificationPreference.objects.filter(owner=self.other).exists())

    def test_post_updates_existing_current_user_preference(self):
        own = NotificationPreference.objects.create(
            owner=self.user, daily_digest_enabled=False, daily_digest_time=time(8, 0)
        )
        foreign = NotificationPreference.objects.create(
            owner=self.other, daily_digest_enabled=True, daily_digest_time=time(9, 0)
        )
        self.login()
        response = self.client.post(
            self.url,
            {"daily_digest_enabled": "on", "daily_digest_time": "07:45"},
            secure=True,
        )
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        own.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual((own.daily_digest_enabled, own.daily_digest_time), (True, time(7, 45)))
        self.assertEqual((foreign.daily_digest_enabled, foreign.daily_digest_time), (True, time(9, 0)))

    def test_user_cannot_edit_other_users_preference(self):
        foreign = NotificationPreference.objects.create(owner=self.other)
        self.login()
        response = self.client.post(
            self.url, {"daily_digest_time": "10:00"}, secure=True
        )
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        foreign.refresh_from_db()
        self.assertEqual(foreign.daily_digest_time, time(8, 0))
        self.assertFalse(foreign.daily_digest_enabled)

    def test_digest_enable_requires_account_email(self):
        self.user.email = ""
        self.user.save(update_fields=["email"])
        self.login()
        response = self.client.post(
            self.url,
            {"daily_digest_enabled": "on", "daily_digest_time": "08:00"},
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Necesitas un correo electrónico en tu cuenta")
        self.assertFalse(NotificationPreference.objects.filter(owner=self.user).exists())

    def test_digest_disabled_allows_missing_email(self):
        self.user.email = ""
        self.user.save(update_fields=["email"])
        self.login()
        response = self.client.post(
            self.url, {"daily_digest_time": "08:00"}, secure=True
        )
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertFalse(NotificationPreference.objects.get(owner=self.user).daily_digest_enabled)

    def test_delivery_ledger_defaults_and_owner_relation(self):
        delivery = DailyDigestDelivery.objects.create(owner=self.user, local_date=date(2026, 8, 22))
        self.assertEqual(delivery.status, DailyDigestDelivery.Status.SCHEDULED)
        self.assertEqual(delivery.delivery_type, DailyDigestDelivery.DeliveryType.DAILY_DIGEST)
        self.assertEqual(delivery.channel, DailyDigestDelivery.Channel.EMAIL)
        self.assertEqual(list(self.user.daily_digest_deliveries.all()), [delivery])

    def test_delivery_ledger_unique_per_identity(self):
        values = {"owner": self.user, "local_date": date(2026, 8, 22)}
        DailyDigestDelivery.objects.create(**values)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DailyDigestDelivery.objects.create(**values)

    def test_delivery_ledger_stores_neither_recipient_nor_body(self):
        field_names = {field.name for field in DailyDigestDelivery._meta.get_fields()}
        self.assertFalse({"recipient", "recipient_email", "email", "body", "html_body", "text_body"} & field_names)

    def test_preference_save_creates_no_alert_or_delivery(self):
        self.login()
        alert_count = Alert.objects.count()
        response = self.client.post(
            self.url,
            {"daily_digest_enabled": "on", "daily_digest_time": "08:00"},
            secure=True,
        )
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(Alert.objects.count(), alert_count)
        self.assertEqual(DailyDigestDelivery.objects.count(), 0)

    @patch("apps.busquedas.tasks.run_search_profile_task.delay")
    def test_preference_save_sends_no_email_and_enqueues_no_celery_task(self, delay):
        self.login()
        run_count = SearchRun.objects.count()
        response = self.client.post(
            self.url,
            {"daily_digest_enabled": "on", "daily_digest_time": "08:00"},
            secure=True,
        )
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)
        delay.assert_not_called()
        self.assertEqual(SearchRun.objects.count(), run_count)

    def test_alert_model_contract_is_unchanged(self):
        self.assertEqual(
            {choice for choice, _label in Alert.Status.choices},
            {"new", "seen", "resolved", "dismissed"},
        )
