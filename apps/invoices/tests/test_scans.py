"""Invoice scans and the owner invoice routes (Billing Build B5, PR 4)."""
import hashlib
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.billing import plan, services as billing
from apps.billing.models import Subscription
from apps.billing.tests.base import JPEG, PDF, PNG, days, make_active, make_operator, submit, transfers_on
from apps.billing.tests.test_transfer_api import TransferApiCase
from apps.common import private_files
from apps.common.errors import ApiError
from apps.invoices import scans, services
from apps.invoices.models import InvoiceEvent, InvoiceScan, ManualInvoice, ManualInvoiceItem
from apps.invoices.staff import ROUTES as INVOICE_ROUTES
from apps.invoices.tests.test_register import AT, ON, RegisterCase
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import HQ, STAFF, StaffTestCase, on_staff
from apps.statements.tests.base import make_book

BASE = "/staff/v1/finance/invoices/"
GOOD = JPEG + b"scan-one"
OTHER = PNG + b"scan-two"


class MemoryScanStore:
    def __init__(self):
        self.files = {}

    def save(self, content, content_type):
        key = private_files.new_key(scans.FOLDER, content_type)
        self.files[key] = bytes(content)
        return key

    def read(self, key):
        return self.files[key]

    def discard(self, key):
        self.files.pop(key, None)


class ScanStoreMixin:
    def use_scan_store(self):
        self.scan_store = MemoryScanStore()
        for name in ("save", "read", "discard"):
            patcher = mock.patch(f"apps.invoices.scans.{name}", side_effect=getattr(self.scan_store, name))
            patcher.start()
            self.addCleanup(patcher.stop)


class ScanServiceTests(ScanStoreMixin, RegisterCase):
    def setUp(self):
        super().setUp()
        self.use_scan_store()
        self.book_a = self.book()

    def recorded(self, serial=1, payment=None):
        if payment is None:
            payment = self.other_sale() if ManualInvoiceItem.objects.exists() else self.paid()
        item = self.item_of(payment)
        return item, services.record_invoice(self.staff, self.body(item, self.book_a, serial=serial))

    def test_upload_stores_hashes_and_moves_the_invoice_item_and_trail(self):
        item, invoice = self.recorded()
        scan = services.upload_scan(self.staff, invoice.public_id, GOOD)
        invoice.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual((invoice.status, item.status), ("uploaded", "uploaded"))
        self.assertEqual(scan.sha256, hashlib.sha256(GOOD).hexdigest())
        self.assertEqual((scan.content_type, scan.size, scan.status), ("image/jpeg", len(GOOD), "uploaded"))
        self.assertTrue(scan.store_key.startswith("billing/invoice-scans/"))
        self.assertEqual(self.scan_store.files[scan.store_key], GOOD)
        self.assertEqual(InvoiceEvent.objects.filter(code="scan_uploaded").count(), 1)
        entry = OperatorAuditEntry.objects.get(action="billing_invoice_scan_uploaded")
        self.assertNotIn(scan.store_key, str(entry.detail))
        self.assertNotIn("Buyer", str(entry.detail))

    def test_the_type_is_decided_by_the_first_bytes_pdf_and_png_pass_others_do_not(self):
        for n, content in enumerate((PDF, PNG), start=1):
            _, invoice = self.recorded(serial=n)
            self.assertEqual(services.upload_scan(self.staff, invoice.public_id, content + b"x").size, len(content) + 1)
        _, invoice = self.recorded(serial=3)
        for bad in (b"GIF89a....", b"RIFF\0\0\0\0WEBPxxxx", b"<html>", b""):
            self.refused("invalid_request", services.upload_scan, self.staff, invoice.public_id, bad)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "recorded")
        self.assertEqual(len(self.scan_store.files), 2)

    def test_over_eight_megabytes_is_refused_and_exactly_eight_passes(self):
        _, invoice = self.recorded()
        self.refused("invalid_request", services.upload_scan, self.staff, invoice.public_id,
                     JPEG + b"x" * scans.MAX_BYTES)
        self.assertEqual(self.scan_store.files, {})
        ok = JPEG + b"x" * (scans.MAX_BYTES - len(JPEG))
        self.assertEqual(services.upload_scan(self.staff, invoice.public_id, ok).size, scans.MAX_BYTES)

    def test_a_duplicate_hash_among_live_scans_is_refused_and_leaves_no_file(self):
        _, first = self.recorded(serial=1)
        services.upload_scan(self.staff, first.public_id, GOOD)
        _, second = self.recorded(serial=2)
        self.refused("duplicate_scan", services.upload_scan, self.staff, second.public_id, GOOD)
        second.refresh_from_db()
        self.assertEqual(second.status, "recorded")
        self.assertEqual(len(self.scan_store.files), 1)

    def test_a_rejected_file_may_be_uploaded_again_and_the_scan_stays(self):
        item, invoice = self.recorded()
        scan = services.upload_scan(self.staff, invoice.public_id, GOOD)
        services.reject_scan(self.staff, scan.public_id, "unreadable")
        invoice.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual((invoice.status, item.status), ("recorded", "recorded"))
        scan.refresh_from_db()
        self.assertEqual((scan.status, scan.reject_reason), ("rejected", "unreadable"))
        again = services.upload_scan(self.staff, invoice.public_id, GOOD)
        self.assertNotEqual(again.pk, scan.pk)
        self.assertEqual(invoice.scans.count(), 2)
        self.assertEqual(InvoiceEvent.objects.filter(code="scan_rejected").count(), 1)
        self.assertTrue(OperatorAuditEntry.objects.filter(action="billing_invoice_scan_rejected").exists())

    def test_one_live_scan_per_invoice(self):
        _, invoice = self.recorded()
        services.upload_scan(self.staff, invoice.public_id, GOOD)
        self.refused("not_recorded", services.upload_scan, self.staff, invoice.public_id, OTHER)

    def test_reject_needs_a_listed_reason(self):
        _, invoice = self.recorded()
        scan = services.upload_scan(self.staff, invoice.public_id, GOOD)
        for reason in (None, "", "because", "UNREADABLE"):
            self.refused("invalid_request", services.reject_scan, self.staff, scan.public_id, reason)
        for reason in ("wrong_invoice", "amount_mismatch", "other"):
            self.assertIn(reason, InvoiceScan.RejectReason.values)

    def test_verify_freezes_the_scan_and_nothing_can_change_or_replace_it(self):
        item, invoice = self.recorded()
        scan = services.upload_scan(self.staff, invoice.public_id, GOOD)
        services.verify_scan(self.staff, scan.public_id)
        invoice.refresh_from_db()
        item.refresh_from_db()
        scan.refresh_from_db()
        self.assertEqual((scan.status, invoice.status, item.status), ("verified", "verified", "verified"))
        self.assertEqual((scan.decided_by, invoice.verified_by), (self.staff, self.staff))
        self.assertEqual(services.verify_scan(self.staff, scan.public_id).pk, scan.pk)  # a repeat
        self.assertEqual(InvoiceEvent.objects.filter(code="verified").count(), 1)
        self.refused("not_uploaded", services.reject_scan, self.staff, scan.public_id, "other")
        self.refused("not_recorded", services.upload_scan, self.staff, invoice.public_id, OTHER)
        scan.status = "rejected"
        with self.assertRaises(Exception):
            scan.save()
        scan.refresh_from_db()
        scan.store_key = "billing/invoice-scans/" + "0" * 32 + ".jpg"
        with self.assertRaises(Exception):
            scan.save()
        with self.assertRaises(Exception):
            InvoiceScan.objects.filter(pk=scan.pk).update(status="uploaded")
        entry = OperatorAuditEntry.objects.get(action="billing_invoice_verified")
        self.assertEqual(entry.detail["scan"], scan.public_id)

    def test_a_rejected_scan_cannot_then_be_verified(self):
        _, invoice = self.recorded()
        scan = services.upload_scan(self.staff, invoice.public_id, GOOD)
        services.reject_scan(self.staff, scan.public_id, "other")
        self.refused("not_uploaded", services.verify_scan, self.staff, scan.public_id)
        self.refused("not_uploaded", services.reject_scan, self.staff, scan.public_id, "unreadable")
        self.assertEqual(services.reject_scan(self.staff, scan.public_id, "other").pk, scan.pk)

    def test_a_failing_row_removes_the_file_it_stored(self):
        _, invoice = self.recorded()
        with mock.patch.object(services, "_event", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                services.upload_scan(self.staff, invoice.public_id, GOOD)
        self.assertEqual(self.scan_store.files, {})
        self.assertEqual(InvoiceScan.objects.count(), 0)

    def test_a_store_that_fails_answers_503_and_changes_nothing(self):
        _, invoice = self.recorded()
        with mock.patch("apps.invoices.scans.save", side_effect=private_files.StoreError("down")):
            with self.assertRaises(ApiError) as raised:
                services.upload_scan(self.staff, invoice.public_id, GOOD)
        self.assertEqual((raised.exception.status, raised.exception.code), (503, "scan_store_unavailable"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "recorded")

    def test_an_unknown_invoice_or_scan_is_404(self):
        self.refused("not_found", services.upload_scan, self.staff, "min_" + "0" * 26, GOOD)
        self.refused("not_found", services.verify_scan, self.staff, "scn_" + "0" * 26)
        self.refused("not_found", services.scan_for_operator, self.staff, "scn_" + "0" * 26)

    def test_reading_the_file_as_staff_is_audited_without_the_key(self):
        _, invoice = self.recorded()
        scan = services.upload_scan(self.staff, invoice.public_id, PDF + b"z")
        content, content_type = services.scan_for_operator(self.staff, scan.public_id)
        self.assertEqual((content, content_type), (PDF + b"z", "application/pdf"))
        entry = OperatorAuditEntry.objects.get(action="billing_invoice_scan_viewed")
        self.assertNotIn(scan.store_key, str(entry.detail))


@on_staff
@override_settings(**ON)
class ScanRouteTests(ScanStoreMixin, StaffTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(plan, "STORAGE_METERED", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        self.use_scan_store()
        self.operator = make_operator()
        self.enrolled()
        payment = billing.confirm_transfer(
            submit(make_active(), AT), self.operator, amount_received_centavos=249_900, now=AT
        )
        self.item = ManualInvoiceItem.objects.get(source_ref=payment.public_id)
        make_book(series="A")

    def post(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def upload(self, invoice, content=GOOD, **extra):
        data = {"invoice": invoice, "scan": SimpleUploadedFile("my name.exe", content, "text/plain")}
        data.update(extra)
        return self.client.post(
            BASE + "upload/", data=data, secure=True, HTTP_HOST=HQ, HTTP_ORIGIN=STAFF,
        )

    def record(self):
        return self.post("record/", {
            "item": self.item.public_id, "series": "A", "serial_from": 1, "serial_number": 7,
            "issued_on": self.item.sale_date.isoformat(), "buyer_name": "Buyer",
            "description": "Paper", "service_start": "2026-12-01", "service_end": "2026-12-31",
            "total_sales_centavos": 249_900,
        }).json()["invoice"]

    def test_the_four_routes_are_post_only_staff_only_and_collide_with_nothing(self):
        keys = [f"finance/invoices/{name}/" for name in ("scan", "upload", "verify", "reject")]
        for key in keys:
            self.assertIn(key, INVOICE_ROUTES)
            self.assertEqual(staff_views.ROUTES[key][0], "POST")
        others = [k for k in staff_views.ROUTES if k not in INVOICE_ROUTES]
        self.assertFalse(set(keys) & set(others))
        for key in keys:
            self.assertEqual(self.post(key[len("finance/invoices/"):]).status_code, 401)
        self.staff_sign_in()
        self.assertError(self.call("GET", BASE + "scan/"), 405, "method_not_allowed")
        for key in keys:
            self.assertError(
                self.post(key[len("finance/invoices/"):], origin="https://evil.example"), 403, "origin_not_allowed"
            )

    def test_switch_off_answers_404_for_the_new_routes(self):
        self.staff_sign_in()
        with override_settings(BILLING_INVOICE_REGISTER_ENABLED=False):
            for path in ("scan/", "verify/", "reject/"):
                self.assertError(self.post(path, {"scan": "scn_x"}), 404, "not_found")
            self.assertError(self.upload("min_x"), 404, "not_found")

    def test_upload_verify_and_read_through_the_gate_and_the_key_never_appears(self):
        self.staff_sign_in()
        invoice = self.record()
        response = self.upload(invoice)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["content_type"], body["size_bytes"]), ("uploaded", "image/jpeg", len(GOOD)))
        key = InvoiceScan.objects.get().store_key
        self.assertNotIn("billing/invoice-scans", response.content.decode())
        self.assertNotIn(key, response.content.decode())
        detail = self.post("detail/", {"item": self.item.public_id})
        self.assertNotIn(key, detail.content.decode())
        self.assertEqual(detail.json()["invoices"][0]["scans"][0]["scan"], body["scan"])
        file = self.post("scan/", {"scan": body["scan"]})
        self.assertEqual((file.status_code, file.content, file["Content-Type"]), (200, GOOD, "image/jpeg"))
        self.assertEqual(file["Cache-Control"], "no-store")
        self.assertEqual(file["X-Content-Type-Options"], "nosniff")
        self.assertEqual(file["Content-Disposition"], 'inline; filename="copy.jpg"')
        verified = self.post("verify/", {"scan": body["scan"]})
        self.assertEqual(verified.json()["status"], "verified")
        actions = set(OperatorAuditEntry.objects.values_list("action", flat=True))
        self.assertTrue({
            "billing_invoice_scan_uploaded", "billing_invoice_scan_viewed", "billing_invoice_verified",
        } <= actions)

    def test_reject_through_the_route_and_refusals_are_json(self):
        self.staff_sign_in()
        invoice = self.record()
        scan = self.upload(invoice).json()["scan"]
        self.assertError(self.post("reject/", {"scan": scan, "reason": "nope"}), 400, "invalid_request")
        self.assertEqual(self.post("reject/", {"scan": scan, "reason": "amount_mismatch"}).json()["reject_reason"], "amount_mismatch")
        self.assertError(self.upload(invoice, content=b"%PDF-1.4 other", extra="x"), 400, "invalid_request")  # unknown field
        self.assertError(self.upload(invoice, content=b"not a scan"), 400, "invalid_request")
        self.assertError(self.upload("min_" + "0" * 26), 404, "not_found")
        again = self.upload(invoice)
        self.assertEqual(again.status_code, 201)
        self.assertError(self.upload(invoice, content=OTHER), 409, "not_recorded")

    def test_a_missing_file_and_a_huge_body_are_refused_before_a_scan_exists(self):
        self.staff_sign_in()
        invoice = self.record()
        response = self.client.post(
            BASE + "upload/", data={"invoice": invoice}, secure=True, HTTP_HOST=HQ, HTTP_ORIGIN=STAFF
        )
        self.assertError(response, 400, "invalid_request")
        with mock.patch.object(scans, "MAX_BYTES", 20):
            self.assertError(self.upload(invoice, content=JPEG + b"y" * 40), 400, "invalid_request")
        self.assertEqual(InvoiceScan.objects.count(), 0)


@override_settings(BILLING_ENABLED=True, BILLING_INVOICE_REGISTER_ENABLED=True)
class OwnerInvoiceRouteTests(ScanStoreMixin, TransferApiCase):
    def setUp(self):
        super().setUp()
        self.use_scan_store()
        self.staff = make_operator("finance-staff")
        self.book = make_book(series="A")
        self.serial = 0
        self.paid_item, self.mine = self.sale(self.site)

    def sale(self, site, upload=None, client=None):
        """A confirmed payment of `site` and its register item."""
        subscription = Subscription.objects.get(site=site)
        sent = submit(subscription, self.start + days(1), content=upload or JPEG + bytes([site.pk % 250]))
        payment = billing.confirm_transfer(
            sent, self.operator, amount_received_centavos=sent.amount_centavos, now=self.start + days(2)
        )
        item = ManualInvoiceItem.objects.get(source_ref=payment.public_id)
        return item, f"/v1/sites/{site.public_id}/billing/invoices/"

    def record(self, item):
        self.serial += 1
        return services.record_invoice(self.staff, {
            "item": item.public_id, "series": "A", "serial_from": self.book.serial_from,
            "serial_number": self.serial, "issued_on": item.sale_date.isoformat(), "buyer_name": "Secret Buyer",
            "description": "Paper", "service_start": "2026-12-01", "service_end": "2026-12-31",
            "total_sales_centavos": item.total_centavos,
        })

    def verified(self, item, content=GOOD):
        invoice = self.record(item)
        scan = services.upload_scan(self.staff, invoice.public_id, content)
        services.verify_scan(self.staff, scan.public_id)
        return invoice, scan

    def test_a_paid_sale_is_pending_with_the_exact_text_and_no_serial_scan_or_hash(self):
        body = self.get(self.mine).json()
        self.assertEqual(len(body["items"]), 1)
        row = body["items"][0]
        self.assertEqual(row["id"], self.paid_item.public_id)
        self.assertEqual((row["state"], row["message"]), ("pending", "Payment received; official invoice copy pending upload"))
        self.assertEqual(self.get(self.mine + f"{row['id']}/copy/").status_code, 404)
        # Recorded and uploaded (not verified) look the same, and show nothing of the paper.
        invoice = self.record(self.paid_item)
        scan = services.upload_scan(self.staff, invoice.public_id, GOOD)
        after = self.get(self.mine)
        self.assertEqual(after.json()["items"][0]["state"], "pending")
        text = after.content.decode()
        for secret in (scan.public_id, scan.sha256, scan.store_key, invoice.public_id, "Secret Buyer", "A0000"):
            self.assertNotIn(secret, text)
        self.assertEqual(set(after.json()["items"][0]), {
            "id", "state", "message", "plan", "period_start_at", "period_end_at", "paid_at",
            "total_centavos", "is_test",
        })
        self.error(self.get(self.mine + f"{row['id']}/copy/"), 404, "not_found")

    def test_a_verified_copy_is_available_and_sent_privately_with_a_neutral_name(self):
        self.verified(self.paid_item, PDF + b"copy")
        row = self.get(self.mine).json()["items"][0]
        self.assertEqual(
            (row["state"], row["message"]),
            ("available", "Copy of manually issued BIR-registered Non-VAT Invoice"),
        )
        response = self.get(self.mine + f"{row['id']}/copy/")
        self.assertEqual((response.status_code, response.content), (200, PDF + b"copy"))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Content-Disposition"], 'inline; filename="copy.pdf"')

    def test_rejected_voided_and_unmatched_are_never_served_and_all_answer_the_same_404(self):
        copy = lambda: self.get(self.mine + f"{self.paid_item.public_id}/copy/")  # noqa: E731
        reference = self.error(copy(), 404, "not_found")
        invoice = self.record(self.paid_item)
        scan = services.upload_scan(self.staff, invoice.public_id, GOOD)
        self.assertEqual(self.error(copy(), 404, "not_found"), reference)  # uploaded
        services.reject_scan(self.staff, scan.public_id, "unreadable")
        self.assertEqual(self.error(copy(), 404, "not_found"), reference)  # rejected
        second = services.upload_scan(self.staff, invoice.public_id, OTHER)
        services.verify_scan(self.staff, second.public_id)
        self.assertEqual(copy().status_code, 200)
        services.void_invoice(self.staff, invoice.public_id, "typo on paper")  # voided
        self.assertEqual(self.error(copy(), 404, "not_found"), reference)
        self.assertEqual(self.get(self.mine).json()["items"][0]["state"], "pending")
        # A replacement invoice with an unverified scan is not served either.
        replacement = self.record(self.paid_item)
        services.upload_scan(self.staff, replacement.public_id, JPEG + b"third")
        self.assertEqual(self.error(copy(), 404, "not_found"), reference)

    def test_another_subscriber_gets_404_and_no_file_is_read(self):
        self.verified(self.paid_item)
        ben = self.other_client()
        bens_site = self.create_site(client=ben, subdomain="bens-store")["id"]
        bens_base = f"/v1/sites/{bens_site}/billing/invoices/"
        self.scan_store.read = mock.Mock(side_effect=AssertionError("a file was read"))
        with mock.patch("apps.invoices.scans.read", side_effect=AssertionError("a file was read")):
            for item_id in (self.paid_item.public_id, "mbi_" + "0" * 26, "nonsense"):
                self.error(self.get(bens_base + f"{item_id}/copy/", client=ben), 404, "not_found")
            # Ana's item under Ben's site, and Ana's site under Ben's login: the same 404.
            self.error(self.get(self.mine + f"{self.paid_item.public_id}/copy/", client=ben), 404, "not_found")
            self.error(self.get(self.mine, client=ben), 404, "not_found")
        self.assertEqual(self.get(bens_base, client=ben).json()["items"], [])

    def test_signed_out_is_401(self):
        from django.test import Client
        for path in ("", f"{self.paid_item.public_id}/copy/"):
            self.assertEqual(self.get(self.mine + path, client=Client()).status_code, 401)

    def test_switched_off_is_404_and_no_invoice_table_is_read(self):
        self.verified(self.paid_item)
        with override_settings(BILLING_INVOICE_REGISTER_ENABLED=False):
            with CaptureQueriesContext(connection) as queries:
                self.error(self.get(self.mine), 404, "not_found")
                self.error(self.get(self.mine + f"{self.paid_item.public_id}/copy/"), 404, "not_found")
        self.assertEqual([q["sql"] for q in queries if "invoices_" in q["sql"]], [])

    def test_the_list_pages_newest_first_with_a_cursor_that_must_be_the_callers_own(self):
        first = self.get(self.mine + "?limit=1").json()
        self.assertEqual(len(first["items"]), 1)
        self.error(self.get(self.mine + "?cursor=mbi_" + "0" * 26), 400, "invalid_cursor")
        self.error(self.get(self.mine + "?cursor=junk"), 400, "invalid_cursor")

    def test_the_routes_are_in_the_api_description(self):
        paths = self.get("/v1/openapi.json").json()["paths"]
        prefix = "/v1/sites/{site_id}/billing/invoices/"
        self.assertIn("get", paths[prefix])
        self.assertIn("get", paths[prefix + "{invoice_id}/copy/"])
