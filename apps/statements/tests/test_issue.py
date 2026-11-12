"""Issuing a statement (Billing Build B5, PR 5): numbering, lines, snapshots, the safety net."""
import threading
from unittest import mock

from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from apps.billing import plan, services as billing
from apps.billing.tests.base import (
    CYCLE_1_END, days, fresh, make_active, make_operator, submit, transfers_on,
)
from apps.billing.tests.test_usage_collection import LINE, MONTHLY, snapshot
from apps.invoices.models import ManualInvoiceItem
from apps.statements import documents, issue, tasks
from apps.statements.managers import Immutable
from apps.statements.models import BillingProfile, Statement, StatementNumberSeries
from apps.statements.tests.base import make_issuer, make_staff

AT = CYCLE_1_END + days(1)
ON = dict(BILLING_ENABLED=True, BILLING_USAGE_ENABLED=True, BILLING_STATEMENTS_ENABLED=True)


def fake_store(document):
    return documents.Stored(key=f"pdf-{document['number']}", sha256="a" * 64, size=100)


@override_settings(**ON)
class IssueCase(TestCase):
    def setUp(self):
        on = transfers_on()
        on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        for patcher in (
            mock.patch.object(plan, "STORAGE_METERED", True),
            mock.patch.object(documents, "store_document", side_effect=fake_store),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.operator = make_operator()
        self.issuer = make_issuer(verified=True)

    def paid(self, subscription=None, at=AT):
        subscription = subscription or make_active()
        payment = submit(subscription, at)
        return billing.confirm_transfer(
            payment, self.operator, amount_received_centavos=payment.amount_centavos, now=at
        )

    def usage_only(self):
        subscription = make_active()
        billing.cancel(subscription, now=CYCLE_1_END - days(2))
        snapshot(subscription)
        payment = submit(fresh(subscription), AT, plan_code=plan.USAGE_ONLY)
        return billing.confirm_transfer(
            payment, self.operator, amount_received_centavos=payment.amount_centavos, now=AT
        )


class IssueTests(IssueCase):
    def test_a_confirmed_payment_gets_one_numbered_statement_with_its_lines_and_snapshots(self):
        payment = self.paid()
        statement = issue.issue_for_payment(payment, now=AT)
        self.assertEqual(statement.number, "NOBS-2026-000001")
        self.assertEqual(
            (statement.source_key, statement.kind, statement.document_type, statement.is_test),
            (f"payment:{payment.public_id}", "monthly", "statement", True),
        )
        self.assertEqual(statement.cycle_id, payment.cycle_id)
        self.assertEqual(statement.total_centavos, MONTHLY)
        self.assertEqual(statement.payment_reference, payment.reference)
        self.assertEqual((statement.pdf_key, statement.pdf_sha256, statement.pdf_size),
                         ("pdf-NOBS-2026-000001", "a" * 64, 100))
        lines = list(statement.lines.all())
        self.assertEqual([(l.position, l.kind, l.amount_centavos) for l in lines], [(1, "base", MONTHLY)])
        self.assertEqual(sum(l.amount_centavos for l in lines), statement.total_centavos)
        self.assertTrue(lines[0].description.startswith("NegosyoOnline SaaS subscription — Monthly Plan, "))
        self.assertEqual(statement.issuer["legal_name"], self.issuer.legal_name)
        self.assertNotIn("tin", statement.bill_to)
        self.assertIn("email", statement.bill_to)

    def test_a_repeat_returns_the_first_statement_and_takes_no_number(self):
        payment = self.paid()
        first = issue.issue_for_payment(payment, now=AT)
        self.assertEqual(issue.issue_for_payment(payment, now=AT + days(1)).pk, first.pk)
        self.assertEqual(Statement.objects.count(), 1)
        self.assertEqual(StatementNumberSeries.objects.get(prefix="NOBS").last_value, 1)

    def test_numbers_run_on_in_order_across_payments(self):
        numbers = [issue.issue_for_payment(self.paid(make_active(email=f"n{i}@example.test", name=f"n-{i}")),
                                           now=AT).number for i in range(3)]
        self.assertEqual(numbers, ["NOBS-2026-000001", "NOBS-2026-000002", "NOBS-2026-000003"])

    def test_the_year_is_the_manila_year_of_issue(self):
        from datetime import datetime, timezone
        late = datetime(2026, 12, 31, 17, 0, tzinfo=timezone.utc)  # 1 Jan 2027 in Manila
        statement = issue.issue_for_payment(self.paid(), now=late)
        self.assertEqual(statement.number, "NOBS-2027-000001")

    def test_a_failed_issue_gives_up_its_number(self):
        payment = self.paid()
        with mock.patch.object(documents, "store_document", side_effect=documents.StoreUnavailable):
            with self.assertRaises(documents.StoreUnavailable):
                issue.issue_for_payment(payment, now=AT)
        self.assertEqual(Statement.objects.count(), 0)
        self.assertFalse(StatementNumberSeries.objects.exists())
        self.assertEqual(issue.issue_for_payment(payment, now=AT).number, "NOBS-2026-000001")

    def test_a_term_payment_with_overage_has_base_and_usage_lines_adding_to_the_payment(self):
        subscription = make_active()
        snapshot(subscription)
        payment = self.paid(subscription)
        statement = issue.issue_for_payment(payment, now=AT)
        lines = list(statement.lines.all())
        self.assertEqual([l.kind for l in lines], ["base", "usage"])
        self.assertEqual([l.amount_centavos for l in lines], [MONTHLY, LINE])
        self.assertEqual(statement.total_centavos, payment.amount_received_centavos)
        self.assertIn("storage overage", lines[1].description)

    def test_a_usage_only_payment_has_no_cycle_and_overage_lines_only(self):
        payment = self.usage_only()
        statement = issue.issue_for_payment(payment, now=AT)
        self.assertIsNone(statement.cycle)
        self.assertEqual(statement.kind, "usage_only")
        self.assertEqual([l.kind for l in statement.lines.all()], ["usage"])
        self.assertEqual(statement.total_centavos, LINE)

    def test_the_billing_profile_is_the_bill_to_and_its_tin_only_when_supplied(self):
        payment = self.paid()
        BillingProfile.objects.create(
            site=payment.subscription.site, company_name="Ana Store Inc", billing_email="bill@example.test",
            address="1 Road", tin="123-456-789-000",
        )
        bill_to = issue.issue_for_payment(payment, now=AT).bill_to
        self.assertEqual(bill_to, {"name": "Ana Store Inc", "email": "bill@example.test",
                                   "address": "1 Road", "tin": "123-456-789-000"})

    def test_a_later_profile_change_leaves_the_statement_snapshot_alone(self):
        payment = self.paid()
        statement = issue.issue_for_payment(payment, now=AT)
        make_issuer(verified=True, legal_name="Renamed")
        self.assertEqual(Statement.objects.get(pk=statement.pk).issuer["legal_name"], self.issuer.legal_name)
        with self.assertRaises(Immutable):
            statement.save()

    @override_settings(BILLING_LIVE_DOCUMENTS=True)
    def test_live_documents_are_not_marked_test(self):
        self.assertFalse(issue.issue_for_payment(self.paid(), now=AT).is_test)

    def test_the_item_of_the_register_is_linked_once(self):
        with override_settings(BILLING_INVOICE_REGISTER_ENABLED=True):
            payment = self.paid()
            statement = issue.issue_for_payment(payment, now=AT)
        item = ManualInvoiceItem.objects.get(source_ref=payment.public_id)
        self.assertEqual(item.statement_id, statement.pk)

    def test_no_register_item_is_fine(self):
        statement = issue.issue_for_payment(self.paid(), now=AT)
        self.assertFalse(ManualInvoiceItem.objects.exists())
        self.assertIsNotNone(statement.pk)

    def test_the_source_key_is_unique_in_the_database(self):
        statement = issue.issue_for_payment(self.paid(), now=AT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Statement.objects.create(
                number="NOBS-2026-999999", document_type="statement", kind="monthly",
                subscription=statement.subscription, cycle=statement.cycle,
                source_key=statement.source_key, period_start=statement.period_start,
                period_end=statement.period_end, total_centavos=1, is_test=True, issuer={},
                bill_to={}, pdf_key="k", pdf_sha256="0" * 64, pdf_size=1, issued_at=AT,
            )


class RefusalTests(IssueCase):
    def refused(self, code, payment):
        with self.assertRaises(issue.Refused) as raised:
            issue.issue_for_payment(payment, now=AT)
        self.assertEqual(raised.exception.code, code)
        self.assertFalse(Statement.objects.exists())
        self.assertFalse(StatementNumberSeries.objects.exists())

    def test_no_verified_issuer_refuses_and_writes_nothing(self):
        payment = self.paid()
        from apps.statements.models import IssuerProfile
        with mock.patch("apps.statements.services.current_issuer", return_value=None):
            self.refused("no_issuer", payment)
        self.assertTrue(IssuerProfile.objects.exists())

    def test_an_issuer_with_an_empty_mandatory_field_refuses(self):
        make_issuer(verified=True, tin="")
        self.refused("issuer_incomplete", self.paid())

    def test_an_unconfirmed_payment_refuses(self):
        payment = submit(make_active(), AT)
        self.refused("not_confirmed", payment)

    def test_an_unknown_plan_refuses(self):
        payment = self.paid()
        payment.plan_code = "mystery"
        with mock.patch.dict(issue.KINDS, clear=True):
            self.refused("unknown_plan", payment)

    def test_lines_that_do_not_add_to_the_payment_refuse(self):
        payment = self.paid()
        with mock.patch.object(issue, "_lines", return_value=([("base", "d", 1, 1, 1, "x")], [])):
            self.refused("total_mismatch", payment)

    def test_switch_off_issues_nothing(self):
        payment = self.paid()
        with override_settings(BILLING_STATEMENTS_ENABLED=False):
            self.assertIsNone(issue.issue_for_payment(payment, now=AT))
            self.assertEqual(issue.ensure_statements(), 0)
        self.assertFalse(Statement.objects.exists())


class SafetyNetTests(IssueCase):
    def test_the_net_issues_for_confirmed_payments_without_a_statement(self):
        first, second = self.paid(), self.paid(make_active(email="b@example.test", name="b-store"))
        issue.issue_for_payment(first, now=AT)
        self.assertEqual(issue.missing_statements(), 1)
        self.assertEqual(issue.ensure_statements(), 1)
        self.assertEqual(issue.missing_statements(), 0)
        self.assertEqual(Statement.objects.count(), 2)
        self.assertEqual(issue.ensure_statements(), 0)
        self.assertTrue(Statement.objects.filter(source_key=f"payment:{second.public_id}").exists())

    def test_one_bad_payment_does_not_stop_the_rest_and_nothing_personal_is_logged(self):
        bad = self.paid()
        good = self.paid(make_active(email="c@example.test", name="c-store"))
        real = issue.issue_for_payment

        def pick(payment, now=None):
            if payment.pk == bad.pk:
                raise RuntimeError("boom c@example.test")
            return real(payment, now=now)

        with mock.patch.object(issue, "issue_for_payment", side_effect=pick):
            with self.assertLogs("apps.statements.issue", level="INFO") as logs:
                self.assertEqual(issue.ensure_statements(), 1)
        self.assertTrue(Statement.objects.filter(source_key=f"payment:{good.public_id}").exists())
        self.assertFalse(Statement.objects.filter(source_key=f"payment:{bad.public_id}").exists())
        self.assertNotIn("c-store", " ".join(logs.output))

    def test_a_refusal_is_retried_on_the_next_run(self):
        self.paid()
        with mock.patch.object(documents, "store_document", side_effect=documents.StoreUnavailable):
            self.assertEqual(issue.ensure_statements(), 0)
        self.assertEqual(issue.ensure_statements(), 1)

    def test_the_task_runs_the_net(self):
        self.paid()
        self.assertEqual(tasks.ensure(), 1)

    def test_a_confirmation_never_fails_because_of_statements(self):
        # Confirming does not call the issuer at all in this PR: the statement is the net's job.
        payment = self.paid()
        self.assertEqual(payment.status, "confirmed")
        self.assertFalse(Statement.objects.exists())


class PlaceholderStoreTests(TestCase):
    def test_the_placeholder_store_is_unavailable(self):
        with self.assertRaises(documents.StoreUnavailable):
            documents.store_document({})


@override_settings(**ON)
class ConcurrentIssueTests(TransactionTestCase):
    """Two issues of one payment at the same moment make one statement and use one number.
    Needs row locks, so it runs on PostgreSQL only."""

    def test_concurrent_issue_is_one_number(self):
        if connection.vendor != "postgresql":
            self.skipTest("needs PostgreSQL row locks")
        with transfers_on(), mock.patch.object(documents, "store_document", side_effect=fake_store):
            make_issuer(verified=True)
            operator = make_operator()
            subscription = make_active()
            payment = submit(subscription, AT)
            payment = billing.confirm_transfer(
                payment, operator, amount_received_centavos=payment.amount_centavos, now=AT
            )
            results, errors = [], []
            barrier = threading.Barrier(2)

            def run():
                try:
                    barrier.wait()
                    results.append(issue.issue_for_payment(payment, now=AT).number)
                except Exception as error:  # noqa: BLE001
                    errors.append(error)
                finally:
                    connections.close_all()

            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(results, ["NOBS-2026-000001", "NOBS-2026-000001"])
            self.assertEqual(Statement.objects.count(), 1)
            self.assertEqual(StatementNumberSeries.objects.get(prefix="NOBS").last_value, 1)
