from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from apps.billing.tests.base import make_active
from apps.invoices.models import InvoiceBook, InvoiceEvent, InvoiceScan, ManualInvoice, ManualInvoiceItem
from apps.statements.managers import Immutable
from apps.statements.tests.base import WHEN, make_book, make_invoice, make_issuer, make_item, make_staff, make_statement


def refuses_in_db(case):
    return case.assertRaises(IntegrityError), transaction.atomic()


@override_settings(BILLING_ENABLED=True)
class ItemTests(TestCase):
    def setUp(self):
        self.item = make_item()

    def test_snapshot_frozen_status_moves_along_flow(self):
        self.item.total_centavos = 1
        with self.assertRaises(Immutable):
            self.item.save()
        item = ManualInvoiceItem.objects.get(pk=self.item.pk)
        item.status = "verified"  # pending cannot jump to verified
        with self.assertRaises(Immutable):
            item.save()
        item.status = "recorded"
        item.save()

    def test_statement_linked_once(self):
        first = make_statement(self.item.subscription)
        second = make_statement(self.item.subscription)
        self.item.statement = first
        self.item.save()
        self.item.statement = second
        with self.assertRaises(Immutable):
            self.item.save()

    def test_void_of_uploaded_invoice_returns_item_to_pending(self):
        item = ManualInvoiceItem.objects.get(pk=self.item.pk)
        for status in ("recorded", "uploaded", "pending"):
            item.status = status
            item.save()

    def test_delete_and_bulk_refused(self):
        with self.assertRaises(Immutable):
            self.item.delete()
        with self.assertRaises(Immutable):
            ManualInvoiceItem.objects.all().delete()
        with self.assertRaises(Immutable):
            ManualInvoiceItem.objects.update(status="recorded")

    def test_one_item_per_payment(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_item(self.item.subscription, ref=self.item.source_ref)

    def test_usage_only_item_has_no_cycle(self):
        item = make_item(self.item.subscription, cycle=None, ref="btp_usage")
        self.assertIsNone(item.cycle)

    def test_total_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_item(total=0)


@override_settings(BILLING_ENABLED=True)
class InvoiceTests(TestCase):
    def setUp(self):
        self.invoice = make_invoice()

    def test_duplicate_serial_in_book_refused(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_invoice(book=self.invoice.book, serial=self.invoice.serial_number)

    def test_one_live_invoice_per_item_and_void_frees_it(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_invoice(item=self.invoice.item, book=self.invoice.book, serial=2)
        self.invoice.status = "voided"
        self.invoice.void_reason = "wrong buyer"
        self.invoice.save()
        make_invoice(item=self.invoice.item, book=self.invoice.book, serial=2)

    def test_amount_due_arithmetic(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_invoice(serial=3, amount_due_centavos=1)

    def test_discount_kind_needs_discount(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_invoice(serial=3, discount_kind="senior")

    def test_fields_frozen_and_verified_only_voids(self):
        self.invoice.buyer_name = "Other"
        with self.assertRaises(Immutable):
            self.invoice.save()
        invoice = ManualInvoice.objects.get(pk=self.invoice.pk)
        invoice.status, invoice.verified_at, invoice.verified_by = "uploaded", WHEN, make_staff("a")
        invoice.save()
        invoice.status = "verified"
        invoice.save()
        invoice.status = "recorded"
        with self.assertRaises(Immutable):
            invoice.save()
        invoice.status = "voided"
        invoice.save()

    def test_verified_invoice_rewrites_nothing_without_a_void(self):
        invoice = ManualInvoice.objects.get(pk=self.invoice.pk)
        invoice.status = "uploaded"
        invoice.save()
        invoice.status, invoice.verified_at, invoice.verified_by = "verified", WHEN, make_staff("v")
        invoice.save()
        for field, value in (("verified_at", WHEN.replace(year=2027)), ("void_reason", "x"),
                             ("verified_by", make_staff("w"))):
            changed = ManualInvoice.objects.get(pk=invoice.pk)
            setattr(changed, field, value)
            with self.assertRaises(Immutable, msg=field):
                changed.save()

    def test_delete_and_bulk_refused(self):
        with self.assertRaises(Immutable):
            self.invoice.delete()
        with self.assertRaises(Immutable):
            ManualInvoice.objects.update(buyer_name="x")


@override_settings(BILLING_ENABLED=True)
class BookTests(TestCase):
    def test_range_ordered_and_never_deleted(self):
        book = make_book(series="Z")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_book(series="Y", serial_from=10, serial_to=5)
        with self.assertRaises(Immutable):
            book.delete()

    def test_book_is_evidence_only_active_moves(self):
        book = make_book(series="E")
        for field, value in (("serial_to", 99), ("series", "F"), ("atp_reference", "X"),
                             ("issuer", make_issuer())):
            changed = InvoiceBook.objects.get(pk=book.pk)
            setattr(changed, field, value)
            with self.assertRaises(Immutable, msg=field):
                changed.save()
        book.active = False
        book.save()
        with self.assertRaises(Immutable):
            InvoiceBook.objects.update(active=True)

    def test_second_booklet_of_same_or_blank_series_with_next_range(self):
        for series in ("A", ""):
            make_book(series=series, serial_from=1, serial_to=50)
            make_book(series=series, serial_from=51, serial_to=100)
            make_book(series=series, serial_from=101, serial_to=150)

    def test_overlapping_range_in_same_series_refused(self):
        make_book(series="A", serial_from=1, serial_to=50)
        for lo, hi in ((50, 80), (1, 1), (10, 20), (0, 200)):
            with self.assertRaises(Immutable):
                make_book(series="A", serial_from=lo, serial_to=hi)
        make_book(series="B", serial_from=1, serial_to=50)  # another series is free


@override_settings(BILLING_ENABLED=True)
class ScanTests(TestCase):
    def setUp(self):
        self.invoice = make_invoice()
        self.staff = make_staff("scanner")

    def scan(self, invoice=None, sha="a" * 64, **fields):
        return InvoiceScan.objects.create(
            invoice=invoice or self.invoice, store_key="k", content_type="application/pdf",
            size=1, sha256=sha, uploaded_by=self.staff, **fields,
        )

    def test_one_live_scan_per_invoice_rejected_stays(self):
        first = self.scan()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.scan(sha="b" * 64)
        first.status, first.reject_reason, first.decided_at = "rejected", "unreadable", WHEN
        first.save()
        self.scan(sha="b" * 64)

    def test_same_file_cannot_back_two_invoices(self):
        self.scan()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.scan(invoice=make_invoice(serial=9))

    def test_rejected_needs_reason(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.scan(status="rejected")

    def test_decided_scan_rewrites_nothing(self):
        scan = self.scan()
        scan.status, scan.reject_reason, scan.decided_at = "rejected", "unreadable", WHEN
        scan.save()
        rejected = InvoiceScan.objects.get(pk=scan.pk)
        rejected.reject_reason = "other"
        with self.assertRaises(Immutable):
            rejected.save()
        live = self.scan(sha="c" * 64)
        live.status, live.decided_at, live.decided_by = "verified", WHEN, self.staff
        live.save()
        verified = InvoiceScan.objects.get(pk=live.pk)
        verified.decided_at = WHEN.replace(year=2027)
        with self.assertRaises(Immutable):
            verified.save()

    def test_verified_scan_frozen(self):
        scan = self.scan()
        scan.status, scan.decided_at, scan.decided_by = "verified", WHEN, self.staff
        scan.save()
        scan.status, scan.reject_reason = "rejected", "other"
        with self.assertRaises(Immutable):
            scan.save()
        with self.assertRaises(Immutable):
            scan.delete()


@override_settings(BILLING_ENABLED=True)
class EventTests(TestCase):
    def test_append_only(self):
        item = make_item()
        event = InvoiceEvent.objects.create(item=item, code="opened", idempotency_key="opened:1")
        event.code = "voided"
        with self.assertRaises(Immutable):
            event.save()
        with self.assertRaises(Immutable):
            event.delete()
        with self.assertRaises(Immutable):
            InvoiceEvent.objects.update(code="x")
        with self.assertRaises(IntegrityError), transaction.atomic():
            InvoiceEvent.objects.create(item=item, code="opened", idempotency_key="opened:1")
