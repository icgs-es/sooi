from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.busquedas.models import SearchProfile
from apps.core.models import SystemSettings, UserProfile
from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import FollowUpTask, PropertyOpportunity


class WP08DashboardContracts(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = Source.objects.create(name="Sintética", code="wp08")
        SystemSettings.objects.create(pk=1, company_name="SOOI")

    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="secret")
        self.client.force_login(self.user)

    def search(self, owner=None, name="Búsqueda propia"):
        return SearchProfile.objects.create(owner=owner or self.user, name=name, province="Madrid")

    def capture(self, owner=None, search=None, title="Captación propia"):
        owner = owner or self.user
        return CapturedProperty.objects.create(
            owner=owner,
            search_profile=search or self.search(owner=owner),
            source=self.source,
            title=title,
            property_type=CapturedProperty.PropertyType.FLAT,
        )

    def opportunity(self, owner=None, capture=None, title="Oportunidad propia", **kwargs):
        owner = owner or self.user
        return PropertyOpportunity.objects.create(
            owner=owner,
            captured_property=capture or self.capture(owner=owner),
            title=title,
            **kwargs,
        )

    @override_settings(SOOI_WP08_ENABLED=False)
    def test_flag_disabled_preserves_legacy_dashboard(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Panel operativo")
        self.assertNotContains(response, "Tu recorrido")
        self.assertIsNone(response.context["wp08"])

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_flag_enabled_renders_server_side_guide(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Tu recorrido")
        self.assertIsNotNone(response.context["wp08"])

    def test_login_settings_contract(self):
        self.assertEqual(settings.LOGIN_URL, "/accounts/login/")
        self.assertEqual(settings.LOGIN_REDIRECT_URL, "/app/")
        self.assertFalse(settings.SOOI_WP08_ENABLED)

    def test_login_preserves_next(self):
        self.client.logout()
        response = self.client.post(
            reverse("login") + "?next=/app/agenda/",
            {"username": "owner", "password": "secret", "next": "/app/agenda/"},
        )
        self.assertRedirects(response, "/app/agenda/", fetch_redirect_response=False)

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_empty_account_has_one_canonical_primary_cta(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["wp08"]["primary_cta"], {
            "label": "Crear primera búsqueda", "url": "/app/busquedas/nuevo/"
        })
        self.assertContains(response, "wp08-guide__primary", count=1)

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_search_advances_to_related_capture_journey(self):
        search = self.search()
        wp08 = self.client.get(reverse("dashboard")).context["wp08"]
        self.assertEqual(wp08["primary_cta"]["url"], f"/app/busquedas/{search.pk}/")

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_capture_advances_to_owned_capture_review(self):
        capture = self.capture()
        wp08 = self.client.get(reverse("dashboard")).context["wp08"]
        self.assertEqual(wp08["primary_cta"]["url"], f"/app/captacion/{capture.pk}/")

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_opportunity_advances_to_next_action_edit(self):
        opportunity = self.opportunity()
        wp08 = self.client.get(reverse("dashboard")).context["wp08"]
        self.assertEqual(wp08["primary_cta"]["url"], f"/app/oportunidades/{opportunity.pk}/editar/")

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_dated_task_completes_journey_and_is_next_action(self):
        opportunity = self.opportunity()
        task = FollowUpTask.objects.create(
            owner=self.user, property_opportunity=opportunity, title="Llamar mañana",
            due_date=timezone.now() + timedelta(days=1),
        )
        wp08 = self.client.get(reverse("dashboard")).context["wp08"]
        self.assertEqual(wp08["primary_cta"]["url"], f"/app/tareas/{task.pk}/")
        self.assertTrue(wp08["milestones"][-1]["complete"])

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_automatic_review_task_deduplicates_opportunity_review(self):
        due = timezone.now() + timedelta(days=1)
        opportunity = self.opportunity(next_review_at=due)
        task = FollowUpTask.objects.get(
            owner=self.user,
            property_opportunity=opportunity,
            task_type=FollowUpTask.TaskType.REVIEW,
        )
        wp08 = self.client.get(reverse("dashboard")).context["wp08"]
        self.assertEqual(wp08["next_action"]["url"], f"/app/tareas/{task.pk}/")
        self.assertNotIn("Revisar Oportunidad propia", wp08["next_action"]["label"])

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_active_trial_comes_from_entitlement(self):
        now = timezone.now()
        UserProfile.objects.create(user=self.user, is_trial=True, trial_start=now, trial_end=now + timedelta(days=2))
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["wp08"]["plan_name"], "Professional")
        self.assertTrue(response.context["trial_info"]["active"])

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_expired_trial_comes_from_entitlement(self):
        now = timezone.now()
        UserProfile.objects.create(user=self.user, is_trial=True, trial_start=now - timedelta(days=2), trial_end=now - timedelta(days=1))
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["wp08"]["plan_name"], "Starter")
        self.assertTrue(response.context["trial_info"]["expired"])

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_starter_plan_display_uses_entitlement(self):
        UserProfile.objects.create(user=self.user, is_trial=False, plan="starter")
        self.assertEqual(self.client.get(reverse("dashboard")).context["wp08"]["plan_name"], "Starter")

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_professional_plan_display_uses_entitlement(self):
        UserProfile.objects.create(user=self.user, is_trial=False, plan="professional")
        self.assertEqual(self.client.get(reverse("dashboard")).context["wp08"]["plan_name"], "Professional")

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_other_owner_data_never_advances_or_appears(self):
        other = get_user_model().objects.create_user("other")
        search = self.search(owner=other, name="SECRETA")
        capture = self.capture(owner=other, search=search, title="CAPTACIÓN SECRETA")
        self.opportunity(owner=other, capture=capture, title="OPORTUNIDAD SECRETA")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["wp08"]["primary_cta"]["label"], "Crear primera búsqueda")
        self.assertNotContains(response, "SECRETA")

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_nearest_owned_active_action_wins(self):
        opportunity = self.opportunity()
        later = FollowUpTask.objects.create(owner=self.user, title="Más tarde", due_date=timezone.now() + timedelta(days=3))
        sooner = FollowUpTask.objects.create(owner=self.user, property_opportunity=opportunity, title="Más próxima", due_date=timezone.now() + timedelta(hours=1))
        FollowUpTask.objects.create(owner=self.user, title="Terminada", status=FollowUpTask.Status.DONE, due_date=timezone.now())
        wp08 = self.client.get(reverse("dashboard")).context["wp08"]
        self.assertEqual(wp08["next_action"]["url"], f"/app/tareas/{sooner.pk}/")
        self.assertNotEqual(wp08["next_action"]["url"], f"/app/tareas/{later.pk}/")

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_navigation_has_landmark_and_current_page(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, '<nav class="nav nav-grouped" aria-label="Navegación principal">')
        self.assertContains(response, 'href="/app/" aria-current="page"')

    def test_mobile_and_focus_accessibility_signals_are_present(self):
        root = Path(__file__).parents[1]
        css = (root / "apps/core/static/core/sooi_modern.css").read_text()
        template = (root / "templates/core/base_private.html").read_text()
        self.assertIn(":focus-visible", css)
        self.assertIn("@media (max-width: 980px)", css)
        self.assertIn(".sidebar { display: flex !important", css)
        self.assertIn('aria-label="Navegación principal"', template)

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_primary_cta_requires_no_javascript(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, '<a class="btn btn-primary wp08-guide__primary" href="/app/busquedas/nuevo/">')

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_query_count_is_constant_with_many_rows(self):
        for index in range(12):
            search = self.search(name=f"Búsqueda {index}")
            capture = self.capture(search=search, title=f"Captación {index}")
            opportunity = self.opportunity(capture=capture, title=f"Oportunidad {index}")
            FollowUpTask.objects.create(owner=self.user, property_opportunity=opportunity, title=f"Tarea {index}", due_date=timezone.now() + timedelta(days=index + 1))
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 30)

    @override_settings(SOOI_WP08_ENABLED=True)
    def test_dashboard_render_performs_no_data_writes(self):
        self.opportunity()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        sql = " ".join(query["sql"] for query in queries.captured_queries).upper()
        self.assertNotRegex(sql, r"\b(INSERT|UPDATE|DELETE)\b")
