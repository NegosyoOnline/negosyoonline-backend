import tempfile
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from django.utils import timezone

from apps.sites.tests.base import make_account, make_site
from apps.members import messaging
from apps.notifications import store_email
from apps.notifications.models import EmailMessage
from apps.notifications.services import EmailNotSent, send_email
from apps.sites.models import SiteBusinessProfile

BRAND = {"name": "Live for More", "color": "#2e7d32", "email": "hi@store.test", "mobile": "+639181112222",
         "address": "1 Main St"}
BARE = {"name": "Bare", "color": "", "email": "", "mobile": "", "address": ""}
NEGOSYO = "NegosyoOnline <no-reply@negosyoonline.com>"


class RenderTests(TestCase):
    def render(self, brand=BRAND, body="Hi Ana,\n\nYour code is 482913. It expires.\nSee https://x.test/a?b=1.", **kw):
        return store_email.render_store(brand, subject="S", body=body, code=kw.pop("code", "482913"), **kw)

    def test_text_is_the_body_plus_footer(self):
        text, _ = self.render()
        self.assertTrue(text.startswith("Hi Ana,\n\nYour code is 482913. It expires.\nSee https://x.test/a?b=1.\n\n--\n"))
        self.assertIn("Live for More\nhi@store.test\n+639181112222\n1 Main St\n", text)
        self.assertTrue(text.rstrip().endswith("your order or account with Live for More."))

    def test_code_once_in_html_and_text_body_and_link_is_a_link(self):
        text, html = self.render()
        self.assertEqual(html.count("482913"), 1)
        self.assertEqual(text.count("482913"), 1)
        self.assertIn('<a href="https://x.test/a?b=1"', html)
        self.assertIn("max-width:600px", html)
        self.assertIn("padding:24px 16px", html)

    def test_hostile_values_are_escaped(self):
        brand = {**BRAND, "name": '<script>alert(1)</script>"', "address": "<b>x</b>"}
        _, html = self.render(brand, body="<img src=x onerror=1>\n\nHi")
        for bad in ("<script>", "<b>x</b>", "<img src=x"):
            self.assertNotIn(bad, html)
        self.assertIn("&lt;script&gt;", html)

    def test_colour_accepted_or_refused(self):
        self.assertEqual(store_email.safe_color("#12ab34"), "#12ab34")
        for bad in ("red", "#12", "url(x)", "#12ab34;x", "", None):
            self.assertEqual(store_email.safe_color(bad), store_email.DEFAULT_COLOR)
        _, html = self.render({**BRAND, "color": "red;}</style><script>"})
        self.assertNotIn("<script>", html)
        self.assertIn(store_email.DEFAULT_COLOR, html)

    def test_contrast_picks_readable_button_text(self):
        self.assertEqual(store_email.button_text_color("#0b3d91"), "#ffffff")
        self.assertEqual(store_email.button_text_color("#ffeb99"), "#111827")
        _, html = self.render({**BRAND, "color": "#ffeb99"}, cta=("Go", "https://x.test/"))
        self.assertIn("color:#111827;text-decoration:none", html)
        _, none = self.render(cta=("Go", "javascript:alert(1)"))
        self.assertNotIn("javascript:", none)

    def test_bare_store_has_no_empty_rows_and_no_platform_line(self):
        text, html = self.render(BARE)
        self.assertNotIn("None", text + html)
        self.assertNotIn("NegosyoOnline", text + html)
        self.assertNotIn("Powered", html)
        self.assertIn("Bare", html)
        self.assertEqual(html.count('<p style="margin:0 0 4px 0;">'), 0)


class StoreCase(TestCase):
    def setUp(self):
        self.owner = make_account("ana@example.test")
        self.owner.email_verified_at = timezone.now()
        self.owner.save()
        self.site = make_site(self.owner)
        self.send = lambda **kw: store_email.send_store_email(
            self.site, to="buyer@x.test", category="pack_verified", subject="Subj", body="Body 482913 text", **kw
        )


class SwitchOffTests(StoreCase):
    @override_settings(STORE_EMAILS_BRANDED=False, STORE_SENDERS="anas-store=orders@store.test")
    def test_off_is_byte_for_byte_today(self):
        self.send(code="482913")
        sent = mail.outbox[0]
        self.assertEqual(sent.body, "Body 482913 text")
        self.assertEqual(getattr(sent, "alternatives", []), [])
        self.assertEqual(sent.from_email, '"Ana\'s Store" <no-reply@negosyoonline.com>')
        self.assertEqual(sent.reply_to, [self.owner.email])
        self.assertEqual(sent.subject, "Subj")

    @override_settings(STORE_EMAILS_BRANDED=False)
    def test_off_unverified_owner_has_no_reply_to(self):
        self.owner.email_verified_at = None
        self.owner.save()
        self.send()
        self.assertEqual(mail.outbox[0].reply_to, [])


@override_settings(STORE_EMAILS_BRANDED=True)
class SwitchOnTests(StoreCase):
    def test_on_without_sender_is_multipart_from_negosyo_address_under_store_name(self):
        self.send(code="482913")
        sent = mail.outbox[0]
        self.assertEqual(sent.alternatives[0][1], "text/html")
        self.assertEqual(sent.from_email, '"Ana\'s Store" <no-reply@negosyoonline.com>')
        self.assertEqual(sent.reply_to, [self.owner.email])
        self.assertTrue(sent.body.startswith("Body 482913 text\n\n--\nAna's Store"))
        self.assertNotIn(self.owner.email, sent.body + sent.alternatives[0][0])

    @override_settings(STORE_SENDERS="anas-store=orders@liveformore.test")
    def test_store_sender_by_subdomain_name(self):
        self.send()
        sent = mail.outbox[0]
        self.assertEqual(sent.from_email, '"Ana\'s Store" <orders@liveformore.test>')
        self.assertEqual(sent.reply_to, [self.owner.email])

    def test_store_sender_by_site_id_and_other_stores_unaffected(self):
        with override_settings(STORE_SENDERS=f"{self.site.public_id}=orders@liveformore.test, nope=x@y.test"):
            self.send()
            other = make_site(make_account("ben@example.test"), name="bens-store", display_name="Ben")
            store_email.send_store_email(other, to="b@x.test", category="c", subject="s", body="b")
        self.assertIn("orders@liveformore.test", mail.outbox[0].from_email)
        self.assertIn("no-reply@negosyoonline.com", mail.outbox[1].from_email)

    @override_settings(STORE_SENDERS="anas-store=bad address,anas-store=a@b.test\nBcc: c@d.test")
    def test_bad_sender_pairs_are_ignored(self):
        senders, problems = store_email.parse_senders(
            "anas-store=bad address, x=a@b.test\nBcc: c@d.test, ok=ok@b.test")
        self.assertEqual(senders, {"ok": "ok@b.test"})
        self.assertTrue(problems)
        self.send()
        self.assertIn("no-reply@negosyoonline.com", mail.outbox[0].from_email)

    def test_from_address_with_line_break_is_refused_by_send_email(self):
        with self.assertLogs("apps.notifications.services", level="ERROR"):
            with self.assertRaises(EmailNotSent):
                send_email(to="a@x.test", category="c", subject="s", body="b", sender_name="S",
                           from_address="a@b.test\nBcc: c@d.test")
        self.assertEqual(mail.outbox, [])
        self.assertEqual(EmailMessage.objects.get().status, EmailMessage.Status.FAILED)

    @override_settings(STORE_SENDERS="anas-store=orders@liveformore.test")
    def test_blocked_from_name_falls_back_but_address_is_the_stores(self):
        self.site.display_name = "GCash Deals"
        self.site.save()
        with self.assertLogs("apps.notifications.store_email", level="WARNING"):
            self.send()
        self.assertEqual(mail.outbox[0].from_email, '"Your store" <orders@liveformore.test>')

    def test_guard_words(self):
        for bad in ("NegosyoOnline Shop", "My PayPal Store", "BDO Loans", "gcash", "PayMaya Load", "maya bank agent", "Maya Wallet Shop"):
            self.assertEqual(store_email.safe_from_name(bad), "Your store", bad)
        for fine in ("Santé Barley", "Live for More", "Abdominal Care", "Bpitt", "Maya's Boutique", "Maya"):
            self.assertEqual(store_email.safe_from_name(fine), fine, fine)

    def test_line_break_in_name_still_refuses(self):
        with mock.patch.object(messaging, "sender", return_value=("A\nB", "")):
            with self.assertLogs("apps.notifications.services", level="ERROR"):
                with self.assertRaises(EmailNotSent):
                    self.send()
        self.assertEqual(mail.outbox, [])

    def test_disposed_recipient_still_refused(self):
        with mock.patch("apps.notifications.services.is_disposed_address", return_value=True):
            with self.assertLogs("apps.notifications.services", level="WARNING"):
                with self.assertRaises(EmailNotSent):
                    self.send()

    def test_backend_down_fails_the_record_and_raises_only_email_not_sent(self):
        with mock.patch("apps.notifications.services.EmailMultiAlternatives.send", side_effect=OSError("down")):
            with self.assertLogs("apps.notifications.services", level="ERROR"):
                with self.assertRaises(EmailNotSent):
                    self.send()
        self.assertEqual(EmailMessage.objects.get().status, EmailMessage.Status.FAILED)

    def test_brand_uses_store_contact_details_not_owner_email(self):
        SiteBusinessProfile.objects.filter(site=self.site).update(business_address="9 Elm St")
        self.site.contact_email = "shop@store.test"
        self.site.contact_mobile = "+639170000000"
        self.site.accent_color = "#112233"
        self.site.save()
        brand = store_email.brand_for(self.site)
        self.assertEqual(
            (brand["email"], brand["mobile"], brand["address"], brand["color"]),
            ("shop@store.test", "+639170000000", "9 Elm St", "#112233"),
        )
        self.assertNotIn(self.owner.email, brand.values())


@override_settings(STORE_EMAILS_BRANDED=True, STORE_SENDERS="anas-store=orders@liveformore.test")
class IsolationTests(TestCase):
    def test_account_and_billing_style_mail_never_gets_the_store_address(self):
        send_email(to="a@x.test", category="signin_code", subject="s", body="b")
        send_email(to="a@x.test", category="billing", subject="s", body="b", reply_to="support@negosyoonline.com")
        for sent in mail.outbox:
            self.assertEqual(sent.from_email, NEGOSYO)
            self.assertNotIn("liveformore", sent.from_email)


class CallerGoldenTests(StoreCase):
    """The moved callers: switch off, the mail is what it was; on, the same words with a wrapper."""

    @override_settings(STORE_EMAILS_BRANDED=False)
    def test_pack_notice_off_matches_today(self):
        from apps.pack import notices
        subject, body = notices.VERIFIED
        values = {"name": "Ana's Store", "first": "Maria", "no": "N1", "url": "", "link": "https://dash.test/x/"}
        store_email.send_store_email(self.site, to="m@x.test", category="pack_verified",
                                     subject=subject.format(**values), body=body.format(**values))
        sent = mail.outbox[0]
        self.assertEqual(sent.body, "Hi Maria,\n\nThe store verified your payment for order N1. We will let you know when your pack ships.\n\nContinue your registration: https://dash.test/x/\n\nAna's Store")
        self.assertEqual(getattr(sent, "alternatives", []), [])


class PreviewTests(TestCase):
    def test_all_kinds_written_and_nothing_sent(self):
        with tempfile.TemporaryDirectory() as folder:
            call_command("preview_store_email", "all", "--out-dir", folder)
            files = sorted(p.name for p in Path(folder).iterdir())
            self.assertEqual(len(files), 40)
            html = (Path(folder) / "member_signin.html").read_text()
            self.assertEqual(html.count("123456"), 1)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(EmailMessage.objects.count(), 0)

    def test_registered_notice_preview_has_the_video_button_only_when_set(self):
        for url, expected in (("https://videos.example.test/ordering", True), ("", False)):
            with self.settings(PACK_ORDERING_VIDEO_URL=url), tempfile.TemporaryDirectory() as folder:
                out = Path(folder) / "a.html"
                call_command("preview_store_email", "pack_registered", "--out", str(out))
                self.assertEqual("https://videos.example.test/ordering" in out.read_text(), expected)
                self.assertEqual("coming soon" in out.read_text(), not expected)

    def test_single_kind_text_and_minimal(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "a.txt"
            call_command("preview_store_email", "pack_verified", "--text", "--minimal", "--out", str(out))
            self.assertIn("Bare Store", out.read_text())
