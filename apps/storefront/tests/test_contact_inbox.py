"""With INQUIRY_CONTACT_INBOX_ENABLED on, the contact form also stores a copy as an email
conversation. The email to the store is the same either way."""
import logging
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.billing.tests.base import make_subscription
from apps.inquiries import stored
from apps.inquiries.models import InquiryChannelSettings, InquiryConversation, InquiryMessage
from apps.sites.models import Site
from apps.storefront.tests.test_contact import FORM, SENT, ContactTestCase

ON = override_settings(INQUIRIES_ENABLED=True, INQUIRY_CONTACT_INBOX_ENABLED=True)


def mail_shape(message):
    return (message.subject, message.body, message.from_email, message.to, message.reply_to, message.cc, message.bcc)


def inquiry_sql(queries):
    return [q["sql"] for q in queries if "inquiries_" in q["sql"]]


class InboxCopyTests(ContactTestCase):
    def setUp(self):
        super().setUp()
        InquiryChannelSettings.objects.create(site=self.site, email_enabled=True)  # the site's own switch

    @ON
    def test_a_form_message_becomes_a_new_unverified_email_conversation(self):
        self.assert_answer(self.post(), 200, SENT)
        (conversation,) = InquiryConversation.objects.all()
        self.assertEqual(
            (conversation.site_id, conversation.provider, conversation.stores_history, conversation.status),
            (self.site.pk, "email", True, "open"),
        )
        self.assertTrue(conversation.external_id.startswith("em_"))
        self.assertEqual(
            (conversation.contact_name, conversation.display_name, conversation.contact_email),
            ("Maria Santos", "Maria Santos", "maria@visitor.test"),
        )
        self.assertEqual((conversation.auth_flag, conversation.unread), ("unverified_sender", True))
        self.assertIsNotNone(conversation.last_message_at)
        self.assertEqual(conversation.last_message_at, conversation.last_inbound_at)
        self.assertEqual((conversation.visitor_hash, conversation.thread_token), ("", ""))
        (message,) = InquiryMessage.objects.all()
        self.assertEqual((message.direction, message.sender_kind), ("in", "customer"))
        self.assertEqual(message.body, "Subject: Barley for my lola\n\n" + FORM["message"].strip())

    @ON
    def test_the_same_address_twice_makes_two_conversations(self):
        self.post()
        self.post()
        self.assertEqual(InquiryConversation.objects.count(), 2)
        self.assertEqual(len({c.external_id for c in InquiryConversation.objects.all()}), 2)

    @ON
    def test_the_body_is_cut_to_4000_characters(self):
        self.post(message="x" * 4000)
        self.assertEqual(len(InquiryMessage.objects.get().body), 4000)

    def test_the_email_is_the_same_with_the_switch_on_and_off(self):
        self.post()
        with ON:
            self.post(visitor_ip="203.0.113.51")
        off, on = mail.outbox
        self.assertEqual(mail_shape(off), mail_shape(on))
        keep = lambda m: [(k, v) for k, v in m.message().items() if k not in ("Date", "Message-ID")]  # noqa: E731
        self.assertEqual(keep(off), keep(on))
        self.assertEqual(InquiryConversation.objects.count(), 1)

    def test_switch_off_stores_nothing_and_touches_no_inquiries_table(self):
        with CaptureQueriesContext(connection) as queries:
            self.assert_answer(self.post(), 200, SENT)
        self.assertEqual(inquiry_sql(queries), [])
        self.assertFalse(InquiryConversation.objects.exists())

    @override_settings(INQUIRY_CONTACT_INBOX_ENABLED=True, INQUIRIES_ENABLED=False)
    def test_the_inbox_switch_alone_does_nothing(self):
        with CaptureQueriesContext(connection) as queries:
            self.assert_answer(self.post(), 200, SENT)
        self.assertEqual(inquiry_sql(queries), [])

    @ON
    def test_with_the_tables_absent_nothing_is_read_or_written(self):
        with mock.patch("apps.inquiries.switch.stored_tables_exist", return_value=False):
            with CaptureQueriesContext(connection) as queries:
                self.assert_answer(self.post(), 200, SENT)
        self.assertEqual(inquiry_sql(queries), [])
        self.assertEqual(len(mail.outbox), 1)

    @ON
    def test_the_honeypot_stores_nothing(self):
        with CaptureQueriesContext(connection) as queries:
            self.assert_answer(self.post(website="http://spam.test"), 200, SENT)
        self.assertEqual(inquiry_sql(queries), [])
        self.assertEqual(mail.outbox, [])

    @ON
    def test_a_failure_to_store_still_answers_sent_and_logs_the_class_only(self):
        with mock.patch.object(stored, "add_contact_message", side_effect=RuntimeError("SECRET maria@visitor.test")):
            with self.assertLogs("apps.storefront.services", level=logging.DEBUG) as logs:
                self.assert_answer(self.post(), 200, SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("RuntimeError", "\n".join(logs.output))
        self.assertNotIn("SECRET", "\n".join(logs.output))
        self.assertNotIn("maria@visitor.test", "\n".join(logs.output))

    @ON
    def test_nothing_is_stored_when_the_email_could_not_go(self):
        Site.objects.filter(pk=self.site.pk).update(contact_email="")
        self.assertEqual(self.post().status_code, 409)
        self.assertFalse(InquiryConversation.objects.exists())

    @ON
    def test_the_site_cap_keeps_the_email_but_stores_nothing_more(self):
        with mock.patch.object(stored, "CONTACT_SITE_DAY", ("inq-contact-site", 2, 86400)):
            for i in range(3):
                self.assert_answer(self.post(visitor_ip=f"203.0.113.{60 + i}", email=f"p{i}@visitor.test"), 200, SENT)
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(InquiryConversation.objects.count(), 2)

    @ON
    def test_the_sender_cap_is_per_address_and_per_site_and_hashes_the_address(self):
        other = make_subscription(email="ben@example.test", name="bens-store").site
        with mock.patch.object(stored, "CONTACT_SENDER_DAY", ("inq-contact-sender", 2, 86400)):
            for i in range(3):
                self.post(visitor_ip=f"203.0.113.{70 + i}")
            self.assertEqual(InquiryConversation.objects.filter(site=self.site).count(), 2)
            self.post(visitor_ip="203.0.113.80", email="someone@else.test")
            self.assertEqual(InquiryConversation.objects.filter(site=self.site).count(), 3)
            # the same address on another site has its own count
            for i in range(2):
                stored.add_contact_message(other, name="M", email="maria@visitor.test", subject="s", message="m")
            self.assertEqual(InquiryConversation.objects.filter(site=other).count(), 2)
        self.assertEqual(len(mail.outbox), 4)
        self.assertFalse(any("maria@visitor.test" in str(key) for key in getattr(cache, "_cache", {})))

    @ON
    def test_the_conversation_belongs_only_to_the_hosts_site(self):
        other = make_subscription(email="ben@example.test", name="bens-store").site
        self.post()
        self.assertEqual(InquiryConversation.objects.filter(site=self.site).count(), 1)
        self.assertFalse(InquiryConversation.objects.filter(site=other).exists())
        self.assertFalse(InquiryMessage.objects.filter(site=other).exists())
        self.assertEqual(InquiryMessage.objects.get().site_id, self.site.pk)


class SiteSwitchTests(ContactTestCase):
    """Stored only when the global switch AND the site's email_enabled are both on. The email goes
    out in every combination."""

    def combo(self, global_on, site_on):
        InquiryChannelSettings.objects.update_or_create(site=self.site, defaults={"email_enabled": site_on})
        with override_settings(INQUIRIES_ENABLED=True, INQUIRY_CONTACT_INBOX_ENABLED=global_on):
            self.assert_answer(self.post(), 200, SENT)

    def test_the_email_always_goes_and_a_copy_is_stored_only_when_both_are_on(self):
        for global_on in (False, True):
            for site_on in (False, True):
                with self.subTest(global_on=global_on, site_on=site_on):
                    mail.outbox.clear()
                    InquiryConversation.objects.all().delete()
                    cache.clear()
                    self.combo(global_on, site_on)
                    self.assertEqual(len(mail.outbox), 1)
                    self.assertEqual(InquiryConversation.objects.count(), 1 if (global_on and site_on) else 0)

    @ON
    def test_a_site_with_no_settings_row_stores_nothing(self):
        self.assert_answer(self.post(), 200, SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(InquiryConversation.objects.exists())
        self.assertFalse(InquiryChannelSettings.objects.exists())  # reading creates no row

    @ON
    def test_another_sites_switch_does_not_affect_this_site(self):
        other = make_subscription(email="ben@example.test", name="bens-store").site
        InquiryChannelSettings.objects.create(site=other, email_enabled=True)
        self.assert_answer(self.post(), 200, SENT)  # this site's switch is off
        self.assertFalse(InquiryConversation.objects.exists())
        InquiryChannelSettings.objects.create(site=self.site, email_enabled=True)
        InquiryChannelSettings.objects.filter(site=other).update(email_enabled=False)
        self.assert_answer(self.post(visitor_ip="203.0.113.99"), 200, SENT)
        (conversation,) = InquiryConversation.objects.all()
        self.assertEqual(conversation.site_id, self.site.pk)
