"""Facebook comments (task z8uq9mg9pe), with recorded fake Meta payloads only: no live Meta, no real
token. `messenger._transport` is replaced by a fake that records every call."""
import json
import time
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlparse

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.inquiries import comments, messenger, messenger_inbox, retention
from apps.inquiries.models import (
    InquiryComment, InquiryCommentAction, InquiryCommentSettings, InquiryConnection, InquiryConversation,
    InquiryMessage, InquiryPost,
)
from apps.inquiries.tests.test_messenger import (
    PAGE_A, PAGE_B, PAGE_TOKEN, SECRET, FakeMeta, MessengerTestCase, signed_request,
)
from apps.sites.models import Site

POST = "111000111_222"
COMMENT = "111000111_333"
COMMENTER = "7700001"
comments_on = override_settings(INQUIRY_COMMENTS_ENABLED=True, META_COMMENTS_CONFIG_ID="")


def feed(comment_id=COMMENT, verb="add", page=PAGE_A, post_id=POST, parent_id=None, author=COMMENTER,
         name="Maria Santos", message="How much is the medium?", created=None, post=None, item="comment"):
    value = {"item": item, "verb": verb, "comment_id": comment_id, "post_id": post_id,
             "parent_id": parent_id or post_id, "created_time": created or int(time.time())}
    if verb != "remove":
        value.update({"from": {"id": author, "name": name}, "message": message})
    if post is not None:
        value["post"] = post
    return {"object": "page", "entry": [{"id": page, "time": 1, "changes": [{"field": "feed", "value": value}]}]}


class CommentMeta(FakeMeta):
    """FakeMeta plus the three comment calls, answered the way Meta's docs show."""

    def __init__(self):
        super().__init__()
        self.comment_error = None
        self.replied = 0

    def __call__(self, method, path, *, params=None, form=None, token=None):
        if path.endswith("/comments") or (method == "POST" and path.count("/") == 1 and "_" in path):
            self.calls.append((method, path, params or {}, form or {}, token))
            if self.comment_error:
                return self.comment_error
            if path.endswith("/comments"):
                self.replied += 1
                return 200, {"id": f"111000111_900{self.replied}"}
            return 200, {"success": True}
        return super().__call__(method, path, params=params, form=form, token=token)


@comments_on
class CommentsTestCase(MessengerTestCase):
    def setUp(self):
        super().setUp()
        self.meta = CommentMeta()
        patch = mock.patch.object(messenger, "_transport", self.meta)
        patch.start()
        self.addCleanup(patch.stop)
        self.base = f"/v1/sites/{self.site_id}/inquiries/comments/"

    def ready(self, site=None, page=PAGE_A):
        row = self.connect_row(site=site, page=page) if not InquiryConnection.objects.filter(
            site=site or self.site).exists() else InquiryConnection.objects.get(site=site or self.site)
        InquiryCommentSettings.objects.update_or_create(
            site=site or self.site, defaults={"feed_subscribed": True, "page_id": row.external_page_id})
        return row

    def feed_in(self, **kw):
        response = self.hook(feed(**kw))
        self.assertEqual(response.status_code, 200)
        return InquiryComment.objects.filter(external_id=kw.get("comment_id", COMMENT)).first()

    def stored(self, **kw):
        self.ready()
        return self.feed_in(**kw)

    def query(self, path, params):
        return self.get(path + "?" + urlencode(params))

    def other_site(self):
        return Site.objects.get(public_id=self.create_site(client=self.other_client(), subdomain="ben-store")["id"])


class FeedWebhookTests(CommentsTestCase):
    def test_an_added_comment_is_stored_with_its_post(self):
        self.connect_row()
        comment = self.feed_in(post={"message": "Fresh mangoes  3 kilos", "is_published": True,
                                     "permalink_url": "https://www.facebook.com/111000111/posts/222"})
        self.assertEqual((comment.site, comment.text, comment.author_name, comment.author_psid, comment.unread),
                         (self.site, "How much is the medium?", "Maria Santos", COMMENTER, True))
        self.assertEqual(comment.parent_external_id, POST)
        self.assertTrue(comment.ref.startswith("cm_"))
        post = InquiryPost.objects.get()
        self.assertEqual((post.external_id, post.is_ad, post.caption, post.permalink),
                         (POST, False, "Fresh mangoes 3 kilos", "https://www.facebook.com/111000111/posts/222"))

    def test_a_permalink_with_a_query_is_replaced_by_the_id_pattern(self):
        self.connect_row()
        self.feed_in(post={"permalink_url": "https://www.facebook.com/x/posts/1?access_token=SECRET"})
        self.assertEqual(InquiryPost.objects.get().permalink, f"https://www.facebook.com/{POST}")

    def test_an_unpublished_post_is_marked_as_an_ad(self):
        self.connect_row()
        self.feed_in(post={"is_published": False})
        self.assertTrue(InquiryPost.objects.get().is_ad)

    def test_it_is_idempotent_by_comment_id(self):
        self.connect_row()
        self.feed_in()
        self.feed_in()
        self.feed_in(message="a retry with other text")
        self.assertEqual((InquiryComment.objects.count(), InquiryPost.objects.count()), (1, 1))
        self.assertEqual(InquiryComment.objects.get().text, "How much is the medium?")

    def test_an_edit_changes_the_text_and_never_revives_a_removed_comment(self):
        self.connect_row()
        self.feed_in()
        self.feed_in(verb="edited", message="And the large?")
        self.assertEqual(InquiryComment.objects.get().text, "And the large?")
        self.feed_in(verb="remove")
        self.feed_in(verb="edited", message="back again")
        comment = InquiryComment.objects.get()
        self.assertEqual((comment.removed, comment.text), (True, ""))

    def test_a_remove_deletes_the_text_and_the_author_and_cancels_queued_actions(self):
        self.connect_row()
        comment = self.feed_in()
        queued = InquiryCommentAction.objects.create(
            site=self.site, comment=comment, kind="public", source="rule", state=InquiryCommentAction.QUEUED)
        self.feed_in(verb="remove")
        comment.refresh_from_db()
        self.assertEqual((comment.removed, comment.text, comment.author_name, comment.author_psid, comment.unread),
                         (True, "", "", "", False))
        queued.refresh_from_db()
        self.assertEqual(queued.state, "cancelled")

    def test_a_remove_for_an_unknown_comment_keeps_a_tombstone_so_a_late_add_is_not_stored(self):
        self.connect_row()
        self.feed_in(verb="remove")
        self.feed_in()
        comment = InquiryComment.objects.get()
        self.assertEqual((comment.removed, comment.text), (True, ""))

    def test_hide_and_unhide_events_follow_facebook(self):
        self.connect_row()
        self.feed_in()
        self.feed_in(verb="hide")
        self.assertTrue(InquiryComment.objects.get().is_hidden)
        self.feed_in(verb="unhide")
        self.assertFalse(InquiryComment.objects.get().is_hidden)

    def test_the_pages_own_comment_is_not_unread_and_answers_its_parent(self):
        self.connect_row()
        self.feed_in()
        own = self.feed_in(comment_id="111000111_334", author=PAGE_A, name="Ana Store Page",
                           parent_id=COMMENT, message="Hello Maria!")
        self.assertEqual((own.is_page, own.unread, own.author_psid), (True, False, ""))
        self.assertIsNotNone(InquiryComment.objects.get(external_id=COMMENT).public_reply_at)

    def test_other_items_fields_and_verbs_are_ignored(self):
        self.connect_row()
        self.feed_in(item="post")
        self.feed_in(verb="block")
        self.feed_in(comment_id="not an id!")
        self.assertFalse(InquiryComment.objects.exists())

    def test_a_wrong_page_id_stores_nothing(self):
        self.connect_row()
        self.feed_in(page="999000999")
        self.assertFalse(InquiryComment.objects.exists())

    def test_a_bad_signature_is_403_and_stores_nothing(self):
        self.connect_row()
        body = json.dumps(feed()).encode()
        for header in (False, "sha256=" + "0" * 64):
            self.assertEqual(self.hook(None, raw=body, header=header).status_code, 403)
        self.assertFalse(InquiryComment.objects.exists())

    def test_the_comment_goes_to_the_site_that_owns_the_page_only(self):
        self.connect_row()
        other = self.other_site()
        self.connect_row(site=other, page=PAGE_B, user="fbuser-2")
        self.feed_in(page=PAGE_B, comment_id="222000222_5", post_id="222000222_1")
        self.assertEqual(InquiryComment.objects.get().site, other)
        self.assertFalse(InquiryComment.objects.filter(site=self.site).exists())

    def test_the_switch_off_stores_nothing(self):
        self.connect_row()
        for off in ({"INQUIRY_COMMENTS_ENABLED": False}, {"INQUIRY_MESSENGER_ENABLED": False}):
            with override_settings(**off):
                self.assertEqual(self.hook(feed()).status_code, 200)
        self.assertFalse(InquiryComment.objects.exists() or InquiryPost.objects.exists())

    def test_messaging_events_still_work_with_comments_on_and_the_summary_has_counts_only(self):
        self.connect_row()
        with self.assertLogs("apps.inquiries.comments", "WARNING") as logs:
            self.feed_in()
        out = "\n".join(logs.output)
        self.assertIn("Comments webhook summary stored:1", out)
        for private in (COMMENT, POST, COMMENTER, "Maria", "How much", PAGE_A):
            self.assertNotIn(private, out)
        self.inbound(mid="m_x", text="hello")
        self.assertEqual(InquiryMessage.objects.count(), 1)

    def test_a_broken_change_does_not_lose_the_others(self):
        self.connect_row()
        payload = feed()
        payload["entry"][0]["changes"].insert(0, {"field": "feed", "value": "junk"})
        payload["entry"][0]["changes"].insert(0, "junk")
        self.assertEqual(self.hook(payload).status_code, 200)
        self.assertEqual(InquiryComment.objects.count(), 1)


class ListAndDetailTests(CommentsTestCase):
    def make(self, n, **kw):
        return self.feed_in(comment_id=f"111000111_4{n:02d}", **kw)

    def test_the_list_shapes_a_comment(self):
        self.ready()
        self.feed_in(post={"message": "Mangoes", "is_published": True})
        body = self.get(self.base).json()
        self.assertEqual((body["next_cursor"], body["unread_total"]), (None, 1))
        item = body["items"][0]
        self.assertEqual(set(item), {"ref", "author_name", "text", "created_at", "unread", "answered", "hidden",
                                     "is_reply", "post", "public_reply", "private_reply", "hide"})
        self.assertEqual(item["post"], {"id": POST, "is_ad": False, "permalink": f"https://www.facebook.com/{POST}", "caption": "Mangoes"})
        self.assertEqual(item["public_reply"], {"available": True, "reason": "", "replied_at": None})
        self.assertTrue(item["private_reply"]["available"])
        self.assertNotIn(COMMENT, json.dumps(body))  # Meta's comment id never reaches the browser
        self.assertNotIn(COMMENTER, json.dumps(body))

    def test_filters_search_and_paging(self):
        self.ready()
        now = int(time.time())
        self.make(1, created=now - 50, post={"is_published": False}, message="price please")
        self.make(2, created=now - 40, message="Interested", name="Juan", post_id="111000111_998")
        self.make(3, created=now - 30, post_id="111000111_999", message="Where are you?")
        self.feed_in(comment_id="111000111_500", author=PAGE_A, name="Ana", parent_id="111000111_401", message="Hi", created=now - 20)
        ids = lambda **q: [i["text"] for i in self.query(self.base, q).json()["items"]]
        self.assertEqual(ids(), ["Where are you?", "Interested", "price please"])
        self.assertEqual(ids(scope="ad"), ["price please"])
        self.assertEqual(ids(scope="post"), ["Where are you?", "Interested"])
        self.assertEqual(ids(answered="answered"), ["price please"])  # the Page answered it on Facebook
        self.assertEqual(ids(answered="unanswered"), ["Where are you?", "Interested"])
        self.assertEqual(ids(q="JUAN"), ["Interested"])
        self.assertEqual(ids(q="price"), ["price please"])
        first = self.get(self.base).json()
        InquiryComment.objects.filter(external_id="111000111_402").update(unread=False)
        self.assertEqual(ids(unread="0"), ["Interested"])
        self.assertEqual(len(ids(unread="1")), 2)
        with mock.patch.object(comments, "LIST_LIMIT", 2):
            page = self.get(self.base).json()
            self.assertEqual(len(page["items"]), 2)
            self.assertIsNotNone(page["next_cursor"])
            rest = self.query(self.base, {"cursor": page["next_cursor"]}).json()
            self.assertEqual([i["text"] for i in rest["items"]], ["price please"])
            self.assertIsNone(rest["next_cursor"])
        self.assertEqual(first["unread_total"], 3)

    def test_bad_query_values_are_422(self):
        self.ready()
        for params, code in (({"scope": "x"}, "invalid_scope"), ({"answered": "x"}, "invalid_answered"),
                             ({"unread": "2"}, "invalid_unread"), ({"q": "a"}, "invalid_query"),
                             ({"cursor": "nope"}, "invalid_cursor")):
            self.error(self.query(self.base, params), 422, code)

    def test_removed_and_own_comments_are_not_listed(self):
        self.ready()
        self.feed_in()
        self.feed_in(comment_id="111000111_334", author=PAGE_A, parent_id=COMMENT)
        self.feed_in(comment_id="111000111_335")
        self.feed_in(comment_id="111000111_335", verb="remove")
        self.assertEqual(len(self.get(self.base).json()["items"]), 1)

    def test_detail_has_the_replies_and_the_actions(self):
        self.ready()
        comment = self.feed_in()
        self.feed_in(comment_id="111000111_334", author=PAGE_A, name="Ana Store Page", parent_id=COMMENT, message="Hello!")
        InquiryCommentAction.objects.create(site=self.site, comment=comment, kind="hide", state="sent")
        body = self.get(self.base + comment.ref + "/").json()
        self.assertEqual([(r["author_name"], r["is_page"], r["text"]) for r in body["replies"]], [("Ana Store Page", True, "Hello!")])
        self.assertEqual([(a["kind"], a["state"]) for a in body["actions"]], [("hide", "sent")])
        self.assertTrue(body["answered"])

    def test_mark_read(self):
        self.ready()
        comment = self.feed_in()
        body = self.send("post", self.base + comment.ref + "/read/").json()
        self.assertFalse(body["unread"])
        self.assertFalse(InquiryComment.objects.get().unread)

    def test_another_sites_comments_are_invisible_and_untouchable(self):
        other = self.other_site()
        self.ready(site=other, page=PAGE_B)
        theirs = self.feed_in(page=PAGE_B, comment_id="222000222_5", post_id="222000222_1")
        self.ready()
        mine = self.feed_in()
        self.assertEqual([i["ref"] for i in self.get(self.base).json()["items"]], [mine.ref])
        self.assertEqual(self.get(self.base).json()["unread_total"], 1)
        url = self.base + theirs.ref + "/"
        self.error(self.get(url), 404, "not_found")
        for path, body in (("read/", None), ("reply/", {"text": "hi", "idempotency_key": "key-00000001"}),
                           ("private-reply/", {"text": "hi", "idempotency_key": "key-00000002"}), ("hide/", None)):
            self.error(self.send("post", url + path, body), 404, "not_found")
        self.assertEqual(self.meta.paths().count(("POST", f"/{theirs.external_id}/comments")), 0)
        # and the other site's owner cannot read mine through their own site id
        self.error(self.get(f"/v1/sites/{other.public_id}/inquiries/comments/{mine.ref}/", client=self.other_client()), 404, "not_found")

    def test_every_route_is_404_when_off_or_unconfigured_and_401_signed_out(self):
        self.ready()
        comment = self.feed_in()
        calls = lambda: [
            self.get(self.base), self.get(self.base + "status/"), self.get(self.base + comment.ref + "/"),
            self.send("post", self.base + comment.ref + "/read/"), self.send("post", self.base + comment.ref + "/hide/"),
            self.send("post", self.base + comment.ref + "/reply/", {"text": "hi", "idempotency_key": "key-00000001"}),
            self.send("post", self.base + comment.ref + "/private-reply/", {"text": "hi", "idempotency_key": "key-00000002"}),
        ]
        for off in ({"INQUIRY_COMMENTS_ENABLED": False}, {"INQUIRY_MESSENGER_ENABLED": False}, {"META_MESSENGER_APP_SECRET": ""}):
            with override_settings(**off):
                for response in calls():
                    self.error(response, 404, "not_found")
        before = list(self.meta.calls)
        self.client.cookies.clear()
        for response in calls():
            self.error(response, 401, "unauthenticated")
        self.assertEqual(self.meta.calls, before)  # nothing was sent to Meta

    def test_status_says_why_comments_are_not_ready(self):
        self.assertEqual(self.get(self.base + "status/").json()["reason"], "not_connected")
        row = self.connect_row()
        status = self.get(self.base + "status/").json()
        self.assertEqual((status["connected"], status["ready"], status["reason"]), (True, False, "permission_missing"))
        self.ready()
        self.assertEqual(self.get(self.base + "status/").json(), {
            "enabled": True, "connected": True, "page_name": "Ana Store Page", "ready": True, "reason": ""})
        InquiryConnection.objects.filter(pk=row.pk).update(status="invalid")
        self.assertEqual(self.get(self.base + "status/").json()["reason"], "reconnect")


class ActionTests(CommentsTestCase):
    def setUp(self):
        super().setUp()
        self.ready()
        self.comment = self.feed_in()
        self.url = self.base + self.comment.ref + "/"

    def reply(self, key="key-00000001", text="Hi Maria, 120 pesos!"):
        return self.send("post", self.url + "reply/", {"text": text, "idempotency_key": key})

    def private(self, key="key-00000002", text="Hello! Here are the details."):
        return self.send("post", self.url + "private-reply/", {"text": text, "idempotency_key": key})

    def age(self, days):
        InquiryComment.objects.filter(pk=self.comment.pk).update(created_at=timezone.now() - timedelta(days=days))

    def test_a_public_reply_goes_to_the_comment_edge_and_is_audited_without_the_text(self):
        response = self.reply()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["state"], body["error_code"]), ("sent", ""))
        self.assertTrue(body["comment"]["answered"])
        self.assertFalse(body["comment"]["unread"])
        method, path, params, form, token = self.meta.calls[-1]
        self.assertEqual((method, path, token), ("POST", f"/{COMMENT}/comments", PAGE_TOKEN))
        self.assertEqual(form["message"], "Hi Maria, 120 pesos!")
        self.assertNotIn(PAGE_TOKEN, json.dumps(form))
        action = InquiryCommentAction.objects.get()
        self.assertEqual((action.kind, action.source, action.state, action.error_code, action.meta_id, action.author),
                         ("public", "manual", "sent", "", "111000111_9001", self.account))
        for field in action._meta.concrete_fields:
            value = str(getattr(action, field.attname))
            self.assertNotIn("120 pesos", value)
            self.assertNotIn(PAGE_TOKEN, value)

    def test_a_repeated_key_sends_once(self):
        self.reply()
        again = self.reply()
        self.assertEqual(again.json()["state"], "sent")
        self.assertEqual(self.meta.replied, 1)
        self.assertEqual(InquiryCommentAction.objects.count(), 1)

    def test_a_key_used_for_another_comment_is_a_conflict(self):
        self.reply()
        other = self.feed_in(comment_id="111000111_340")
        self.error(self.send("post", self.base + other.ref + "/reply/", {"text": "x", "idempotency_key": "key-00000001"}), 409, "conflict")

    def test_text_and_key_are_checked(self):
        self.error(self.reply(text="  "), 422, "invalid")
        self.error(self.reply(text="x" * 501), 422, "invalid")
        self.error(self.reply(key="short"), 422, "invalid")
        self.error(self.private(text="x" * 1001), 422, "invalid")
        self.assertEqual(self.private(text="x" * 1000).status_code, 200)
        self.assertFalse([c for c in self.meta.calls if c[1] == f"/{PAGE_A}/messages" and not c[3]])

    def test_a_meta_refusal_is_a_200_with_a_fixed_code_and_never_the_meta_text(self):
        self.meta.comment_error = (400, {"error": {"code": 10, "error_subcode": 1, "message": "LEAKED-META-TEXT token PAGETOKEN-made-up-xxxxxxxxxxxxxxxx"}})
        response = self.reply()
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["state"], response.json()["error_code"]), ("failed", "forbidden_page"))
        self.assertNotIn("LEAKED-META-TEXT", response.content.decode())
        action = InquiryCommentAction.objects.get()
        self.assertEqual((action.state, action.error_code, action.meta_id), ("failed", "forbidden_page", ""))
        self.assertIsNone(InquiryComment.objects.get().public_reply_at)

    def test_a_rejected_token_marks_the_connection_invalid_and_later_sends_are_refused(self):
        self.meta.comment_error = (400, {"error": {"code": 190, "message": "x"}})
        self.assertEqual(self.reply().json()["error_code"], "token_invalid")
        self.assertEqual(InquiryConnection.objects.get().status, "invalid")
        self.error(self.reply(key="key-00000009"), 409, "reconnect")

    def test_a_rate_limit_answer_is_blocked_rate(self):
        self.meta.comment_error = (429, {"error": {"code": 4}})
        self.assertEqual((self.reply().json()["state"], InquiryCommentAction.objects.get().state), ("blocked_rate", "blocked_rate"))

    def test_our_own_rate_cap_stops_the_call(self):
        with mock.patch("apps.inquiries.limiter.acquire", return_value=False):
            body = self.reply().json()
        self.assertEqual((body["state"], body["error_code"]), ("blocked_rate", "rate_limited"))
        self.assertEqual(self.meta.replied, 0)

    def test_the_owners_public_reply_is_stored_and_shown_after_a_reload(self):
        InquiryConnection.objects.filter(site=self.site).update(label="Ana Store Page")
        self.assertEqual(self.reply().json()["state"], "sent")
        row = InquiryComment.objects.get(external_id="111000111_9001")
        self.assertEqual((row.is_page, row.text, row.unread, row.parent_external_id, row.post, row.author_name, row.author_psid),
                         (True, "Hi Maria, 120 pesos!", False, COMMENT, self.comment.post, "Ana Store Page", ""))
        replies = self.get(self.url).json()["replies"]
        self.assertEqual([(r["kind"], r["author_name"], r["is_page"], r["text"]) for r in replies],
                         [("comment", "Ana Store Page", True, "Hi Maria, 120 pesos!")])

    def test_the_webhook_echo_of_the_stored_reply_is_a_duplicate(self):
        self.reply()
        counts = comments.receive(
            InquiryConnection.objects.get(site=self.site), PAGE_A,
            feed(comment_id="111000111_9001", author=PAGE_A, parent_id=COMMENT, message="Hi Maria, 120 pesos!")
            ["entry"][0]["changes"], timezone.now())
        self.assertEqual((counts[comments.DUPLICATE], counts[comments.STORED]), (1, 0))
        self.assertEqual(InquiryComment.objects.filter(external_id="111000111_9001").count(), 1)

    def test_a_reply_without_a_valid_meta_id_stores_no_comment(self):
        with mock.patch.object(messenger, "reply_comment", return_value=messenger.Result("ok", "bad id!")):
            self.assertEqual(self.reply().json()["state"], "sent")
        self.assertEqual(InquiryComment.objects.count(), 1)

    def test_a_private_message_is_kept_and_shown_in_time_order(self):
        self.reply()
        self.assertEqual(self.private().json()["state"], "sent")
        action = InquiryCommentAction.objects.get(kind="private")
        self.assertEqual(action.text, "Hello! Here are the details.")
        self.assertEqual(InquiryCommentAction.objects.get(kind="public").text, "")
        replies = self.get(self.url).json()["replies"]
        self.assertEqual([(r["kind"], r["ref"] == "", r["is_page"], r["text"]) for r in replies],
                         [("comment", False, True, "Hi Maria, 120 pesos!"), ("private", True, True, "Hello! Here are the details.")])
        self.assertEqual(sorted(r["created_at"] for r in replies), [r["created_at"] for r in replies])

    def test_a_failed_private_message_stores_no_text(self):
        self.meta.send_error = (400, {"error": {"code": 10, "error_subcode": 2018278}})
        self.assertEqual(self.private().json()["state"], "failed")
        self.assertEqual(InquiryCommentAction.objects.get().text, "")
        self.assertEqual(self.get(self.url).json()["replies"], [])

    def test_removing_the_comment_blanks_the_private_text(self):
        self.private()
        self.feed_in(verb="remove")
        self.assertEqual(InquiryCommentAction.objects.get().text, "")

    def test_disposal_and_erasure_delete_the_private_text_with_the_comment(self):
        self.private()
        retention.blank_inquiries(Site.objects.filter(pk=self.site.pk))
        self.assertFalse(InquiryCommentAction.objects.exists())

    def test_private_reply_goes_to_the_send_api_with_the_comment_id(self):
        response = self.private()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["state"], "sent")
        method, path, params, form, token = self.meta.calls[-1]
        self.assertEqual((method, path, token), ("POST", f"/{PAGE_A}/messages", PAGE_TOKEN))
        self.assertEqual(json.loads(form["recipient"]), {"comment_id": COMMENT})
        item = self.get(self.url).json()
        self.assertEqual(item["private_reply"]["reason"], "already_sent")
        self.assertTrue(item["answered"])

    def test_only_one_private_reply_per_comment(self):
        self.private()
        self.error(self.private(key="key-00000003"), 409, "already_sent")
        self.assertEqual(len([c for c in self.meta.calls if c[1] == f"/{PAGE_A}/messages"]), 1)

    def test_the_database_refuses_a_second_live_private_action(self):
        from django.db import IntegrityError, transaction
        InquiryCommentAction.objects.create(site=self.site, comment=self.comment, kind="private", state="sending")
        with self.assertRaises(IntegrityError), transaction.atomic():
            InquiryCommentAction.objects.create(site=self.site, comment=self.comment, kind="private", state="sent")

    def test_a_failed_private_reply_does_not_use_up_the_one_reply(self):
        self.meta.send_error = (400, {"error": {"code": 10, "error_subcode": 2018278}})
        self.assertEqual(self.private().json()["error_code"], "window_closed")
        self.meta.send_error = None
        self.assertEqual(self.private(key="key-00000003").json()["state"], "sent")

    def test_private_reply_after_seven_days_is_refused_before_any_call(self):
        self.age(7)
        self.age(8)
        item = self.get(self.url).json()
        self.assertEqual((item["private_reply"]["available"], item["private_reply"]["reason"]), (False, "window_closed"))
        self.assertTrue(item["public_reply"]["available"])
        calls = len(self.meta.calls)
        self.error(self.private(), 409, "window_closed")
        self.assertEqual(len(self.meta.calls), calls)
        self.age(6)
        self.assertEqual(self.private().status_code, 200)

    def test_hide_calls_is_hidden_true_and_marks_the_comment(self):
        response = self.send("post", self.url + "hide/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["comment"]["hidden"])
        method, path, params, form, token = self.meta.calls[-1]
        self.assertEqual((method, path, form["is_hidden"], token), ("POST", f"/{COMMENT}", "true", PAGE_TOKEN))
        self.assertEqual(InquiryCommentAction.objects.get().kind, "hide")
        self.error(self.send("post", self.url + "hide/"), 409, "already_hidden")

    def test_a_removed_comment_cannot_be_acted_on(self):
        self.feed_in(verb="remove")
        calls = len(self.meta.calls)
        for path, body in (("reply/", {"text": "x", "idempotency_key": "key-00000001"}), ("hide/", None)):
            self.error(self.send("post", self.url + path, body), 409, "comment_removed")
        self.error(self.get(self.url), 404, "not_found")
        self.assertEqual(len(self.meta.calls), calls)

    def test_the_pages_own_comment_cannot_be_acted_on(self):
        own = self.feed_in(comment_id="111000111_334", author=PAGE_A, parent_id=COMMENT)
        self.error(self.send("post", self.base + own.ref + "/hide/"), 409, "own_comment")

    def test_sends_are_refused_until_comments_are_turned_on_for_the_page(self):
        InquiryCommentSettings.objects.update(feed_subscribed=False)
        self.error(self.reply(), 409, "permission_missing")
        InquiryCommentSettings.objects.update(feed_subscribed=True, page_id="other")
        self.error(self.reply(), 409, "permission_missing")  # the connection is for another Page now
        self.assertEqual(self.meta.replied, 0)
        item = self.get(self.url).json()
        self.assertEqual(item["hide"], {"available": False, "reason": "permission_missing"})


class ConnectFeedTests(CommentsTestCase):
    def callback(self, state):
        return self.client.get("/v1/inquiries/messenger/callback/", {"state": state, "code": "AUTHCODE"}, secure=True)

    def start(self, suffix=""):
        response = self.get(self.base.replace("comments", "messenger") + "start/" + suffix)
        self.assertEqual(response.status_code, 302)
        return response["Location"]

    def connect(self, comment_perms=True, comments_flag=True):
        if comment_perms:
            self.meta.permissions = self.meta.permissions + list(messenger.COMMENT_PERMISSIONS)
        state = parse_qs(urlparse(self.start("?comments=1" if comments_flag else "")).query)["state"][0]
        response = self.callback(state)
        return response, self.post(self.base.replace("comments", "messenger") + "connect/", {"page_id": PAGE_A})

    def subscribe_fields(self):
        calls = [c for c in self.meta.calls if c[1] == f"/{PAGE_A}/subscribed_apps" and c[0] == "POST"]
        return calls[-1][3]["subscribed_fields"]

    def test_start_with_comments_asks_for_the_six_scopes_or_the_second_configuration(self):
        query = parse_qs(urlparse(self.start("?comments=1")).query)
        self.assertEqual(query["scope"], [messenger.COMMENT_SCOPES])
        self.assertIn("pages_manage_engagement", query["scope"][0])
        with override_settings(META_COMMENTS_CONFIG_ID="cfg_comments", META_MESSENGER_CONFIG_ID="cfg_msg"):
            query = parse_qs(urlparse(self.start("?comments=1")).query)
            self.assertEqual((query["config_id"], "scope" in query), (["cfg_comments"], False))
            self.assertEqual(parse_qs(urlparse(self.start()).query)["config_id"], ["cfg_msg"])

    def test_start_without_the_flag_or_with_the_switch_off_is_the_messenger_login(self):
        self.assertEqual(parse_qs(urlparse(self.start()).query)["scope"], [messenger.SCOPES])
        with override_settings(INQUIRY_COMMENTS_ENABLED=False):
            self.assertEqual(parse_qs(urlparse(self.start("?comments=1")).query)["scope"], [messenger.SCOPES])

    def test_with_the_comments_permissions_the_feed_field_is_subscribed_and_comments_are_ready(self):
        response, connect = self.connect()
        self.assertEqual(connect.status_code, 200, connect.content)
        self.assertIn("feed", self.subscribe_fields().split(","))
        self.assertIn("messages", self.subscribe_fields().split(","))
        self.assertTrue(connect.json()["comments_ready"])
        self.assertTrue(InquiryCommentSettings.objects.get().feed_subscribed)

    def test_without_the_permissions_the_callback_refuses_a_comments_reconnect(self):
        response, connect = self.connect(comment_perms=False)
        self.assertIn("code=permission_missing", response["Location"])

    def test_a_plain_messenger_connect_subscribes_the_messenger_fields_only(self):
        response, connect = self.connect(comment_perms=True, comments_flag=False)
        self.assertEqual(connect.status_code, 200, connect.content)  # the permissions happen to be there
        self.assertTrue(InquiryCommentSettings.objects.get().feed_subscribed)
        InquiryCommentSettings.objects.all().delete()
        InquiryConnection.objects.all().delete()
        self.meta.permissions = self.meta.permissions[:4]
        self.meta.calls.clear()
        response, connect = self.connect(comment_perms=False, comments_flag=False)
        self.assertEqual(connect.status_code, 200, connect.content)
        self.assertNotIn("feed", self.subscribe_fields().split(","))
        self.assertFalse(InquiryCommentSettings.objects.exists())
        self.assertFalse(connect.json()["comments_ready"])

    def test_with_the_comments_switch_off_the_feed_is_never_subscribed(self):
        with override_settings(INQUIRY_COMMENTS_ENABLED=False):
            response, connect = self.connect()
        self.assertEqual(connect.status_code, 200, connect.content)
        self.assertNotIn("feed", self.subscribe_fields().split(","))
        self.assertFalse(connect.json()["comments_ready"])

    def test_disconnecting_forgets_the_subscription(self):
        self.connect()
        self.assertEqual(self.send("delete", self.base.replace("comments", "messenger") + "connection/").status_code, 204)
        self.assertFalse(InquiryCommentSettings.objects.get().feed_subscribed)

    def test_deploy_check_needs_the_messenger_inbox(self):
        from apps.inquiries.checks import check_comments_dependencies
        self.assertEqual(check_comments_dependencies(None), [])
        with override_settings(INQUIRY_MESSENGER_ENABLED=False):
            self.assertEqual([e.id for e in check_comments_dependencies(None)], ["inquiries.E006"])
        with override_settings(INQUIRY_COMMENTS_ENABLED=False, INQUIRY_MESSENGER_ENABLED=False):
            self.assertEqual(check_comments_dependencies(None), [])


@override_settings(RETENTION_ENABLED=True)
class RetentionAndErasureTests(CommentsTestCase):
    def setUp(self):
        super().setUp()
        self.row = self.connect_row()
        self.comment = self.feed_in()

    def test_comments_older_than_180_days_are_deleted_with_their_actions_and_empty_posts(self):
        InquiryCommentAction.objects.create(site=self.site, comment=self.comment, kind="hide", state="sent")
        self.feed_in(comment_id="111000111_340", post_id="111000111_777")
        old = timezone.now() - retention.COMMENT_RETENTION - timedelta(days=1)
        InquiryComment.objects.filter(pk=self.comment.pk).update(created_at=old)
        InquiryPost.objects.filter(external_id=POST).update(created_at=old)
        self.assertEqual(retention.old_comments(timezone.now(), True), 2)  # a dry run counts and changes nothing
        self.assertEqual(InquiryComment.objects.count(), 2)
        retention.old_comments(timezone.now(), False)
        self.assertEqual(list(InquiryComment.objects.values_list("external_id", flat=True)), ["111000111_340"])
        self.assertFalse(InquiryCommentAction.objects.exists())
        self.assertEqual(list(InquiryPost.objects.values_list("external_id", flat=True)), ["111000111_777"])

    def test_the_rule_is_registered_and_waits_for_the_retention_switch(self):
        from apps.common import retention as registry
        self.assertIn("inquiry_comments", registry._rules)
        InquiryComment.objects.update(created_at=timezone.now() - timedelta(days=400))
        with override_settings(RETENTION_ENABLED=False):
            retention.old_comments(timezone.now(), False)
        self.assertEqual(InquiryComment.objects.count(), 1)

    def post_signed(self, value):
        return self.client.post("/v1/webhooks/messenger/data-deletion/", {"signed_request": value}, secure=True)

    def test_data_deletion_erases_the_comments_of_the_users_pages_only(self):
        other = self.other_site()
        self.connect_row(site=other, page=PAGE_B, user="fbuser-2")
        self.feed_in(page=PAGE_B, comment_id="222000222_5", post_id="222000222_1")
        self.assertEqual(self.post_signed(signed_request("fbuser-1")).status_code, 200)
        self.assertFalse(InquiryComment.objects.filter(site=self.site).exists())
        self.assertFalse(InquiryPost.objects.filter(site=self.site).exists())
        self.assertEqual(InquiryComment.objects.filter(site=other).count(), 1)

    def test_site_disposal_deletes_the_comments_and_forgets_the_subscription(self):
        InquiryCommentSettings.objects.create(site=self.site, feed_subscribed=True, page_id=PAGE_A)
        counts = retention.blank_inquiries(Site.objects.filter(pk=self.site.pk))
        self.assertEqual(counts[retention.COMMENTS], 1)
        self.assertFalse(InquiryComment.objects.exists() or InquiryPost.objects.exists())
        self.assertFalse(InquiryCommentSettings.objects.get().feed_subscribed)
        self.assertIn(retention.COMMENTS, retention.blanked())

    def test_disposal_leaves_another_sites_comments(self):
        other = self.other_site()
        self.connect_row(site=other, page=PAGE_B, user="fbuser-2")
        self.feed_in(page=PAGE_B, comment_id="222000222_5", post_id="222000222_1")
        retention.blank_inquiries(Site.objects.filter(pk=self.site.pk))
        self.assertEqual(InquiryComment.objects.get().site, other)

    def test_deauthorize_keeps_the_comments_for_their_own_retention_but_stops_the_subscription(self):
        InquiryCommentSettings.objects.create(site=self.site, feed_subscribed=True, page_id=PAGE_A)
        self.client.post("/v1/webhooks/messenger/deauthorize/", {"signed_request": signed_request("fbuser-1")}, secure=True)
        self.assertEqual(InquiryComment.objects.count(), 1)
        self.assertFalse(InquiryCommentSettings.objects.get().feed_subscribed)


class WhenTheTablesAreMissingTests(CommentsTestCase):
    """The code is released before its migration: nothing may touch the comment tables until it is applied."""

    def test_nothing_reads_or_writes_the_tables(self):
        self.connect_row()
        with mock.patch("apps.inquiries.switch.comment_tables_exist", return_value=False), \
                mock.patch("apps.inquiries.messenger_inbox.comment_tables_exist", return_value=False), \
                mock.patch("apps.inquiries.api_comments.comment_tables_exist", return_value=False), \
                mock.patch("apps.inquiries.retention.comment_tables_exist", return_value=False), \
                mock.patch("apps.inquiries.api_messenger.comment_tables_exist", return_value=False):
            self.assertEqual(self.hook(feed()).status_code, 200)
            self.error(self.get(self.base), 404, "not_found")
            self.assertEqual(retention.old_comments(timezone.now(), False), 0)
            self.assertEqual(self.get(self.base.replace("comments", "messenger") + "connection/").json()["comments_ready"], False)
            messenger_inbox.disconnect(self.site)
        self.assertFalse(InquiryComment.objects.exists())
