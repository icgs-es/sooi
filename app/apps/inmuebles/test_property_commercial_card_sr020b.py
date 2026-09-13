from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.busquedas.models import SearchProfile, SearchRun
from apps.fuentes.models import Source
from apps.seguimiento.models import PropertyOpportunity

from .models import CapturedProperty


User = get_user_model()


class PropertyCommercialCardSR020BTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="commercial-card", password="secret")
        self.source = Source.objects.create(name="Idealista", code="idealista-sr020b")
        self.profile = SearchProfile.objects.create(
            owner=self.user,
            name="Viviendas en Pozoblanco hasta 180.000 €",
            operation_type=SearchProfile.OperationType.SALE,
            province="Córdoba",
            zone="Pozoblanco",
            property_types=["flat"],
            max_price=180000,
        )
        self.run = SearchRun.objects.create(search_profile=self.profile)

    def make_capture(
        self,
        *,
        status=CapturedProperty.Status.IN_REVIEW,
        availability=CapturedProperty.AvailabilityVerificationState.CONFIRMED,
        suffix="base",
        optional=True,
    ):
        values = {
            "owner": self.user,
            "search_profile": self.profile if optional else None,
            "search_run": self.run if optional else None,
            "source": self.source,
            "source_external_id": f"sr020b-{suffix}",
            "source_url": "https://www.idealista.com/inmueble/020/" if optional else "",
            "title": "Piso luminoso en el centro",
            "operation_type": CapturedProperty.OperationType.SALE,
            "property_type": CapturedProperty.PropertyType.FLAT,
            "province": "Córdoba" if optional else "",
            "municipality": "Pozoblanco" if optional else "",
            "zone_text": "Centro" if optional else "",
            "price": 175000 if optional else None,
            "area_m2": 120 if optional else None,
            "bedrooms": 3 if optional else None,
            "bathrooms": 2 if optional else None,
            "status": status,
            "availability_verification_state": availability,
            "availability_verification_note": "Anunciante confirma disponibilidad",
            "possible_duplicate": optional,
        }
        if availability != CapturedProperty.AvailabilityVerificationState.UNKNOWN:
            values["availability_verified_at"] = timezone.now()
            values["availability_verified_by"] = self.user
        return CapturedProperty.objects.create(**values)

    def make_opportunity(self, capture, **overrides):
        values = {
            "owner": self.user,
            "captured_property": capture,
            "search_profile": capture.search_profile,
            "title": capture.title,
            "asking_price_current": 169000,
            "status": PropertyOpportunity.Status.ANALYSIS,
            "priority": PropertyOpportunity.Priority.HIGH,
            "next_action_type": PropertyOpportunity.NextActionType.CALL,
            "next_action_notes": "Llamar al agente",
            "next_review_at": timezone.now(),
            "province": capture.province,
            "municipality": capture.municipality,
        }
        values.update(overrides)
        return PropertyOpportunity.objects.create(**values)

    def get(self, name, *args):
        self.client.force_login(self.user)
        return self.client.get(reverse(name, args=args), secure=True)

    def test_capture_list_shows_commercial_identity_facts_and_context(self):
        self.make_capture()
        response = self.get("capturedproperty_list")
        for expected in (
            "Piso luminoso en el centro",
            "Pozoblanco",
            "Córdoba",
            "175000 €",
            "120 m²",
            "3 hab.",
            "2 baños",
            "Disponibilidad: Disponible",
            "Idealista",
            "Viviendas en Pozoblanco hasta 180.000 €",
            "Posible duplicado",
        ):
            self.assertContains(response, expected)

    def test_capture_list_action_governance_is_explanatory(self):
        unknown = self.make_capture(availability="unknown", suffix="unknown")
        unavailable = self.make_capture(availability="unavailable", suffix="unavailable")
        confirmed = self.make_capture(status="captured", suffix="confirmed")
        discarded = self.make_capture(status="discarded", suffix="discarded")
        validated = self.make_capture(status="validated", suffix="validated")
        opportunity = self.make_opportunity(validated)

        response = self.get("capturedproperty_list")
        content = response.content.decode()
        self.assertIn("Disponibilidad: Sin verificar", content)
        self.assertIn("Disponibilidad: No disponible", content)
        self.assertIn("Verifica disponibilidad", content)
        self.assertIn("No disponible", content)
        self.assertIn("Descartado", content)
        self.assertIn(f'/app/captacion/{confirmed.pk}/convertir/', content)
        self.assertNotIn(f'/app/captacion/{unknown.pk}/convertir/', content)
        self.assertNotIn(f'/app/captacion/{unavailable.pk}/convertir/', content)
        self.assertNotIn(f'/app/captacion/{discarded.pk}/convertir/', content)
        validated_response = self.client.get(
            reverse("capturedproperty_list") + "?status=validated",
            secure=True,
        )
        validated_content = validated_response.content.decode()
        self.assertNotIn(f'/app/captacion/{validated.pk}/convertir/', validated_content)
        self.assertIn(f'/app/oportunidades/{opportunity.pk}/', validated_content)

    def test_capture_detail_shows_listing_verification_search_and_duplicate_evidence(self):
        capture = self.make_capture()
        response = self.get("capturedproperty_detail", capture.pk)
        for expected in (
            "Ver anuncio original",
            "Anunciante confirma disponibilidad",
            self.user.username,
            "Viviendas en Pozoblanco hasta 180.000 €",
            "Búsqueda ejecutada el",
            "Posible duplicado",
            "Captación: En revisión",
        ):
            self.assertContains(response, expected)
        self.assertContains(response, capture.availability_verified_at.strftime("%d/%m/%Y"))

    def test_opportunity_list_is_property_first_and_keeps_current_price_workflow(self):
        capture = self.make_capture(status="validated")
        opportunity = self.make_opportunity(capture)
        response = self.get("opportunity_list")
        for expected in (
            capture.title,
            "Pozoblanco",
            "Disponibilidad: Disponible",
            "169000",
            "En análisis",
            "Alta",
            "Llamar",
            "Llamar al agente",
        ):
            self.assertContains(response, expected)
        self.assertContains(response, reverse("opportunity_detail", args=[opportunity.pk]))

    def test_opportunity_detail_shows_property_provenance_and_commercial_action(self):
        capture = self.make_capture(status="validated")
        opportunity = self.make_opportunity(capture)
        response = self.get("opportunity_detail", opportunity.pk)
        for expected in (
            capture.title,
            "Disponibilidad: Disponible",
            "Anunciante confirma disponibilidad",
            self.user.username,
            "Idealista",
            "Ver anuncio original",
            "Viviendas en Pozoblanco hasta 180.000 €",
            "Ver inmueble captado",
            "Búsqueda ejecutada el",
            "Siguiente acción",
            "Llamar",
            "Llamar al agente",
        ):
            self.assertContains(response, expected)
        self.assertContains(response, reverse("capturedproperty_detail", args=[capture.pk]))

    def test_missing_optional_fields_render_gracefully_without_literal_none(self):
        capture = self.make_capture(optional=False, availability="unknown", suffix="minimal")
        response = self.get("capturedproperty_detail", capture.pk)
        self.assertContains(response, "Ubicación no indicada")
        self.assertContains(response, "Precio no indicado")
        self.assertContains(response, "Disponibilidad: Sin verificar")
        self.assertNotContains(response, ">None<")
        self.assertNotContains(response, ">null<")

    def test_provider_text_is_autoescaped_and_no_raw_html_is_introduced(self):
        capture = self.make_capture()
        capture.title = '<script>alert("provider")</script>'
        capture.availability_verification_note = "<b>evidencia</b>"
        capture.save(update_fields=["title", "availability_verification_note", "updated_at"])
        response = self.get("capturedproperty_detail", capture.pk)
        content = response.content.decode()
        self.assertNotIn('<script>alert("provider")</script>', content)
        self.assertNotIn("<b>evidencia</b>", content)
        self.assertIn("&lt;script&gt;", content)
        self.assertIn("&lt;b&gt;evidencia&lt;/b&gt;", content)

    def test_list_querysets_preload_shared_card_relations(self):
        capture = self.make_capture(status="validated")
        self.make_opportunity(capture)

        self.client.force_login(self.user)
        capture_response = self.client.get(
            reverse("capturedproperty_list") + "?status=validated",
            secure=True,
        )
        rendered_capture = list(capture_response.context["captured_properties"])[0]
        self.assertTrue(
            {"source", "search_profile", "search_run", "availability_verified_by", "opportunity"}
            <= set(rendered_capture._state.fields_cache)
        )

        opportunity_response = self.get("opportunity_list")
        rendered_opportunity = list(opportunity_response.context["opportunities"])[0]
        self.assertIn("captured_property", rendered_opportunity._state.fields_cache)
        rendered_origin = rendered_opportunity.captured_property
        self.assertTrue(
            {"source", "search_profile", "search_run", "availability_verified_by"}
            <= set(rendered_origin._state.fields_cache)
        )
