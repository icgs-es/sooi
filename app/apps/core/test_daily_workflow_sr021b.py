from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.busquedas.models import SearchProfile, SearchRun
from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import FollowUpTask, PropertyOpportunity

from .daily_workflow import build_daily_workflow
from .models import SystemSettings


User = get_user_model()


class DailyWorkflowSR021BTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="daily-owner", password="secret")
        self.other = User.objects.create_user(username="daily-other", password="secret")
        self.source = Source.objects.create(name="Idealista", code="idealista-sr021b")
        self.profile = SearchProfile.objects.create(
            owner=self.user,
            name="Viviendas en Pozoblanco",
            operation_type=SearchProfile.OperationType.SALE,
            province="Córdoba",
            zone="Pozoblanco",
            property_types=["house"],
        )
        self.run = SearchRun.objects.create(search_profile=self.profile)
        self.now = datetime(2026, 8, 22, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))

    def make_capture(
        self,
        *,
        owner=None,
        suffix="base",
        status=CapturedProperty.Status.IN_REVIEW,
        availability=CapturedProperty.AvailabilityVerificationState.UNKNOWN,
        review_status=CapturedProperty.ReviewStatus.REVIEWED,
        duplicate=False,
    ):
        owner = owner or self.user
        values = {
            "owner": owner,
            "source": self.source,
            "search_profile": self.profile if owner == self.user else None,
            "search_run": self.run if owner == self.user else None,
            "source_external_id": f"sr021b-{suffix}",
            "title": f"Casa diaria {suffix}",
            "operation_type": CapturedProperty.OperationType.SALE,
            "property_type": CapturedProperty.PropertyType.HOUSE,
            "province": "Córdoba",
            "municipality": "Pozoblanco",
            "price": 120000,
            "status": status,
            "review_status": review_status,
            "availability_verification_state": availability,
            "possible_duplicate": duplicate,
        }
        if availability != CapturedProperty.AvailabilityVerificationState.UNKNOWN:
            values["availability_verified_at"] = self.now - timedelta(hours=1)
            values["availability_verified_by"] = owner
        return CapturedProperty.objects.create(**values)

    def make_opportunity(self, capture, *, suffix="base", review_at=None, status=None, owner=None):
        owner = owner or capture.owner
        return PropertyOpportunity.objects.create(
            owner=owner,
            captured_property=capture,
            search_profile=capture.search_profile,
            title=f"Oportunidad diaria {suffix}",
            status=status or PropertyOpportunity.Status.ACTIVE,
            priority=PropertyOpportunity.Priority.HIGH,
            next_action_type=PropertyOpportunity.NextActionType.CALL,
            next_action_notes="Llamar al agente",
            next_review_at=review_at,
        )

    def make_task(self, *, suffix="base", due_at=None, owner=None):
        return FollowUpTask.objects.create(
            owner=owner or self.user,
            title=f"Tarea diaria {suffix}",
            task_type=FollowUpTask.TaskType.FOLLOW_UP,
            status=FollowUpTask.Status.OPEN,
            priority=FollowUpTask.Priority.HIGH,
            due_date=due_at,
        )

    def dashboard(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("dashboard"), secure=True)

    def test_dashboard_requires_auth_and_is_labeled_hoy(self):
        response = self.client.get(reverse("dashboard"), secure=True)
        self.assertEqual(response.status_code, 302)
        response = self.dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hoy en SOOI")
        self.assertContains(response, "¿Qué requiere tu atención?")

    def test_capture_categories_counts_exclusions_and_precedence(self):
        ready = self.make_capture(suffix="ready", availability="confirmed", duplicate=True)
        unknown = self.make_capture(suffix="unknown", duplicate=True)
        review = self.make_capture(
            suffix="review",
            availability="confirmed",
            review_status="pending",
        )
        duplicate = self.make_capture(suffix="duplicate", availability="confirmed", duplicate=True)
        unavailable = self.make_capture(suffix="unavailable", availability="unavailable")
        discarded = self.make_capture(suffix="discarded", status="discarded", availability="confirmed")
        converted = self.make_capture(suffix="converted", status="validated", availability="confirmed")
        self.make_opportunity(converted, suffix="converted")

        workflow = build_daily_workflow(self.user, self.now)
        self.assertEqual(workflow["counters"]["captures_ready"], 3)
        self.assertEqual(workflow["counters"]["captures_availability"], 1)
        capture_items = workflow["pending_captures"]
        self.assertEqual(
            [(item["object"].pk, item["category"]) for item in capture_items],
            [(duplicate.pk, "ready"), (review.pk, "ready"), (ready.pk, "ready"), (unknown.pk, "availability")],
        )
        shown = {item["object"].pk for item in capture_items}
        self.assertFalse({unavailable.pk, discarded.pk, converted.pk} & shown)

    def test_opportunity_overdue_today_no_next_and_past_today(self):
        overdue_capture = self.make_capture(suffix="opp-overdue", status="validated", availability="confirmed")
        today_capture = self.make_capture(suffix="opp-today", status="validated", availability="confirmed")
        no_next_capture = self.make_capture(suffix="opp-none", status="validated", availability="confirmed")
        overdue = self.make_opportunity(overdue_capture, suffix="overdue", review_at=self.now - timedelta(minutes=1))
        today = self.make_opportunity(today_capture, suffix="today", review_at=self.now + timedelta(hours=2))
        no_next = self.make_opportunity(no_next_capture, suffix="none", review_at=None)

        workflow = build_daily_workflow(self.user, self.now)
        self.assertEqual(workflow["counters"]["opportunities_overdue"], 1)
        self.assertEqual(workflow["counters"]["opportunities_today"], 1)
        self.assertEqual(workflow["counters"]["opportunities_no_next"], 1)
        categories = {item["object"].pk: item["category"] for item in workflow["attention"]}
        self.assertEqual(categories[overdue.pk], "overdue")
        self.assertEqual(categories[today.pk], "today")
        self.assertEqual(categories[no_next.pk], "no_next")

    def test_tasks_and_mirrored_review_task_are_not_double_counted(self):
        overdue = self.make_task(suffix="overdue", due_at=self.now - timedelta(hours=1))
        today = self.make_task(suffix="today", due_at=self.now + timedelta(hours=1))
        capture = self.make_capture(suffix="mirrored", status="validated", availability="confirmed")
        opportunity = self.make_opportunity(capture, suffix="mirrored", review_at=self.now + timedelta(hours=2))
        mirrored = FollowUpTask.objects.get(
            property_opportunity=opportunity,
            task_type=FollowUpTask.TaskType.REVIEW,
            status=FollowUpTask.Status.OPEN,
        )

        workflow = build_daily_workflow(self.user, self.now)
        self.assertEqual(workflow["counters"]["tasks_overdue"], 1)
        self.assertEqual(workflow["counters"]["tasks_today"], 1)
        self.assertEqual(workflow["counters"]["opportunities_today"], 1)
        task_ids = {item["object"].pk for item in workflow["next_actions"] if item["kind"] == "task"}
        self.assertEqual(task_ids, {overdue.pk, today.pk})
        self.assertNotIn(mirrored.pk, task_ids)

    def test_every_attention_item_has_reason_and_order_is_deterministic(self):
        capture = self.make_capture(suffix="ready", availability="confirmed")
        opportunity_capture = self.make_capture(suffix="opp", status="validated", availability="confirmed")
        opportunity = self.make_opportunity(
            opportunity_capture,
            suffix="overdue",
            review_at=self.now - timedelta(days=1),
        )
        task = self.make_task(suffix="today", due_at=self.now + timedelta(hours=1))
        workflow = build_daily_workflow(self.user, self.now)
        self.assertTrue(all(item["reason_label"] for item in workflow["attention"]))
        self.assertEqual(
            [(item["kind"], item["object"].pk) for item in workflow["attention"]],
            [("opportunity", opportunity.pk), ("task", task.pk), ("capture", capture.pk)],
        )

    def test_dashboard_has_direct_links_and_action_reasons(self):
        capture = self.make_capture(suffix="ready", availability="confirmed")
        opportunity_capture = self.make_capture(suffix="opp", status="validated", availability="confirmed")
        opportunity = self.make_opportunity(opportunity_capture, review_at=timezone.now() - timedelta(days=1))
        response = self.dashboard()
        self.assertContains(response, "Disponible pendiente de decisión")
        self.assertContains(response, "Seguimiento vencido")
        self.assertContains(response, f'/app/captacion/{capture.pk}/')
        self.assertContains(response, f'/app/oportunidades/{opportunity.pk}/')

    def test_dashboard_is_owner_scoped_in_counts_and_content(self):
        own = self.make_capture(suffix="own", availability="confirmed")
        foreign = self.make_capture(owner=self.other, suffix="foreign", availability="confirmed")
        response = self.dashboard()
        self.assertContains(response, own.title)
        self.assertNotContains(response, foreign.title)
        self.assertEqual(response.context["daily_workflow"]["counters"]["captures_ready"], 1)

    def test_capture_daily_filter_ready_is_owner_scoped(self):
        ready = self.make_capture(suffix="ready", availability="confirmed")
        unknown = self.make_capture(suffix="unknown")
        foreign = self.make_capture(owner=self.other, suffix="foreign", availability="confirmed")
        self.client.force_login(self.user)
        response = self.client.get(reverse("capturedproperty_list") + "?daily=ready", secure=True)
        content = response.content.decode()
        self.assertIn(ready.title, content)
        self.assertNotIn(unknown.title, content)
        self.assertNotIn(foreign.title, content)

    def test_opportunity_daily_filters_are_owner_scoped(self):
        overdue_capture = self.make_capture(suffix="overdue", status="validated", availability="confirmed")
        today_capture = self.make_capture(suffix="today", status="validated", availability="confirmed")
        overdue = self.make_opportunity(overdue_capture, suffix="overdue", review_at=timezone.now() - timedelta(days=1))
        today = self.make_opportunity(today_capture, suffix="today", review_at=timezone.now() + timedelta(hours=1))
        foreign_capture = self.make_capture(owner=self.other, suffix="foreign", status="validated", availability="confirmed")
        foreign = self.make_opportunity(foreign_capture, owner=self.other, suffix="foreign", review_at=timezone.now() - timedelta(days=1))
        self.client.force_login(self.user)
        overdue_response = self.client.get(reverse("opportunity_list") + "?daily=overdue", secure=True)
        today_response = self.client.get(reverse("opportunity_list") + "?daily=today", secure=True)
        self.assertContains(overdue_response, overdue.captured_property.title)
        self.assertNotContains(overdue_response, today.captured_property.title)
        self.assertNotContains(overdue_response, foreign.captured_property.title)
        self.assertContains(today_response, today.captured_property.title)
        self.assertNotContains(today_response, overdue.captured_property.title)

    def test_task_daily_filter_is_available(self):
        overdue = self.make_task(suffix="overdue", due_at=timezone.now() - timedelta(days=1))
        today = self.make_task(suffix="today", due_at=timezone.now() + timedelta(hours=1))
        self.client.force_login(self.user)
        response = self.client.get(reverse("task_list") + "?daily=overdue", secure=True)
        self.assertContains(response, overdue.title)
        self.assertNotContains(response, today.title)

    def test_local_day_uses_europe_madrid_boundaries(self):
        utc_now = datetime(2026, 1, 1, 23, 30, tzinfo=ZoneInfo("UTC"))
        capture = self.make_capture(suffix="local", status="validated", availability="confirmed")
        local_due = datetime(2026, 1, 2, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))
        self.make_opportunity(capture, review_at=local_due)
        workflow = build_daily_workflow(self.user, utc_now)
        self.assertEqual(workflow["start_today"].date().isoformat(), "2026-01-02")
        self.assertEqual(workflow["counters"]["opportunities_today"], 1)

    def test_empty_dashboard_shows_todo_al_dia(self):
        response = self.dashboard()
        self.assertContains(response, "Todo al día")
        self.assertContains(response, "No hay acciones prioritarias pendientes para hoy.")

    def test_dashboard_get_has_no_database_writes(self):
        SystemSettings.get_solo()
        self.client.force_login(self.user)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("dashboard"), secure=True)
        self.assertEqual(response.status_code, 200)
        mutating = [
            query["sql"] for query in captured.captured_queries
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertEqual(mutating, [])

    def test_selector_query_count_is_bounded(self):
        self.make_capture(suffix="ready", availability="confirmed")
        capture = self.make_capture(suffix="opp", status="validated", availability="confirmed")
        self.make_opportunity(capture, review_at=self.now + timedelta(hours=1))
        self.make_task(suffix="today", due_at=self.now + timedelta(hours=2))
        with CaptureQueriesContext(connection) as captured:
            workflow = build_daily_workflow(self.user, self.now)
        self.assertTrue(workflow["attention"])
        self.assertLessEqual(len(captured), 12)

    def test_run223_semantics_are_preserved_conceptually(self):
        run571 = self.make_capture(suffix="571", availability="unavailable")
        run572 = self.make_capture(suffix="572", availability="confirmed")
        run573 = self.make_capture(suffix="573", status="validated", availability="confirmed")
        self.make_opportunity(run573, suffix="573")
        run574 = self.make_capture(suffix="574", availability="unavailable")
        workflow = build_daily_workflow(self.user, self.now)
        shown = {item["object"].pk: item for item in workflow["pending_captures"]}
        self.assertNotIn(run571.pk, shown)
        self.assertEqual(shown[run572.pk]["reason_label"], "Disponible pendiente de decisión")
        self.assertNotIn(run573.pk, shown)
        self.assertNotIn(run574.pk, shown)
