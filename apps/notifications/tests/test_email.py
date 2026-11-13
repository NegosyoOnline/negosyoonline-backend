from email.header import decode_header, make_header
from email.utils import parseaddr
from unittest import mock

from django.conf import settings
from django.core import mail
from django.test import TestCase

from apps.notifications.models import EmailMessage
from apps.notifications.services import EmailNotSent, send_email


class SendEmailTests(TestCase):
    def test_sends_the_email_and_records_it_without_the_body(self):
        message = send_email(
            to="ana@example.test", category="signin_code", subject="Your code", body="123456"
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ana@example.test"])
        self.assertEqual(mail.outbox[0].body, "123456")
        self.assertEqual(message.status, EmailMessage.Status.SENT)
        self.assertIsNotNone(message.sent_at)
        self.assertNotIn("body", [field.name for field in EmailMessage._meta.get_fields()])

    def test_a_failed_send_is_recorded_and_raised(self):
        with mock.patch("apps.notifications.services.send_mail", side_effect=OSError("smtp down")):
            with self.assertLogs("apps.notifications.services", level="ERROR"):
                with self.assertRaises(EmailNotSent) as raised:
                    send_email(
                        to="ana@example.test", category="signin_code", subject="s", body="b"
                    )

        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(EmailMessage.objects.get().status, EmailMessage.Status.FAILED)


class EmailHeaderSafetyTests(TestCase):
    """A14: sender, recipient and subject text can never add a header to an email."""

    def assert_refused(self, **overrides):
        fields = {"to": "ana@example.test", "category": "signin_code", "subject": "s", "body": "b"}
        fields.update(overrides)
        with self.assertLogs("apps.notifications.services", level="ERROR"):
            with self.assertRaises(EmailNotSent):
                send_email(**fields)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(EmailMessage.objects.get().status, EmailMessage.Status.FAILED)

    def test_a_subject_with_a_line_break_is_refused(self):
        for subject in ("Hello\nBcc: thief@example.test", "Hello\r\nBcc: thief@example.test"):
            with self.subTest(subject=subject):
                EmailMessage.objects.all().delete()
                self.assert_refused(subject=subject)

    def test_a_recipient_with_a_line_break_is_refused(self):
        self.assert_refused(to="ana@example.test\nBcc: thief@example.test")

    def test_a_sender_with_a_line_break_is_refused(self):
        with self.settings(DEFAULT_FROM_EMAIL="NegosyoOnline\nBcc: thief@example.test <a@b.test>"):
            self.assert_refused()

    def test_a_safe_message_carries_no_extra_recipient_header(self):
        send_email(to="ana@example.test", category="signin_code", subject="Your code", body="1")
        headers = mail.outbox[0].message()
        self.assertIsNone(headers["Bcc"])
        self.assertIsNone(headers["Cc"])
        self.assertEqual(headers["To"], "ana@example.test")


class SenderNameAndReplyToTests(TestCase):
    """The two optional parts of an email: the name beside our From address and the Reply-To."""

    FIELDS = {"to": "store@example.test", "category": "storefront_contact", "subject": "s", "body": "b"}

    def sender(self):
        raw = mail.outbox[0].message()["From"]
        return parseaddr(str(make_header(decode_header(raw))))

    def test_without_them_the_email_is_exactly_as_before(self):
        send_email(**self.FIELDS)

        self.assertEqual(mail.outbox[0].from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(mail.outbox[0].reply_to, [])
        self.assertIsNone(mail.outbox[0].message()["Reply-To"])

    def test_the_name_changes_and_the_from_address_stays_ours(self):
        ours = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
        for name in ("Ana's Store", "Santé Barley", 'Ana "Best" <x@y.test>', "a\\b, c"):
            with self.subTest(name=name):
                mail.outbox.clear()
                send_email(**self.FIELDS, sender_name=name, reply_to="maria@visitor.test")

                self.assertEqual(self.sender(), (name, ours))
                self.assertEqual(mail.outbox[0].message()["Reply-To"], "maria@visitor.test")
                self.assertEqual(mail.outbox[0].to, ["store@example.test"])

    def test_each_works_alone(self):
        send_email(**self.FIELDS, sender_name="Ana's Store")
        send_email(**self.FIELDS, reply_to="maria@visitor.test")

        self.assertIsNone(mail.outbox[0].message()["Reply-To"])
        self.assertEqual(self.sender()[0], "Ana's Store")
        self.assertEqual(mail.outbox[1].from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(mail.outbox[1].reply_to, ["maria@visitor.test"])

    def test_a_line_break_or_a_bad_reply_to_sends_nothing_and_is_recorded_as_failed(self):
        tries = (
            {"sender_name": "Ana\nBcc: thief@example.test"},
            {"sender_name": "Ana\r\nBcc: thief@example.test"},
            {"reply_to": "maria@visitor.test\nBcc: thief@example.test"},
            {"reply_to": "maria@visitor.test\r\nBcc: thief@example.test"},
            {"reply_to": "maria@visitor.test, thief@example.test"},
            {"reply_to": "Maria <maria@visitor.test>"},
            {"reply_to": "not-an-address"},
            {"sender_name": "Ana", "subject": "Hello\nBcc: thief@example.test"},
        )
        for extra in tries:
            with self.subTest(extra=extra):
                EmailMessage.objects.all().delete()
                with self.assertLogs("apps.notifications.services", level="ERROR") as logs:
                    with self.assertRaises(EmailNotSent):
                        send_email(**{**self.FIELDS, **extra})
                self.assertEqual(mail.outbox, [])
                self.assertEqual(EmailMessage.objects.get().status, EmailMessage.Status.FAILED)
                if "subject" not in extra:
                    self.assertNotIn("thief", "\n".join(logs.output))
                    self.assertNotIn("maria", "\n".join(logs.output))
