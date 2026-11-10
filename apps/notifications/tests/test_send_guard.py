"""A disposed contact is never emailed."""
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts import retention as account_rules
from apps.accounts.models import Account
from apps.accounts.tests.base import EMAIL, ApiTestCase
from apps.common.disposal import DISPOSED_DOMAIN, disposed_address, is_disposed_address
from apps.notifications import retention
from apps.notifications.models import EmailMessage
from apps.notifications.services import EmailNotSent, send_email

PLACEHOLDER = "deleted-7@deleted.invalid"


class DisposedAddressTests(TestCase):
    def test_the_placeholder_is_recognised_whatever_its_case_or_spacing(self):
        self.assertEqual(DISPOSED_DOMAIN, "deleted.invalid")
        self.assertEqual(disposed_address(7), PLACEHOLDER)
        for address in (PLACEHOLDER, " Deleted-7@DELETED.invalid ", "anything@deleted.invalid"):
            self.assertTrue(is_disposed_address(address), address)

    def test_a_real_address_is_not(self):
        for address in (EMAIL, "ana@notdeleted.invalid", "deleted.invalid@example.test", "", None):
            self.assertFalse(is_disposed_address(address), address)


class SendGuardTests(TestCase):
    def test_a_disposed_address_is_refused_with_no_email_and_no_record(self):
        with self.assertLogs("apps.notifications.services", level="WARNING") as logs:
            with self.assertRaises(EmailNotSent):
                send_email(to=PLACEHOLDER, category="signin_code", subject="s", body="b")
        self.assertEqual(mail.outbox, [])
        self.assertFalse(EmailMessage.objects.exists())
        self.assertIn("signin_code", logs.output[0])
        self.assertNotIn("deleted-7", logs.output[0])

    def test_any_other_address_is_sent_as_before(self):
        send_email(to=EMAIL, category="signin_code", subject="s", body="b")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EmailMessage.objects.get().status, EmailMessage.Status.SENT)


@override_settings(RETENTION_ENABLED=True)
class DisposedContactTests(ApiTestCase):
    """The whole path: an account signs in, is disposed of, and can no longer be reached."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.sign_in().status_code, 200)
        self.account = Account.objects.get(email=EMAIL)
        self.assertEqual(self.get("/v1/me/").status_code, 200)
        account_rules.dispose_account(self.account, now=timezone.now(), rule="request")
        self.account.refresh_from_db()
        mail.outbox.clear()

    def test_the_disposed_account_cannot_be_emailed(self):
        self.assertTrue(is_disposed_address(self.account.email))
        with self.assertLogs("apps.notifications.services", level="WARNING"):
            with self.assertRaises(EmailNotSent):
                send_email(to=self.account.email, category="signin_code", subject="s", body="b")
        self.assertEqual(mail.outbox, [])
        self.assertFalse(EmailMessage.objects.exists())

    def test_its_old_session_is_refused(self):
        self.assertEqual(self.get("/v1/me/").status_code, 401)

    def test_asking_for_a_sign_in_code_for_the_placeholder_sends_nothing(self):
        with self.assertLogs("apps.notifications.services", level="WARNING"):
            response = self.post("/v1/auth/email-code/request/", {"email": self.account.email})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(EmailMessage.objects.exists())

    def test_a_new_sign_up_with_the_original_address_is_emailed_normally(self):
        response = self.sign_in()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox[-1].to, [EMAIL])
        fresh = Account.objects.get(email=EMAIL)
        self.assertNotEqual(fresh.pk, self.account.pk)
        self.assertIsNone(fresh.closed_at)
        self.assertEqual(Account.objects.count(), 2)


class SendRecordsOfAnAddressTests(TestCase):
    def setUp(self):
        for recipient, category in (
            (EMAIL, "signin_code"),
            (EMAIL, "email_changed_notice"),
            ("Ana@Example.Test", "signin_code"),
            (EMAIL, "admin_signin_code"),
            (EMAIL, "admin_lockout"),
            ("ben@example.test", "signin_code"),
        ):
            EmailMessage.objects.create(recipient=recipient, category=category)

    def left(self):
        return sorted(EmailMessage.objects.values_list("recipient", "category"))

    def test_the_records_of_the_address_are_deleted_except_the_admin_ones(self):
        self.assertEqual(retention.delete_records_for(EMAIL), 3)
        self.assertEqual(
            self.left(),
            [
                (EMAIL, "admin_lockout"),
                (EMAIL, "admin_signin_code"),
                ("ben@example.test", "signin_code"),
            ],
        )

    def test_the_dry_run_counts_the_same_and_deletes_nothing(self):
        self.assertEqual(retention.delete_records_for(EMAIL, dry_run=True), 3)
        self.assertEqual(EmailMessage.objects.count(), 6)

    def test_the_export_lists_the_same_records_without_the_address_of_anyone_else(self):
        records = retention.export_records_for(EMAIL)
        self.assertEqual(
            sorted(record["category"] for record in records),
            ["email_changed_notice", "signin_code", "signin_code"],
        )
        self.assertEqual(
            sorted(records[0]), ["category", "created_at", "sent_at", "status"]
        )
        self.assertEqual(EmailMessage.objects.count(), 6)
