"""Storing an incoming text: the gateway's `received` call with the inbox on."""
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.inbox.models import Conversation, ConversationMessage
from apps.inbox.tests.base import SENDER, InboxTestCase
from apps.notifications import gateway, sms
from apps.notifications.models import SmsDevice
from apps.operators.models import OperatorAuditEntry

TOKEN = "token-of-the-inbox-phone"
LOGGER = "apps.inbox.storing"


def text(ref="in_1", sender=SENDER, body="Is my order ready?", **fields):
    item = {"id": ref, "from": sender, "text": body, "received_at": "2026-11-11T02:03:04.567Z", "sim_slot": 0}
    item.update(fields)
    return item


def without(key, **fields):
    item = text(**fields)
    del item[key]
    return item


class ReceivedStoringTests(InboxTestCase):
    def setUp(self):
        super().setUp()
        SmsDevice.objects.filter(pk=self.phone.pk).update(token_hash=sms.token_hash(TOKEN), last_contact_at=None)

    def post(self, raw):
        return self.client.post(
            "/gateway/v1/received", data=raw, content_type="application/json", secure=True,
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}", HTTP_X_REAL_IP="203.0.113.7",
        )

    def call(self, *items):
        return self.post(json.dumps({"messages": list(items)}))

    def stored(self, *items):
        response = self.call(*items)
        self.assertEqual((response.status_code, response.content), (204, b""))

    def assert_no_inbox_row(self):
        self.assertEqual((Conversation.objects.count(), ConversationMessage.objects.count()), (0, 0))

    def assert_nothing_kept_and_no_inbox_table_read(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.call(text())
        self.assertEqual((response.status_code, response.content), (204, b""))
        self.assertEqual(len(queries), 1, queries.captured_queries)  # the phone, found by its token
        self.assertNotIn("inbox_", queries[0]["sql"])
        self.assert_no_inbox_row()

    def test_1_inbox_switch_off_keeps_nothing_and_reads_no_inbox_table(self):
        with override_settings(SMS_INBOX_ENABLED=False):
            self.assert_nothing_kept_and_no_inbox_table_read()

    def test_2_phone_switch_off_keeps_nothing(self):
        SmsDevice.objects.update(forwards_incoming=False)
        self.assert_nothing_kept_and_no_inbox_table_read()

    def test_3_a_valid_list_is_stored(self):
        self.stored(text())
        conversation = Conversation.objects.get()
        self.assertEqual(
            (conversation.owner_kind, conversation.site, conversation.account, conversation.platform),
            ("hq", None, None, "sms"),
        )
        self.assertEqual(
            (conversation.line, conversation.contact, conversation.can_reply, conversation.unread_count),
            (self.phone, SENDER, True, 1),
        )
        message = ConversationMessage.objects.get()
        self.assertEqual(
            (message.conversation, message.direction, message.body, message.gateway_ref, message.device),
            (conversation, "in", "Is my order ready?", "in_1", self.phone),
        )
        self.assertEqual((message.sim_slot, message.author, message.kind, message.platform), (0, None, "", "sms"))
        self.assertEqual(message.clipped, False)
        phone_time = datetime(2026, 11, 11, 2, 3, 4, 567000, tzinfo=dt_timezone.utc)
        self.assertEqual((message.occurred_at, conversation.last_message_at), (phone_time, phone_time))

    def test_4_a_repeat_of_the_same_ids_stores_nothing_new(self):
        self.stored(text())
        self.stored(text())
        self.assertEqual(ConversationMessage.objects.count(), 1)
        self.assertEqual(Conversation.objects.get().unread_count, 1)

    def test_5_two_texts_from_one_sender_share_a_conversation(self):
        later = "2026-11-11T05:00:00Z"
        self.stored(text("in_1", received_at=later), text("in_2", body="Hello?"))
        conversation = Conversation.objects.get()
        self.assertEqual((conversation.messages.count(), conversation.unread_count), (2, 2))
        self.assertEqual(conversation.last_message_at, datetime(2026, 11, 11, 5, tzinfo=dt_timezone.utc))

    def test_6_every_form_of_a_philippine_mobile_is_one_contact(self):
        self.stored(
            text("in_1", sender="09189990000"), text("in_2", sender="639189990000"),
            text("in_3", sender="+63 918 999 0000"),
        )
        conversation = Conversation.objects.get()
        self.assertEqual((conversation.contact, conversation.messages.count()), (SENDER, 3))

    def test_7_a_sender_that_is_not_a_mobile_is_kept_as_received(self):
        self.stored(text("in_1", sender="GLOBE"), text("in_2", sender="  " + "N" * 40 + " "))
        self.assertEqual(
            sorted(Conversation.objects.values_list("contact", "can_reply")),
            [("GLOBE", False), ("N" * 32, False)],
        )

    def test_8_a_long_text_is_cut_and_marked(self):
        self.stored(text(body="x" * 2500))
        message = ConversationMessage.objects.get()
        self.assertEqual((len(message.body), message.clipped), (2000, True))

    def test_9_the_owner_is_hq_whatever_the_body_says(self):
        self.stored(text(owner_kind="site", site=1, account=1, platform="email"))
        conversation = Conversation.objects.get()
        self.assertEqual(
            (conversation.owner_kind, conversation.site, conversation.account, conversation.platform),
            ("hq", None, None, "sms"),
        )
        self.assertEqual(ConversationMessage.objects.get().platform, "sms")

    def test_10_a_body_that_is_not_a_list_of_texts_stores_nothing_and_answers_400(self):
        bodies = (
            "not json", json.dumps([text()]), json.dumps({}), json.dumps({"messages": "x"}),
            json.dumps({"messages": [text(f"in_{n}") for n in range(21)]}),
        )
        for raw in bodies:
            with self.subTest(raw=raw[:30]), self.assertLogs(LOGGER, "ERROR"):
                response = self.post(raw)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_request")
                self.assert_no_inbox_row()

    BAD_ITEMS = (
        "not an object", without("id"), text(ref=7), text(ref=""), text(ref="x" * 101), without("from"),
        text(sender=""), text(sender="   "), without("text"), text(body=7),
    )

    def test_10b_a_bad_item_is_skipped_and_the_rest_are_stored(self):
        for number, bad in enumerate(self.BAD_ITEMS):
            with self.subTest(bad=bad), self.assertLogs(LOGGER, "ERROR"):
                good = text(f"good_{number}", body=f"good {number}")
                self.stored(bad, good)
                self.assertEqual(ConversationMessage.objects.count(), number + 1)
                self.assertTrue(ConversationMessage.objects.filter(gateway_ref=f"good_{number}").exists())
        ConversationMessage.objects.all().delete()
        Conversation.objects.all().delete()
        with self.assertLogs(LOGGER, "ERROR"):
            self.stored(*self.BAD_ITEMS)
        self.assert_no_inbox_row()

    def test_10c_the_same_list_again_stores_nothing_new(self):
        for _ in range(2):
            with self.assertLogs(LOGGER, "ERROR"):
                self.stored(without("from", ref="in_bad"), text())
        self.assertEqual(ConversationMessage.objects.count(), 1)
        self.assertEqual(Conversation.objects.get().unread_count, 1)

    def test_10d_an_empty_text_is_a_text(self):
        self.stored(text(body=""))
        self.assertEqual(ConversationMessage.objects.get().body, "")

    def test_11_skipped_items_log_their_count_only(self):
        with self.assertLogs(LOGGER, "ERROR") as logs:
            self.stored(without("from", ref="in_secret_1"), text(ref="in_secret_2", body=7), text())
        self.assertEqual([record.getMessage() for record in logs.records], ["sms_incoming_skipped count=2"])
        for word in (SENDER, "9990000", "order", "in_secret"):
            self.assertNotIn(word, logs.output[0])

    def test_11b_a_refused_list_logs_its_count_only(self):
        with self.assertLogs(LOGGER, "ERROR") as logs:
            self.post(json.dumps({"messages": [text(f"in_{n}") for n in range(21)]}))
        self.assertEqual([record.getMessage() for record in logs.records], ["sms_incoming_rejected count=21"])
        with self.assertLogs(LOGGER, "ERROR") as logs:
            self.post("not json")
        self.assertEqual([record.getMessage() for record in logs.records], ["sms_incoming_rejected count=0"])

    def test_12_storing_writes_no_log_cache_or_audit_entry(self):
        with mock.patch.object(gateway, "cache", wraps=cache) as gateway_cache:
            with mock.patch.object(sms, "cache", wraps=cache) as sms_cache:
                with self.assertNoLogs(level="DEBUG"):
                    self.stored(text())
        for spied in (gateway_cache, sms_cache):
            for write in ("set", "add", "incr", "set_many"):
                getattr(spied, write).assert_not_called()
        self.assertEqual(OperatorAuditEntry.objects.count(), 0)
        self.assertEqual(ConversationMessage.objects.count(), 1)

    def test_13_an_unreadable_time_becomes_the_servers(self):
        self.stored(
            text("in_1", received_at="yesterday"), without("received_at", ref="in_2"),
            text("in_3", received_at="2026-11-11T02:03:04"), text("in_4", received_at=7),
        )
        now = timezone.now()
        for occurred_at in ConversationMessage.objects.values_list("occurred_at", flat=True):
            self.assertLess(abs(now - occurred_at), timedelta(seconds=10))
        self.assertEqual(ConversationMessage.objects.count(), 4)

    def test_14_an_odd_sim_slot_is_left_empty(self):
        self.stored(
            text("in_1", sim_slot="1"), text("in_2", sim_slot=-1), text("in_3", sim_slot=True),
            text("in_4", sim_slot=12), without("sim_slot", ref="in_5"), text("in_6", sim_slot=1),
        )
        slots = dict(ConversationMessage.objects.values_list("gateway_ref", "sim_slot"))
        self.assertEqual(
            slots, {"in_1": None, "in_2": None, "in_3": None, "in_4": None, "in_5": None, "in_6": 1}
        )

    def test_15_received_still_does_not_count_as_a_poll(self):
        self.stored(text())
        self.assertIsNone(SmsDevice.objects.get().last_contact_at)

    def test_16_nul_characters_are_removed(self):
        with self.assertLogs(LOGGER, "ERROR") as logs:
            self.stored(
                text(ref="in_\x001", sender="GLO\x00BE", body="a\x00b"),
                text(ref="in_2", sender="\x00\x00"),
            )
        self.assertEqual([record.getMessage() for record in logs.records], ["sms_incoming_skipped count=1"])
        message = ConversationMessage.objects.get()
        self.assertEqual((message.body, message.gateway_ref, message.conversation.contact), ("ab", "in_1", "GLOBE"))
