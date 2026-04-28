from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.busquedas.models import SearchProfile
from apps.fuentes.models import Source
from apps.inmuebles.forms import CapturedPropertyManualForm
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import PropertyOpportunity


User = get_user_model()


class SearchProfileOperationalRulesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="tester",
            email="tester@example.com",
            password="testpass123",
        )
        self.other_user = User.objects.create_user(
            username="other",
            email="other@example.com",
            password="testpass123",
        )

        self.source = Source.objects.create(
            name="Idealista",
            code="idealista",
        )

    def make_search(self, name="Búsqueda test", owner=None, color="blue", status=None):
        return SearchProfile.objects.create(
            owner=owner or self.user,
            name=name,
            operation_type=SearchProfile.OperationType.RENT,
            province="Córdoba",
            zone="Pozoblanco",
            property_types=["flat"],
            max_price=600,
            min_bedrooms=1,
            status=status or SearchProfile.Status.ACTIVE,
            is_active=True,
            color=color,
            automation_enabled=False,
        )

    def test_cannot_create_more_than_six_active_searches(self):
        self.client.login(username="tester", password="testpass123")

        colors = ["blue", "green", "orange", "purple", "red", "teal"]
        for i, color in enumerate(colors):
            self.make_search(name=f"Búsqueda {i}", color=color)

        response = self.client.post(
            reverse("searchprofile_create"),
            data={
                "name": "Séptima búsqueda",
                "operation_type": SearchProfile.OperationType.RENT,
                "province": "Córdoba",
                "zone": "Centro",
                "property_types": ["flat"],
                "max_price": "600",
                "min_bedrooms": "1",
                "ai_prompt": "",
                "automation_enabled": "",
                "notes": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            SearchProfile.objects.filter(
                owner=self.user,
                status__in=[SearchProfile.Status.ACTIVE, SearchProfile.Status.PAUSED],
            ).count(),
            6,
        )
        self.assertFalse(
            SearchProfile.objects.filter(owner=self.user, name="Séptima búsqueda").exists()
        )

    def test_manual_capture_requires_search_profile(self):
        form = CapturedPropertyManualForm(
            user=self.user,
            data={
                "source": self.source.id,
                "operation_type": CapturedProperty.OperationType.RENT,
                "property_type": CapturedProperty.PropertyType.FLAT,
                "title": "Ático en alquiler",
                "source_url": "https://www.idealista.com/inmueble/test/",
                "price": "450.00",
                "province": "Córdoba",
                "municipality": "Hinojosa del Duque",
                "zone_text": "Centro",
                "bedrooms": "2",
                "bathrooms": "1",
                "area_m2": "70",
                "description_raw": "Descripción mínima operativa.",
                "manual_notes": "Contacto pendiente desde fuente original.",
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("search_profile", form.errors)

    def test_manual_capture_accepts_only_user_search_profile(self):
        other_search = self.make_search(
            name="Búsqueda de otro usuario",
            owner=self.other_user,
            color="green",
        )

        form = CapturedPropertyManualForm(
            user=self.user,
            data={
                "search_profile": other_search.id,
                "source": self.source.id,
                "operation_type": CapturedProperty.OperationType.RENT,
                "property_type": CapturedProperty.PropertyType.FLAT,
                "title": "Ático en alquiler",
                "source_url": "https://www.idealista.com/inmueble/test/",
                "price": "450.00",
                "province": "Córdoba",
                "municipality": "Hinojosa del Duque",
                "zone_text": "Centro",
                "bedrooms": "2",
                "bathrooms": "1",
                "area_m2": "70",
                "description_raw": "Descripción mínima operativa.",
                "manual_notes": "Contacto pendiente desde fuente original.",
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("search_profile", form.errors)

    def test_opportunity_inherits_search_profile_from_capture(self):
        self.client.login(username="tester", password="testpass123")
        search = self.make_search(name="Córdoba alquiler", color="orange")

        captured = CapturedProperty.objects.create(
            owner=self.user,
            search_profile=search,
            source=self.source,
            entry_mode=CapturedProperty.EntryMode.MANUAL,
            operation_type=CapturedProperty.OperationType.RENT,
            property_type=CapturedProperty.PropertyType.FLAT,
            title="Ático en alquiler",
            source_url="https://www.idealista.com/inmueble/test/",
            price="450.00",
            province="Córdoba",
            municipality="Hinojosa del Duque",
            zone_text="Centro",
            status=CapturedProperty.Status.CAPTURED,
            review_status=CapturedProperty.ReviewStatus.PENDING,
            captured_at=timezone.now(),
        )

        response = self.client.post(
            reverse("capturedproperty_convert_to_opportunity", args=[captured.id]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)

        opportunity = PropertyOpportunity.objects.get(captured_property=captured)
        self.assertEqual(opportunity.owner, self.user)
        self.assertEqual(opportunity.search_profile, search)

    def test_closing_empty_search_releases_color_and_active_slot(self):
        self.client.login(username="tester", password="testpass123")
        search = self.make_search(name="Córdoba alquiler", color="purple")

        response = self.client.post(
            reverse("searchprofile_close_empty", args=[search.id]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)

        search.refresh_from_db()
        self.assertEqual(search.status, SearchProfile.Status.CLOSED_EMPTY)
        self.assertFalse(search.is_active)
        self.assertEqual(search.color, "")
        self.assertIsNotNone(search.closed_at)

        active_count = SearchProfile.objects.filter(
            owner=self.user,
            status__in=[SearchProfile.Status.ACTIVE, SearchProfile.Status.PAUSED],
        ).count()

        self.assertEqual(active_count, 0)
