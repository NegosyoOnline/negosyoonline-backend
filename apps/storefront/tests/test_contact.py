"""POST /storefront/v1/contact/: a visitor's "Send a Message" form, emailed to the store."""
import logging
from email.header import decode_header, make_header
from email.utils import parseaddr
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.billing.tests.base import make_subscription
from apps.common import throttle
from apps.notifications.models import EmailMessage
from apps.sites.models import Site, SiteBusinessProfile
from apps.storefront import services
from apps.storefront.tests.base import (
    HOST, NO_SUCH_ENDPOINT, NOT_FOUND, SECRET, UNAUTHENTICATED, SurfaceTestCase,
)

CONTACT = "/storefront/v1/contact/"
SENT = {"status": "sent"}
STORE_EMAIL = "store@example.test"
VISITOR = "203.0.113.50"
FORM = {
    "name": "Maria Santos", "email": "maria@visitor.test", "subject": "Barley for my lola",
    "message": "Hello,\nDo you deliver to Cebu?\n\nSalamat!",
}
PRIVATE = ("Maria Santos", "maria@visitor.test", "Barley for my lola", "deliver to Cebu")


def header(message, name):
    return str(make_header(decode_header(message.message()[name])))


class ContactTestCase(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.site = make_subscription().site
        Site.objects.filter(pk=self.site.pk).update(contact_email=STORE_EMAIL)

    def post(self, host=HOST, visitor_ip=VISITOR, secret=SECRET, **fields):
        body = {"host": host, "visitor_ip": visitor_ip, **FORM, **fields}
        headers = {"HTTP_X_REAL_IP": "198.51.100.200"}  # the storefronts server, not the visitor
        if secret is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
        return self.client.post(
            CONTACT, {key: value for key, value in body.items() if value is not None},
            content_type="application/json", secure=True, **headers,
        )

    def assert_nothing_sent(self):
        self.assertEqual(mail.outbox, [])
        self.assertFalse(EmailMessage.objects.exists())


class SendTests(ContactTestCase):
    def test_the_message_goes_to_the_store_with_the_visitor_as_reply_to(self):
        self.assert_answer(self.post(), 200, SENT)

        (sent,) = mail.outbox
        self.assertEqual(sent.to, [STORE_EMAIL])
        self.assertEqual(sent.reply_to, ["maria@visitor.test"])
        self.assertEqual(header(sent, "Reply-To"), "maria@visitor.test")
        self.assertEqual(
            parseaddr(header(sent, "From")),
            ("Ana's Store", parseaddr(settings.DEFAULT_FROM_EMAIL)[1]),
        )
        self.assertEqual(sent.subject, "[Ana's Store] New message from Maria Santos")
        self.assertEqual(
            sent.body,
            "New message from the Ana's Store website contact form.\n\n"
            "Name: Maria Santos\nEmail: maria@visitor.test\nSubject: Barley for my lola\n\n"
            "Hello,\nDo you deliver to Cebu?\n\nSalamat!\n",
        )
        self.assertEqual((sent.cc, sent.bcc), ([], []))

    def test_a_store_name_with_an_accent_or_a_quote_is_the_sender_name(self):
        for name in ("Santé Barley Cebu", 'Ana "The Best" Store', "Reyes, Cruz & Co. <shop>"):
            with self.subTest(name=name):
                mail.outbox.clear()
                Site.objects.filter(pk=self.site.pk).update(display_name=name)

                self.assert_answer(self.post(), 200, SENT)

                self.assertEqual(
                    parseaddr(header(mail.outbox[0], "From")),
                    (name, parseaddr(settings.DEFAULT_FROM_EMAIL)[1]),
                )

    def test_the_recipient_is_the_address_the_site_call_shows(self):
        Site.objects.filter(pk=self.site.pk).update(contact_email="")
        SiteBusinessProfile.objects.filter(site=self.site).update(
            privacy_contact_email="privacy@example.test"
        )

        for expected in ("privacy@example.test", STORE_EMAIL):
            with self.subTest(expected=expected):
                mail.outbox.clear()
                shown = self.call().json()["business"]["contact_email"]
                self.assert_answer(self.post(), 200, SENT)
                self.assertEqual(mail.outbox[0].to, [shown])
                self.assertEqual(shown, expected)
                Site.objects.filter(pk=self.site.pk).update(contact_email=STORE_EMAIL)

    def test_the_owner_account_email_is_never_used(self):
        self.assert_answer(self.post(), 200, SENT)

        text = repr(mail.outbox[0].message().as_string())
        self.assertNotIn(self.site.account.email, text)
        self.assertNotIn("ana@example.test", text)

    def test_an_optional_subject_and_the_field_limits(self):
        self.assert_answer(
            self.post(
                name=" Maria\r\n Santos " + "x" * 200, email="  MARIA@Visitor.TEST ", subject="",
                message="  " + "m" * 5000 + "  ",
            ),
            200, SENT,
        )

        (sent,) = mail.outbox
        name = ("Maria Santos " + "x" * 200)[:120]
        self.assertEqual(sent.subject, f"[Ana's Store] New message from {name}")
        self.assertEqual(sent.reply_to, ["maria@visitor.test"])
        self.assertIn("Subject: (none)\n\n" + "m" * 4000 + "\n", sent.body)
        self.assertNotIn("m" * 4001, sent.body)

    def test_one_store_never_receives_another_stores_message(self):
        ben = make_subscription(email="ben@example.test", name="bens-store").site
        Site.objects.filter(pk=ben.pk).update(contact_email="ben-store@example.test")

        self.assert_answer(self.post("bens-store.negosyotest.com"), 200, SENT)

        self.assertEqual(mail.outbox[0].to, ["ben-store@example.test"])


class NotSentTests(ContactTestCase):
    def test_a_store_with_no_contact_email(self):
        Site.objects.filter(pk=self.site.pk).update(contact_email="")

        response = self.post()

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "no_contact_email")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assert_nothing_sent()

    def test_a_filled_spam_trap_is_answered_as_sent_and_nothing_is_sent(self):
        real = self.post().content
        mail.outbox.clear()
        EmailMessage.objects.all().delete()

        for _ in range(services.CONTACT_LIMIT + 5):  # never counted, so never limited
            trapped = self.post(website="https://spam.example")
            self.assert_answer(trapped, 200, SENT)
            self.assertEqual(trapped.content, real)

        self.assert_nothing_sent()
        self.assert_answer(self.post(), 200, SENT)  # the visitor address is not used up

    def test_missing_and_invalid_fields(self):
        cases = (
            ({"name": "  "}, {"name": ["Please enter your name."]}),
            ({"email": "not-an-email"}, {"email": ["Please enter a valid email address."]}),
            ({"email": None}, {"email": ["Please enter a valid email address."]}),
            ({"message": " \n "}, {"message": ["Please enter a message."]}),
            (
                {"name": "", "email": "", "message": ""},
                {
                    "name": ["Please enter your name."],
                    "email": ["Please enter a valid email address."],
                    "message": ["Please enter a message."],
                },
            ),
        )
        for number, (fields, errors) in enumerate(cases):
            with self.subTest(fields=fields):
                response = self.post(visitor_ip=f"203.0.113.{number}", **fields)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()["error"]["code"], "invalid_request")
                self.assertEqual(response.json()["error"]["fields"], errors)
        self.assert_nothing_sent()

    def test_a_body_that_is_not_the_form_is_refused_without_echoing_it(self):
        bodies = (  # a list holds no host, so it gets the surface's 404
            ("not json", 400), ('["Maria Santos"]', 404),
            ('{"host": "%s", "name": {"first": "Maria Santos"}}' % HOST, 400),
        )
        for body, status in bodies:
            with self.subTest(body=body):
                response = self.client.post(
                    CONTACT, body, content_type="application/json", secure=True,
                    HTTP_AUTHORIZATION=f"Bearer {SECRET}",
                )
                self.assertEqual(response.status_code, status, response.content)
                self.assertNotIn("Maria", response.content.decode())
        self.assert_nothing_sent()

    def test_a_mail_server_failure_is_503_and_recorded_as_failed(self):
        with mock.patch.object(mail.EmailMessage, "send", side_effect=OSError("smtp down")):
            with self.assertLogs("apps.notifications.services", level="ERROR") as logs:
                response = self.post()

        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json()["error"]["code"], "email_not_sent")
        self.assertEqual(EmailMessage.objects.get().status, EmailMessage.Status.FAILED)
        for private in PRIVATE:
            self.assertNotIn(private, "\n".join(logs.output))

    def test_an_unserved_host_is_the_surfaces_404(self):
        other = make_subscription(email="eve@example.test", name="eves-store").site
        Site.objects.filter(pk=other.pk).update(template="other", contact_email=STORE_EMAIL)

        answers = {
            self.assert_answer(self.post(host), 404, NOT_FOUND)
            for host in (
                "nobody-here.negosyotest.com", "eves-store.negosyotest.com",
                "anas-store.example.com", "www.negosyotest.com", "", None,
            )
        }

        self.assertEqual(answers, {self.call("nobody-here.negosyotest.com").content})
        self.assert_nothing_sent()

    def test_the_switch_off_answers_404_before_anything_else(self):
        with override_settings(STOREFRONT_ENABLED=False):
            for secret in (SECRET, None):
                self.assert_answer(self.post(secret=secret), 404, NO_SUCH_ENDPOINT)
        with override_settings(STOREFRONT_SERVER_SECRET=""):
            self.assert_answer(self.post(), 404, NO_SUCH_ENDPOINT)
        self.assert_nothing_sent()

    def test_a_missing_or_wrong_credential_is_401(self):
        for secret in (None, "wrong", SECRET[:-1]):
            with self.subTest(secret=secret is None):
                self.assert_answer(self.post(secret=secret), 401, UNAUTHENTICATED)
        self.assert_nothing_sent()

    def test_only_a_post_reaches_it(self):
        for method in ("get", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = self.call(path=CONTACT, method=method)
                self.assertEqual(response.status_code, 405)
        self.assert_nothing_sent()


class LimitTests(ContactTestCase):
    def assert_limited(self, response):
        self.assertEqual(response.status_code, 429, response.content)
        self.assertEqual(response.json()["error"]["code"], "rate_limited")
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_the_eleventh_message_in_an_hour_from_one_visitor_is_refused(self):
        for _ in range(services.CONTACT_LIMIT):
            self.assert_answer(self.post(), 200, SENT)

        self.assert_limited(self.post())
        self.assert_limited(self.post(visitor_ip=f" {VISITOR} "))
        self.assertEqual(len(mail.outbox), 10)
        self.assertEqual(EmailMessage.objects.count(), 10)

    def test_the_limit_follows_the_visitor_not_the_store_or_the_calling_server(self):
        ben = make_subscription(email="ben@example.test", name="bens-store").site
        Site.objects.filter(pk=ben.pk).update(contact_email="ben-store@example.test")
        for _ in range(services.CONTACT_LIMIT):
            self.post()

        self.assert_limited(self.post("bens-store.negosyotest.com"))
        self.assert_answer(self.post(visitor_ip="203.0.113.51"), 200, SENT)
        self.assert_answer(self.post(visitor_ip="2001:db8::1"), 200, SENT)

    def test_a_refused_form_counts_and_a_missing_address_shares_one_count(self):
        for _ in range(services.CONTACT_LIMIT):
            self.assertEqual(self.post(name="").status_code, 400)
        self.assert_limited(self.post())

        for number in range(services.CONTACT_LIMIT):
            address = ("", None, "not-an-address", "203.0.113.999")[number % 4]
            self.assert_answer(self.post(visitor_ip=address), 200, SENT)
        self.assert_limited(self.post(visitor_ip="also-not-an-address"))

    def test_the_count_is_kept_for_one_hour_and_then_starts_again(self):
        self.assertEqual(
            (services.CONTACT_LIMIT, services.CONTACT_WINDOW), (10, 3600)
        )
        with mock.patch.object(throttle.cache, "add", wraps=cache.add) as add:
            for _ in range(services.CONTACT_LIMIT):
                self.post()
        key = f"throttle:storefront-contact:{VISITOR}"
        self.assertEqual({call.args for call in add.call_args_list}, {(key, 0, 3600)})
        self.assert_limited(self.post())

        cache.delete(key)  # what the cache does itself when the hour is over

        self.assert_answer(self.post(), 200, SENT)


class HeaderInjectionTests(ContactTestCase):
    BREAKS = ("\n", "\r\n", "\r", "\x0b", "\x0c", "\x1c", "\x85", " ", " ", "\x00")

    def assert_one_clean_email(self):
        (sent,) = mail.outbox
        raw = sent.message()
        self.assertEqual(sent.to, [STORE_EMAIL])
        self.assertEqual((sent.cc, sent.bcc), ([], []))
        self.assertIsNone(raw["Bcc"])
        self.assertIsNone(raw["Cc"])
        self.assertIsNone(raw["X-Injected"])
        self.assertEqual(len(raw.get_all("To")), 1)
        self.assertEqual(len(raw.get_all("From")), 1)
        self.assertEqual(len(raw.get_all("Reply-To")), 1)
        self.assertEqual(len(raw.get_all("Subject")), 1)
        return sent

    def test_a_line_break_in_the_name_or_the_subject_adds_no_header(self):
        for number, gap in enumerate(self.BREAKS):
            for field in ("name", "subject"):
                with self.subTest(gap=repr(gap), field=field):
                    mail.outbox.clear()
                    value = f"Maria{gap}Bcc: thief@example.test{gap}X-Injected: yes"
                    response = self.post(visitor_ip=f"203.0.113.{number}", **{field: value})

                    self.assert_answer(response, 200, SENT)
                    sent = self.assert_one_clean_email()
                    for char in self.BREAKS:
                        self.assertNotIn(char, sent.subject)

    def test_an_email_that_is_not_one_address_is_refused(self):
        tries = (
            "maria@visitor.test\nBcc: thief@example.test",
            "maria@visitor.test\r\nBcc: thief@example.test",
            "maria@visitor.test, thief@example.test",
            "maria@visitor.test>, <thief@example.test",
            "Maria <maria@visitor.test>",
            "maria@visitor.test thief@example.test",
            "maria@visitor.test\x00",
        )
        for number, email in enumerate(tries):
            with self.subTest(email=repr(email)):
                response = self.post(visitor_ip=f"203.0.113.{number}", email=email)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(set(response.json()["error"]["fields"]), {"email"})
        self.assert_nothing_sent()

    def test_a_store_name_with_a_line_break_adds_no_header(self):
        Site.objects.filter(pk=self.site.pk).update(
            display_name="Ana's Store\r\nBcc: thief@example.test"
        )

        self.assert_answer(self.post(), 200, SENT)

        sent = self.assert_one_clean_email()
        self.assertEqual(
            parseaddr(header(sent, "From"))[0], "Ana's Store Bcc: thief@example.test"
        )

    def test_headers_in_the_message_stay_in_the_body(self):
        self.assert_answer(
            self.post(message="Hi\r\n\r\nBcc: thief@example.test\r\nX-Injected: yes"), 200, SENT
        )

        self.assert_one_clean_email()


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(f"{record.getMessage()} {record.args!r} {record.__dict__!r}")


class NothingStoredTests(ContactTestCase):
    def test_no_row_no_log_line_and_no_cache_entry_holds_the_message(self):
        records = _Records()
        root = logging.getLogger()
        level = root.level
        root.addHandler(records)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, level)
        self.addCleanup(root.removeHandler, records)

        with CaptureQueriesContext(connection) as queries:
            self.assert_answer(self.post(), 200, SENT)
            self.assertEqual(self.post(name="").status_code, 400)
            self.assert_answer(self.post(website="x"), 200, SENT)
            for _ in range(services.CONTACT_LIMIT):
                self.post()
            self.assertEqual(self.post().status_code, 429)
            with mock.patch.object(mail.EmailMessage, "send", side_effect=OSError("down")):
                self.assertEqual(self.post(visitor_ip="203.0.113.51").status_code, 503)
            self.assertEqual(self.post("nobody-here.negosyotest.com").status_code, 404)
            self.assertEqual(self.post(secret="wrong").status_code, 401)
            Site.objects.filter(pk=self.site.pk).update(contact_email="")
            self.assertEqual(self.post(visitor_ip="203.0.113.52").status_code, 409)

        written = [
            query["sql"] for query in queries.captured_queries
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertTrue(written)  # the email record was written, so the search means something
        for sql in written[:-1]:  # the last is this test's own change to the site
            self.assertIn("notifications_emailmessage", sql)
        self.assertTrue(records.lines)
        for text in written + records.lines:
            for private in (*PRIVATE, VISITOR, "203.0.113.51", "203.0.113.52"):
                self.assertNotIn(private, text)

    def test_the_email_record_holds_the_store_address_the_category_and_the_status_only(self):
        self.assert_answer(self.post(), 200, SENT)

        row = EmailMessage.objects.values().get()
        self.assertEqual(
            set(row),
            {"id", "recipient", "category", "status", "provider_reference", "created_at", "sent_at"},
        )
        self.assertEqual(
            (row["recipient"], row["category"], row["status"], row["provider_reference"]),
            (STORE_EMAIL, "storefront_contact", "sent", ""),
        )
        self.assertIsNotNone(row["sent_at"])

    def test_the_only_cache_entry_is_the_visitors_count(self):
        with mock.patch.object(throttle.cache, "add", wraps=cache.add) as add, \
                mock.patch.object(throttle.cache, "set", wraps=cache.set) as put:
            self.assert_answer(self.post(), 200, SENT)

        self.assertEqual(
            [call.args for call in add.call_args_list],
            [(f"throttle:storefront-contact:{VISITOR}", 0, 3600)],
        )
        put.assert_not_called()
