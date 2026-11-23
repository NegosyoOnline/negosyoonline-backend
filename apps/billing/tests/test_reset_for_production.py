"""`billing_reset_for_production`: empties the billing, ledger, invoice and statement rows of the
restored production copy, and nothing else. Refuses outside production. Dry run by default."""
from io import StringIO
from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings

from apps.accounts.models import Account
from apps.billing.management.commands import billing_reset_for_production as command
from apps.billing.models import BankTransferEvent, ReceivingAccount, Subscription
from apps.billing.tests.base import make_receiving_account
from apps.invoices.models import InvoiceBook, ManualInvoiceItem
from apps.operators.models import OperatorAuditEntry
from apps.sites.models import Site
from apps.statements.models import (
    BillingProfile,
    IssuerProfile,
    Statement,
    StatementLine,
    StatementNumberSeries,
)
from apps.statements.tests.base import make_book, make_invoice, make_item, make_line, make_statement

ACTION = "billing_reset_for_production"
User = get_user_model()


def run(*args, **kwargs):
    out = StringIO()
    call_command("billing_reset_for_production", *args, stdout=out, **kwargs)
    return out.getvalue()


def counts():
    return {m._meta.label: m._base_manager.count() for m in command.reset_models()}


def audit_rows():
    return OperatorAuditEntry.objects.filter(action=ACTION)


class ResetBase(TestCase):
    def setUp(self):
        self.operator = User.objects.create(username="coach", is_staff=True)

    def populate(self):
        """One or more rows in each kind of table the command empties, and the kept ones."""
        item = make_item()  # a paid subscription, its cycle and transitions, and a register item
        subscription = item.subscription
        make_receiving_account()
        statement = make_statement(subscription=subscription)
        make_line(statement)
        StatementNumberSeries.objects.create(prefix="NOBS", year=2026, last_value=7)
        make_invoice(item=make_item(subscription=subscription), book=make_book())
        # The ledger app imports nothing from here and nothing imports it (its own test says so),
        # so its rows are made through the app registry. The posted entry is in the ledger's test.
        entity = django_apps.get_model("ledger", "AccountingEntity").objects.create(
            name="Test book", kind="rehearsal", created_by=self.operator
        )
        django_apps.get_model("ledger", "LedgerEvent").objects.create(
            entity=entity, kind="test", target_type="t", target_ref="r"
        )
        if connection.vendor == "postgresql":
            # The ledger's balance trigger is deferred to commit, and a test never commits: fire it
            # now, as the restored copy's committed rows would have, so TRUNCATE can take the table.
            connection.cursor().execute("SET CONSTRAINTS ALL IMMEDIATE")
        BillingProfile.objects.create(
            site=subscription.site, company_name="Test Co", billing_email="co@example.test"
        )


@override_settings(APP_ENVIRONMENT="production", BILLING_ENABLED=True)
class ApplyTests(ResetBase):
    def test_apply_empties_exactly_the_listed_models(self):
        self.populate()
        ledger_entity = django_apps.get_model("ledger", "AccountingEntity")
        ledger_event = django_apps.get_model("ledger", "LedgerEvent")
        for model in (Subscription, ReceivingAccount, Statement, StatementLine, StatementNumberSeries,
                      InvoiceBook, ManualInvoiceItem, ledger_entity, ledger_event):
            self.assertGreater(model._base_manager.count(), 0, model)
        kept = {
            "sites": Site.objects.count(), "accounts": Account.objects.count(),
            "users": User.objects.count(), "audit": OperatorAuditEntry.objects.count(),
            "issuer": IssuerProfile.objects.count(), "profile": BillingProfile.objects.count(),
        }
        before = counts()

        out = run("--apply", "--operator", "coach")

        self.assertEqual(sum(counts().values()), 0)
        self.assertEqual(Site.objects.count(), kept["sites"])
        self.assertEqual(Account.objects.count(), kept["accounts"])
        self.assertEqual(User.objects.count(), kept["users"])
        self.assertEqual(IssuerProfile.objects.count(), kept["issuer"])
        self.assertEqual(BillingProfile.objects.count(), kept["profile"])
        self.assertEqual(OperatorAuditEntry.objects.count(), kept["audit"] + 1)
        entry = audit_rows().get()
        self.assertEqual(entry.operator, self.operator)
        self.assertEqual(entry.detail["deleted"], before)
        self.assertEqual(entry.personal, {})
        self.assertIn("Every listed table is empty", out)
        self.assertNotIn("example.test", out)  # counts and model names only

    def test_a_second_apply_finds_nothing_and_still_succeeds(self):
        self.populate()
        run("--apply", "--operator", "coach")
        out = run("--apply", "--operator", "coach")
        self.assertIn("total: 0", out)
        self.assertEqual(audit_rows().count(), 2)

    def test_rolls_back_and_fails_if_any_count_stays_above_zero(self):
        self.populate()
        before = counts()
        audits = OperatorAuditEntry.objects.count()

        # A delete that leaves every row behind: the re-count must catch it.
        with mock.patch.object(command, "empty_tables"):
            with self.assertRaises(CommandError) as caught:
                run("--apply", "--operator", "coach")
        self.assertIn("billing.Subscription", str(caught.exception))
        self.assertEqual(counts(), before)  # the part that was deleted rolled back with it
        self.assertEqual(OperatorAuditEntry.objects.count(), audits)
        self.assertFalse(audit_rows().exists())

    def test_a_database_error_rolls_back_and_fails_cleanly(self):
        self.populate()
        before = counts()
        with mock.patch.object(command, "empty_tables", side_effect=RuntimeError("secret detail")):
            with self.assertRaises(CommandError) as caught:
                run("--apply", "--operator", "coach")
        self.assertNotIn("secret detail", str(caught.exception))
        self.assertEqual(counts(), before)
        self.assertFalse(audit_rows().exists())


@override_settings(APP_ENVIRONMENT="production", BILLING_ENABLED=True)
class DryRunTests(ResetBase):
    def test_dry_run_is_the_default_and_changes_nothing(self):
        self.populate()
        before, audits = counts(), OperatorAuditEntry.objects.count()
        out = run()
        self.assertEqual(counts(), before)
        self.assertEqual(OperatorAuditEntry.objects.count(), audits)
        self.assertIn("Dry run", out)
        for label, n in before.items():
            self.assertIn(f"{label}: {n}", out)
        self.assertNotIn("example.test", out)

    def test_dry_run_needs_no_operator(self):
        self.assertIn("total: 0", run())


@override_settings(APP_ENVIRONMENT="production", BILLING_ENABLED=True)
class OperatorTests(ResetBase):
    def refused(self, *args):
        self.populate()
        before = counts()
        with self.assertRaises(CommandError):
            run("--apply", *args)
        self.assertEqual(counts(), before)
        self.assertFalse(audit_rows().exists())

    def test_apply_needs_an_operator(self):
        self.refused()

    def test_an_unknown_operator_is_refused(self):
        self.refused("--operator", "nobody")

    def test_a_user_who_is_not_staff_is_refused(self):
        User.objects.create(username="plain", is_staff=False)
        self.refused("--operator", "plain")

    def test_an_inactive_staff_user_is_refused(self):
        User.objects.create(username="gone", is_staff=True, is_active=False)
        self.refused("--operator", "gone")

    def test_an_unknown_database_alias_is_refused(self):
        with self.assertRaises(CommandError):
            run("--database", "no_such_alias")


@override_settings(BILLING_ENABLED=True)
class EnvironmentTests(ResetBase):
    def test_refuses_anywhere_but_production_and_touches_nothing(self):
        self.populate()
        before = counts()
        for value in ("staging", "", "Production ", "prod", "local"):
            with self.subTest(app_environment=value), override_settings(APP_ENVIRONMENT=value):
                for args in ((), ("--apply", "--operator", "coach")):
                    with self.assertRaises(CommandError) as caught:
                        run(*args)
                    self.assertIn("APP_ENVIRONMENT", str(caught.exception))
        self.assertEqual(counts(), before)
        self.assertFalse(audit_rows().exists())


class ListTests(TestCase):
    def test_the_list_and_the_kept_models_are_every_model_of_the_four_apps(self):
        every = {
            m._meta.label for app in ("billing", "ledger", "invoices", "statements")
            for m in django_apps.get_app_config(app).get_models()
        }
        listed = set(command.RESET_MODELS)
        self.assertEqual(len(listed), len(command.RESET_MODELS), "a model is listed twice")
        self.assertFalse(listed & set(command.KEPT_MODELS))
        # A new model in these apps fails here until someone decides whether the reset empties it.
        self.assertEqual(listed | set(command.KEPT_MODELS), every)

    def test_children_come_before_the_rows_they_point_to(self):
        position = {label: i for i, label in enumerate(command.RESET_MODELS)}
        for model in command.reset_models():
            for field in model._meta.get_fields():
                if not (field.is_relation and field.concrete and field.related_model):
                    continue
                target = field.related_model._meta.label
                if target in position and target != model._meta.label:
                    self.assertLess(position[model._meta.label], position[target], f"{model} -> {target}")

    def test_the_bank_transfer_event_table_is_in_the_list(self):
        self.assertIn(BankTransferEvent._meta.label, command.RESET_MODELS)
