"""LIVE-INBOX publish side: what is published, when (after commit only), and that a failure is
swallowed. Redis itself is not needed: the send step is replaced."""
import json
import logging
from unittest import mock

from django.test import override_settings

from apps.inquiries import live, stored
from apps.inquiries.models import InquiryConversation
from apps.inquiries.tests.base import REPLY_TEXT, WC_REF, switched_on
from apps.inquiries.tests.test_stored import StoredOwnerTestCase
from apps.inquiries.tests.test_webchat import ChatTestCase


@switched_on
class PublishTests(StoredOwnerTestCase):

    def test_nothing_is_sent_until_the_transaction_commits(self):
        with mock.patch.object(live, "_send") as send:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                live.publish(self.site, WC_REF, live.KIND_MESSAGE, None)
            send.assert_not_called()
            callbacks[0]()
            send.assert_called_once()

    def test_the_payload_carries_ref_time_and_kind_only(self):
        conversation = self.conv
        with mock.patch.object(live, "_send") as send, self.captureOnCommitCallbacks(execute=True):
            live.publish(self.site, WC_REF, live.KIND_STATUS, conversation.last_message_at)
        site_id, payload = send.call_args.args
        self.assertEqual(site_id, self.site.public_id)
        self.assertEqual(set(payload), {"type", "ref", "last_message_at", "kind"})
        self.assertRegex(payload["last_message_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual((payload["type"], payload["kind"]), ("conversation_updated", "status"))

    def test_a_message_status_carries_the_ref_only(self):
        with mock.patch.object(live, "_send") as send, self.captureOnCommitCallbacks(execute=True):
            live.publish(self.site, WC_REF, None, event=live.MESSAGE_STATUS)
        self.assertEqual(send.call_args.args[1], {"type": "message_status", "ref": WC_REF})

    @override_settings(REDIS_URL="")
    def test_no_redis_url_is_silent(self):
        with self.assertNoLogs("apps.inquiries.live", logging.WARNING):
            live._send(self.site.public_id, {"type": "x"})

    @override_settings(REDIS_URL="redis://127.0.0.1:1/0")
    def test_a_redis_failure_is_swallowed_with_one_fixed_warning(self):
        with self.assertLogs("apps.inquiries.live", logging.WARNING) as logs:
            live._send(self.site.public_id, {"type": "x"})
        self.assertEqual(len(logs.records), 1)
        self.assertRegex(logs.output[0], r"Inbox live publish failed \(\w+\)$")
        self.assertNotIn("127.0.0.1", logs.output[0])

    def test_published_to_the_sites_channel_as_json(self):
        fake = mock.Mock()
        with override_settings(REDIS_URL="redis://example.invalid/0"), mock.patch.object(live, "_client", return_value=fake):
            live._send("site_x", {"type": "message_status", "ref": "wc_1"})
        channel, body = fake.publish.call_args.args
        self.assertEqual(channel, "nego:inbox:site:site_x")
        self.assertEqual(json.loads(body), {"type": "message_status", "ref": "wc_1"})


@switched_on
class PublishPointTests(StoredOwnerTestCase):
    """Each stored change publishes once, after commit."""

    def published(self):
        return [call.args[1] for call in self.send_mock.call_args_list]

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(live, "_send")
        self.send_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_reply_close_reopen_read(self):
        path = self.base + f"conversations/{WC_REF}/"
        with self.captureOnCommitCallbacks(execute=True):
            self.send("post", path + "read/")
            self.send("post", path + "close/")
            self.send("post", path + "reopen/")
            self.reply()
        kinds = [(p["type"], p.get("kind")) for p in self.published()]
        self.assertIn(("conversation_updated", "status"), kinds)
        self.assertEqual(kinds.count(("conversation_updated", "status")), 3)
        self.assertIn(("conversation_updated", "message"), kinds)
        self.assertNotIn(REPLY_TEXT, json.dumps(self.published()))

    def test_read_when_already_read_publishes_nothing(self):
        InquiryConversation.objects.filter(external_id=WC_REF).update(unread=False)
        with self.captureOnCommitCallbacks(execute=True):
            self.send("post", self.base + f"conversations/{WC_REF}/read/")
        self.send_mock.assert_not_called()

    def test_a_contact_message_publishes_a_message_event(self):
        with self.captureOnCommitCallbacks(execute=True):
            conversation = stored.add_contact_message(
                self.site, name="Ana", email="a@example.test", subject="Hi", message="Is it in stock?",
            )
        payload = self.published()[0]
        self.assertEqual((payload["ref"], payload["kind"]), (conversation.external_id, "message"))
        self.assertNotIn("Ana", json.dumps(payload))


class ChatSendPublishTests(ChatTestCase):
    def test_a_visitor_message_publishes_once_with_no_text(self):
        with mock.patch.object(live, "_send") as send, self.captureOnCommitCallbacks(execute=True):
            body = self.first(text="Private words about delivery", name="Maria")
        self.assertEqual(body["state"], "open")
        send.assert_called_once()
        payload = send.call_args.args[1]
        self.assertEqual((payload["type"], payload["kind"]), ("conversation_updated", "message"))
        self.assertNotIn("Private words", json.dumps(payload))
        self.assertNotIn("Maria", json.dumps(payload))
