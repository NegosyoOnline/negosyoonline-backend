"""The Pancake adapter against recorded payloads: the fixed outcome taxonomy, the token kept out of
every result and log, reply-window reading, and the page check on a conversation ref."""
import logging
import urllib.parse
from datetime import datetime, timezone
from unittest import mock

from django.test import SimpleTestCase

from apps.inquiries import outcomes
from apps.inquiries.adapters import get as get_adapter
from apps.inquiries.adapters.base import Credentials, redact
from apps.inquiries.adapters.pancake import PancakeAdapter, _stamp
from apps.inquiries.tests.base import (
    BAD_TOKEN,
    PAGE,
    REF,
    SECRET_TEXT,
    THREAD_CLOSED,
    THREAD_OPEN,
    TOKEN,
    UNKNOWN_FAILURE,
    FakePancake,
    http_error,
)

ADAPTER = PancakeAdapter()
CREDS = Credentials(PAGE, TOKEN)


class OutcomeTests(SimpleTestCase):
    def test_every_failure_is_one_fixed_code_never_pancakes_text(self):
        cases = [
            (BAD_TOKEN, outcomes.INVALID_CREDENTIALS),  # HTTP 200 with success false: the body decides
            (UNKNOWN_FAILURE, outcomes.BAD_RESPONSE),
            ((429, {}), outcomes.RATE_LIMITED),
            ((500, {}), outcomes.PROVIDER_DOWN),
            ((503, {}), outcomes.PROVIDER_DOWN),
            ((401, {}), outcomes.INVALID_CREDENTIALS),
            ((403, {}), outcomes.FORBIDDEN_PAGE),
            ((404, {}), outcomes.NOT_FOUND),
            ((400, {}), outcomes.BAD_RESPONSE),
            (b"<html>gateway</html>", outcomes.BAD_RESPONSE),
            ({"conversations": []}, outcomes.BAD_RESPONSE),  # no `success` at all
            ({"success": "yes"}, outcomes.BAD_RESPONSE),
            (b"[1, 2]", outcomes.BAD_RESPONSE),
            (TimeoutError("timed out"), outcomes.PROVIDER_DOWN),
            (OSError(f"failed https://pages.fm/x?page_access_token={TOKEN}"), outcomes.PROVIDER_DOWN),
        ]
        for answer, code in cases:
            with self.subTest(code=code, answer=repr(answer)[:40]), FakePancake(listing=answer):
                result = ADAPTER.test_connection(CREDS)
                self.assertEqual(result.code, code)
                self.assertNotIn(TOKEN, repr(result))
                self.assertNotIn(SECRET_TEXT, repr(result))

    def test_a_failing_transport_leaks_no_token_into_the_log(self):
        error = http_error(500)
        with FakePancake(listing=error), self.assertLogs(level="DEBUG") as logs:
            logging.getLogger("probe").debug("start")
            ADAPTER.test_connection(CREDS)
        self.assertNotIn(TOKEN, "\n".join(logs.output))

    def test_the_credentials_never_print_their_token(self):
        self.assertNotIn(TOKEN, repr(CREDS))
        self.assertNotIn(TOKEN, str(CREDS))

    def test_the_registry_fails_closed_on_an_unknown_provider(self):
        self.assertIsNone(get_adapter("whatsapp"))
        self.assertIsInstance(get_adapter("pancake"), PancakeAdapter)


class RequestTests(SimpleTestCase):
    def test_the_token_is_a_query_parameter_only_and_never_in_the_path(self):
        with FakePancake() as pancake:
            ADAPTER.test_connection(CREDS)
            ADAPTER.get_thread(CREDS, REF, None)
            ADAPTER.send_reply(CREDS, REF, "hi")
        for request in pancake.requests:
            parts = urllib.parse.urlsplit(request.full_url)
            self.assertEqual(parts.netloc, "pages.fm")
            self.assertNotIn(TOKEN, parts.path)
            self.assertEqual(urllib.parse.parse_qs(parts.query)["page_access_token"], [TOKEN])

    def test_the_conversation_list_carries_a_30_day_since_until_window_in_unix_seconds(self):
        now = datetime(2026, 11, 17, 8, 0, 0, tzinfo=timezone.utc)
        with mock.patch("apps.inquiries.adapters.pancake._now", return_value=now), FakePancake() as pancake:
            ADAPTER.test_connection(CREDS)
            ADAPTER.list_conversations(CREDS, "30", 30)
            ADAPTER.get_thread(CREDS, REF, None)
            ADAPTER.send_reply(CREDS, REF, "hi")
        listing = [r for r in pancake.requests if r.full_url.split("?")[0].endswith("/conversations")]
        self.assertEqual(len(listing), 2)
        for request in listing:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            for key in ("since", "until"):
                self.assertRegex(query[key][0], r"^[0-9]+$")
            self.assertEqual(int(query["until"][0]), int(now.timestamp()))
            self.assertEqual(int(query["until"][0]) - int(query["since"][0]), 30 * 86400)
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(listing[1].full_url).query)["page_number"], ["30"])
        others = [r for r in pancake.requests if r not in listing]
        self.assertEqual(len(others), 2)
        for request in others:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            self.assertNotIn("since", query)
            self.assertNotIn("until", query)

    def _list_query(self, cursor):
        with FakePancake() as pancake:
            ADAPTER.list_conversations(CREDS, cursor, 30)
        return urllib.parse.parse_qs(urllib.parse.urlsplit(pancake.requests[0].full_url).query)

    def test_the_list_pages_by_page_number_and_never_sends_current_count(self):
        query = self._list_query(None)
        self.assertEqual((query["page_number"], query["page_size"]), (["1"], ["30"]))
        self.assertIn("since", query)
        self.assertIn("until", query)
        self.assertNotIn("current_count", query)
        self.assertEqual(self._list_query("2")["page_number"], ["2"])
        self.assertEqual(self._list_query("9999")["page_number"], ["9999"])
        for bad in ("0", "", "abc", "10000", "-1", "1.5", "2&x=1"):
            with self.subTest(cursor=bad):
                self.assertEqual(self._list_query(bad)["page_number"], ["1"])

    def test_a_full_page_gives_the_next_page_number_and_a_short_one_gives_none(self):
        full = {"success": True, "conversations": [{"id": f"{PAGE}_{n}"} for n in range(30)]}
        with FakePancake(listing=full):
            self.assertEqual(ADAPTER.list_conversations(CREDS, "3", 30).value.next_cursor, "4")
            self.assertEqual(ADAPTER.list_conversations(CREDS, None, 30).value.next_cursor, "2")
        short = {"success": True, "conversations": [{"id": f"{PAGE}_{n}"} for n in range(29)]}
        with FakePancake(listing=short):
            self.assertIsNone(ADAPTER.list_conversations(CREDS, "3", 30).value.next_cursor)

    def test_test_connection_asks_for_one_row_of_page_one(self):
        with FakePancake() as pancake:
            ADAPTER.test_connection(CREDS)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(pancake.requests[0].full_url).query)
        self.assertEqual((query["page_number"], query["page_size"]), (["1"], ["1"]))
        self.assertIn("since", query)

    def test_a_page_number_is_required_answer_is_a_bad_response(self):
        with FakePancake(listing={"success": False, "message": "page_number is required!"}):
            self.assertEqual(ADAPTER.test_connection(CREDS).code, outcomes.BAD_RESPONSE)
            self.assertEqual(ADAPTER.list_conversations(CREDS, None, 30).code, outcomes.BAD_RESPONSE)

    def test_pancakes_naive_microsecond_timestamp_is_read_as_utc(self):
        self.assertEqual(_stamp("2026-11-17T01:23:45.123456"), "2026-11-17T01:23:45.123456+00:00")
        payload = {"success": True, "conversations": [{"id": REF, "updated_at": "2026-11-17T01:23:45.123456"}]}
        with FakePancake(listing=payload):
            item = ADAPTER.list_conversations(CREDS, None, 30).value.items[0]
        self.assertEqual(item.updated_at, "2026-11-17T01:23:45.123456+00:00")

    def test_the_thread_get_carries_no_list_paging_or_window(self):
        with FakePancake() as pancake:
            ADAPTER.get_thread(CREDS, REF, None)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(pancake.requests[0].full_url).query)
        for key in ("page_number", "page_size", "since", "until"):
            self.assertNotIn(key, query)

    def test_a_since_is_required_answer_is_still_a_bad_response(self):
        answer = {"success": False, "message": "since is required!"}
        with FakePancake(listing=answer):
            self.assertEqual(ADAPTER.test_connection(CREDS).code, outcomes.BAD_RESPONSE)
            self.assertEqual(ADAPTER.list_conversations(CREDS, None, 30).code, outcomes.BAD_RESPONSE)

    def test_a_ref_of_another_page_is_refused_without_a_request(self):
        with FakePancake() as pancake:
            for ref in ("999999_7000000000001", "../etc/passwd", f"{PAGE}_1/../../x", f"{PAGE}_1?x=1", "x"):
                self.assertEqual(ADAPTER.get_thread(CREDS, ref, None).code, outcomes.NOT_FOUND)
                self.assertEqual(ADAPTER.send_reply(CREDS, ref, "hi").code, outcomes.NOT_FOUND)
        self.assertEqual(pancake.requests, [])

    def test_a_reply_goes_out_as_the_page_with_the_text_only(self):
        with FakePancake() as pancake:
            result = ADAPTER.send_reply(CREDS, REF, "Yes, we do.")
        self.assertEqual((result.code, result.value), (outcomes.OK, "m_abc123"))
        request = pancake.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(pancake.sent_bodies(), [{"action": "reply_inbox", "message": "Yes, we do."}])

    def test_a_send_answer_without_a_usable_ref_is_still_sent(self):
        for answer in ({"success": True}, {"success": True, "message_id": "bad ref/with space"}):
            with FakePancake(send=answer):
                result = ADAPTER.send_reply(CREDS, REF, "hi")
            self.assertEqual((result.code, result.value), (outcomes.OK, ""))


class ReadTests(SimpleTestCase):
    def test_the_list_is_normalised(self):
        with FakePancake():
            result = ADAPTER.list_conversations(CREDS, None, 30)
        self.assertTrue(result.ok)
        first, second = result.value.items
        self.assertEqual((first.ref, first.display_name, first.channel), (REF, "Maria Santos", "facebook"))
        self.assertEqual(first.snippet, "Do you have this in medium?")
        self.assertEqual(first.updated_at, "2026-11-12T09:30:00+00:00")
        self.assertEqual(second.updated_at, "2026-11-11T18:00:00+00:00")

    def test_list_rows_without_a_safe_id_are_skipped(self):
        payload = {"success": True, "conversations": [{"id": "../x"}, {"id": 5}, "junk", {"id": REF}]}
        with FakePancake(listing=payload):
            result = ADAPTER.list_conversations(CREDS, None, 30)
        self.assertEqual([c.ref for c in result.value.items], [REF])

    def test_a_list_that_is_not_a_list_is_a_bad_response(self):
        with FakePancake(listing={"success": True, "conversations": "x"}):
            self.assertEqual(ADAPTER.list_conversations(CREDS, None, 30).code, outcomes.BAD_RESPONSE)

    def test_a_token_in_provider_text_is_redacted(self):
        payload = {"success": True, "conversations": [{
            "id": REF, "snippet": "see https://x.example/a?page_access_token=LEAKME&b=1",
            "customers": [{"name": "Eve access_token=LEAKME2"}],
        }]}
        with FakePancake(listing=payload):
            item = ADAPTER.list_conversations(CREDS, None, 30).value.items[0]
        self.assertNotIn("LEAKME", item.snippet + item.display_name)
        self.assertEqual(redact("a page_access_token=abc&x=1"), "a page_access_token=[redacted]&x=1")

    def test_the_thread_is_normalised_with_attachment_types_only(self):
        with FakePancake():
            thread = ADAPTER.get_thread(CREDS, REF, None).value
        self.assertEqual(thread.display_name, "Maria Santos")
        inbound, outbound = thread.messages
        self.assertEqual((inbound.direction, inbound.sender_kind, inbound.attachments), ("in", "customer", ("photo",)))
        self.assertEqual((outbound.direction, outbound.sender_kind), ("out", "page"))
        self.assertNotIn("fbcdn", repr(thread))  # a signed media address never leaves the adapter
        self.assertTrue(thread.window.open)

    def test_the_reply_window_is_the_providers_answer_and_fails_closed(self):
        with FakePancake(thread=THREAD_CLOSED):
            window = ADAPTER.get_thread(CREDS, REF, None).value.window
        self.assertEqual((window.open, window.reason), (False, "provider_closed"))
        without_flag = {key: value for key, value in THREAD_OPEN.items() if key != "can_inbox"}
        for payload in (without_flag, {**THREAD_OPEN, "can_inbox": "true"}):
            with FakePancake(thread=payload):
                window = ADAPTER.get_thread(CREDS, REF, None).value.window
            self.assertEqual((window.open, window.reason), (False, "unknown"))

    def test_a_cursor_that_is_not_a_number_is_ignored(self):
        with FakePancake() as pancake:
            ADAPTER.get_thread(CREDS, REF, "abc&page_access_token=x")
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(pancake.requests[0].full_url).query).get("current_count"), None)
