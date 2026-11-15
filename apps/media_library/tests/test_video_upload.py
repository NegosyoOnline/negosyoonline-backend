"""A video goes browser to store with a backend-signed client token; images stay on this server.
No test reaches the network: the HEAD of the stored object is mocked."""
import base64
import hashlib
import hmac
import json
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from apps.accounts.tests.base import ORIGIN
from apps.common import middleware
from apps.media_library import client_upload, store
from apps.media_library.models import MediaAsset
from apps.media_library.tests.base import MB, MediaOwnerCase, make_asset, png_file

RW = "vercel_blob_rw_store123_secretpart"
BASE_URL = "https://store123.public.blob.vercel-storage.com"
SETTINGS = {"PUBLIC_BLOB_READ_WRITE_TOKEN": RW, "PUBLIC_MEDIA_BASE_URL": BASE_URL}
HEAD = "apps.media_library.client_upload.confirm_object"


def decode(token):
    store_id, _, rest = token[len("vercel_blob_client_"):].partition("_")
    signature, _, payload = base64.b64decode(rest).decode().partition(".")
    return store_id, signature, payload, json.loads(base64.b64decode(payload))


class VideoTestCase(MediaOwnerCase):
    def setUp(self):
        super().setUp()
        self.video = make_asset(self.site, "Vid", asset_type="video", file_key="", published_at=None,
                                poster_key=f"media-library/{self.site_id}/poster.png")
        self.image = make_asset(self.site, "Img")
        self.enter = override_settings(**SETTINGS)
        self.enter.enable()
        self.addCleanup(self.enter.disable)

    def prefix(self, asset=None, site_id=None):
        return f"media-library/{site_id or self.site_id}/{(asset or self.video).public_id}/"

    def url(self, tail="", site_id=None):  # the editor's media routes
        return super().url(tail, site_id)

    def blob_url(self, asset=None, name="abc.mp4", site_id=None):
        return f"{BASE_URL}/{self.prefix(asset, site_id)}{name}"

    def token(self, asset=None, **extra):
        return self.send("post", self.url(f"{(asset or self.video).public_id}/video/token/"), None, **extra)


class TokenTests(VideoTestCase):
    def test_the_token_is_signed_for_one_path_one_type_one_size_and_ten_minutes(self):
        before = timezone.now()
        response = self.token()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        store_id, signature, payload, claims = decode(body["client_token"])
        self.assertEqual(store_id, "store123")
        self.assertEqual(signature, hmac.new(RW.encode(), payload.encode(), hashlib.sha256).hexdigest())
        self.assertTrue(claims["pathname"].startswith(self.prefix()))  # media-library/<site id>/<asset id>/
        self.assertTrue(claims["pathname"].endswith(".mp4"))
        self.assertEqual(claims["pathname"], body["pathname"])
        self.assertEqual(claims["allowedContentTypes"], ["video/mp4"])
        self.assertEqual(claims["maximumSizeInBytes"], 50 * MB)
        self.assertFalse(claims["allowOverwrite"])
        self.assertFalse(claims["addRandomSuffix"])
        valid_until = claims["validUntil"] / 1000
        self.assertGreater(valid_until, (before + timedelta(minutes=9)).timestamp())
        self.assertLess(valid_until, (timezone.now() + timedelta(minutes=10, seconds=1)).timestamp())
        self.assertEqual((body["method"], body["max_bytes"], body["content_type"]), ("PUT", 50 * MB, "video/mp4"))
        self.assertTrue(body["upload_url"].startswith("https://vercel.com/api/blob/?pathname=media-library%2F"))
        self.assertEqual(body["headers"]["authorization"], "Bearer " + body["client_token"])
        self.assertEqual(body["public_url"], f"{BASE_URL}/{claims['pathname']}")
        self.assertNotIn(RW.split("_")[-1], body["client_token"])  # the secret part is not in the token
        self.assertEqual(self.get(self.url())["Cache-Control"], "no-store")
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_each_token_names_a_new_random_path(self):
        paths = {self.token().json()["pathname"] for _ in range(3)}
        self.assertEqual(len(paths), 3)

    def test_the_token_expires_ten_minutes_after_it_is_made(self):
        now = timezone.now()
        _, claims = client_upload.client_token(f"{self.prefix()}x.mp4", now=now)
        self.assertEqual(claims["validUntil"], int((now + timedelta(minutes=10)).timestamp() * 1000))
        self.assertEqual(client_upload.TOKEN_MINUTES, 10)

    def test_only_a_video_asset_of_this_site(self):
        self.assertEqual(self.token(self.image).status_code, 400)
        self.assertEqual(self.send("post", self.url("mda_missing/video/token/"), None).status_code, 404)
        bens = make_asset(self.ben_site, "Bens", asset_type="video", file_key="")
        self.assertEqual(self.token(bens).status_code, 404)  # another site's asset
        self.client.cookies.clear()
        self.assertEqual(self.token().status_code, 401)

    def test_no_store_means_503_and_no_token(self):
        with override_settings(PUBLIC_BLOB_READ_WRITE_TOKEN="", PUBLIC_MEDIA_BASE_URL=""):
            self.error(self.token(), 503, "image_store_not_configured")
        with override_settings(PUBLIC_BLOB_READ_WRITE_TOKEN="test-token"):  # no store id in it
            self.error(self.token(), 503, "image_store_not_configured")


class AttachTests(VideoTestCase):
    def attach(self, url, kind="video/mp4", size=5 * MB, asset=None):
        with mock.patch(HEAD, return_value=(kind, size)) as head:
            response = self.send("post", self.url(f"{(asset or self.video).public_id}/video/attach/"), {"url": url})
        return response, head

    def test_a_good_video_is_recorded_and_the_old_one_goes_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            response, head = self.attach(self.blob_url())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["file_size"], 5 * MB)
        self.assertEqual(response.json()["file_url"], self.blob_url())
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, f"{self.prefix()}abc.mp4")
        head.assert_called_once()
        with self.captureOnCommitCallbacks(execute=True):
            again, _ = self.attach(self.blob_url(name="def.mp4"), size=1)
        self.assertEqual(again.status_code, 200)
        self.assertIn(f"{self.prefix()}abc.mp4", self.store.deleted)

    def test_a_url_that_is_not_ours_or_not_this_sites_and_assets_is_refused_without_a_head(self):
        other = make_asset(self.site, "Vid2", asset_type="video", file_key="")
        bens = make_asset(self.ben_site, "Bens", asset_type="video", file_key="")
        bad = (
            f"https://evil.example/{self.prefix()}abc.mp4",
            f"http://store123.public.blob.vercel-storage.com/{self.prefix()}abc.mp4",
            f"https://user@store123.public.blob.vercel-storage.com/{self.prefix()}abc.mp4",
            self.blob_url(other),  # another asset's prefix, same site
            self.blob_url(bens, site_id=self.ben_site_id),  # another site's asset
            self.blob_url(self.video, site_id=self.ben_site_id),  # this asset id under another site's prefix
            f"{BASE_URL}/media-library/{self.video.public_id}/abc.mp4",  # the old, site-less layout
            f"{BASE_URL}/media-library/abc.mp4",
            f"{BASE_URL}/{self.prefix()}../{other.public_id}/abc.mp4",
            f"{BASE_URL}/{self.prefix()}sub/abc.mp4",
            self.blob_url() + "?x=1",
            self.blob_url(name="abc.png"),
            "", None, 7,
        )
        for url in bad:
            with self.subTest(url=url):
                response, head = self.attach(url)
                self.assertEqual(response.status_code, 400, url)
                head.assert_not_called()
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, "")
        self.assertEqual(self.store.deleted, [])  # nothing of anyone else's is touched

    def test_wrong_type_or_too_big_is_refused_and_the_stored_object_is_removed(self):
        for kind, size in (("video/webm", MB), ("text/html", MB), ("video/mp4", 50 * MB + 1), ("video/mp4", 0)):
            before = len(self.store.deleted)
            response, _ = self.attach(self.blob_url(), kind, size)
            self.assertEqual(response.status_code, 400, (kind, size))
            self.assertIn("url", response.json()["error"]["fields"])
            self.assertEqual(self.store.deleted[before:], [f"{self.prefix()}abc.mp4"])
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, "")
        edge, _ = self.attach(self.blob_url(), size=50 * MB)
        self.assertEqual(edge.status_code, 200)

    def test_a_missing_object_is_502_and_nothing_changes(self):
        with mock.patch(HEAD, side_effect=store.StoreError("gone")):
            response = self.send("post", self.url(f"{self.video.public_id}/video/attach/"), {"url": self.blob_url()})
        self.error(response, 502, "video_not_confirmed")
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, "")

    def test_only_a_video_of_this_site_and_only_the_signed_in_owner(self):
        self.assertEqual(self.attach(self.blob_url(self.image), asset=self.image)[0].status_code, 400)
        bens = make_asset(self.ben_site, "Bens", asset_type="video", file_key="")
        self.assertEqual(self.attach(self.blob_url(bens, site_id=self.ben_site_id), asset=bens)[0].status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.attach(self.blob_url())[0].status_code, 401)

    def test_a_video_without_its_file_cannot_be_published_until_attached(self):
        publish = self.url(f"{self.video.public_id}/publish/")
        self.assertEqual(self.send("post", publish, {}).status_code, 400)
        self.attach(self.blob_url())
        self.assertEqual(self.send("post", publish, {}).status_code, 200)

    def test_the_video_file_never_comes_through_the_image_route(self):
        response = self.upload(f"{self.video.public_id}/image/", file=png_file(), kind="file")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.files, {})


class RequestCapTests(VideoTestCase):
    """Real Content-Length headers through RequestSizeCapMiddleware: nothing here skips the cap."""

    def test_an_image_under_the_cap_is_stored_and_an_oversized_body_is_413(self):
        path = self.url(f"{self.image.public_id}/image/")
        small = self.upload(f"{self.image.public_id}/image/", file=png_file(b"\x89PNG\r\n\x1a\n" + b"0" * (9 * MB)))
        self.assertEqual(small.status_code, 200, small.content)
        self.assertGreater(int(small.wsgi_request.META["CONTENT_LENGTH"]), 9 * MB)  # a real length went through
        stored = len(self.store.files)
        self.assertEqual(stored, 1)
        # A body past the cap, with its true length declared, is refused before the route runs.
        body = b"x" * (middleware.MAX_REQUEST_BYTES + 1)
        big = self.client.post(path, data=body, content_type="multipart/form-data; boundary=x",
                               secure=True, HTTP_ORIGIN=ORIGIN)
        self.assertEqual(int(big.wsgi_request.META["CONTENT_LENGTH"]), len(body))
        self.assertEqual(big.status_code, 413)
        self.assertEqual(big.json()["error"]["code"], "payload_too_large")
        self.assertEqual(len(self.store.files), stored)

    def test_the_image_limit_is_under_the_request_cap(self):
        self.assertLess(store.IMAGE_MAX_BYTES * 2, middleware.MAX_REQUEST_BYTES)  # one image per request, with room

    def test_an_image_just_over_the_limit_is_400_not_413(self):
        response = self.upload(f"{self.image.public_id}/image/", file=png_file(b"\x89PNG\r\n\x1a\n" + b"0" * (10 * MB)))
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.store.files, {})


class PublicUrlTests(VideoTestCase):
    """The token's public_url is what the browser sends to video/attach/."""

    def test_the_public_url_from_the_token_passes_the_attach_prefix_check(self):
        body = self.token().json()
        self.assertEqual(body["public_url"], store.url_for(body["pathname"]))
        asset = MediaAsset.objects.select_related("site").get(pk=self.video.pk)
        self.assertEqual(client_upload.key_from_url(asset, body["public_url"]), body["pathname"])
        with mock.patch(HEAD, return_value=("video/mp4", 5 * MB)):
            response = self.send("post", self.url(f"{self.video.public_id}/video/attach/"), {"url": body["public_url"]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["file_url"], body["public_url"])

    def test_another_assets_public_url_is_still_refused(self):
        other = make_asset(self.site, "Vid2", asset_type="video", file_key="")
        foreign = self.token(other).json()["public_url"]
        with mock.patch(HEAD, return_value=("video/mp4", 5 * MB)) as head:
            response = self.send("post", self.url(f"{self.video.public_id}/video/attach/"), {"url": foreign})
        self.assertEqual(response.status_code, 400, response.content)
        head.assert_not_called()
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, "")
