"""INBOX-UI backend (task z8uq9mg7c5): the customer's name and photo (U1), delivered and seen states
(U2), the reply window's end (U3), search and the unread count (U5). Recorded fake Meta payloads only."""
import json
import logging
import time
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.inquiries import live, messenger, messenger_inbox, profile, retention, stored, tasks
from apps.inquiries.models import InquiryConversation, InquiryMessage
from apps.inquiries.tests.base import make_stored
from apps.inquiries.tests.test_messenger import (
    PAGE_A, PAGE_B, PAGE_TOKEN, PSID, FakeMeta, MessengerTestCase, event, signed_request,
)
from apps.sites.models import Site

REAL_TRANSPORT = messenger._transport  # before any test replaces it
PHOTO = "https://platform-lookaside.fbsbx.com/platform/profilepic/?psid=SECRETPSID&width=1024&ext=1&hash=h"
SECRET_BITS = (PSID, "Maria", "Santos", "fbsbx.com", "SECRETPSID", PAGE_TOKEN)


class ProfileMeta(FakeMeta):
    """FakeMeta that also answers the customer's profile read."""

    def __init__(self):
        super().__init__()
        self.profile = (200, {"first_name": "Maria", "last_name": "Santos", "profile_pic": PHOTO})

    def __call__(self, method, path, *, params=None, form=None, token=None, timeout=None):
        if method == "GET" and not path.startswith(("/me", "/debug_token")) and path.count("/") == 1 \
                and "fields" in (params or {}) and "first_name" in params["fields"]:
            self.calls.append((method, path, params, form or {}, token, timeout))
            return self.profile
        return super().__call__(method, path, params=params, form=form, token=token)

    def profile_calls(self):
        return [c for c in self.calls if len(c) == 6]


class InboxUiCase(MessengerTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()  # a Redis cache outlives a test and primary keys repeat: no stale photo or retry flag
        self.addCleanup(cache.clear)
        # With REDIS_URL set (the CI PostgreSQL job) Celery is not eager and `.delay` would queue to a
        # broker: run the task in-process, as it does with no REDIS_URL.
        delay = mock.patch.object(
            tasks.fetch_messenger_profile, "delay", side_effect=lambda *a, **k: tasks.fetch_messenger_profile(*a, **k))
        delay.start()
        self.addCleanup(delay.stop)
        self.meta = ProfileMeta()
        patch = mock.patch.object(messenger, "_transport", self.meta)
        patch.start()
        self.addCleanup(patch.stop)
        self.row = self.connect_row()
        self.list_url = f"/v1/sites/{self.site_id}/inquiries/conversations/"

    def inbound_and_run(self, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            return self.inbound(**kw)

    def thread_url(self, conversation):
        return f"{self.list_url}{conversation.external_id}/"

    def other_site(self):
        return Site.objects.get(public_id=self.create_site(client=self.other_client(), subdomain="ben-store")["id"])


def receipt(kind, body, psid=PSID, page=PAGE_A, recipient=None):
    return {"object": "page", "entry": [{"id": page, "time": 1, "messaging": [
        {"sender": {"id": psid}, "recipient": {"id": recipient or page}, "timestamp": 1, kind: body}]}]}


def ms(at):
    return int(at.timestamp() * 1000)


class ProfileFetchTests(InboxUiCase):
    def test_a_new_customer_gets_the_name_and_a_cached_photo_after_commit(self):
        self.inbound(mid="m_nocommit")
        self.assertEqual(self.meta.profile_calls(), [])  # nothing runs before the transaction commits
        self.inbound_and_run(mid="m_2", psid="psid-second")
        conversation = InquiryConversation.objects.get(psid="psid-second")
        self.assertEqual(conversation.display_name, "Maria Santos")
        self.assertEqual(cache.get(f"inbox:avatar:{conversation.pk}"), PHOTO)
        (call,) = self.meta.profile_calls()
        self.assertEqual(call[:2], ("GET", "/psid-second"))
        self.assertEqual(call[2]["fields"], "first_name,last_name,profile_pic")
        self.assertEqual((call[4], call[5]), (PAGE_TOKEN, 3))  # the Page token, a 3 second timeout
        self.assertNotIn(PAGE_TOKEN, str(call[2]))

    def test_the_list_and_thread_carry_avatar_url_and_other_channels_carry_null(self):
        conversation = self.inbound_and_run()
        make_stored(self.site)
        items = {i["channel"]: i for i in self.get(self.list_url).json()["items"]}
        self.assertEqual(items["messenger"]["avatar_url"], PHOTO)
        self.assertEqual(items["messenger"]["display_name"], "Maria Santos")
        self.assertIsNone(items["webchat"]["avatar_url"])
        self.assertEqual(self.get(self.thread_url(conversation)).json()["avatar_url"], PHOTO)
        wc = self.get(f"{self.list_url}wc_{'a1b2c3d4e5f6' * 2}/").json()
        self.assertIsNone(wc["avatar_url"])

    def test_without_a_photo_it_is_null(self):
        conversation = self.inbound()
        self.assertIsNone(self.get(self.list_url).json()["items"][0]["avatar_url"])
        self.assertIsNone(self.get(self.thread_url(conversation)).json()["avatar_url"])

    def test_the_task_is_registered_and_takes_only_the_table_id(self):
        self.assertEqual(tasks.fetch_messenger_profile.name, "inquiries.fetch_messenger_profile")
        conversation = self.inbound()
        tasks.fetch_messenger_profile(conversation.pk)
        self.assertEqual(self.meta.profile_calls()[0][1], f"/{PSID}")
        conversation.refresh_from_db()
        self.assertEqual(conversation.display_name, "Maria Santos")

    def test_an_existing_name_is_kept_and_the_old_fallback_is_replaced(self):
        conversation = self.inbound()
        InquiryConversation.objects.filter(pk=conversation.pk).update(display_name="Ana the owner typed")
        tasks.fetch_messenger_profile(conversation.pk)
        conversation.refresh_from_db()
        self.assertEqual(conversation.display_name, "Ana the owner typed")
        self.assertEqual(cache.get(profile.avatar_key(conversation.pk)), PHOTO)
        InquiryConversation.objects.filter(pk=conversation.pk).update(display_name="Messenger customer")
        tasks.fetch_messenger_profile(conversation.pk)
        conversation.refresh_from_db()
        self.assertEqual(conversation.display_name, "Maria Santos")

    def test_the_name_is_cleaned_and_capped_at_120(self):
        conversation = self.inbound()
        self.meta.profile = (200, {"first_name": "  Maria\n", "last_name": "S" * 300, "profile_pic": PHOTO})
        tasks.fetch_messenger_profile(conversation.pk)
        conversation.refresh_from_db()
        self.assertEqual(len(conversation.display_name), 120)
        self.assertTrue(conversation.display_name.startswith("Maria SSS"))

    def test_a_photo_off_meta_hosts_or_not_https_is_dropped_but_the_name_is_kept(self):
        for bad in ("http://platform-lookaside.fbsbx.com/x.jpg", "https://evil.example/x.jpg",
                    "https://fbcdn.net.evil.example/x.jpg", "javascript:alert(1)", 5, None, "https://u:p@x.fbcdn.net/a"):
            cache.clear()
            conversation = self.inbound()
            self.meta.profile = (200, {"first_name": "Maria", "profile_pic": bad})
            tasks.fetch_messenger_profile(conversation.pk)
            self.assertIsNone(self.get(self.list_url).json()["items"][0]["avatar_url"], bad)
        ok = "https://scontent-hkg1-1.xx.fbcdn.net/v/t1/pic.jpg"
        self.meta.profile = (200, {"first_name": "Maria", "profile_pic": ok})
        tasks.fetch_messenger_profile(conversation.pk)
        self.assertEqual(self.get(self.list_url).json()["items"][0]["avatar_url"], ok)

    def test_a_refusal_is_silent_and_logs_codes_only(self):
        self.meta.profile = (400, {"error": {"code": 100, "error_subcode": 2018001, "type": "OAuthException",
                                             "message": f"Object with ID '{PSID}' does not exist, Maria Santos", "fbtrace_id": "T" + PSID}})
        with self.assertLogs("apps", logging.DEBUG) as logs:
            conversation = self.inbound_and_run()
        out = "\n".join(logs.output)
        self.assertIn("Messenger profile not fetched (not_found)", out)
        self.assertIn("Messenger call refused (not_found) GET /{psid} status=400 code=100 subcode=2018001", out)
        for bit in SECRET_BITS:
            self.assertNotIn(bit, out)
        conversation.refresh_from_db()
        self.assertEqual(conversation.display_name, "")
        self.assertIsNone(cache.get(profile.avatar_key(conversation.pk)))

    def test_no_answer_is_silent_and_logs_the_class_only(self):
        with mock.patch.object(messenger, "_transport", side_effect=OSError(f"timed out {PSID} {PAGE_TOKEN}")):
            with self.assertLogs("apps", logging.DEBUG) as logs:
                self.inbound_and_run()
        out = "\n".join(logs.output)
        self.assertIn("Messenger call failed (OSError) GET /{psid}", out)
        for bit in SECRET_BITS:
            self.assertNotIn(bit, out)
        self.assertEqual(self.client.get(self.list_url, secure=True).status_code, 200)

    def test_an_unexpected_failure_is_swallowed_with_the_class_name(self):
        conversation = self.inbound()
        with mock.patch.object(messenger, "profile", side_effect=ValueError(PSID)):
            with self.assertLogs("apps.inquiries.profile", logging.WARNING) as logs:
                tasks.fetch_messenger_profile(conversation.pk)
        self.assertEqual(logs.output, ["WARNING:apps.inquiries.profile:Messenger profile failed (ValueError)"])

    def test_a_queue_failure_never_fails_the_webhook(self):
        with mock.patch.object(tasks.fetch_messenger_profile, "delay", side_effect=RuntimeError(PSID)):
            with self.assertLogs("apps.inquiries.profile", logging.WARNING) as logs:
                self.inbound_and_run()
        self.assertEqual(logs.output, ["WARNING:apps.inquiries.profile:Messenger profile not queued (RuntimeError)"])

    def test_a_cached_photo_or_a_recent_try_means_no_new_call(self):
        self.inbound_and_run()
        self.assertEqual(len(self.meta.profile_calls()), 1)
        self.inbound_and_run(mid="m_2")  # photo cached: nothing to fetch
        self.assertEqual(len(self.meta.profile_calls()), 1)
        cache.delete(profile.avatar_key(InquiryConversation.objects.get().pk))  # 48 hours passed
        cache.delete(profile._retry_key(InquiryConversation.objects.get().pk))
        self.inbound_and_run(mid="m_3")
        self.assertEqual(len(self.meta.profile_calls()), 2)

    def test_a_failed_try_is_not_repeated_within_the_hour(self):
        self.meta.profile = (400, {"error": {"code": 100}})
        self.inbound_and_run()
        self.inbound_and_run(mid="m_2")
        self.assertEqual(len(self.meta.profile_calls()), 1)

    def test_no_call_without_an_active_connection_or_app_settings(self):
        conversation = self.inbound()
        with override_settings(META_MESSENGER_APP_SECRET=""):
            tasks.fetch_messenger_profile(conversation.pk)
        self.row.status = "invalid"
        self.row.save()
        tasks.fetch_messenger_profile(conversation.pk)
        self.assertEqual(self.meta.profile_calls(), [])

    def test_another_sites_conversation_cannot_show_this_sites_photo(self):
        other = self.other_site()
        self.connect_row(site=other, page=PAGE_B, user="fbuser-2")
        conversation = self.inbound_and_run()
        self.inbound(page=PAGE_B, psid="psid-ben", mid="m_ben")
        ben = InquiryConversation.objects.get(site=other)
        self.assertIsNone(cache.get(profile.avatar_key(ben.pk)))
        self.assertEqual(cache.get(profile.avatar_key(conversation.pk)), PHOTO)

    def test_profile_call_has_a_three_second_default_timeout_in_the_transport(self):
        self.assertEqual(messenger.PROFILE_TIMEOUT, 3)
        with mock.patch.object(messenger, "_transport", REAL_TRANSPORT), mock.patch(
                "apps.inquiries.messenger.urllib.request.urlopen", side_effect=OSError("x")) as opened:
            messenger.profile(PSID, PAGE_TOKEN)
            messenger.call("GET", "/x")
        self.assertEqual([c.kwargs["timeout"] for c in opened.call_args_list], [3, messenger.TIMEOUT])


class ErasureTests(InboxUiCase):
    def seed(self, conversation):
        cache.set(profile.avatar_key(conversation.pk), PHOTO, 3600)
        cache.set(profile._retry_key(conversation.pk), 1, 3600)

    def gone(self, conversation):
        return cache.get(profile.avatar_key(conversation.pk)) is None and cache.get(profile._retry_key(conversation.pk)) is None

    def test_data_deletion_deletes_the_photo_of_that_users_conversations_only(self):
        mine = self.inbound()
        other = self.other_site()
        self.connect_row(site=other, page=PAGE_B, user="fbuser-2")
        self.inbound(page=PAGE_B, psid="psid-ben", mid="m_ben")
        theirs = InquiryConversation.objects.get(site=other)
        self.seed(mine)
        self.seed(theirs)
        response = self.client.post("/v1/webhooks/messenger/data-deletion/", {"signed_request": signed_request("fbuser-1")}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.gone(mine))
        self.assertFalse(self.gone(theirs))

    def test_deauthorize_deletes_the_photo(self):
        mine = self.inbound()
        self.seed(mine)
        self.client.post("/v1/webhooks/messenger/deauthorize/", {"signed_request": signed_request("fbuser-1")}, secure=True)
        self.assertTrue(self.gone(mine))

    def test_retention_deletes_the_photo_with_the_messages(self):
        mine = self.inbound()
        kept = InquiryConversation.objects.create(
            site=self.site, connection=self.row, provider="messenger", psid="psid-new", page_id=PAGE_A, external_id="fm_new",
            stores_history=True, last_message_at=timezone.now())
        self.seed(mine)
        self.seed(kept)
        old = timezone.now() - retention.MESSAGE_RETENTION - timedelta(days=1)
        InquiryConversation.objects.filter(pk=mine.pk).update(last_message_at=old)
        with override_settings(RETENTION_ENABLED=True):
            retention.old_messages(timezone.now(), False)
        self.assertTrue(self.gone(mine))
        self.assertFalse(self.gone(kept))

    def test_a_retention_dry_run_keeps_it(self):
        mine = self.inbound()
        self.seed(mine)
        InquiryConversation.objects.filter(pk=mine.pk).update(last_message_at=timezone.now() - retention.MESSAGE_RETENTION - timedelta(days=1))
        with override_settings(RETENTION_ENABLED=True):
            retention.old_messages(timezone.now(), True)
        self.assertFalse(self.gone(mine))

    def test_site_disposal_deletes_the_photo_of_that_site_only(self):
        mine = self.inbound()
        other = self.other_site()
        self.connect_row(site=other, page=PAGE_B, user="fbuser-2")
        self.inbound(page=PAGE_B, psid="psid-ben", mid="m_ben")
        theirs = InquiryConversation.objects.get(site=other)
        self.seed(mine)
        self.seed(theirs)
        retention.blank_inquiries(Site.objects.filter(pk=self.site.pk), dry_run=True)
        self.assertFalse(self.gone(mine))
        retention.blank_inquiries(Site.objects.filter(pk=self.site.pk))
        self.assertTrue(self.gone(mine))
        self.assertFalse(self.gone(theirs))

    def test_a_fetch_that_finishes_after_erasure_leaves_no_photo(self):
        mine = self.inbound()

        def erase_meanwhile(*args, **kwargs):
            InquiryConversation.objects.filter(pk=mine.pk).update(psid="")
            return (200, {"first_name": "Maria", "profile_pic": PHOTO})
        with mock.patch.object(self.meta, "profile", create=True), mock.patch.object(
                messenger, "_transport", side_effect=erase_meanwhile):
            tasks.fetch_messenger_profile(mine.pk)
        self.assertTrue(self.gone(mine))


class DeliveryStateTests(InboxUiCase):
    def setUp(self):
        super().setUp()
        self.conversation = self.inbound()
        self.base_time = timezone.now() - timedelta(minutes=10)
        self.out = []
        for i in range(3):  # three of the Page's own messages, a minute apart
            self.out.append(InquiryMessage.objects.create(
                site=self.site, conversation=self.conversation, direction="out", sender_kind="owner", body=f"reply {i}",
                external_id=f"m_out_{i}", delivery_state="sent", created_at=self.base_time + timedelta(minutes=i)))

    def states(self):
        return [InquiryMessage.objects.get(pk=m.pk).delivery_state for m in self.out]

    def deliver(self, payload):
        with self.assertLogs("apps.inquiries.messenger_inbox", logging.WARNING) as logs:
            self.assertEqual(self.hook(payload).status_code, 200)
        self.assertEqual(len(logs.records), 1)  # still the one summary line per POST
        for bit in ("m_out_", PSID):
            self.assertNotIn(bit, logs.output[0])

    def test_delivery_by_mids_marks_those_messages_delivered(self):
        self.deliver(receipt("delivery", {"mids": ["m_out_1"]}))
        self.assertEqual(self.states(), ["sent", "delivered", "sent"])

    def test_delivery_by_watermark_marks_everything_up_to_it(self):
        self.deliver(receipt("delivery", {"watermark": ms(self.base_time + timedelta(seconds=90))}))
        self.assertEqual(self.states(), ["delivered", "delivered", "sent"])

    def test_delivery_by_mids_and_watermark_does_both(self):
        self.deliver(receipt("delivery", {"mids": ["m_out_2"], "watermark": ms(self.base_time + timedelta(seconds=30))}))
        self.assertEqual(self.states(), ["delivered", "sent", "delivered"])

    def test_read_marks_seen_from_sent_or_delivered_up_to_the_watermark(self):
        self.deliver(receipt("delivery", {"mids": ["m_out_0"]}))
        self.deliver(receipt("read", {"watermark": ms(self.base_time + timedelta(seconds=90))}))
        self.assertEqual(self.states(), ["seen", "seen", "sent"])

    def test_it_never_downgrades(self):
        self.deliver(receipt("read", {"watermark": ms(timezone.now())}))
        self.assertEqual(self.states(), ["seen"] * 3)
        self.deliver(receipt("delivery", {"mids": ["m_out_0", "m_out_1"], "watermark": ms(timezone.now())}))
        self.assertEqual(self.states(), ["seen"] * 3)
        InquiryMessage.objects.filter(pk=self.out[0].pk).update(delivery_state="failed")
        InquiryMessage.objects.filter(pk=self.out[1].pk).update(delivery_state="delivered")
        self.deliver(receipt("delivery", {"watermark": ms(timezone.now())}))
        self.assertEqual(self.states(), ["failed", "delivered", "seen"])
        self.deliver(receipt("read", {"watermark": ms(timezone.now())}))
        self.assertEqual(self.states(), ["failed", "seen", "seen"])

    def test_the_customers_own_messages_are_never_touched(self):
        incoming = InquiryMessage.objects.get(conversation=self.conversation, direction="in")
        self.deliver(receipt("read", {"watermark": ms(timezone.now())}))
        self.assertEqual(InquiryMessage.objects.get(pk=incoming.pk).delivery_state, "")

    def test_a_future_watermark_is_capped_at_now(self):
        later = InquiryMessage.objects.create(
            site=self.site, conversation=self.conversation, direction="out", sender_kind="owner", body="x",
            external_id="m_later", delivery_state="sent", created_at=timezone.now() + timedelta(hours=1))
        self.deliver(receipt("read", {"watermark": ms(timezone.now() + timedelta(days=1))}))
        self.assertEqual(InquiryMessage.objects.get(pk=later.pk).delivery_state, "sent")

    def test_garbage_receipts_change_nothing(self):
        for body in ({}, {"watermark": "soon"}, {"watermark": True}, {"mids": "m_out_0"}, {"mids": [5, None]}, {"watermark": 10**30}):
            self.deliver(receipt("read", body))
            self.deliver(receipt("delivery", body))
        self.assertEqual(self.states(), ["sent"] * 3)

    def test_a_receipt_for_another_customer_page_or_site_changes_nothing(self):
        self.deliver(receipt("read", {"watermark": ms(timezone.now())}, psid="someone-else"))
        self.deliver(receipt("read", {"watermark": ms(timezone.now())}, recipient=PAGE_B))
        self.assertEqual(self.states(), ["sent"] * 3)
        other = self.other_site()
        self.connect_row(site=other, page=PAGE_B, user="fbuser-2")
        ben = self.inbound(page=PAGE_B, psid=PSID, mid="m_ben_in")  # same customer id, another Page and site
        mine = InquiryMessage.objects.create(
            site=other, conversation=ben, direction="out", sender_kind="owner", body="b", external_id="m_out_0b",
            delivery_state="sent", created_at=self.base_time)
        self.deliver(receipt("read", {"watermark": ms(timezone.now())}))  # Ana's page
        self.assertEqual(InquiryMessage.objects.get(pk=mine.pk).delivery_state, "sent")
        self.assertEqual(self.states(), ["seen"] * 3)

    def test_the_thread_shows_delivered_and_seen(self):
        self.deliver(receipt("delivery", {"mids": ["m_out_0"]}))
        self.deliver(receipt("read", {"watermark": ms(self.base_time + timedelta(seconds=90))}))
        url = f"{self.list_url}{self.conversation.external_id}/"
        states = [m["delivery_state"] for m in self.get(url).json()["messages"] if m["direction"] == "out"]
        self.assertEqual(states, ["seen", "seen", "sent"])
        self.assertIn(("seen", "Seen"), InquiryMessage.DELIVERY_STATES)

    def published(self):
        return [call.args[1] for call in self.send_mock.call_args_list]

    def deliver_live(self, payload):
        with mock.patch.object(live, "_send") as send:
            self.send_mock = send
            with self.captureOnCommitCallbacks(execute=True):
                self.deliver(payload)

    def test_a_change_publishes_message_status_once_and_no_change_publishes_nothing(self):
        self.deliver_live(receipt("delivery", {"mids": ["m_out_0", "m_out_1"]}))
        self.assertEqual(self.published(), [{"type": "message_status", "ref": self.conversation.external_id}])
        self.deliver_live(receipt("delivery", {"mids": ["m_out_0"]}))  # already delivered
        self.assertEqual(self.published(), [])

    def test_a_read_receipt_publishes_message_status(self):
        self.deliver_live(receipt("read", {"watermark": ms(self.base_time + timedelta(seconds=90))}))
        self.assertEqual(self.published(), [{"type": "message_status", "ref": self.conversation.external_id}])

    def test_nothing_is_published_before_commit(self):
        with mock.patch.object(live, "_send") as send:
            self.deliver(receipt("delivery", {"mids": ["m_out_0"]}))  # no on_commit callbacks run in a test case
            send.assert_not_called()

    def test_the_event_carries_no_text_or_psid(self):
        self.deliver_live(receipt("delivery", {"mids": ["m_out_0"]}))
        text = json.dumps(self.published())
        for bit in (PSID, "reply 0", "m_out_"):
            self.assertNotIn(bit, text)

    def test_a_redis_failure_never_fails_the_webhook(self):
        with mock.patch.object(live, "_client", side_effect=ConnectionError("down")):
            with self.captureOnCommitCallbacks(execute=True):
                self.deliver(receipt("delivery", {"mids": ["m_out_0"]}))
        self.assertEqual(self.states(), ["delivered", "sent", "sent"])

    def test_a_publish_error_is_swallowed_with_a_fixed_warning(self):
        with mock.patch.object(live, "publish", side_effect=RuntimeError("boom")):
            with self.assertLogs("apps.inquiries.messenger_inbox", logging.WARNING) as logs:
                self.assertEqual(self.hook(receipt("delivery", {"mids": ["m_out_0"]})).status_code, 200)
        self.assertTrue(any("Messenger status not published (RuntimeError)" in line for line in logs.output))
        self.assertEqual(self.states(), ["delivered", "sent", "sent"])

class ReplyWindowTests(InboxUiCase):
    def test_closes_at_is_the_last_customer_message_plus_24_hours(self):
        conversation = self.inbound()
        thread = self.get(self.thread_url(conversation)).json()
        conversation.refresh_from_db()
        want = (conversation.last_inbound_at + timedelta(hours=24)).isoformat()
        self.assertEqual(thread["reply_window"], {"open": True, "closes_at": want})

    def test_a_closed_window_still_says_when_it_closed(self):
        conversation = self.inbound()
        InquiryConversation.objects.filter(pk=conversation.pk).update(last_inbound_at=timezone.now() - timedelta(hours=30))
        window = self.get(self.thread_url(conversation)).json()["reply_window"]
        self.assertFalse(window["open"])
        self.assertIsNotNone(window["closes_at"])

    def test_a_conversation_the_customer_never_wrote_in_has_a_null_end(self):
        self.inbound(echo=True)
        conversation = InquiryConversation.objects.get()
        self.assertIsNone(conversation.last_inbound_at)
        self.assertEqual(self.get(self.thread_url(conversation)).json()["reply_window"], {"open": False, "closes_at": None})

    def test_another_channel_has_no_window(self):
        make_stored(self.site)
        thread = self.get(f"{self.list_url}wc_{'a1b2c3d4e5f6' * 2}/").json()
        self.assertNotIn("reply_window", thread)


class SearchTests(InboxUiCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.maria = make_stored(self.site, ref="wc_" + "1" * 24, name="Maria Santos", email="maria@example.com",
                                 text="Do you deliver to Cebu?", at=now - timedelta(minutes=5))
        self.ben = make_stored(self.site, ref="wc_" + "2" * 24, name="Ben Cruz", email="ben@shop.example",
                               text="Is the BLUE shirt available", at=now - timedelta(minutes=4))
        self.messenger = self.inbound(text="Hello from messenger about coupons")
        InquiryConversation.objects.filter(pk=self.messenger.pk).update(display_name="Lola Remedios")

    def refs(self, q, **extra):
        response = self.get(f"{self.list_url}?q={q}" + "".join(f"&{k}={v}" for k, v in extra.items()))
        self.assertEqual(response.status_code, 200, response.content)
        return [i["ref"] for i in response.json()["items"]], response.json()

    def test_matches_the_name_the_email_and_any_message_text_ignoring_case(self):
        self.assertEqual(self.refs("MARIA")[0][-1:], [self.maria.external_id])
        self.assertEqual(self.refs("ben@SHOP")[0], [self.ben.external_id])
        self.assertEqual(self.refs("blue shirt")[0], [self.ben.external_id])
        self.assertEqual(self.refs("cebu")[0], [self.maria.external_id])
        self.assertEqual(self.refs("remedios")[0], [self.messenger.external_id])
        self.assertEqual(self.refs("coupons")[0], [self.messenger.external_id])

    def test_an_earlier_message_of_the_thread_matches_too(self):
        InquiryMessage.objects.create(site=self.site, conversation=self.maria, direction="out", sender_kind="owner", body="Yes, via LBC courier")
        self.assertEqual(self.refs("courier")[0], [self.maria.external_id])

    def test_no_match_is_an_empty_list_and_unread_total_stays_the_sites(self):
        refs, body = self.refs("zzzzqq")
        self.assertEqual((refs, body["stored_next_cursor"]), ([], None))
        self.assertEqual(body["unread_total"], 3)

    def test_wildcards_are_plain_characters(self):
        self.assertEqual(self.refs("%25%25")[0], [])
        self.assertEqual(self.refs("__")[0], [])

    def test_the_text_is_2_to_80_characters_else_422_invalid_query(self):
        for bad in ("", "a", "%20a%20", "x" * 81):
            response = self.get(f"{self.list_url}?q={bad}")
            self.error(response, 422, "invalid_query")
        self.assertEqual(self.get(f"{self.list_url}?q=ma").status_code, 200)
        self.assertEqual(self.get(f"{self.list_url}?q={'x' * 80}").status_code, 200)

    def test_another_sites_conversations_never_match(self):
        other = self.other_site()
        make_stored(other, ref="wc_" + "9" * 24, name="Maria Santos", email="maria@example.com", text="cebu courier coupons blue shirt")
        for text in ("maria", "cebu", "courier", "coupons", "blue shirt", "example.com"):
            refs, _ = self.refs(text)
            self.assertNotIn("wc_" + "9" * 24, refs)
        ben = self.other_client()
        response = ben.get(f"/v1/sites/{other.public_id}/inquiries/conversations/?q=lola", secure=True)
        self.assertEqual((response.status_code, response.json()["items"]), (200, []))  # this site's text is not theirs
        response = ben.get(f"/v1/sites/{other.public_id}/inquiries/conversations/?q=cebu", secure=True)
        self.assertEqual([i["ref"] for i in response.json()["items"]], ["wc_" + "9" * 24])

    def test_search_pages_with_the_same_cursor_as_the_list(self):
        now = timezone.now()
        for i in range(32):
            make_stored(self.site, ref=f"wc_{i:024d}", name=f"Pat {i}", text="the same words", at=now - timedelta(seconds=i + 10))
        first, body = self.refs("same%20words")
        self.assertEqual(len(first), stored.LIST_LIMIT)
        self.assertIsNotNone(body["stored_next_cursor"])
        second, body2 = self.refs("same%20words", stored_cursor=body["stored_next_cursor"])
        self.assertEqual((len(second), body2["stored_next_cursor"]), (2, None))
        self.assertEqual(len(set(first + second)), 32)

    def test_a_hidden_messenger_is_not_searched_while_its_switch_is_off(self):
        with override_settings(INQUIRY_MESSENGER_ENABLED=False):
            self.assertEqual(self.refs("coupons")[0], [])

    def test_search_uses_the_read_limit(self):
        with mock.patch("apps.inquiries.api_owner.READ_LIMIT", 2):
            self.assertEqual(self.get(f"{self.list_url}?q=maria").status_code, 200)
            self.assertEqual(self.get(f"{self.list_url}?q=maria").status_code, 200)
            self.assertEqual(self.get(f"{self.list_url}?q=maria").status_code, 429)


class StatusFilterTests(InboxUiCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.open1 = make_stored(self.site, ref="wc_" + "1" * 24, name="Maria Santos", text="Do you deliver to Cebu?",
                                 at=now - timedelta(minutes=5))
        self.closed1 = make_stored(self.site, ref="wc_" + "2" * 24, name="Ben Cruz", text="Cebu order done",
                                   status=InquiryConversation.CLOSED, at=now - timedelta(minutes=4))
        self.open2 = self.inbound(text="Hello from messenger")

    def refs(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        response = self.get(f"{self.list_url}?{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return [i["ref"] for i in response.json()["items"]], response.json()

    def test_open_keeps_only_open_conversations(self):
        refs, _ = self.refs(status="open")
        self.assertEqual(sorted(refs), sorted([self.open1.external_id, self.open2.external_id]))

    def test_closed_keeps_only_closed_conversations(self):
        self.assertEqual(self.refs(status="closed")[0], [self.closed1.external_id])

    def test_missing_or_empty_status_is_all(self):
        self.assertEqual(len(self.refs()[0]), 3)
        self.assertEqual(len(self.refs(status="")[0]), 3)

    def test_any_other_value_is_422_invalid_status(self):
        for bad in ("all", "Open", "x", "open,closed"):
            response = self.get(f"{self.list_url}?status={bad}")
            self.error(response, 422, "invalid_status")
            self.assertIn("status", response.json()["error"]["fields"])

    def test_works_together_with_q(self):
        self.assertEqual(self.refs(status="open", q="cebu")[0], [self.open1.external_id])
        self.assertEqual(self.refs(status="closed", q="cebu")[0], [self.closed1.external_id])
        self.assertEqual(self.refs(status="closed", q="messenger")[0], [])

    def test_pages_with_the_same_cursor_under_a_filter(self):
        now = timezone.now()
        for i in range(32):
            make_stored(self.site, ref=f"wc_{i:024d}", name=f"Pat {i}", status=InquiryConversation.CLOSED,
                        at=now - timedelta(seconds=i + 10))
        first, body = self.refs(status="closed")
        self.assertEqual(len(first), stored.LIST_LIMIT)
        self.assertIsNotNone(body["stored_next_cursor"])
        second, body2 = self.refs(status="closed", stored_cursor=body["stored_next_cursor"])
        self.assertEqual((len(second), body2["stored_next_cursor"]), (3, None))
        self.assertEqual(len(set(first + second)), 33)

    def test_unread_total_is_not_filtered(self):
        _, body = self.refs(status="closed")
        self.assertEqual(body["unread_total"], 3)

    def test_another_sites_conversations_never_appear(self):
        other = self.other_site()
        make_stored(other, ref="wc_" + "9" * 24, status=InquiryConversation.CLOSED)
        self.assertNotIn("wc_" + "9" * 24, self.refs(status="closed")[0])
        ben = self.other_client()
        response = ben.get(f"/v1/sites/{other.public_id}/inquiries/conversations/?status=closed", secure=True)
        self.assertEqual([i["ref"] for i in response.json()["items"]], ["wc_" + "9" * 24])
        response = ben.get(f"/v1/sites/{other.public_id}/inquiries/conversations/?status=open", secure=True)
        self.assertEqual(response.json()["items"], [])


class UnreadFilterTests(InboxUiCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.u1 = make_stored(self.site, ref="wc_" + "1" * 24, name="Maria Santos", text="Do you deliver to Cebu?",
                              unread=True, at=now - timedelta(minutes=5))
        self.r1 = make_stored(self.site, ref="wc_" + "2" * 24, name="Ben Cruz", text="Cebu order done",
                              unread=False, status=InquiryConversation.CLOSED, at=now - timedelta(minutes=4))
        self.r2 = make_stored(self.site, ref="wc_" + "3" * 24, name="Ana Lim", text="Thanks",
                              unread=False, at=now - timedelta(minutes=3))

    def refs(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        response = self.get(f"{self.list_url}?{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return [i["ref"] for i in response.json()["items"]], response.json()

    def test_unread_1_keeps_only_unread(self):
        self.assertEqual(self.refs(unread=1)[0], [self.u1.external_id])

    def test_unread_0_keeps_only_read(self):
        self.assertEqual(sorted(self.refs(unread=0)[0]), sorted([self.r1.external_id, self.r2.external_id]))

    def test_missing_or_empty_is_all(self):
        self.assertEqual(len(self.refs()[0]), 3)
        self.assertEqual(len(self.refs(unread="")[0]), 3)

    def test_any_other_value_is_422_invalid_unread(self):
        for bad in ("true", "2", "yes", "1,0", "-1"):
            response = self.get(f"{self.list_url}?unread={bad}")
            self.error(response, 422, "invalid_unread")
            self.assertIn("unread", response.json()["error"]["fields"])

    def test_works_together_with_status_and_q(self):
        self.assertEqual(self.refs(unread=0, status="closed")[0], [self.r1.external_id])
        self.assertEqual(self.refs(unread=0, status="open")[0], [self.r2.external_id])
        self.assertEqual(self.refs(unread=1, status="closed")[0], [])
        self.assertEqual(self.refs(unread=0, q="cebu")[0], [self.r1.external_id])
        self.assertEqual(self.refs(unread=1, q="cebu")[0], [self.u1.external_id])
        self.assertEqual(self.refs(unread=1, q="thanks")[0], [])

    def test_pages_with_the_same_cursor_under_a_filter(self):
        now = timezone.now()
        for i in range(32):
            make_stored(self.site, ref=f"wc_{i:024d}", name=f"Pat {i}", unread=False, at=now - timedelta(seconds=i + 10))
        first, body = self.refs(unread=0)
        self.assertEqual(len(first), stored.LIST_LIMIT)
        self.assertIsNotNone(body["stored_next_cursor"])
        second, body2 = self.refs(unread=0, stored_cursor=body["stored_next_cursor"])
        self.assertEqual((len(second), body2["stored_next_cursor"]), (4, None))
        self.assertEqual(len(set(first + second)), 34)

    def test_unread_total_is_not_filtered(self):
        self.assertEqual(self.refs(unread=0)[1]["unread_total"], 1)
        self.assertEqual(self.refs(unread=1)[1]["unread_total"], 1)

    def test_another_sites_conversations_never_appear(self):
        other = self.other_site()
        make_stored(other, ref="wc_" + "9" * 24, unread=True)
        make_stored(other, ref="wc_" + "8" * 24, unread=False)
        self.assertNotIn("wc_" + "9" * 24, self.refs(unread=1)[0])
        self.assertNotIn("wc_" + "8" * 24, self.refs(unread=0)[0])
        ben = self.other_client()
        response = ben.get(f"/v1/sites/{other.public_id}/inquiries/conversations/?unread=1", secure=True)
        self.assertEqual([i["ref"] for i in response.json()["items"]], ["wc_" + "9" * 24])


class UnreadTests(InboxUiCase):
    def setUp(self):
        super().setUp()
        self.unread_url = f"/v1/sites/{self.site_id}/inquiries/unread/"

    def test_counts_this_sites_unread_conversations_only(self):
        self.assertEqual(self.get(self.unread_url).json(), {"count": 0})
        a = make_stored(self.site, ref="wc_" + "1" * 24, unread=True)
        make_stored(self.site, ref="wc_" + "2" * 24, unread=False)
        make_stored(self.site, ref="wc_" + "3" * 24, unread=True)
        other = self.other_site()
        make_stored(other, ref="wc_" + "9" * 24, unread=True)
        self.assertEqual(self.get(self.unread_url).json(), {"count": 2})
        body = self.get(self.list_url).json()
        self.assertEqual(body["unread_total"], 2)
        self.assertEqual(self.other_client().get(f"/v1/sites/{other.public_id}/inquiries/unread/", secure=True).json(), {"count": 1})
        self.send("post", f"{self.list_url}{a.external_id}/read/")
        self.assertEqual(self.get(self.unread_url).json(), {"count": 1})
        self.assertEqual(self.get(self.list_url).json()["unread_total"], 1)

    def test_it_is_one_indexed_count_query(self):
        make_stored(self.site, unread=True)
        with self.assertNumQueries(1):
            stored.unread_count(self.site)

    def test_the_total_ignores_the_search_and_the_paging(self):
        make_stored(self.site, ref="wc_" + "1" * 24, name="Maria", unread=True)
        make_stored(self.site, ref="wc_" + "2" * 24, name="Ben", unread=True)
        self.assertEqual(self.get(f"{self.list_url}?q=maria").json()["unread_total"], 2)

    def test_messenger_counts_only_while_its_switch_is_on(self):
        self.inbound()
        self.assertEqual(self.get(self.unread_url).json(), {"count": 1})
        with override_settings(INQUIRY_MESSENGER_ENABLED=False):
            self.assertEqual(self.get(self.unread_url).json(), {"count": 0})

    def test_same_auth_as_the_other_inbox_routes_and_the_read_limit(self):
        self.error(self.get(f"/v1/sites/s_doesnotexist/inquiries/unread/"), 404, "not_found")
        self.error(self.other_client().get(self.unread_url, secure=True), 404, "not_found")
        with override_settings(INQUIRIES_ENABLED=False):
            self.error(self.get(self.unread_url), 404, "not_found")
        with mock.patch("apps.inquiries.api_owner.READ_LIMIT", 1):
            self.assertEqual(self.get(self.unread_url).status_code, 200)
            self.assertEqual(self.get(self.unread_url).status_code, 429)
        self.client.cookies.clear()
        self.error(self.get(self.unread_url), 401, "unauthenticated")

    def test_the_list_without_stored_tables_still_has_the_total(self):
        with mock.patch("apps.inquiries.api_owner.stored_tables_exist", return_value=False):
            self.assertEqual(self.get(self.list_url).json(), {"items": [], "stored_next_cursor": None, "unread_total": 0})
            self.assertEqual(self.get(self.unread_url).json(), {"count": 0})
