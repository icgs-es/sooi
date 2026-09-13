from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from apps.busquedas.models import SearchProfile, SearchRun
from apps.fuentes.models import Source
from apps.seguimiento.models import PropertyOpportunity

from .forms import CapturedPropertyAvailabilityVerificationForm
from .models import CapturedProperty


User = get_user_model()


class AvailabilityVerificationSR018BTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="secret")
        self.other_user = User.objects.create_user(username="other", password="secret")
        self.source = Source.objects.create(name="Fuente test", code="source-test")
        self.profile = SearchProfile.objects.create(
            owner=self.user,
            name="Búsqueda SR0.18B",
            province="Córdoba",
            property_types=["flat"],
        )
        self.run = SearchRun.objects.create(search_profile=self.profile)
        self.capture = self.make_capture()
        self.verify_url = reverse(
            "capturedproperty_verify_availability", args=[self.capture.pk]
        )
        self.convert_url = reverse(
            "capturedproperty_convert_to_opportunity", args=[self.capture.pk]
        )

    def make_capture(self, **overrides):
        values = {
            "owner": self.user,
            "search_profile": self.profile,
            "search_run": self.run,
            "source": self.source,
            "source_external_id": "stable-external-id",
            "title": "Captación SR0.18B",
            "property_type": CapturedProperty.PropertyType.FLAT,
            "status": CapturedProperty.Status.IN_REVIEW,
        }
        values.update(overrides)
        return CapturedProperty.objects.create(**values)

    def post_verification(self, state, note=""):
        self.client.force_login(self.user)
        return self.client.post(
            self.verify_url,
            {
                "availability_verification_state": state,
                "availability_verification_note": note,
            },
            secure=True,
        )

    def confirm(self):
        response = self.post_verification(
            CapturedProperty.AvailabilityVerificationState.CONFIRMED
        )
        self.assertEqual(response.status_code, 302)
        self.capture.refresh_from_db()

    def test_default_state_unknown_with_null_audit_fields_and_optional_note(self):
        self.assertEqual(
            self.capture.availability_verification_state,
            CapturedProperty.AvailabilityVerificationState.UNKNOWN,
        )
        self.assertIsNone(self.capture.availability_verified_at)
        self.assertIsNone(self.capture.availability_verified_by)
        self.assertEqual(self.capture.availability_verification_note, "")
        self.capture.full_clean()

    def test_form_exposes_only_state_and_note_with_spanish_labels(self):
        form = CapturedPropertyAvailabilityVerificationForm(instance=self.capture)
        self.assertEqual(
            list(form.fields),
            ["availability_verification_state", "availability_verification_note"],
        )
        self.assertEqual(form.fields["availability_verification_state"].label, "Disponibilidad")
        self.assertEqual(form.fields["availability_verification_note"].label, "Nota de verificación")

    def test_model_validation_accepts_all_consistent_states(self):
        self.capture.full_clean()
        for state in (
            CapturedProperty.AvailabilityVerificationState.CONFIRMED,
            CapturedProperty.AvailabilityVerificationState.UNAVAILABLE,
        ):
            self.capture.availability_verification_state = state
            self.capture.availability_verified_at = timezone.now()
            self.capture.availability_verified_by = self.user
            self.capture.full_clean()

    def test_confirmed_requires_verifier_and_timestamp(self):
        self.capture.availability_verification_state = (
            CapturedProperty.AvailabilityVerificationState.CONFIRMED
        )
        with self.assertRaises(ValidationError):
            self.capture.full_clean()
        self.capture.availability_verified_at = timezone.now()
        with self.assertRaises(ValidationError):
            self.capture.full_clean()

    def test_unavailable_requires_verifier_and_timestamp(self):
        self.capture.availability_verification_state = (
            CapturedProperty.AvailabilityVerificationState.UNAVAILABLE
        )
        with self.assertRaises(ValidationError):
            self.capture.full_clean()
        self.capture.availability_verified_by = self.user
        with self.assertRaises(ValidationError):
            self.capture.full_clean()

    def test_unknown_rejects_verification_audit_fields(self):
        self.capture.availability_verified_at = timezone.now()
        self.capture.availability_verified_by = self.user
        with self.assertRaises(ValidationError):
            self.capture.full_clean()

    def test_database_constraint_rejects_inconsistent_state(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CapturedProperty.objects.filter(pk=self.capture.pk).update(
                availability_verification_state="confirmed"
            )

    def test_verification_requires_authentication_and_post(self):
        response = self.client.post(
            self.verify_url,
            {"availability_verification_state": "confirmed"},
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.verify_url, secure=True).status_code, 405)

    def test_other_owner_cannot_mutate_capture(self):
        self.client.force_login(self.other_user)
        response = self.client.post(
            self.verify_url,
            {"availability_verification_state": "confirmed"},
            secure=True,
        )
        self.assertEqual(response.status_code, 404)
        self.capture.refresh_from_db()
        self.assertEqual(self.capture.availability_verification_state, "unknown")

    def test_standard_csrf_protection_is_preserved(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(
            client.post(
                self.verify_url,
                {"availability_verification_state": "confirmed"},
                secure=True,
            ).status_code,
            403,
        )
        detail_path = reverse("capturedproperty_detail", args=[self.capture.pk])
        detail = client.get(detail_path, secure=True)
        token = detail.cookies["csrftoken"].value
        response = client.post(
            self.verify_url,
            {
                "csrfmiddlewaretoken": token,
                "availability_verification_state": "confirmed",
                "availability_verification_note": "Verificado por teléfono",
            },
            secure=True,
            HTTP_REFERER=f"https://testserver{detail_path}",
        )
        self.assertEqual(response.status_code, 302)

    def test_confirmed_records_user_timestamp_note_without_auto_conversion(self):
        original = (
            self.capture.pk,
            self.capture.source_id,
            self.capture.source_external_id,
            self.capture.search_run_id,
            self.capture.status,
        )
        self.post_verification("confirmed", "Confirmado con anunciante")
        self.capture.refresh_from_db()
        self.assertEqual(self.capture.availability_verified_by, self.user)
        self.assertIsNotNone(self.capture.availability_verified_at)
        self.assertEqual(self.capture.availability_verification_note, "Confirmado con anunciante")
        self.assertEqual(
            (
                self.capture.pk,
                self.capture.source_id,
                self.capture.source_external_id,
                self.capture.search_run_id,
                self.capture.status,
            ),
            original,
        )
        self.assertFalse(PropertyOpportunity.objects.filter(captured_property=self.capture).exists())

    def test_unavailable_records_user_timestamp_without_changing_status(self):
        self.post_verification("unavailable")
        self.capture.refresh_from_db()
        self.assertEqual(self.capture.availability_verified_by, self.user)
        self.assertIsNotNone(self.capture.availability_verified_at)
        self.assertEqual(self.capture.status, CapturedProperty.Status.IN_REVIEW)

    def test_unknown_clears_verifier_and_timestamp(self):
        self.post_verification("confirmed")
        self.post_verification("unknown", "Pendiente de nueva comprobación")
        self.capture.refresh_from_db()
        self.assertIsNone(self.capture.availability_verified_by)
        self.assertIsNone(self.capture.availability_verified_at)
        self.assertEqual(
            self.capture.availability_verification_note,
            "Pendiente de nueva comprobación",
        )

    def test_state_can_be_corrected_and_repeated_save_refreshes_timestamp(self):
        self.post_verification("confirmed")
        self.capture.refresh_from_db()
        first_timestamp = self.capture.availability_verified_at
        self.post_verification("unavailable")
        self.post_verification("confirmed")
        self.capture.refresh_from_db()
        self.assertEqual(self.capture.availability_verification_state, "confirmed")
        self.assertGreaterEqual(self.capture.availability_verified_at, first_timestamp)
        self.assertEqual(PropertyOpportunity.objects.count(), 0)

    def test_unknown_and_unavailable_cannot_convert(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.convert_url, secure=True).status_code, 302)
        self.assertEqual(PropertyOpportunity.objects.count(), 0)
        self.post_verification("unavailable")
        self.assertEqual(self.client.post(self.convert_url, secure=True).status_code, 302)
        self.assertEqual(PropertyOpportunity.objects.count(), 0)

    def test_confirmed_explicit_conversion_creates_one_and_is_idempotent(self):
        self.confirm()
        self.assertEqual(PropertyOpportunity.objects.count(), 0)
        self.assertEqual(self.client.post(self.convert_url, secure=True).status_code, 302)
        self.assertEqual(PropertyOpportunity.objects.count(), 1)
        self.assertEqual(self.client.post(self.convert_url, secure=True).status_code, 302)
        self.assertEqual(PropertyOpportunity.objects.count(), 1)

    def test_other_owner_cannot_convert(self):
        self.confirm()
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.post(self.convert_url, secure=True).status_code, 404)
        self.assertEqual(PropertyOpportunity.objects.count(), 0)

    @skipUnlessDBFeature("supports_transactions")
    def test_conversion_rejects_confirmed_row_with_inconsistent_audit_fields(self):
        if connection.vendor != "sqlite":
            self.skipTest("Focused test uses SQLite's check-constraint test switch")
        self.confirm()
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA ignore_check_constraints = ON")
            CapturedProperty.objects.filter(pk=self.capture.pk).update(
                availability_verified_at=None
            )
            cursor.execute("PRAGMA ignore_check_constraints = OFF")
        self.assertEqual(self.client.post(self.convert_url, secure=True).status_code, 302)
        self.assertEqual(PropertyOpportunity.objects.count(), 0)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_secure_requests_reach_view_with_ssl_redirect_enabled(self):
        self.client.force_login(self.other_user)
        response = self.client.post(self.verify_url, secure=True)
        self.assertEqual(response.status_code, 404)

    def test_four_review_captures_share_run_and_remain_unknown_without_opportunity(self):
        captures = [self.capture] + [
            self.make_capture(source_external_id=f"fixture-{index}")
            for index in range(1, 4)
        ]
        self.assertEqual({item.search_run_id for item in captures}, {self.run.pk})
        self.assertTrue(all(item.status == "in_review" for item in captures))
        self.assertTrue(
            all(item.availability_verification_state == "unknown" for item in captures)
        )
        self.assertEqual(PropertyOpportunity.objects.count(), 0)
