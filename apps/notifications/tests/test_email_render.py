import tempfile
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.accounts import codes, services
from apps.accounts.models import Account
from apps.notifications import email_render
from apps.notifications.services import EmailNotSent, send_email

IP = "203.0.113.9"


class RenderTests(TestCase):
    def test_every_kind_returns_subject_text_and_html(self):
        for kind in email_render.kinds():
            subject, text, html = email_render.render(
                kind, code="123456", last4="4567", subject="S", heading="H", change="C"
            )
            self.assertTrue(subject and text and html.startswith("<!DOCTYPE html>"), kind)
            self.assertIn("support@negosyoonline.com", text)
            self.assertIn("/terms/", html)
            self.assertIn("/privacy/", html)

    def test_code_is_in_one_element_and_in_text_with_the_ignore_line(self):
        _, text, html = email_render.render("signin", code="123456")
        self.assertEqual(html.count(">123456<"), 1)
        self.assertIn("Code: 123456", text)
        self.assertIn("works once and expires in 10 minutes", text)
        self.assertIn("you can ignore this email", text)

    def test_hostile_values_are_escaped(self):
        _, _, html = email_render.render(
            "connection_notice", subject="x", heading="<script>alert(1)</script>", change="<b>hi</b>"
        )
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>hi</b>", html)

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            email_render.render("nope")


class SendEmailHtmlTests(TestCase):
    def test_html_body_makes_a_multipart_email_with_reply_to(self):
        send_email(
            to="a@example.test", category="signin_code", subject="s", body="plain",
            html_body="<p>hi</p>", reply_to="support@negosyoonline.com",
        )
        sent = mail.outbox[0]
        self.assertEqual(sent.body, "plain")
        self.assertEqual(sent.alternatives[0][1], "text/html")
        self.assertEqual(sent.reply_to, ["support@negosyoonline.com"])
        self.assertEqual(sent.from_email, "NegosyoOnline <no-reply@negosyoonline.com>")

    def test_without_html_it_is_plain(self):
        send_email(to="a@example.test", category="signin_code", subject="s", body="plain")
        self.assertEqual(mail.outbox[0].alternatives, [])

    def test_a_reply_to_with_a_line_break_is_refused_with_html(self):
        with self.assertLogs("apps.notifications.services", level="ERROR"):
            with self.assertRaises(EmailNotSent):
                send_email(
                    to="a@example.test", category="x", subject="s", body="b",
                    html_body="<p>x</p>", reply_to="a@example.test\nBcc: b@example.test",
                )
        self.assertEqual(mail.outbox, [])


class AccountEmailSwitchTests(TestCase):
    def setUp(self):
        cache.clear()

    def _sign_in_email(self):
        services.request_signin_code("ana@example.test", IP)
        return mail.outbox[-1]

    def test_switch_off_is_the_plain_text_it_always_was(self):
        sent = self._sign_in_email()
        code = sent.body.split("is ")[1][:6]
        self.assertEqual(
            sent.body,
            f"Your sign-in code is {code}. It expires in 10 minutes.\n\n"
            "If you did not ask for this code, you can ignore this email.",
        )
        self.assertEqual(sent.alternatives, [])
        self.assertEqual(sent.reply_to, [])

    @override_settings(ACCOUNT_EMAILS_BRANDED=True)
    def test_switch_on_is_branded_with_reply_to_support(self):
        sent = self._sign_in_email()
        self.assertEqual(sent.reply_to, ["support@negosyoonline.com"])
        self.assertEqual(sent.alternatives[0][1], "text/html")
        self.assertEqual(sent.subject, "Your NegosyoOnline sign-in code")

    @override_settings(ACCOUNT_EMAILS_BRANDED=True)
    def test_notices_are_branded_too(self):
        account = Account.objects.create(email="ana@example.test", email_verified_at=None)
        services._notify_connection_change(account, "google", connected=True)
        self.assertEqual(len(mail.outbox[-1].alternatives), 1)


class FailedSendTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_resend_down_leaves_the_person_able_to_ask_again_at_once(self):
        with mock.patch("apps.notifications.services.send_mail", side_effect=OSError("down")):
            with self.assertLogs("apps.notifications.services", level="ERROR"):
                with self.assertRaises(EmailNotSent):
                    services.request_signin_code("ana@example.test", IP)
        services.request_signin_code("ana@example.test", IP)  # not a 429
        self.assertEqual(len(mail.outbox), 1)

    def test_a_failed_send_gives_the_hourly_allowance_back(self):
        with mock.patch("apps.notifications.services.send_mail", side_effect=OSError("down")):
            with self.assertLogs("apps.notifications.services", level="ERROR"):
                for _ in range(codes.PER_EMAIL_PER_HOUR + 2):
                    with self.assertRaises(EmailNotSent):
                        services.request_signin_code("ana@example.test", IP)


class PreviewCommandTests(TestCase):
    def test_writes_every_kind_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            call_command("preview_account_email", "all", "--out-dir", folder)
            names = {p.name for p in Path(folder).iterdir()}
            self.assertEqual(len(names), 2 * len(email_render.kinds()))
            self.assertIn("signin.html", names)
        self.assertEqual(mail.outbox, [])

    def test_single_kind_text_to_a_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "t.txt"
            call_command("preview_account_email", "signin", "--text", "--out", str(target))
            self.assertIn("123456", target.read_text())
