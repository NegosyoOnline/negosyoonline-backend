"""The statement PDF and its store (Billing Build B5, PR 6): totals, labels, bytes, keys."""
import re
import tempfile
from unittest import mock

from django.core.checks import Error
from django.test import SimpleTestCase, TestCase, override_settings

from apps.billing import plan, services as billing
from apps.billing.tests.base import (
    CYCLE_1_END, days, make_active, make_operator, submit, transfers_on,
)
from apps.common import private_files
from apps.statements import checks, documents, issue, pdf
from apps.statements.models import LABEL, Statement
from apps.statements.tests.base import make_issuer

BANNED = ("BIR compliant", "PTI", "EIS", "ESP")
SHOWS = re.compile(rb"\((?P<text>(?:\\.|[^\\)])*)\) Tj")


def text_of(content):
    """The text drawn in an uncompressed PDF, one string per line, in drawing order."""
    rows = []
    for match in SHOWS.finditer(content):
        raw = re.sub(
            rb"\\([0-7]{3}|[()\\])",
            lambda m: bytes([int(m.group(1), 8)]) if len(m.group(1)) == 3 else m.group(1),
            match.group("text"),
        )
        rows.append(raw.decode("cp1252"))
    return rows


def document(**changes):
    values = {
        "number": "NOBS-2026-000001", "kind": "monthly", "is_test": False,
        "issuer": {
            "legal_name": "Issuer Legal Name", "trade_name": "Issuer Trade", "tin": "123-456-789",
            "branch_code": "000", "registered_address": "1 Issuer Street, Manila",
            "tax_type": "non_vat", "contact_email": "issuer@example.test",
        },
        "bill_to": {"name": "Store Owner", "email": "owner@example.test", "address": "Cebu"},
        "period_start": "2026-11-01T00:00:00+08:00", "period_end": "2026-11-30T00:00:00+08:00",
        "issued_at": "2026-11-25T02:00:00+00:00", "payment_reference": "btp_abc123",
        "total_centavos": 249900,
        "lines": [{
            "kind": "base", "description": "NegosyoOnline SaaS subscription - Monthly Plan",
            "quantity": 1, "unit_centavos": 249900, "amount_centavos": 249900,
        }],
    }
    values.update(changes)
    return values


def render_text(**changes):
    return text_of(pdf.render(document(**changes)))


class RenderCase(SimpleTestCase):
    def test_monthly_total_is_in_the_text_to_the_centavo(self):
        rows = render_text()
        self.assertEqual(rows.count("PHP 2,499.00"), 3)  # unit price, amount, total
        self.assertIn("Statement number: NOBS-2026-000001", rows)

    def test_promo_total(self):
        line = {"kind": "base", "description": "Launch Promo (6 Months)", "quantity": 1,
                "unit_centavos": 499900, "amount_centavos": 499900}
        rows = render_text(kind="launch_promo", total_centavos=499900, lines=[line])
        self.assertEqual(rows.count("PHP 4,999.00"), 3)

    def test_multi_line_total_is_the_sum(self):
        lines = [
            {"kind": "base", "description": "Base", "quantity": 1, "unit_centavos": 249900,
             "amount_centavos": 249900},
            {"kind": "usage", "description": "Storage overage - 3 GB", "quantity": 3,
             "unit_centavos": 5000, "amount_centavos": 15000},
        ]
        rows = render_text(lines=lines, total_centavos=264900)
        self.assertIn("PHP 150.00", rows)
        self.assertIn("PHP 2,649.00", rows)
        self.assertEqual(sum(line["amount_centavos"] for line in lines), 264900)

    def test_negative_adjustment_line(self):
        line = {"kind": "adjustment", "description": "Correction", "quantity": 1,
                "unit_centavos": -5000, "amount_centavos": -5000}
        self.assertIn("-PHP 50.00", render_text(kind="adjustment", total_centavos=-5000,
                                                 lines=[line]))

    def test_exact_label_and_no_test_band_when_live(self):
        content = pdf.render(document())
        joined = " ".join(text_of(content))
        self.assertIn("Internal Billing Statement — Not an Official BIR Receipt", joined)
        self.assertIn("or BIR Electronic Invoice.", joined)
        self.assertNotIn("TEST", joined)

    def test_label_is_the_stored_label_text(self):
        self.assertEqual(
            LABEL,
            "Internal Billing Statement — Not an Official BIR Receipt or BIR Electronic Invoice.",
        )

    def test_test_band_when_not_live(self):
        self.assertIn("TEST DOCUMENT - NOT A PAID CHARGE", render_text(is_test=True))

    def test_label_on_every_page(self):
        lines = [{"kind": "usage", "description": f"Line {n}", "quantity": 1,
                  "unit_centavos": 100, "amount_centavos": 100} for n in range(80)]
        content = pdf.render(document(lines=lines, total_centavos=8000, is_test=True))
        pages = content.count(b"/Type /Page\n")
        self.assertGreater(pages, 1)
        joined = text_of(content)
        self.assertEqual(sum(1 for r in joined if r.startswith("Internal Billing")), pages)
        self.assertEqual(joined.count("TEST DOCUMENT - NOT A PAID CHARGE"), pages)

    def test_banned_words_absent(self):
        for test in (False, True):
            joined = " ".join(render_text(is_test=test))
            for word in BANNED:
                self.assertNotIn(word, joined)

    def test_tin_of_bill_to_only_when_supplied(self):
        self.assertNotIn("TIN", " ".join(render_text()).split("Billed to")[1])
        bill_to = {"name": "N", "email": "e@example.test", "address": "A", "tin": "999-999"}
        self.assertIn("TIN 999-999", render_text(bill_to=bill_to))

    def test_byte_stable(self):
        self.assertEqual(pdf.render(document()), pdf.render(document()))

    def test_a_change_changes_the_bytes(self):
        self.assertNotEqual(pdf.render(document()), pdf.render(document(number="NOBS-2026-000002")))

    def test_parentheses_and_long_descriptions_survive(self):
        description = "Plan (monthly) \\ " + "word " * 60
        line = {"kind": "base", "description": description, "quantity": 1,
                "unit_centavos": 100, "amount_centavos": 100}
        rows = render_text(lines=[line], total_centavos=100)
        self.assertIn("Plan (monthly) \\ word word", " ".join(rows))

    def test_is_a_pdf(self):
        self.assertTrue(pdf.render(document()).startswith(b"%PDF-"))


def local_store():
    folder = tempfile.mkdtemp()
    return override_settings(
        PRIVATE_BLOB_READ_WRITE_TOKEN="", PRIVATE_FILES_DIR=folder, DEBUG=True
    )


class StoreCase(SimpleTestCase):
    def test_stored_under_a_random_key_and_hashed(self):
        with local_store():
            first = documents.store_document(document())
            second = documents.store_document(document())
            self.assertRegex(first.key, r"^statements/pdf/[0-9a-f]{32}\.pdf$")
            self.assertNotEqual(first.key, second.key)  # same bytes, new random key
            self.assertEqual(first.sha256, second.sha256)
            self.assertNotIn("NOBS", first.key)
            stored = private_files.read(first.key)
        self.assertEqual(first.size, len(stored))
        self.assertEqual(stored, pdf.render(document()))

    def test_no_url_is_exposed(self):
        self.assertEqual(set(documents.Stored._fields), {"key", "sha256", "size"})
        with local_store():
            key = documents.store_document(document()).key
        self.assertNotIn("http", key)

    def test_no_store_raises_and_stores_nothing(self):
        with override_settings(PRIVATE_BLOB_READ_WRITE_TOKEN="", DEBUG=False):
            with self.assertRaises(documents.StoreUnavailable):
                documents.store_document(document())

    def test_a_refusing_store_is_unavailable(self):
        with local_store(), mock.patch.object(
            private_files, "save", side_effect=private_files.StoreError("no")
        ):
            with self.assertRaises(documents.StoreUnavailable):
                documents.store_document(document())

    def test_read_pdf_checks_the_hash(self):
        with local_store():
            stored = documents.store_document(document())
            statement = mock.Mock(pdf_key=stored.key, pdf_sha256=stored.sha256)
            self.assertEqual(documents.read_pdf(statement), pdf.render(document()))
            statement.pdf_sha256 = "0" * 64
            with self.assertRaises(documents.StoreUnavailable):
                documents.read_pdf(statement)

    def test_read_pdf_of_a_missing_file_is_unavailable(self):
        with local_store():
            statement = mock.Mock(pdf_key="statements/pdf/" + "a" * 32 + ".pdf", pdf_sha256="x")
            with self.assertRaises(documents.StoreUnavailable):
                documents.read_pdf(statement)


class CheckCase(SimpleTestCase):
    def ids(self):
        return [e.id for e in checks.check_statements_store(None)]

    def test_switch_off_is_silent(self):
        with override_settings(BILLING_STATEMENTS_ENABLED=False, PRIVATE_BLOB_READ_WRITE_TOKEN=""):
            self.assertEqual(self.ids(), [])

    def test_on_without_billing_is_silent(self):
        with override_settings(BILLING_ENABLED=False, BILLING_STATEMENTS_ENABLED=True,
                               PRIVATE_BLOB_READ_WRITE_TOKEN="", DEBUG=False):
            self.assertEqual(self.ids(), [])

    def test_on_without_a_store_is_an_error(self):
        with override_settings(BILLING_ENABLED=True, BILLING_STATEMENTS_ENABLED=True,
                               PRIVATE_BLOB_READ_WRITE_TOKEN="", DEBUG=False):
            errors = checks.check_statements_store(None)
        self.assertEqual([e.id for e in errors], ["statements.E001"])
        self.assertIsInstance(errors[0], Error)

    def test_on_with_a_store_is_fine(self):
        with override_settings(BILLING_ENABLED=True, BILLING_STATEMENTS_ENABLED=True,
                               PRIVATE_BLOB_READ_WRITE_TOKEN="vercel_blob_rw_abc123_secret"):
            self.assertEqual(self.ids(), [])


ON = dict(BILLING_ENABLED=True, BILLING_STATEMENTS_ENABLED=True)
AT = CYCLE_1_END + days(1)


@override_settings(**ON)
class IssueWithTheRealStoreCase(TestCase):
    """The issue job with the real renderer and a local store: what is recorded is what is kept."""

    def setUp(self):
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        local = local_store()
        local.enable()
        self.addCleanup(local.disable)
        make_issuer(verified=True)
        self.operator = make_operator()

    def paid(self, **settings_):
        subscription = make_active()
        payment = submit(subscription, AT)
        return billing.confirm_transfer(
            payment, self.operator, amount_received_centavos=payment.amount_centavos, now=AT
        )

    def test_the_stored_pdf_matches_the_statement(self):
        with mock.patch.object(plan, "STORAGE_METERED", True):
            payment = self.paid()
            statement = issue.issue_for_payment(payment)
        content = documents.read_pdf(statement)
        self.assertEqual(len(content), statement.pdf_size)
        rows = text_of(content)
        self.assertIn(f"Statement number: {statement.number}", rows)
        self.assertEqual(rows.count(pdf.money(statement.total_centavos)), 3)
        self.assertIn("TEST DOCUMENT - NOT A PAID CHARGE", rows)  # BILLING_LIVE_DOCUMENTS unset
        self.assertTrue(statement.is_test)
        self.assertRegex(statement.pdf_key, r"^statements/pdf/[0-9a-f]{32}\.pdf$")

    def test_live_documents_carry_no_test_band(self):
        with mock.patch.object(plan, "STORAGE_METERED", True), override_settings(
            BILLING_LIVE_DOCUMENTS=True
        ):
            statement = issue.issue_for_payment(self.paid())
        self.assertFalse(statement.is_test)
        self.assertNotIn("TEST", " ".join(text_of(documents.read_pdf(statement))))

    def test_a_failed_save_discards_the_stored_file(self):
        with mock.patch.object(plan, "STORAGE_METERED", True):
            payment = self.paid()
            with mock.patch.object(issue, "_link_item", side_effect=RuntimeError("x")), \
                    mock.patch.object(documents, "discard") as discard:
                with self.assertRaises(RuntimeError):
                    issue.issue_for_payment(payment)
        discard.assert_called_once()
        self.assertEqual(Statement.objects.count(), 0)
