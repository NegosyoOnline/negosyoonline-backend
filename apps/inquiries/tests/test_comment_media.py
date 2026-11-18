"""A commenter's photo and a post's text + link (task z8uq9mgkpy): recorded fake Meta answers only."""
import logging
from unittest import mock

from django.core.cache import cache
from django.test import override_settings

from apps.inquiries import comment_media, messenger, messenger_inbox, retention, tasks
from apps.inquiries.models import InquiryComment, InquiryPost
from apps.inquiries.tests.test_comments import COMMENT, COMMENTER, POST, CommentMeta, CommentsTestCase, feed
from apps.inquiries.tests.test_messenger import PAGE_A, PAGE_TOKEN, signed_request
from apps.sites.models import Site

PHOTO = "https://scontent.xx.fbcdn.net/v/t1.0-1/p50x50/secretphoto.jpg?oh=abc&oe=1"
LINK = "https://www.facebook.com/111000111/posts/222"


class MediaMeta(CommentMeta):
    def __init__(self):
        super().__init__()
        self.picture = (200, {"data": {"url": PHOTO, "is_silhouette": False, "width": 50, "height": 50}})
        self.post = (200, {"message": "  Fresh   mangoes, 3 kilos  ", "permalink_url": LINK, "id": POST})

    def __call__(self, method, path, *, params=None, form=None, token=None, timeout=None):
        if method == "GET" and path == f"/{COMMENTER}/picture":
            self.calls.append((method, path, params, form or {}, token, "picture"))
            return self.picture
        if method == "GET" and path == f"/{POST}":
            self.calls.append((method, path, params, form or {}, token, "post"))
            return self.post
        return super().__call__(method, path, params=params, form=form, token=token, timeout=timeout)

    def kind(self, name):
        return [c for c in self.calls if len(c) == 6 and c[5] == name]


class MediaCase(CommentsTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        for task in (tasks.fetch_comment_avatar, tasks.fetch_post_text):  # in-process, as with no REDIS_URL
            patch = mock.patch.object(task, "delay", side_effect=lambda *a, _t=task, **k: _t(*a, **k))
            patch.start()
            self.addCleanup(patch.stop)
        self.meta = MediaMeta()
        patch = mock.patch.object(messenger, "_transport", self.meta)
        patch.start()
        self.addCleanup(patch.stop)

    def arrive(self, **kw):
        self.ready()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.hook(feed(**kw)).status_code, 200)
        return InquiryComment.objects.get(external_id=kw.get("comment_id", COMMENT))


class PhotoTests(MediaCase):
    def test_nothing_is_read_before_the_commit_then_the_photo_is_cached_not_stored(self):
        self.ready()
        response = self.hook(feed())  # inside the test transaction: on_commit callbacks wait
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.meta.kind("picture"), [])
        comment = self.arrive(comment_id="111000111_334")
        (call,) = self.meta.kind("picture")
        self.assertEqual((call[2]["redirect"], call[2]["type"]), ("false", "normal"))
        self.assertEqual(call[4], PAGE_TOKEN)  # the Page token
        self.assertIn("appsecret_proof", call[2])
        self.assertEqual(cache.get(comment_media.avatar_key(comment.pk)), PHOTO)
        for field in InquiryComment._meta.get_fields():  # no table holds the URL
            self.assertNotEqual(getattr(field, "name", ""), "avatar_url")

    def test_the_api_carries_avatar_url_in_the_list_the_detail_and_the_read(self):
        comment = self.arrive()
        listed = self.get(self.base).json()["items"][0]
        self.assertEqual(listed["avatar_url"], PHOTO)
        self.assertEqual(self.get(self.base + f"{comment.ref}/").json()["avatar_url"], PHOTO)

    def test_no_picture_from_meta_means_null_and_the_initials_stay(self):
        for answer in ((200, {"data": {"url": PHOTO, "is_silhouette": True}}), (200, {"data": {}}),
                       (200, {"data": {"url": "https://evil.example.com/x.jpg"}}),
                       (200, {"data": {"url": "http://scontent.xx.fbcdn.net/x.jpg"}})):
            cache.clear()
            InquiryComment.objects.all().delete()
            self.meta.picture = answer
            self.arrive()
            self.assertIsNone(self.get(self.base).json()["items"][0]["avatar_url"])

    def test_a_graph_error_is_silent_logs_codes_only_and_is_tried_again_after_the_retry_mark(self):
        self.meta.picture = (400, {"error": {"code": 100, "error_subcode": 33, "message": f"user {COMMENTER} {PHOTO}"}})
        with self.assertLogs("apps.inquiries", level="WARNING") as logs:
            comment = self.arrive()
        text = "\n".join(logs.output)
        for secret in (COMMENTER, PHOTO, PAGE_TOKEN, "Maria"):
            self.assertNotIn(secret, text)
        self.assertIsNone(cache.get(comment_media.avatar_key(comment.pk)))
        self.assertIsNone(self.get(self.base).json()["items"][0]["avatar_url"])
        self.assertTrue(comment_media.cache.get(comment_media._retry_key(comment.pk)))  # the hour mark is set

    def test_the_pages_own_comment_asks_for_no_photo(self):
        self.arrive(author=PAGE_A, comment_id="111000111_900")
        self.assertEqual(self.meta.kind("picture"), [])

    def test_a_remove_event_deletes_the_cached_photo(self):
        comment = self.arrive()
        self.assertTrue(cache.get(comment_media.avatar_key(comment.pk)))
        self.arrive(verb="remove")
        self.assertIsNone(cache.get(comment_media.avatar_key(comment.pk)))

    def test_a_fetch_that_finishes_after_the_removal_leaves_nothing(self):
        comment = self.arrive()
        cache.clear()
        InquiryComment.objects.filter(pk=comment.pk).update(removed=True)
        comment_media.fetch_avatar(comment.pk)
        self.assertIsNone(cache.get(comment_media.avatar_key(comment.pk)))

    @override_settings(RETENTION_ENABLED=True)
    def test_retention_deletes_the_cached_photo(self):
        from datetime import timedelta
        from django.utils import timezone
        comment = self.arrive()
        InquiryComment.objects.filter(pk=comment.pk).update(created_at=timezone.now() - retention.COMMENT_RETENTION - timedelta(days=1))
        retention.old_comments(timezone.now(), True)
        self.assertTrue(cache.get(comment_media.avatar_key(comment.pk)))  # a dry run changes nothing
        retention.old_comments(timezone.now(), False)
        self.assertIsNone(cache.get(comment_media.avatar_key(comment.pk)))

    def test_deauthorize_deletes_the_cached_photo(self):
        comment = self.arrive()
        self.client.post("/v1/webhooks/messenger/deauthorize/", {"signed_request": signed_request("fbuser-1")}, secure=True)
        self.assertIsNone(cache.get(comment_media.avatar_key(comment.pk)))

    def test_data_deletion_deletes_the_cached_photo(self):
        comment = self.arrive()
        self.client.post("/v1/webhooks/messenger/data-deletion/", {"signed_request": signed_request("fbuser-1")}, secure=True)
        self.assertFalse(InquiryComment.objects.exists())
        self.assertIsNone(cache.get(comment_media.avatar_key(comment.pk)))

    def test_site_disposal_deletes_the_cached_photo(self):
        comment = self.arrive()
        retention.blank_inquiries(Site.objects.filter(pk=self.site.pk))
        self.assertIsNone(cache.get(comment_media.avatar_key(comment.pk)))

    def test_disconnect_deletes_the_cached_photo(self):
        comment = self.arrive()
        messenger_inbox.disconnect(self.site)
        self.assertIsNone(cache.get(comment_media.avatar_key(comment.pk)))


class PostTextTests(MediaCase):
    def test_a_new_post_without_text_is_read_once_and_stored(self):
        self.arrive()
        (call,) = self.meta.kind("post")
        self.assertEqual(call[2]["fields"], "message,permalink_url")
        self.assertEqual(call[4], PAGE_TOKEN)
        post = InquiryPost.objects.get()
        self.assertEqual((post.caption, post.permalink), ("Fresh mangoes, 3 kilos", LINK))
        item = self.get(self.base).json()["items"][0]["post"]
        self.assertEqual((item["caption"], item["permalink"]), ("Fresh mangoes, 3 kilos", LINK))

    def test_a_post_with_text_and_link_from_the_webhook_is_not_read(self):
        self.arrive(post={"message": "Mangoes", "permalink_url": LINK})
        self.assertEqual(self.meta.kind("post"), [])

    def test_the_caption_is_cut_to_140_and_a_link_with_a_query_is_refused(self):
        self.meta.post = (200, {"message": "x" * 300, "permalink_url": LINK + "?access_token=SECRET"})
        self.arrive()
        post = InquiryPost.objects.get()
        self.assertEqual(len(post.caption), 140)
        self.assertEqual(post.permalink, f"https://www.facebook.com/{POST}")  # the id pattern stays

    def test_a_graph_error_does_not_fail_the_webhook_or_log_ids(self):
        self.meta.post = (403, {"error": {"code": 10, "message": f"post {POST} {PAGE_TOKEN}"}})
        with self.assertLogs("apps.inquiries", level="WARNING") as logs:
            comment = self.arrive()
        self.assertIsNotNone(comment)
        text = "\n".join(logs.output)
        self.assertNotIn(POST, text.replace("Comments webhook summary", ""))
        self.assertNotIn(PAGE_TOKEN, text)
        self.assertEqual(InquiryPost.objects.get().caption, "")

    def test_a_second_comment_within_the_hour_does_not_read_again(self):
        self.meta.post = (200, {})  # a photo-only post: nothing to store
        self.arrive()
        self.arrive(comment_id="111000111_335")
        self.assertEqual(len(self.meta.kind("post")), 1)

    def test_no_read_without_a_usable_token(self):
        row = self.ready()
        messenger_inbox.disconnect(self.site)
        post = InquiryPost.objects.create(site=self.site, connection=row, external_id=POST)
        comment_media.fetch_post(post.pk)
        self.assertEqual(self.meta.kind("post"), [])
