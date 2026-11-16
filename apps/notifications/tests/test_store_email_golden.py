"""send_store_email with its defaults is byte-identical to what it produced before the optional
`reply_to`, `footer_reason`, `branded` and `log_detail` arguments existed. The strings below were
saved from the code at 49dd122, before the change."""
import hashlib
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from apps.notifications import store_email
from apps.notifications.services import EmailNotSent
from apps.sites.tests.base import make_account, make_site

BRAND = {"name": "Live for More", "color": "#2e7d32", "email": "hi@store.test", "mobile": "+639181112222",
         "address": "1 Main St"}
BODY_ONE = "Hi Ana,\n\nYour code is 482913. It expires.\nSee https://x.test/a?b=1."
TEXT_ONE = (
    "Hi Ana,\n\nYour code is 482913. It expires.\nSee https://x.test/a?b=1.\n\n--\nLive for More\n"
    "hi@store.test\n+639181112222\n1 Main St\n"
    "You are receiving this because of your order or account with Live for More.\n"
)
HTML_ONE = "4604b1ca4125f2369c7251a45da913a197203ef921e303d10c21afbcfd5deef1"
TEXT_TWO = (
    "Plain body\n\nTwo\n\n--\nLive for More\nhi@store.test\n+639181112222\n1 Main St\n"
    "You are receiving this because of your order or account with Live for More.\n"
)
HTML_TWO = "71872f33ca7ce80d1c2d2c8dd97562291c426473e9fd9fafe24e66c66f83815e"


def digest(html):
    return hashlib.sha256(html.encode()).hexdigest()


class GoldenRenderTests(TestCase):
    def test_the_defaults_render_exactly_as_before(self):
        text, html = store_email.render_store(BRAND, subject="Subj", body=BODY_ONE, code="482913")
        self.assertEqual(text, TEXT_ONE)
        self.assertEqual(digest(html), HTML_ONE)
        text, html = store_email.render_store(
            BRAND, subject="Subj", body="Plain body\n\nTwo", cta=("Go", "https://x.test/"),
        )
        self.assertEqual(text, TEXT_TWO)
        self.assertEqual(digest(html), HTML_TWO)

    def test_an_explicit_default_footer_changes_nothing(self):
        same = store_email.default_footer_reason(BRAND)
        text, html = store_email.render_store(BRAND, subject="Subj", body=BODY_ONE, code="482913", footer_reason=same)
        self.assertEqual((text, digest(html)), (TEXT_ONE, HTML_ONE))

    def test_a_footer_reason_replaces_only_that_sentence_in_text_and_html(self):
        text, html = store_email.render_store(
            BRAND, subject="Subj", body=BODY_ONE, code="482913", footer_reason="Because you <wrote> to us.",
        )
        self.assertEqual(text, TEXT_ONE.replace(store_email.default_footer_reason(BRAND), "Because you <wrote> to us."))
        self.assertIn("Because you &lt;wrote&gt; to us.", html)
        self.assertNotIn("your order or account", html)


@override_settings(STORE_SENDERS="anas-store=orders@store.test", DEFAULT_FROM_EMAIL="NegosyoOnline <no-reply@negosyoonline.com>")
class GoldenSendTests(TestCase):
    def setUp(self):
        self.account = make_account()
        self.site = make_site(self.account)

    @override_settings(STORE_EMAILS_BRANDED=True)
    def test_branded_default_send(self):
        store_email.send_store_email(self.site, to="b@x.test", category="c", subject="S", body="Hello there")
        (sent,) = mail.outbox
        text, html = store_email.render_store(store_email.brand_for(self.site), subject="S", body="Hello there")
        self.assertEqual(sent.body, text)
        self.assertEqual(sent.alternatives, [(html, "text/html")])
        self.assertEqual(sent.reply_to, [self.account.email])
        self.assertEqual(sent.from_email, '"Ana\'s Store" <orders@store.test>')
        self.assertIn("because of your order or account with Ana&#x27;s Store.", html)

    @override_settings(STORE_EMAILS_BRANDED=False)
    def test_plain_default_send(self):
        store_email.send_store_email(self.site, to="b@x.test", category="c", subject="S", body="Hello there")
        (sent,) = mail.outbox
        self.assertEqual(sent.body, "Hello there")
        self.assertFalse(getattr(sent, "alternatives", []))
        self.assertEqual(sent.reply_to, [self.account.email])

    @override_settings(STORE_EMAILS_BRANDED=True)
    def test_a_given_reply_to_is_checked_like_the_default_one(self):
        store_email.send_store_email(
            self.site, to="b@x.test", category="c", subject="S", body="Hi", reply_to="thread@in.test",
        )
        self.assertEqual(mail.outbox[0].reply_to, ["thread@in.test"])
        for bad in ("not an address", "a@b.test\nBcc: c@d.test"):
            with self.subTest(bad=bad), self.assertRaises(EmailNotSent):
                store_email.send_store_email(self.site, to="b@x.test", category="c", subject="S", body="Hi", reply_to=bad)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(STORE_EMAILS_BRANDED=False)
    def test_branded_true_uses_the_branded_layout_whatever_the_setting_says(self):
        store_email.send_store_email(
            self.site, to="b@x.test", category="c", subject="S", body="Hi", branded=True, footer_reason="Because.",
        )
        (sent,) = mail.outbox
        self.assertTrue(sent.body.rstrip().endswith("Because."))
        self.assertEqual(sent.from_email, '"Ana\'s Store" <orders@store.test>')

    def test_log_detail_false_keeps_the_exception_text_out_of_the_log(self):
        with mock.patch("apps.notifications.services._deliver", side_effect=RuntimeError("PASSWORD-123")):
            with self.assertLogs("apps.notifications.services", level="DEBUG") as logs:
                with self.assertRaises(EmailNotSent):
                    store_email.send_store_email(
                        self.site, to="b@x.test", category="c", subject="S", body="Hi", log_detail=False,
                    )
        self.assertNotIn("PASSWORD-123", "\n".join(logs.output))
        self.assertIn("RuntimeError", "\n".join(logs.output))
