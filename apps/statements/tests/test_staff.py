"""The issuer and book routes (Billing Build B5, PR 2). They are registered in the staff route
table by the HQ/SMS coordinator's lines in staff_views.py; until those land the tests register
them here, so the real gate (session, two steps, origin, method) is what answers."""
from unittest import mock

from django.test import override_settings

from apps.invoices.models import InvoiceBook
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff
from apps.statements import staff
from apps.statements.models import IssuerProfile

BASE = "/staff/v1/finance/"
ISSUER = {
    "legal_name": "Test Issuer", "trade_name": "", "tin": "000-000-000-000", "branch_code": "000",
    "registered_address": "Test address", "cor_number": "COR-TEST", "cor_date": "2026-01-01",
    "contact_email": "issuer@example.test",
}
BOOK = {
    "series": "A", "serial_from": 1, "serial_to": 50, "serial_width": 6,
    "atp_reference": "ATP-TEST", "atp_date": "2026-01-01", "printer_name": "Printer",
}
ISSUER_PATHS = ("issuer/get/", "issuer/set/", "issuer/verify/")
BOOK_PATHS = ("invoices/books/", "invoices/books/set/", "invoices/books/active/")


@on_staff
@override_settings(BILLING_ENABLED=True, BILLING_INVOICE_REGISTER_ENABLED=True)
class StaffTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(
            staff_views.ROUTES,
            {p: ("POST", staff_views._for_staff(v)) for p, v in staff.ROUTES.items()},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.operator = self.enrolled().user

    def post(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def verified_issuer(self):
        made = self.post("issuer/set/", ISSUER).json()
        self.post("issuer/verify/", {"issuer": made["issuer"], "note": "Matches the COR"})
        return made["issuer"]

    def audits(self, action):
        return list(OperatorAuditEntry.objects.filter(action=action))


class GateTests(StaffTests):
    def test_signed_out_wrong_origin_and_switch_off(self):
        for path in ISSUER_PATHS + BOOK_PATHS:
            self.assertError(self.post(path), 401, "unauthenticated")
        self.staff_sign_in()
        for path in ISSUER_PATHS + BOOK_PATHS:
            self.assertError(self.post(path, origin="https://evil.example"), 403, "origin_not_allowed")
        with override_settings(BILLING_INVOICE_REGISTER_ENABLED=False):
            for path in ISSUER_PATHS + BOOK_PATHS:
                self.assertError(self.post(path, ISSUER), 404, "not_found")
        with override_settings(BILLING_INVOICE_REGISTER_ENABLED=False, BILLING_STATEMENTS_ENABLED=True):
            self.assertEqual(self.post("issuer/get/").status_code, 200)  # the issuer is shared
            self.assertError(self.post("invoices/books/"), 404, "not_found")
        with override_settings(BILLING_ENABLED=False):
            self.assertError(self.post("issuer/get/"), 404, "not_found")
        self.assertFalse(IssuerProfile.objects.exists())

    def test_post_only_and_keys_do_not_collide(self):
        from apps.billing import staff as billing_staff
        self.assertFalse(set(staff.ROUTES) & set(billing_staff.ROUTES))
        self.staff_sign_in()
        self.assertError(self.call("GET", BASE + "issuer/get/"), 405, "method_not_allowed")


class IssuerTests(StaffTests):
    def test_set_makes_an_unverified_row_and_an_audit_that_holds_no_value(self):
        self.staff_sign_in()
        made = self.post("issuer/set/", ISSUER)
        self.assertEqual(made.status_code, 201)
        self.assertIsNone(made.json()["verified_at"])
        got = self.post("issuer/get/").json()
        self.assertIsNone(got["current"])
        self.assertEqual(got["pending"]["issuer"], made.json()["issuer"])
        entry = self.audits("billing_issuer_set")[0]
        self.assertEqual(entry.target_id, made.json()["issuer"])
        self.assertNotIn("Test Issuer", str(entry.detail) + entry.reason)
        self.assertEqual(entry.operator, self.operator)

    def test_mandatory_fields_and_bad_values_refused(self):
        self.staff_sign_in()
        for field in ("legal_name", "tin", "branch_code", "registered_address", "cor_number",
                      "cor_date", "contact_email"):
            body = {**ISSUER, field: ""}
            self.assertError(self.post("issuer/set/", body), 400, "invalid_request")
        self.assertError(self.post("issuer/set/", {**ISSUER, "cor_date": "soon"}), 400, "invalid_request")
        self.assertError(self.post("issuer/set/", {**ISSUER, "contact_email": "nope"}), 400, "invalid_request")
        self.assertError(self.post("issuer/set/", {**ISSUER, "tin": "1" * 18}), 400, "invalid_request")
        self.assertEqual(self.audits("billing_issuer_set"), [])

    def test_verify_once_and_the_newest_verified_is_current(self):
        self.staff_sign_in()
        first = self.verified_issuer()
        self.assertEqual(self.post("issuer/get/").json()["current"]["issuer"], first)
        again = self.post("issuer/verify/", {"issuer": first})
        self.assertError(again, 409, "already_verified")
        second = self.post("issuer/set/", {**ISSUER, "branch_code": "001"}).json()["issuer"]
        got = self.post("issuer/get/").json()
        self.assertEqual((got["current"]["issuer"], got["pending"]["issuer"]), (first, second))
        self.post("issuer/verify/", {"issuer": second})
        got = self.post("issuer/get/").json()
        self.assertEqual((got["current"]["issuer"], got["pending"]), (second, None))
        self.assertEqual(IssuerProfile.objects.count(), 2)  # nothing was edited
        self.assertEqual(len(self.audits("billing_issuer_verified")), 2)
        self.assertError(self.post("issuer/verify/", {"issuer": "iss_nope"}), 404, "not_found")

    def test_an_older_unverified_profile_cannot_be_verified_after_a_newer_one(self):
        self.staff_sign_in()
        old = self.post("issuer/set/", ISSUER).json()["issuer"]
        new = self.post("issuer/set/", {**ISSUER, "branch_code": "001"}).json()["issuer"]
        self.post("issuer/verify/", {"issuer": new})
        self.assertError(self.post("issuer/verify/", {"issuer": old}), 409, "superseded")
        self.assertEqual(self.post("issuer/get/").json()["current"]["issuer"], new)
        self.assertEqual(len(self.audits("billing_issuer_verified")), 1)
        self.assertIsNone(IssuerProfile.objects.get(public_id=old).verified_at)
        self.assertError(self.post("issuer/verify/", {}), 400, "invalid_request")


class BookTests(StaffTests):
    def test_a_book_needs_a_verified_issuer(self):
        self.staff_sign_in()
        self.assertError(self.post("invoices/books/set/", BOOK), 409, "issuer_not_verified")
        self.post("issuer/set/", ISSUER)  # recorded but not verified
        self.assertError(self.post("invoices/books/set/", BOOK), 409, "issuer_not_verified")
        self.assertFalse(InvoiceBook.objects.exists())

    def test_add_list_and_second_booklet_of_same_or_blank_series(self):
        self.staff_sign_in()
        issuer = self.verified_issuer()
        for series in ("A", ""):
            for lo, hi in ((1, 50), (51, 100)):
                made = self.post("invoices/books/set/", {**BOOK, "series": series, "serial_from": lo, "serial_to": hi})
                self.assertEqual(made.status_code, 201)
                self.assertEqual(made.json()["issuer"], issuer)
        items = self.post("invoices/books/").json()["items"]
        self.assertEqual([(i["series"], i["serial_from"]) for i in items],
                         [("", 1), ("", 51), ("A", 1), ("A", 51)])
        self.assertEqual(len(self.audits("billing_invoice_book_set")), 4)

    def test_overlap_and_bad_ranges_refused(self):
        self.staff_sign_in()
        self.verified_issuer()
        self.post("invoices/books/set/", BOOK)
        for lo, hi in ((50, 80), (10, 20), (1, 1)):
            self.assertError(
                self.post("invoices/books/set/", {**BOOK, "serial_from": lo, "serial_to": hi}),
                409, "serial_range_overlaps",
            )
        self.assertError(self.post("invoices/books/set/", {**BOOK, "serial_from": 9, "serial_to": 3}), 400, "invalid_request")
        self.assertError(self.post("invoices/books/set/", {**BOOK, "serial_width": 0}), 400, "invalid_request")
        self.assertError(self.post("invoices/books/set/", {**BOOK, "atp_reference": ""}), 400, "invalid_request")
        self.assertError(self.post("invoices/books/set/", {**BOOK, "serial_from": True}), 400, "invalid_request")
        self.assertEqual(InvoiceBook.objects.count(), 1)

    def test_only_active_moves_and_the_system_never_assigns_a_serial(self):
        self.staff_sign_in()
        self.verified_issuer()
        self.post("invoices/books/set/", BOOK)
        off = self.post("invoices/books/active/", {"series": "A", "serial_from": 1, "active": False})
        self.assertFalse(off.json()["active"])
        self.assertEqual(len(self.audits("billing_invoice_book_set")), 2)
        self.post("invoices/books/active/", {"series": "A", "serial_from": 1, "active": False})
        self.assertEqual(len(self.audits("billing_invoice_book_set")), 2)  # no change, no entry
        self.assertError(self.post("invoices/books/active/", {"series": "Q", "serial_from": 1, "active": True}), 404, "not_found")
        self.assertError(self.post("invoices/books/active/", {"series": "A", "serial_from": 1, "active": "yes"}), 400, "invalid_request")
        book = InvoiceBook.objects.get()
        self.assertEqual((book.serial_from, book.serial_to), (1, 50))
