from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import call, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from .daily_digest_scheduler import PUBLIC_BASE_URL, dispatch_due_daily_digests
from .models import DailyDigestDelivery, NotificationPreference


User = get_user_model()
MADRID = ZoneInfo("Europe/Madrid")


def delivery_result(status):
    return SimpleNamespace(status=status)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DailyDigestSchedulerSR022B3CTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 23, 14, 0, tzinfo=MADRID)
        self.user = self.make_user("owner")

    def make_user(self, suffix):
        return User.objects.create_user(
            username=f"scheduler-{suffix}",
            email=f"{suffix}@example.test",
            password="secret",
        )

    def preference(self, user=None, *, enabled=True, scheduled=time(14, 0)):
        return NotificationPreference.objects.create(
            owner=user or self.user,
            daily_digest_enabled=enabled,
            daily_digest_time=scheduled,
        )

    def ledger(self, user=None, *, status="sent", local_date=None):
        return DailyDigestDelivery.objects.create(
            owner=user or self.user,
            delivery_type=DailyDigestDelivery.DeliveryType.DAILY_DIGEST,
            channel=DailyDigestDelivery.Channel.EMAIL,
            local_date=local_date or self.now.date(),
            status=status,
        )

    @patch("apps.core.daily_digest_scheduler.deliver_daily_digest")
    def test_before_exact_and_after_schedule_catch_up(self, delivery):
        self.preference()
        before = dispatch_due_daily_digests(
            now=datetime(2026, 8, 23, 13, 59, tzinfo=MADRID)
        )
        self.assertEqual(before.processed_count, 0)
        delivery.assert_not_called()

        delivery.return_value = delivery_result("sent")
        exact = dispatch_due_daily_digests(now=self.now)
        after = dispatch_due_daily_digests(
            now=datetime(2026, 8, 23, 14, 37, tzinfo=MADRID)
        )
        self.assertEqual((exact.processed_count, after.processed_count), (1, 1))
        self.assertEqual(delivery.call_count, 2)

    @patch("apps.core.daily_digest_scheduler.deliver_daily_digest")
    def test_disabled_and_no_due_fast_path(self, delivery):
        self.preference(enabled=False)
        summary = dispatch_due_daily_digests(now=self.now)
        self.assertEqual((summary.eligible_count, summary.processed_count), (0, 0))
        delivery.assert_not_called()

    @patch("apps.core.daily_digest_scheduler.deliver_daily_digest")
    def test_every_existing_status_excludes_today(self, delivery):
        self.preference()
        for status in DailyDigestDelivery.Status.values:
            with self.subTest(status=status):
                row = self.ledger(status=status)
                summary = dispatch_due_daily_digests(now=self.now)
                self.assertEqual(summary.processed_count, 0)
                row.delete()
        delivery.assert_not_called()

    @patch("apps.core.daily_digest_scheduler.deliver_daily_digest", return_value=delivery_result("sent"))
    def test_yesterday_does_not_block_today(self, delivery):
        self.preference()
        self.ledger(local_date=self.now.date() - timedelta(days=1))
        summary = dispatch_due_daily_digests(now=self.now)
        self.assertEqual(summary.sent_count, 1)
        delivery.assert_called_once_with(self.user, now=self.now, base_url=PUBLIC_BASE_URL)

    @patch("apps.core.daily_digest_scheduler.deliver_daily_digest", return_value=delivery_result("sent"))
    def test_madrid_date_boundary_differs_from_utc(self, delivery):
        self.preference(scheduled=time(0, 15))
        utc_instant = datetime(2026, 8, 22, 22, 30, tzinfo=ZoneInfo("UTC"))
        summary = dispatch_due_daily_digests(now=utc_instant)
        self.assertEqual(summary.local_date, date(2026, 8, 23))
        self.assertEqual(summary.local_time, time(0, 30))
        delivery.assert_called_once()

    @patch("apps.core.daily_digest_scheduler.deliver_daily_digest", return_value=delivery_result("sent"))
    def test_dst_uses_zoneinfo_offsets(self, delivery):
        self.preference(scheduled=time(3, 0))
        winter = dispatch_due_daily_digests(
            now=datetime(2026, 1, 15, 2, 0, tzinfo=ZoneInfo("UTC"))
        )
        summer = dispatch_due_daily_digests(
            now=datetime(2026, 7, 15, 1, 0, tzinfo=ZoneInfo("UTC"))
        )
        self.assertEqual(winter.local_time, time(3, 0))
        self.assertEqual(summer.local_time, time(3, 0))
        self.assertEqual(delivery.call_count, 2)

    @patch("apps.core.daily_digest_scheduler.deliver_daily_digest", return_value=delivery_result("sent"))
    def test_multiple_users_are_selected_deterministically(self, delivery):
        users = {letter: self.make_user(letter) for letter in "ABCDE"}
        self.preference(users["A"])
        self.preference(users["B"], scheduled=time(14, 1))
        self.preference(users["C"])
        self.preference(users["D"], enabled=False)
        self.preference(users["E"])
        self.ledger(users["C"])

        summary = dispatch_due_daily_digests(now=self.now)
        self.assertEqual(summary.processed_count, 2)
        self.assertEqual(
            delivery.call_args_list,
            [
                call(users["A"], now=self.now, base_url=PUBLIC_BASE_URL),
                call(users["E"], now=self.now, base_url=PUBLIC_BASE_URL),
            ],
        )

    @patch("apps.core.daily_digest_scheduler.deliver_daily_digest")
    def test_per_user_failure_does_not_stop_batch(self, delivery):
        second = self.make_user("second")
        self.preference()
        self.preference(second)
        delivery.side_effect = [RuntimeError("sensitive"), delivery_result("sent")]
        with self.assertLogs("apps.core.daily_digest_scheduler", level="ERROR") as logs:
            summary = dispatch_due_daily_digests(now=self.now)
        self.assertEqual(summary.processed_count, 2)
        self.assertEqual((summary.error_count, summary.sent_count), (1, 1))
        self.assertEqual(delivery.call_count, 2)
        self.assertNotIn("sensitive", " ".join(logs.output))

    @patch("apps.core.daily_digest_scheduler.deliver_daily_digest", return_value=delivery_result("sent"))
    def test_batch_limit(self, delivery):
        for index in range(5):
            self.preference(self.make_user(f"batch-{index}"))
        summary = dispatch_due_daily_digests(now=self.now, batch_size=2)
        self.assertEqual((summary.eligible_count, summary.processed_count), (2, 2))
        self.assertEqual(delivery.call_count, 2)

    @patch("apps.core.daily_digest_delivery.send_transactional_email", return_value=1)
    @patch("apps.core.daily_digest_delivery.render_daily_digest")
    def test_repeated_dispatch_uses_real_governed_ledger(self, render, transport):
        self.preference()
        render.return_value = {
            "subject": "Approved subject",
            "text_body": "Plain body",
            "html_body": "<p>HTML body</p>",
            "digest": {"total_attention": 1},
        }
        first = dispatch_due_daily_digests(now=self.now)
        second = dispatch_due_daily_digests(now=self.now)
        self.assertEqual((first.sent_count, second.processed_count), (1, 0))
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(DailyDigestDelivery.objects.get().status, "sent")

    @patch("apps.core.daily_digest_scheduler.deliver_daily_digest")
    def test_real_like_today_sent_never_reaches_delivery(self, delivery):
        self.preference()
        self.ledger(status="sent")
        summary = dispatch_due_daily_digests(now=self.now)
        self.assertEqual(summary.processed_count, 0)
        delivery.assert_not_called()

    @patch("apps.core.tasks.dispatch_due_daily_digests")
    def test_task_wrapper_calls_scheduler_once_without_retry(self, scheduler):
        from .tasks import dispatch_due_daily_digests_task

        expected = SimpleNamespace(processed_count=0)
        scheduler.return_value = expected
        self.assertIs(dispatch_due_daily_digests_task(), expected)
        scheduler.assert_called_once_with()
        self.assertFalse(hasattr(dispatch_due_daily_digests_task, "autoretry_for"))

    def test_beat_contract_has_one_sixty_second_entry(self):
        from config.celery import app

        entries = [
            entry for entry in app.conf.beat_schedule.values()
            if entry.get("task") == "core.dispatch_due_daily_digests"
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["schedule"], 60.0)
        self.assertLessEqual(entries[0]["options"]["expires"], 60.0)
