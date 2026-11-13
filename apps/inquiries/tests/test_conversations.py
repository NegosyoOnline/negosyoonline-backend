"""The conversation list, the thread and the reply: isolation between sites, the provider's reply
window, idempotency, rate control, and a token and reply text that leave no trace."""
import logging
from unittest import mock

from django.core.cache import cache

from apps.inquiries.models import InquiryConnection, InquiryConversation, InquiryReply
from apps.inquiries.tests.base import (
    BAD_TOKEN,
    LIST_OK,
    OTHER_REF,
    PAGE,
    REF,
    REPLY_TEXT,
    SECRET_TEXT,
    THREAD_CLOSED,
    THREAD_OPEN,
    TOKEN,
    FakePancake,
    configure,
)
from apps.inquiries.tests.test_owner import InquiryOwnerTestCase
from apps.sites.models import Site
from apps.sites.tests.base import make_site


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class ConversationTestCase(InquiryOwnerTestCase):
    def setUp(self):
        super().setUp()
        self.connection = configure(self.site)
        self.thread_path = f"{self.base}conversations/{REF}/"
        self.reply_path = self.thread_path + "reply/"
        self.n = 0

    def reply(self, text=REPLY_TEXT, *, ref=REF, key=None, client=None):
        self.n += 1
        return self.send(
            "post", f"{self.base}conversations/{ref}/reply/",
            {"text": text, "idempotency_key": key or f"key-{self.n:08d}"}, client=client,
        )

    def all_stored_text(self):
        values = []
        for model in (InquiryConnection, InquiryConversation, InquiryReply):
            for row in model.objects.values():
                values.extend(str(v) for v in row.values())
        return "\n".join(values)


class ListTests(ConversationTestCase):
    def test_the_list_comes_from_the_provider_and_is_cached_briefly(self):
        with FakePancake() as pancake:
            first = self.get(self.base + "conversations/")
            second = self.get(self.base + "conversations/")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(pancake.kinds(), ["list"])  # the second read came from the cache
        item = first.json()["items"][0]
        self.assertEqual(
            (item["ref"], item["display_name"], item["channel"]), (REF, "Maria Santos", "facebook"),
        )
        self.assertEqual(InquiryConversation.objects.count(), 0)  # a list makes no rows
        self.assertNotIn(TOKEN, first.content.decode())

    def test_a_new_page_does_not_reuse_the_old_pages_list(self):
        with FakePancake() as pancake:
            self.get(self.base + "conversations/")
            self.send("put", self.path, {"page_id": "2000000000002"})
            self.get(self.base + "conversations/")
        self.assertEqual(pancake.kinds(), ["list", "list"])

    def test_not_connected_is_409_without_a_provider_call(self):
        InquiryConnection.objects.update(token_encrypted="")
        with FakePancake() as pancake:
            for path in ("conversations/", f"conversations/{REF}/"):
                self.error(self.get(self.base + path), 409, "not_connected")
        self.assertEqual(pancake.requests, [])

    def test_provider_failures_are_fixed_messages(self):
        cases = [
            (BAD_TOKEN, 409, "reconnect"), ((403, {}), 409, "forbidden_page"), ((429, {}), 429, "rate_limited"),
            ((503, {}), 502, "provider_down"), (b"<html>", 502, "provider_error"),
        ]
        for answer, status, code in cases:
            with self.subTest(code=code):
                cache.clear()
                InquiryConnection.objects.update(status="active")
                with FakePancake(listing=answer):
                    response = self.get(self.base + "conversations/")
                self.error(response, status, code)
                for leak in (SECRET_TEXT, TOKEN):
                    self.assertNotIn(leak, response.content.decode())

    def test_a_cursor_must_be_digits(self):
        self.error(self.get(self.base + "conversations/?cursor=abc"), 422, "invalid")

    def test_reads_are_limited_per_account(self):
        with FakePancake():
            for _ in range(600):
                self.assertEqual(self.get(self.base + "conversations/").status_code, 200)
            self.error(self.get(self.base + "conversations/"), 429, "rate_limited")


class ThreadTests(ConversationTestCase):
    def test_the_thread_carries_the_window_and_makes_a_thin_handle(self):
        with FakePancake() as pancake:
            response = self.get(self.thread_path)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(pancake.kinds(), ["thread"])
        self.assertEqual(body["reply"], {"open": True, "reason": "provider_open"})
        self.assertEqual([m["direction"] for m in body["messages"]], ["in", "out"])
        self.assertEqual(body["messages"][0]["attachments"], ["photo"])
        self.assertIsNone(body["provider_url"])
        self.assertEqual(body["replies"], [])
        handle = InquiryConversation.objects.get()
        self.assertEqual((handle.site, handle.external_id, handle.reply_open, handle.display_name), (self.site, REF, True, "Maria Santos"))
        self.assertFalse(handle.stores_history)
        self.assertNotIn("Do you have this", self.all_stored_text())  # no inbound text is kept

    def test_a_closed_window_is_shown(self):
        with FakePancake(thread=THREAD_CLOSED):
            body = self.get(self.thread_path).json()
        self.assertEqual(body["reply"], {"open": False, "reason": "provider_closed"})

    def test_a_ref_of_another_page_or_a_malformed_one_is_the_unknown_404_without_a_call(self):
        with FakePancake() as pancake:
            for ref in ("999_7000000000001", "ab", "x" * 129, f"{PAGE}_a%2Fb", f"{PAGE}_a b"):
                response = self.get(f"{self.base}conversations/{ref}/")
                self.error(response, 404, "not_found")
        self.assertEqual(pancake.requests, [])
        self.assertEqual(InquiryConversation.objects.count(), 0)

    def test_the_provider_not_finding_it_is_the_same_404(self):
        with FakePancake(thread=(404, {})):
            response = self.get(self.thread_path)
        body = self.error(response, 404, "not_found")
        self.assertEqual(body["message"], "Not found.")

    def test_two_sites_with_the_same_page_and_ref_never_share_a_cached_thread(self):
        other = make_site(self.account, name="second-store", first=False)
        configure(other, token=TOKEN.replace("abcdefghij", "klmnopqrst"))
        other_base = f"/v1/sites/{other.public_id}/inquiries/"
        with FakePancake(thread=[THREAD_OPEN, THREAD_CLOSED]) as pancake:
            mine = self.get(self.thread_path).json()
            theirs = self.get(f"{other_base}conversations/{REF}/").json()
        self.assertEqual(pancake.kinds(), ["thread", "thread"])
        self.assertTrue(mine["reply"]["open"])
        self.assertFalse(theirs["reply"]["open"])
        self.assertEqual(InquiryConversation.objects.count(), 2)

    def test_a_conversation_of_site_a_cannot_be_read_or_answered_from_site_b(self):
        other = make_site(self.account, name="second-store", first=False)
        configure(other, page="2000000000002", token=TOKEN.replace("abcdefghij", "klmnopqrst"))
        other_base = f"/v1/sites/{other.public_id}/inquiries/"
        with FakePancake() as pancake:
            read = self.get(f"{other_base}conversations/{REF}/")
            sent = self.send("post", f"{other_base}conversations/{REF}/reply/", {"text": "hi", "idempotency_key": "key-99999999"})
        self.error(read, 404, "not_found")
        self.error(sent, 404, "not_found")
        self.assertEqual(read.json(), self.get("/v1/sites/s_doesnotexist/inquiries/conversations/x/").json())
        self.assertEqual(pancake.requests, [])
        self.assertEqual(InquiryReply.objects.count(), 0)

    def test_another_accounts_thread_is_the_site_404(self):
        ben = self.other_client()
        with FakePancake() as pancake:
            self.error(self.get(self.thread_path, client=ben), 404, "not_found")
            self.error(self.reply(client=ben), 404, "not_found")
        self.assertEqual(pancake.requests, [])


class ReplyTests(ConversationTestCase):
    def test_a_reply_checks_the_window_then_sends_and_audits_without_the_text(self):
        with FakePancake() as pancake:
            response = self.reply()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["state"], "sent")
        self.assertEqual(response.json()["error_code"], "")
        self.assertEqual(pancake.kinds(), ["thread", "send"])  # a fresh read of the window first
        self.assertEqual(pancake.sent_bodies(), [{"action": "reply_inbox", "message": REPLY_TEXT}])
        row = InquiryReply.objects.get()
        self.assertEqual((row.state, row.body_length, row.provider_message_ref, row.author), ("sent", len(REPLY_TEXT), "m_abc123", self.account))
        self.assertNotIn(REPLY_TEXT[:20], self.all_stored_text())
        self.assertNotIn(REPLY_TEXT[:20], response.content.decode())

    def test_the_thread_lists_the_replies_sent_for_that_conversation(self):
        with FakePancake():
            self.reply()
            self.reply(ref=OTHER_REF)
            body = self.get(self.thread_path).json()
        self.assertEqual([(r["state"], r["error_code"]) for r in body["replies"]], [("sent", "")])
        self.assertEqual(set(body["replies"][0]), {"state", "error_code", "created_at"})

    def test_a_closed_window_sends_nothing_and_is_audited(self):
        with FakePancake(thread=THREAD_CLOSED) as pancake:
            response = self.reply()
        self.assertEqual((response.json()["state"], response.json()["error_code"]), ("blocked_window", "window_closed"))
        self.assertEqual(pancake.kinds(), ["thread"])
        self.assertEqual(InquiryReply.objects.get().state, "blocked_window")

    def test_a_missing_window_flag_is_treated_as_closed(self):
        payload = {k: v for k, v in THREAD_OPEN.items() if k != "can_inbox"}
        with FakePancake(thread=payload) as pancake:
            response = self.reply()
        self.assertEqual(response.json()["state"], "blocked_window")
        self.assertNotIn("send", pancake.kinds())

    def test_the_same_key_never_sends_twice(self):
        with FakePancake() as pancake:
            first = self.reply(key="key-same-0001")
            second = self.reply(key="key-same-0001")
        self.assertEqual(first.json(), second.json())
        self.assertEqual(pancake.kinds(), ["thread", "send"])
        self.assertEqual(InquiryReply.objects.count(), 1)

    def test_a_key_used_for_another_conversation_is_a_conflict(self):
        with FakePancake():
            self.reply(key="key-same-0001")
            self.error(self.reply(ref=OTHER_REF, key="key-same-0001"), 409, "conflict")
        self.assertEqual(InquiryReply.objects.count(), 1)

    def test_a_key_is_per_site(self):
        other = make_site(self.account, name="second-store", first=False)
        configure(other)
        with FakePancake() as pancake:
            self.reply(key="key-same-0001")
            second = self.send(
                "post", f"/v1/sites/{other.public_id}/inquiries/conversations/{REF}/reply/",
                {"text": "hello", "idempotency_key": "key-same-0001"},
            )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(pancake.kinds().count("send"), 2)

    def test_send_failures_are_audited_with_a_fixed_code(self):
        cases = [((403, {}), "forbidden_page"), ((503, {}), "provider_down"), (b"oops", "bad_response"), ((404, {}), "not_found")]
        for answer, code in cases:
            with self.subTest(code=code), FakePancake(send=answer):
                response = self.reply()
            self.assertEqual((response.json()["state"], response.json()["error_code"]), ("failed", code))
            for leak in (SECRET_TEXT, TOKEN, REPLY_TEXT):
                self.assertNotIn(leak, response.content.decode())
        self.assertEqual(InquiryReply.objects.filter(state="failed").count(), 4)

    def test_a_refused_token_marks_the_connection_invalid(self):
        with FakePancake(send=BAD_TOKEN):
            response = self.reply()
        self.assertEqual((response.json()["state"], response.json()["error_code"]), ("failed", "invalid_credentials"))
        self.assertEqual(InquiryConnection.objects.get().status, "invalid")
        with FakePancake() as pancake:
            again = self.reply()
        self.assertEqual(again.json()["error_code"], "invalid_credentials")
        self.assertEqual(pancake.requests, [])

    def test_a_provider_429_is_retried_once(self):
        with FakePancake(send=[(429, {}), {"success": True, "message_id": "m_9"}]) as pancake:
            response = self.reply()
        self.assertEqual(response.json()["state"], "sent")
        self.assertEqual(pancake.kinds(), ["thread", "send", "send"])

    def test_two_429s_fail_with_rate_limited(self):
        with FakePancake(send=(429, {})) as pancake:
            response = self.reply()
        self.assertEqual((response.json()["state"], response.json()["error_code"]), ("failed", "rate_limited"))
        self.assertEqual(pancake.kinds().count("send"), 2)

    def test_no_permit_from_the_limiter_blocks_without_calling_the_provider(self):
        with mock.patch("apps.inquiries.limiter.acquire", return_value=False), FakePancake() as pancake:
            response = self.reply()
        self.assertEqual((response.json()["state"], response.json()["error_code"]), ("blocked_rate", "rate_limited"))
        self.assertEqual(pancake.requests, [])
        with mock.patch("apps.inquiries.limiter.acquire", return_value=False), FakePancake() as pancake:
            self.error(self.get(self.thread_path), 429, "rate_limited")

    def test_a_permit_is_taken_for_each_provider_call_of_this_connection(self):
        with mock.patch("apps.inquiries.limiter.acquire", return_value=True) as acquire, FakePancake():
            self.reply()
        self.assertEqual([c.args for c in acquire.call_args_list], [(self.connection.pk,)] * 2)

    def test_input_is_validated_and_refused_before_any_row_or_call(self):
        cases = [
            {"text": "   ", "idempotency_key": "key-12345678"}, {"text": "x" * 2001, "idempotency_key": "key-12345678"},
            {"text": "hi", "idempotency_key": "short"}, {"text": "hi", "idempotency_key": "bad key!bad key!"},
        ]
        with FakePancake() as pancake:
            for case in cases:
                with self.subTest(case=case["idempotency_key"] + str(len(case["text"]))):
                    self.error(self.send("post", self.reply_path, case), 422, "invalid")
            self.assertEqual(self.send("post", self.reply_path, {"text": "hi", "idempotency_key": "key-12345678", "x": 1}).status_code, 400)
        self.assertEqual(pancake.requests, [])
        self.assertEqual(InquiryReply.objects.count(), 0)

    def test_the_longest_reply_is_accepted(self):
        with FakePancake() as pancake:
            self.assertEqual(self.reply("x" * 2000).json()["state"], "sent")
        self.assertEqual(len(pancake.sent_bodies()[0]["message"]), 2000)

    def test_not_connected_is_409_and_leaves_no_row(self):
        InquiryConnection.objects.update(token_encrypted="")
        with FakePancake() as pancake:
            self.error(self.reply(), 409, "not_connected")
        self.assertEqual((pancake.requests, InquiryReply.objects.count()), ([], 0))

    def test_replies_are_limited_per_account(self):
        with FakePancake():
            for _ in range(120):
                self.assertEqual(self.reply().status_code, 200)
            self.error(self.reply(), 429, "rate_limited")

    def test_neither_the_token_nor_the_reply_text_reaches_a_log_or_the_database(self):
        capture = Capture()
        root = logging.getLogger()
        root.addHandler(capture)
        self.addCleanup(root.removeHandler, capture)
        old = root.level
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, old)
        failures = [BAD_TOKEN, (503, {}), b"junk", TimeoutError(f"boom https://pages.fm/x?page_access_token={TOKEN}")]
        for answer in failures:
            InquiryConnection.objects.update(status="active")
            with FakePancake(send=answer):
                self.reply()
            with FakePancake(listing=answer):
                cache.clear()
                self.get(self.base + "conversations/")
        logged = "\n".join(capture.lines)
        for secret in (TOKEN, REPLY_TEXT, SECRET_TEXT):
            self.assertNotIn(secret, logged)
            self.assertNotIn(secret, self.all_stored_text().replace(self.connection.token_encrypted, ""))
        self.assertNotIn(LIST_OK["conversations"][0]["snippet"], self.all_stored_text())


class DisconnectIsolationTests(ConversationTestCase):
    def test_the_other_sites_connection_is_untouched_by_a_disconnect(self):
        other = make_site(self.account, name="second-store", first=False)
        configure(other)
        self.send("delete", self.path, None)
        self.assertNotEqual(InquiryConnection.objects.get(site=other).token_encrypted, "")
        self.assertEqual(InquiryConnection.objects.get(site=self.site).token_encrypted, "")
        self.assertEqual(Site.objects.filter(account=self.account).count(), 2)
