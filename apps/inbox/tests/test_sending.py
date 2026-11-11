"""Replying and composing: one send function, one inbox row, one audit entry, nothing kept
that should not be."""
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.common.errors import ApiError
from apps.inbox import sending, storing
from apps.inbox.access import scope_for
from apps.inbox.models import Conversation, ConversationMessage, InboxAccess
from apps.inbox.tests.base import SENDER, InboxTestCase
from apps.notifications import sms
from apps.notifications.models import SmsDevice, SmsMessage
from apps.notifications.tests.base import set_policy
from apps.operators.models import OperatorAuditEntry
from apps.operators.retention import admin_audit_personal_values
from apps.sites.tests.base import make_account, make_site

TEXT = "Yes, we are open until 6 PM."
REQUEST = "request-id-0000001"
MASKED = "+63918***0000"
NEW_NUMBER = "+639170000042"
Out = ConversationMessage.Direction.OUT


class SendingCase(InboxTestCase):
    def setUp(self):
        super().setUp()
        InboxAccess.objects.create(operator=self.staff, platform="sms", can_send=True, granted_by=self.staff)
        self.scope = scope_for(self.staff, "sms")
        self.count = 0

    def receive(self, sender=SENDER, body="Are you open?"):
        self.count += 1
        item = {"id": f"in_{self.count}", "from": sender, "text": body}
        self.assertTrue(storing.store(self.phone, {"messages": [item]}))
        return Conversation.objects.get(messages__gateway_ref=item["id"])

    def entries(self, action=None):
        rows = OperatorAuditEntry.objects.order_by("pk")
        return list(rows.filter(action=action) if action else rows)

    def nothing_written(self):
        self.assertEqual(SmsMessage.objects.count(), 0)
        self.assertEqual(ConversationMessage.objects.filter(direction=Out).count(), 0)
        self.assertEqual(self.entries(), [])


class SendingTests(SendingCase):
    def test_1_a_reply_is_one_send_record_one_message_and_one_entry(self):
        conversation = self.receive()
        unread = Conversation.objects.get(pk=conversation.pk).unread_count
        before = conversation.last_message_at

        answer, created = sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)

        self.assertTrue(created)
        record = SmsMessage.objects.get()
        self.assertEqual(
            (record.recipient, record.purpose, record.category, record.status),
            (SENDER, "inbox_reply", "inbox", "queued"),
        )
        for field in ("purpose", "reason", "recipient", "public_id"):
            self.assertNotIn(TEXT, getattr(record, field))
        message = ConversationMessage.objects.get(direction="out")
        self.assertEqual(
            (message.body, message.kind, message.author, message.gateway_ref),
            (TEXT, "reply", self.staff, record.public_id),
        )
        conversation.refresh_from_db()
        self.assertGreater(conversation.last_message_at, before)
        self.assertEqual(conversation.unread_count, unread)
        self.assertEqual(answer["conversation"], conversation.public_id)
        self.assertEqual(answer["message"]["id"], message.public_id)
        entry, = self.entries("sms_inbox_reply")
        self.assertEqual((entry.target_type, entry.target_id), ("conversation", conversation.public_id))
        self.assertEqual(
            entry.detail,
            {"platform": "sms", "outcome": "queued", "length": len(TEXT),
             "message": message.public_id, "send_record": record.public_id},
        )
        self.assertEqual(entry.personal, {"contact": MASKED})
        self.assertIsNotNone(cache.get(sms.text_key(record)))

    def test_2_a_composed_text_starts_a_conversation(self):
        for number in ("+639170000042", "09170000042", "639170000042", "9170000042", "0917 000 0042"):
            with self.subTest(number=number):
                Conversation.objects.all().delete()
                SmsMessage.objects.all().delete()
                answer, created = sending.compose(self.scope, number, TEXT, f"request-id-{abs(hash(number))}")
                self.assertTrue(created)
                conversation = Conversation.objects.get()
                self.assertEqual(
                    (conversation.contact, conversation.owner_kind, conversation.can_reply, conversation.line),
                    (NEW_NUMBER, "hq", True, self.phone),
                )
                message = ConversationMessage.objects.get()
                self.assertEqual((message.kind, message.direction), ("compose", "out"))
                entry = self.entries("sms_inbox_compose")[-1]
                self.assertIs(entry.detail["new_conversation"], True)
                self.assertEqual(entry.target_id, conversation.public_id)

    def test_3_composing_to_a_number_with_a_conversation_adds_to_it(self):
        conversation = self.receive()
        sending.compose(self.scope, SENDER, TEXT, REQUEST)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(conversation.messages.count(), 2)
        entry, = self.entries("sms_inbox_compose")
        self.assertIs(entry.detail["new_conversation"], False)

    def test_4_a_bad_text_is_refused_before_anything_is_queued(self):
        conversation = self.receive()
        bad = ["", "x" * 161, "café ☃", "see example.com", "https://x.test", "www.x.test", "10.0.0.1"]
        for text in bad:
            for call in (
                lambda t: sending.reply(self.scope, conversation.public_id, t, REQUEST),
                lambda t: sending.compose(self.scope, NEW_NUMBER, t, REQUEST),
            ):
                with self.subTest(text=text), self.assertRaises(ApiError) as caught:
                    call(text)
                error = caught.exception
                self.assertEqual(error.code, "invalid_text")
                self.assertEqual(error.fields, {"text": [sms.text_problem(text)]})
        self.assertEqual(SmsMessage.objects.count(), 0)
        self.assertEqual(ConversationMessage.objects.filter(direction=Out).count(), 0)
        self.assertEqual(self.entries(), [])

    def test_5_a_number_that_is_not_a_philippine_mobile_is_refused(self):
        for number in ("028123456", "+14155550123", "1234", "Ana", None, 639170000042, ["x"]):
            with self.subTest(number=number), self.assertRaises(ApiError) as caught:
                sending.compose(self.scope, number, TEXT, REQUEST)
            self.assertEqual(caught.exception.code, "invalid_mobile")
            self.assertEqual(list(caught.exception.fields), ["to"])
        self.nothing_written()
        self.assertEqual(Conversation.objects.count(), 0)

    def test_6_a_refused_send_stores_no_message_and_is_audited(self):
        conversation = self.receive()
        cases = {
            "paused": (503, "sms_paused", lambda: set_policy(sending_stopped=True)),
            "gateway_offline": (
                503, "sms_unavailable",
                lambda: SmsDevice.objects.update(last_contact_at=timezone.now() - timedelta(minutes=5)),
            ),
            "staff_limit": (429, "limit_reached", lambda: set_policy(platform_daily_limit=300)),
        }
        for reason, (status, code, arrange) in cases.items():
            with self.subTest(reason=reason):
                cache.clear()
                set_policy(sending_stopped=False, platform_daily_limit=2000)
                SmsDevice.objects.update(last_contact_at=timezone.now())
                arrange()
                before = OperatorAuditEntry.objects.count()
                with self.assertLogs("apps.notifications", level="ERROR"), self.assertRaises(ApiError) as caught:
                    sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)
                self.assertEqual((caught.exception.status, caught.exception.code), (status, code))
                self.assertEqual(ConversationMessage.objects.filter(direction=Out).count(), 0)
                entry = self.entries("sms_inbox_reply")[-1]
                self.assertEqual(OperatorAuditEntry.objects.count(), before + 1)
                self.assertEqual((entry.detail["outcome"], entry.detail["reason"]), ("refused", reason))
                self.assertIn("send_record", entry.detail)
                self.assertNotIn("message", entry.detail)
        words = sending.REFUSALS["staff_limit"][2]
        self.assertEqual(words, "Today's limit for staff texts is reached. 300 texts are kept for sign-in codes.")

    def test_6_the_gateway_off_and_a_composed_text_with_no_conversation(self):
        set_policy(sending_stopped=True)
        with self.assertLogs("apps.notifications", level="ERROR"), self.assertRaises(ApiError) as caught:
            sending.compose(self.scope, NEW_NUMBER, TEXT, REQUEST)
        self.assertEqual(caught.exception.code, "sms_paused")
        self.assertEqual(Conversation.objects.count(), 0)
        entry, = self.entries("sms_inbox_compose")
        self.assertEqual(entry.target_id, "")
        self.assertEqual(entry.detail["outcome"], "refused")
        set_policy(sending_stopped=False)
        with override_settings(SMS_GATEWAY_ENABLED=False), self.assertLogs("apps.notifications", level="ERROR"):
            with self.assertRaises(ApiError) as caught:
                sending.compose(self.scope, NEW_NUMBER, TEXT, "request-id-0000002")
        self.assertEqual((caught.exception.status, caught.exception.code), (503, "sms_unavailable"))
        self.assertNotIn("send_record", self.entries("sms_inbox_compose")[-1].detail)

    def test_7_the_hourly_limit(self):
        conversation = self.receive()
        other = get_user_model().objects.create_user("ben", password="pw", is_staff=True)
        now = timezone.now()

        def made(author, age, count):
            rows = ConversationMessage.objects.bulk_create(
                ConversationMessage(
                    conversation=conversation, platform="sms", direction="out", body="x",
                    author=author, kind="reply",
                ) for _ in range(count)
            )
            ConversationMessage.objects.filter(pk__in=[r.pk for r in rows]).update(created_at=now - age)

        made(self.staff, timedelta(minutes=61), 10)  # too old to count
        made(other, timedelta(minutes=1), 30)  # another user's
        made(self.staff, timedelta(minutes=5), 59)
        self.assertEqual(sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)[1], True)
        with self.assertRaises(ApiError) as caught:
            sending.reply(self.scope, conversation.public_id, TEXT, "request-id-0000002")
        self.assertEqual((caught.exception.status, caught.exception.code), (429, "rate_limited"))
        self.assertEqual(SmsMessage.objects.count(), 1)
        entry = self.entries("sms_inbox_reply")[-1]
        self.assertEqual((entry.detail["outcome"], entry.detail["reason"]), ("refused", "rate_limited"))

    def test_8_read_access_cannot_send(self):
        conversation = self.receive()
        InboxAccess.objects.update(can_send=False)
        scope = scope_for(self.staff, "sms")
        for call in (
            lambda: sending.reply(scope, conversation.public_id, TEXT, REQUEST),
            lambda: sending.compose(scope, NEW_NUMBER, TEXT, REQUEST),
        ):
            with self.assertRaises(ApiError) as caught:
                call()
            self.assertEqual((caught.exception.status, caught.exception.code), (403, "forbidden"))
        self.nothing_written()

    def test_9_a_reply_outside_the_scope_is_not_found(self):
        theirs = Conversation.objects.create(
            owner_kind="site", site=make_site(make_account()), platform="sms", line=self.phone,
            contact=SENDER, can_reply=True, last_message_at=timezone.now(),
        )
        for public_id in ("not an id", "conv_" + "0" * 20, "", None, theirs.public_id):
            with self.subTest(public_id=public_id), self.assertRaises(ApiError) as caught:
                sending.reply(self.scope, public_id, TEXT, REQUEST)
            self.assertEqual((caught.exception.status, caught.exception.code), (404, "not_found"))
        self.nothing_written()

    def test_10_a_sender_that_cannot_be_replied_to(self):
        for sender in ("GLOBE", "+14155550123"):
            conversation = self.receive(sender=sender)
            with self.subTest(sender=sender), self.assertRaises(ApiError) as caught:
                sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)
            self.assertEqual(caught.exception.code, "cannot_reply")
        self.nothing_written()

    def test_11_the_same_request_id_does_not_send_twice(self):
        conversation = self.receive()
        first, created = sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)
        second, again = sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)
        self.assertEqual((created, again), (True, False))
        self.assertEqual(first, second)
        self.assertEqual(SmsMessage.objects.count(), 1)
        self.assertEqual(ConversationMessage.objects.filter(direction=Out).count(), 1)
        self.assertEqual(len(self.entries("sms_inbox_reply")), 1)

        other = get_user_model().objects.create_user("ben", password="pw", is_staff=True)
        InboxAccess.objects.create(operator=other, platform="sms", can_send=True, granted_by=self.staff)
        sending.reply(scope_for(other, "sms"), conversation.public_id, TEXT, REQUEST)
        self.assertEqual(SmsMessage.objects.count(), 2)

        for bad in ("a" * 15, "a" * 65, "request id 000000001", None, 12345678901234567):
            with self.subTest(request_id=bad), self.assertRaises(ApiError) as caught:
                sending.reply(self.scope, conversation.public_id, TEXT, bad)
            self.assertEqual(caught.exception.code, "invalid_request")
            self.assertEqual(list(caught.exception.fields), ["request_id"])

    def test_12_a_request_still_being_sent_and_one_that_was_refused(self):
        conversation = self.receive()
        cache.add(f"inbox:request:{self.staff.pk}:{REQUEST}", "", 60)
        with self.assertRaises(ApiError) as caught:
            sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "request_in_progress"))
        self.assertEqual(SmsMessage.objects.count(), 0)
        cache.clear()

        set_policy(sending_stopped=True)
        with self.assertLogs("apps.notifications", level="ERROR"), self.assertRaises(ApiError):
            sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)
        set_policy(sending_stopped=False)
        self.assertTrue(sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)[1])

    def test_13_no_text_and_no_full_number_in_an_entry_or_a_log(self):
        conversation = self.receive()
        with self.assertNoLogs("apps.inbox", "DEBUG"):
            sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)
            sending.compose(self.scope, NEW_NUMBER, TEXT, "request-id-0000002")
        for entry in self.entries():
            held = json.dumps([entry.detail, entry.personal, entry.target_id, entry.reason])
            for form in (TEXT, "Yes, we", SENDER, "9189990000", NEW_NUMBER, "9170000042"):
                self.assertNotIn(form, held)

    def test_14_the_send_and_its_inbox_row_are_one_transaction(self):
        conversation = self.receive()
        with mock.patch.object(ConversationMessage.objects, "create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)
        self.nothing_written()
        self.assertTrue(sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)[1])

    @override_settings(RETENTION_ENABLED=True)
    def test_15_the_masked_number_of_a_reply_and_a_composed_text_is_blanked_after_a_year(self):
        conversation = self.receive()
        sending.reply(self.scope, conversation.public_id, TEXT, REQUEST)
        sending.compose(self.scope, NEW_NUMBER, TEXT, "request-id-0000002")
        before = [
            (e.pk, e.action, e.target_id, e.detail, dict(e.personal))
            for e in self.entries() if e.action in ("sms_inbox_reply", "sms_inbox_compose")
        ]
        self.assertEqual(len(before), 2)
        self.assertTrue(all(personal for *_, personal in before))
        written = timezone.now()

        self.assertEqual(admin_audit_personal_values(written + timedelta(days=364), True), 0)
        self.assertEqual(admin_audit_personal_values(written + timedelta(days=364), False), 0)
        self.assertTrue(all(e.personal for e in OperatorAuditEntry.objects.filter(pk__in=[b[0] for b in before])))

        self.assertEqual(admin_audit_personal_values(written + timedelta(days=366), True), 2)
        self.assertTrue(all(e.personal for e in OperatorAuditEntry.objects.filter(pk__in=[b[0] for b in before])))
        later = written + timedelta(days=366)
        self.assertEqual(admin_audit_personal_values(later, False), 2)
        for pk, action, target_id, detail, _ in before:
            entry = OperatorAuditEntry.objects.get(pk=pk)
            self.assertEqual(entry.personal, {})
            self.assertEqual(entry.personal_blanked_at, later)
            self.assertEqual((entry.action, entry.target_id, entry.detail), (action, target_id, detail))
