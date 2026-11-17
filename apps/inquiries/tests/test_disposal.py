"""Account and site disposal blank the connection token and the customer names; reply rows follow
their own retention rule; the deploy check and the tables guard hold."""
from datetime import timedelta
from unittest import mock

from django.core.checks import run_checks
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts import retention as rules
from apps.accounts.tests.disposal_base import EMAIL, DisposalTestCase
from apps.common import retention
from apps.inquiries import retention as inquiry_retention
from apps.inquiries.models import InquiryConnection, InquiryConversation, InquiryReply
from apps.inquiries.tests.base import KEY, TOKEN, make_connection, make_stored, switched_on
from apps.inquiries.switch import tables_exist
from apps.sites.models import Site

RULE = "never_verified_accounts"


@switched_on
@override_settings(RETENTION_ENABLED=True)
class InquiryDisposalTests(DisposalTestCase):
    def setUp(self):
        super().setUp()
        self.mine = Site.objects.get(account=self.account)
        self.theirs = Site.objects.get(account=self.other)
        for site in (self.mine, self.theirs):
            make_connection(site)
            conversation = make_stored(site)
            InquiryReply.objects.create(
                site=site, conversation=conversation, author=site.account, idempotency_key="key-12345678", body_length=5,
            )

    def dispose(self, **kw):
        return rules.dispose_account(self.account, now=self.now, rule=RULE, **kw)

    def test_a_dry_run_counts_and_changes_nothing(self):
        counts = self.dispose(dry_run=True)
        self.assertEqual((counts["inquiry_connections"], counts["inquiry_conversations"]), (1, 1))
        self.assertEqual(InquiryConnection.objects.exclude(token_encrypted="").count(), 2)
        self.assertEqual(InquiryConversation.objects.exclude(display_name="").count(), 2)

    def test_a_disposal_blanks_the_token_and_the_customer_name_only(self):
        counts = self.dispose()
        self.assertEqual((counts["inquiry_connections"], counts["inquiry_conversations"]), (1, 1))
        mine = InquiryConnection.objects.get(site=self.mine)
        self.assertEqual((mine.token_encrypted, mine.status), ("", "unset"))
        self.assertEqual(InquiryConversation.objects.get(site=self.mine).display_name, "")
        self.assertEqual(InquiryReply.objects.filter(site=self.mine).count(), 1)  # a length and a code, no personal data

    def test_another_accounts_rows_are_untouched(self):
        self.dispose()
        theirs = InquiryConnection.objects.get(site=self.theirs)
        self.assertNotEqual(theirs.token_encrypted, "")
        self.assertEqual(theirs.status, "active")
        self.assertEqual(InquiryConversation.objects.get(site=self.theirs).display_name, "Maria Santos")

    def test_it_runs_with_the_switch_off_and_the_audit_names_the_fields(self):
        with override_settings(INQUIRIES_ENABLED=False):
            self.dispose()
        self.assertEqual(InquiryConnection.objects.get(site=self.mine).token_encrypted, "")
        from apps.operators.models import OperatorAuditEntry

        entry = OperatorAuditEntry.objects.filter(action="account_disposed").latest("pk")
        self.assertEqual(entry.detail["blanked"]["inquiry_connections"], ["token_encrypted"])
        self.assertEqual(
            entry.detail["blanked"]["inquiry_conversations"],
            ["display_name", "contact_name", "contact_email", "visitor_hash", "thread_token"],
        )
        self.assertEqual(entry.detail["blanked"]["inquiry_messages"], ["all rows deleted"])
        self.assertNotIn(TOKEN, str(entry.detail))
        self.assertNotIn(EMAIL, str(entry.detail))

    def test_a_site_erasure_dry_run_counts_the_rows(self):
        from apps.lifecycle import dry_run

        counts, _ = dry_run.planned_counts(self.mine)
        self.assertEqual((counts["inquiry_connections"], counts["inquiry_conversations"]), (1, 1))

    def test_before_the_tables_exist_disposal_skips_them(self):
        with mock.patch("apps.inquiries.retention.tables_exist", return_value=False):
            self.assertEqual(inquiry_retention.blank_inquiries_of(self.account), {})
            self.dispose()
        self.assertNotEqual(InquiryConnection.objects.get(site=self.mine).token_encrypted, "")


@switched_on
class ReplyRetentionTests(DisposalTestCase):
    def setUp(self):
        super().setUp()
        site = Site.objects.get(account=self.account)
        conversation = make_stored(site)
        self.old = InquiryReply.objects.create(
            site=site, conversation=conversation, author=self.account, idempotency_key="key-old-0001", body_length=3,
        )
        self.new = InquiryReply.objects.create(
            site=site, conversation=conversation, author=self.account, idempotency_key="key-new-0001", body_length=3,
        )
        InquiryReply.objects.filter(pk=self.old.pk).update(created_at=timezone.now() - timedelta(days=366))

    def test_old_reply_rows_go_only_while_both_switches_are_on(self):
        with override_settings(RETENTION_ENABLED=True):
            self.assertEqual(retention.run_all(only=["inquiry_replies"], dry_run=True).counts["inquiry_replies"], 1)
            with override_settings(INQUIRIES_ENABLED=False):
                retention.run_all(only=["inquiry_replies"])
            self.assertEqual(InquiryReply.objects.count(), 2)
            retention.run_all(only=["inquiry_replies"])
        self.assertEqual(list(InquiryReply.objects.values_list("pk", flat=True)), [self.new.pk])

    def test_nothing_is_deleted_with_retention_off(self):
        with override_settings(RETENTION_ENABLED=False):
            retention.run_all(only=["inquiry_replies"])
        self.assertEqual(InquiryReply.objects.count(), 2)


class DeployCheckTests(TestCase):
    def ids(self):
        return [m.id for m in run_checks(include_deployment_checks=True)]

    def test_the_deploy_check_refuses_a_switch_without_a_key(self):
        with override_settings(INQUIRIES_ENABLED=True, INQUIRIES_CREDENTIALS_KEY=""):
            self.assertIn("inquiries.E001", self.ids())
        with override_settings(INQUIRIES_ENABLED=True, INQUIRIES_CREDENTIALS_KEY="not-a-key"):
            self.assertIn("inquiries.E001", self.ids())
        with override_settings(INQUIRIES_ENABLED=True, INQUIRIES_CREDENTIALS_KEY=KEY):
            self.assertNotIn("inquiries.E001", self.ids())
        with override_settings(INQUIRIES_ENABLED=False, INQUIRIES_CREDENTIALS_KEY=""):
            self.assertNotIn("inquiries.E001", self.ids())

    def test_the_switch_is_off_by_default_and_the_tables_guard_sees_the_tables(self):
        from django.conf import settings

        self.assertFalse(settings.INQUIRIES_ENABLED)
        self.assertTrue(tables_exist())

    def test_the_tables_guard_answers_false_when_the_table_is_absent(self):
        with mock.patch("apps.inquiries.switch.connection.introspection.table_names", return_value=["other"]):
            self.assertFalse(tables_exist())
