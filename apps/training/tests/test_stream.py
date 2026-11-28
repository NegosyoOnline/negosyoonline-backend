"""R2: the Bunny Stream client (HTTP mocked), the per-store library and its command, the owner's
video endpoints, the delete guard, and how a lesson reads. No real Bunny call is made."""
import hashlib
import io
import os
import urllib.error
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.training import stream, video
from apps.training.models import Lesson, StreamLibrary
from apps.training.tests.base import YT, make_course, make_lesson, make_module
from apps.training.tests.stream_fake import KEYS, FakeBunny, set_library

LIB = stream.Library(id=816560, cdn_host="vz-live.b-cdn.net", key="live-local-test-key")


class Response(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ClientTests(SimpleTestCase):
    def call_with(self, body=b"{}", error=None):
        seen = {}

        def fake(request, timeout=None):
            seen["url"], seen["method"] = request.full_url, request.get_method()
            seen["headers"] = {k.lower(): v for k, v in request.header_items()}
            seen["data"] = request.data
            if error is not None:
                raise error
            return Response(body)

        return seen, mock.patch("urllib.request.urlopen", side_effect=fake)

    def test_create_video_posts_to_this_library_with_its_own_key(self):
        seen, patch = self.call_with(b'{"guid": "11111111-2222-3333-4444-555555555555"}')
        with patch:
            guid = stream.create_video(LIB, "Welcome")
        self.assertEqual(guid, "11111111-2222-3333-4444-555555555555")
        self.assertEqual((seen["method"], seen["url"]), ("POST", "https://video.bunnycdn.com/library/816560/videos"))
        self.assertEqual(seen["headers"]["accesskey"], "live-local-test-key")
        self.assertIn(b"Welcome", seen["data"])

    def test_get_video_reads_status_length_and_size(self):
        seen, patch = self.call_with(b'{"status": 4, "length": 61, "storageSize": 9000}')
        with patch:
            self.assertEqual(stream.get_video(LIB, "abc-12345"), {"status": 4, "length": 61, "storage_size": 9000})
        self.assertEqual(seen["url"], "https://video.bunnycdn.com/library/816560/videos/abc-12345")

    def test_delete_and_put_use_the_right_methods(self):
        seen, patch = self.call_with()
        with patch:
            stream.delete_video(LIB, "abc-12345")
            self.assertEqual(seen["method"], "DELETE")
            stream.upload_bytes(LIB, "abc-12345", io.BytesIO(b"xyz"), 3)
        self.assertEqual((seen["method"], seen["headers"]["content-length"]), ("PUT", "3"))
        self.assertEqual(seen["headers"]["accesskey"], "live-local-test-key")

    def test_the_status_numbers_map_to_lesson_states(self):
        got = {n: stream.status_name(n) for n in range(7)}
        self.assertEqual(got, {0: "uploading", 1: "processing", 2: "processing", 3: "processing",
                               4: "ready", 5: "failed", 6: "failed"})

    def test_the_tus_signature_is_sha256_of_library_key_expiry_and_video(self):
        sig = stream.tus_signature(LIB, "vid-12345678", now=1000)
        expires = 1000 + stream.SIGNATURE_TTL
        expected = hashlib.sha256(f"816560live-local-test-key{expires}vid-12345678".encode()).hexdigest()
        self.assertEqual(sig, {
            "endpoint": "https://video.bunnycdn.com/tusupload", "library_id": 816560,
            "video_id": "vid-12345678", "signature": expected, "expires": expires,
        })
        self.assertNotIn("live-local-test-key", repr(sig))

    def test_the_embed_url_carries_the_lessons_own_library(self):
        self.assertEqual(stream.embed_url(816560, "g-1"), "https://iframe.mediadelivery.net/embed/816560/g-1")
        with override_settings(BUNNY_STREAM_EMBED_BASE="http://127.0.0.1:8024/embed"):
            self.assertEqual(stream.embed_url(7, "g-1"), "http://127.0.0.1:8024/embed/7/g-1")

    def test_the_source_download_tries_the_original_then_the_mp4_and_sends_no_key(self):
        urls, headers = [], []

        def fake(request, timeout=None):
            urls.append(request.full_url)
            headers.append({k.lower() for k, _ in request.header_items()})
            if request.full_url.endswith("/original"):
                raise urllib.error.HTTPError(request.full_url, 404, "x", {}, None)
            body = Response(b"mp4-bytes")
            body.headers = {}
            return body

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            source, length = stream.download_source(LIB, "abc-12345")
        self.assertEqual((source.read(), length), (b"mp4-bytes", 9))
        self.assertEqual(urls, ["https://vz-live.b-cdn.net/abc-12345/original", "https://vz-live.b-cdn.net/abc-12345/play_720p.mp4"])
        self.assertTrue(all("accesskey" not in h for h in headers))

    def test_a_failed_call_raises_a_short_error_that_never_holds_the_key(self):
        _, patch = self.call_with(error=urllib.error.HTTPError("https://x/library/1/videos", 401, "no", {}, None))
        with patch, self.assertLogs("apps.training.stream", "WARNING") as logs:
            with self.assertRaises(stream.StreamError) as raised:
                stream.get_video(LIB, "abc-12345")
        text = str(raised.exception) + "".join(logs.output)
        self.assertNotIn("live-local-test-key", text)
        self.assertIn("401", text)
        self.assertIsNone(raised.exception.__cause__)

    def test_an_unexpected_answer_is_an_error_not_a_crash(self):
        _, patch = self.call_with(b"not json")
        with patch:
            with self.assertRaises(stream.StreamError):
                stream.get_video(LIB, "abc-12345")
        _, patch = self.call_with(b'{"guid": "x y"}')
        with patch:
            with self.assertRaises(stream.StreamError):
                stream.create_video(LIB, "t")

    def test_key_names_are_validated_and_read_from_the_environment(self):
        for good in ("LIVE4MORE", "A_1", "X"):
            self.assertTrue(stream.key_ref_ok(good))
        for bad in ("", "live4more", "A-B", "A B", "A" * 41, None, "../X"):
            self.assertFalse(stream.key_ref_ok(bad))
        row = StreamLibrary(library_id=5, cdn_host="h.b-cdn.net", key_ref="NOKEYSET")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BUNNY_STREAM_KEY_NOKEYSET", None)
            with self.assertRaises(stream.NotConfigured):
                stream.library_from_row(row)
            self.assertFalse(stream.key_present("NOKEYSET"))
        with mock.patch.dict(os.environ, {"BUNNY_STREAM_KEY_NOKEYSET": "k"}):
            self.assertEqual(stream.library_from_row(row).key, "k")
        self.assertNotIn("k", repr(stream.Library(1, "h", "secret-value")).replace("Library", "").replace("cdn_host", ""))


@override_settings(MEMBERS_ENABLED=True)
class StreamCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.ben = self.other_client()
        self.ben_site_id = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben's")["id"]
        self.ben_site = Site.objects.get(public_id=self.ben_site_id)
        self.bunny = FakeBunny()
        self.module = make_module(make_course(self.site))
        self.lesson = make_lesson(self.module, "Welcome", video_url="")
        self.addCleanup(cache.clear)

    def url(self, lesson=None, tail="video/", site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/training/lessons/{(lesson or self.lesson).public_id}/{tail}"

    def configure(self):
        set_library(self.site, 111, "ANA")
        set_library(self.ben_site, 222, "BEN")


class CommandTests(StreamCase):
    def run_cmd(self, *args):
        out = io.StringIO()
        call_command("stream_library", *args, stdout=out)
        return out.getvalue()

    def test_set_then_show_prints_everything_except_the_key(self):
        with mock.patch.dict(os.environ, {"BUNNY_STREAM_KEY_LIVE4MORE": "super-secret-local"}):
            out = self.run_cmd("set", "--site", self.site_id, "--library-id", "816560",
                               "--cdn-host", "VZ-BC410DCD-91E.b-cdn.net", "--key-ref", "LIVE4MORE")
            shown = self.run_cmd("show")
        row = StreamLibrary.objects.get()
        self.assertEqual((row.library_id, row.cdn_host, row.key_ref), (816560, "vz-bc410dcd-91e.b-cdn.net", "LIVE4MORE"))
        self.assertIn("key_ref=LIVE4MORE", out)
        self.assertIn("key_variable_present=yes", shown)
        self.assertNotIn("super-secret-local", out + shown)

    def test_the_pull_zone_id_is_set_shown_kept_and_checked(self):
        base = ["set", "--site", self.site_id, "--library-id", "5", "--cdn-host", "h.b-cdn.net", "--key-ref", "ANA"]
        self.run_cmd(*base)
        self.assertIsNone(StreamLibrary.objects.get().pull_zone_id)
        self.assertIn("pull_zone_id=none", self.run_cmd("show"))
        self.run_cmd(*base, "--pull-zone-id", "321")
        self.assertIn("pull_zone_id=321", self.run_cmd("show"))
        self.run_cmd(*base)  # set again without it: the value stays
        self.assertEqual(StreamLibrary.objects.get().pull_zone_id, 321)
        with self.assertRaises(CommandError):
            self.run_cmd(*base, "--pull-zone-id", "0")

    def test_show_says_when_the_variable_is_missing(self):
        os.environ.pop("BUNNY_STREAM_KEY_NOPE", None)
        self.run_cmd("set", "--site", self.site_id, "--library-id", "5", "--cdn-host", "h.b-cdn.net", "--key-ref", "NOPE")
        self.assertIn("key_variable_present=no", self.run_cmd("show", "--site", self.site_id))
        self.assertEqual(self.run_cmd("show", "--site", self.ben_site_id).strip(), "No library is set.")

    def test_the_command_never_takes_the_key_and_refuses_bad_values(self):
        base = ["set", "--site", self.site_id, "--library-id", "5", "--cdn-host", "h.b-cdn.net", "--key-ref", "OK"]
        with self.assertRaises(CommandError):
            self.run_cmd(*base, "--key", "abc")
        for change in (("--key-ref", "lower-case"), ("--cdn-host", "https://h.b-cdn.net/x"), ("--library-id", "0"),
                       ("--site", "site_00000000000000000000")):
            args = base[:]
            args[args.index(change[0]) + 1] = change[1]
            with self.subTest(change=change), self.assertRaises(CommandError):
                self.run_cmd(*args)
        self.assertFalse(StreamLibrary.objects.exists())

    def test_one_library_cannot_belong_to_two_stores(self):
        self.run_cmd("set", "--site", self.site_id, "--library-id", "5", "--cdn-host", "h.b-cdn.net", "--key-ref", "ANA")
        with self.assertRaises(CommandError):
            self.run_cmd("set", "--site", self.ben_site_id, "--library-id", "5", "--cdn-host", "h.b-cdn.net", "--key-ref", "BEN")
        self.run_cmd("set", "--site", self.site_id, "--library-id", "6", "--cdn-host", "h.b-cdn.net", "--key-ref", "ANA")
        self.assertEqual(StreamLibrary.objects.get(site=self.site).library_id, 6)


class UploadTests(StreamCase):
    def test_an_unconfigured_store_gets_503_and_nothing_is_created(self):
        with self.bunny:
            r = self.send("post", self.url(tail="video/upload/"), None)
        self.assertEqual(r.status_code, 503, r.content)
        self.assertEqual(r.json()["error"]["message"], "Video uploads are not available yet.")
        self.assertFalse(self.bunny.videos)

    def test_a_row_whose_key_variable_is_missing_is_also_503_and_other_stores_still_work(self):
        self.configure()
        with self.bunny, mock.patch.dict(os.environ, {}):
            os.environ.pop("BUNNY_STREAM_KEY_ANA")
            self.assertEqual(self.send("post", self.url(tail="video/upload/"), None).status_code, 503)
            ben_lesson = make_lesson(make_module(make_course(self.ben_site)), "B", video_url="")
            r = self.send("post", self.url(ben_lesson, "video/upload/", self.ben_site_id), None, client=self.ben)
        self.assertEqual(r.status_code, 201, r.content)

    def test_the_upload_is_created_in_the_stores_own_library_and_signed(self):
        self.configure()
        with self.bunny:
            r = self.send("post", self.url(tail="video/upload/"), None)
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        guid = body["upload"]["video_id"]
        self.assertEqual(self.bunny.calls, [("create", 111)])  # not Ben's library
        self.assertEqual(set(body["upload"]), {"endpoint", "library_id", "video_id", "signature", "expires"})
        self.assertEqual(body["upload"]["library_id"], 111)
        expected = hashlib.sha256(f"111{KEYS['ANA']}{body['upload']['expires']}{guid}".encode()).hexdigest()
        self.assertEqual(body["upload"]["signature"], expected)
        self.assertEqual(body["video"], {"kind": "stream", "status": "uploading", "embed_url": None, "needs_video": False})
        for secret in (KEYS["ANA"], "ANA", "key_ref"):
            self.assertNotIn(secret, r.content.decode())
        self.lesson.refresh_from_db()
        self.assertEqual((self.lesson.stream_library_id, self.lesson.stream_video_id, self.lesson.stream_status), (111, guid, "uploading"))

    def test_a_failed_create_is_a_clean_error_and_the_lesson_is_untouched(self):
        self.configure()
        self.bunny.fail = {"create"}
        with self.bunny:
            r = self.send("post", self.url(tail="video/upload/"), None)
        self.assertEqual(r.status_code, 502, r.content)
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.stream_video_id, "")

    def test_a_foreign_lesson_or_site_is_not_found_and_calls_nothing(self):
        self.configure()
        with self.bunny:
            foreign = self.send("post", self.url(site_id=self.ben_site_id, tail="video/upload/"), None, client=self.ben)
            other = self.send("post", self.url(self.lesson, "video/upload/", self.ben_site_id), None, client=self.ben)
        self.assertEqual((foreign.status_code, other.status_code), (404, 404))
        self.assertFalse(self.bunny.calls)


class RefreshAndRemoveTests(StreamCase):
    def start(self, lesson=None):
        r = self.send("post", self.url(lesson, "video/upload/"), None)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["upload"]["video_id"]

    def test_refresh_maps_bunny_status_to_ready_processing_and_failed(self):
        self.configure()
        with self.bunny:
            guid = self.start()
            key = (111, guid)
            for code, status in ((1, "processing"), (3, "processing")):
                self.bunny.videos[key]["status_override"] = code
                got = self.get(self.url()).json()["video"]
                self.assertEqual(got["status"], status, code)
            for code in (5, 6):  # a failure is final for that video
                lesson = make_lesson(self.module, f"F{code}", video_url="")
                bad = self.start(lesson)
                self.bunny.videos[(111, bad)]["status_override"] = code
                self.assertEqual(self.get(self.url(lesson)).json()["video"]["status"], "failed", code)
            self.bunny.videos[key]["status_override"] = 4
            self.bunny.videos[key]["storage"] = 4096
            got = self.get(self.url()).json()
        self.assertEqual(got["video"], {
            "kind": "stream", "status": "ready", "needs_video": False,
            "embed_url": f"https://iframe.mediadelivery.net/embed/111/{guid}",
        })
        self.lesson.refresh_from_db()
        self.assertEqual((self.lesson.stream_status, self.lesson.stream_bytes), ("ready", 4096))

    def test_a_ready_video_is_not_asked_about_again(self):
        self.configure()
        with self.bunny:
            guid = self.start()
            self.bunny.videos[(111, guid)]["status_override"] = 4
            self.get(self.url())
            self.bunny.calls.clear()
            self.get(self.url())
        self.assertEqual(self.bunny.calls, [])

    def test_refresh_when_bunny_is_down_is_502_and_changes_nothing(self):
        self.configure()
        with self.bunny:
            self.start()
            self.bunny.fail = {"check"}
            r = self.get(self.url())
        self.assertEqual(r.status_code, 502)
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.stream_status, "uploading")

    def test_delete_removes_the_video_through_the_guard_with_its_librarys_key(self):
        self.configure()
        with self.bunny:
            guid = self.start()
            with self.captureOnCommitCallbacks(execute=True):
                r = self.send("delete", self.url(), None)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["video"]["kind"], "none")
        self.assertEqual(self.bunny.deleted, [(111, guid)])
        self.lesson.refresh_from_db()
        self.assertEqual((self.lesson.stream_library_id, self.lesson.stream_video_id, self.lesson.stream_status), (None, "", ""))

    def test_a_video_another_lesson_still_names_is_not_deleted(self):
        self.configure()
        with self.bunny:
            guid = self.start()
            twin = make_lesson(self.module, "Twin", video_url="", stream_library_id=111, stream_video_id=guid, stream_status="uploading")
            with self.captureOnCommitCallbacks(execute=True):
                self.send("delete", self.url(), None)
            self.assertEqual(self.bunny.deleted, [])
            with self.captureOnCommitCallbacks(execute=True):
                self.send("delete", self.url(twin), None)
        self.assertEqual(self.bunny.deleted, [(111, guid)])

    def test_deleting_the_lesson_module_or_course_discards_its_video(self):
        self.configure()
        with self.bunny:
            guids = []
            for n in range(3):
                module = make_module(make_course(self.site, f"C{n}"))
                lesson = make_lesson(module, "L", video_url="")
                guids.append(self.start(lesson))
            lessons = list(Lesson.objects.filter(stream_video_id__in=guids).order_by("pk"))
            with self.captureOnCommitCallbacks(execute=True):
                self.send("delete", f"/v1/sites/{self.site_id}/training/lessons/{lessons[0].public_id}/", None)
                self.send("delete", f"/v1/sites/{self.site_id}/training/modules/{lessons[1].module.public_id}/", None)
                self.send("delete", f"/v1/sites/{self.site_id}/training/courses/{lessons[2].module.course.public_id}/", None)
        self.assertEqual(sorted(g for _, g in self.bunny.deleted), sorted(guids))

    def test_the_guard_uses_the_key_of_the_library_the_video_lives_in(self):
        self.configure()
        with self.bunny:
            ben_guid = self.bunny.create_video(stream.library_for_site(self.ben_site), "x")
            video.discard_if_unreferenced(222, ben_guid)
            video.discard_if_unreferenced(111, "unknown-guid-1")  # not stored: no crash
        self.assertEqual(self.bunny.deleted, [(222, ben_guid), (111, "unknown-guid-1")])

    def test_the_guard_never_raises_and_keeps_the_video_when_unsure(self):
        self.configure()
        with self.bunny:
            guid = self.bunny.create_video(stream.library_for_site(self.site), "x")
            self.bunny.fail = {"delete"}
            video.discard_if_unreferenced(111, guid)  # no raise
            with mock.patch.dict(os.environ, {}):
                os.environ.pop("BUNNY_STREAM_KEY_ANA")
                video.discard_if_unreferenced(111, guid)  # key missing: nothing sent
        self.assertEqual(self.bunny.deleted, [])


class ReplaceTests(StreamCase):
    def test_a_legacy_lesson_keeps_playing_until_the_replacement_is_ready(self):
        self.configure()
        lesson = make_lesson(self.module, "Old", video_url=YT)
        with self.bunny:
            r = self.send("post", self.url(lesson, "video/upload/"), None).json()
            guid = r["upload"]["video_id"]
            self.assertEqual(r["video"], {"kind": "legacy", "status": "uploading", "embed_url": None, "needs_video": False})
            lesson.refresh_from_db()
            self.assertEqual((lesson.video_url, lesson.stream_video_id, lesson.stream_pending_video_id), (YT, "", guid))
            self.bunny.videos[(111, guid)]["status_override"] = 4
            done = self.get(self.url(lesson)).json()["video"]
        self.assertEqual(done["kind"], "stream")
        self.assertEqual(done["embed_url"], f"https://iframe.mediadelivery.net/embed/111/{guid}")
        lesson.refresh_from_db()
        self.assertEqual((lesson.video_url, lesson.stream_video_id, lesson.stream_pending_video_id), (YT, guid, ""))  # the link is left alone

    def test_a_ready_video_is_replaced_and_the_previous_one_discarded_only_when_the_new_is_ready(self):
        self.configure()
        with self.bunny:
            old = self.send("post", self.url(tail="video/upload/"), None).json()["upload"]["video_id"]
            self.bunny.videos[(111, old)]["status_override"] = 4
            self.get(self.url())
            new = self.send("post", self.url(tail="video/upload/"), None).json()["upload"]["video_id"]
            self.assertNotEqual(old, new)
            read = self.get(self.url()).json()["video"]  # new one still uploading
            self.assertEqual(read["status"], "uploading")
            self.assertTrue(read["embed_url"].endswith(old))  # the old one still plays
            self.assertEqual(self.bunny.deleted, [])
            self.bunny.videos[(111, new)]["status_override"] = 4
            with self.captureOnCommitCallbacks(execute=True):
                self.get(self.url())
        self.assertEqual(self.bunny.deleted, [(111, old)])
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.stream_video_id, new)

    def test_a_failed_replacement_keeps_the_playing_video(self):
        self.configure()
        with self.bunny:
            old = self.send("post", self.url(tail="video/upload/"), None).json()["upload"]["video_id"]
            self.bunny.videos[(111, old)]["status_override"] = 4
            self.get(self.url())
            new = self.send("post", self.url(tail="video/upload/"), None).json()["upload"]["video_id"]
            self.bunny.videos[(111, new)]["status_override"] = 5
            got = self.get(self.url()).json()["video"]
        self.assertEqual(got["status"], "failed")
        self.assertTrue(got["embed_url"].endswith(old))
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.stream_video_id, old)


class ReadTests(StreamCase):
    def test_owner_lessons_carry_the_video_block_for_stream_legacy_and_none(self):
        stream_lesson = make_lesson(self.module, "S", video_url="", stream_library_id=111, stream_video_id="g-12345678", stream_status="ready")
        legacy = make_lesson(self.module, "L", video_url=YT)
        none = make_lesson(self.module, "N", video_url="", needs_video=True)
        tree = self.get(f"/v1/sites/{self.site_id}/training/courses/{self.module.course.public_id}/").json()
        by_title = {x["title"]: x["video"] for x in tree["modules"][0]["lessons"]}
        self.assertEqual(by_title["S"], {"kind": "stream", "status": "ready", "needs_video": False,
                                         "embed_url": "https://iframe.mediadelivery.net/embed/111/g-12345678"})
        self.assertEqual(by_title["L"], {"kind": "legacy", "status": "ready", "needs_video": True, "embed_url": None})
        self.assertEqual(by_title["N"], {"kind": "none", "status": "", "needs_video": True, "embed_url": None})
        for lesson in (stream_lesson, legacy, none):
            self.assertNotIn("key", str(self.get(self.url(lesson)).content).lower().replace("keys", ""))
