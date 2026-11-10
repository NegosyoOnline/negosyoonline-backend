from unittest import mock

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


class NotificationsAdminMaskingTests(TestCase):
    def test_the_email_list_shows_the_recipient_masked(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        operator = get_user_model().objects.create_superuser("op", "op@operators.test", "pw")
        self.client.force_login(operator)
        EmailMessage.objects.create(recipient="ana.reyes@example.test", category="signin_code")

        url = reverse("admin:notifications_emailmessage_changelist")
        response = self.client.get(url, secure=True)

        self.assertContains(response, "a***@example.test")
        self.assertNotContains(response, "ana.reyes@example.test")
