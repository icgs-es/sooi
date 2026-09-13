"""Acceptance coverage for the SR0.13A first-search authorization presentation."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import SearchProfile, SearchRun
from .search_budget_ux import MODE_AUTHORIZATION, mode_previews


FLAGS_ON = {
    "SOOI_SEARCH_GOVERNANCE_V1": "1",
    "SOOI_GEOGRAPHY_RUNTIME_V1": "1",
}


class FirstSearchUXSR013ATests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="sr013a-owner", password="test",
        )
        self.profile = SearchProfile.objects.create(
            owner=self.owner,
            name="Compra comercial",
            operation_type=SearchProfile.OperationType.SALE,
            province="Málaga",
            zone="Puerto la Torre",
            property_types=["flat"],
            max_price=Decimal("300000"),
            min_bedrooms=2,
        )
        self.client.force_login(self.owner)

    @patch.dict("os.environ", FLAGS_ON, clear=False)
    @patch("apps.busquedas.commercial_metering.get_commercial_usage_summary")
    @patch("apps.busquedas.commercial_metering.commercial_metering_enabled")
    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(True, ""))
    def test_first_search_page_has_commercial_hierarchy_and_preserves_detail(
        self, _allowed, metering_enabled, usage_summary,
    ):
        metering_enabled.return_value = True
        usage_summary.return_value = {"credits_remaining": Decimal("17")}

        response = self.client.get(reverse("searchprofile_detail", args=[self.profile.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu búsqueda")
        self.assertContains(response, "Elige el nivel de búsqueda")
        self.assertContains(response, 'type="radio" name="search_mode"', count=4)
        for label in ("Gratis", "Eco", "Amplia", "Profunda"):
            self.assertContains(response, label)
        self.assertContains(response, "Recomendado", count=1)
        self.assertContains(response, "Saldo disponible: 17 Créditos de Búsqueda", count=1)
        self.assertContains(response, "Buscar ahora")
        self.assertContains(response, "Ver detalle técnico")
        self.assertContains(response, "lotes")
        self.assertContains(response, "fuentes externas")
        self.assertContains(response, "Créditos máximos para esta ejecución")
        self.assertContains(response, "Alias reconocido")
        self.assertNotContains(response, "canonical_key")

    @patch.dict("os.environ", FLAGS_ON, clear=False)
    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(True, ""))
    @patch("apps.busquedas.views.run_search_profile_task.delay")
    def test_render_keeps_free_default_and_authorizes_nothing(self, delay, _allowed):
        before = SearchRun.objects.count()
        response = self.client.get(reverse("searchprofile_detail", args=[self.profile.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'value="free" data-budget="0" checked',
            html=False,
        )
        self.assertNotContains(response, 'value="eco" data-budget="4" checked', html=False)
        self.assertEqual(SearchRun.objects.count(), before)
        delay.assert_not_called()

    @patch("apps.busquedas.search_budget_ux.ai_discovery_permitted", return_value=(True, ""))
    def test_previews_do_not_change_credit_calculation_or_caps(self, _allowed):
        previews = {item["mode"]: item for item in mode_previews(self.profile, self.owner)}

        self.assertEqual(tuple(previews), ("free", "eco", "amplia", "profunda"))
        for mode, preview in previews.items():
            self.assertEqual(preview["default_authorization"], MODE_AUTHORIZATION[mode]["default"])
            self.assertEqual(preview["maximum_authorization"], MODE_AUTHORIZATION[mode]["maximum"])
        self.assertEqual(previews["free"]["default_authorization"], Decimal("0"))
