"""Delivering a statement by email (Billing Build B5, PR 8; plan 7.4, C12, Q4)."""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.notifications.models import EmailMessage
from apps.notifications.services import EmailNotSent
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff
from apps.statements import delivery, issue, tasks
from apps.statements.models import BillingProfile, LABEL, Statement, StatementDelivery
from apps.statements.tests.base import WHEN, make_staff, make_statement, new_subscription
from apps.statements.tests.test_issue import IssueCase
from apps.statements.tests.test_pdf import BANNED

ON = dict(BILLING_ENABLED=True, BILLING_STATEMENTS_ENABLED=True)
DASHBOARD = "https://dash.example.test"


def make(**fields):
    values = dict(
        bill_to={"name": "Ana Cruz", "email": "x@example.test"}, period_start=WHEN,
        period_end=WHEN + timedelta(days=30), total_centavos=249900,
    )
    values.update(fields)
    return make_statement(new_subscription(), **values)


@override_settings(DASHBOARD_URL=DASHBOARD, **ON)
class DeliverTests(TestCase):
    def test_the_rendered_subject_and_body_are_the_approved_wording(self):
        statement = make(number="NOBS-2026-000123")
        site = statement.subscription.site

        row = delivery.deliver(statement)

        self.assertEqual(row.status, "sent")
        self.assertIsNotNone(row.sent_at)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, "Your NegosyoOnline billing statement NOBS-2026-000123")
        self.assertEqual(
            message.body,
            f"Hello Ana Cruz, your billing statement for {site.display_name} is ready: total "
            f"PHP 2,499.00 for 25 November 2026 to 25 December 2026. You can view and download "
            f"it on your Billing page: {DASHBOARD}/settings/billing/. "
            f"Internal Billing Statement — Not an Official BIR Receipt or BIR Electronic "
            f"Invoice. NegosyoOnline",
        )
        self.assertIn(LABEL, message.body)
        for banned in BANNED:
            self.assertNotIn(banned, message.subject + message.body)
        self.assertEqual(message.attachments, [])
        self.assertEqual(EmailMessage.objects.get().category, "billing_statement")

    def test_goes_to_the_billing_email_else_the_account_email(self):
        with_profile, without = make(), make()
        BillingProfile.objects.create(
            site=with_profile.subscription.site, company_name="Ana Co",
            billing_email="billing@example.test",
        )

        delivery.deliver(with_profile)
        delivery.deliver(without)

        self.assertEqual(mail.outbox[0].to, ["billing@example.test"])
        self.assertEqual(mail.outbox[1].to, [without.subscription.site.account.email])

    def test_one_row_per_attempt_with_its_key_and_a_sent_statement_is_not_sent_again(self):
        statement = make()

        delivery.deliver(statement)

        self.assertIsNone(delivery.deliver(statement))
        row = statement.deliveries.get()
        self.assertEqual(row.idempotency_key, f"deliver:{statement.public_id}:1")
        self.assertEqual(row.attempt, 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_failure_keeps_a_code_not_a_message(self):
        statement = make()
        with mock.patch.object(delivery, "send_email", side_effect=EmailNotSent()):
            row = delivery.deliver(statement)
        self.assertEqual((row.status, row.error_code), ("failed", "email_not_sent"))

        with mock.patch.object(delivery, "send_email", side_effect=RuntimeError("secret smtp")):
            with self.assertLogs("apps.statements.delivery", "ERROR"):
                row = delivery.deliver(statement)
        self.assertEqual((row.status, row.error_code), ("failed", "error"))
        self.assertEqual([r.attempt for r in statement.deliveries.order_by("attempt")], [1, 2])

    def test_a_disposed_address_is_never_mailed(self):
        statement = make()
        BillingProfile.objects.create(
            site=statement.subscription.site, company_name="Gone",
            billing_email="deleted-3@deleted.invalid",
        )

        row = delivery.deliver(statement)

        self.assertEqual((row.status, row.error_code), ("failed", "disposed_address"))
        self.assertEqual(mail.outbox, [])
        self.assertEqual(delivery.due(), [])  # a retry cannot cure it

    def test_nothing_is_sent_or_written_while_the_switch_is_off(self):
        statement = make()
        with override_settings(BILLING_STATEMENTS_ENABLED=False):
            self.assertIsNone(delivery.deliver(statement))
            self.assertEqual(tasks.deliver(), 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(StatementDelivery.objects.exists())

    def test_an_adjustment_is_not_emailed(self):
        original = make()
        adjustment = make(number="NOCN-2026-000001", document_type="adjustment", original=original, total_centavos=-100)
        self.assertIsNone(delivery.deliver(adjustment))


@override_settings(DASHBOARD_URL=DASHBOARD, **ON)
class RetryTests(TestCase):
    def fail(self, statement, times):
        with mock.patch.object(delivery, "send_email", side_effect=EmailNotSent()):
            for _ in range(times):
                delivery.deliver(statement, manual=True)

    def age(self, statement, delta):
        StatementDelivery.objects.filter(statement=statement).update(
            created_at=timezone.now() - delta
        )

    def test_an_unsent_statement_is_picked_up_and_sent(self):
        statement = make()
        self.assertEqual(tasks.deliver(), 1)
        self.assertEqual(statement.deliveries.get().status, "sent")
        self.assertEqual(tasks.deliver(), 0)

    def test_a_failed_attempt_waits_for_its_backoff_then_retries(self):
        statement = make()
        self.fail(statement, 1)
        self.assertEqual(delivery.due(), [])
        self.age(statement, delivery.BACKOFF[0] + timedelta(seconds=1))
        self.assertEqual(delivery.due(), [statement])

        self.assertEqual(tasks.deliver(), 1)

        self.assertEqual(statement.deliveries.get(attempt=2).status, "sent")
        self.assertEqual(delivery.due(), [])

    def test_the_backoff_grows(self):
        statement = make()
        self.fail(statement, 3)
        self.age(statement, delivery.BACKOFF[1] + timedelta(seconds=1))
        self.assertEqual(delivery.due(), [])  # attempt 3 waits the third wait
        self.age(statement, delivery.BACKOFF[2] + timedelta(seconds=1))
        self.assertEqual(delivery.due(), [statement])

    def test_five_attempts_in_all_then_it_stays_failed(self):
        statement = make()
        self.fail(statement, 5)
        self.age(statement, timedelta(days=30))

        self.assertEqual(delivery.due(), [])
        self.assertIsNone(delivery.deliver(statement))
        self.assertEqual(statement.deliveries.count(), 5)
        self.assertEqual(statement.deliveries.filter(status="failed").count(), 5)

    def test_the_task_is_registered_and_the_safety_net_makes_the_first_attempt(self):
        self.assertEqual(tasks.deliver.name, "statements.deliver")


@on_staff
@override_settings(DASHBOARD_URL=DASHBOARD, **ON)
class ResendTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.operator = self.enrolled().user

    def post(self, path, data=None, **kwargs):
        return self.call("POST", "/staff/v1/finance/statements/" + path, data, **kwargs)

    def statement_mail(self):
        return [m for m in mail.outbox if m.subject.startswith("Your NegosyoOnline billing")]

    def test_a_resend_sends_again_and_is_audited_without_the_address(self):
        statement = make()
        delivery.deliver(statement)
        self.staff_sign_in()

        response = self.post("resend/", {"statement": statement.public_id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["attempt"], 2)
        self.assertEqual(response.json()["status"], "sent")
        self.assertEqual(len(self.statement_mail()), 2)
        entry = OperatorAuditEntry.objects.get(action="billing_statement_resent")
        self.assertEqual(entry.target_id, statement.public_id)
        self.assertEqual(entry.detail["attempt"], 2)
        self.assertNotIn("@", str(entry.detail))

    def test_a_resend_after_five_failures_still_goes(self):
        statement = make()
        with mock.patch.object(delivery, "send_email", side_effect=EmailNotSent()):
            for _ in range(5):
                delivery.deliver(statement, manual=True)
        self.staff_sign_in()

        response = self.post("resend/", {"statement": statement.public_id})

        self.assertEqual(response.json()["attempt"], 6)
        self.assertEqual(response.json()["status"], "sent")

    def test_failed_deliveries_show_in_the_list_and_the_detail(self):
        failed, sent, untouched = make(), make(), make()
        with mock.patch.object(delivery, "send_email", side_effect=EmailNotSent()):
            delivery.deliver(failed)
        delivery.deliver(sent)
        self.staff_sign_in()

        everything = self.post("list/", {}).json()["items"]
        by_id = {i["statement"]: i["delivery"] for i in everything}
        self.assertEqual(by_id[failed.public_id]["status"], "failed")
        self.assertEqual(by_id[failed.public_id]["error_code"], "email_not_sent")
        self.assertEqual(by_id[sent.public_id]["status"], "sent")
        self.assertIsNone(by_id[untouched.public_id])

        only = self.post("list/", {"delivery_failed": True}).json()["items"]
        self.assertEqual([i["statement"] for i in only], [failed.public_id])
        detail = self.post("detail/", {"statement": failed.public_id}).json()
        self.assertEqual(detail["deliveries"][0]["status"], "failed")

    def test_gates_adjustments_unknown_ids_and_switch_off(self):
        statement = make()
        self.assertError(self.post("resend/", {"statement": statement.public_id}), 401, "unauthenticated")
        self.staff_sign_in()
        adjustment = make(number="NOCN-2026-000009", document_type="adjustment", original=statement, total_centavos=-100)
        self.assertError(self.post("resend/", {"statement": adjustment.public_id}), 400, "invalid_request")
        self.assertError(self.post("resend/", {"statement": "stm_nope"}), 404, "not_found")
        self.assertError(self.post("resend/", {}), 400, "invalid_request")
        with override_settings(BILLING_STATEMENTS_ENABLED=False):
            self.assertError(self.post("resend/", {"statement": statement.public_id}), 404, "not_found")
        self.assertFalse(OperatorAuditEntry.objects.filter(action="billing_statement_resent"))
        self.assertEqual(self.statement_mail(), [])


class AfterIssueTests(IssueCase):
    def test_the_safety_net_mails_each_new_statement_once(self):
        payment = self.paid()

        self.assertEqual(issue.ensure_statements(), 1)

        statement = Statement.objects.get(source_key=issue.source_key(payment))
        self.assertEqual(statement.deliveries.get().status, "sent")
        self.assertEqual(
            [m.subject for m in mail.outbox if "billing statement" in m.subject],
            [f"Your NegosyoOnline billing statement {statement.number}"],
        )
        issue.ensure_statements()
        self.assertEqual(statement.deliveries.count(), 1)
