"""Task 2 of plan 8: a STOP reply lists the sender as do-not-text. Every number is made up."""
from unittest import mock

from django.test import override_settings

from apps.inbox import storing
from apps.inbox.models import ConversationMessage, DoNotText
from apps.inbox.tests.base import SENDER, InboxTestCase
from apps.inbox.tests.test_storing import TOKEN, text
from apps.notifications import sms
from apps.notifications.models import SmsDevice


class StopRuleTests(InboxTestCase):
    def setUp(self):
        super().setUp()
        SmsDevice.objects.filter(pk=self.phone.pk).update(token_hash=sms.token_hash(TOKEN), last_contact_at=None)
        self.n = 0

    def receive(self, body, sender=SENDER):
        self.n += 1
        return storing.store(self.phone, {"messages": [text(ref=f"in_{self.n}", sender=sender, body=body)]})

    def test_1_these_list_the_sender(self):
        for body in ("STOP", "stop", "  Stop  ", "STOP\n", "stop.", "STOP!", "Stop!!", " stop. ", "...STOP..."):
            DoNotText.objects.all().delete()
            self.receive(body)
            self.assertEqual(DoNotText.objects.filter(mobile=SENDER).count(), 1, repr(body))

    def test_2_these_do_not(self):
        for body in ("STOP please", "nostop", "unsubscribe", "end", "", "   ", "st op", "stop stop"):
            self.receive(body or " ")
        self.receive("stop", sender="BDO")
        self.assertEqual(DoNotText.objects.count(), 0)

    def test_3_twice_lists_once_and_the_text_is_stored(self):
        self.receive("STOP")
        self.receive("Stop.")
        self.assertEqual(DoNotText.objects.count(), 1)
        self.assertEqual(ConversationMessage.objects.filter(body__in=["STOP", "Stop."]).count(), 2)

    def test_4_written_with_the_bulk_switch_off(self):
        with override_settings(SMS_BULK_ENABLED=False):
            self.receive("STOP")
        self.assertEqual(DoNotText.objects.count(), 1)

    def test_5_with_the_table_missing_the_text_is_still_stored(self):
        with mock.patch.object(storing, "dnt_tables_exist", return_value=False):
            self.receive("STOP")
        self.assertEqual(DoNotText.objects.count(), 0)
        self.assertEqual(ConversationMessage.objects.count(), 1)

    def test_6_the_number_is_in_no_log_line(self):
        with self.assertNoLogs(level="DEBUG"):
            self.receive("STOP")
