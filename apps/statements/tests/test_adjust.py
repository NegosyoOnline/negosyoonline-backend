"""Adjustments and the staff statement routes (Billing Build B5, PR 9; plan 7.3, 9.2, 10, 11)."""
import hashlib
import tempfile
import threading
from unittest import mock

from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings

from apps.billing import clock
from apps.common.errors import ApiError
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff
from apps.statements import adjust, documents, staff
from apps.statements.managers import Immutable
from apps.statements.models import LABEL, Statement, StatementNumberSeries
from apps.statements.tests.base import (
    WHEN, make_issuer, make_line, make_staff, make_statement, new_subscription,
)
from apps.statements.tests.test_pdf import BANNED, document, text_of

ON = dict(BILLING_ENABLED=True, BILLING_STATEMENTS_ENABLED=True)
BASE = "/staff/v1/finance/statements/"
PATHS = ("list/", "detail/", "pdf/", "adjust/")
LINE = {"description": "Refund of part of the term", "amount_centavos": -10000}


def fake_store(doc):
    return documents.Stored(key=f"pdf-{doc['number']}", sha256="a" * 64, size=100)


def stored_original(subscription=None, total=39900, **fields):
    """A statement whose PDF really is in the (local) store, as issue leaves it."""
    stored = documents.store_document(document(total_centavos=total))
    statement = make_statement(
        subscription, total_centavos=total, pdf_key=stored.key, pdf_sha256=stored.sha256,
        pdf_size=stored.size, issuer=document()["issuer"], bill_to=document()["bill_to"],
        **fields,
    )
    make_line(statement, unit_centavos=total, amount_centavos=total)
    return statement


def local_store():
    return override_settings(
        PRIVATE_BLOB_READ_WRITE_TOKEN="", PRIVATE_FILES_DIR=tempfile.mkdtemp(), DEBUG=True
    )


@override_settings(**ON)
class AdjustTests(TestCase):
    def setUp(self):
        patcher = mock.patch.object(documents, "store_document", side_effect=fake_store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.operator = make_staff()
        self.original = make_statement()

    def adjust(self, lines=None, reason="billing_error", original=None, **kwargs):
        return adjust.adjust(
            self.operator, (original or self.original).public_id, reason,
            lines or [LINE], **kwargs,
        )

    def test_makes_a_numbered_negative_document_of_its_own_and_leaves_the_original(self):
        before = Statement.objects.get(pk=self.original.pk)
        made = self.adjust([LINE, {"description": "Second", "amount_centavos": -500}])
        self.assertRegex(made.number, r"^NOCN-\d{4}-000001$")
        self.assertEqual(made.document_type, "adjustment")
        self.assertEqual(made.original, self.original)
        self.assertEqual(made.total_centavos, -10500)
        self.assertEqual(made.adjustment_reason, "billing_error")
        self.assertEqual(made.label, LABEL)
        self.assertEqual(
            [(line.position, line.kind, line.amount_centavos) for line in made.lines.all()],
            [(1, "adjustment", -10000), (2, "adjustment", -500)],
        )
        self.assertEqual(made.subscription, self.original.subscription)
        self.assertEqual(made.is_test, self.original.is_test)
        after = Statement.objects.get(pk=self.original.pk)
        self.assertEqual(
            [getattr(before, f.name) for f in Statement._meta.concrete_fields],
            [getattr(after, f.name) for f in Statement._meta.concrete_fields],
        )
        self.assertEqual(after.lines.count(), 0)

    def test_the_number_series_is_its_own_and_runs_on(self):
        first, second = self.adjust(), self.adjust()
        self.assertEqual(second.number.rsplit("-", 1)[1], "000002")
        self.assertEqual(StatementNumberSeries.objects.get(prefix="NOCN").last_value, 2)
        self.assertFalse(StatementNumberSeries.objects.filter(prefix="NOBS").exists())
        self.assertNotEqual(first.source_key, second.source_key)

    def test_the_year_is_the_manila_year_of_creation(self):
        from datetime import datetime, timezone
        late = datetime(2026, 12, 31, 17, 0, tzinfo=timezone.utc)  # 1 Jan 2027 in Manila
        self.assertEqual(self.adjust(now=late).number, "NOCN-2027-000001")
        self.assertEqual(late.astimezone(clock.MANILA).year, 2027)

    def test_the_total_may_reach_the_remainder_but_not_pass_it(self):
        self.adjust([{"description": "a", "amount_centavos": -30000}])
        self.assertEqual(adjust.remaining(self.original), 9900)
        with self.assertRaises(ApiError) as caught:
            self.adjust([{"description": "b", "amount_centavos": -9901}])
        self.assertEqual((caught.exception.code, caught.exception.status), ("exceeds_remaining", 409))
        self.adjust([{"description": "b", "amount_centavos": -9900}])  # exactly the rest
        self.assertEqual(adjust.remaining(self.original), 0)
        with self.assertRaises(ApiError):
            self.adjust([{"description": "c", "amount_centavos": -1}])
        self.assertEqual(Statement.objects.filter(document_type="adjustment").count(), 2)

    def test_one_line_over_the_original_is_refused_and_takes_no_number(self):
        with self.assertRaises(ApiError):
            self.adjust([{"description": "a", "amount_centavos": -39901}])
        self.assertFalse(StatementNumberSeries.objects.filter(prefix="NOCN").exists())

    def test_bad_input_is_refused_and_writes_nothing(self):
        bad = (
            ("nope", [LINE]), ("billing_error", []), ("billing_error", "x"),
            ("billing_error", [{"description": "a", "amount_centavos": 100}]),
            ("billing_error", [{"description": "a", "amount_centavos": 0}]),
            ("billing_error", [{"description": "a", "amount_centavos": -1.5}]),
            ("billing_error", [{"description": "a", "amount_centavos": True}]),
            ("billing_error", [{"description": " ", "amount_centavos": -1}]),
            ("billing_error", [{"description": "x" * 301, "amount_centavos": -1}]),
            ("billing_error", [LINE] * 11), ("billing_error", ["x"]),
        )
        for reason, lines in bad:
            with self.assertRaises(ApiError) as caught:
                adjust.adjust(self.operator, self.original.public_id, reason, lines)
            self.assertEqual(caught.exception.code, "invalid_request")
        self.assertFalse(Statement.objects.filter(document_type="adjustment").exists())
        self.assertFalse(OperatorAuditEntry.objects.filter(action="billing_statement_adjusted"))

    def test_unknown_and_malformed_originals_are_not_found(self):
        for value in ("stm_" + "0" * 20, "nope", None, 5):
            with self.assertRaises(ApiError) as caught:
                adjust.adjust(self.operator, value, "other", [LINE])
            self.assertEqual(caught.exception.status, 404)

    def test_an_adjustment_cannot_be_adjusted(self):
        made = self.adjust()
        with self.assertRaises(ApiError) as caught:
            self.adjust(original=made)
        self.assertEqual(caught.exception.code, "not_adjustable")

    def test_another_subscribers_statement_is_not_touched(self):
        other = make_statement(new_subscription())
        self.adjust([{"description": "a", "amount_centavos": -39900}])
        self.assertEqual(adjust.remaining(other), 39900)
        self.adjust([{"description": "a", "amount_centavos": -39900}], original=other)
        self.assertEqual(adjust.remaining(self.original), 0)
        self.assertEqual(adjust.remaining(other), 0)

    def test_switch_off_writes_nothing(self):
        for off in (dict(BILLING_STATEMENTS_ENABLED=False), dict(BILLING_ENABLED=False)):
            with override_settings(**off), self.assertRaises(ApiError) as caught:
                self.adjust()
            self.assertEqual(caught.exception.status, 404)
        self.assertFalse(Statement.objects.filter(document_type="adjustment").exists())

    def test_an_unavailable_store_writes_nothing_and_takes_no_number(self):
        with mock.patch.object(documents, "store_document", side_effect=documents.StoreUnavailable):
            with self.assertRaises(ApiError) as caught:
                self.adjust()
        self.assertEqual((caught.exception.code, caught.exception.status), ("store_unavailable", 503))
        self.assertFalse(StatementNumberSeries.objects.filter(prefix="NOCN").exists())

    def test_a_failed_write_discards_the_stored_file(self):
        with mock.patch.object(documents, "discard") as discard, mock.patch(
            "apps.statements.adjust.audit.record", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                self.adjust()
        discard.assert_called_once_with(f"pdf-NOCN-{WHEN.year}-000001")
        self.assertFalse(Statement.objects.filter(document_type="adjustment").exists())

    def test_the_audit_holds_ids_numbers_and_amounts_only(self):
        made = self.adjust()
        entry = OperatorAuditEntry.objects.get(action="billing_statement_adjusted")
        self.assertEqual(entry.operator, self.operator)
        self.assertEqual((entry.target_type, entry.target_id), ("statement", made.public_id))
        self.assertEqual(entry.detail, {
            "number": made.number, "original": self.original.public_id,
            "original_number": self.original.number, "total_centavos": -10000,
        })
        self.assertEqual(entry.reason, "billing_error")
        self.assertEqual(entry.personal, {})

    def test_the_original_and_its_lines_stay_final(self):
        made = self.adjust()
        with self.assertRaises(Immutable):
            made.save()
        with self.assertRaises(Immutable):
            Statement.objects.filter(pk=self.original.pk).update(total_centavos=1)


@override_settings(**ON)
class AdjustmentPdfTests(TestCase):
    def setUp(self):
        local = local_store()
        local.enable()
        self.addCleanup(local.disable)
        self.original = stored_original()

    def test_the_pdf_is_rendered_and_stored_at_creation_with_the_exact_label(self):
        made = adjust.adjust(make_staff(), self.original.public_id, "goodwill", [LINE])
        content = documents.read_pdf(made)  # checks the recorded hash
        self.assertEqual(hashlib.sha256(content).hexdigest(), made.pdf_sha256)
        self.assertEqual(len(content), made.pdf_size)
        self.assertNotEqual(made.pdf_key, self.original.pdf_key)
        rows = text_of(content)
        joined = " ".join(rows)
        self.assertIn(LABEL, joined)
        self.assertIn(made.number, joined)
        self.assertIn(f"Adjusts statement: {self.original.number}", rows)
        self.assertIn("Reason: Goodwill reduction", rows)
        self.assertIn("-PHP 100.00", rows)
        self.assertIn("TEST DOCUMENT - NOT A PAID CHARGE", joined)
        for banned in BANNED:
            self.assertNotIn(banned, joined)
        self.assertNotIn("receipt", joined.replace(LABEL, "").lower())

    def test_the_original_pdf_is_unchanged(self):
        adjust.adjust(make_staff(), self.original.public_id, "goodwill", [LINE])
        self.assertEqual(documents.read_pdf(Statement.objects.get(pk=self.original.pk)),
                         documents.read_pdf(self.original))


@override_settings(**ON)
class ConcurrentAdjustTests(TransactionTestCase):
    """Two adjustments that cannot both fit: exactly one is made. Needs row locks."""

    def test_two_at_once_cannot_pass_the_remainder(self):
        if connection.vendor != "postgresql":
            self.skipTest("needs PostgreSQL row locks")
        operator = make_staff()
        original = make_statement()
        outcomes, barrier = [], threading.Barrier(2)

        def run():
            try:
                barrier.wait()
                adjust.adjust(
                    operator, original.public_id, "other",
                    [{"description": "a", "amount_centavos": -30000}],
                )
                outcomes.append("made")
            except ApiError as refusal:
                outcomes.append(refusal.code)
            except Exception as error:  # noqa: BLE001
                outcomes.append(repr(error))
            finally:
                connections.close_all()

        with mock.patch.object(documents, "store_document", side_effect=fake_store):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(sorted(outcomes), ["exceeds_remaining", "made"])
        self.assertEqual(Statement.objects.filter(document_type="adjustment").count(), 1)
        self.assertEqual(adjust.remaining(original), 9900)
        self.assertEqual(StatementNumberSeries.objects.get(prefix="NOCN").last_value, 1)


@on_staff
@override_settings(**ON)
class StaffStatementRouteTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.operator = self.enrolled().user
        patcher = mock.patch.object(documents, "store_document", side_effect=fake_store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def body(self, statement, **extra):
        return {"statement": statement.public_id, **extra}

    def test_signed_out_wrong_origin_get_and_switch_off(self):
        for path in PATHS:
            self.assertError(self.post(path), 401, "unauthenticated")
        self.staff_sign_in()
        for path in PATHS:
            self.assertError(self.post(path, origin="https://evil.example"), 403, "origin_not_allowed")
            self.assertError(self.call("GET", BASE + path), 405, "method_not_allowed")
        statement = make_statement()
        for off in (dict(BILLING_STATEMENTS_ENABLED=False), dict(BILLING_ENABLED=False)):
            with override_settings(**off):
                for path in PATHS:
                    self.assertError(self.post(path, self.body(statement)), 404, "not_found")
        # the register switch alone does not open the statement routes
        with override_settings(BILLING_STATEMENTS_ENABLED=False, BILLING_INVOICE_REGISTER_ENABLED=True):
            self.assertError(self.post("list/"), 404, "not_found")
        self.assertFalse(Statement.objects.filter(document_type="adjustment").exists())
        self.assertFalse(OperatorAuditEntry.objects.filter(action__startswith="billing_statement"))

    def test_no_key_collides_with_an_existing_staff_key(self):
        from apps.billing import staff as billing_staff
        from apps.inbox import staff as inbox_staff
        from apps.invoices import staff as invoices_staff
        from apps.operators import staff_views
        keys = [f"finance/statements/{path}" for path in (*PATHS, "resend/")]
        self.assertTrue(set(keys) <= set(staff.ROUTES))
        self.assertIn("finance/statements/resend/", staff.ROUTES)  # PR 8
        for other in (billing_staff.ROUTES, inbox_staff.ROUTES, invoices_staff.ROUTES):
            self.assertFalse(set(keys) & set(other))
        for key in keys:
            self.assertEqual(staff_views.ROUTES[key][0], "POST")

    def test_list_pages_and_filters_by_site_and_type(self):
        subscription = new_subscription()
        first = make_statement(subscription)
        second = make_statement(subscription)
        other = make_statement(new_subscription())
        self.staff_sign_in()
        adjusted = adjust.adjust(self.operator, first.public_id, "other", [LINE])
        everything = self.post("list/").json()
        self.assertEqual(len(everything["items"]), 4)
        site = first.subscription.site.public_id
        mine = self.post("list/", {"site": site}).json()["items"]
        self.assertEqual({i["number"] for i in mine}, {first.number, second.number, adjusted.number})
        self.assertNotIn(other.number, {i["number"] for i in mine})
        only = self.post("list/", {"document_type": "adjustment"}).json()["items"]
        self.assertEqual([i["number"] for i in only], [adjusted.number])
        self.assertEqual(only[0]["original_number"], first.number)
        paged = self.post("list/", {"limit": 2}).json()
        self.assertEqual(len(paged["items"]), 2)
        rest = self.post("list/", {"limit": 2, "cursor": paged["next_cursor"]}).json()
        self.assertEqual(len(rest["items"]), 2)
        self.assertIsNone(rest["next_cursor"])
        self.assertError(self.post("list/", {"cursor": "stm_nope"}), 400, "invalid_cursor")
        self.assertError(self.post("list/", {"document_type": "x"}), 400, "invalid_request")
        self.assertError(self.post("list/", {"limit": "x"}), 400, "invalid_request")

    def test_what_list_and_detail_show_never_includes_the_file(self):
        statement = make_statement()
        make_line(statement)
        self.staff_sign_in()
        listed = self.post("list/").json()["items"][0]
        detail = self.post("detail/", self.body(statement)).json()
        for shown in (listed, detail):
            self.assertNotIn("pdf_key", shown)
            self.assertNotIn("pdf_sha256", shown)
            self.assertNotIn("bill_to", shown)
            self.assertNotIn("issuer", shown)
        self.assertEqual(detail["lines"][0]["amount_centavos"], 39900)
        self.assertEqual(detail["remaining_centavos"], 39900)
        self.assertEqual(detail["adjustments"], [])
        self.assertIn("billing_error", detail["reasons"])

    def test_detail_after_adjusting_and_unknown_ids(self):
        statement = make_statement()
        self.staff_sign_in()
        made = self.post("adjust/", self.body(statement, reason="other", lines=[LINE]))
        self.assertEqual(made.status_code, 201)
        detail = self.post("detail/", self.body(statement)).json()
        self.assertEqual(detail["remaining_centavos"], 29900)
        self.assertEqual([a["number"] for a in detail["adjustments"]], [made.json()["number"]])
        adjustment = self.post("detail/", {"statement": made.json()["statement"]}).json()
        self.assertEqual(adjustment["original"], statement.public_id)
        self.assertIsNone(adjustment["remaining_centavos"])
        for call in ("detail/", "pdf/", "adjust/"):
            extra = {"reason": "other", "lines": [LINE]} if call == "adjust/" else {}
            for unknown in ("stm_" + "0" * 20, "junk"):
                self.assertError(self.post(call, {"statement": unknown, **extra}), 404, "not_found")
        for call in ("detail/", "pdf/"):
            self.assertError(self.post(call, {}), 400, "invalid_request")

    def test_adjust_over_the_remainder_is_409_and_bad_values_400(self):
        statement = make_statement()
        self.staff_sign_in()
        self.assertError(
            self.post("adjust/", self.body(statement, reason="other", lines=[
                {"description": "x", "amount_centavos": -39901}])), 409, "exceeds_remaining")
        self.assertError(
            self.post("adjust/", self.body(statement, reason="bad", lines=[LINE])), 400,
            "invalid_request")
        self.assertFalse(Statement.objects.filter(document_type="adjustment").exists())
        self.assertFalse(OperatorAuditEntry.objects.filter(action="billing_statement_adjusted"))

    def test_the_audit_of_the_route_names_the_operator(self):
        statement = make_statement()
        self.staff_sign_in()
        self.post("adjust/", self.body(statement, reason="other", lines=[LINE]))
        entry = OperatorAuditEntry.objects.get(action="billing_statement_adjusted")
        self.assertEqual(entry.operator, self.operator)

    def test_a_site_cannot_be_reached_through_another_sites_statement(self):
        mine, theirs = make_statement(), make_statement(new_subscription())
        self.staff_sign_in()
        self.post("adjust/", self.body(mine, reason="other", lines=[
            {"description": "all", "amount_centavos": -39900}]))
        self.assertEqual(self.post("detail/", self.body(theirs)).json()["remaining_centavos"], 39900)
        self.assertEqual(Statement.objects.filter(original=theirs).count(), 0)


@on_staff
@override_settings(**ON)
class StaffPdfRouteTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.operator = self.enrolled().user
        local = local_store()
        local.enable()
        self.addCleanup(local.disable)

    def test_the_file_comes_back_in_the_answer_only_and_each_read_is_audited(self):
        statement = stored_original()
        self.staff_sign_in()
        response = self.call("POST", BASE + "pdf/", {"statement": statement.public_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), statement.pdf_sha256)
        self.call("POST", BASE + "pdf/", {"statement": statement.public_id})
        entries = OperatorAuditEntry.objects.filter(action="billing_statement_viewed")
        self.assertEqual(entries.count(), 2)
        self.assertEqual(entries[0].detail, {"number": statement.number})
        self.assertEqual(entries[0].target_id, statement.public_id)

    def test_a_missing_or_changed_file_is_503_and_not_audited(self):
        statement = make_statement()  # pdf_key "k": nothing stored
        self.staff_sign_in()
        response = self.call("POST", BASE + "pdf/", {"statement": statement.public_id})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "store_unavailable")
        self.assertFalse(OperatorAuditEntry.objects.filter(action="billing_statement_viewed"))
