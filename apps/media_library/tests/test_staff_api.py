"""The HQ routes under /staff/v1/media/: staff only, validated, files checked, no admin."""
from unittest import mock

from django.conf import settings

from apps.media_library import services, staff, store
from apps.media_library.models import MediaAsset
from apps.media_library.tests.base import MP4, FakeStore, make_asset, png_file, video_file
from apps.operators import staff_views
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.base import HQ
from apps.operators.tests.test_staff_routes import STAFF, StaffTestCase, on_staff

BASE = "/staff/v1/media/"


@on_staff
class StaffMediaTestCase(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.identity = self.enrolled()

    def api(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def form(self, path, data):
        return self.client.post(BASE + path, data, secure=True, HTTP_HOST=HQ, HTTP_ORIGIN=STAFF)

    def upload(self, **extra):
        data = {"title": "Barley promo", "asset_type": "image", "file": png_file(), **extra}
        return self.form("upload/", {k: v for k, v in data.items() if v is not None})


class AccessTests(StaffMediaTestCase):
    def test_the_routes_are_in_the_table_and_are_posts(self):
        self.assertEqual(len(staff.ROUTES), 12)
        for path in staff.ROUTES:
            self.assertTrue(path.startswith("media/"))
            self.assertEqual(staff_views.ROUTES[path][0], "POST")

    def test_another_origin_is_refused(self):
        for path in staff.ROUTES:
            response = self.call("POST", "/staff/v1/" + path, {}, origin="https://evil.example")
            self.assertError(response, 403, "origin_not_allowed")

    def test_no_session_and_a_session_before_the_second_step_are_refused(self):
        with FakeStore():
            for path in staff.ROUTES:
                self.assertError(self.call("POST", "/staff/v1/" + path, {"id": "x"}), 401, "unauthenticated")
            self.assertEqual(self.upload().status_code, 401)
            self.staff_email_step()
            for path in staff.ROUTES:
                self.assertError(self.call("POST", "/staff/v1/" + path, {"id": "x"}), 401, "second_step_required")
            self.assertEqual(self.upload().status_code, 401)
        self.assertFalse(MediaAsset.objects.exists())
        self.assertFalse(OperatorAuditEntry.objects.filter(action="media_library_authored").exists())

    def test_a_member_or_subscriber_surface_cannot_write_and_there_is_no_django_admin(self):
        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)
        self.assertFalse(hasattr(services, "admin"))
        for path in ("/v1/media/upload/", "/storefront/v1/member/media/upload/"):
            response = self.client.post(path, {"title": "x"}, secure=True)
            self.assertEqual(response.status_code, 404, path)
        self.assertFalse(MediaAsset.objects.exists())


class UploadTests(StaffMediaTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()

    def test_an_image_is_stored_as_an_unpublished_draft(self):
        with FakeStore() as fake:
            response = self.upload(category="ad", product_category="drinks", crop_ratio="1:1", caption="Hi",
                                   width="1080", height="1080")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(
            (body["title"], body["asset_type"], body["category"], body["product_category"], body["crop_ratio"]),
            ("Barley promo", "image", "ad", "drinks", "1:1"),
        )
        self.assertEqual((body["published"], body["published_at"], body["active"], body["order"]), (False, None, True, 1))
        self.assertEqual((body["width"], body["height"], body["file_size"]), (1080, 1080, 40))
        self.assertTrue(body["file_url"].startswith("https://media.example.test/media-library/"))
        self.assertEqual(body["thumb_url"], body["file_url"])  # no thumb: the image stands in
        self.assertEqual(len(fake.files), 1)
        entry = OperatorAuditEntry.objects.get(action="media_library_authored")
        self.assertEqual((entry.target_id, entry.detail), (body["id"], {"op": "upload"}))
        self.assertNotIn("Barley", str(entry.detail))

    def test_a_video_is_created_without_its_file_and_needs_a_poster(self):
        with FakeStore() as fake:
            no_poster = self.upload(asset_type="video", file=None)
            self.assertEqual(no_poster.status_code, 400)
            self.assertIn("poster", no_poster.json()["error"]["fields"])
            with_file = self.upload(asset_type="video", file=video_file(), poster=png_file())
            self.assertEqual(with_file.status_code, 400)  # a video never passes through this server
            self.assertIn("file", with_file.json()["error"]["fields"])
            self.assertEqual(fake.files, {})
            ok = self.upload(asset_type="video", file=None, poster=png_file(), thumb=png_file(), duration_seconds="30")
            self.assertEqual(ok.status_code, 200, ok.content)
            self.assertEqual(len(fake.files), 2)
        body = ok.json()
        self.assertEqual((body["asset_type"], body["duration_seconds"], body["file_size"]), ("video", 30, 0))
        self.assertFalse(body["file_url"])
        self.assertNotEqual(body["thumb_url"], body["poster_url"])

    def test_the_store_not_configured_is_503_and_nothing_is_written(self):
        response = self.upload()
        self.assertEqual((response.status_code, response.json()["error"]["code"]),
                         (503, "image_store_not_configured"))
        self.assertFalse(MediaAsset.objects.exists())

    def test_file_type_limits(self):
        with FakeStore() as fake:
            cases = (
                dict(file=png_file(b"not an image", "text/plain", "x.txt")),
                dict(file=png_file(b"GIF89a" + b"0" * 20, "image/gif", "x.gif")),
                dict(file=png_file(b"\x89PNG\r\n\x1a\n" + b"0", "text/html", "x.png")),  # declared type wrong
                dict(file=video_file(), asset_type="image"),  # a video sent as an image
                dict(asset_type="video", file=video_file(), poster=png_file()),  # no video file goes here
                dict(asset_type="video", file=video_file(), poster=video_file()),  # a video as a poster
                dict(file=None),
            )
            for case in cases:
                data = {"title": "T", "asset_type": "image", "file": png_file(), **case}
                data = {k: v for k, v in data.items() if v is not None}
                response = self.form("upload/", data)
                self.assertEqual(response.status_code, 400, (case, response.content))
            self.assertEqual(fake.files, {})
        self.assertFalse(MediaAsset.objects.exists())

    def test_file_size_limits(self):
        big_image = png_file(b"\x89PNG\r\n\x1a\n" + b"0" * store.IMAGE_MAX_BYTES)
        with FakeStore() as fake:
            response = self.upload(file=big_image)
            self.assertEqual(response.status_code, 400)
            self.assertIn("10 MB", response.json()["error"]["fields"]["file"][0])
            edge = png_file(b"\x89PNG\r\n\x1a\n" + b"0" * (store.IMAGE_MAX_BYTES - 8))
            self.assertEqual(self.upload(file=edge).status_code, 200)
            self.assertEqual(len(fake.files), 1)

    def test_bad_metadata_is_refused_and_a_refused_upload_stores_nothing(self):
        with FakeStore() as fake:
            for extra in ({"title": ""}, {"title": "x" * 161}, {"category": "spons"}, {"product_category": "nope"},
                          {"crop_ratio": "3:2"}, {"asset_type": "audio"}, {"width": "-1"}, {"active": "maybe"}):
                self.assertEqual(self.upload(**extra).status_code, 400, extra)
            self.assertEqual(fake.files, {})

    def test_a_store_failure_is_502_and_cleans_up_what_was_stored(self):
        with FakeStore() as fake:
            real = fake._save
            calls = []

            def flaky(content, content_type):
                calls.append(1)
                if len(calls) == 2:
                    raise store.StoreError("down")
                return real(content, content_type)

            with mock.patch("apps.media_library.store.save", side_effect=flaky):
                response = self.upload(asset_type="video", file=None, poster=png_file(), thumb=png_file())
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (502, "image_not_saved"))
            self.assertEqual(fake.files, {})
            self.assertEqual(len(fake.deleted), 1)
        self.assertFalse(MediaAsset.objects.exists())


class ManageTests(StaffMediaTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()
        self.a = make_asset("A", order=1, published_at=None)
        self.b = make_asset("B", order=2, thumb_key="media-library/t.png", poster_key="media-library/p.png")

    def test_list_shows_drafts_and_inactive_assets_for_staff(self):
        make_asset("C", active=False, order=3)
        make_asset("Other", template_key="basic")
        body = self.api("list/").json()
        self.assertEqual([x["title"] for x in body["assets"]], ["A", "B", "C"])
        self.assertEqual([x["published"] for x in body["assets"]], [False, True, True])
        self.assertEqual(self.api("get/", {"id": self.a.public_id}).json()["title"], "A")

    def test_another_templates_asset_is_never_found_or_changed(self):
        other = make_asset("Other", template_key="basic")
        for path, data in (("get/", {}), ("update/", {"title": "x"}), ("delete/", {}), ("publish/", {}),
                           ("unpublish/", {}), ("file/clear/", {})):
            self.assertEqual(self.api(path, {"id": other.public_id, **data}).status_code, 404, path)
        self.assertEqual(self.api("get/", {"id": "mda_missing"}).status_code, 404)
        self.assertEqual(self.api("get/", {}).status_code, 400)
        self.assertEqual(MediaAsset.objects.get(pk=other.pk).title, "Other")

    def test_update_changes_the_labels_and_flags(self):
        response = self.api("update/", {
            "id": self.a.public_id, "title": " New ", "caption": "Cap", "category": "page",
            "product_category": "beauty", "crop_ratio": "9:16", "width": 5, "order": 9, "active": False,
        })
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["title"], body["category"], body["product_category"], body["crop_ratio"]),
                         ("New", "page", "beauty", "9:16"))
        self.assertEqual((body["order"], body["active"], body["width"]), (9, False, 5))
        cleared = self.api("update/", {"id": self.a.public_id, "product_category": ""}).json()
        self.assertIsNone(cleared["product_category"])
        for bad in ({"title": ""}, {"category": "x"}, {"order": 0}, {"active": "yes"}, {"width": -1}):
            self.assertEqual(self.api("update/", {"id": self.a.public_id, **bad}).status_code, 400, bad)

    def test_publish_and_unpublish(self):
        published = self.api("publish/", {"id": self.a.public_id}).json()
        self.assertTrue(published["published"])
        stamp = published["published_at"]
        self.assertEqual(self.api("publish/", {"id": self.a.public_id}).json()["published_at"], stamp)
        later = self.api("publish/", {"id": self.a.public_id, "published_at": "2026-12-01T09:00:00+08:00"}).json()
        self.assertEqual(MediaAsset.objects.get(pk=self.a.pk).published_at.isoformat(), "2026-12-01T01:00:00+00:00")
        self.assertIn("2026-12-01", later["published_at"])
        for bad in ("tomorrow", "2026-12-01T09:00:00", 5):
            self.assertEqual(self.api("publish/", {"id": self.a.public_id, "published_at": bad}).status_code, 400)
        gone = self.api("unpublish/", {"id": self.a.public_id}).json()
        self.assertEqual((gone["published"], gone["published_at"]), (False, None))

    def test_reorder_needs_every_asset_once(self):
        ok = self.api("reorder/", {"ids": [self.b.public_id, self.a.public_id]})
        self.assertEqual([x["title"] for x in ok.json()["assets"]], ["B", "A"])
        self.assertEqual([x["order"] for x in ok.json()["assets"]], [1, 2])
        for ids in ([self.a.public_id], [self.a.public_id, self.a.public_id], "x", [self.a.public_id, "mda_none"]):
            self.assertEqual(self.api("reorder/", {"ids": ids}).status_code, 400)
        self.assertEqual(MediaAsset.objects.get(pk=self.b.pk).order, 1)

    def test_delete_removes_the_row_and_its_files(self):
        with FakeStore() as fake:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.api("delete/", {"id": self.b.public_id})
        self.assertEqual(response.json(), {"status": "deleted"})
        self.assertFalse(MediaAsset.objects.filter(pk=self.b.pk).exists())
        self.assertEqual(sorted(fake.deleted), sorted([self.b.file_key, "media-library/t.png", "media-library/p.png"]))

    def test_every_write_is_audited_without_values(self):
        self.api("update/", {"id": self.a.public_id, "title": "Secret title"})
        self.api("publish/", {"id": self.a.public_id})
        ops = [e.detail["op"] for e in OperatorAuditEntry.objects.filter(action="media_library_authored")]
        self.assertEqual(ops, ["update", "publish"])
        self.assertNotIn("Secret", str(list(OperatorAuditEntry.objects.values_list("detail", flat=True))))


class FileTests(StaffMediaTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()
        self.image = make_asset("Img", file_key="media-library/old.png", thumb_key="media-library/oldthumb.png")
        self.video = make_asset("Vid", asset_type="video", file_key="media-library/old.mp4",
                                poster_key="media-library/oldposter.png")

    def test_a_file_is_replaced_and_the_old_one_goes_after_commit(self):
        with FakeStore() as fake:
            with self.captureOnCommitCallbacks(execute=True):
                r = self.form("file/set/", {"id": self.image.public_id, "kind": "file", "file": png_file(b"\x89PNG\r\n\x1a\n" + b"1" * 100)})
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual(r.json()["file_size"], 108)
            self.assertIn("media-library/old.png", fake.deleted)
            v = self.form("file/set/", {"id": self.video.public_id, "kind": "file", "file": video_file()})
            self.assertEqual(v.status_code, 400)  # a video file is attached, not posted here
            with self.captureOnCommitCallbacks(execute=True):
                p = self.form("file/set/", {"id": self.video.public_id, "kind": "poster", "file": png_file()})
            self.assertEqual(p.status_code, 200)
            self.assertIn("media-library/oldposter.png", fake.deleted)

    def test_wrong_kinds_and_files_are_refused_and_change_nothing(self):
        with FakeStore() as fake:
            cases = (
                (self.image, "poster", png_file()),                 # an image has no poster
                (self.image, "other", png_file()),
                (self.image, "file", video_file()),                 # type must match the asset
                (self.video, "file", png_file()),
                (self.image, "thumb", png_file(b"junk", "text/plain", "j.txt")),
            )
            for asset, kind, upload in cases:
                response = self.form("file/set/", {"id": asset.public_id, "kind": kind, "file": upload})
                self.assertEqual(response.status_code, 400, (kind, response.content))
            self.assertEqual(self.form("file/set/", {"id": self.image.public_id, "kind": "file"}).status_code, 400)
            self.assertEqual(fake.files, {})
        self.assertEqual(MediaAsset.objects.get(pk=self.image.pk).file_key, "media-library/old.png")

    def test_the_store_not_configured_is_503(self):
        r = self.form("file/set/", {"id": self.image.public_id, "kind": "file", "file": png_file()})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (503, "image_store_not_configured"))

    def test_the_thumb_can_be_cleared_and_falls_back(self):
        with FakeStore() as fake:
            with self.captureOnCommitCallbacks(execute=True):
                r = self.api("file/clear/", {"id": self.image.public_id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["thumb_url"], r.json()["file_url"])
        self.assertIn("media-library/oldthumb.png", fake.deleted)
