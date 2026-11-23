"""The Subscriber's media library editor: the gates, validation, publish rules, the image upload,
and one owner per site. The video token and attach routes are in test_video_upload.py."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from apps.accounts.tests.base import ORIGIN
from apps.catalog.tests.base import image_file
from apps.media_library.models import MediaAsset
from apps.sites.models import Site
from apps.media_library.tests.base import (
    MB, MISSING_ASSET, MISSING_SITE, NOT_FOUND, MediaOwnerCase, make_asset, png_file,
)

OUT_KEYS = {
    "id", "title", "asset_type", "category", "category_label", "product_category",
    "product_category_label", "crop_ratio", "caption", "file_url", "thumb_url", "poster_url",
    "width", "height", "file_size", "duration_seconds", "published_at", "is_new", "active", "order",
    "published", "shared", "copied_from", "created_at", "updated_at",
}


class GateTests(MediaOwnerCase):
    def test_every_route_is_404_while_the_switch_is_off(self):
        asset = make_asset(self.site)
        with override_settings(MEMBERS_ENABLED=False):
            for method, tail, body in (
                ("get", "", None), ("post", "", {"title": "x", "asset_type": "image"}),
                ("post", "reorder/", {"ids": []}), ("patch", f"{asset.public_id}/", {"title": "x"}),
                ("delete", f"{asset.public_id}/", None), ("post", f"{asset.public_id}/publish/", {}),
                ("post", f"{asset.public_id}/unpublish/", None), ("delete", f"{asset.public_id}/image/", None),
                ("post", f"{asset.public_id}/video/token/", None),
                ("post", f"{asset.public_id}/video/attach/", {"url": "https://x.test/a.mp4"}),
            ):
                with self.subTest(method=method, tail=tail):
                    self.error(self.req(method, self.url(tail), body), 404, "not_found")

    def test_signed_out_is_401(self):
        self.client.cookies.clear()
        self.assertEqual(self.get(self.url()).status_code, 401)
        self.assertEqual(self.send("post", self.url(), {"title": "x", "asset_type": "image"}).status_code, 401)

    def test_a_site_that_is_not_santa_is_404(self):
        Site.objects.filter(pk=self.site.pk).update(template="other")
        self.error(self.get(self.url()), 404, "not_found")

    def test_every_answer_is_not_cached(self):
        self.assertEqual(self.get(self.url())["Cache-Control"], "no-store")
        made = self.send("post", self.url(), {"title": "x", "asset_type": "image"})
        self.assertEqual(made["Cache-Control"], "no-store")


class CrudTests(MediaOwnerCase):
    def test_create_edit_list_and_delete(self):
        made = self.send("post", self.url(), {
            "title": "  Barley promo ", "asset_type": "image", "category": "ad", "product_category": "barley",
            "crop_ratio": "1:1", "caption": " Buy now ", "width": 1080, "height": 1080,
        })
        self.assertEqual(made.status_code, 201, made.content)
        asset = made.json()
        self.assertEqual(set(asset), OUT_KEYS)
        self.assertTrue(asset["id"].startswith("mda_"))
        self.assertEqual(
            (asset["title"], asset["category"], asset["category_label"], asset["product_category"],
             asset["crop_ratio"], asset["caption"], asset["order"], asset["active"], asset["published"],
             asset["file_url"]),
            ("Barley promo", "ad", "Ad Creative", "barley", "1:1", "Buy now", 1, True, False, None),
        )
        self.assertEqual(MediaAsset.objects.get().site, self.site)
        patched = self.send("patch", self.url(f"{asset['id']}/"), {"title": "Renamed", "active": False, "product_category": None})
        self.assertEqual((patched.status_code, patched.json()["title"], patched.json()["active"]), (200, "Renamed", False))
        self.assertEqual((patched.json()["product_category"], patched.json()["caption"]), (None, "Buy now"))
        self.assertEqual([a["id"] for a in self.get(self.url()).json()["assets"]], [asset["id"]])
        self.assertEqual(self.send("delete", self.url(f"{asset['id']}/")).json(), {"status": "deleted"})
        self.assertFalse(MediaAsset.objects.exists())

    def test_the_list_holds_drafts_and_switched_off_assets_in_order(self):
        make_asset(self.site, "A", order=2, active=False)
        make_asset(self.site, "B", order=1, published_at=None)
        self.assertEqual([a["title"] for a in self.get(self.url()).json()["assets"]], ["B", "A"])

    def test_new_assets_go_to_the_end_of_this_sites_list_only(self):
        make_asset(self.ben_site, "Bens", order=7)
        for title in ("one", "two"):
            self.send("post", self.url(), {"title": title, "asset_type": "video"})
        self.assertEqual(list(MediaAsset.objects.filter(site=self.site).values_list("title", "order")), [("one", 1), ("two", 2)])

    def test_bad_values_unknown_keys_and_nulls_are_refused(self):
        asset = make_asset(self.site)
        cases = (
            ("post", "", {"title": "", "asset_type": "image"}, "title"),
            ("post", "", {"title": "x" * 161, "asset_type": "image"}, "title"),
            ("post", "", {"title": "x", "asset_type": "audio"}, "asset_type"),
            ("post", "", {"title": "x", "asset_type": "image", "category": "spons"}, "category"),
            ("post", "", {"title": "x", "asset_type": "image", "crop_ratio": "3:2"}, "crop_ratio"),
            ("post", "", {"title": "x", "asset_type": "image", "product_category": "x"}, "product_category"),
            ("patch", f"{asset.public_id}/", {"title": None}, "title"),
            ("patch", f"{asset.public_id}/", {"order": 0}, "order"),
            ("patch", f"{asset.public_id}/", {"caption": "x" * 5001}, "caption"),
        )
        for method, tail, body, name in cases:
            with self.subTest(body=body):
                response = self.send(method, self.url(tail), body)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(name, response.json()["error"]["fields"])
        for body in ({"title": "x", "asset_type": "image", "site": "other"}, {"title": "x", "asset_type": "image", "file_key": "k"},
                     {"title": "x"}, {}):
            self.assertEqual(self.send("post", self.url(), body).status_code, 400)
        for body in ({"asset_type": "video"}, {"template_key": "x"}, {"published_at": "2026-01-01T00:00:00+00:00"}):
            self.assertEqual(self.send("patch", self.url(f"{asset.public_id}/"), body).status_code, 400, body)
        self.assertEqual(MediaAsset.objects.count(), 1)

    def test_deleting_an_asset_removes_all_its_files_after_commit(self):
        asset = make_asset(self.site, thumb_key="media-library/t.png", poster_key="media-library/p.png")
        with self.captureOnCommitCallbacks(execute=True):
            self.send("delete", self.url(f"{asset.public_id}/"))
        self.assertCountEqual(self.store.deleted, [asset.file_key, "media-library/t.png", "media-library/p.png"])

    def test_ordering(self):
        a, b, c = (make_asset(self.site, t, order=n) for n, t in enumerate("ABC", start=1))
        response = self.send("post", self.url("reorder/"), {"ids": [c.public_id, a.public_id, b.public_id]})
        self.assertEqual([x["title"] for x in response.json()["assets"]], ["C", "A", "B"])
        self.assertEqual([x["order"] for x in response.json()["assets"]], [1, 2, 3])

    def test_a_reorder_that_misses_repeats_or_adds_an_item_changes_nothing(self):
        a, b = make_asset(self.site, "A", order=1), make_asset(self.site, "B", order=2)
        bens = make_asset(self.ben_site, "Bens")
        for ids in ([a.public_id], [a.public_id, a.public_id], [a.public_id, b.public_id, bens.public_id], []):
            with self.subTest(ids=ids):
                response = self.send("post", self.url("reorder/"), {"ids": ids})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("ids", response.json()["error"]["fields"])
        self.assertEqual([MediaAsset.objects.get(pk=x.pk).order for x in (a, b)], [1, 2])


class PublishTests(MediaOwnerCase):
    def test_an_image_is_published_only_once_it_has_its_file(self):
        asset = self.send("post", self.url(), {"title": "Promo", "asset_type": "image"}).json()
        refused = self.send("post", self.url(f"{asset['id']}/publish/"), {})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("file", refused.json()["error"]["fields"])
        self.assertEqual(self.upload(f"{asset['id']}/image/").status_code, 200)
        published = self.send("post", self.url(f"{asset['id']}/publish/"), {})
        self.assertEqual(published.status_code, 200, published.content)
        self.assertEqual((published.json()["published"], published.json()["is_new"]), (True, True))
        first = published.json()["published_at"]
        again = self.send("post", self.url(f"{asset['id']}/publish/"), {}).json()
        self.assertEqual(again["published_at"], first)  # publishing again keeps its time
        gone = self.send("post", self.url(f"{asset['id']}/unpublish/")).json()
        self.assertEqual((gone["published"], gone["published_at"]), (False, None))

    def test_a_video_needs_its_attached_file_and_a_poster(self):
        video = make_asset(self.site, "Clip", asset_type="video", file_key="", published_at=None)
        refused = self.send("post", self.url(f"{video.public_id}/publish/"), {})
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(set(refused.json()["error"]["fields"]), {"file", "poster"})
        MediaAsset.objects.filter(pk=video.pk).update(file_key=f"media-library/{self.site_id}/{video.public_id}/a.mp4")
        refused = self.send("post", self.url(f"{video.public_id}/publish/"), {})
        self.assertEqual(set(refused.json()["error"]["fields"]), {"poster"})
        self.assertEqual(self.upload(f"{video.public_id}/image/", kind="poster").status_code, 200)
        self.assertEqual(self.send("post", self.url(f"{video.public_id}/publish/"), {}).status_code, 200)

    def test_a_time_to_publish_at_must_carry_a_time_zone(self):
        asset = make_asset(self.site, published_at=None)
        later = (timezone.now() + timedelta(days=2)).replace(microsecond=0)
        ok = self.send("post", self.url(f"{asset.public_id}/publish/"), {"published_at": later.isoformat()})
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(MediaAsset.objects.get(pk=asset.pk).published_at, later)
        naive = self.send("post", self.url(f"{asset.public_id}/publish/"), {"published_at": "2026-12-01T10:00:00"})
        self.assertEqual(naive.status_code, 400, naive.content)
        self.assertEqual(self.send("post", self.url(f"{asset.public_id}/publish/"), {"x": 1}).status_code, 400)


class ImageTests(MediaOwnerCase):
    def test_an_image_is_stored_under_the_sites_and_assets_prefix_replaced_and_the_thumbnail_cleared(self):
        asset = make_asset(self.site, file_key="", published_at=None)
        prefix = f"media-library/{self.site_id}/{asset.public_id}/"
        response = self.upload(f"{asset.public_id}/image/")
        self.assertEqual(response.status_code, 200, response.content)
        first = MediaAsset.objects.get(pk=asset.pk).file_key
        self.assertTrue(first.startswith(prefix), first)
        self.assertEqual(response.json()["file_url"], f"https://media.example.test/{first}")
        self.assertGreater(response.json()["file_size"], 0)
        with self.captureOnCommitCallbacks(execute=True):
            self.upload(f"{asset.public_id}/image/")
        self.assertNotEqual(MediaAsset.objects.get(pk=asset.pk).file_key, first)
        self.assertIn(first, self.store.deleted)
        thumb = self.upload(f"{asset.public_id}/image/", kind="thumb")
        self.assertEqual(thumb.status_code, 200)
        self.assertTrue(MediaAsset.objects.get(pk=asset.pk).thumb_key.startswith(prefix))
        with self.captureOnCommitCallbacks(execute=True):
            cleared = self.send("delete", self.url(f"{asset.public_id}/image/"))
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertEqual(MediaAsset.objects.get(pk=asset.pk).thumb_key, "")

    def test_the_kind_must_fit_the_asset(self):
        image = make_asset(self.site, published_at=None)
        video = make_asset(self.site, "Clip", asset_type="video", file_key="", published_at=None)
        for tail, kind in ((image.public_id, "poster"), (video.public_id, "file"), (image.public_id, "other")):
            response = self.upload(f"{tail}/image/", kind=kind)
            self.assertEqual(response.status_code, 400, (tail, kind))
            self.assertIn("kind", response.json()["error"]["fields"])
        self.assertEqual(self.store.files, {})

    def test_a_wrong_type_a_missing_file_and_a_too_large_image_store_nothing(self):
        asset = make_asset(self.site, published_at=None)
        bad = self.upload(f"{asset.public_id}/image/", file=image_file(b"plain text", "text/plain", "x.txt"))
        self.assertEqual(bad.status_code, 400, bad.content)
        self.assertIn("file", bad.json()["error"]["fields"])
        big = self.upload(f"{asset.public_id}/image/", file=png_file(b"\x89PNG\r\n\x1a\n" + b"0" * (10 * MB)))
        self.assertEqual(big.status_code, 400, big.content)
        self.assertIn("10 MB", big.json()["error"]["fields"]["file"][0])
        none = self.client.post(self.url(f"{asset.public_id}/image/"), data={"kind": "file"}, secure=True,
                                HTTP_ORIGIN=ORIGIN)
        self.assertEqual(none.status_code, 400)
        self.assertEqual(self.store.files, {})

    def test_an_image_at_the_limit_is_stored(self):
        asset = make_asset(self.site, published_at=None)
        edge = self.upload(f"{asset.public_id}/image/", file=png_file(b"\x89PNG\r\n\x1a\n" + b"0" * (10 * MB - 8)))
        self.assertEqual(edge.status_code, 200, edge.content)
        self.assertEqual(edge.json()["file_size"], 10 * MB)

    def test_no_store_configured_is_a_503_and_nothing_changes(self):
        asset = make_asset(self.site, file_key="", published_at=None)
        with override_settings(PUBLIC_BLOB_READ_WRITE_TOKEN="", PUBLIC_MEDIA_BASE_URL=""):
            response = self.upload(f"{asset.public_id}/image/")
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json()["error"]["code"], "image_store_not_configured")
        self.assertEqual(MediaAsset.objects.get(pk=asset.pk).file_key, "")


class IsolationTests(MediaOwnerCase):
    def test_an_owner_sees_only_their_own_sites_media(self):
        mine = make_asset(self.site, "Mine")
        bens = make_asset(self.ben_site, "Bens")
        self.assertEqual([a["id"] for a in self.get(self.url()).json()["assets"]], [mine.public_id])
        theirs = self.get(self.url(site_id=self.ben_site_id), client=self.ben).json()["assets"]
        self.assertEqual([a["id"] for a in theirs], [bens.public_id])

    def test_a_foreign_site_is_the_same_404_as_a_missing_site(self):
        bens = make_asset(self.ben_site, "Bens")
        before = list(MediaAsset.objects.order_by("pk").values())
        for method, tail, body in (
            ("get", "", None), ("post", "", {"title": "x", "asset_type": "image"}),
            ("patch", f"{bens.public_id}/", {"title": "hacked"}), ("delete", f"{bens.public_id}/", None),
            ("post", f"{bens.public_id}/publish/", {}), ("post", "reorder/", {"ids": []}),
        ):
            with self.subTest(method=method, tail=tail):
                foreign = self.req(method, self.url(tail, self.ben_site_id), body)
                missing = self.req(method, self.url(tail, MISSING_SITE), body)
                self.assertEqual((foreign.status_code, foreign.json()), (404, NOT_FOUND))
                self.assertEqual(foreign.content, missing.content)
        self.assertEqual(list(MediaAsset.objects.order_by("pk").values()), before)

    def test_another_sites_ids_under_my_own_site_are_the_same_404_as_missing_ones(self):
        bens = make_asset(self.ben_site, "Bens", published_at=None, asset_type="video", file_key="")
        make_asset(self.site, "Mine")
        cases = (
            ("patch", "{id}/", {"title": "hacked"}), ("delete", "{id}/", None),
            ("post", "{id}/publish/", {}), ("post", "{id}/unpublish/", None),
            ("delete", "{id}/image/", None), ("post", "{id}/video/token/", None),
            ("post", "{id}/video/attach/", {"url": "https://media.example.test/media-library/x/y/z.mp4"}),
        )
        before = list(MediaAsset.objects.order_by("pk").values())
        for method, tail, body in cases:
            with self.subTest(method=method, tail=tail):
                foreign = self.req(method, self.url(tail.format(id=bens.public_id)), body)
                gone = self.req(method, self.url(tail.format(id=MISSING_ASSET)), body)
                self.assertEqual((foreign.status_code, foreign.json()), (404, NOT_FOUND))
                self.assertEqual(foreign.content, gone.content)
        for kind in ("file", "poster", "thumb"):
            self.assertEqual(self.upload(f"{bens.public_id}/image/", kind=kind).status_code, 404)
        self.assertEqual(self.store.files, {})
        self.assertEqual(list(MediaAsset.objects.order_by("pk").values()), before)

    def test_ben_cannot_reach_anas_site(self):
        make_asset(self.site)
        self.assertEqual(self.get(self.url(), client=self.ben).status_code, 404)
        self.assertEqual(self.send("post", self.url(), {"title": "x", "asset_type": "image"}, client=self.ben).status_code, 404)
        self.assertEqual(MediaAsset.objects.count(), 1)
