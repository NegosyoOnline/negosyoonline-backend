"""SMS-OWNER BE-2: a store's text conversations with its members on the owner's routes. Compose and
reply (prefix, the 160 limit, links, members of other stores, unverified or disposed members,
opt-outs, idempotency, the gateway's refusals), the live send state of a thread, the per-store
isolation, the channels switch, and every route answering 404 with a switch off or before the
tables exist."""
from unittest import mock

from django.core.checks import run_checks
from django.test import override_settings
from django.utils import timezone

from apps.inbox.tests.base import inbox_on
from apps.inquiries.models import (
    InquiryConversation, InquiryMessage, InquiryReply, InquirySmsOptOut, InquirySmsSettings,
)
from apps.inquiries.tests.test_stored import StoredOwnerTestCase
from apps.members.models import Member
from apps.notifications.models import SmsMessage
from apps.notifications.tests.base import gateway_on, online_phone, set_policy

MOBILE = "+639171234567"
OTHER_MOBILE = "+639181112222"
PREFIX = "Ana's Store: "


def make_member(site, mobile=MOBILE, state="verified", name="Maria Santos"):
    return Member.objects.create(
        site=site, full_name=name, mobile=mobile, mobile_state=state,
        email=f"m{mobile[-6:]}-{site.pk}@example.test",
    )


@inbox_on
@gateway_on
@override_settings(INQUIRY_SMS_ENABLED=True)
class SmsOwnerCase(StoredOwnerTestCase):
    def setUp(self):
        super().setUp()
        later = mock.patch("apps.operators.background.run_later")
        later.start()
        self.addCleanup(later.stop)
        self.phone = online_phone(forwards_incoming=True)
        InquirySmsSettings.objects.create(site=self.site, enabled=True)
        InquirySmsSettings.objects.create(site=self.ben_site, enabled=True)
        self.member = make_member(self.site)

    def compose(self, text="Your order is ready.", key="key-compose-1", member=None, base=None, client=None):
        return self.send(
            "post", f"{base or self.base}sms/compose/",
            {"member_id": (member or self.member).public_id, "text": text, "idempotency_key": key}, client=client,
        )

    def thread(self, ref, base=None, client=None):
        return self.get(f"{base or self.base}conversations/{ref}/", client=client)

    def sent_text(self):
        """The records the gateway holds, newest first."""
        return SmsMessage.objects.order_by("-pk")


class ComposeTests(SmsOwnerCase):
    def test_a_text_to_a_verified_member_is_queued_with_the_store_name_in_front(self):
        body = self.compose().json()
        self.assertEqual((body["state"], body["new_conversation"], body["error_code"]), ("sending", True, ""))
        self.assertTrue(body["ref"].startswith("sm_"))
        record = SmsMessage.objects.get()
        self.assertEqual((record.purpose, record.category, record.site_id, record.recipient, record.status),
                         ("store_compose", "store", self.site.pk, MOBILE, "queued"))
        message = InquiryMessage.objects.get(site=self.site, direction="out")
        self.assertEqual(message.body, PREFIX + "Your order is ready.")
        self.assertEqual(message.external_id, record.public_id)
        conversation = InquiryConversation.objects.get(site=self.site, provider="sms")
        self.assertEqual((conversation.contact_mobile, conversation.contact_name, conversation.stores_history),
                         (MOBILE, "Maria Santos", True))
        self.assertEqual(conversation.external_id, body["ref"])

    def test_the_audit_row_keeps_the_length_and_never_the_text(self):
        self.compose()
        row = InquiryReply.objects.get(site=self.site)
        self.assertEqual((row.state, row.body_length), ("sent", len(PREFIX + "Your order is ready.")))
        self.assertNotIn("order is ready", str(InquiryReply.objects.values().get(pk=row.pk)))
        self.assertNotIn("order is ready", str(SmsMessage.objects.values().get()))  # the text waits in the cache only

    def test_a_second_text_goes_to_the_same_conversation(self):
        first = self.compose().json()
        second = self.compose(key="key-compose-2", text="And it ships today.").json()
        self.assertEqual((second["ref"], second["new_conversation"]), (first["ref"], False))
        self.assertEqual(InquiryConversation.objects.filter(site=self.site, provider="sms").count(), 1)
        self.assertEqual(InquiryMessage.objects.filter(site=self.site, direction="out").count(), 2)

    def test_the_text_is_at_most_160_characters_with_the_name_in_front(self):
        room = 160 - len(PREFIX)
        self.assertEqual(self.compose("x" * room, key="key-room-001").status_code, 200)
        over = self.compose("x" * (room + 1), key="key-room-002")
        self.assertEqual(over.status_code, 422)
        self.assertIn("text", over.json()["error"]["fields"])
        self.assertEqual(SmsMessage.objects.count(), 1)

    def test_a_link_an_empty_text_and_a_non_plain_text_are_refused(self):
        for index, text in enumerate(("See https://example.test/a", "See example.com/a", "   ", "café ☃")):
            with self.subTest(text=text):
                response = self.compose(text, key=f"key-bad-000{index}")
                self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(SmsMessage.objects.count(), 0)
        self.assertFalse(InquiryMessage.objects.filter(direction="out").exists())

    def test_a_store_name_that_cannot_be_texted_gives_way_to_the_neutral_name(self):
        self.site.display_name = "Visit anas.example.com now"
        self.site.save(update_fields=["display_name"])
        self.assertEqual(self.compose().status_code, 200)
        self.assertTrue(InquiryMessage.objects.get(direction="out").body.startswith("Your store: "))
        self.assertEqual(self.get(self.base + "sms/status/").json()["prefix"], "Your store: ")

    def test_only_a_verified_member_of_this_store_can_be_texted(self):
        pending = make_member(self.site, OTHER_MOBILE, state="pending", name="Pending")
        disposed = make_member(self.site, "+639000000042", name="")
        bens = make_member(self.ben_site, "+639175550000", name="Ben's member")
        for member in (pending, disposed, bens):
            with self.subTest(member=member.pk):
                self.error(self.compose(member=member, key=f"key-m-{member.pk:06d}"), 404, "not_found")
        self.error(self.send("post", self.base + "sms/compose/",
                             {"member_id": "mem_nope", "text": "Hi", "idempotency_key": "key-nope-001"}), 404, "not_found")
        self.assertEqual(SmsMessage.objects.count(), 0)

    def test_the_same_key_twice_sends_once(self):
        first = self.compose().json()
        again = self.compose().json()
        self.assertEqual((again["ref"], again["state"]), (first["ref"], "sending"))
        self.assertEqual(SmsMessage.objects.count(), 1)
        self.assertEqual(InquiryMessage.objects.filter(direction="out").count(), 1)

    def test_a_key_used_for_another_member_is_a_conflict(self):
        self.compose()
        other = make_member(self.site, OTHER_MOBILE, name="Jun")
        self.error(self.compose(member=other), 409, "conflict")

    def test_the_body_must_carry_only_the_known_fields(self):
        response = self.send("post", self.base + "sms/compose/", {
            "member_id": self.member.public_id, "text": "Hi", "idempotency_key": "key-extra-01", "to": "+639170000000",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SmsMessage.objects.count(), 0)

    def test_an_opted_out_number_is_refused_and_only_for_this_store(self):
        InquirySmsOptOut.objects.create(site=self.site, mobile=MOBILE)
        self.error(self.compose(), 409, "opted_out")
        self.assertEqual(SmsMessage.objects.count(), 0)
        bens = make_member(self.ben_site, MOBILE, name="Same number, other store")
        self.assertEqual(self.compose(member=bens, base=self.ben_base, client=self.ben, key="key-ben-0001").status_code, 200)

    def test_a_store_that_switched_texts_off_cannot_compose(self):
        InquirySmsSettings.objects.filter(site=self.site).update(enabled=False)
        self.error(self.compose(), 409, "sms_off")

    def test_a_phone_that_does_not_pass_texts_on_makes_composing_unavailable(self):
        self.phone.forwards_incoming = False
        self.phone.save(update_fields=["forwards_incoming"])
        self.error(self.compose(), 409, "replies_off")
        with override_settings(SMS_INBOX_ENABLED=False):
            self.error(self.compose(key="key-compose-2"), 409, "replies_off")
        self.assertEqual(SmsMessage.objects.count(), 0)


class RefusalTests(SmsOwnerCase):
    def test_an_offline_phone_is_a_failed_text_with_the_fixed_reason(self):
        self.phone.last_contact_at = timezone.now() - timezone.timedelta(minutes=10)
        self.phone.save(update_fields=["last_contact_at"])
        body = self.compose().json()
        self.assertEqual((body["state"], body["error_code"]), ("failed", "gateway_offline"))
        self.assertIn("offline", body["message"])
        row = InquiryReply.objects.get()
        self.assertEqual((row.state, row.error_code), ("failed", "provider_down"))
        message = InquiryMessage.objects.get(direction="out")
        self.assertEqual((message.delivery_state, message.error_code), ("failed", "gateway_offline"))
        thread = self.thread(body["ref"]).json()
        self.assertEqual(thread["messages"][-1]["error_code"], "gateway_offline")
        self.assertIn("offline", thread["messages"][-1]["error_message"])

    def test_the_stores_daily_limit_refuses_the_next_text(self):
        set_policy(store_daily_limit=1)
        self.assertEqual(self.compose().json()["state"], "sending")
        body = self.compose(key="key-compose-2").json()
        self.assertEqual((body["state"], body["error_code"]), ("failed", "site_limit"))
        self.assertEqual(InquiryReply.objects.get(idempotency_key="key-compose-2").error_code, "rate_limited")
        self.assertEqual(self.get(self.base + "sms/status/").json()["left_today"], 0)

    def test_the_stores_hourly_limit_refuses_the_next_text(self):
        with mock.patch("apps.notifications.sms.STORE_HOURLY_LIMIT", 1):
            self.compose()
            body = self.compose(key="key-compose-2").json()
        self.assertEqual((body["state"], body["error_code"]), ("failed", "site_hour_limit"))

    def test_sending_stopped_by_staff_refuses_the_text(self):
        set_policy(sending_stopped=True)
        body = self.compose().json()
        self.assertEqual((body["state"], body["error_code"]), ("failed", "paused"))
        self.assertEqual(InquiryReply.objects.get().error_code, "provider_down")

    def test_one_stores_limit_leaves_another_store_able_to_send(self):
        set_policy(store_daily_limit=1)
        self.compose()
        bens = make_member(self.ben_site, OTHER_MOBILE, name="Ben's")
        self.assertEqual(
            self.compose(member=bens, base=self.ben_base, client=self.ben, key="key-ben-0001").json()["state"], "sending")


class ThreadAndReplyTests(SmsOwnerCase):
    def setUp(self):
        super().setUp()
        self.ref = self.compose().json()["ref"]

    def test_the_thread_shows_the_masked_number_the_room_and_the_flags(self):
        body = self.thread(self.ref).json()
        self.assertEqual(body["channel"], "sms")
        self.assertEqual(body["contact_mobile"], "+63917***4567")
        self.assertNotIn(MOBILE, str(body))
        self.assertEqual((body["max_length"], body["prefix"], body["opted_out"]), (160 - len(PREFIX), PREFIX, False))
        self.assertEqual(body["reply"], {"open": True, "reason": ""})
        self.assertEqual(body["contact_email"], "")

    def test_the_list_shows_the_name_not_the_number(self):
        items = self.get(self.base + "conversations/").json()["items"]
        item = next(i for i in items if i["channel"] == "sms")
        self.assertEqual(item["display_name"], "Maria Santos")
        self.assertNotIn("4567", str(item))

    def test_the_search_does_not_find_a_text_conversation_by_its_number(self):
        self.assertEqual(
            [i for i in self.get(self.base + "conversations/?q=91712").json()["items"] if i["channel"] == "sms"], [])
        found = self.get(self.base + "conversations/?q=Maria").json()["items"]
        self.assertIn("sms", [i["channel"] for i in found])

    def test_the_send_state_follows_the_gateways_record(self):
        record = SmsMessage.objects.get()
        sending = lambda: self.thread(self.ref).json()["messages"][-1]["delivery_state"]  # noqa: E731
        self.assertEqual(sending(), "")
        SmsMessage.objects.filter(pk=record.pk).update(status="dispatched")
        self.assertEqual(sending(), "")
        SmsMessage.objects.filter(pk=record.pk).update(status="sent", sent_at=timezone.now())
        self.assertEqual(sending(), "sent")
        self.assertEqual(InquiryMessage.objects.get(direction="out").delivery_state, "sent")

    def test_a_text_the_phone_could_not_send_shows_failed_with_the_reason(self):
        SmsMessage.objects.update(status="failed", reason="")
        message = self.thread(self.ref).json()["messages"][-1]
        self.assertEqual(message["delivery_state"], "failed")
        self.assertEqual(message["error_code"], "not_sent")
        self.assertEqual(InquiryReply.objects.get().state, "failed")
        SmsMessage.objects.update(status="expired", reason="expired")
        self.assertEqual(InquiryReply.objects.get().state, "failed")

    def test_a_reply_goes_out_as_a_store_reply(self):
        response = self.send("post", f"{self.base}conversations/{self.ref}/reply/",
                             {"text": "Thank you!", "idempotency_key": "key-reply-01"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["state"], response.json()["error_code"]), ("sending", ""))
        record = SmsMessage.objects.order_by("-pk").first()
        self.assertEqual((record.purpose, record.site_id), ("store_reply", self.site.pk))
        self.assertEqual(InquiryMessage.objects.filter(direction="out").order_by("-pk").first().body, PREFIX + "Thank you!")

    def test_a_reply_is_refused_when_opted_out_off_closed_or_too_long(self):
        path = f"{self.base}conversations/{self.ref}/reply/"
        send = lambda key, text="Hello": self.send("post", path, {"text": text, "idempotency_key": key})  # noqa: E731
        self.assertEqual(send("key-reply-02", "x" * 150).status_code, 422)
        InquirySmsSettings.objects.filter(site=self.site).update(enabled=False)
        self.error(send("key-reply-03"), 409, "sms_off")
        InquirySmsSettings.objects.filter(site=self.site).update(enabled=True)
        InquirySmsOptOut.objects.create(site=self.site, mobile=MOBILE)
        self.error(send("key-reply-04"), 409, "opted_out")
        thread = self.thread(self.ref).json()
        self.assertEqual((thread["opted_out"], thread["reply"]), (True, {"open": False, "reason": "opted_out"}))
        InquirySmsOptOut.objects.all().delete()
        self.send("post", f"{self.base}conversations/{self.ref}/close/")
        self.error(send("key-reply-05"), 409, "closed")
        self.assertEqual(SmsMessage.objects.count(), 1)

    def test_the_same_reply_key_twice_sends_once(self):
        path = f"{self.base}conversations/{self.ref}/reply/"
        for _ in range(2):
            self.send("post", path, {"text": "Hi again", "idempotency_key": "key-reply-06"})
        self.assertEqual(SmsMessage.objects.count(), 2)


class IsolationTests(SmsOwnerCase):
    def setUp(self):
        super().setUp()
        self.ref = self.compose().json()["ref"]

    def test_another_store_cannot_list_open_reply_to_or_change_the_conversation(self):
        self.assertNotIn(self.ref, str(self.get(self.ben_base + "conversations/", client=self.ben).json()))
        self.error(self.thread(self.ref, base=self.ben_base, client=self.ben), 404, "not_found")
        for action in ("reply", "close", "reopen", "read"):
            with self.subTest(action=action):
                payload = {"text": "Hi", "idempotency_key": "key-ben-reply"} if action == "reply" else None
                self.error(self.send("post", f"{self.ben_base}conversations/{self.ref}/{action}/", payload, client=self.ben),
                           404, "not_found")
        self.assertEqual(SmsMessage.objects.count(), 1)

    def test_another_store_cannot_text_this_stores_member(self):
        self.error(self.compose(base=self.ben_base, client=self.ben, key="key-ben-0001"), 404, "not_found")

    def test_the_hq_inbox_scope_never_includes_a_store_conversation(self):
        from django.contrib.auth import get_user_model
        from apps.inbox.access import scope_for
        from apps.inbox.models import Conversation, InboxAccess

        staff = get_user_model().objects.create_user("ana-staff", password="pw", is_staff=True)
        InboxAccess.objects.create(operator=staff, platform="sms", can_send=True, granted_by=staff)
        scope = scope_for(staff, "sms")
        self.assertEqual(scope.conversations.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)


class ChannelsAndSwitchTests(SmsOwnerCase):
    def test_the_channels_route_reports_and_changes_the_stores_text_switch(self):
        body = self.get(self.base + "channels/").json()
        self.assertEqual((body["sms_available"], body["sms_enabled"]), (True, True))
        body = self.send("put", self.base + "channels/", {"sms_enabled": False}).json()
        self.assertEqual((body["sms_available"], body["sms_enabled"]), (True, False))
        self.assertFalse(InquirySmsSettings.objects.get(site=self.site).enabled)
        self.assertTrue(InquirySmsSettings.objects.get(site=self.ben_site).enabled)
        self.assertEqual(self.send("put", self.base + "channels/", {"sms_enabled": True}).json()["sms_enabled"], True)

    def test_a_store_with_no_settings_row_has_texts_off_and_the_put_makes_the_row(self):
        InquirySmsSettings.objects.filter(site=self.site).delete()
        self.assertFalse(self.get(self.base + "channels/").json()["sms_enabled"])
        self.send("put", self.base + "channels/", {"sms_enabled": True})
        self.assertTrue(InquirySmsSettings.objects.get(site=self.site).enabled)

    def test_the_status_route_has_the_gateways_numbers_and_the_prefix(self):
        body = self.get(self.base + "sms/status/").json()
        self.assertEqual((body["phone_online"], body["enabled"], body["incoming_ready"]), (True, True, True))
        self.assertEqual((body["daily_limit"], body["left_today"], body["hourly_limit"], body["left_this_hour"]), (50, 50, 20, 20))
        self.assertEqual((body["prefix"], body["max_length"]), (PREFIX, 160 - len(PREFIX)))
        self.compose()
        after = self.get(self.base + "sms/status/").json()
        self.assertEqual((after["left_today"], after["left_this_hour"]), (49, 19))

    def routes(self):
        return [
            ("get", self.base + "sms/status/", None),
            ("post", self.base + "sms/compose/", {"member_id": self.member.public_id, "text": "Hi", "idempotency_key": "key-sw-0001"}),
            ("put", self.base + "channels/", {"sms_enabled": True}),
        ]

    def assert_all_404(self):
        for method, path, payload in self.routes():
            with self.subTest(path=path):
                response = self.get(path) if method == "get" else self.send(method, path, payload)
                self.error(response, 404, "not_found")
        self.assertEqual(SmsMessage.objects.count(), 0)
        self.assertEqual(InquiryMessage.objects.filter(direction="out").count(), 0)

    def test_every_route_answers_404_with_the_text_switch_off(self):
        with override_settings(INQUIRY_SMS_ENABLED=False):
            self.assert_all_404()
            self.assertFalse(self.get(self.base + "channels/").json()["sms_available"])

    def test_every_route_answers_404_with_the_gateway_off(self):
        with override_settings(SMS_GATEWAY_ENABLED=False):
            self.assert_all_404()

    def test_every_route_answers_404_before_the_tables_exist_and_reads_none(self):
        with mock.patch("apps.inquiries.switch.sms_tables_exist", return_value=False):
            self.assert_all_404()
            self.assertFalse(self.get(self.base + "channels/").json()["sms_available"])
            self.assertEqual(self.get(self.base + "conversations/").status_code, 200)

    def test_a_text_conversation_is_not_listed_or_opened_while_a_switch_is_off(self):
        ref = self.compose().json()["ref"]
        with override_settings(INQUIRY_SMS_ENABLED=False):
            self.assertNotIn(ref, str(self.get(self.base + "conversations/").json()))
            self.error(self.thread(ref), 404, "not_found")

    def test_the_deploy_check_wants_the_inquiries_and_gateway_switches(self):
        def ids():
            return {m.id for m in run_checks(include_deployment_checks=True) if m.id == "inquiries.E008"}
        with override_settings(INQUIRY_SMS_ENABLED=True, INQUIRIES_ENABLED=False, SMS_GATEWAY_ENABLED=True):
            self.assertEqual(ids(), {"inquiries.E008"})
        with override_settings(INQUIRY_SMS_ENABLED=True, INQUIRIES_ENABLED=True, SMS_GATEWAY_ENABLED=False):
            self.assertEqual(ids(), {"inquiries.E008"})
        with override_settings(INQUIRY_SMS_ENABLED=True, INQUIRIES_ENABLED=True, SMS_GATEWAY_ENABLED=True):
            self.assertEqual(ids(), set())
        with override_settings(INQUIRY_SMS_ENABLED=False, INQUIRIES_ENABLED=False):
            self.assertEqual(ids(), set())
