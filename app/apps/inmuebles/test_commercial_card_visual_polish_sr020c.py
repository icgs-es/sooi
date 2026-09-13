from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.busquedas.models import SearchProfile, SearchRun
from apps.fuentes.models import Source
from apps.seguimiento.models import PropertyOpportunity

from .models import CapturedProperty


User = get_user_model()


class CommercialCardVisualPolishSR020CTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sr020c", password="secret")
        self.source = Source.objects.create(name="Idealista", code="idealista-sr020c")
        self.profile = SearchProfile.objects.create(
            owner=self.user,
            name="Viviendas en Pozoblanco",
            operation_type=SearchProfile.OperationType.SALE,
            province="Córdoba",
            zone="Pozoblanco",
            property_types=["house"],
        )
        self.run = SearchRun.objects.create(search_profile=self.profile)
        self.client.force_login(self.user)

    def make_capture(
        self,
        *,
        suffix="base",
        status=CapturedProperty.Status.IN_REVIEW,
        availability=CapturedProperty.AvailabilityVerificationState.CONFIRMED,
        description="Descripción real del anunciante",
    ):
        values = {
            "owner": self.user,
            "source": self.source,
            "search_profile": self.profile,
            "search_run": self.run,
            "source_external_id": f"sr020c-{suffix}",
            "source_url": "https://www.idealista.com/inmueble/020c/",
            "title": "Casa con patio",
            "description_raw": description,
            "operation_type": CapturedProperty.OperationType.SALE,
            "property_type": CapturedProperty.PropertyType.HOUSE,
            "province": "Córdoba",
            "municipality": "Pozoblanco",
            "price": 33000,
            "area_m2": 99,
            "bedrooms": 3,
            "bathrooms": 1,
            "status": status,
            "availability_verification_state": availability,
            "availability_verification_note": "Confirmado por teléfono",
        }
        if availability != CapturedProperty.AvailabilityVerificationState.UNKNOWN:
            values["availability_verified_at"] = timezone.now()
            values["availability_verified_by"] = self.user
        return CapturedProperty.objects.create(**values)

    def make_opportunity(self, capture):
        return PropertyOpportunity.objects.create(
            owner=self.user,
            captured_property=capture,
            search_profile=self.profile,
            title=capture.title,
            asking_price_current=31000,
            status=PropertyOpportunity.Status.ANALYSIS,
            priority=PropertyOpportunity.Priority.HIGH,
            next_action_type=PropertyOpportunity.NextActionType.CALL,
            next_action_notes="Llamar al agente",
            province=capture.province,
            municipality=capture.municipality,
        )

    def get(self, name, *args):
        return self.client.get(reverse(name, args=args), secure=True)

    def test_property_summary_has_structured_fact_groups_and_labeled_states(self):
        capture = self.make_capture()
        response = self.get("capturedproperty_detail", capture.pk)
        content = response.content.decode()
        self.assertIn('class="property-summary__facts"', content)
        self.assertIn('class="property-summary__states"', content)
        self.assertIn('class="property-summary__context"', content)
        self.assertContains(response, "Venta")
        self.assertContains(response, "Casa")
        self.assertContains(response, "99 m²")
        self.assertContains(response, "3 hab.")
        self.assertContains(response, "Disponibilidad: Disponible")
        self.assertContains(response, "Captación: En revisión")

    def test_capture_price_has_primary_detail_presentation(self):
        capture = self.make_capture()
        response = self.get("capturedproperty_detail", capture.pk)
        content = response.content.decode()
        self.assertIn("property-summary--detail", content)
        self.assertIn('class="property-summary__price"', content)
        self.assertContains(response, "Precio")
        self.assertContains(response, "33000 €")

    def test_blocked_conversion_states_are_non_actionable_text(self):
        unavailable = self.make_capture(suffix="unavailable", availability="unavailable")
        unknown = self.make_capture(suffix="unknown", availability="unknown")
        response = self.get("capturedproperty_list")
        content = response.content.decode()
        self.assertIn("No disponible · No convertible", content)
        self.assertIn("Verifica disponibilidad", content)
        self.assertIn('class="conversion-blocked-state', content)
        self.assertNotIn(f'/app/captacion/{unavailable.pk}/convertir/', content)
        self.assertNotIn(f'/app/captacion/{unknown.pk}/convertir/', content)
        self.assertNotIn("<button disabled", content)

    def test_confirmed_and_validated_actions_remain_governed(self):
        confirmed = self.make_capture(suffix="confirmed")
        validated = self.make_capture(suffix="validated", status="validated")
        opportunity = self.make_opportunity(validated)
        response = self.get("capturedproperty_list")
        content = response.content.decode()
        self.assertIn(f'/app/captacion/{confirmed.pk}/convertir/', content)
        validated_response = self.client.get(
            reverse("capturedproperty_list") + "?status=validated",
            secure=True,
        )
        validated_content = validated_response.content.decode()
        self.assertNotIn(f'/app/captacion/{validated.pk}/convertir/', validated_content)
        self.assertIn(f'/app/oportunidades/{opportunity.pk}/', validated_content)

    def test_only_narrow_sooi_technical_description_is_suppressed(self):
        technical = self.make_capture(
            suffix="technical",
            description="SOOI V2.6.1 · idealista · availability_unknown:http_403",
        )
        genuine = self.make_capture(suffix="genuine")
        technical_response = self.get("capturedproperty_detail", technical.pk)
        genuine_response = self.get("capturedproperty_detail", genuine.pk)
        self.assertNotContains(technical_response, "availability_unknown:http_403")
        self.assertNotContains(technical_response, "Descripción original")
        self.assertContains(genuine_response, "Descripción original")
        self.assertContains(genuine_response, "Descripción real del anunciante")

    def test_verification_note_lives_once_in_capture_verification_section(self):
        capture = self.make_capture()
        response = self.get("capturedproperty_detail", capture.pk)
        content = response.content.decode()
        self.assertContains(response, "Verificación de disponibilidad")
        self.assertContains(response, "Nota de verificación")
        self.assertEqual(content.count("Confirmado por teléfono"), 1)

    def test_opportunity_current_price_and_capture_evidence_are_preserved(self):
        capture = self.make_capture(status="validated")
        opportunity = self.make_opportunity(capture)
        response = self.get("opportunity_detail", opportunity.pk)
        content = response.content.decode()
        self.assertContains(response, "Precio actual")
        self.assertContains(response, "31000")
        self.assertContains(response, "Disponibilidad: Disponible")
        self.assertContains(response, "Confirmado por teléfono")
        self.assertContains(response, "Idealista")
        self.assertContains(response, "Viviendas en Pozoblanco")
        self.assertContains(response, "Llamar al agente")
        self.assertNotIn(">None<", content)
        self.assertNotIn("|safe", content)

    def test_provider_content_remains_escaped(self):
        capture = self.make_capture(description='<script>alert("source")</script>')
        response = self.get("capturedproperty_detail", capture.pk)
        content = response.content.decode()
        self.assertNotIn('<script>alert("source")</script>', content)
        self.assertIn("&lt;script&gt;", content)
