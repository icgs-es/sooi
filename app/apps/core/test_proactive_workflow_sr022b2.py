from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.busquedas.models import SearchRun
from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import Alert

from .daily_workflow import build_daily_workflow
from .models import DailyDigestDelivery, NotificationPreference, SystemSettings
from .proactive_workflow import (
    DAILY_DIGEST_SUBJECT,
    build_daily_digest,
    render_daily_digest,
)


User = get_user_model()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ProactiveWorkflowSR022B2Tests(TestCase):
    def setUp(self):
        SystemSettings.get_solo()
        self.user = User.objects.create_user(
            username="preview-owner", email="owner@example.test", password="secret"
        )
        self.other = User.objects.create_user(
            username="preview-other", email="other@example.test", password="secret"
        )
        self.source = Source.objects.create(name="Fuente preview", code="preview-source")
        self.url = reverse("daily_digest_preview")
        self.now = datetime(2026, 8, 23, 9, 15, tzinfo=ZoneInfo("Europe/Madrid"))

    def make_capture(self, owner, suffix):
        return CapturedProperty.objects.create(
            owner=owner,
            source=self.source,
            source_external_id=f"preview-{owner.pk}-{suffix}",
            title=f"Casa preview {suffix}",
            operation_type=CapturedProperty.OperationType.SALE,
            property_type=CapturedProperty.PropertyType.HOUSE,
            province="Córdoba",
            municipality="Pozoblanco",
            status=CapturedProperty.Status.IN_REVIEW,
            review_status=CapturedProperty.ReviewStatus.REVIEWED,
            availability_verification_state=CapturedProperty.AvailabilityVerificationState.UNKNOWN,
        )

    def login(self):
        self.client.force_login(self.user)

    def test_digest_selector_reuses_daily_workflow(self):
        workflow = {
            "generated_at": self.now,
            "attention_total": 0,
            "counters": {
                "captures_ready": 0, "captures_availability": 0,
                "opportunities_overdue": 0, "opportunities_today": 0,
                "opportunities_no_next": 0, "tasks_overdue": 0, "tasks_today": 0,
            },
            "attention": [],
            "is_all_clear": True,
        }
        with patch("apps.core.proactive_workflow.build_daily_workflow", return_value=workflow) as selector:
            digest = build_daily_digest(self.user, now=self.now, limit=5, base_url="https://example.test")
        selector.assert_called_once_with(self.user, now=self.now, limit=5)
        self.assertTrue(digest["is_all_clear"])

    def test_digest_is_bounded_preserves_order_and_reports_overflow(self):
        for index in range(7):
            self.make_capture(self.user, str(index))
        workflow = build_daily_workflow(self.user, now=self.now, limit=5)
        digest = build_daily_digest(self.user, now=self.now, limit=5)
        self.assertEqual(digest["total_attention"], 7)
        self.assertEqual(len(digest["items"]), 5)
        self.assertEqual(digest["overflow_count"], 2)
        self.assertEqual(
            [item["title"] for item in digest["items"]],
            [item["object"].title for item in workflow["attention"]],
        )
        self.assertTrue(all(item["reason"] for item in digest["items"]))

    def test_digest_owner_scoped_with_absolute_direct_links_and_today_cta(self):
        own = self.make_capture(self.user, "own")
        foreign = self.make_capture(self.other, "foreign")
        rendered = render_daily_digest(
            self.user, now=self.now, base_url="https://example.test"
        )
        digest = rendered["digest"]
        self.assertIn(own.title, rendered["text_body"])
        self.assertNotIn(foreign.title, rendered["text_body"])
        self.assertEqual(
            digest["items"][0]["url"],
            f"https://example.test/app/captacion/{own.pk}/",
        )
        self.assertEqual(digest["today_url"], "https://example.test/app/")

    def test_html_and_text_have_semantic_parity(self):
        own = self.make_capture(self.user, "parity")
        rendered = render_daily_digest(
            self.user, now=self.now, base_url="https://example.test"
        )
        reason = rendered["digest"]["items"][0]["reason"]
        for body in (rendered["html_body"], rendered["text_body"]):
            self.assertIn(DAILY_DIGEST_SUBJECT.split(" · ")[-1], body)
            self.assertIn("1", body)
            self.assertIn(own.title, body)
            self.assertIn(reason, body)
            self.assertIn("https://example.test/app/", body)

    def test_empty_digest_renders_todo_al_dia_without_delivery(self):
        rendered = render_daily_digest(self.user, now=self.now)
        self.assertEqual(rendered["digest"]["total_attention"], 0)
        self.assertEqual(rendered["digest"]["items"], [])
        self.assertEqual(rendered["digest"]["overflow_count"], 0)
        self.assertIn("Todo al día", rendered["html_body"])
        self.assertIn("Todo al día", rendered["text_body"])
        self.assertEqual(DailyDigestDelivery.objects.count(), 0)

    def test_preview_requires_auth_over_https(self):
        response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('login')}?next={self.url}")

    def test_preview_route_is_get_only(self):
        self.login()
        response = self.client.post(self.url, {}, secure=True)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(DailyDigestDelivery.objects.count(), 0)

    @patch("apps.busquedas.tasks.run_search_profile_task.delay")
    def test_preview_is_zero_write_and_sends_or_enqueues_nothing(self, delay):
        self.make_capture(self.user, "zero-write")
        NotificationPreference.objects.create(owner=self.user, daily_digest_enabled=False)
        self.login()
        counts_before = {
            "preferences": NotificationPreference.objects.count(),
            "deliveries": DailyDigestDelivery.objects.count(),
            "alerts": Alert.objects.count(),
            "runs": SearchRun.objects.count(),
            "captures": CapturedProperty.objects.count(),
        }
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(counts_before, {
            "preferences": NotificationPreference.objects.count(),
            "deliveries": DailyDigestDelivery.objects.count(),
            "alerts": Alert.objects.count(),
            "runs": SearchRun.objects.count(),
            "captures": CapturedProperty.objects.count(),
        })
        writes = [
            query["sql"] for query in queries.captured_queries
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertEqual(writes, [])
        self.assertEqual(len(mail.outbox), 0)
        delay.assert_not_called()

    def test_preview_works_without_preference_and_does_not_create_it(self):
        self.make_capture(self.user, "no-preference")
        self.login()
        response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Casa preview no-preference")
        self.assertFalse(NotificationPreference.objects.filter(owner=self.user).exists())
        self.assertEqual(DailyDigestDelivery.objects.count(), 0)

    def test_preview_works_without_email(self):
        self.user.email = ""
        self.user.save(update_fields=["email"])
        self.login()
        response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sin correo de destino configurado")

    def test_settings_has_preview_button_and_preview_has_no_send_control(self):
        self.login()
        settings_response = self.client.get(reverse("notification_preferences"), secure=True)
        self.assertContains(settings_response, "Vista previa del resumen")
        self.assertContains(settings_response, self.url)
        preview_response = self.client.get(self.url, secure=True)
        self.assertContains(preview_response, "Vista previa")
        self.assertContains(preview_response, "No se ha enviado ningún correo.")
        self.assertContains(preview_response, "Volver a Notificaciones")
        self.assertContains(preview_response, "Abrir Hoy")
        self.assertNotContains(preview_response, "Enviar ahora")
        self.assertNotContains(preview_response, "Enviar resumen")
        self.assertNotContains(preview_response, "Enviar correo")
        self.assertNotContains(preview_response, "Probar envío")
        self.assertNotContains(preview_response, "/enviar/")
