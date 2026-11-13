"""The receiving account routes under /staff/v1/finance/accounts/ (HQ Finance, Accounts): staff
set where Subscribers are told to pay. Every value here is made up."""
import ast
import json
from pathlib import Path
from unittest import mock, skipUnless

from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.billing import plan, staff
from apps.billing.models import ReceivingAccount
from apps.billing.tests.base import make_operator, make_receiving_account, transfers_on
from apps.billing.tests.test_staff_finance import FinanceTestCase
from apps.billing.tests.test_transfer_api import TransferApiCase
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.base import HQ, HqAdminMixin
from apps.operators.tests.test_staff_routes import STAFF
from apps.sites.tests.test_concurrency import run_together

BASE = "/staff/v1/finance/accounts/"
PATHS = ("list/", "add/", "edit/", "active/")
KEYS = sorted([
    "id", "kind", "provider_name", "account_name", "account_number", "active", "sort_order",
    "created_at", "updated_at", "updated_by",
])
NEW = {
    "kind": "ewallet", "provider_name": "Test Wallet", "account_name": "Test Owner",
    "account_number": "0917 000 0000",
}
ACTIONS = (
    "billing_receiving_account_added", "billing_receiving_account_edited",
    "billing_receiving_account_activated", "billing_receiving_account_deactivated",
)


class AccountsTestCase(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()

    def accounts(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def entries(self, *actions):
        return list(OperatorAuditEntry.objects.filter(action__in=actions or ACTIONS).order_by("pk"))

    def add(self, **fields):
        response = self.accounts("add/", {**NEW, **fields})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()


class GateTests(AccountsTestCase):
    def test_the_routes_are_registered_post_only_under_finance(self):
        for path in PATHS:
            self.assertIn(f"finance/accounts/{path}", staff.ROUTES)
            self.assertEqual(staff_views.ROUTES[f"finance/accounts/{path}"][0], "POST")
        self.assertEqual(
            sorted(p for p in staff.ROUTES if p.startswith("finance/accounts/")),
            sorted(f"finance/accounts/{p}" for p in PATHS),
        )

    def test_get_is_refused_and_options_answers(self):
        for path in PATHS:
            self.assertError(self.call("GET", BASE + path), 405, "method_not_allowed")
            self.assertEqual(self.call("OPTIONS", BASE + path, "POST").status_code, 204)

    def test_another_origin_no_session_and_one_step_are_refused(self):
        for path in PATHS:
            for origin in ("https://evil.example", None):
                self.assertError(self.accounts(path, origin=origin), 403, "origin_not_allowed")
        self.client.cookies.clear()
        for path in PATHS:
            self.assertError(self.accounts(path), 401, "unauthenticated")
        self.staff_email_step()
        for path in PATHS:
            self.assertError(self.accounts(path), 401, "second_step_required")
        self.assertEqual(ReceivingAccount.objects.filter(provider_name="Test Wallet").count(), 0)
        self.assertEqual(self.entries(), [])

    def test_open_while_collection_is_not_live(self):
        # The chicken-and-egg case: no active account yet, so the Finance payment routes are
        # closed, but the first account can still be entered.
        ReceivingAccount.objects.all().delete()
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=False):
            self.assertFalse(plan.collection_live())
            self.assertError(self.call("POST", "/staff/v1/finance/payments/list/", {"status": "submitted"}), 404, "not_found")
            self.assertEqual(self.accounts("list/").status_code, 200)
            self.add()
        self.assertEqual(ReceivingAccount.objects.count(), 1)

    @override_settings(BILLING_ENABLED=False)
    def test_billing_off_is_not_found_and_reads_no_table(self):
        with CaptureQueriesContext(connection) as queries:
            for path in PATHS:
                self.assertError(self.accounts(path, {**NEW, "account": "x"}), 404, "not_found")
        self.assertEqual([q for q in queries if "receivingaccount" in q["sql"].lower()], [])
        self.assertEqual(ReceivingAccount.objects.filter(provider_name="Test Wallet").count(), 0)


class ListTests(AccountsTestCase):
    def test_lists_every_row_by_order_with_the_full_number_and_the_status(self):
        make_receiving_account([
            {"kind": "bank", "provider_name": "Late", "account_name": "n", "account_number": "111111"},
        ])
        ReceivingAccount.objects.filter(provider_name="Late").update(sort_order=5, active=False)
        body = self.accounts("list/").json()
        self.assertEqual(sorted(body), ["bank_transfer", "items"])
        self.assertEqual([i["provider_name"] for i in body["items"]], ["Test Bank", "Late"])
        self.assertEqual(sorted(body["items"][0]), KEYS)
        self.assertEqual(body["items"][0]["account_number"], "0000 0000 00")
        self.assertEqual(
            body["bank_transfer"],
            {"switch_on": True, "store_available": True, "active_accounts": 1, "live": True},
        )

    def test_the_status_says_why_it_is_not_live(self):
        ReceivingAccount.objects.update(active=False)
        self.assertEqual(self.accounts("list/").json()["bank_transfer"]["live"], False)
        self.assertEqual(self.accounts("list/").json()["bank_transfer"]["active_accounts"], 0)
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=False):
            status = self.accounts("list/").json()["bank_transfer"]
            self.assertEqual((status["switch_on"], status["live"]), (False, False))
        with mock.patch("apps.billing.proofs.available", return_value=False):
            status = self.accounts("list/").json()["bank_transfer"]
            self.assertEqual((status["store_available"], status["live"]), (False, False))


class AddTests(AccountsTestCase):
    def test_add_saves_the_row_and_one_audit_entry_with_the_after_values(self):
        body = self.add(sort_order=3)
        self.assertEqual(sorted(body), KEYS)
        self.assertEqual((body["active"], body["sort_order"]), (True, 3))
        row = ReceivingAccount.objects.get(public_id=body["id"])
        self.assertTrue(row.public_id.startswith("rca_"))
        self.assertEqual((row.created_by, row.updated_by), (self.operator, self.operator))
        (entry,) = self.entries()
        self.assertEqual(
            (entry.action, entry.target_type, entry.target_id, entry.operator),
            ("billing_receiving_account_added", "receiving_account", row.public_id, self.operator),
        )
        self.assertEqual(entry.detail, {"after": {
            **NEW, "sort_order": 3, "active": True,
        }})

    def test_add_inactive_and_text_is_stripped(self):
        body = self.add(active=False, provider_name="  Test Wallet  ")
        self.assertEqual((body["active"], body["provider_name"]), (False, "Test Wallet"))

    def test_every_field_is_checked(self):
        cases = [
            ("kind", "card"), ("kind", None), ("provider_name", ""), ("provider_name", "x" * 61),
            ("provider_name", 5), ("account_name", "  "), ("account_name", "x" * 121),
            ("account_number", "123"), ("account_number", "12345a"), ("account_number", "1" * 41),
            ("account_number", None), ("sort_order", -1), ("sort_order", 1000),
            ("sort_order", "1"), ("sort_order", True), ("active", "yes"), ("active", 1),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                response = self.accounts("add/", {**NEW, field: value})
                self.assertError(response, 400, "invalid_request")
                self.assertIn(field, response.json()["error"]["fields"])
        for field in ("kind", "provider_name", "account_name", "account_number"):
            body = {k: v for k, v in NEW.items() if k != field}
            self.assertIn(field, self.accounts("add/", body).json()["error"]["fields"])
        self.assertEqual(ReceivingAccount.objects.count(), 1)
        self.assertEqual(self.entries(), [])

    def test_unknown_keys_are_ignored(self):
        body = self.add(created_by=999, public_id="evil", extra="x")
        self.assertTrue(body["id"].startswith("rca_"))

    def test_a_duplicate_provider_and_number_is_refused_even_when_inactive(self):
        self.add()
        ReceivingAccount.objects.filter(provider_name="Test Wallet").update(active=False)
        self.assertError(self.accounts("add/", NEW), 409, "account_exists")
        self.assertEqual(ReceivingAccount.objects.count(), 2)
        self.assertEqual(len(self.entries()), 1)

    def test_a_body_over_the_limit_is_an_empty_body(self):
        response = self.accounts("add/", {**NEW, "account_name": "x" * 5000})
        self.assertError(response, 400, "invalid_request")


class EditTests(AccountsTestCase):
    def setUp(self):
        super().setUp()
        self.row = ReceivingAccount.objects.get()

    def edit(self, **fields):
        return self.accounts("edit/", {"account": self.row.public_id, **fields})

    def test_edit_changes_only_what_was_sent_and_audits_before_and_after(self):
        before = {
            "kind": "bank", "provider_name": "Test Bank", "account_name": "Test Receiver",
            "account_number": "0000 0000 00", "sort_order": 0, "active": True,
        }
        response = self.edit(account_number="1111 2222 33", sort_order=2)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["account_number"], "1111 2222 33")
        (entry,) = self.entries()
        self.assertEqual(entry.action, "billing_receiving_account_edited")
        self.assertEqual(entry.target_id, self.row.public_id)
        self.assertEqual(entry.detail["before"], before)
        self.assertEqual(entry.detail["after"], {**before, "account_number": "1111 2222 33", "sort_order": 2})
        self.row.refresh_from_db()
        self.assertEqual((self.row.account_number, self.row.updated_by), ("1111 2222 33", self.operator))

    def test_an_edit_that_changes_nothing_writes_no_audit_entry_and_returns_the_row(self):
        response = self.edit(provider_name="Test Bank", account_number="0000 0000 00")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], self.row.public_id)
        self.assertEqual(self.entries(), [])

    def test_fields_are_checked_and_the_account_must_exist(self):
        for field, value in (("kind", "x"), ("account_number", "12"), ("provider_name", ""), ("sort_order", 5000)):
            self.assertIn(field, self.edit(**{field: value}).json()["error"]["fields"])
        self.assertError(self.accounts("edit/", {}), 400, "invalid_request")
        self.assertError(self.accounts("edit/", {"account": "rca_nope", "kind": "bank"}), 404, "not_found")
        self.assertError(self.accounts("edit/", {"account": 5}), 400, "invalid_request")
        self.assertEqual(self.entries(), [])

    def test_editing_into_another_rows_pair_is_a_duplicate(self):
        self.add()
        self.assertError(
            self.edit(provider_name="Test Wallet", account_number="0917 000 0000"),
            409, "account_exists",
        )
        self.row.refresh_from_db()
        self.assertEqual(self.row.provider_name, "Test Bank")

    def test_an_audit_failure_rolls_the_change_back(self):
        with mock.patch("apps.operators.audit.record", side_effect=RuntimeError("audit down")):
            self.assertEqual(self.edit(account_number="9999 9999 99").status_code, 500)
        self.row.refresh_from_db()
        self.assertEqual(self.row.account_number, "0000 0000 00")

    def test_an_audit_failure_rolls_an_add_and_a_switch_back(self):
        self.add()
        with mock.patch("apps.operators.audit.record", side_effect=RuntimeError("audit down")):
            self.assertEqual(self.accounts("add/", {**NEW, "account_number": "5555 5555"}).status_code, 500)
            self.assertEqual(
                self.accounts("active/", {"account": self.row.public_id, "active": False}).status_code, 500
            )
        self.assertEqual(ReceivingAccount.objects.count(), 2)
        self.row.refresh_from_db()
        self.assertTrue(self.row.active)


class ActiveTests(AccountsTestCase):
    def setUp(self):
        super().setUp()
        self.first = ReceivingAccount.objects.get()
        self.second = ReceivingAccount.objects.get(public_id=self.add()["id"])

    def active(self, row, active):
        return self.accounts("active/", {"account": row.public_id, "active": active})

    def test_deactivate_and_reactivate_each_write_one_entry_with_before_and_after(self):
        self.assertEqual(self.active(self.second, False).json()["active"], False)
        self.assertEqual(self.active(self.second, True).json()["active"], True)
        off, on = self.entries("billing_receiving_account_deactivated", "billing_receiving_account_activated")
        self.assertEqual(off.action, "billing_receiving_account_deactivated")
        self.assertEqual((off.detail["before"]["active"], off.detail["after"]["active"]), (True, False))
        self.assertEqual(on.action, "billing_receiving_account_activated")
        self.assertEqual((on.detail["before"]["active"], on.detail["after"]["active"]), (False, True))
        self.assertEqual(off.target_id, self.second.public_id)

    def test_setting_the_state_it_already_has_writes_nothing(self):
        before = len(self.entries())
        self.assertEqual(self.active(self.second, True).status_code, 200)
        self.assertEqual(len(self.entries()), before)

    def test_the_last_active_account_cannot_be_switched_off_while_the_switch_is_on(self):
        self.assertEqual(self.active(self.first, False).status_code, 200)
        response = self.active(self.second, False)
        self.assertError(response, 409, "last_active_account")
        self.assertIn("Add the new account first", response.json()["error"]["message"])
        self.second.refresh_from_db()
        self.assertTrue(self.second.active)
        self.assertTrue(plan.collection_live())

    def test_the_last_active_account_can_be_switched_off_with_the_switch_off(self):
        self.active(self.first, False)
        with override_settings(BILLING_BANK_TRANSFER_ENABLED=False):
            self.assertEqual(self.active(self.second, False).status_code, 200)
        self.assertEqual(ReceivingAccount.objects.filter(active=True).count(), 0)

    def test_checks_the_input(self):
        self.assertError(self.accounts("active/", {"account": self.first.public_id}), 400, "invalid_request")
        self.assertError(self.accounts("active/", {"account": self.first.public_id, "active": "no"}), 400, "invalid_request")
        self.assertError(self.accounts("active/", {"account": "rca_nope", "active": False}), 404, "not_found")


class NothingIsDeletedTests(AccountsTestCase):
    def test_a_row_cannot_be_deleted_and_there_is_no_delete_route(self):
        with self.assertRaises(RuntimeError):
            ReceivingAccount.objects.get().delete()
        self.assertEqual(ReceivingAccount.objects.count(), 1)
        self.assertNotIn("finance/accounts/delete/", staff.ROUTES)
        self.assertNotIn("finance/accounts/remove/", staff.ROUTES)


class ModelTests(AccountsTestCase):
    def test_the_database_refuses_blank_text_a_bad_kind_and_a_duplicate(self):
        from django.db import IntegrityError, transaction

        base = dict(created_by=self.operator, updated_by=self.operator)
        row = ReceivingAccount.objects.get()
        for bad in (
            dict(kind="card", provider_name="a", account_name="b", account_number="111111"),
            dict(kind="bank", provider_name="", account_name="b", account_number="111111"),
            dict(kind="bank", provider_name="a", account_name="", account_number="111111"),
            dict(kind="bank", provider_name="a", account_name="b", account_number=""),
            dict(kind="bank", provider_name=row.provider_name, account_name="b", account_number=row.account_number),
        ):
            with self.subTest(bad=bad), self.assertRaises(IntegrityError), transaction.atomic():
                ReceivingAccount.objects.create(**bad, **base)


class AuditTests(AccountsTestCase):
    def test_the_four_actions_are_kept_and_hold_no_personal_value(self):
        from apps.operators import audit

        for action in ACTIONS:
            self.assertEqual(audit.ACTIONS[action], audit.KEPT)
            with self.assertRaises(ValueError):
                audit.record(self.operator, action, personal={"x": "y"})


class IsolationTests(AccountsTestCase):
    def test_only_the_billing_modules_import_the_model(self):
        root = Path(__file__).resolve().parents[2]
        allowed = {
            root / "billing" / name for name in ("plan.py", "accounts.py", "models.py")
        }
        for path in root.rglob("*.py"):
            if "tests" in path.parts or "migrations" in path.parts or path in allowed:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                self.assertNotIn("ReceivingAccount", names, str(path))
                if isinstance(node, ast.Attribute) and node.attr == "ReceivingAccount":
                    self.fail(f"{path} reaches the model")

    def test_the_table_has_no_owner_so_the_answer_is_the_same_for_every_subscriber(self):
        names = {field.name for field in ReceivingAccount._meta.get_fields()}
        self.assertFalse(names & {"site", "account", "subscription", "owner"})


@override_settings(HQ_HOST=HQ, ALLOWED_HOSTS=[HQ, "testserver"], HQ_STAFF_ORIGIN=STAFF)
class SubscriberCannotReachTests(TransferApiCase):
    def rows(self):
        return list(ReceivingAccount.objects.order_by("pk").values())

    def test_the_payment_route_shows_only_active_accounts_and_only_the_four_fields(self):
        make_receiving_account(
            [{"kind": "bank", "provider_name": "Off", "account_name": "n", "account_number": "999999"}],
            active=False,
        )
        body = self.get(self.base + "payment/").json()
        self.assertEqual(body["transfer_accounts"], [{
            "kind": "bank", "provider_name": "Test Bank", "account_name": "Test Receiver",
            "account_number": "0000 0000 00",
        }])
        self.assertNotIn("Off", json.dumps(body))

    def test_a_subscriber_session_is_not_a_staff_session_and_nothing_changes(self):
        before = self.rows()
        audits = OperatorAuditEntry.objects.count()
        for path in PATHS:
            for origin in (STAFF, None):
                kwargs = {"HTTP_ORIGIN": origin} if origin else {}
                response = self.client.post(
                    BASE + path, data=json.dumps({**NEW, "account": "x", "active": False}),
                    content_type="application/json", secure=True, HTTP_HOST=HQ, **kwargs,
                )
                self.assertIn(response.status_code, (401, 403), path)
        self.assertEqual(self.rows(), before)
        self.assertEqual(OperatorAuditEntry.objects.count(), audits)

    def test_every_subscriber_call_leaves_the_rows_as_they_were(self):
        before = self.rows()
        self.get(self.base + "payment/")
        self.upload()
        self.get(self.base + "payment/")
        self.assertEqual(self.rows(), before)
        for path in PATHS:
            self.assertEqual(
                self.client.post(f"/v1/billing/accounts/{path}", secure=True).status_code, 404
            )
        self.assertEqual(self.rows(), before)


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(
    HQ_HOST=HQ, ALLOWED_HOSTS=[HQ, "testserver"], HQ_STAFF_ORIGIN=STAFF, BILLING_ENABLED=True
)
class AccountsConcurrencyTests(HqAdminMixin, TransactionTestCase):
    def setUp(self):
        switched = transfers_on()
        switched.__enter__()
        self.addCleanup(switched.__exit__, None, None, None)
        self.open_hq_session(make_operator())

    def call(self, path, body):
        def run():
            client = Client()
            client.cookies = self.client.cookies
            return client.post(
                BASE + path, data=json.dumps(body), content_type="application/json",
                secure=True, HTTP_HOST=HQ, HTTP_ORIGIN=STAFF,
            )
        return run

    def test_two_deactivations_of_two_active_rows_leave_one(self):
        first = ReceivingAccount.objects.get()
        second = make_receiving_account(
            [{"kind": "bank", "provider_name": "Two", "account_name": "n", "account_number": "222222"}]
        )[0]
        results = run_together(
            self.call("active/", {"account": first.public_id, "active": False}),
            self.call("active/", {"account": second.public_id, "active": False}),
        )
        self.assertEqual(sorted(r.status_code for r in results), [200, 409], results)
        self.assertEqual(ReceivingAccount.objects.filter(active=True).count(), 1)
        self.assertTrue(plan.collection_live())

    def test_two_adds_of_the_same_account_save_one(self):
        results = run_together(self.call("add/", NEW), self.call("add/", NEW))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409], results)
        self.assertEqual(ReceivingAccount.objects.filter(provider_name="Test Wallet").count(), 1)
        self.assertEqual(
            OperatorAuditEntry.objects.filter(action="billing_receiving_account_added").count(), 1
        )

