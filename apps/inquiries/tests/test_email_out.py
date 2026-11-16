"""Email replies on a stored `em_` conversation: the recipient, the sender, the failures, the limits
and site isolation. Tests use the in-memory mail backend; nothing is sent."""
import logging
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.inquiries import outcomes, stored
from apps.inquiries.models import InquiryConversation, InquiryMessage, InquiryReply
from apps.inquiries.tests.base import REPLY_TEXT, switched_on
from apps.notifications.models import EmailMessage
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

EM_A = "em_" + "a1b2c3d4e5f6" * 2
EM_B = "em_" + "0f1e2d3c4b5a" * 2
SECRET_SMTP = "SMTP-535-PASSWORD-hunter2"


def make_email(site, *, ref, email="maria@visitor.test"):
    at = timezone.now()
    conversation = InquiryConversation.objects.create(
        site=site, provider=InquiryConversation.EMAIL, external_id=ref, stores_history=True,
        display_name="Maria Santos", contact_name="Maria Santos", contact_email=email,
        auth_flag=InquiryConversation.UNVERIFIED_SENDER, unread=True, last_message_at=at, last_inbound_at=at,
    )
    InquiryMessage.objects.create(
        site=site, conversation=conversation, direction=InquiryMessage.IN, sender_kind=InquiryMessage.CUSTOMER,
        body="Subject: Sizes\n\nDo you have medium?", created_at=at,
    )
    return conversation


@switched_on
class EmailOutTestCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.site_id = self.create_site(display_name="Ana Barley")["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.ben = self.other_client()
        self.ben_site_id = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben Herbs")["id"]
        self.ben_site = Site.objects.get(public_id=self.ben_site_id)
        self.base = f"/v1/sites/{self.site_id}/inquiries/"
        self.ben_base = f"/v1/sites/{self.ben_site_id}/inquiries/"
        self.conv = make_email(self.site, ref=EM_A, email="maria@visitor.test")
        self.ben_conv = make_email(self.ben_site, ref=EM_B, email="maria@visitor.test")  # the same typed address
        self.senders = override_settings(
            STORE_SENDERS=f"{self.site.public_id}=ana@ana-barley.test,{self.ben_site.public_id}=ben@ben-herbs.test",
            STORE_EMAILS_BRANDED=False,  # an inquiry reply is branded either way
        )
        self.senders.enable()
        self.addCleanup(self.senders.disable)
        mail.outbox.clear()  # the sign-ins and site creations above sent mail of their own
        EmailMessage.objects.all().delete()

    def reply(self, key="key-12345678", text=REPLY_TEXT, ref=EM_A, base=None, client=None, **extra):
        return self.send(
            "post", f"{base or self.base}conversations/{ref}/reply/",
            {"text": text, "idempotency_key": key, **extra}, client=client,
        )

    def ben_reply(self, **kw):
        return self.reply(ref=EM_B, base=self.ben_base, client=self.ben, **kw)


class SendTests(EmailOutTestCase):
    def test_a_reply_goes_to_the_conversations_own_address_from_the_stores_own_sender(self):
        body = self.reply().json()
        self.assertEqual((body["state"], body["error_code"]), ("sent", ""))
        (sent,) = mail.outbox
        self.assertEqual(sent.to, ["maria@visitor.test"])
        self.assertEqual((sent.cc, sent.bcc), ([], []))
        self.assertEqual(sent.from_email, '"Ana Barley" <ana@ana-barley.test>')
        self.assertEqual(sent.subject, "A message from Ana Barley")
        self.assertTrue(sent.body.startswith(REPLY_TEXT + "\n"))
        self.assertIn("You are receiving this because you contacted Ana Barley.", sent.body)
        self.assertEqual(sent.reply_to, [self.site.account.email])  # the owner, until a receiving domain exists
        self.assertNotIn("http", sent.body)  # no link injected
        self.assertEqual(EmailMessage.objects.get().category, "inquiry_reply")

    def test_the_reply_is_stored_and_the_audit_row_keeps_length_and_code_only(self):
        self.reply()
        message = InquiryMessage.objects.get(conversation=self.conv, direction="out")
        self.assertEqual((message.body, message.delivery_state, message.error_code), (REPLY_TEXT, "sent", ""))
        (row,) = InquiryReply.objects.filter(conversation=self.conv)
        self.assertEqual((row.state, row.error_code, row.body_length, row.message_id), ("sent", "", len(REPLY_TEXT), message.pk))
        self.conv.refresh_from_db()
        self.assertFalse(self.conv.unread)

    def test_the_request_cannot_name_a_recipient(self):
        for field in ("to", "email", "recipient", "cc", "bcc", "reply_to", "contact_email"):
            with self.subTest(field=field):
                self.assertEqual(self.reply(**{field: "evil@attacker.test"}).status_code, 400)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(InquiryReply.objects.exists())

    def test_an_unverified_sender_still_gets_the_reply_and_the_thread_shows_the_flag(self):
        self.assertEqual(self.reply().json()["state"], "sent")
        thread = self.get(f"{self.base}conversations/{EM_A}/").json()
        self.assertEqual((thread["channel"], thread["contact_email"]), ("email", "maria@visitor.test"))
        self.assertEqual(thread["replies"][0]["state"], "sent")

    def test_a_retry_with_the_same_key_sends_once(self):
        self.reply()
        self.assertEqual(self.reply().json()["state"], "sent")
        self.assertEqual(len(mail.outbox), 1)

    def test_a_closed_conversation_refuses(self):
        InquiryConversation.objects.filter(pk=self.conv.pk).update(status="closed")
        self.error(self.reply(), 409, "closed")
        self.assertEqual(mail.outbox, [])

    def test_a_chat_reply_still_sends_no_email(self):
        from apps.inquiries.tests.base import WC_REF, make_stored

        make_stored(self.site, ref=WC_REF, email="chat@visitor.test")
        self.assertEqual(self.reply(ref=WC_REF).json()["state"], "sent")
        self.assertEqual(mail.outbox, [])


class SiteIsolationTests(EmailOutTestCase):
    def test_site_a_and_site_b_each_send_with_their_own_name_and_sender(self):
        self.reply()
        self.ben_reply()
        first, second = mail.outbox
        self.assertEqual((first.from_email, first.subject), ('"Ana Barley" <ana@ana-barley.test>', "A message from Ana Barley"))
        self.assertEqual((second.from_email, second.subject), ('"Ben Herbs" <ben@ben-herbs.test>', "A message from Ben Herbs"))
        for sent, other in ((first, "Ben"), (second, "Ana")):
            text = sent.message().as_string() + sent.body
            self.assertNotIn(other, text)
            self.assertNotIn(f"{other.lower()}", sent.from_email.lower())
        self.assertEqual(second.reply_to, [self.ben_site.account.email])

    def test_another_sites_conversation_is_not_found_and_sends_nothing(self):
        self.error(self.reply(ref=EM_B), 404, "not_found")
        self.error(self.reply(ref=EM_A, base=self.ben_base, client=self.ben), 404, "not_found")
        self.assertEqual(mail.outbox, [])
        self.assertFalse(InquiryReply.objects.exists())


class FailureTests(EmailOutTestCase):
    def test_no_store_sender_means_sender_not_ready_and_nothing_is_sent(self):
        with override_settings(STORE_SENDERS=f"{self.ben_site.public_id}=ben@ben-herbs.test"):
            body = self.reply().json()
            self.assertEqual((body["state"], body["error_code"]), ("failed", "sender_not_ready"))
            self.assertEqual(body["message"], outcomes.EMAIL_TEXTS["sender_not_ready"])
            self.assertEqual(self.ben_reply().json()["state"], "sent")  # B has one; A's gap is A's only
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["maria@visitor.test"])
        self.assertFalse(EmailMessage.objects.filter(recipient="maria@visitor.test").exclude(status="sent").exists())
        row = InquiryReply.objects.get(site=self.site)
        self.assertEqual((row.state, row.error_code), ("failed", "not_configured"))  # a code CODES already holds
        self.assertEqual(InquiryMessage.objects.get(site=self.site, direction="out").error_code, "sender_not_ready")

    def test_a_missing_sender_is_not_replaced_by_the_platform_address(self):
        with override_settings(STORE_SENDERS=""):
            self.assertEqual(self.reply().json()["error_code"], "sender_not_ready")
        self.assertEqual(mail.outbox, [])

    def test_a_conversation_with_no_address_fails_with_no_recipient(self):
        InquiryConversation.objects.filter(pk=self.conv.pk).update(contact_email="")
        body = self.reply().json()
        self.assertEqual((body["state"], body["error_code"]), ("failed", "no_recipient"))
        self.assertEqual(mail.outbox, [])
        self.assertEqual(InquiryReply.objects.get().error_code, "not_found")

    @override_settings(EMAIL_HOST_PASSWORD="CREDENTIAL-PW-9", EMAIL_HOST_USER="CREDENTIAL-USER-9")
    def test_a_failed_send_shows_a_fixed_code_and_no_mail_server_text_anywhere(self):
        with self.assertLogs(level=logging.DEBUG) as logs:
            with mock.patch("apps.notifications.services._deliver", side_effect=RuntimeError(SECRET_SMTP)):
                response = self.reply()
        body = response.json()
        self.assertEqual((body["state"], body["error_code"]), ("failed", "email_failed"))
        self.assertEqual(body["message"], outcomes.EMAIL_TEXTS["email_failed"])
        everything = [response.content.decode(), "\n".join(logs.output)]
        for model in (InquiryReply, InquiryMessage, EmailMessage):
            everything += [str(vars(row)) for row in model.objects.all() if model is not InquiryMessage or row.direction == "out"]
        for blob in everything:
            for secret in (SECRET_SMTP, "CREDENTIAL-PW-9", "CREDENTIAL-USER-9"):
                self.assertNotIn(secret, blob)
        self.assertIn("RuntimeError", "\n".join(logs.output))
        row = InquiryReply.objects.get()
        self.assertEqual((row.state, row.error_code), ("failed", "provider_down"))
        message = InquiryMessage.objects.get(direction="out")
        self.assertEqual((message.delivery_state, message.error_code), ("failed", "email_failed"))
        self.assertEqual(EmailMessage.objects.get().status, "failed")

    def test_a_failed_reply_does_not_move_the_conversation(self):
        before = InquiryConversation.objects.get(pk=self.conv.pk).last_message_at
        with mock.patch("apps.notifications.services._deliver", side_effect=RuntimeError("x")):
            self.reply()
        after = InquiryConversation.objects.get(pk=self.conv.pk)
        self.assertEqual(after.last_message_at, before)
        self.assertTrue(after.unread)


class LimitTests(EmailOutTestCase):
    def test_the_hourly_limit_blocks_the_site_and_only_that_site(self):
        for i in range(stored.EMAIL_HOUR[1]):
            self.assertEqual(self.reply(key=f"key-{i:08d}").json()["state"], "sent")
        blocked = self.reply(key="key-over-limit").json()
        self.assertEqual((blocked["state"], blocked["error_code"]), ("blocked_rate", "rate_limited"))
        self.assertEqual(blocked["message"], outcomes.EMAIL_TEXTS["rate_limited"])
        self.assertEqual(len(mail.outbox), stored.EMAIL_HOUR[1])
        self.assertEqual(self.ben_reply().json()["state"], "sent")  # B's budget is its own
        self.assertEqual(len(mail.outbox), stored.EMAIL_HOUR[1] + 1)
        self.assertEqual(InquiryReply.objects.get(idempotency_key="key-over-limit").state, "blocked_rate")

    def test_the_daily_limit(self):
        with mock.patch.object(stored, "EMAIL_DAY", ("inq-email-day", 2, 86400)):
            self.assertEqual(self.reply(key="key-00000001").json()["state"], "sent")
            self.assertEqual(self.reply(key="key-00000002").json()["state"], "sent")
            self.assertEqual(self.reply(key="key-00000003").json()["state"], "blocked_rate")
            self.assertEqual(self.ben_reply(key="key-00000004").json()["state"], "sent")
