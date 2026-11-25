"""P5 launch acceptance (ClickUp z8uq9m8h49): the Billing and statements system end to end on a
synthetic fixture. Plan: docs/superpowers/plans/2026-11-13-p5-launch-acceptance-z8uq9m8h49.md.

This module drives the real services, tasks and routes. It adds no rule and no calculation:
every expected figure is read from a stored row or from a constant in `plan.py`. Two synthetic
subscribers, made-up issuer and receiving-account values, an in-memory private store, no staging
data, no real merchant and no outbound network.

Scenarios 2, 3 and 4 and the order lines of 11 are not here: with BILLING_ORDER_OVERAGE_ENABLED
off, which is the default, NegosyoOnline charges no order (Coach Ed, 2026-11-11). From 2026-11-23
it bills only the orders over the 500 a month includes, at PHP 5 (OV-9), behind that switch, and
never a share of sales. `OrdersAreNeverBilledTests` guards what stays out. Lines
that need the Xendit gateway, the automatic restore or the resolution tool are blocked and are
listed in the plan, section 4.
"""
import hashlib
import json
import re
import socket
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from apps.accounts.tests.base import ORIGIN
from apps.billing import notices, plan, services, usage
from apps.billing.models import (
    BankTransferPayment, BillingCycle, ChargeLine, Subscription, UsageEntry, UsageEvent,
)
from apps.billing.tests.base import (
    CYCLE_1_END, CYCLE_2_END, JPEG, PNG, SECOND, T0, TRIAL_END, days, fresh, make_active, make_operator,
    make_subscription, make_suspended, submit, transfers_on,
)
from apps.common import private_files
from apps.common.errors import ApiError
from apps.accounts.services import SUPPORT_EMAIL
from apps.invoices import services as invoices
from apps.lifecycle import workflow
from apps.lifecycle.tests import run_base
from apps.invoices.models import InvoiceScan, ManualInvoice, ManualInvoiceItem
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.test_concurrency import run_together
from apps.statements import adjust, documents, issue, pdf
from apps.statements.managers import Immutable
from apps.statements.models import (
    LABEL, BillingProfile, IssuerProfile, Statement, StatementLine, StatementNumberSeries,
)
from apps.payments.models import SitePaymentAccount
from apps.statements.tests.base import (
    make_book, make_invoice, make_issuer, make_item, make_line, make_staff, make_statement,
)
from apps.statements.tests.test_pdf import text_of

GB = plan.GB_BYTES
HOUR = timedelta(hours=1)
RENEWAL_AT = CYCLE_1_END - days(2)  # a renewal proof confirmed two days before the cycle ends
AFTER_CYCLE_1 = CYCLE_1_END + days(1)  # the period has ended, so its overage is due
STATE = Subscription.State
ON = dict(
    BILLING_ENABLED=True, BILLING_BANK_TRANSFER_ENABLED=True, BILLING_STATEMENTS_ENABLED=True,
    BILLING_INVOICE_REGISTER_ENABLED=True, OWNER_NOTICES_ENABLED=True,
    BILLING_USAGE_ENABLED=False, BILLING_LIVE_DOCUMENTS=False,
)
# What no generated output may claim (plan, scenario 10). The exact label is removed first.
CLAIMS = re.compile(
    r"\bPTI\b|\bEIS\b|\bESP\b|BIR[ -]compliant|BIR[ -]authori[sz]ed|BIR transmission|"
    r"official receipt|tax invoice|accredit|certif|BIR[ -]registered",
    re.IGNORECASE,
)
COPY_LABEL = "Copy of manually issued BIR-registered Non-VAT Invoice"  # allowed on the copy route only


class MemoryStore:
    """The private store, in memory: statement PDFs and invoice scans really are written and
    read back through the same `private_files` functions production uses."""

    def __init__(self):
        self.files = {}

    def save(self, folder, content, content_type):
        key = private_files.new_key(folder, content_type)
        self.files[key] = bytes(content)
        return key

    def read(self, key):
        return self.files[key]

    def delete(self, key):
        self.files.pop(key, None)


def _local_only(real):
    """Connections to this machine (PostgreSQL, Redis) are allowed; any other is a failure."""
    def connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in ("127.0.0.1", "::1", "localhost") and not host.startswith("/"):
            raise AssertionError(f"An outbound connection to {host!r} was attempted.")
        return real(self, address, *args, **kwargs)
    return connect


class Fixture:
    """The synthetic billing fixture every class shares (plan, section 3)."""

    def setUp(self):
        super().setUp()
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        self.store = MemoryStore()
        for name, target in (
            ("available", mock.Mock(return_value=True)), ("save", self.store.save),
            ("read", self.store.read), ("delete", self.store.delete),
            ("discard", self.store.delete),
        ):
            patcher = mock.patch.object(private_files, name, target)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            socket.socket, "connect", _local_only(socket.socket.connect)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.operator = make_operator("acceptance-op")
        self.finance = make_staff("finance")
        self.issuer = make_issuer(verified=True)
        self.book = make_book(self.issuer, series="A")

    # payments

    def confirm(self, payment, at):
        return services.confirm_transfer(
            payment, self.operator, amount_received_centavos=payment.amount_centavos, now=at
        )

    def pay(self, subscription, at, plan_code="monthly"):
        """The owner sends a proof at `at`; the operator confirms it an hour later."""
        payment = submit(fresh(subscription), at, plan_code=plan_code)
        return self.confirm(payment, at + HOUR)

    def paid_statement(self, subscription, at):
        payment = self.pay(subscription, at)
        return payment, issue.issue_for_payment(payment, now=at + 2 * HOUR)

    # the register

    def record(self, statement, buyer="Synthetic Buyer"):
        """The operator records the handwritten invoice of a statement's sale."""
        item = ManualInvoiceItem.objects.get(statement=statement)
        serial = self.book.invoices.count() + 1
        invoice = invoices.record_invoice(self.finance, {
            "item": item.public_id, "series": self.book.series, "serial_from": self.book.serial_from,
            "serial_number": serial, "issued_on": item.sale_date.isoformat(), "sale_type": "cash",
            "buyer_name": buyer, "description": "Written on paper",
            "service_start": item.period_start.date().isoformat(),
            "service_end": item.period_end.date().isoformat(),
            "total_sales_centavos": item.total_centavos,
        })
        return item, invoice

    def register(self, statement, buyer="Synthetic Buyer"):
        """Record the handwritten invoice, upload its scan and verify it. Returns the rows."""
        item, invoice = self.record(statement, buyer)
        content = PNG + f"scan-{statement.pk}".encode()
        scan = invoices.upload_scan(self.finance, invoice.public_id, content)
        invoices.verify_scan(self.finance, scan.public_id)
        return item, invoice, InvoiceScan.objects.get(pk=scan.pk), content


def reconcile(case, subscription):
    """Source rows = frozen statement lines = statement totals = register items = PDF facts
    (plan, section 6). It reads stored values only and prices nothing."""
    statements = list(Statement.objects.filter(subscription=subscription, document_type="statement"))
    for payment in BankTransferPayment.objects.filter(subscription=subscription, status="confirmed"):
        case.assertEqual(payment.amount_received_centavos, payment.amount_centavos)
        lines = ChargeLine.objects.filter(settled_payment=payment)
        case.assertEqual(sum(c.amount_centavos for c in lines), payment.usage_centavos)
        case.assertFalse(lines.exclude(status="settled").exists())
        statement = Statement.objects.get(source_key=issue.source_key(payment))
        stored = list(statement.lines.order_by("position"))
        case.assertEqual(sum(line.amount_centavos for line in stored), statement.total_centavos)
        case.assertEqual(statement.total_centavos, payment.amount_received_centavos)
        base = [line for line in stored if line.kind == "base"]
        if payment.plan_code != plan.USAGE_ONLY:
            case.assertEqual([line.amount_centavos for line in base], [payment.amount_centavos - payment.usage_centavos])
        refs = sorted(line.source_ref for line in stored if line.kind == "usage")
        case.assertEqual(refs, sorted(f"charge:{c.pk}" for c in lines))
        for line in (line for line in stored if line.kind == "usage"):
            charge = ChargeLine.objects.get(pk=int(line.source_ref.split(":")[1]))
            case.assertEqual(line.amount_centavos, charge.amount_centavos)
        item = ManualInvoiceItem.objects.filter(source_ref=payment.public_id).first()
        if item is not None:
            case.assertEqual(item.total_centavos, statement.total_centavos)
            case.assertEqual(sum(row["amount_centavos"] for row in item.lines), item.total_centavos)
            case.assertEqual(item.statement_id, statement.pk)
        pdf_facts(case, statement)
        case.assertIn(statement, statements)
    for corrections in Statement.objects.filter(subscription=subscription, document_type="adjustment"):
        original = corrections.original
        case.assertEqual(
            adjust.remaining(original),
            original.total_centavos + sum(
                c.total_centavos for c in Statement.objects.filter(original=original, document_type="adjustment")
            ),
        )
        pdf_facts(case, corrections)


def pdf_facts(case, statement):
    """The stored PDF is the one issued and says what the stored lines say."""
    content = documents.read_pdf(statement)
    case.assertEqual(hashlib.sha256(content).hexdigest(), statement.pdf_sha256)
    case.assertEqual(len(content), statement.pdf_size)
    rows = text_of(content)
    flat = " ".join(rows)
    case.assertIn(f"Statement number: {statement.number}", rows)
    case.assertIn(LABEL, flat)
    case.assertIn(pdf.money(statement.total_centavos), rows)
    for line in statement.lines.all():
        case.assertIn(line.description, flat)
        case.assertIn(pdf.money(line.amount_centavos), rows)
    return rows


# ---------------------------------------------------------------- scenario 1


@override_settings(**ON)
class TrialBoundaryTests(Fixture, TestCase):
    def test_a_trial_in_progress_has_no_statement_and_no_charge(self):
        subscription = make_subscription()
        view = services.overview(subscription.site, at=T0 + days(13))
        self.assertEqual(view["state"], STATE.TRIAL_ACTIVE)
        self.assertTrue(view["storefront_served"])
        self.assertEqual(view["outstanding_centavos"], 0)
        self.assertEqual(view["current_cycle"]["settlement"], BillingCycle.Settlement.NOT_BILLABLE)
        self.assertEqual(Statement.objects.count(), 0)
        self.assertEqual(ManualInvoiceItem.objects.count(), 0)
        self.assertEqual(BankTransferPayment.objects.count(), 0)

    def test_day_14_without_a_payment_ends_the_trial_with_no_charge_or_statement(self):
        subscription = make_subscription()
        self.assertEqual(services.sweep(now=TRIAL_END), {"trial_ended_no_method": 1})
        subscription = fresh(subscription)
        self.assertEqual(subscription.state, STATE.CANCELLED_NO_PAYMENT_METHOD)
        self.assertEqual(subscription.service_ended_at, TRIAL_END)
        self.assertEqual(subscription.download_until, TRIAL_END + days(30))
        # L75: a site that never paid is due for deletion 30 days after service end (the task text
        # says 90; the ruled rule in code and in the scope document is 30, day 44 of the trial).
        self.assertEqual(subscription.deletion_due_at, TRIAL_END + days(30))
        self.assertEqual(subscription.deletion_due_at, T0 + days(44))
        view = services.overview(subscription.site, at=TRIAL_END + HOUR)
        self.assertTrue(view["billing_only"])
        self.assertFalse(view["storefront_served"])
        self.assertEqual(view["outstanding_centavos"], 0)
        self.assertEqual(subscription.cycles.filter(settlement="paid").count(), 0)
        self.assertEqual(
            list(subscription.transitions.order_by("pk").values_list("code", flat=True)),
            ["trial_started", "trial_ended_no_method"],
        )
        self.assertFalse(services.deletion_reached(subscription.site, TRIAL_END + days(29)))
        self.assertTrue(services.deletion_reached(subscription.site, TRIAL_END + days(30)))
        for model in (BankTransferPayment, Statement, ManualInvoiceItem, ChargeLine):
            self.assertEqual(model.objects.count(), 0, model.__name__)

    def test_a_proof_under_review_at_day_14_keeps_the_trial_waiting(self):
        subscription = make_subscription()
        payment = submit(subscription, TRIAL_END - days(1))
        self.assertEqual(services.sweep(now=TRIAL_END), {})  # waiting for the operator
        self.assertEqual(fresh(subscription).state, STATE.TRIAL_ACTIVE)
        self.confirm(payment, TRIAL_END + days(2))
        subscription = fresh(subscription)
        self.assertEqual(subscription.state, STATE.ACTIVE)
        cycle = subscription.cycles.get(sequence=1)
        self.assertEqual((cycle.start_at, cycle.end_at), (TRIAL_END, CYCLE_1_END))
        self.assertEqual(cycle.settlement, BillingCycle.Settlement.PAID)

    def test_a_proof_left_undecided_for_7_days_suspends_and_stays_for_the_operator(self):
        subscription = make_subscription()
        payment = submit(subscription, TRIAL_END - days(1))
        self.assertEqual(services.sweep(now=TRIAL_END + days(7)), {"suspended_no_outcome": 1})
        subscription = fresh(subscription)
        self.assertEqual(subscription.state, STATE.SUSPENDED)
        self.assertTrue(services.overview(subscription.site, at=TRIAL_END + days(7))["billing_only"])
        self.assertEqual(BankTransferPayment.objects.get(pk=payment.pk).status, "submitted")
        # The held proof is still decided by the operator; confirming it brings the site back.
        confirmed = self.confirm(payment, TRIAL_END + days(8))
        self.assertEqual(confirmed.status, "confirmed")
        subscription = fresh(subscription)
        self.assertEqual(subscription.state, STATE.ACTIVE)
        self.assertEqual(
            subscription.cycles.order_by("-sequence").first().start_at, TRIAL_END + days(8)
        )

    def test_after_day_14_a_payment_before_day_44_restores_service_and_from_day_44_is_refused(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END)
        at = TRIAL_END + days(29)
        payment = self.pay(subscription, at)
        subscription = fresh(subscription)
        self.assertEqual(subscription.state, STATE.ACTIVE)
        latest = subscription.cycles.order_by("-sequence").first()
        self.assertEqual((latest.settlement, latest.start_at), ("paid", at + HOUR))
        self.assertEqual(payment.cycle_id, latest.pk)
        late = make_subscription("late@example.test", "late-store")
        services.sweep(now=TRIAL_END)
        with self.assertRaises(ApiError) as raised:
            submit(fresh(late), TRIAL_END + days(30))
        self.assertEqual(raised.exception.code, "past_deletion_date")


# ---------------------------------------------------------------- scenarios 2, 3, 4 (guard)


@override_settings(**ON)
class OrdersAreNeverBilledTests(Fixture, TestCase):
    """Scenarios 2, 3 and 4 are dropped: no per-order charge, only the overage over the 500
    included, and none of it while BILLING_ORDER_OVERAGE_ENABLED is off (the default)."""

    ORDER_NAMES = {"ORDERS_INCLUDED_PER_MONTH", "ORDER_OVERAGE_CENTAVOS_PER_ORDER"}

    def test_nothing_in_the_billing_tables_can_hold_a_per_order_charge(self):
        self.assertEqual(UsageEntry.Kind.values, ["storage", "orders"])
        self.assertEqual(ChargeLine.Kind.values, ["overage_storage", "overage_orders"])
        self.assertEqual(
            sorted(StatementLine.Kind.values), ["adjustment", "base", "credit", "usage"]
        )
        for model in (UsageEntry, ChargeLine, UsageEvent, StatementLine):
            names = " ".join(f.name for f in model._meta.get_fields())
            self.assertNotRegex(names, r"order|ordinal")
        for name in dir(plan):
            if name not in self.ORDER_NAMES:  # exactly these two, OV-1 and OV-2
                self.assertNotRegex(name, r"ORDER|OVERAGE_ORDER|PER_ORDER")
        self.assertEqual((plan.ORDERS_INCLUDED_PER_MONTH, plan.ORDER_OVERAGE_CENTAVOS_PER_ORDER), (500, 500))

    def test_orders_on_the_site_change_no_billing_row(self):
        from apps.shop.tests.owner_base import make_order

        subscription = make_active()
        payment, statement = self.paid_statement(subscription, RENEWAL_AT)
        before = self.counts()
        for status in ("verified", "cancelled", "pending_review"):
            make_order(subscription.site, status=status)
        self.assertEqual(self.counts(), before)
        item = ManualInvoiceItem.objects.get(statement=statement)
        for text in [line.description for line in statement.lines.all()] + [
            row["description"] for row in item.lines
        ]:
            self.assertNotRegex(text, r"(?i)order|product sale")

    @staticmethod
    def counts():
        return tuple(
            model.objects.count() for model in (
                UsageEntry, ChargeLine, UsageEvent, Statement, StatementLine, ManualInvoiceItem,
                BankTransferPayment, BillingCycle,
            )
        )


# ---------------------------------------------------------------- scenario 5


@override_settings(**ON)
class StorageAtLaunchTests(Fixture, TestCase):
    def test_storage_is_not_metered_so_a_snapshot_is_not_charged(self):
        self.assertFalse(plan.STORAGE_METERED)
        subscription = make_active()
        for switch in (True, False):
            with override_settings(BILLING_USAGE_ENABLED=switch):
                result = usage.record_storage_snapshot(
                    subscription.site, source_reference=f"s{switch}", period_end_at=CYCLE_1_END,
                    measured_bytes=7 * GB, measured_at=CYCLE_1_END, now=CYCLE_1_END + SECOND,
                )
                self.assertEqual(result.status, "not_metered" if switch else "disabled")
        for model in (UsageEntry, ChargeLine, UsageEvent):
            self.assertEqual(model.objects.count(), 0)

    def test_a_renewal_and_its_statement_carry_the_base_fee_only(self):
        subscription = make_active()
        with override_settings(BILLING_USAGE_ENABLED=True):
            usage_view = services.overview(subscription.site, at=RENEWAL_AT)["outstanding_centavos"]
            payment, statement = self.paid_statement(subscription, RENEWAL_AT)
        self.assertEqual(usage_view, 0)
        self.assertEqual((payment.amount_centavos, payment.usage_centavos), (plan.PRICE_CENTAVOS, 0))
        lines = list(statement.lines.all())
        self.assertEqual([(l.kind, l.amount_centavos) for l in lines], [("base", plan.PRICE_CENTAVOS)])
        self.assertEqual(statement.total_centavos, plan.PRICE_CENTAVOS)
        reconcile(self, subscription)

    def test_there_is_no_video_meter(self):
        self.assertEqual(UsageEntry.Kind.values, ["storage", "orders"])
        self.assertFalse([c for c in UsageEntry._meta.get_fields() if "video" in c.name])


@override_settings(**{**ON, "BILLING_USAGE_ENABLED": True})
class PostLaunchStorageRehearsal(Fixture, TestCase):
    """NOT a launch behaviour. Storage is switched on only by patching `plan.STORAGE_METERED`, as
    B4's own tests do, to prove the statement consumes the charge lines and never recomputes."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(plan, "STORAGE_METERED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def snap(self, subscription, ref, size, at=CYCLE_1_END, now=None):
        return usage.record_storage_snapshot(
            subscription.site, source_reference=ref, period_end_at=at, measured_bytes=size,
            measured_at=at, now=now or at + SECOND,
        )

    def test_snapshots_charge_started_gb_over_the_allowance_once_and_the_statement_consumes_them(self):
        subscription = make_active()
        included = plan.STORAGE_INCLUDED_GB * GB
        self.assertEqual(self.snap(subscription, "within", included).status, "within_allowance")
        self.assertEqual(UsageEntry.objects.count(), 0)
        first = self.snap(subscription, "s1", included + 1 + GB)  # 1 GB and a byte over: 2 started GB
        self.assertEqual((first.status, first.charge_centavos), ("counted", 2 * plan.STORAGE_OVERAGE_CENTAVOS_PER_GB))
        again = self.snap(subscription, "s1", 99 * GB)
        other_ref = self.snap(subscription, "s2", 99 * GB)
        self.assertTrue(again.replayed and other_ref.replayed)
        self.assertEqual(other_ref.charge_centavos, first.charge_centavos)
        line = ChargeLine.objects.get()
        with self.assertRaises(Exception):
            line.amount_centavos += 1
            line.save()
        line.refresh_from_db()
        owed = services.overview(subscription.site, at=AFTER_CYCLE_1)["outstanding_centavos"]
        self.assertEqual(owed, line.amount_centavos)
        payment = submit(fresh(subscription), AFTER_CYCLE_1)
        self.assertEqual(payment.usage_centavos, line.amount_centavos)
        self.assertEqual(payment.amount_centavos, plan.PRICE_CENTAVOS + line.amount_centavos)
        payment = self.confirm(payment, AFTER_CYCLE_1 + HOUR)
        statement = issue.issue_for_payment(payment, now=AFTER_CYCLE_1 + 2 * HOUR)
        usage_lines = statement.lines.filter(kind="usage")
        self.assertEqual([l.source_ref for l in usage_lines], [f"charge:{line.pk}"])
        self.assertEqual(usage_lines[0].amount_centavos, line.amount_centavos)
        self.assertEqual(ChargeLine.objects.get().status, "settled")
        reconcile(self, subscription)
        self.assertEqual(UsageEvent.objects.filter(entry__source_reference="s1").count(), 1)

    def test_a_late_snapshot_is_reviewed_and_admitting_it_is_audited_and_collected_next(self):
        subscription = make_active()
        late = self.snap(subscription, "late-1", 7 * GB, now=CYCLE_1_END + days(4))
        self.assertEqual((late.status, late.reason), ("needs_review", "late_over_72h"))
        self.assertEqual(ChargeLine.objects.count(), 0)
        usage.dismiss("late-1", self.operator, "Proof the upload was late", now=CYCLE_1_END + days(5))
        entry = UsageEntry.objects.get()
        self.assertEqual((entry.status, entry.reason), ("written_off", "reviewed_not_billable"))
        self.assertEqual(ChargeLine.objects.count(), 0)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="billing_usage_dismissed").count(), 1)
        other = make_active(email="rev@example.test", name="rev-store")
        self.snap(other, "late-2", 7 * GB, now=CYCLE_1_END + days(4))
        usage.admit("late-2", self.operator, "Proof of upload time seen", now=CYCLE_1_END + days(5))
        line = ChargeLine.objects.get()
        self.assertEqual(line.labels, ["late_admitted"])
        self.assertEqual(OperatorAuditEntry.objects.filter(action="billing_usage_admitted").count(), 1)

    def test_a_suspended_site_accrues_nothing_and_the_row_waits_for_review(self):
        subscription = make_suspended()  # cycle 2 lapsed: its period ends at CYCLE_2_END
        result = self.snap(subscription, "dark", 7 * GB, at=CYCLE_2_END)
        self.assertEqual((result.status, result.reason), ("written_off", "cycle_lapsed"))
        self.assertEqual(ChargeLine.objects.count(), 0)
        self.assertEqual(fresh(subscription).state, STATE.SUSPENDED)  # never reactivates service

    def test_a_cancelled_site_pays_its_overage_alone_and_nothing_more_is_charged_after(self):
        subscription = make_active()
        services.cancel(subscription, now=CYCLE_1_END - days(2))
        self.snap(subscription, "final", 7 * GB)
        payment = submit(fresh(subscription), CYCLE_1_END + days(1), plan_code=plan.USAGE_ONLY)
        self.assertEqual(
            (payment.cycle_id, payment.amount_centavos), (None, 2 * plan.STORAGE_OVERAGE_CENTAVOS_PER_GB)
        )
        payment = self.confirm(payment, CYCLE_1_END + days(1) + HOUR)
        statement = issue.issue_for_payment(payment, now=CYCLE_1_END + days(2))
        self.assertEqual([l.kind for l in statement.lines.all()], ["usage"])
        reconcile(self, subscription)
        later = self.snap(subscription, "after", 9 * GB, at=CYCLE_1_END + days(0), now=CYCLE_1_END + SECOND * 2)
        self.assertEqual(later.replayed, True)  # one snapshot per period
        self.assertEqual(ChargeLine.objects.count(), 1)


class NoSecondCalculationPathTests(unittest.TestCase):
    """The allowance and the price of overage live in plan.py and usage.py only."""

    ROOT = Path(settings.BASE_DIR) / "apps"
    NAMES = ("STORAGE_OVERAGE_CENTAVOS_PER_GB", "STORAGE_INCLUDED_GB", "GB_BYTES", "started_gb")

    def sources(self):
        for path in self.ROOT.rglob("*.py"):
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            yield path, path.read_text()

    def test_only_plan_and_usage_know_the_allowance_and_the_price(self):
        for path, text in self.sources():
            if path.name in ("plan.py", "usage.py") and path.parent.name == "billing":
                continue
            for name in self.NAMES:
                self.assertNotIn(name, text, f"{name} in {path}")

    def test_only_issue_and_the_register_make_document_lines_and_both_read_the_charge_lines(self):
        makers = [p.relative_to(self.ROOT).as_posix() for p, t in self.sources() if re.search(r"StatementLine\(\s*statement=", t)]
        self.assertEqual(sorted(makers), ["statements/adjust.py", "statements/issue.py"])
        for path in ("statements/issue.py", "invoices/services.py"):
            self.assertIn("ChargeLine", (self.ROOT / path).read_text())
            self.assertIn("amount_centavos", (self.ROOT / path).read_text())

    def test_there_is_one_billing_page_route_module_and_one_usage_route(self):
        count = sum(t.count('"/sites/{site_id}/billing/usage/"') for _, t in self.sources())
        self.assertEqual(count, 1)

    def test_no_bir_client_exists(self):
        for path, text in self.sources():
            self.assertFalse(
                {"bir", "eis", "esp", "pti"} & set(re.split(r"[_.]", path.name.lower())), str(path)
            )
            self.assertNotRegex(text, r"(?i)bir\.gov|eis\.bir|efps|bir_api|BIR_ENDPOINT", str(path))
        self.assertFalse([n for n in dir(settings) if re.search(r"BIR|EIS_|ESP_|PTI_", n)])


# ---------------------------------------------------------------- scenarios 6, 7, 10 (services)


def snapshot_of(statement):
    """Everything about a statement that must never change."""
    statement = Statement.objects.get(pk=statement.pk)
    return (
        statement.number, statement.total_centavos, statement.issuer, statement.bill_to,
        statement.pdf_sha256, statement.pdf_size, statement.label, statement.issued_at,
        tuple(statement.lines.values_list("position", "kind", "description", "amount_centavos")),
    )


@override_settings(**ON)
class StatementJourneyTests(Fixture, TestCase):
    def setUp(self):
        super().setUp()
        self.subscription = make_active()
        BillingProfile.objects.create(
            site=self.subscription.site, company_name="Synthetic Co", billing_email="bill@example.test",
            address="1 Test Street",
        )
        self.payment = self.pay(self.subscription, RENEWAL_AT)
        self.statement = issue.issue_for_payment(self.payment, now=RENEWAL_AT + 2 * HOUR)

    def test_a_confirmed_payment_is_finalised_once_with_a_gap_free_number(self):
        statement = self.statement
        self.assertRegex(statement.number, r"^NOBS-2026-\d{6}$")
        self.assertEqual(statement.source_key, f"payment:{self.payment.public_id}")
        self.assertTrue(statement.is_test)
        series = StatementNumberSeries.objects.get(prefix="NOBS", year=2026)
        self.assertEqual(series.last_value, 1)
        again = issue.issue_for_payment(self.payment, now=RENEWAL_AT + 5 * HOUR)
        self.assertEqual(again.pk, statement.pk)
        self.assertEqual(issue.ensure_statements(), 0)
        self.assertEqual(StatementNumberSeries.objects.get(pk=series.pk).last_value, 1)
        self.assertEqual(Statement.objects.count(), 1)
        self.pay(make_active(email="two@example.test", name="two-store"), RENEWAL_AT)
        # The safety net reads the clock for the number's year, so hold it inside 2026.
        with mock.patch("django.utils.timezone.now", return_value=RENEWAL_AT + 6 * HOUR):
            self.assertEqual(issue.ensure_statements(), 1)  # the safety net issues the missing one
            self.assertEqual(issue.ensure_statements(), 0)
        self.assertEqual(
            sorted(Statement.objects.values_list("number", flat=True)),
            ["NOBS-2026-000001", "NOBS-2026-000002"],
        )

    def test_the_statement_carries_the_label_and_frozen_snapshots(self):
        statement = self.statement
        self.assertEqual(statement.label, LABEL)
        self.assertEqual(statement.issuer["legal_name"], self.issuer.legal_name)
        self.assertEqual(statement.issuer["tin"], self.issuer.tin)
        self.assertEqual(statement.bill_to["name"], "Synthetic Co")
        self.assertNotIn("tin", statement.bill_to)  # only when the Subscriber supplied it
        frozen = snapshot_of(statement)
        with self.assertRaises(Immutable):
            self.issuer.tin = "999-999-999-999"
            self.issuer.save()
        profile = BillingProfile.objects.get(site=self.subscription.site)
        profile.company_name = "Changed Name"
        profile.save()
        self.assertEqual(snapshot_of(statement), frozen)

    def test_the_pdf_is_the_stored_one_and_says_what_the_stored_lines_say(self):
        rows = pdf_facts(self, self.statement)
        self.assertEqual(rows.count(pdf.money(self.statement.total_centavos)), 3)
        reconcile(self, self.subscription)

    def test_a_finalised_statement_refuses_edit_and_delete(self):
        statement, line = self.statement, self.statement.lines.get()
        with self.assertRaises(Immutable):
            statement.total_centavos = 1
            statement.save()
        for call in (
            lambda: Statement.objects.filter(pk=statement.pk).update(total_centavos=1),
            lambda: Statement.objects.filter(pk=statement.pk).delete(),
            lambda: statement.delete(),
            lambda: StatementLine.objects.filter(pk=line.pk).update(amount_centavos=1),
            lambda: StatementLine.objects.filter(pk=line.pk).delete(),
            lambda: StatementLine.objects.bulk_update([line], ["amount_centavos"]),
        ):
            with self.assertRaises(Immutable):
                call()
        if connection.vendor == "postgresql":
            for sql, params in (
                ("UPDATE statements_statement SET total_centavos = 1 WHERE id = %s", [statement.pk]),
                ("DELETE FROM statements_statementline WHERE id = %s", [line.pk]),
            ):
                with self.assertRaises(DatabaseError), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(sql, params)
        self.assertEqual(Statement.objects.get(pk=statement.pk).total_centavos, plan.PRICE_CENTAVOS)

    def test_a_refused_edit_leaves_audit_evidence(self):
        before = OperatorAuditEntry.objects.count()
        with self.assertRaises(Immutable):
            self.statement.total_centavos = 1
            self.statement.save()
        self.assertEqual(OperatorAuditEntry.objects.count(), before + 1)
        entry = OperatorAuditEntry.objects.get(action="billing_record_edit_refused")
        self.assertEqual(entry.target_id, self.statement.public_id)

    def test_the_handwritten_invoice_scan_is_evidence_and_leaves_the_statement_untouched(self):
        frozen = snapshot_of(self.statement)
        statements_before = Statement.objects.count()
        item, invoice, scan, content = self.register(self.statement)
        self.assertEqual(snapshot_of(self.statement), frozen)
        self.assertEqual(Statement.objects.count(), statements_before)  # a scan creates no statement
        item.refresh_from_db()
        self.assertEqual(item.statement_id, self.statement.pk)
        self.assertEqual(item.status, "verified")
        self.assertEqual(scan.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(scan.size, len(content))
        self.assertEqual(invoice.total_sales_centavos, self.statement.total_centavos)
        self.assertEqual(self.store.read(scan.store_key), content)
        for action in (
            "billing_payment_confirmed", "billing_invoice_recorded",
            "billing_invoice_scan_uploaded", "billing_invoice_verified",
        ):
            self.assertEqual(OperatorAuditEntry.objects.filter(action=action).count(), 1, action)
        reconcile(self, self.subscription)

    def test_a_credit_note_reduces_without_touching_the_original(self):
        original = self.statement
        self.register(original)
        frozen = snapshot_of(original)
        first = adjust.adjust(
            self.finance, original.public_id, "billing_error",
            [{"description": "Part of the term refunded", "amount_centavos": -50000}],
            now=RENEWAL_AT + days(1),
        )
        second = adjust.adjust(
            self.finance, original.public_id, "goodwill",
            [{"description": "Goodwill reduction", "amount_centavos": -50000}],
            now=RENEWAL_AT + days(2),
        )
        self.assertEqual(snapshot_of(original), frozen)
        for made in (first, second):
            self.assertEqual(made.original_id, original.pk)
            self.assertRegex(made.number, r"^NOCN-2026-\d{6}$")
            self.assertLess(made.total_centavos, 0)
            self.assertEqual(made.document_type, "adjustment")
        self.assertEqual(adjust.remaining(original), plan.PRICE_CENTAVOS - 100000)
        rows = pdf_facts(self, first)
        self.assertIn(f"Adjusts statement: {original.number}", rows)
        self.assertIn("Reason: Billing error", rows)
        with self.assertRaises(ApiError) as raised:
            adjust.adjust(
                self.finance, original.public_id, "other",
                [{"description": "Too much", "amount_centavos": -plan.PRICE_CENTAVOS}],
            )
        self.assertEqual(raised.exception.code, "exceeds_remaining")
        with self.assertRaises(ApiError) as raised:
            adjust.adjust(
                self.finance, first.public_id, "other", [{"description": "x", "amount_centavos": -1}],
            )
        self.assertEqual(raised.exception.code, "not_adjustable")
        listed = [r for r in invoices.reconcile()["items"] if r["statement"] == original.number]
        self.assertEqual(
            listed[0]["adjustments"],
            [{"number": c.number, "total_centavos": c.total_centavos} for c in (first, second)],
        )
        self.assertEqual(
            OperatorAuditEntry.objects.filter(action="billing_statement_adjusted").count(), 2
        )
        reconcile(self, self.subscription)
        self.assertEqual(snapshot_of(original), frozen)

    def test_every_generated_document_carries_the_label_and_no_bir_claim(self):
        adjustment = adjust.adjust(
            self.finance, self.statement.public_id, "billing_error",
            [{"description": "Part of the term refunded", "amount_centavos": -1000}],
        )
        for statement in (self.statement, adjustment):
            self.assertTrue(statement.is_test)
            text = " ".join(pdf_facts(self, statement)).replace(LABEL, "")
            self.assertNotRegex(text, CLAIMS)


@override_settings(**ON, BILLING_STAFF_ALERT_EMAIL="staff@example.test")
class EmailsMakeNoBirClaimTests(Fixture, TestCase):
    def test_no_billing_email_claims_authority_or_carries_a_statement(self):
        subscription = make_subscription()
        notices.run(T0 + days(11) + HOUR)  # trial reminder
        notices.run(T0 + days(13) + HOUR)  # trial reminder
        rejected = submit(fresh(subscription), T0 + days(13) + 2 * HOUR)
        notices.run(T0 + days(13) + 3 * HOUR)  # proof received, staff alert
        services.reject_transfer(
            rejected, self.operator, reason="not_received", now=T0 + days(13) + 4 * HOUR
        )
        notices.run(T0 + days(13) + 5 * HOUR)  # rejected
        proof = submit(fresh(subscription), T0 + days(13) + 6 * HOUR, content=PNG)
        self.confirm(proof, T0 + days(13) + 7 * HOUR)
        notices.run(T0 + days(13) + 8 * HOUR)  # approved
        notices.run(CYCLE_1_END - days(7) + HOUR)  # renewal reminder
        notices.run(CYCLE_1_END + days(7) + HOUR)  # paused
        make_subscription("end@example.test", "end-store")
        services.sweep(now=TRIAL_END)
        notices.run(TRIAL_END + HOUR)  # trial ended, export notice
        self.assertGreaterEqual(len(mail.outbox), 6)
        for message in mail.outbox:
            self.assertEqual(message.attachments, [], message.subject)
            text = f"{message.subject}\n{message.body}"
            self.assertNotRegex(text, CLAIMS, message.subject)
            self.assertNotRegex(text, r"NOBS-|NOCN-", message.subject)


@override_settings(**ON)
class PlanListMakesNoBirClaimTests(SitesApiTestCase):
    def test_the_public_plan_list_makes_no_claim(self):
        from django.test import Client

        response = Client().get("/v1/plan/", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotRegex(response.content.decode(), CLAIMS)


# ---------------------------------------------------------------- scenarios 6, 8, 10 (owner routes)

PROFILE = {
    "company_name": "Ana Trading", "billing_email": "billing@example.test",
    "address": "1 Rizal St, Cebu", "tin": "",
}


def text_of_json(response):
    return json.dumps(response.json(), ensure_ascii=False)


@override_settings(**ON)
class OwnerApiTests(Fixture, SitesApiTestCase):
    """Two subscribers on the real routes: Ana (the full journey) and Ben (isolation)."""

    def setUp(self):
        super().setUp()
        self.a_id = self.create_site()["id"]
        self.ben = self.other_client()
        self.b_id = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben Store")["id"]
        self.a_sub = Subscription.objects.get(site__public_id=self.a_id)
        self.b_sub = Subscription.objects.get(site__public_id=self.b_id)
        self.a_payment, self.a_statement = self.paid_statement(
            self.a_sub, self.a_sub.trial_start_at + days(1)
        )
        self.b_payment, self.b_statement = self.paid_statement(
            self.b_sub, self.b_sub.trial_start_at + days(1)
        )
        self.a_item, self.a_invoice, self.a_scan, self.a_bytes = self.register(self.a_statement)
        self.a = f"/v1/sites/{self.a_id}/billing/"
        self.b = f"/v1/sites/{self.b_id}/billing/"

    # scenarios 5 to 7: the owner's figures are the stored ones

    def test_the_owner_sees_exactly_what_was_stored(self):
        statement = self.a_statement
        listed = self.get(self.a + "statements/").json()
        self.assertEqual([r["number"] for r in listed["items"]], [statement.number])
        row = listed["items"][0]
        self.assertEqual(
            (row["total_centavos"], row["kind"], row["document_type"], row["is_test"]),
            (statement.total_centavos, "monthly", "statement", True),
        )
        detail = self.get(self.a + f"statements/{statement.number}/").json()
        self.assertEqual(detail["label"], LABEL)
        self.assertEqual(detail["total_centavos"], statement.total_centavos)
        self.assertEqual(
            [(l["position"], l["kind"], l["description"], l["quantity"], l["unit_centavos"], l["amount_centavos"]) for l in detail["lines"]],
            list(statement.lines.values_list(
                "position", "kind", "description", "quantity", "unit_centavos", "amount_centavos"
            )),
        )
        self.assertEqual(sum(l["amount_centavos"] for l in detail["lines"]), detail["total_centavos"])
        response = self.get(self.a + f"statements/{statement.number}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, documents.read_pdf(statement))
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), statement.pdf_sha256)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Content-Type"], "application/pdf")
        flat = " ".join(text_of(response.content))
        for line in detail["lines"]:
            self.assertIn(line["description"], flat)
        payments = self.get(self.a + "payments/").json()["items"]
        self.assertEqual([p["id"] for p in payments], [self.a_payment.public_id])
        self.assertEqual(payments[0]["amount_centavos"], self.a_payment.amount_received_centavos)
        self.assertEqual(payments[0]["amount_centavos"], statement.total_centavos)
        self.assertEqual(self.get(self.a).json()["outstanding_centavos"], 0)
        reconcile(self, self.a_sub)

    def test_the_owner_gets_the_verified_copy_only_and_never_a_statement_change(self):
        frozen = snapshot_of(self.a_statement)
        listed = self.get(self.a + "invoices/").json()["items"]
        self.assertEqual(
            (listed[0]["id"], listed[0]["state"], listed[0]["message"], listed[0]["total_centavos"]),
            (self.a_item.public_id, "available", COPY_LABEL, self.a_statement.total_centavos),
        )
        copy = self.get(self.a + f"invoices/{self.a_item.public_id}/copy/")
        self.assertEqual((copy.status_code, copy.content), (200, self.a_bytes))
        self.assertEqual(copy["Cache-Control"], "private, no-store")
        # Ben's sale has no verified scan yet: pending, and no copy.
        pending = self.get(self.b + "invoices/", client=self.ben).json()["items"]
        self.assertEqual(pending[0]["state"], "pending")
        self.assertEqual(
            self.get(self.b + f"invoices/{pending[0]['id']}/copy/", client=self.ben).status_code, 404
        )
        self.assertEqual(snapshot_of(self.a_statement), frozen)

    # scenario 8: tenant isolation

    def routes(self, payment, statement, item_id):
        return [
            "", "usage/", "payment/", "payments/", f"payments/{payment.public_id}/",
            f"payments/{payment.public_id}/proof/", "statements/", f"statements/{statement.number}/",
            f"statements/{statement.number}/pdf/", "invoices/", f"invoices/{item_id}/copy/", "profile/",
        ]

    def test_ben_cannot_read_download_infer_upload_or_save_anything_of_anas(self):
        unknown = "/v1/sites/site_unknownunknownunknownxx/billing/"
        for route in self.routes(self.a_payment, self.a_statement, self.a_item.public_id):
            with self.subTest(route=route):
                got = self.get(self.a + route, client=self.ben)
                want = self.get(unknown + route, client=self.ben)
                self.assertEqual((got.status_code, got.json()["error"]["code"]), (404, "not_found"))
                self.assertEqual(got.json(), want.json())  # a foreign site looks like none at all
        # Ana's ids under Ben's own site answer as made-up ids do.
        made_up = {
            f"payments/{self.a_payment.public_id}/": "payments/btp_" + "0" * 26 + "/",
            f"payments/{self.a_payment.public_id}/proof/": "payments/btp_" + "0" * 26 + "/proof/",
            f"statements/{self.a_statement.number}/": "statements/NOBS-2026-999999/",
            f"statements/{self.a_statement.number}/pdf/": "statements/NOBS-2026-999999/pdf/",
            f"invoices/{self.a_item.public_id}/copy/": "invoices/mbi_" + "0" * 26 + "/copy/",
        }
        for theirs, nothing in made_up.items():
            with self.subTest(theirs=theirs):
                got = self.get(self.b + theirs, client=self.ben)
                want = self.get(self.b + nothing, client=self.ben)
                self.assertEqual((got.status_code, got.json()), (want.status_code, want.json()))
                self.assertEqual(got.status_code, 404)
        for cursor in (self.a_statement.public_id, "stm_" + "0" * 26):
            response = self.get(self.b + f"statements/?cursor={cursor}", client=self.ben)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid_cursor"))
        # Upload and save against Ana's site: refused, and nothing is written.
        payments, profiles = BankTransferPayment.objects.count(), BillingProfile.objects.count()
        proof = SimpleUploadedFile("slip.jpg", JPEG, "image/jpeg")
        response = self.ben.post(
            self.a + "payments/", data={"plan": "monthly", "proof": proof}, secure=True,
            HTTP_ORIGIN=ORIGIN,
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.send("put", self.a + "profile/", PROFILE, client=self.ben).status_code, 404)
        self.assertEqual(self.send("post", self.a + "cancel/", {}, client=self.ben).status_code, 404)
        self.assertEqual(BankTransferPayment.objects.count(), payments)
        self.assertEqual(BillingProfile.objects.count(), profiles)
        self.assertEqual(fresh(self.a_sub).state, STATE.ACTIVE)
        # What Ben is shown of his own account holds nothing of Ana's.
        mine = self.get(self.b + "statements/", client=self.ben).json()["items"]
        self.assertEqual([r["number"] for r in mine], [self.b_statement.number])
        self.assertEqual(
            [p["id"] for p in self.get(self.b + "payments/", client=self.ben).json()["items"]],
            [self.b_payment.public_id],
        )
        self.assertEqual(
            [i["id"] for i in self.get(self.b + "invoices/", client=self.ben).json()["items"]],
            [ManualInvoiceItem.objects.get(statement=self.b_statement).public_id],
        )

    def test_signed_out_is_refused_on_every_route(self):
        from django.test import Client

        for route in self.routes(self.a_payment, self.a_statement, self.a_item.public_id):
            with self.subTest(route=route):
                self.assertEqual(Client().get(self.a + route, secure=True).status_code, 401)

    def test_no_owner_response_claims_an_authority_it_does_not_have(self):
        statement = self.a_statement
        for route in self.routes(self.a_payment, statement, self.a_item.public_id):
            if route.endswith(("pdf/", "proof/", "copy/")):
                continue
            response = self.get(self.a + route)
            if response.status_code != 200:
                continue
            text = text_of_json(response).replace(LABEL, "")
            if route == "invoices/":
                text = text.replace(COPY_LABEL, "")
            with self.subTest(route=route):
                self.assertNotRegex(text, CLAIMS)
        detail = self.get(self.a + f"statements/{statement.number}/").json()
        self.assertEqual((detail["label"], detail["is_test"]), (LABEL, True))


# ---------------------------------------------------------------- scenarios 6, 7, 8 (staff routes)


@on_staff
@override_settings(**ON)
class StaffFinanceTests(Fixture, StaffTestCase):
    BASE = "/staff/v1/finance/statements/"
    PATHS = ("list/", "detail/", "pdf/", "adjust/")

    def setUp(self):
        super().setUp()
        self.identity = self.enrolled()
        self.subscription = make_active()
        self.payment, self.statement = self.paid_statement(self.subscription, RENEWAL_AT)

    def post(self, path, data=None, **kwargs):
        return self.call("POST", self.BASE + path, data, **kwargs)

    def test_signed_out_and_foreign_origin_are_refused(self):
        for path in self.PATHS:
            self.assertError(self.post(path, {}), 401, "unauthenticated")
        self.staff_sign_in()
        for path in self.PATHS:
            self.assertError(self.post(path, {}, origin="https://evil.example"), 403, "origin_not_allowed")

    def test_staff_read_the_same_statement_the_owner_has_and_every_read_is_audited(self):
        self.staff_sign_in()
        listed = self.post("list/", {}).json()["items"]
        self.assertEqual([r["number"] for r in listed], [self.statement.number])
        self.assertNotIn("pdf_key", listed[0])
        self.assertNotIn("pdf_sha256", listed[0])
        detail = self.post("detail/", {"statement": self.statement.public_id}).json()
        self.assertEqual(detail["total_centavos"], self.statement.total_centavos)
        self.assertEqual(sum(l["amount_centavos"] for l in detail["lines"]), detail["total_centavos"])
        self.assertEqual(detail["remaining_centavos"], self.statement.total_centavos)
        response = self.post("pdf/", {"statement": self.statement.public_id})
        self.assertEqual(response.content, documents.read_pdf(self.statement))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(OperatorAuditEntry.objects.filter(action="billing_statement_viewed").count(), 1)

    def test_a_credit_note_through_the_staff_route_leaves_the_original_alone(self):
        self.staff_sign_in()
        frozen = snapshot_of(self.statement)
        made = self.post("adjust/", {
            "statement": self.statement.public_id, "reason": "billing_error",
            "lines": [{"description": "Part of the term refunded", "amount_centavos": -25000}],
        })
        self.assertEqual(made.status_code, 201, made.content)
        body = made.json()
        self.assertEqual((body["document_type"], body["total_centavos"]), ("adjustment", -25000))
        self.assertEqual(body["original"], self.statement.public_id)
        self.assertEqual(snapshot_of(self.statement), frozen)
        detail = self.post("detail/", {"statement": self.statement.public_id}).json()
        self.assertEqual(detail["remaining_centavos"], self.statement.total_centavos - 25000)
        self.assertEqual([a["total_centavos"] for a in detail["adjustments"]], [-25000])
        over = self.post("adjust/", {
            "statement": self.statement.public_id, "reason": "other",
            "lines": [{"description": "Too much", "amount_centavos": -self.statement.total_centavos}],
        })
        self.assertError(over, 409, "exceeds_remaining")
        entry = OperatorAuditEntry.objects.get(action="billing_statement_adjusted")
        self.assertEqual(entry.target_id, body["statement"])
        reconcile(self, self.subscription)


# ---------------------------------------------------------------- scenario 11 (bank transfer)

EXPORT_SUBJECT = "Your NegosyoOnline store data: how to ask for your export"


@override_settings(**ON)
class SuspensionAndRecoveryTests(Fixture, TestCase):
    """Suspension, late payment and recovery as the bank-transfer launch has them. The
    `payment_requires_attention` state, the automatic restore and the Xendit lines are blocked."""

    def test_no_payment_for_7_days_suspends_the_site_and_the_owner_is_told_once(self):
        subscription = make_active()
        at = CYCLE_1_END + days(7)
        self.assertEqual(services.sweep(now=at), {"suspended_no_outcome": 1})
        transition = fresh(subscription).transitions.get(code="suspended_no_outcome")
        self.assertEqual(transition.effective_at, at)
        view = services.overview(subscription.site, at=at + HOUR)
        self.assertTrue(view["billing_only"])
        self.assertFalse(view["storefront_served"])
        self.assertEqual(notices.run(at + HOUR)["service_paused"], 1)
        self.assertNotIn("service_paused", notices.run(at + 2 * HOUR))
        paused = [m for m in mail.outbox if m.subject == "Your NegosyoOnline site is paused"]
        self.assertEqual(len(paused), 1)
        self.assertEqual((UsageEntry.objects.count(), ChargeLine.objects.count()), (0, 0))

    def test_a_proof_confirmed_inside_the_7_days_settles_the_next_cycle_from_the_old_end(self):
        subscription = make_active()
        payment = submit(fresh(subscription), CYCLE_1_END + days(1))
        self.confirm(payment, CYCLE_1_END + days(3))
        subscription = fresh(subscription)
        self.assertEqual(subscription.state, STATE.ACTIVE)
        cycle = subscription.cycles.get(sequence=2)
        self.assertEqual((cycle.start_at, cycle.end_at), (CYCLE_1_END, CYCLE_2_END))
        self.assertEqual((cycle.settlement, cycle.paid_at), ("paid", CYCLE_1_END + days(3)))
        statement = issue.issue_for_payment(BankTransferPayment.objects.get(pk=payment.pk))
        self.assertEqual(statement.cycle_id, cycle.pk)
        self.assertEqual(subscription.cycles.filter(settlement="lapsed").count(), 0)

    def test_a_payment_after_the_suspension_starts_a_new_paid_cycle_and_leaves_the_old_alone(self):
        subscription = make_active()
        services.sweep(now=CYCLE_1_END + days(7))
        subscription = fresh(subscription)
        self.assertEqual(subscription.state, STATE.SUSPENDED)
        before = {c.pk: (c.settlement, c.start_at, c.end_at) for c in subscription.cycles.all()}
        at = CYCLE_1_END + days(9)
        payment = self.pay(subscription, at)
        subscription = fresh(subscription)
        self.assertEqual(subscription.state, STATE.ACTIVE)
        newest = subscription.cycles.order_by("-sequence").first()
        self.assertEqual((newest.settlement, newest.start_at), ("paid", at + HOUR))
        for pk, values in before.items():
            cycle = BillingCycle.objects.get(pk=pk)
            self.assertEqual((cycle.settlement, cycle.start_at, cycle.end_at), values)
        statement = issue.issue_for_payment(payment)
        self.assertEqual(statement.cycle_id, newest.pk)
        reconcile(self, subscription)

    def test_a_suspended_site_can_be_paid_only_before_its_deletion_date(self):
        subscription = make_active()
        services.sweep(now=CYCLE_1_END + days(7))
        subscription = fresh(subscription)
        self.assertEqual(subscription.deletion_due_at, CYCLE_1_END + days(7) + days(90))
        with self.assertRaises(ApiError) as raised:
            submit(subscription, subscription.deletion_due_at)
        self.assertEqual(raised.exception.code, "past_deletion_date")
        self.assertEqual(BankTransferPayment.objects.count(), 0)

    def test_a_proof_left_undecided_past_the_window_suspends_and_stays_for_the_operator(self):
        subscription = make_active()
        payment = submit(fresh(subscription), CYCLE_1_END + days(1))
        self.assertEqual(services.sweep(now=CYCLE_1_END + days(7)), {"suspended_no_outcome": 1})
        self.assertEqual(fresh(subscription).state, STATE.SUSPENDED)
        self.assertEqual(BankTransferPayment.objects.get(pk=payment.pk).status, "submitted")
        self.confirm(payment, CYCLE_1_END + days(8))
        self.assertEqual(fresh(subscription).state, STATE.ACTIVE)

    def test_a_second_proof_replaces_the_one_under_review_and_the_same_file_is_not_a_second_payment(self):
        subscription = make_active()
        first = submit(fresh(subscription), CYCLE_1_END + days(1))
        same = submit(fresh(subscription), CYCLE_1_END + days(1) + HOUR)
        self.assertEqual(same.pk, first.pk)
        second = submit(fresh(subscription), CYCLE_1_END + days(1) + 2 * HOUR, content=PNG)
        self.assertEqual(BankTransferPayment.objects.filter(status="submitted").count(), 1)
        self.assertEqual(BankTransferPayment.objects.get(status="submitted").pk, second.pk)


@override_settings(**ON)
class ServiceEndNoticeTests(Fixture, TestCase):
    def notice_mails(self):
        return [m for m in mail.outbox if m.subject == EXPORT_SUBJECT]

    def check_text(self, body, subscription):
        subscription = fresh(subscription)
        self.assertIn("for 30 days after your service ended", body)
        self.assertIn(SUPPORT_EMAIL, body)
        self.assertIn(notices._day_time(subscription.download_until), body)
        self.assertIn(notices._day_time(subscription.deletion_due_at), body)
        self.assertIn("receiving-account details", body)
        self.assertIn("what the law requires us to keep", body)
        self.assertIn("a dispute or a fraud check", body)  # not shown as unconditional
        self.assertNotRegex(body, r"(?i)image|xendit")  # no promise about proof images; no Xendit
        self.assertNotRegex(body, CLAIMS)

    def test_a_cancelled_site_gets_the_notice_once(self):
        subscription = make_active()
        services.cancel(subscription, now=CYCLE_1_END - days(3))
        self.assertEqual(notices.run(CYCLE_1_END + HOUR)["export_notice"], 1)
        self.assertNotIn("export_notice", notices.run(CYCLE_1_END + 2 * HOUR))
        self.assertEqual(len(self.notice_mails()), 1)
        self.check_text(self.notice_mails()[0].body, subscription)

    def test_a_trial_that_ended_without_payment_gets_the_notice_once(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END + HOUR)
        sent = notices.run(TRIAL_END + 2 * HOUR)
        self.assertEqual((sent["export_notice"], sent["trial_ended"]), (1, 1))
        self.assertNotIn("export_notice", notices.run(TRIAL_END + 3 * HOUR))
        self.assertEqual(len(self.notice_mails()), 1)
        body = self.notice_mails()[0].body
        self.assertIn("no payment was approved.", body)
        self.check_text(body, subscription)


class ErasureKeepsBillingRecordsTests(run_base.RunTestCase):
    """Day-90 clearing on the synthetic fixture (the receiving accounts), and the billing
    records the clearing must leave whole (scenario 1, 'subject to retained records')."""

    def setUp(self):
        super().setUp()
        statement = make_statement(self.sub)
        make_line(statement)
        make_invoice(make_item(self.sub))

    @staticmethod
    def billing_rows():
        return {
            label: rows for label, rows in run_base.kept_rows().items()
            if label.split(".")[0] in run_base.KEPT_APPS
        }

    def test_the_receiving_account_is_cleared_and_every_billing_record_stays(self):
        before = self.billing_rows()
        self.assertTrue(before["statements.Statement"])
        result = self.run_batch(self.approved())
        self.assertEqual(result["erased"], 1)
        accounts = list(SitePaymentAccount.objects.filter(site=self.site))
        self.assertTrue(accounts)
        for account in accounts:
            self.assertEqual(
                (account.account_name, account.account_number, account.qr_file_key), ("", "", "")
            )
            self.assertIsNotNone(account.removed_at)
        self.assertEqual(self.billing_rows(), before)

    def test_a_held_site_is_not_proposed_and_keeps_its_account_until_the_hold_ends(self):
        def accounts():
            return list(
                SitePaymentAccount.objects.filter(site=self.site).order_by("pk")
                .values_list("account_name", "account_number", "qr_file_key", "removed_at")
            )

        before = accounts()
        self.assertTrue(any(number for _, number, _, _ in before))
        workflow.place_hold(self.staff, self.site.public_id, reason_code="legal", reference="P5-1", now=run_base.NOW)
        with self.assertRaises(workflow.ApiError) as raised:
            self.propose()
        self.assertIn("held", raised.exception.fields["sites"][0])
        self.assertEqual(accounts(), before)
        workflow.release_hold(self.staff, self.site.public_id, now=run_base.NOW)
        self.assertEqual(self.propose().items.count(), 1)


# ---------------------------------------------------------------- scenario 9 (concurrency)


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(**ON)
class ConcurrencyTests(Fixture, TransactionTestCase):
    """Real simultaneous calls on separate connections. Each ends with one of everything."""

    def failures(self, results):
        return [r for r in results if isinstance(r, Exception)]

    def test_two_operators_confirming_one_proof_settle_it_once(self):
        subscription = make_active()
        payment = submit(fresh(subscription), AFTER_CYCLE_1)
        at = AFTER_CYCLE_1 + HOUR
        results = run_together(lambda: self.confirm(payment, at), lambda: self.confirm(payment, at))
        self.assertFalse(self.failures(results), results)
        self.assertEqual({r.status for r in results}, {"confirmed"})
        subscription = fresh(subscription)
        self.assertEqual(subscription.cycles.filter(settlement="paid").count(), 2)
        self.assertEqual(subscription.transitions.filter(code="renewed").count(), 1)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="billing_payment_confirmed").count(), 1)
        self.assertEqual(ManualInvoiceItem.objects.count(), 1)

    def test_statements_are_issued_once_each_and_their_numbers_have_no_gap(self):
        payments = [
            self.pay(make_active(email=f"c{n}@example.test", name=f"c-store-{n}"), AFTER_CYCLE_1)
            for n in range(3)
        ]
        calls = [
            (lambda p=p: issue.issue_for_payment(p, now=AFTER_CYCLE_1 + 3 * HOUR))
            for p in payments for _ in range(2)
        ] + [issue.ensure_statements]
        results = run_together(*calls)
        self.assertFalse(self.failures(results), results)
        self.assertEqual(
            sorted(Statement.objects.values_list("number", flat=True)),
            [f"NOBS-2026-{n:06d}" for n in (1, 2, 3)],
        )
        self.assertEqual(StatementNumberSeries.objects.get(prefix="NOBS", year=2026).last_value, 3)
        self.assertEqual(len({s.source_key for s in Statement.objects.all()}), 3)

    def test_a_duplicate_register_item_and_a_duplicate_scan_are_one_row(self):
        subscription = make_active()
        payment = self.pay(subscription, AFTER_CYCLE_1)
        statement = issue.issue_for_payment(payment, now=AFTER_CYCLE_1 + 2 * HOUR)
        results = run_together(
            lambda: invoices.open_item_safely(payment, now=AFTER_CYCLE_1),
            lambda: invoices.open_item_safely(payment, now=AFTER_CYCLE_1),
        )
        self.assertFalse(self.failures(results), results)
        self.assertEqual(ManualInvoiceItem.objects.count(), 1)
        item, invoice = self.record(statement)
        content = PNG + b"one-file"
        results = run_together(
            lambda: invoices.upload_scan(self.finance, invoice.public_id, content),
            lambda: invoices.upload_scan(self.finance, invoice.public_id, content),
        )
        self.assertEqual(len(self.failures(results)), 1, results)
        self.assertIsInstance(self.failures(results)[0], ApiError)
        self.assertEqual(InvoiceScan.objects.count(), 1)

    def test_two_notice_scans_at_once_send_each_email_once(self):
        subscription = make_subscription()
        submit(subscription, T0 + days(1))
        at = T0 + days(1) + HOUR
        results = run_together(lambda: notices.run(at), lambda: notices.run(at))
        self.assertFalse(self.failures(results), results)
        received = [m for m in mail.outbox if m.subject == "We received your bank transfer proof"]
        self.assertEqual(len(received), 1)

    def test_a_duplicate_proof_is_one_payment(self):
        subscription = make_active()
        results = run_together(
            lambda: submit(fresh(subscription), AFTER_CYCLE_1),
            lambda: submit(fresh(subscription), AFTER_CYCLE_1),
        )
        self.assertFalse(self.failures(results), results)
        self.assertEqual(BankTransferPayment.objects.count(), 1)


# ---------------------------------------------------------------- the whole fixture adds up


@override_settings(**{**ON, "BILLING_ANNUAL_ENABLED": True, "BILLING_LAUNCH_PROMO_ENABLED": True})
class ReconciliationTests(Fixture, TestCase):
    def test_every_plan_reconciles_from_payment_to_pdf(self):
        monthly = make_active()
        annual = make_subscription("yearly@example.test", "yearly-store")
        promo = make_subscription("promo@example.test", "promo-store")
        made = [self.paid_statement(monthly, RENEWAL_AT)]
        payment = submit(fresh(annual), T0 + days(2), plan_code="annual")
        payment = self.confirm(payment, T0 + days(2) + HOUR)
        made.append((payment, issue.issue_for_payment(payment, now=T0 + days(3))))
        payment = submit(fresh(promo), T0 + days(2), plan_code="launch_promo")
        payment = self.confirm(payment, T0 + days(2) + HOUR)
        made.append((payment, issue.issue_for_payment(payment, now=T0 + days(3))))
        kinds = sorted(statement.kind for _, statement in made)
        self.assertEqual(kinds, ["annual", "launch_promo", "monthly"])
        for subscription, (payment, statement) in zip((monthly, annual, promo), made):
            item, invoice, scan, content = self.register(statement)
            adjust.adjust(
                self.finance, statement.public_id, "billing_error",
                [{"description": "Controlled correction", "amount_centavos": -100}],
                now=T0 + days(4),
            )
            reconcile(self, subscription)
            self.assertEqual(statement.total_centavos, payment.amount_received_centavos)
        self.assertEqual(Statement.objects.filter(document_type="adjustment").count(), 3)
        self.assertEqual(
            sorted(Statement.objects.values_list("number", flat=True))[:3],
            ["NOBS-2026-000001", "NOBS-2026-000002", "NOBS-2026-000003"],
        )
