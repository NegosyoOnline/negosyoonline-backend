"""The register core (Billing Build B5, PR 3): the item a confirmation opens, the safety net,
recording and voiding the paper invoice, the exceptions, the queue and the reconciliation."""
from datetime import date
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.billing import plan, services as billing
from apps.billing.models import ChargeLine
from apps.billing.tests.base import (
    CYCLE_1_END, SECOND, days, fresh, make_active, make_operator, transfers_on, submit,
)
from apps.billing.tests.test_usage_collection import GB, LINE, MONTHLY, snapshot
from apps.common.errors import ApiError
from apps.invoices import services, tasks
from apps.invoices.models import InvoiceEvent, ManualInvoice, ManualInvoiceItem
from apps.operators.models import OperatorAuditEntry
from apps.statements.tests.base import make_book, make_staff

AT = CYCLE_1_END + days(1)
ON = dict(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=True, BILLING_INVOICE_REGISTER_ENABLED=True)


@override_settings(**ON)
class RegisterCase(TestCase):
    def setUp(self):
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        patcher = mock.patch.object(plan, "STORAGE_METERED", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.operator = make_operator()
        self.staff = make_staff("finance")

    def confirm(self, payment, at=AT):
        return billing.confirm_transfer(
            payment, self.operator, amount_received_centavos=payment.amount_centavos, now=at
        )

    def paid(self, subscription=None, at=AT):
        """A renewal payment, confirmed. Returns the payment."""
        subscription = subscription or make_active()
        return self.confirm(submit(subscription, at), at)

    def other_sale(self):
        """A payment of another subscriber."""
        self._n = getattr(self, "_n", 0) + 1
        return self.paid(make_active(email=f"other{self._n}@example.test", name=f"other-{self._n}"))

    def item_of(self, payment):
        return ManualInvoiceItem.objects.get(source_ref=payment.public_id)

    def book(self, **fields):
        return make_book(**{"series": "A", **fields})

    def body(self, item, book, serial=1, **fields):
        return {
            "item": item.public_id, "series": book.series, "serial_from": book.serial_from,
            "serial_number": serial, "issued_on": item.sale_date.isoformat(), "sale_type": "cash",
            "buyer_name": "Buyer", "description": "Written on paper",
            "service_start": date(2026, 12, 1).isoformat(), "service_end": date(2026, 12, 31).isoformat(),
            "total_sales_centavos": item.total_centavos, **fields,
        }

    def refused(self, code, call, *args):
        with self.assertRaises(ApiError) as raised:
            call(*args)
        self.assertEqual(raised.exception.code, code)


class OpenTests(RegisterCase):
    def test_a_confirmation_opens_one_frozen_item_and_a_repeat_returns_it(self):
        payment = self.paid()
        item = self.item_of(payment)
        self.assertEqual(
            (item.status, item.total_centavos, item.source_kind, item.is_test, item.cycle_id),
            ("pending", MONTHLY, "bank_transfer", True, payment.cycle_id),
        )
        self.assertEqual(sum(l["amount_centavos"] for l in item.lines), item.total_centavos)
        self.assertTrue(item.lines[0]["description"].startswith(
            "NegosyoOnline SaaS subscription — Monthly Plan, "))
        self.assertEqual(item.sale_date, services.manila_date(AT))
        self.assertEqual(services.open_item(payment).pk, item.pk)
        self.assertEqual(ManualInvoiceItem.objects.count(), 1)
        self.assertEqual(list(item.events.values_list("code", "idempotency_key")),
                         [("opened", f"opened:{payment.public_id}")])

    @override_settings(BILLING_LIVE_DOCUMENTS=True)
    def test_live_documents_are_not_marked_test(self):
        self.assertFalse(self.item_of(self.paid()).is_test)

    def test_switch_off_opens_nothing_and_the_confirmation_is_unaffected(self):
        with override_settings(BILLING_INVOICE_REGISTER_ENABLED=False):
            payment = self.paid()
            self.assertEqual(payment.status, "confirmed")
            self.assertIsNone(services.open_item(payment))
            self.assertEqual(services.ensure_items(), 0)
        self.assertFalse(ManualInvoiceItem.objects.exists())

    def test_an_error_in_the_register_does_not_fail_the_confirmation_and_the_net_catches_it(self):
        subscription = make_active()
        payment = submit(subscription, AT)
        with mock.patch.object(services, "_lines", side_effect=RuntimeError("boom")):
            confirmed = self.confirm(payment)
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(fresh(subscription).cycles.get(sequence=2).settlement, "paid")
        self.assertFalse(ManualInvoiceItem.objects.exists())
        self.assertEqual(tasks.ensure_items(), 1)
        self.assertEqual(tasks.ensure_items(), 0)
        self.assertEqual(ManualInvoiceItem.objects.count(), 1)

    def test_backfill_covers_a_payment_confirmed_before_the_switch(self):
        with override_settings(BILLING_INVOICE_REGISTER_ENABLED=False):
            self.paid()
        out = StringIO()
        call_command("invoices_backfill_items", stdout=out)
        self.assertIn("Payments without an item: 1", out.getvalue())
        self.assertFalse(ManualInvoiceItem.objects.exists())
        call_command("invoices_backfill_items", "--apply", stdout=out)
        self.assertEqual(ManualInvoiceItem.objects.count(), 1)
        call_command("invoices_backfill_items", "--apply", stdout=out)
        self.assertEqual(ManualInvoiceItem.objects.count(), 1)
        with override_settings(BILLING_INVOICE_REGISTER_ENABLED=False):
            out = StringIO()
            call_command("invoices_backfill_items", "--apply", stdout=out)
            self.assertIn("off", out.getvalue())

    def test_a_term_payment_with_overage_has_a_base_and_an_overage_line_that_add_up(self):
        subscription = make_active()
        snapshot(subscription)
        payment = self.confirm(submit(subscription, AT), AT)
        item = self.item_of(payment)
        self.assertEqual([l["kind"] for l in item.lines], ["base", "usage"])
        self.assertEqual([l["amount_centavos"] for l in item.lines], [MONTHLY, LINE])
        self.assertEqual(item.total_centavos, payment.amount_received_centavos)
        self.assertIn("storage overage", item.lines[1]["description"])

    def test_a_usage_only_payment_has_an_item_with_no_cycle_and_only_overage_lines(self):
        subscription = make_active()
        billing.cancel(subscription, now=CYCLE_1_END - days(2))
        snapshot(subscription)
        payment = self.confirm(submit(fresh(subscription), AT, plan_code=plan.USAGE_ONLY), AT)
        item = self.item_of(payment)
        self.assertIsNone(item.cycle)
        self.assertEqual([l["kind"] for l in item.lines], ["usage"])
        self.assertEqual((item.total_centavos, item.plan_code), (LINE, "usage_only"))
        self.assertNotIn("unknown_plan_wording", [e["code"] for e in services.exceptions(item)])


class RecordTests(RegisterCase):
    def setUp(self):
        super().setUp()
        self.payment = self.paid()
        self.item = self.item_of(self.payment)
        self.book_ = self.book()

    def record(self, **fields):
        return services.record_invoice(self.staff, self.body(self.item, self.book_, **fields))

    def test_recording_moves_the_item_writes_an_event_and_an_audit_and_a_repeat_is_a_replay(self):
        invoice = self.record(total_sales_centavos=self.item.total_centavos)
        self.item.refresh_from_db()
        self.assertEqual((self.item.status, invoice.status, invoice.amount_due_centavos),
                         ("recorded", "recorded", MONTHLY))
        self.assertEqual(invoice.recorded_by, self.staff)
        self.assertEqual(invoice.issued_on, self.item.sale_date)
        self.assertEqual(self.item.events.filter(code="recorded").count(), 1)
        entry = OperatorAuditEntry.objects.get(action="billing_invoice_recorded")
        self.assertEqual(entry.detail["serial"], "A1")  # series A, serial 1, width 1
        self.assertEqual(self.record().pk, invoice.pk)
        self.assertEqual(ManualInvoice.objects.count(), 1)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="billing_invoice_recorded").count(), 1)

    def test_refusals_change_nothing(self):
        other = self.item_of(self.other_sale())
        self.record()
        with self.assertRaises(ApiError) as r:
            services.record_invoice(self.staff, self.body(self.item, self.book_, serial=2))
        self.assertEqual(r.exception.code, "already_recorded")
        with self.assertRaises(ApiError) as r:  # the same serial for another sale
            services.record_invoice(self.staff, self.body(other, self.book_, serial=1))
        self.assertEqual(r.exception.code, "duplicate_serial")
        with self.assertRaises(ApiError) as r:
            services.record_invoice(self.staff, self.body(other, self.book_, serial=2, total_sales_centavos=1))
        self.assertEqual(r.exception.code, "amount_mismatch")
        off = self.book(series="B")
        off.active = False
        off.save()
        with self.assertRaises(ApiError) as r:
            services.record_invoice(self.staff, self.body(other, off, serial=1))
        self.assertEqual(r.exception.code, "book_inactive")
        self.assertEqual(ManualInvoice.objects.count(), 1)
        self.assertEqual(other.invoices.count(), 0)

    def test_missing_and_bad_fields_are_refused(self):
        item = self.item
        for field, value in (
            ("issued_on", None), ("issued_on", "soon"), ("buyer_name", ""), ("description", ""),
            ("service_start", None), ("total_sales_centavos", True), ("serial_number", -1),
            ("sale_type", "barter"), ("discount_centavos", 5), ("discount_kind", "senior"),
            ("withholding_centavos", MONTHLY + 1), ("service_end", "2020-01-01"),
        ):
            with self.assertRaises(ApiError, msg=field) as r:
                services.record_invoice(self.staff, self.body(item, self.book_, **{field: value}))
            self.assertEqual(r.exception.code, "invalid_request", field)
        body = self.body(item, self.book_)
        del body["issued_on"]  # the issue date has no default
        with self.assertRaises(ApiError):
            services.record_invoice(self.staff, body)
        self.assertEqual(ManualInvoice.objects.count(), 0)

    def test_amount_due_is_total_less_deductions_and_the_deductions_are_flagged(self):
        invoice = self.record(discount_centavos=100, discount_kind="senior", withholding_centavos=50)
        self.assertEqual(invoice.amount_due_centavos, MONTHLY - 150)
        self.assertIn("discount_or_withholding", [e["code"] for e in services.exceptions(self.item, invoice)])

    def test_exceptions_are_facts_not_refusals(self):
        book = self.book(series="C", serial_from=10, serial_to=20)
        invoice = services.record_invoice(self.staff, self.body(
            self.item, book, serial=25, issued_on="2026-01-01",
            service_start="2020-01-01", service_end="2020-02-01",
        ))
        codes = [e["code"] for e in services.exceptions(self.item, invoice)]
        for code in ("serial_out_of_range", "serial_gap", "issued_before_payment", "service_dates_differ"):
            self.assertIn(code, codes)
        late = services.exceptions(self.item, invoice, today=self.item.sale_date)
        self.assertNotIn("issued_late", [e["code"] for e in late])
        invoice.issued_on = date(2099, 1, 1)
        late = services.exceptions(self.item, invoice)
        self.assertIn(("issued_late"), [e["code"] for e in late])

    def test_a_serial_out_of_date_order_is_flagged(self):
        other = self.item_of(self.other_sale())
        services.record_invoice(self.staff, self.body(self.item, self.book_, serial=1, issued_on="2026-12-20"))
        second = services.record_invoice(self.staff, self.body(other, self.book_, serial=2, issued_on="2026-12-10"))
        self.assertIn("serial_out_of_order", [e["code"] for e in services.exceptions(other, second)])

    def test_void_keeps_the_row_returns_the_item_and_allows_a_replacement(self):
        invoice = self.record()
        voided = services.void_invoice(self.staff, invoice.public_id, "Wrong buyer written")
        self.item.refresh_from_db()
        self.assertEqual((voided.status, voided.void_reason, self.item.status),
                         ("voided", "Wrong buyer written", "pending"))
        self.assertEqual(services.void_invoice(self.staff, invoice.public_id, "again").pk, invoice.pk)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="billing_invoice_voided").count(), 1)
        replacement = services.record_invoice(self.staff, self.body(self.item, self.book_, serial=2))
        self.assertEqual(ManualInvoice.objects.count(), 2)
        self.assertEqual(replacement.status, "recorded")
        # the voided serial stays used
        other = self.item_of(self.other_sale())
        with self.assertRaises(ApiError) as r:
            services.record_invoice(self.staff, self.body(other, self.book_, serial=1))
        self.assertEqual(r.exception.code, "duplicate_serial")
        self.assertEqual(list(self.item.events.values_list("code", flat=True)),
                         ["opened", "recorded", "voided", "recorded"])
        self.refused("invalid_request", services.void_invoice, self.staff, replacement.public_id, " ")
        self.refused("not_found", services.void_invoice, self.staff, "min_nope", "x")

    def test_no_event_or_invoice_is_ever_deleted(self):
        invoice = self.record()
        with self.assertRaises(Exception):
            invoice.delete()
        with self.assertRaises(Exception):
            InvoiceEvent.objects.all().delete()
