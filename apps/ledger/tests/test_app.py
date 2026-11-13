import importlib
import pathlib
from unittest import skipUnless

from django.apps import apps
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.billing import services
from apps.billing.tests.base import T0, make_subscription

TABLES = {
    "ledger_access", "ledger_account", "ledger_entity", "ledger_entry",
    "ledger_event", "ledger_line", "ledger_period", "ledger_recognition",
}
APP_DIR = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOT = APP_DIR.parent


class AppTests(TestCase):
    def test_1_the_app_is_installed_and_labelled_ledger(self):
        config = apps.get_app_config("ledger")
        self.assertEqual(config.name, "apps.ledger")
        self.assertEqual(len(list(config.get_models())), 8)

    def test_2_the_django_admin_is_not_installed(self):
        self.assertFalse(apps.is_installed("django.contrib.admin"))

    def test_3_the_database_has_exactly_the_eight_tables(self):
        names = {n for n in connection.introspection.table_names() if n.startswith("ledger_")}
        self.assertEqual(names, TABLES)

    def test_4_the_tables_are_empty_after_migrate(self):
        for model in apps.get_app_config("ledger").get_models():
            self.assertEqual(model._base_manager.count(), 0, model.__name__)

    def test_5_migration_0001_holds_only_schema_operations(self):
        migration = importlib.import_module("apps.ledger.migrations.0001_initial").Migration
        allowed = {"CreateModel", "AddConstraint", "AddIndex"}
        self.assertEqual({type(op).__name__ for op in migration.operations} - allowed, set())

    def test_6_migration_0002_depends_only_on_0001(self):
        migration = importlib.import_module("apps.ledger.migrations.0002_postgres_guards").Migration
        self.assertEqual(migration.dependencies, [("ledger", "0001_initial")])

    def test_7_migration_0002_uses_no_app_import(self):
        source = (APP_DIR / "migrations" / "0002_postgres_guards.py").read_text()
        self.assertNotIn("from apps", source)
        self.assertNotIn("import apps", source)

    def test_8_no_other_app_imports_ledger(self):
        # Holds for S2-0 only. S2-B (billing hook) and S2-C (staff_views) must import the ledger
        # and each narrows this test to its named import sites in its own PR.
        offenders = []
        for path in SOURCE_ROOT.rglob("*.py"):
            if APP_DIR in path.parents:
                continue
            text = path.read_text()
            if "apps.ledger" in text or "from apps import ledger" in text:
                offenders.append(str(path.relative_to(SOURCE_ROOT)))
        self.assertEqual(offenders, [])

    def test_9_the_schema_names_no_tax_or_receiving_account(self):
        for model in apps.get_app_config("ledger").get_models():
            for field in model._meta.get_fields():
                for word in ("tax", "suspense", "receiving"):
                    self.assertNotIn(word, field.name.lower(), f"{model.__name__}.{field.name}")

    def test_10_with_every_switch_off_nothing_outside_the_app_reaches_a_ledger_table(self):
        seen = []

        def record(execute, sql, params, many, context):
            seen.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(record):
            response = self.client.get(reverse("healthz"), secure=True)
            self.assertEqual(response.status_code, 200)
            with override_settings(BILLING_ENABLED=True):  # billing on, the ledger switches off
                subscription = make_subscription()
                services.overview(subscription.site, at=T0)
            self.assertTrue(seen)
        self.assertEqual([s for s in seen if "ledger_" in s], [])


@skipUnless(connection.vendor == "sqlite", "SQLite only")
class SqliteNoopTests(TestCase):
    def test_sqlite_noop(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'ledger_%'")
            self.assertEqual(cursor.fetchall(), [])
