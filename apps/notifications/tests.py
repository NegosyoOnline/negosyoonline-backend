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
