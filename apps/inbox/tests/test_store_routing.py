"""SMS-OWNER BE-3: an incoming text goes to the store that texted the number last, else to HQ. The
routing rules, one place per text, the repeat delivery, STOP per store (HQ's stays global, codes are
never blocked), and what retention, erasure, disposal, member removal and `sms_inbox_delete` do to a
store's text conversations."""
import io
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.common import retention as retention_run
from apps.inbox import storing
from apps.inbox.models import Conversation, ConversationMessage, DoNotText
from apps.inbox.tests.base import SENDER, InboxTestCase
from apps.inquiries import retention as inquiry_retention
from apps.inquiries import sms_inbox
from apps.inquiries.models import (
    InquiryConversation, InquiryMessage, InquiryReply, InquirySmsOptOut, InquirySmsSettings,
)
from apps.members import retention as member_retention
from apps.members.models import Member
from apps.notifications import services
from apps.notifications.models import SmsMessage
from apps.notifications.tests.base import CODE_TEXT
from apps.operators.models import OperatorAuditEntry
from apps.sites.tests.base import make_account, make_site

TEXT = "Ana's Store: your order is ready."


@override_settings(INQUIRIES_ENABLED=True, INQUIRY_SMS_ENABLED=True)
class RoutingCase(InboxTestCase):
    def setUp(self):
        super().setUp()
        self.ana = make_site(make_account())
        self.ben = make_site(make_account("ben@example.test"), name="bens-store", display_name="Ben's")
        for site in (self.ana, self.ben):
            InquirySmsSettings.objects.create(site=site, enabled=True)
        self.n = 0

    def text_to(self, site, purpose="store_reply", to=SENDER, text=TEXT, status="queued", at=None):
        record = services.send_sms(to=to, purpose=purpose, text=text, site=site, ttl=3600).message
        fields = {"status": status}
        if at:
            fields["created_at"] = at
        SmsMessage.objects.filter(pk=record.pk).update(**fields)
        return record

    def hq_text_to(self, purpose="inbox_reply", to=SENDER, status="queued", at=None):
        record = SmsMessage.objects.create(
            recipient=to, category="inbox", purpose=purpose, priority=20, status=status,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        if at:
            SmsMessage.objects.filter(pk=record.pk).update(created_at=at)
        return record

    def receive(self, body="Yes please", sender=SENDER, ref=None, phone=None):
        self.n += 1
        ref = ref or f"in_{self.n}"
        self.assertIs(storing.store(phone or self.phone, {"messages": [{"id": ref, "from": sender, "text": body}]}), True)
        return ref

    def where(self):
        """(text conversations of the stores, their messages, HQ conversations, HQ messages)."""
        return (
            InquiryConversation.objects.filter(provider="sms").count(), InquiryMessage.objects.filter(direction="in").count(),
            Conversation.objects.count(), ConversationMessage.objects.count(),
        )


class RoutingTests(RoutingCase):
    def test_a_reply_to_a_store_text_is_stored_in_that_stores_conversation_only(self):
        self.text_to(self.ana)
        self.receive("Yes please")
        self.assertEqual(self.where(), (1, 1, 0, 0))
        conversation = InquiryConversation.objects.get()
        self.assertEqual((conversation.site_id, conversation.contact_mobile, conversation.unread, conversation.status),
                         (self.ana.pk, SENDER, True, "open"))
        self.assertTrue(conversation.external_id.startswith("sm_"))
        message = InquiryMessage.objects.get()
        self.assertEqual((message.body, message.direction, message.sender_kind), ("Yes please", "in", "customer"))
        self.assertEqual(conversation.last_inbound_at, conversation.last_message_at)

    def test_it_joins_the_conversation_the_store_started_and_reopens_a_closed_one(self):
        member = Member.objects.create(site=self.ana, full_name="Maria", mobile=SENDER, mobile_state="verified",
                                       email="m@example.test")
        conversation, _ = sms_inbox._conversation(self.ana, SENDER, member)
        InquiryConversation.objects.filter(pk=conversation.pk).update(status="closed", unread=False)
        self.text_to(self.ana)
        self.receive()
        self.assertEqual(InquiryConversation.objects.count(), 1)
        conversation.refresh_from_db()
        self.assertEqual((conversation.status, conversation.unread, conversation.contact_name), ("open", True, "Maria"))

    def test_a_number_no_store_texted_goes_to_hq(self):
        self.text_to(self.ana, to="+639170000001")
        self.receive()
        self.assertEqual(self.where(), (0, 0, 1, 1))

    def test_each_row_of_the_routing_table(self):
        now = timezone.now()
        self.text_to(self.ana, at=now - timedelta(hours=3))
        self.hq_text_to(at=now - timedelta(hours=2))  # HQ texted last: HQ wins over the older store text
        self.receive()
        self.assertEqual(self.where(), (0, 0, 1, 1))
        self.text_to(self.ben, purpose="store_compose", at=now - timedelta(hours=1))  # a store texted after HQ
        self.receive()
        self.assertEqual(InquiryConversation.objects.get().site_id, self.ben.pk)
        self.assertEqual(self.where(), (1, 1, 1, 1))

    def test_hq_texting_last_beats_a_store_whichever_hq_purpose_it_was(self):
        for purpose in ("inbox_reply", "inbox_compose", "inbox_bulk"):
            with self.subTest(purpose=purpose):
                SmsMessage.objects.all().delete()
                InquiryMessage.objects.all().delete()
                InquiryConversation.objects.all().delete()
                self.text_to(self.ana, at=timezone.now() - timedelta(hours=2))
                self.hq_text_to(purpose=purpose, at=timezone.now() - timedelta(hours=1))
                self.assertIsNone(sms_inbox.route_incoming(SENDER))

    def test_a_text_older_than_seven_days_does_not_route(self):
        self.text_to(self.ana, at=timezone.now() - timedelta(days=7, minutes=1))
        self.assertIsNone(sms_inbox.route_incoming(SENDER))
        SmsMessage.objects.update(created_at=timezone.now() - timedelta(days=6, hours=23))
        self.assertEqual(sms_inbox.route_incoming(SENDER), self.ana)

    def test_a_code_sent_after_the_store_text_is_ignored(self):
        self.text_to(self.ana, at=timezone.now() - timedelta(hours=1))
        for purpose in ("mobile_verify", "member_signin_code", "member_mobile_verify"):
            services.send_sms(to=SENDER, purpose=purpose, text=CODE_TEXT)
        self.receive()
        self.assertEqual(self.where(), (1, 1, 0, 0))

    def test_a_store_text_that_never_went_out_does_not_route(self):
        for status in ("failed", "expired", "refused"):
            with self.subTest(status=status):
                SmsMessage.objects.all().delete()
                self.text_to(self.ana, status=status)
                self.assertIsNone(sms_inbox.route_incoming(SENDER))
        for status in ("queued", "dispatched", "sent"):
            with self.subTest(status=status):
                SmsMessage.objects.all().delete()
                self.text_to(self.ana, status=status)
                self.assertEqual(sms_inbox.route_incoming(SENDER), self.ana)

    def test_a_store_that_switched_texts_off_or_was_closed_goes_to_hq(self):
        self.text_to(self.ana)
        InquirySmsSettings.objects.filter(site=self.ana).update(enabled=False)
        self.receive()
        self.assertEqual(self.where(), (0, 0, 1, 1))
        InquirySmsSettings.objects.filter(site=self.ana).update(enabled=True)
        self.assertEqual(sms_inbox.route_incoming(SENDER), self.ana)
        self.ana.deleted_at = timezone.now()
        self.ana.save(update_fields=["deleted_at"])
        self.assertIsNone(sms_inbox.route_incoming(SENDER))

    def test_a_store_with_no_settings_row_goes_to_hq(self):
        InquirySmsSettings.objects.filter(site=self.ana).delete()
        self.text_to(self.ana)
        self.assertIsNone(sms_inbox.route_incoming(SENDER))

    def test_with_a_switch_off_or_no_tables_everything_goes_to_hq_and_no_text_table_is_read(self):
        self.text_to(self.ana)
        for patch in (override_settings(INQUIRY_SMS_ENABLED=False), override_settings(INQUIRIES_ENABLED=False),
                      override_settings(SMS_GATEWAY_ENABLED=False)):
            with patch:
                self.assertIsNone(sms_inbox.route_incoming(SENDER))
        with mock.patch("apps.inquiries.switch.sms_tables_exist", return_value=False):
            self.receive()
            self.assertEqual(self.where()[2:], (1, 1))
            self.assertEqual(sms_inbox.seen_refs(self.phone, ["in_1"]), set())

    def test_a_number_that_cannot_be_replied_to_goes_to_hq(self):
        self.receive(sender="INFOTXT")
        self.assertEqual(self.where(), (0, 0, 1, 1))


class DedupeAndStopTests(RoutingCase):
    def test_a_repeat_delivery_is_stored_once_wherever_it_went(self):
        self.text_to(self.ana)
        self.receive(ref="same_1")
        self.receive(ref="same_1")
        self.assertEqual(self.where(), (1, 1, 0, 0))
        # an HQ text repeated after the routing rules changed is not stored a second time in the store
        SmsMessage.objects.all().delete()
        self.receive(ref="hq_1")
        self.receive(ref="hq_1")
        self.assertEqual(self.where(), (1, 1, 1, 1))

    def test_two_phones_with_the_same_ref_are_two_texts(self):
        from apps.notifications.tests.base import online_phone
        second = online_phone(name="second", forwards_incoming=True)
        self.text_to(self.ana)
        self.receive(ref="same_1")
        self.receive(ref="same_1", phone=second)
        self.assertEqual(InquiryMessage.objects.filter(direction="in").count(), 2)

    def test_a_text_is_stored_in_exactly_one_place(self):
        self.text_to(self.ana)
        storing.store(self.phone, {"messages": [
            {"id": "a1", "from": SENDER, "text": "one"}, {"id": "a2", "from": "+639170000009", "text": "two"},
        ]})
        self.assertEqual(self.where(), (1, 1, 1, 1))

    def test_a_stop_to_a_store_is_that_stores_opt_out_only(self):
        self.text_to(self.ana)
        self.receive("  Stop. ")
        optout = InquirySmsOptOut.objects.get()
        self.assertEqual((optout.site_id, optout.mobile), (self.ana.pk, SENDER))
        self.assertEqual(optout.message.body, "  Stop. ")
        self.assertFalse(DoNotText.objects.exists())
        self.assertTrue(sms_inbox.opted_out(self.ana, SENDER))
        self.assertFalse(sms_inbox.opted_out(self.ben, SENDER))
        self.receive("stop")  # twice: still one row
        self.assertEqual(InquirySmsOptOut.objects.count(), 1)

    def test_a_stop_that_is_only_part_of_a_text_is_no_stop(self):
        self.text_to(self.ana)
        self.receive("Please do not stop the order")
        self.assertFalse(InquirySmsOptOut.objects.exists())

    def test_an_hq_stop_stays_global_and_makes_no_store_opt_out(self):
        self.receive("STOP")
        self.assertEqual(list(DoNotText.objects.values_list("mobile", flat=True)), [SENDER])
        self.assertFalse(InquirySmsOptOut.objects.exists())

    def test_codes_are_never_blocked_by_a_stores_opt_out(self):
        self.text_to(self.ana)
        self.receive("STOP")
        result = services.send_sms(to=SENDER, purpose="mobile_verify", text=CODE_TEXT)
        self.assertEqual(result.outcome, "queued")


@override_settings(RETENTION_ENABLED=True)
class RetentionAndErasureTests(RoutingCase):
    def conversation(self, site=None, mobile=SENDER, age=timedelta(0), replies=False):
        site = site or self.ana
        at = timezone.now() - age
        conversation = InquiryConversation.objects.create(
            site=site, provider="sms", external_id=sms_inbox._new_ref(), stores_history=True, contact_name="Maria",
            display_name="Maria", contact_mobile=mobile, last_message_at=at, last_inbound_at=at,
        )
        message = InquiryMessage.objects.create(
            site=site, conversation=conversation, direction="in", sender_kind="customer", body="Hello", created_at=at)
        if replies:
            InquiryReply.objects.create(
                site=site, conversation=conversation, author=site.account, idempotency_key=f"key-{conversation.pk}-0001",
                body_length=5, message=message)
        return conversation

    def test_180_days_after_the_last_message_the_text_conversation_goes_like_any_stored_one(self):
        old = self.conversation(age=timedelta(days=181))
        fresh = self.conversation(mobile="+639170000002", age=timedelta(days=10))
        kept = self.conversation(mobile="+639170000003", age=timedelta(days=200), replies=True)
        retention_run.run_all(only=["inquiry_messages"])
        self.assertFalse(InquiryConversation.objects.filter(pk=old.pk).exists())
        self.assertEqual(InquiryMessage.objects.filter(conversation=fresh).count(), 1)
        self.assertEqual(InquiryConversation.objects.get(pk=fresh.pk).contact_mobile, "+639170000002")
        kept.refresh_from_db()  # an audit row points at it: blanked, not deleted
        self.assertEqual((kept.contact_mobile, kept.contact_name, kept.status), ("", "", "closed"))
        self.assertFalse(InquiryMessage.objects.filter(conversation=kept).exists())

    def test_the_site_disposal_deletes_the_opt_outs_blanks_the_numbers_and_turns_texts_off(self):
        self.conversation()
        InquirySmsOptOut.objects.create(site=self.ana, mobile=SENDER)
        InquirySmsOptOut.objects.create(site=self.ben, mobile=SENDER)
        counts = inquiry_retention.blank_inquiries_of(self.ana.account)
        self.assertEqual(counts["inquiry_sms_optouts"], 1)
        self.assertFalse(InquirySmsOptOut.objects.filter(site=self.ana).exists())
        self.assertTrue(InquirySmsOptOut.objects.filter(site=self.ben).exists())
        self.assertFalse(InquirySmsSettings.objects.get(site=self.ana).enabled)
        self.assertTrue(InquirySmsSettings.objects.get(site=self.ben).enabled)
        self.assertEqual(InquiryConversation.objects.get().contact_mobile, "")
        self.assertFalse(InquiryMessage.objects.exists())
        self.assertIn("inquiry_sms_optouts", inquiry_retention.blanked())

    def test_a_site_with_no_opt_outs_keeps_the_disposal_report_as_it_was(self):
        self.assertNotIn("inquiry_sms_optouts", inquiry_retention.blank_inquiries_of(self.ana.account))

    def test_before_inquiries_0010_the_disposal_reads_no_text_table(self):
        with mock.patch("apps.inquiries.retention.sms_tables_exist", return_value=False):
            self.assertEqual(inquiry_retention.blank_inquiries_of(self.ana.account), {})

    def test_removing_a_member_deletes_that_members_text_conversation_and_its_replies(self):
        member = Member.objects.create(site=self.ana, full_name="Maria", mobile=SENDER, mobile_state="verified",
                                       email="m@example.test")
        other = Member.objects.create(site=self.ana, full_name="Jun", mobile="+639170000002", mobile_state="verified",
                                      email="j@example.test")
        self.conversation(replies=True)
        theirs = self.conversation(mobile="+639170000002")
        InquirySmsOptOut.objects.create(site=self.ana, mobile=SENDER)
        counts = member_retention.blank_members(
            type(self.ana).objects.filter(pk=self.ana.pk), ignore_switch=True, only_member=member)
        self.assertEqual(counts["inquiry_sms_conversations"], 1)
        self.assertEqual(list(InquiryConversation.objects.values_list("pk", flat=True)), [theirs.pk])
        self.assertFalse(InquiryReply.objects.exists())
        self.assertTrue(InquirySmsOptOut.objects.exists())  # a STOP outlives the member
        other.refresh_from_db()
        self.assertEqual(other.mobile, "+639170000002")

    def test_removing_a_member_with_no_text_conversation_keeps_the_report_as_it_was(self):
        member = Member.objects.create(site=self.ana, full_name="Maria", mobile=SENDER, mobile_state="verified",
                                       email="m@example.test")
        counts = member_retention.blank_members(
            type(self.ana).objects.filter(pk=self.ana.pk), ignore_switch=True, only_member=member)
        self.assertNotIn("inquiry_sms_conversations", counts)


class DeleteCommandTests(RoutingCase):
    def run_command(self, typed, *args, confirm=False):
        out, err = io.StringIO(), io.StringIO()
        from django.contrib.auth import get_user_model
        get_user_model().objects.get_or_create(username="ana", defaults={"is_staff": True})
        with mock.patch("sys.stdin", io.StringIO(typed + "\n")):
            call_command("sms_inbox_delete", *args, actor="ana", confirm=confirm, stdout=out, stderr=err)
        return out.getvalue()

    def keep(self, site, mobile=SENDER, replies=True):
        conversation = InquiryConversation.objects.create(
            site=site, provider="sms", external_id=sms_inbox._new_ref(), stores_history=True, contact_mobile=mobile)
        message = InquiryMessage.objects.create(
            site=site, conversation=conversation, direction="out", sender_kind="owner", body="Hi")
        if replies:
            InquiryReply.objects.create(site=site, conversation=conversation, author=site.account,
                                        idempotency_key=f"key-{conversation.pk}-0001", body_length=2, message=message)
        return conversation

    def test_the_count_names_each_store_and_deletes_nothing(self):
        self.keep(self.ana)
        self.keep(self.ben)
        out = self.run_command("0918 999 0000")
        self.assertIn(f"1 store text conversation of store {self.ana.public_id}", out)
        self.assertIn(f"1 store text conversation of store {self.ben.public_id}", out)
        self.assertIn("Nothing was deleted", out)
        self.assertEqual(InquiryConversation.objects.count(), 2)
        self.assertNotIn(SENDER, out)

    def test_confirm_deletes_every_stores_conversation_with_its_audit_rows_and_the_send_records(self):
        self.keep(self.ana)
        self.keep(self.ben)
        self.keep(self.ana, mobile="+639170000002")
        self.text_to(self.ana, status="sent")
        self.text_to(self.ana, to="+639170000002", status="sent")
        InquirySmsOptOut.objects.create(site=self.ana, mobile=SENDER)
        out = self.run_command("0918 999 0000", confirm=True)
        self.assertIn("Deleted:", out)
        self.assertEqual(list(InquiryConversation.objects.values_list("contact_mobile", flat=True)), ["+639170000002"])
        self.assertEqual(InquiryReply.objects.count(), 1)
        self.assertEqual(list(SmsMessage.objects.values_list("recipient", flat=True)), ["+639170000002"])
        self.assertEqual(InquirySmsOptOut.objects.count(), 1)  # kept: the store must never text the number again
        entry = OperatorAuditEntry.objects.get(action="sms_inbox_delete")
        self.assertEqual((entry.detail["store_conversations"], entry.detail["store_messages"]), (2, 2))
        self.assertNotIn(SENDER, str(entry.detail))

    def test_with_site_only_that_stores_conversation_goes_and_hq_is_untouched(self):
        self.keep(self.ana)
        self.keep(self.ben)
        self.receive_hq()
        out = self.run_command("0918 999 0000", "--site", self.ana.public_id, confirm=True)
        self.assertIn(f"of store {self.ana.public_id}", out)
        self.assertEqual(list(InquiryConversation.objects.values_list("site_id", flat=True)), [self.ben.pk])
        self.assertEqual(Conversation.objects.count(), 1)

    def receive_hq(self):
        storing.store(self.phone, {"messages": [{"id": "hq_1", "from": SENDER, "text": "Hello HQ"}]})

    def test_an_unknown_site_is_refused(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.run_command("0918 999 0000", "--site", "site_nope", confirm=True)

    def test_before_inquiries_0010_it_reads_no_text_table(self):
        with mock.patch("apps.inbox.management.commands.sms_inbox_delete.sms_tables_exist", return_value=False):
            self.assertEqual(self.run_command("0918 999 0000").strip(), "Nothing is stored for that number.")

