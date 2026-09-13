from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class CoreMigrationContracts(TransactionTestCase):
    migrate_from = [("core", "0007_userprofile")]
    migrate_to = [("core", "0008_wp05_wp06_entitlements_delivery")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("auth", "User")
        Profile = old_apps.get_model("core", "UserProfile")
        Demo = old_apps.get_model("core", "DemoRequest")
        user = User.objects.create(username="historical")
        self.start = timezone.now() - timedelta(days=1)
        self.end = timezone.now() + timedelta(days=1)
        self.profile_pk = Profile.objects.create(user=user, trial_start=self.start, trial_end=self.end, is_trial=True).pk
        self.demo_pk = Demo.objects.create(name="Historical", email="historical@example.test").pk
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_existing_profile_compatibility_defaults_and_trial_dates(self):
        profile = self.apps.get_model("core", "UserProfile").objects.get(pk=self.profile_pk)
        self.assertEqual(profile.plan, "professional")
        self.assertEqual((profile.trial_start, profile.trial_end), (self.start, self.end))
        self.assertTrue(profile.is_trial)

    def test_delivery_metadata_defaults(self):
        request = self.apps.get_model("core", "DemoRequest").objects.get(pk=self.demo_pk)
        self.assertEqual((request.delivery_status, request.notification_attempts, request.last_notification_error, request.notified_at), ("pending", 0, "", None))

    def test_schema_is_reversible_on_ephemeral_database(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        fields = {field.name for field in executor.loader.project_state(self.migrate_from).apps.get_model("core", "UserProfile")._meta.fields}
        self.assertNotIn("plan", fields)
        executor.migrate(self.migrate_to)
