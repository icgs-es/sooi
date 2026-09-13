from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ImapSecretMigrationTests(TransactionTestCase):
    reset_sequences = True

    migrate_from = [("inbox", "0001_initial")]
    migrate_to = [("inbox", "0003_remove_plaintext_imap_password")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("auth", "User")
        EmailAccount = old_apps.get_model("inbox", "EmailAccount")
        owner = User.objects.create(username="migration-user")
        self.account_pk = EmailAccount.objects.create(
            owner=owner,
            name="Historical",
            email_address="historical@example.test",
            imap_password="legacy-plaintext",
        ).pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_plaintext_is_removed_and_deterministic_reference_remains(self):
        EmailAccount = self.apps.get_model("inbox", "EmailAccount")
        account = EmailAccount.objects.get(pk=self.account_pk)
        self.assertEqual(account.imap_secret_ref, f"imap-account-{self.account_pk}")
        self.assertNotIn("imap_password", {field.name for field in EmailAccount._meta.fields})
