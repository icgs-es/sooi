from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.busquedas.models import SearchProfile, SearchRun
from apps.fuentes.models import Source
from apps.seguimiento.models import OpportunityActivity, PropertyOpportunity

from .models import CapturedProperty


User = get_user_model()


class OpportunityConversionGovernanceSR019BTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner-019b", password="secret")
        self.other_user = User.objects.create_user(username="other-019b", password="secret")
        self.source = Source.objects.create(name="Fuente SR0.19B", code="source-sr019b")
        self.profile = SearchProfile.objects.create(
            owner=self.user,
            name="Búsqueda SR0.19B",
            province="Córdoba",
            property_types=["flat"],
        )
        self.run = SearchRun.objects.create(search_profile=self.profile)

    def make_capture(self, *, status=CapturedProperty.Status.IN_REVIEW, availability="confirmed"):
        values = {
            "owner": self.user,
            "search_profile": self.profile,
            "search_run": self.run,
            "source": self.source,
            "source_external_id": "external-sr019b",
            "source_url": "https://example.test/property/sr019b",
            "title": "Captación gobernada SR0.19B",
            "property_type": CapturedProperty.PropertyType.FLAT,
            "status": status,
            "review_status": CapturedProperty.ReviewStatus.PENDING,
            "availability_verification_state": availability,
            "availability_verification_note": "Confirmada por teléfono",
        }
        if availability != CapturedProperty.AvailabilityVerificationState.UNKNOWN:
            values["availability_verified_at"] = timezone.now()
            values["availability_verified_by"] = self.user
        return CapturedProperty.objects.create(**values)

    def convert_url(self, capture):
        return reverse("capturedproperty_convert_to_opportunity", args=[capture.pk])

    def post_conversion(self, capture, *, user=None):
        self.client.force_login(user or self.user)
        return self.client.post(self.convert_url(capture), secure=True)

    def test_unauthenticated_get_and_other_owner_cannot_convert(self):
        capture = self.make_capture()
        response = self.client.post(self.convert_url(capture), secure=True)
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.convert_url(capture), secure=True).status_code, 405)
        self.assertEqual(self.post_conversion(capture, user=self.other_user).status_code, 404)
        self.assertEqual(PropertyOpportunity.objects.count(), 0)

    def test_unknown_and_unavailable_cannot_convert(self):
        for availability in (
            CapturedProperty.AvailabilityVerificationState.UNKNOWN,
            CapturedProperty.AvailabilityVerificationState.UNAVAILABLE,
        ):
            capture = self.make_capture(availability=availability)
            self.assertEqual(self.post_conversion(capture).status_code, 302)
        self.assertEqual(PropertyOpportunity.objects.count(), 0)

    def test_discarded_confirmed_fails_closed_without_mutation(self):
        capture = self.make_capture(status=CapturedProperty.Status.DISCARDED)
        before = (
            capture.status,
            capture.review_status,
            capture.is_interesting,
            capture.availability_verification_state,
            capture.availability_verified_at,
            capture.availability_verified_by_id,
        )
        self.assertEqual(self.post_conversion(capture).status_code, 302)
        capture.refresh_from_db()
        self.assertEqual(PropertyOpportunity.objects.count(), 0)
        self.assertEqual(
            (
                capture.status,
                capture.review_status,
                capture.is_interesting,
                capture.availability_verification_state,
                capture.availability_verified_at,
                capture.availability_verified_by_id,
            ),
            before,
        )

    def test_confirmed_captured_and_in_review_can_convert(self):
        for status in (CapturedProperty.Status.CAPTURED, CapturedProperty.Status.IN_REVIEW):
            capture = self.make_capture(status=status)
            self.assertFalse(PropertyOpportunity.objects.filter(captured_property=capture).exists())
            self.assertEqual(self.post_conversion(capture).status_code, 302)
            self.assertTrue(PropertyOpportunity.objects.filter(captured_property=capture).exists())

    def test_success_sets_lifecycle_and_preserves_availability_and_provenance(self):
        capture = self.make_capture()
        preserved = {
            "availability_verification_state": capture.availability_verification_state,
            "availability_verified_at": capture.availability_verified_at,
            "availability_verified_by_id": capture.availability_verified_by_id,
            "availability_verification_note": capture.availability_verification_note,
            "search_run_id": capture.search_run_id,
            "source_id": capture.source_id,
            "source_external_id": capture.source_external_id,
            "source_url": capture.source_url,
        }
        self.post_conversion(capture)
        capture.refresh_from_db()
        self.assertEqual(capture.status, CapturedProperty.Status.VALIDATED)
        self.assertEqual(capture.review_status, CapturedProperty.ReviewStatus.REVIEWED)
        self.assertTrue(capture.is_interesting)
        for field, value in preserved.items():
            self.assertEqual(getattr(capture, field), value)

    def test_creation_records_one_activity_by_current_user(self):
        capture = self.make_capture()
        self.post_conversion(capture)
        opportunity = PropertyOpportunity.objects.get(captured_property=capture)
        activities = OpportunityActivity.objects.filter(
            opportunity=opportunity,
            activity_type=OpportunityActivity.ActivityType.CREATED,
        )
        self.assertEqual(activities.count(), 1)
        self.assertEqual(activities.get().created_by, self.user)
        self.assertEqual(activities.get().extra_data["captured_property_id"], capture.pk)

    def test_repeated_conversion_returns_same_opportunity_without_second_activity(self):
        capture = self.make_capture()
        self.post_conversion(capture)
        opportunity_id = PropertyOpportunity.objects.get(captured_property=capture).pk
        self.post_conversion(capture)
        self.assertEqual(PropertyOpportunity.objects.get(captured_property=capture).pk, opportunity_id)
        self.assertEqual(PropertyOpportunity.objects.count(), 1)
        self.assertEqual(
            OpportunityActivity.objects.filter(
                opportunity_id=opportunity_id,
                activity_type=OpportunityActivity.ActivityType.CREATED,
            ).count(),
            1,
        )

    def test_validated_with_existing_opportunity_is_idempotent(self):
        capture = self.make_capture(status=CapturedProperty.Status.VALIDATED)
        opportunity = PropertyOpportunity.objects.create(
            owner=self.user,
            captured_property=capture,
            title=capture.title,
        )
        self.assertEqual(self.post_conversion(capture).status_code, 302)
        self.assertEqual(PropertyOpportunity.objects.get(captured_property=capture).pk, opportunity.pk)
        self.assertEqual(PropertyOpportunity.objects.count(), 1)
        self.assertEqual(OpportunityActivity.objects.count(), 0)

    def test_validated_without_opportunity_fails_closed_and_preserves_capture(self):
        capture = self.make_capture(status=CapturedProperty.Status.VALIDATED)
        before = {
            field: getattr(capture, field)
            for field in (
                "status",
                "review_status",
                "is_interesting",
                "last_reviewed_at",
                "availability_verification_state",
                "availability_verified_at",
                "availability_verified_by_id",
                "availability_verification_note",
                "search_run_id",
                "source_id",
                "source_external_id",
                "source_url",
            )
        }
        self.assertEqual(self.post_conversion(capture).status_code, 302)
        capture.refresh_from_db()
        self.assertEqual(PropertyOpportunity.objects.count(), 0)
        for field, value in before.items():
            self.assertEqual(getattr(capture, field), value)

    def test_activity_failure_rolls_back_opportunity_and_capture_transition(self):
        capture = self.make_capture()
        with patch(
            "apps.inmuebles.views.OpportunityActivity.objects.create",
            side_effect=RuntimeError("synthetic activity failure"),
        ), self.assertRaises(RuntimeError):
            self.post_conversion(capture)
        capture.refresh_from_db()
        self.assertEqual(PropertyOpportunity.objects.count(), 0)
        self.assertEqual(capture.status, CapturedProperty.Status.IN_REVIEW)

    def test_detail_ui_reflects_conversion_governance(self):
        self.client.force_login(self.user)
        cases = (
            (self.make_capture(availability="unknown"), "Verifica disponibilidad"),
            (self.make_capture(availability="unavailable"), "No disponible"),
            (self.make_capture(status="discarded"), "Descartado"),
            (self.make_capture(status="in_review"), "Convertir en oportunidad"),
            (self.make_capture(status="validated"), "Validado sin oportunidad"),
        )
        for capture, expected in cases:
            response = self.client.get(
                reverse("capturedproperty_detail", args=[capture.pk]),
                secure=True,
            )
            self.assertContains(response, expected)

        validated = self.make_capture(status="validated")
        opportunity = PropertyOpportunity.objects.create(
            owner=self.user,
            captured_property=validated,
            title=validated.title,
        )
        response = self.client.get(
            reverse("capturedproperty_detail", args=[validated.pk]),
            secure=True,
        )
        self.assertContains(response, f'/app/oportunidades/{opportunity.pk}/')
        self.assertContains(response, "Ver oportunidad")
