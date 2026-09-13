from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.busquedas.models import SearchProfile, SearchRun
from apps.core.models import DemoRequest, UserProfile
from apps.core.notifications import retry_demo_notification
from apps.core.plans import resolve_entitlement


class EntitlementContracts(TestCase):
    def _user(self, name, **profile):
        user = get_user_model().objects.create_user(name, password="x")
        UserProfile.objects.create(user=user, **profile)
        return user

    def test_trial_active_allows_protected_operation(self):
        from apps.busquedas.forms import SearchProfileForm
        province_choices = [
            value
            for value, label in SearchProfileForm().fields["province"].choices
            if value and not isinstance(label, (list, tuple))
        ]
        self.assertTrue(province_choices)
        valid_province = province_choices[0]
        now = timezone.now()
        user = self._user("active", is_trial=True, trial_start=now - timedelta(days=1), trial_end=now + timedelta(days=1), plan="starter")
        self.assertEqual(resolve_entitlement(user).plan_code, "professional")
        for index in range(2):
            SearchProfile.objects.create(owner=user, name=f"s{index}", status=SearchProfile.Status.ACTIVE)
        self.client.force_login(user)
        self.client.post(reverse("searchprofile_create"), {
            "name": "allowed", "operation_type": "sale", "province": valid_province,
            "geography_scope": SearchProfile.GeographyScope.PROVINCE,
        })
        self.assertTrue(SearchProfile.objects.filter(owner=user, name="allowed").exists())

    def test_trial_expired_denies_protected_operation(self):
        now = timezone.now()
        user = self._user("expired", is_trial=True, trial_start=now - timedelta(days=2), trial_end=now - timedelta(days=1), plan="professional")
        for index in range(2):
            SearchProfile.objects.create(owner=user, name=f"s{index}", status=SearchProfile.Status.ACTIVE)
        self.client.force_login(user)
        self.assertRedirects(self.client.post(reverse("searchprofile_create")), reverse("searchprofile_list"))

    def test_starter_entitlement_is_enforced_server_side(self):
        user = self._user("starter", is_trial=False, plan="starter")
        for index in range(2):
            SearchProfile.objects.create(owner=user, name=f"s{index}", status=SearchProfile.Status.ACTIVE)
        self.client.force_login(user)
        self.client.post(reverse("searchprofile_create"), {"name": "blocked"})
        self.assertFalse(SearchProfile.objects.filter(owner=user, name="blocked").exists())

    def test_professional_entitlement_is_enforced_server_side(self):
        user = self._user("professional", is_trial=False, plan="professional")
        for index in range(6):
            SearchProfile.objects.create(owner=user, name=f"s{index}", status=SearchProfile.Status.ACTIVE)
        self.client.force_login(user)
        self.client.post(reverse("searchprofile_create"), {"name": "blocked"})
        self.assertFalse(SearchProfile.objects.filter(owner=user, name="blocked").exists())

    def test_template_visibility_does_not_replace_server_enforcement(self):
        user = self._user("quota", is_trial=False, plan="starter")
        search = SearchProfile.objects.create(owner=user, name="quota", status=SearchProfile.Status.ACTIVE)
        for _ in range(5):
            SearchRun.objects.create(search_profile=search, execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY)
        self.client.force_login(user)
        self.client.post(reverse("searchprofile_execute", args=[search.pk]))
        self.assertEqual(search.runs.count(), 5)


class DemoNotificationContracts(TestCase):
    def _post_data(self):
        return {"name": "Persona", "email": "person@example.test", "profile_type": "investor", "phone": "", "message": ""}

    @patch("apps.core.notifications.send_mail", return_value=1)
    def test_demo_request_persists_before_notification(self, send_mail):
        send_mail.side_effect = lambda **kwargs: 1 if DemoRequest.objects.exists() else self.fail("not persisted")
        self.client.post(reverse("demo_request"), self._post_data())
        self.assertEqual(DemoRequest.objects.count(), 1)

    @patch("apps.core.notifications.send_mail", side_effect=OSError("sensitive endpoint"))
    def test_demo_notification_failure_does_not_lose_request(self, _send_mail):
        self.client.post(reverse("demo_request"), self._post_data())
        request = DemoRequest.objects.get()
        self.assertEqual(request.delivery_status, DemoRequest.DeliveryStatus.FAILED)
        self.assertNotIn("sensitive", request.last_notification_error)

    def test_demo_notification_retry_updates_delivery_metadata(self):
        request = DemoRequest.objects.create(**self._post_data())
        with patch("apps.core.notifications.send_mail", side_effect=OSError("down")):
            retry_demo_notification(request.pk)
        with patch("apps.core.notifications.send_mail", return_value=1):
            retry_demo_notification(request.pk)
            retry_demo_notification(request.pk)
        request.refresh_from_db()
        self.assertEqual(request.notification_attempts, 2)
        self.assertEqual(request.delivery_status, DemoRequest.DeliveryStatus.SENT)
        self.assertIsNotNone(request.notified_at)


class PublicCopyContracts(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.copy = (Path(__file__).parents[1] / "templates/core/home.html").read_text()

    def test_public_copy_contains_no_unverified_prices(self):
        self.assertNotRegex(self.copy, r">(?:29|79)<")

    def test_public_copy_contains_no_unverified_ai_credit_values(self):
        self.assertNotRegex(self.copy, r"(?:50|300) créditos IA")
