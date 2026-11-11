"""The inbox's retention rule: 365 days, wherever the tables exist, switch on or off."""
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from apps.common import retention
from apps.inbox import storing
from apps.inbox.models import Conversation, ConversationMessage
from apps.inbox.retention import inbox_messages
from apps.inbox.tests.base import SENDER, InboxTestCase
from apps.notifications import retention as notifications_retention
from apps.operators.models import OperatorAuditEntry

inbox_off = override_settings(SMS_INBOX_ENABLED=False)


class InboxRetentionTests(InboxTestCase):
    def setUp(self):
        super().setUp()
        self.count = 0
        self.now = timezone.now()

    def message(self, days, sender=SENDER):
        """One stored text, aged by this many days."""
        self.count += 1
        ref = f"in_{self.count}"
        storing.store(self.phone, {"messages": [{"id": ref, "from": sender, "text": "hello"}]})
        ConversationMessage.objects.filter(gateway_ref=ref).update(created_at=self.now - timedelta(days=days))
        return ref

    def refs(self):
        return sorted(ConversationMessage.objects.values_list("gateway_ref", flat=True))

    def test_1_the_rule_is_registered_and_daily(self):
        self.assertIn("inbox_messages", retention.names())
        self.assertIn("inbox_messages", notifications_retention.DAILY_RULES)

    def test_2_a_message_older_than_365_days_is_deleted(self):
        self.message(366)
        young = self.message(364)
        self.assertEqual(inbox_messages(self.now, False), 1)
        self.assertEqual(self.refs(), [young])

    def test_3_a_conversation_left_empty_is_deleted(self):
        self.message(366)
        self.message(366, sender="+639170000001")
        young = self.message(1, sender="+639170000001")
        inbox_messages(self.now, False)
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.contact, "+639170000001")
        self.assertEqual(list(conversation.messages.values_list("gateway_ref", flat=True)), [young])

    def test_4_the_dry_run_changes_nothing(self):
        self.message(366)
        self.assertEqual(inbox_messages(self.now, True), 1)
        self.assertEqual((ConversationMessage.objects.count(), Conversation.objects.count()), (1, 1))

    def test_5_the_rule_still_deletes_while_the_inbox_is_off(self):
        self.message(366)
        young = self.message(364, sender="+639170000001")
        with inbox_off:
            self.assertEqual(inbox_messages(self.now, False), 1)
        self.assertEqual(self.refs(), [young])
        self.assertEqual(list(Conversation.objects.values_list("contact", flat=True)), ["+639170000001"])

    def test_5b_the_dry_run_counts_while_the_inbox_is_off(self):
        self.message(366)
        with inbox_off:
            self.assertEqual(inbox_messages(self.now, True), 1)
        self.assertEqual(ConversationMessage.objects.count(), 1)

    def test_5c_with_no_table_the_rule_does_nothing(self):
        old = self.message(366)
        with mock.patch("apps.inbox.retention.tables_exist", return_value=False):
            for dry_run in (False, True):
                with self.assertNumQueries(0):
                    self.assertEqual(inbox_messages(self.now, dry_run), 0)
                with inbox_off, self.assertNumQueries(0):
                    self.assertEqual(inbox_messages(self.now, dry_run), 0)
        self.assertEqual(self.refs(), [old])

    def test_5d_the_table_check_is_one_query(self):
        with self.assertNumQueries(2):  # the table names, then the count
            self.assertEqual(inbox_messages(self.now, True), 0)

    @override_settings(RETENTION_ENABLED=False)
    def test_6_it_is_not_behind_retention_enabled(self):
        self.message(366)
        report = retention.run_all(only=["inbox_messages"])
        self.assertEqual((report.counts, report.switched_off), ({"inbox_messages": 1}, []))
        self.assertEqual(ConversationMessage.objects.count(), 0)

    def test_7_the_daily_run_includes_it(self):
        self.message(366)
        report = notifications_retention.run_daily_rules()
        self.assertEqual(report.counts["inbox_messages"], 1)
        entry = OperatorAuditEntry.objects.get(action="retention_run")
        self.assertEqual(entry.detail["counts"]["inbox_messages"], 1)
