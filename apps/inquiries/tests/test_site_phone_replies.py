"""SITE-PHONE-REPLIES (Coach Ed "A", 2026-11-26): a store's own phone forwards the texts it receives,
and the server keeps only a reply to that store's own text of the last 7 days (Bulk included), in the
store Inbox. A STOP goes to the store's opt-out list. Everything else is dropped unstored and unlogged.
The platform phone is unchanged."""
import json
import logging
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from apps.inbox import storing
from apps.inbox.models import Conversation, ConversationMessage, DoNotText
from apps.inquiries import sms_inbox
from apps.inquiries.models import InquiryConversation, InquiryMessage, InquirySmsOptOut, InquirySmsSettings
from apps.inquiries.tests.test_store_bulk import NUMBERS, TOKEN, BulkCase
from apps.notifications import sms
from apps.notifications.models import SmsDevice, SmsMessage
from apps.notifications.tests.base import make_phone

REPLY = "Yes, I will come by tomorrow."
OTHER_STORE_NUMBER = "+639171000044"
PLATFORM_TOKEN = "token-platform-phone"


@override_settings(SMS_SITE_INCOMING_ENABLED=True)
class SitePhoneCase(BulkCase):
    def setUp(self):
        super().setUp()
        SmsDevice.objects.filter(pk=self.platform.pk).update(token_hash=sms.token_hash(PLATFORM_TOKEN))
        self.n = 0
        self.bens_line = make_phone("ben-line", "token-ben-line", site=self.ben_site)

    # -- helpers
    def gateway(self, name, body=None, token=TOKEN):
        return self.client.post(
            f"/gateway/v1/{name}", data=json.dumps(body or {}), content_type="application/json",
            secure=True, HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_REAL_IP="203.0.113.7",
        )

    def incoming(self, token=TOKEN):
        response = self.gateway("poll", token=token)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["incoming"]

    def text_out(self, site=None, purpose="store_reply", to=NUMBERS[0], status="sent", age=timedelta(hours=1), device=None):
        site = site or self.site
        device = device or (self.line if site.pk == self.site.pk else self.bens_line)
        record = SmsMessage.objects.create(
            recipient=to, category="storebulk" if purpose == "store_bulk" else "store", purpose=purpose, site=site,
            priority=20, status=status, device=device, expires_at=timezone.now() + timedelta(hours=1),
        )
        SmsMessage.objects.filter(pk=record.pk).update(created_at=timezone.now() - age)
        return record

    def receive(self, body=REPLY, sender=NUMBERS[0], ref=None, token=TOKEN):
        self.n += 1
        ref = ref or f"in_{self.n}"
        response = self.gateway("received", {"messages": [{"id": ref, "from": sender, "text": body}]}, token=token)
        self.assertEqual(response.status_code, 204, response.content)
        return ref

    def tables(self):
        """(store text conversations, store inbound messages, HQ conversations, HQ messages, HQ do-not-text)."""
        return (
            InquiryConversation.objects.filter(provider="sms").count(),
            InquiryMessage.objects.filter(direction="in", conversation__provider="sms").count(),
            Conversation.objects.count(), ConversationMessage.objects.count(), DoNotText.objects.count(),
        )


class PollTests(SitePhoneCase):
    def test_a_site_phone_is_told_to_forward_when_every_switch_and_the_line_are_on(self):
        self.assertTrue(self.incoming())
        self.assertFalse(SmsDevice.objects.get(pk=self.line.pk).forwards_incoming)  # the server decides, not the phone's own switch

    def test_each_off_switch_tells_the_site_phone_not_to_forward(self):
        for name in ("SMS_INBOX_ENABLED", "SMS_SITE_LINES_ENABLED", "INQUIRY_SMS_ENABLED", "INQUIRIES_ENABLED"):
            with self.subTest(name), override_settings(**{name: False}):
                self.assertFalse(self.incoming(), name)

    def test_a_store_with_no_line_or_with_its_line_disabled_is_told_not_to(self):
        self.has_line.return_value = False
        self.assertFalse(self.incoming())

    def test_a_store_with_text_conversations_off_is_told_not_to(self):
        InquirySmsSettings.objects.filter(site=self.site).update(enabled=False)
        self.assertFalse(self.incoming())

    def test_a_phone_that_is_not_the_active_line_is_told_not_to(self):
        with mock.patch.object(sms, "_lined", return_value=self.bens_line):  # the store's active line is another phone
            self.assertFalse(self.incoming())
        with mock.patch.object(sms, "_lined", return_value=None):
            self.assertFalse(self.incoming())
        self.assertTrue(self.incoming())

    def test_the_platform_phone_poll_is_unchanged(self):
        self.assertTrue(self.incoming(PLATFORM_TOKEN))
        with override_settings(SMS_SITE_LINES_ENABLED=False):
            self.assertTrue(self.incoming(PLATFORM_TOKEN))  # the Site-line switch is not the platform phone's
        with override_settings(SMS_INBOX_ENABLED=False):
            self.assertFalse(self.incoming(PLATFORM_TOKEN))
        SmsDevice.objects.filter(pk=self.platform.pk).update(forwards_incoming=False)
        self.assertFalse(self.incoming(PLATFORM_TOKEN))


class KeepAndDropTests(SitePhoneCase):
    def test_a_reply_to_a_bulk_text_from_an_hour_ago_is_kept_in_that_stores_inbox(self):
        self.text_out(purpose="store_bulk", age=timedelta(hours=1))
        self.receive()
        self.assertEqual(self.tables(), (1, 1, 0, 0, 0))
        conversation = InquiryConversation.objects.get(provider="sms")
        self.assertEqual((conversation.site_id, conversation.contact_mobile, conversation.unread), (self.site.pk, NUMBERS[0], True))
        message = InquiryMessage.objects.get(direction="in", conversation__provider="sms")
        self.assertEqual((message.sender_kind, message.body), ("customer", REPLY))
        listed = [i for i in self.get(self.base + "conversations/").json()["items"] if i["channel"] == "sms"]
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0]["unread"])
        self.assertEqual([i for i in self.get(self.ben_base + "conversations/", client=self.ben).json()["items"] if i["channel"] == "sms"], [])

    def test_the_same_for_store_compose_and_store_reply(self):
        for number, purpose in ((NUMBERS[1], "store_compose"), (NUMBERS[2], "store_reply")):
            with self.subTest(purpose):
                self.text_out(purpose=purpose, to=number)
                self.receive(sender=number)
                self.assertTrue(InquiryConversation.objects.filter(site=self.site, contact_mobile=number, unread=True).exists())
        self.assertEqual(self.tables(), (2, 2, 0, 0, 0))

    def test_a_local_format_sender_is_cleaned_like_the_other_routes(self):
        self.text_out(purpose="store_bulk", to=NUMBERS[0])
        self.receive(sender="0" + NUMBERS[0][3:])
        self.assertEqual(InquiryConversation.objects.get(provider="sms").contact_mobile, NUMBERS[0])

    def test_a_reply_to_a_text_8_days_old_is_dropped(self):
        self.text_out(purpose="store_bulk", age=timedelta(days=8))
        self.receive()
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))

    def test_a_reply_to_a_text_just_under_7_days_old_is_kept(self):
        self.text_out(purpose="store_bulk", age=timedelta(days=6, hours=23))
        self.receive()
        self.assertEqual(self.tables(), (1, 1, 0, 0, 0))

    def test_a_text_from_a_number_the_store_never_texted_is_dropped(self):
        self.text_out(to=NUMBERS[1])
        self.receive(sender=NUMBERS[0])
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))

    def test_a_text_from_a_number_another_store_texted_is_dropped_and_shown_to_neither(self):
        self.text_out(site=self.ben_site, purpose="store_bulk", to=OTHER_STORE_NUMBER)
        self.receive(sender=OTHER_STORE_NUMBER)
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))
        for base, client in ((self.base, None), (self.ben_base, self.ben)):
            self.assertEqual([i for i in self.get(base + "conversations/", client=client).json()["items"] if i["channel"] == "sms"], [])

    def test_a_short_code_or_a_sender_name_is_dropped(self):
        for sender in ("22395", "GLOBE", "AMAXCARE"):
            self.text_out(to=sender)
            self.receive(sender=sender)
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))

    def test_a_reply_to_a_text_the_shared_phone_sent_is_dropped_on_the_site_phone(self):
        self.text_out(purpose="store_compose", device=self.platform)
        self.receive()
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))

    def test_a_reply_to_a_queued_text_is_dropped(self):
        self.text_out(purpose="store_compose", status="queued", device=None)
        SmsMessage.objects.update(device=None)
        self.receive()
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))

    def test_a_reply_to_a_dispatched_text_is_kept(self):
        self.text_out(purpose="store_compose", status="dispatched")
        self.receive()
        self.assertEqual(self.tables(), (1, 1, 0, 0, 0))

    def test_a_text_that_never_went_out_does_not_count_as_a_text_to_the_number(self):
        for state in ("failed", "expired", "cancelled"):
            if state in SmsMessage.Status.values:
                self.text_out(purpose="store_reply", status=state)
        self.receive()
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))

    def test_a_code_or_an_hq_text_to_the_number_does_not_count(self):
        SmsMessage.objects.create(
            recipient=NUMBERS[0], category="inbox", purpose="inbox_reply", priority=20, status="sent",
            expires_at=timezone.now() + timedelta(hours=1))
        SmsMessage.objects.create(
            recipient=NUMBERS[0], category="code", purpose="member_code", priority=10, status="sent",
            expires_at=timezone.now() + timedelta(hours=1))
        self.receive()
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))

    def test_a_repeated_upload_with_the_same_ref_is_stored_once(self):
        self.text_out(purpose="store_bulk")
        self.receive(ref="same_ref")
        self.receive(ref="same_ref")
        self.assertEqual(self.tables(), (1, 1, 0, 0, 0))

    def test_with_a_switch_off_the_body_is_not_read_and_nothing_is_stored(self):
        self.text_out(purpose="store_bulk")
        for name in ("SMS_INBOX_ENABLED", "SMS_SITE_LINES_ENABLED", "INQUIRY_SMS_ENABLED"):
            with self.subTest(name), override_settings(**{name: False}):
                with mock.patch.object(storing, "_clean") as clean:
                    self.gateway("received", {"messages": [{"id": f"x_{name}", "from": NUMBERS[0], "text": REPLY}]})
                clean.assert_not_called()
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))

    def test_a_store_with_text_conversations_off_keeps_nothing(self):
        self.text_out(purpose="store_bulk")
        InquirySmsSettings.objects.filter(site=self.site).update(enabled=False)
        self.receive()
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))

    def test_a_kept_and_a_dropped_text_in_one_call_keep_the_one_that_is_a_reply(self):
        self.text_out(purpose="store_bulk", to=NUMBERS[0])
        body = {"messages": [{"id": "a", "from": NUMBERS[1], "text": "who"}, {"id": "b", "from": NUMBERS[0], "text": REPLY}]}
        self.assertEqual(self.gateway("received", body).status_code, 204)
        self.assertEqual(self.tables(), (1, 1, 0, 0, 0))


class StopTests(SitePhoneCase):
    def test_a_stop_to_a_bulk_text_is_that_stores_opt_out_and_the_next_bulk_leaves_it_out(self):
        self.start()
        self.pump(1)
        SmsMessage.objects.filter(purpose="store_bulk", site=self.site).update(device=self.line)  # the helper's hand_in skips the phone
        sent = SmsMessage.objects.filter(purpose="store_bulk", site=self.site).first()
        self.assertIsNotNone(sent)
        self.receive(body="Stop", sender=sent.recipient)
        self.assertTrue(InquirySmsOptOut.objects.filter(site=self.site, mobile=sent.recipient).exists())
        self.assertFalse(InquirySmsOptOut.objects.filter(site=self.ben_site).exists())
        self.assertEqual(DoNotText.objects.count(), 0)  # never HQ's list
        counts = self.audience("all").json()
        self.assertEqual(counts["left_out"]["opted_out"], 1)

    def test_a_stop_from_a_number_the_store_did_not_text_is_dropped(self):
        self.receive(body="STOP", sender=NUMBERS[0])
        self.assertEqual(InquirySmsOptOut.objects.count(), 0)
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))


class LoggingTests(SitePhoneCase):
    def test_no_log_call_holds_a_number_or_a_text(self):
        self.text_out(purpose="store_bulk", to=NUMBERS[0])
        with self.assertLogs(level="DEBUG") as captured:
            self.receive(sender=NUMBERS[0])  # kept
            self.receive(sender=NUMBERS[1], body="a dropped reply")  # dropped
            logging.getLogger("apps.test").debug("sentinel")
        output = "\n".join(captured.output)
        for secret in (NUMBERS[0], NUMBERS[1], NUMBERS[0][3:], NUMBERS[1][3:], REPLY, "a dropped reply"):
            self.assertNotIn(secret, output)
        self.assertIn("sms_site_incoming_dropped count=1", output)


class PlatformPhoneUnchangedTests(SitePhoneCase):
    def test_the_platform_phone_stores_to_hq_and_routes_a_store_reply_to_the_store_as_before(self):
        self.receive(sender="+639189990000", token=PLATFORM_TOKEN, body="hello HQ")
        self.assertEqual(self.tables(), (0, 0, 1, 1, 0))
        self.text_out(purpose="store_reply", to=NUMBERS[0])
        self.receive(sender=NUMBERS[0], token=PLATFORM_TOKEN)
        self.assertEqual(self.tables(), (1, 1, 1, 1, 0))

    def test_a_store_bulk_text_is_not_a_shared_phone_route(self):
        self.assertNotIn("store_bulk", sms_inbox.ROUTED_PURPOSES)
        self.text_out(purpose="store_bulk", to=NUMBERS[0])
        self.receive(sender=NUMBERS[0], token=PLATFORM_TOKEN)
        self.assertEqual(self.tables(), (0, 0, 1, 1, 0))

    def test_the_platform_phone_stores_nothing_with_its_own_switch_off(self):
        SmsDevice.objects.filter(pk=self.platform.pk).update(forwards_incoming=False)
        self.receive(sender="+639189990000", token=PLATFORM_TOKEN)
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))


@override_settings(SMS_SITE_INCOMING_ENABLED=False)
class SwitchOffTests(SitePhoneCase):
    def test_with_the_switch_off_the_store_phone_is_told_not_to_forward_and_received_keeps_nothing(self):
        self.assertFalse(self.incoming())
        self.text_out(purpose="store_bulk", age=timedelta(hours=1))
        with mock.patch.object(storing, "_clean") as clean:
            self.receive()
        clean.assert_not_called()
        self.assertEqual(self.tables(), (0, 0, 0, 0, 0))

    def test_with_the_switch_off_the_platform_phone_is_unaffected(self):
        self.assertTrue(self.incoming(PLATFORM_TOKEN))
        self.receive(sender="+639189990000", token=PLATFORM_TOKEN)
        self.assertEqual(self.tables(), (0, 0, 1, 1, 0))

    def test_the_deploy_check_wants_the_prerequisites(self):
        from django.core.checks import run_checks

        with override_settings(SMS_SITE_INCOMING_ENABLED=True, SMS_SITE_LINES_ENABLED=False):
            self.assertIn("inquiries.E010", [e.id for e in run_checks(include_deployment_checks=True)])
        self.assertNotIn("inquiries.E010", [e.id for e in run_checks(include_deployment_checks=True)])
