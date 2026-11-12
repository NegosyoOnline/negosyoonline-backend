"""The register routes (Billing Build B5, PR 3) through the real staff gate."""
from unittest import mock

from django.test import override_settings

from apps.billing import plan, services as billing
from apps.billing.tests.base import make_active, make_operator, submit, transfers_on
from apps.invoices.models import ManualInvoice, ManualInvoiceItem
from apps.invoices.tests.test_register import AT, ON
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff
from apps.statements.tests.base import make_book

BASE = "/staff/v1/finance/invoices/"
PATHS = ("queue/", "detail/", "record/", "void/", "reconcile/")


@on_staff
@override_settings(**ON)
class RouteTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(plan, "STORAGE_METERED", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        self.operator = make_operator()
        self.enrolled()
        payment = billing.confirm_transfer(
            submit(make_active(), AT), self.operator, amount_received_centavos=249_900, now=AT
        )
        self.item = ManualInvoiceItem.objects.get(source_ref=payment.public_id)
        make_book(series="A")

    def post(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def record_body(self, **fields):
        return {
            "item": self.item.public_id, "series": "A", "serial_from": 1, "serial_number": 7,
            "issued_on": self.item.sale_date.isoformat(), "buyer_name": "Buyer",
            "description": "Paper", "service_start": "2026-12-01", "service_end": "2026-12-31",
            "total_sales_centavos": 249_900, **fields,
        }

    def test_gate_origin_switch_and_method(self):
        for path in PATHS:
            self.assertError(self.post(path), 401, "unauthenticated")
        self.staff_sign_in()
        for path in PATHS:
            self.assertError(self.post(path, origin="https://evil.example"), 403, "origin_not_allowed")
        with override_settings(BILLING_INVOICE_REGISTER_ENABLED=False):
            for path in PATHS:
                self.assertError(self.post(path, {"item": self.item.public_id}), 404, "not_found")
        self.assertError(self.call("GET", BASE + "queue/"), 405, "method_not_allowed")
        self.assertEqual(ManualInvoice.objects.count(), 0)

    def test_queue_groups_by_paid_date_with_the_fixed_notice_and_filters(self):
        self.staff_sign_in()
        body = self.post("queue/").json()
        self.assertIn("DO NOT issue invoices in a single delayed Monday batch", body["notice"])
        self.assertEqual(body["pending_count"], 1)
        group = body["groups"][0]
        self.assertEqual(group["paid_date"], self.item.sale_date.isoformat())
        row = group["items"][0]
        self.assertEqual((row["item"], row["status"], row["total_centavos"]),
                         (self.item.public_id, "pending", 249_900))
        for name in ("awaiting_upload", "awaiting_verification", "published"):
            self.assertEqual(self.post("queue/", {"filter": name}).json()["count"], 0)
        self.assertEqual(self.post("queue/", {"filter": "all"}).json()["count"], 1)
        self.assertError(self.post("queue/", {"filter": "nope"}), 400, "invalid_request")
        self.assertNotIn("@", str(body))

    def test_detail_says_what_to_write_and_never_fills_the_serial_or_the_issue_date(self):
        self.staff_sign_in()
        body = self.post("detail/", {"item": self.item.public_id}).json()
        suggested = body["suggested"]
        self.assertEqual(suggested["label"], "WRITE THIS ON PAPER")
        self.assertEqual(suggested["sale_type"], "cash")
        self.assertIn("Monthly Plan", suggested["rows"][0]["description"])
        self.assertIn("no default", suggested["date"]["issue_date"])
        self.assertEqual(body["books"][0]["next_expected_hint"], 1)
        self.assertEqual(body["invoices"], [])
        self.assertTrue(body["is_test"])
        self.assertError(self.post("detail/", {"item": "mbi_nope"}), 404, "not_found")
        self.assertError(self.post("detail/", {}), 400, "invalid_request")

    def test_record_void_and_reconcile(self):
        self.staff_sign_in()
        made = self.post("record/", self.record_body())
        self.assertEqual(made.status_code, 201)
        invoice = made.json()
        self.assertEqual((invoice["serial"], invoice["status"], invoice["amount_due_centavos"]),
                         ("A7", "recorded", 249_900))
        self.assertEqual(self.post("record/", self.record_body()).json()["invoice"], invoice["invoice"])
        self.assertEqual(OperatorAuditEntry.objects.filter(action="billing_invoice_recorded").count(), 1)
        self.assertError(self.post("record/", self.record_body(serial_number=8)), 409, "already_recorded")
        detail = self.post("detail/", {"item": self.item.public_id}).json()
        self.assertEqual([i["serial"] for i in detail["invoices"]], ["A7"])
        self.assertIn("serial_gap", [e["code"] for e in detail["exceptions"]])
        self.assertEqual(self.post("queue/", {"filter": "awaiting_upload"}).json()["count"], 1)
        rec = self.post("reconcile/").json()
        row = rec["items"][0]
        self.assertEqual((rec["count"], row["serial"], row["paid_centavos"], row["statement"], row["adjustments"]),
                         (1, "A7", 249_900, None, []))
        self.assertEqual(self.post("reconcile/", {"from": "2099-01-01"}).json()["count"], 0)
        self.assertError(self.post("reconcile/", {"from": "x"}), 400, "invalid_request")
        gone = self.post("void/", {"invoice": invoice["invoice"], "reason": "Mistake on paper"})
        self.assertEqual(gone.json()["status"], "voided")
        self.assertError(self.post("void/", {"invoice": invoice["invoice"]}), 400, "invalid_request")
        self.assertEqual(self.post("reconcile/").json()["count"], 0)
        self.assertEqual(self.post("queue/").json()["count"], 1)
        self.assertEqual(ManualInvoice.objects.count(), 1)
