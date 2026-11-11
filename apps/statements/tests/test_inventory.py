import os
import runpy
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.db import connection
from unittest import skipUnless

from django.test import SimpleTestCase, TestCase, override_settings

from apps.statements.managers import Immutable, KeptQuerySet
from apps.statements.tests.base import make_item, make_statement

SWITCHES = ("BILLING_STATEMENTS_ENABLED", "BILLING_INVOICE_REGISTER_ENABLED", "BILLING_LIVE_DOCUMENTS")
APPS = ("statements", "invoices")


class InventoryTests(SimpleTestCase):
    def test_every_model_has_a_guard(self):
        """A new model in these apps must say how it is protected, or this fails."""
        for label in APPS:
            for model in apps.get_app_config(label).get_models():
                manager = model._default_manager
                self.assertIsInstance(manager.all(), KeptQuerySet, model.__name__)
                self.assertTrue(
                    model.delete is not __import__("django").db.models.Model.delete,
                    f"{model.__name__} must override delete()",
                )

    def test_apps_installed(self):
        for label in APPS:
            self.assertTrue(apps.is_installed(f"apps.{label}"))


class SwitchTests(SimpleTestCase):
    def load(self, **env):
        clean = {k: v for k, v in os.environ.items() if k not in SWITCHES}
        clean.update(env)
        with mock.patch.dict("os.environ", clean, clear=True):
            return runpy.run_path("config/settings.py")

    def test_off_by_default_on_only_for_one(self):
        loaded = self.load()
        for name in SWITCHES:
            self.assertIs(loaded[name], False, name)
            self.assertIs(self.load(**{name: "1"})[name], True)
            self.assertIs(self.load(**{name: "true"})[name], False)


on_postgres = skipUnless(connection.vendor == "postgresql", "PostgreSQL triggers")


@on_postgres
@override_settings(BILLING_ENABLED=True)
class TriggerTests(TestCase):
    def test_raw_update_and_delete_refused_on_insert_only_tables(self):
        from django.db import IntegrityError, transaction
        statement = make_statement()
        for sql in (
            "UPDATE statements_statement SET total_centavos = 1 WHERE id = %s",
            "DELETE FROM statements_statement WHERE id = %s",
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql, [statement.pk])
