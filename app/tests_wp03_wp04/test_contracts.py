import json
import os
import tempfile
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.busquedas.models import SearchProfile, SearchRun
from apps.core.observability import emit
from apps.inbox.models import EmailAccount, InboundEmail
from apps.inbox.views import _sooi_email_create_or_update_capture_from_post
from apps.inbox.services import reassign_inbound_email
from apps.inbox.management.commands.sync_inbox_email import persist_message
from apps.inbox.secrets import ImapSecretError, resolve_imap_secret
from apps.busquedas.tasks import run_search_profile_task
from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import PropertyOpportunity


class InboxIsolationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.a = user_model.objects.create_user("a", password="x")
        self.b = user_model.objects.create_user("b", password="x")
        self.staff = user_model.objects.create_user("staff", password="x", is_staff=True)
        self.superuser = user_model.objects.create_superuser("root", password="x")
        self.account_a = EmailAccount.objects.create(name="A", email_address="a@example.test", owner=self.a, imap_secret_ref="imap-account-a")
        self.account_b = EmailAccount.objects.create(name="B", email_address="b@example.test", owner=self.b, imap_secret_ref="imap-account-b")
        self.mail_a = InboundEmail.objects.create(owner=self.a, account=self.account_a, subject="synthetic")
        self.source = Source.objects.create(name="Email", code="email", source_type=Source.SourceType.MANUAL)

    def test_direct_identifier_is_hidden_from_other_user_and_staff(self):
        for user in (self.b, self.staff):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("inbox_detail", args=[self.mail_a.pk])).status_code, 404)

    def test_list_is_owner_scoped(self):
        InboundEmail.objects.create(owner=self.b, account=self.account_b, subject="other")
        self.client.force_login(self.a)
        response = self.client.get(reverse("inbox_list") + "?status=all")
        self.assertContains(response, "synthetic")
        self.assertNotContains(response, "other")

    def test_conversion_fails_closed_on_account_owner_mismatch(self):
        self.mail_a.account = self.account_b
        self.mail_a.save(update_fields=["account"])
        request = RequestFactory().post("/", {})
        request.user = self.a
        with self.assertRaises(PermissionDenied):
            _sooi_email_create_or_update_capture_from_post(request, self.mail_a)

    def test_sync_view_only_dispatches_owned_accounts(self):
        self.client.force_login(self.a)
        with patch("apps.inbox.views.call_command", return_value='{"status":"success","count":1}') as command:
            self.client.get(reverse("inbox_sync"))
        self.assertEqual([call.kwargs["account_id"] for call in command.call_args_list], [self.account_a.pk])

    def test_email_conversion_replay_is_idempotent(self):
        import inspect

        postgres_lock_source = inspect.getsource(
            _sooi_email_create_or_update_capture_from_post
        )
        self.assertIn(
            'select_for_update(of=("self",))',
            postgres_lock_source,
        )
        request = RequestFactory().post("/", {"title": "Synthetic", "source": self.source.pk})
        request.user = self.a
        first = _sooi_email_create_or_update_capture_from_post(request, self.mail_a)
        second = _sooi_email_create_or_update_capture_from_post(request, self.mail_a)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CapturedProperty.objects.filter(owner=self.a).count(), 1)

    def test_capture_to_opportunity_conversion_replay_is_idempotent(self):
        captured = CapturedProperty.objects.create(
            owner=self.a, source=self.source, title="Synthetic capture",
            property_type=CapturedProperty.PropertyType.FLAT,
            availability_verification_state=CapturedProperty.AvailabilityVerificationState.CONFIRMED,
            availability_verified_at=timezone.now(),
            availability_verified_by=self.a,
        )
        self.client.force_login(self.a)
        url = reverse("capturedproperty_convert_to_opportunity", args=[captured.pk])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(PropertyOpportunity.objects.filter(captured_property=captured).count(), 1)

    def test_reassignment_is_superuser_only_and_atomic(self):
        with self.assertRaises(PermissionDenied):
            reassign_inbound_email(message_id=self.mail_a.pk,
                                   target_account_id=self.account_b.pk, actor=self.staff)
        moved = reassign_inbound_email(message_id=self.mail_a.pk,
                                       target_account_id=self.account_b.pk, actor=self.superuser)
        self.assertEqual((moved.owner_id, moved.account_id), (self.b.pk, self.account_b.pk))

    def test_sync_upsert_is_idempotent(self):
        payload = {"subject": "repeat", "from_name": "", "from_email": "",
                   "received_at": None, "snippet": "", "body_text": "",
                   "detected_urls": [], "raw_metadata": {}}
        outcomes = [persist_message(account=self.account_a, owner=self.a,
                    search_profile=None, uid="stable-uid", message_id="stable-id",
                    payload=payload) for _ in range(2)]
        self.assertEqual(outcomes, ["created", "skipped"])
        self.assertEqual(InboundEmail.objects.filter(account=self.account_a,
                                                     message_uid="stable-uid").count(), 1)

    def test_sync_upsert_takes_account_row_lock(self):
        payload = {"subject": "race", "from_name": "", "from_email": "",
                   "received_at": None, "snippet": "", "body_text": "",
                   "detected_urls": [], "raw_metadata": {}}
        with patch("apps.inbox.management.commands.sync_inbox_email.EmailAccount.objects.select_for_update", wraps=EmailAccount.objects.select_for_update) as lock:
            persist_message(account=self.account_a, owner=self.a, search_profile=None,
                            uid="race-uid", message_id="race-id", payload=payload)
        lock.assert_called_once_with()

    def test_sync_stdout_never_contains_account_email(self):
        self.account_a.imap_host = "imap.invalid"
        self.account_a.imap_username = "technical-user"
        self.account_a.save()
        stdout = StringIO()
        with override_settings(SOOI_IMAP_SECRET_DIR=""), patch("sys.stdout", stdout):
            call_command("sync_inbox_email", account_id=self.account_a.pk, stdout=stdout)
        self.assertNotIn(self.account_a.email_address, stdout.getvalue())

    def test_sync_view_propagates_partial_result(self):
        self.client.force_login(self.a)
        with patch("apps.inbox.views.call_command", return_value=json.dumps({"status": "partial", "accounts": []})):
            response = self.client.get(reverse("inbox_sync"), follow=True)
        self.assertContains(response, "parcialmente")


class ObservabilityAndSmokeTests(TestCase):
    def test_observability_rejects_pii_and_free_text_fields(self):
        for forbidden in ("email", "phone", "address", "message", "url", "credentials"):
            with self.assertRaises(ValueError):
                emit("test", "success", **{forbidden: "synthetic"})

    def test_observability_rejects_unsafe_values_in_allowed_fields(self):
        unsafe = ("person@example.test", "+34600111222", "https://example.test/x", "two words", "line\nbreak", "user:password")
        for key in ("event", "operation", "component", "provider_state", "reason_code"):
            for value in unsafe:
                kwargs = {} if key == "event" else {key: value}
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    emit(value if key == "event" else "readiness", "failure", **kwargs)

    def test_structured_event_has_only_allowlisted_fields(self):
        stream = StringIO()
        with self.assertLogs("sooi.operations", level="INFO") as captured:
            payload = emit("search_run", "partial", "corr_12345678", component="celery", count=2)
        parsed = json.loads(captured.output[0].split(":", 2)[-1])
        self.assertEqual(parsed, payload)
        self.assertEqual(parsed["status"], "partial")

    def test_all_operational_states_are_deterministic(self):
        for state in ("success", "failure", "partial", "retry"):
            self.assertEqual(emit("inbox_sync", state)["status"], state)

    def test_public_health_and_readiness(self):
        self.assertEqual(self.client.get("/health/").json(), {"status": "ok", "component": "web"})
        self.assertEqual(self.client.get("/ready/").status_code, 200)

    def test_public_commercial_smoke(self):
        for path in ("/", "/accounts/login/", "/registro/", "/privacidad/", "/terminos/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_authenticated_smoke_uses_synthetic_user(self):
        user = get_user_model().objects.create_user("smoke", password="synthetic-password")
        self.client.force_login(user)
        response = self.client.get("/app/")
        self.assertEqual(response.status_code, 200)

    def test_provider_unavailable_readiness_is_explicit(self):
        with patch("apps.core.observability.connections") as mocked_connections:
            mocked_connections.__getitem__.side_effect = RuntimeError
            response = self.client.get("/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unavailable")


class SecretReferenceTests(TestCase):
    def test_valid_reference_and_restrictive_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "imap-account-7")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("synthetic-secret\n")
            os.chmod(path, 0o600)
            with override_settings(SOOI_IMAP_SECRET_DIR=directory):
                self.assertEqual(resolve_imap_secret("imap-account-7"), "synthetic-secret")

    def test_traversal_absolute_and_unsafe_references_fail_closed(self):
        for reference in ("../secret", "/etc/passwd", "two words", "user@email"):
            with self.subTest(reference=reference), self.assertRaises(ImapSecretError):
                resolve_imap_secret(reference)

    def test_missing_secret_and_open_permissions_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(SOOI_IMAP_SECRET_DIR=directory):
            with self.assertRaises(ImapSecretError):
                resolve_imap_secret("missing")
            path = os.path.join(directory, "open-secret")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("synthetic")
            os.chmod(path, 0o644)
            with self.assertRaises(ImapSecretError):
                resolve_imap_secret("open-secret")


class CeleryRetryTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("retry-user")
        self.search = SearchProfile.objects.create(owner=user, name="Retry", province="Test")

    def test_transient_failure_uses_bounded_framework_retry(self):
        with patch("apps.busquedas.tasks.run_search_profile", side_effect=ConnectionError), \
             patch.object(run_search_profile_task, "retry", side_effect=RuntimeError("retry-dispatched")) as retry:
            with self.assertRaisesRegex(RuntimeError, "retry-dispatched"):
                run_search_profile_task.run(self.search.pk)
        self.assertEqual(retry.call_args.kwargs["max_retries"], 3)
        self.assertEqual(retry.call_args.kwargs["countdown"], 1)

    def test_retry_limit_exhausted_does_not_dispatch_again(self):
        run_search_profile_task.push_request(retries=3)
        try:
            with patch("apps.busquedas.tasks.run_search_profile", side_effect=ConnectionError), \
                 patch.object(run_search_profile_task, "retry") as retry:
                with self.assertRaises(ConnectionError):
                    run_search_profile_task.run(self.search.pk)
            retry.assert_not_called()
        finally:
            run_search_profile_task.pop_request()

    def test_replay_after_transient_partial_work_completes(self):
        completed = SearchRun.objects.create(
            search_profile=self.search, status=SearchRun.Status.COMPLETED,
        )
        with patch("apps.busquedas.tasks.run_search_profile", side_effect=[ConnectionError(), completed]), \
             patch.object(run_search_profile_task, "retry", side_effect=RuntimeError("retry-dispatched")):
            with self.assertRaisesRegex(RuntimeError, "retry-dispatched"):
                run_search_profile_task.run(self.search.pk)
            self.assertEqual(run_search_profile_task.run(self.search.pk), completed.pk)
