"""The storefront chat routes: send and poll, site isolation, the hashed secret, the pause, the
abuse limits, and the switch."""
import hashlib
import logging
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.billing.tests.base import make_subscription
from apps.inquiries import webchat
from apps.inquiries.models import InquiryChannelSettings, InquiryConversation, InquiryMessage
from apps.inquiries.tests.base import chat_on
from apps.sites.models import Site
from apps.storefront.tests.base import HOST, NOT_FOUND, SECRET, SurfaceTestCase

SEND = "/storefront/v1/chat/send/"
POLL = "/storefront/v1/chat/poll/"
CONTACT = "/storefront/v1/chat/contact/"
IP = "203.0.113.60"
OTHER_HOST = "bens-store.negosyotest.com"


@override_settings(INQUIRIES_ENABLED=True, INQUIRY_WEBCHAT_ENABLED=True)
class ChatTestCase(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.site = make_subscription().site
        self.ben = make_subscription(email="ben@example.test", name="bens-store").site
        chat_on(self.site)
        chat_on(self.ben)

    def post(self, path, host=HOST, secret=SECRET, ip="198.51.100.200", **fields):
        headers = {"HTTP_X_REAL_IP": ip}
        if secret is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
        return self.client.post(
            path, {key: value for key, value in {"host": host, **fields}.items() if value is not None},
            content_type="application/json", secure=True, **headers,
        )

    def opened(self, seconds=10):
        return (timezone.now() - timedelta(seconds=seconds)).isoformat()

    def send(self, text="Do you deliver to Cebu?", host=HOST, visitor_ip=IP, **fields):
        fields.setdefault("opened_at", self.opened())
        return self.post(SEND, host=host, visitor_ip=visitor_ip, text=text, **fields)

    def poll(self, secret, host=HOST, visitor_ip=IP, **fields):
        return self.post(POLL, host=host, visitor_ip=visitor_ip, visitor_secret=secret, **fields)

    def first(self, **kw):
        response = self.send(**kw)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def contact(self, secret, contact, host=HOST, visitor_ip=IP, **fields):
        return self.post(CONTACT, host=host, visitor_ip=visitor_ip, visitor_secret=secret, contact=contact, **fields)

    def owner_reply(self, conversation, text="Yes, we deliver."):
        return InquiryMessage.objects.create(
            site=conversation.site, conversation=conversation, direction="out", sender_kind="owner",
            body=text, delivery_state="sent",
        )


class SendTests(ChatTestCase):
    def test_the_first_send_makes_a_conversation_and_returns_a_secret_once(self):
        body = self.first(name="Maria", email="Maria@Visitor.test")
        self.assertEqual(body["state"], "open")
        secret = body["visitor_secret"]
        self.assertEqual(len(secret), 43)
        conversation = InquiryConversation.objects.get(site=self.site)
        self.assertEqual(conversation.provider, "webchat")
        self.assertTrue(conversation.external_id.startswith("wc_"))
        self.assertTrue(conversation.stores_history and conversation.unread)
        self.assertEqual((conversation.contact_name, conversation.contact_email), ("Maria", "maria@visitor.test"))
        self.assertEqual(conversation.visitor_hash, hashlib.sha256(secret.encode()).hexdigest())
        again = self.first(visitor_secret=secret, text="One more thing")
        self.assertEqual(again["visitor_secret"], "")
        self.assertEqual(InquiryConversation.objects.filter(site=self.site).count(), 1)
        self.assertEqual(InquiryMessage.objects.filter(site=self.site).count(), 2)

    def test_the_secret_is_never_stored_in_clear_or_logged(self):
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        root = logging.getLogger()
        level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, level)
        self.addCleanup(root.removeHandler, handler)
        with CaptureQueriesContext(connection) as queries:
            secret = self.first()["visitor_secret"]
            self.poll(secret)
            self.send(visitor_secret=secret, text="Another")
        for sql in (q["sql"] for q in queries.captured_queries):
            self.assertNotIn(secret, sql)
        for row in InquiryConversation.objects.values():
            self.assertNotIn(secret, str(row))
        for record in records:
            self.assertNotIn(secret, record.getMessage())
        self.assertEqual(self.poll(secret).json()["visitor_secret"], "")

    def test_a_closed_conversation_starts_a_new_one_with_the_same_secret(self):
        secret = self.first()["visitor_secret"]
        InquiryConversation.objects.filter(site=self.site).update(status="closed")
        again = self.first(visitor_secret=secret, text="Hello again")
        self.assertEqual(again["state"], "open")
        self.assertEqual(again["visitor_secret"], "")
        self.assertEqual(InquiryConversation.objects.filter(site=self.site, status="open").count(), 1)
        self.assertEqual(InquiryConversation.objects.filter(site=self.site).count(), 2)

    def test_an_older_conversation_the_owner_reopened_takes_the_next_send(self):
        secret = self.first()["visitor_secret"]
        InquiryConversation.objects.filter(site=self.site).update(status="closed")
        self.first(visitor_secret=secret, text="Second chat")  # B, made after A was closed
        older, newer = InquiryConversation.objects.filter(site=self.site).order_by("pk")
        InquiryConversation.objects.filter(pk=newer.pk).update(status="closed")
        InquiryConversation.objects.filter(pk=older.pk).update(status="open")  # the owner reopens A
        again = self.send(visitor_secret=secret, text="Third message")
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.json()["state"], "open")
        self.assertEqual(InquiryConversation.objects.filter(site=self.site).count(), 2)
        self.assertEqual(InquiryMessage.objects.filter(conversation=older).count(), 2)
        self.assertEqual(self.poll(secret).json()["state"], "open")

    def test_the_race_fallback_also_prefers_the_open_conversation(self):
        secret = self.first()["visitor_secret"]
        InquiryConversation.objects.filter(site=self.site).update(status="closed")
        self.first(visitor_secret=secret, text="Second chat")
        older, newer = InquiryConversation.objects.filter(site=self.site).order_by("pk")
        InquiryConversation.objects.filter(pk=newer.pk).update(status="closed")
        InquiryConversation.objects.filter(pk=older.pk).update(status="open")
        # a send that read the newer, closed row just before the reopen (the race): it tries to
        # start a new conversation, the database refuses, and the message joins the open one
        stale = InquiryConversation.objects.get(pk=newer.pk)
        saved = webchat._store(
            self.site, stale, webchat.hash_of(secret), "", "", "Raced message", timezone.now(),
        )
        self.assertEqual(saved.pk, older.pk)
        self.assertEqual(InquiryMessage.objects.filter(conversation=older, body="Raced message").count(), 1)

    def test_a_wrong_secret_is_treated_as_none_and_gets_a_new_one(self):
        body = self.first(visitor_secret="x" * 43)
        self.assertEqual(len(body["visitor_secret"]), 43)
        self.assertNotEqual(body["visitor_secret"], "x" * 43)

    def test_the_site_comes_only_from_the_host_and_extra_fields_are_refused(self):
        for extra in ({"site_id": "s_x"}, {"site": 1}, {"template": "sante"}):
            self.assertEqual(self.send(**extra).status_code, 400)
        self.assertFalse(InquiryMessage.objects.exists())

    def test_the_text_rules(self):
        for text in ("", "   ", "x" * 1001, "bad\x00text", "bad\x07text",
                     "http://a.test http://b.test https://c.test", "www.a.test www.b.test www.c.test"):
            with self.subTest(text=text[:20]):
                self.assertEqual(self.send(text=text).status_code, 422)
        self.assertFalse(InquiryMessage.objects.exists())
        self.assertEqual(self.send(text="Line one\nLine two http://a.test http://b.test").status_code, 200)
        self.assertEqual(self.send(text="x" * 1000, visitor_ip="203.0.113.61").status_code, 200)

    def test_name_and_email_limits(self):
        body = self.first(name="N" * 200)
        self.assertEqual(InquiryConversation.objects.get(site=self.site).contact_name, "N" * 80)
        self.assertEqual(self.send(email="not-an-email", visitor_ip="203.0.113.62").status_code, 422)
        self.assertEqual(self.send(email="a@b.test\nbcc: c@d.test", visitor_ip="203.0.113.62").status_code, 422)
        self.assertTrue(body["visitor_secret"])

    def test_the_honeypot_is_answered_as_sent_and_nothing_is_stored(self):
        response = self.send(website="http://spam.test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["visitor_secret"], "")
        self.assertFalse(InquiryMessage.objects.exists())
        self.assertFalse(InquiryConversation.objects.exists())

    def test_a_first_send_less_than_two_seconds_after_opening_is_refused(self):
        for opened in (self.opened(0), self.opened(1), (timezone.now() + timedelta(minutes=5)).isoformat()):
            self.assertEqual(self.send(opened_at=opened).status_code, 422)
        self.assertEqual(self.post(SEND, visitor_ip=IP, text="hi").status_code, 422)  # no opened_at
        self.assertFalse(InquiryMessage.objects.exists())
        self.assertEqual(self.send(opened_at=self.opened(3)).status_code, 200)

    def test_the_same_text_twice_in_five_minutes_is_dropped(self):
        secret = self.first(text="Same text")["visitor_secret"]
        self.send(visitor_secret=secret, text="  same   TEXT ")
        self.assertEqual(InquiryMessage.objects.filter(site=self.site).count(), 1)
        self.send(visitor_secret=secret, text="Different")
        self.assertEqual(InquiryMessage.objects.filter(site=self.site).count(), 2)

    def test_no_secret_dropped_duplicate_does_not_leak_or_store(self):
        self.first(text="Same text")
        response = self.send(text="Same text")
        self.assertEqual(response.json()["visitor_secret"], "")
        self.assertEqual(InquiryMessage.objects.count(), 1)


class ContactTests(ChatTestCase):
    def conversation(self, site=None):
        return InquiryConversation.objects.get(site=site or self.site)

    def test_a_first_message_needs_no_name_and_no_email(self):
        body = self.first()
        conversation = self.conversation()
        self.assertEqual(body["state"], "open")
        self.assertEqual((conversation.contact_name, conversation.contact_email, conversation.contact_mobile), ("", "", ""))

    def test_an_email_is_saved_lower_cased(self):
        secret = self.first()["visitor_secret"]
        response = self.contact(secret, "  Maria@Visitor.TEST ")
        self.assertEqual((response.status_code, response.json()), (200, {"saved": True, "kind": "email"}))
        conversation = self.conversation()
        self.assertEqual((conversation.contact_email, conversation.contact_mobile), ("maria@visitor.test", ""))

    def test_a_ph_mobile_is_saved_in_the_plus_63_form(self):
        secret = self.first()["visitor_secret"]
        for raw in ("0917 123 4567", "9171234567", "+63 917-123-4567", "639171234567"):
            InquiryConversation.objects.filter(site=self.site).update(contact_mobile="")
            response = self.contact(secret, raw)
            self.assertEqual((response.status_code, response.json()), (200, {"saved": True, "kind": "mobile"}), raw)
            self.assertEqual(self.conversation().contact_mobile, "+639171234567", raw)
        self.assertEqual(self.conversation().contact_email, "")

    def test_anything_else_is_a_422_on_contact_and_changes_nothing(self):
        secret = self.first()["visitor_secret"]
        for raw in ("", "   ", "hello", "not@", "a@b", "12345", "+14155550123", "0817 123 4567", "x" * 300):
            response = self.contact(secret, raw)
            self.assertEqual(response.status_code, 422, raw)
            self.assertEqual(list(response.json()["error"]["fields"]), ["contact"], raw)
        conversation = self.conversation()
        self.assertEqual((conversation.contact_email, conversation.contact_mobile), ("", ""))

    def test_a_filled_field_is_never_overwritten_but_the_other_one_is_filled(self):
        secret = self.first(email="first@visitor.test")["visitor_secret"]
        self.assertEqual(self.contact(secret, "second@visitor.test").json(), {"saved": True, "kind": "email"})
        self.assertEqual(self.conversation().contact_email, "first@visitor.test")
        self.assertEqual(self.contact(secret, "09171234567").json(), {"saved": True, "kind": "mobile"})
        self.assertEqual(self.contact(secret, "09181234567").json(), {"saved": True, "kind": "mobile"})
        conversation = self.conversation()
        self.assertEqual((conversation.contact_email, conversation.contact_mobile), ("first@visitor.test", "+639171234567"))

    def test_a_secret_from_another_store_or_no_secret_gets_the_same_answer_and_stores_nothing(self):
        secret = self.first()["visitor_secret"]
        mine = self.contact(secret, "09171234567")
        for response in (
            self.contact(secret, "09171234567", host=OTHER_HOST),
            self.contact("", "09171234567"),
            self.contact("A" * 43, "09171234567"),
            self.contact("not a secret", "09171234567"),
        ):
            self.assertEqual((response.status_code, response.json()), (200, mine.json()))
        self.assertFalse(InquiryConversation.objects.filter(site=self.ben).exists())
        InquiryConversation.objects.filter(site=self.site).update(contact_mobile="")
        self.contact(secret, "09171234567", host=OTHER_HOST)
        self.assertEqual(self.conversation().contact_mobile, "")

    def test_another_visitors_conversation_is_not_touched(self):
        mine = self.first(visitor_ip="203.0.113.81")["visitor_secret"]
        other = self.first(text="Other question", visitor_ip="203.0.113.82")["visitor_secret"]
        self.contact(other, "09181234567", visitor_ip="203.0.113.82")
        self.contact(mine, "09171234567", visitor_ip="203.0.113.81")
        mobiles = {c.visitor_hash: c.contact_mobile for c in InquiryConversation.objects.filter(site=self.site)}
        self.assertEqual(mobiles[webchat.hash_of(mine)], "+639171234567")
        self.assertEqual(mobiles[webchat.hash_of(other)], "+639181234567")

    def test_it_is_throttled_per_address_and_per_secret(self):
        secret = self.first()["visitor_secret"]
        for _ in range(9):  # the secret has used 1 of its 10 in 10 minutes
            self.assertEqual(self.contact(secret, "09171234567").status_code, 200)
        self.assertEqual(self.contact(secret, "09171234567").status_code, 429)
        with mock.patch.object(webchat, "IP_MESSAGES", ("chat-ip-messages", 1, 3600)):
            self.assertEqual(self.contact("", "09171234567", visitor_ip="203.0.113.91").status_code, 200)
            self.assertEqual(self.contact("", "09171234567", visitor_ip="203.0.113.91").status_code, 429)

    def test_paused_chat_saves_nothing(self):
        secret = self.first()["visitor_secret"]
        InquiryChannelSettings.objects.filter(site=self.site).update(webchat_enabled=False)
        response = self.contact(secret, "09171234567")
        self.assertEqual((response.status_code, response.json()), (200, {"saved": False, "kind": ""}))
        self.assertEqual(self.conversation().contact_mobile, "")

    def test_the_site_comes_only_from_the_host_and_extra_fields_are_refused(self):
        secret = self.first()["visitor_secret"]
        self.assertEqual(self.contact(secret, "09171234567", site_id="s_x").status_code, 400)
        self.assertEqual(self.contact(secret, "09171234567", host="nobody.negosyotest.com").status_code, 404)
        self.assertEqual(self.post(CONTACT, secret=None, host=HOST, contact="x").status_code, 401)

    def test_the_contact_and_secret_are_never_logged(self):
        secret = self.first()["visitor_secret"]
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("apps").debug("marker")
            self.contact(secret, "09171234567")
            self.contact(secret, "private@visitor.test")
            self.contact(secret, "nonsense")
        text = "\n".join(logs.output)
        for needle in (secret, "09171234567", "639171234567", "private@visitor.test", "nonsense", IP):
            self.assertNotIn(needle, text)


class IsolationTests(ChatTestCase):
    def test_a_secret_from_one_host_continues_nothing_on_another(self):
        secret = self.first()["visitor_secret"]
        before = list(InquiryMessage.objects.filter(site=self.site).values_list("pk", flat=True))
        body = self.send(host=OTHER_HOST, visitor_secret=secret, text="From the other store").json()
        self.assertNotEqual(body["visitor_secret"], secret)
        self.assertEqual(len(body["visitor_secret"]), 43)
        self.assertEqual(InquiryConversation.objects.filter(site=self.ben).count(), 1)
        self.assertEqual(list(InquiryMessage.objects.filter(site=self.site).values_list("pk", flat=True)), before)
        self.assertEqual(self.poll(secret, host=OTHER_HOST).json()["state"], "none")

    def test_poll_never_returns_another_visitors_or_stores_messages(self):
        mine = self.first()["visitor_secret"]
        theirs = self.first(visitor_ip="203.0.113.70")["visitor_secret"]
        a, b = InquiryConversation.objects.filter(site=self.site).order_by("pk")
        self.owner_reply(a, "FOR VISITOR ONE")
        self.owner_reply(b, "FOR VISITOR TWO")
        self.assertEqual([m["text"] for m in self.poll(mine).json()["messages"]], ["FOR VISITOR ONE"])
        self.assertEqual([m["text"] for m in self.poll(theirs).json()["messages"]], ["FOR VISITOR TWO"])
        self.assertEqual(self.poll(mine, host=OTHER_HOST).json()["messages"], [])

    def test_a_wrong_secret_gets_the_empty_result_of_a_visitor_who_never_wrote(self):
        secret = self.first()["visitor_secret"]
        self.owner_reply(InquiryConversation.objects.get(site=self.site))
        wrong = self.poll("y" * 43).json()
        nobody = self.poll("").json()
        self.assertEqual(wrong, nobody)
        self.assertEqual(wrong["state"], "none")
        self.assertEqual(wrong["messages"], [])
        self.assertEqual(len(self.poll(secret).json()["messages"]), 1)

    def test_after_id_returns_only_newer_replies(self):
        secret = self.first()["visitor_secret"]
        conversation = InquiryConversation.objects.get(site=self.site)
        first = self.owner_reply(conversation, "one")
        self.owner_reply(conversation, "two")
        self.assertEqual([m["text"] for m in self.poll(secret, after_id=first.pk).json()["messages"]], ["two"])

    def test_unserved_held_and_non_sante_hosts_are_the_same_404_and_store_nothing(self):
        Site.objects.filter(pk=self.ben.pk).update(template="other")
        answers = set()
        for host in ("nobody-here.negosyotest.com", OTHER_HOST, "www.negosyotest.com", "", None):
            for path in (SEND, POLL):
                response = self.post(path, host=host, visitor_ip=IP, text="hi", opened_at=self.opened()) \
                    if path == SEND else self.post(path, host=host, visitor_ip=IP)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), NOT_FOUND)
                answers.add(response.content)
        self.assertEqual(len(answers), 1)
        self.assertFalse(InquiryMessage.objects.exists())

    def test_a_missing_credential_is_401(self):
        self.assertEqual(self.send_with(None).status_code, 401)
        self.assertEqual(self.send_with("wrong").status_code, 401)

    def send_with(self, secret):
        return self.post(SEND, secret=secret, visitor_ip=IP, text="hi", opened_at=self.opened())


class PauseTests(ChatTestCase):
    NOTICE = webchat.PAUSED_NOTICE

    def test_a_site_with_chat_off_answers_the_fixed_text_and_stores_nothing(self):
        InquiryChannelSettings.objects.filter(site=self.site).update(webchat_enabled=False)
        body = self.send().json()
        self.assertEqual((body["state"], body["notice"], body["visitor_secret"]), ("paused", self.NOTICE, ""))
        self.assertFalse(InquiryMessage.objects.exists())

    def test_a_site_with_no_settings_row_is_paused(self):
        InquiryChannelSettings.objects.filter(site=self.site).delete()
        self.assertEqual(self.send().json()["state"], "paused")
        self.assertEqual(self.poll("").json()["state"], "paused")

    def test_paused_until_pauses_and_then_ends(self):
        InquiryChannelSettings.objects.filter(site=self.site).update(paused_until=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.send().json()["state"], "paused")
        self.assertFalse(InquiryMessage.objects.exists())
        InquiryChannelSettings.objects.filter(site=self.site).update(paused_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.send().json()["state"], "open")

    def test_the_pause_still_lets_a_visitor_read_replies(self):
        secret = self.first()["visitor_secret"]
        self.owner_reply(InquiryConversation.objects.get(site=self.site))
        InquiryChannelSettings.objects.filter(site=self.site).update(webchat_enabled=False)
        body = self.poll(secret).json()
        self.assertEqual(body["state"], "paused")
        self.assertEqual(len(body["messages"]), 1)


class LimitTests(ChatTestCase):
    def test_five_new_conversations_an_hour_from_one_address(self):
        for n in range(5):
            self.assertEqual(self.send(text=f"m{n}").status_code, 200)
        self.assertEqual(self.send(text="m6").status_code, 429)
        self.assertEqual(InquiryConversation.objects.filter(site=self.site).count(), 5)

    def test_the_address_count_is_shared_by_every_store(self):
        for n in range(5):
            self.send(text=f"m{n}", host=HOST if n % 2 else OTHER_HOST)
        self.assertEqual(self.send(text="again").status_code, 429)

    def test_ten_messages_in_ten_minutes_per_secret(self):
        secret = self.first()["visitor_secret"]
        for n in range(9):
            self.assertEqual(self.send(visitor_secret=secret, text=f"m{n}").status_code, 200)
        self.assertEqual(self.send(visitor_secret=secret, text="one too many").status_code, 429)

    def test_the_secret_throttle_key_is_per_site(self):
        secret = self.first()["visitor_secret"]
        with mock.patch("apps.inquiries.webchat.throttle.hit") as hit:
            self.send(visitor_secret=secret, text="x")
        scopes = {c.args[0]: c.args[1] for c in hit.call_args_list}
        self.assertEqual(scopes["chat-site-messages"], self.site.pk)
        self.assertTrue(str(scopes["chat-secret-messages"]).startswith(f"{self.site.pk}:"))

    def test_the_site_daily_cap_pauses_the_visitor_and_sets_the_owner_flag(self):
        with mock.patch.object(webchat, "SITE_MESSAGES", ("chat-site-messages", 2, 86400)):
            self.send(text="a", visitor_ip="203.0.113.71")
            self.send(text="b", visitor_ip="203.0.113.72")
            body = self.send(text="c", visitor_ip="203.0.113.73").json()
        self.assertEqual((body["state"], body["notice"]), ("paused", webchat.PAUSED_NOTICE))
        self.assertEqual(InquiryMessage.objects.filter(site=self.site).count(), 2)
        self.assertIsNotNone(InquiryChannelSettings.objects.get(site=self.site).chat_limit_at)
        self.assertIsNone(InquiryChannelSettings.objects.get(site=self.ben).chat_limit_at)

    def test_one_sites_flood_does_not_use_up_another_sites_budget(self):
        with mock.patch.object(webchat, "SITE_MESSAGES", ("chat-site-messages", 1, 86400)):
            self.send(text="a", visitor_ip="203.0.113.71")
            self.assertEqual(self.send(text="b", visitor_ip="203.0.113.72").json()["state"], "paused")
            self.assertEqual(self.send(text="c", host=OTHER_HOST, visitor_ip="203.0.113.73").json()["state"], "open")

    def test_polls_are_limited_per_address(self):
        with mock.patch.object(webchat, "IP_POLLS", ("chat-ip-polls", 2, 3600)):
            self.assertEqual(self.poll("").status_code, 200)
            self.assertEqual(self.poll("").status_code, 200)
            self.assertEqual(self.poll("").status_code, 429)


class SwitchTests(ChatTestCase):
    def walk(self):
        return [
            self.send(), self.poll(""), self.contact("", "09171234567"),
            self.post(SEND, host="nobody.negosyotest.com", text="x"),
        ]

    def test_switch_off_both_routes_are_404_and_read_no_table(self):
        with override_settings(INQUIRY_WEBCHAT_ENABLED=False), CaptureQueriesContext(connection) as queries:
            for response in self.walk():
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), NOT_FOUND)
        self.assertFalse([q for q in queries.captured_queries if "inquir" in q["sql"]])
        self.assertFalse(InquiryMessage.objects.exists())

    def test_the_body_is_not_read_when_the_switch_is_off(self):
        with override_settings(INQUIRY_WEBCHAT_ENABLED=False):
            response = self.send(site_id="s_x")
        self.assertEqual(response.status_code, 404)

    def test_chat_needs_the_inquiries_switch_too(self):
        with override_settings(INQUIRIES_ENABLED=False):
            for response in self.walk():
                self.assertEqual(response.status_code, 404)

    def test_with_the_tables_absent_both_routes_are_404(self):
        with mock.patch("apps.inquiries.api_storefront.stored_tables_exist", return_value=False):
            for response in self.walk():
                self.assertEqual(response.status_code, 404)
