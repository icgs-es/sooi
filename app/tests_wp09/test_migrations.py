from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DailyProductMetricMigrationTests(TransactionTestCase):
    reset_sequences = True

    def test_forward_and_backward_from_exact_dependency(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("core", "0008_wp05_wp06_entitlements_delivery")])
        old_apps = executor.loader.project_state([("core", "0008_wp05_wp06_entitlements_delivery")]).apps
        self.assertNotIn("dailyproductmetric", old_apps.get_app_config("core").models)

        executor = MigrationExecutor(connection)
        executor.migrate([("core", "0009_dailyproductmetric")])
        new_apps = executor.loader.project_state([("core", "0009_dailyproductmetric")]).apps
        self.assertIn("dailyproductmetric", new_apps.get_app_config("core").models)

        executor = MigrationExecutor(connection)
        executor.migrate([("core", "0008_wp05_wp06_entitlements_delivery")])
        back_apps = executor.loader.project_state([("core", "0008_wp05_wp06_entitlements_delivery")]).apps
        self.assertNotIn("dailyproductmetric", back_apps.get_app_config("core").models)

        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_migration_dependency_is_exact(self):
        executor = MigrationExecutor(connection)
        migration = executor.loader.get_migration("core", "0009_dailyproductmetric")
        self.assertEqual(migration.dependencies, [("core", "0008_wp05_wp06_entitlements_delivery")])
        self.assertEqual(len(migration.operations), 1)
