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

from apps.common import middleware
from apps.media_library import client_upload, store
from apps.media_library.models import MediaAsset
from apps.media_library.tests.base import FakeStore, make_asset, png_file
from apps.media_library.tests.test_staff_api import StaffMediaTestCase

RW = "vercel_blob_rw_store123_secretpart"
BASE_URL = "https://store123.public.blob.vercel-storage.com"
SETTINGS = {"PUBLIC_BLOB_READ_WRITE_TOKEN": RW, "PUBLIC_MEDIA_BASE_URL": BASE_URL}
HEAD = "apps.media_library.client_upload.confirm_object"
MB = 1024 * 1024


def decode(token):
    store_id, _, rest = token[len("vercel_blob_client_"):].partition("_")
    signature, _, payload = base64.b64decode(rest).decode().partition(".")
    return store_id, signature, payload, json.loads(base64.b64decode(payload))


class VideoTestCase(StaffMediaTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()
        self.video = make_asset("Vid", asset_type="video", file_key="", published_at=None,
                                poster_key="media-library/poster.png")
        self.image = make_asset("Img")
        self.enter = override_settings(**SETTINGS)
        self.enter.enable()
        self.addCleanup(self.enter.disable)

    def url(self, asset=None, name="abc.mp4"):
        return f"{BASE_URL}/media-library/{(asset or self.video).public_id}/{name}"


class TokenTests(VideoTestCase):
    def test_the_token_is_signed_for_one_path_one_type_one_size_and_ten_minutes(self):
        before = timezone.now()
        response = self.api("video/token/", {"id": self.video.public_id})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        store_id, signature, payload, claims = decode(body["client_token"])
        self.assertEqual(store_id, "store123")
        self.assertEqual(signature, hmac.new(RW.encode(), payload.encode(), hashlib.sha256).hexdigest())
        self.assertTrue(claims["pathname"].startswith(f"media-library/{self.video.public_id}/"))
        self.assertTrue(claims["pathname"].endswith(".mp4"))
        self.assertEqual(claims["pathname"], body["pathname"])
        self.assertEqual(claims["allowedContentTypes"], ["video/mp4"])
        self.assertEqual(claims["maximumSizeInBytes"], 50 * MB)
        self.assertFalse(claims["allowOverwrite"])
        valid_until = claims["validUntil"] / 1000
        self.assertGreater(valid_until, (before + timedelta(minutes=9)).timestamp())
        self.assertLess(valid_until, (timezone.now() + timedelta(minutes=10, seconds=1)).timestamp())
        self.assertEqual((body["method"], body["max_bytes"], body["content_type"]), ("PUT", 50 * MB, "video/mp4"))
        self.assertTrue(body["upload_url"].startswith("https://vercel.com/api/blob/?pathname=media-library%2F"))
        self.assertEqual(body["headers"]["authorization"], "Bearer " + body["client_token"])
        self.assertEqual(body["public_url"], f"{BASE_URL}/{claims['pathname']}")
        self.assertNotIn(RW.split("_")[-1], body["client_token"])  # the secret part is not in the token

    def test_only_a_video_asset_of_the_template_and_only_staff(self):
        self.assertEqual(self.api("video/token/", {"id": self.image.public_id}).status_code, 400)
        self.assertEqual(self.api("video/token/", {"id": "mda_missing"}).status_code, 404)
        self.assertEqual(self.api("video/token/", {}).status_code, 400)
        other = make_asset("Other", asset_type="video", file_key="", template_key="other")
        self.assertEqual(self.api("video/token/", {"id": other.public_id}).status_code, 404)  # another Site's template
        self.client.cookies.clear()
        self.assertError(self.api("video/token/", {"id": self.video.public_id}), 401, "unauthenticated")

    def test_no_store_means_503_and_no_token(self):
        with override_settings(PUBLIC_BLOB_READ_WRITE_TOKEN="", PUBLIC_MEDIA_BASE_URL=""):
            self.assertError(self.api("video/token/", {"id": self.video.public_id}), 503, "image_store_not_configured")
        with override_settings(PUBLIC_BLOB_READ_WRITE_TOKEN="test-token"):  # no store id in it
            self.assertError(self.api("video/token/", {"id": self.video.public_id}), 503, "image_store_not_configured")


class AttachTests(VideoTestCase):
    def attach(self, url, kind="video/mp4", size=5 * MB, asset=None):
        with mock.patch(HEAD, return_value=(kind, size)) as head:
            response = self.api("video/attach/", {"id": (asset or self.video).public_id, "url": url})
        return response, head

    def test_a_good_video_is_recorded_and_the_old_one_goes_after_commit(self):
        deleted = []
        with mock.patch("apps.media_library.store.discard", side_effect=deleted.append):
            with self.captureOnCommitCallbacks(execute=True):
                response, head = self.attach(self.url())
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["file_size"], 5 * MB)
            row = MediaAsset.objects.get(pk=self.video.pk)
            self.assertEqual(row.file_key, f"media-library/{self.video.public_id}/abc.mp4")
            head.assert_called_once()
            with self.captureOnCommitCallbacks(execute=True):
                again, _ = self.attach(self.url(name="def.mp4"), size=1)
            self.assertEqual(again.status_code, 200)
            self.assertIn(f"media-library/{self.video.public_id}/abc.mp4", deleted)

    def test_a_url_that_is_not_ours_or_not_this_assets_is_refused_without_a_head(self):
        other = make_asset("Vid2", asset_type="video", file_key="")
        bad = (
            f"https://evil.example/media-library/{self.video.public_id}/abc.mp4",
            f"http://store123.public.blob.vercel-storage.com/media-library/{self.video.public_id}/abc.mp4",
            f"https://user@store123.public.blob.vercel-storage.com/media-library/{self.video.public_id}/abc.mp4",
            self.url(other),  # another asset's prefix
            f"{BASE_URL}/media-library/abc.mp4",
            f"{BASE_URL}/media-library/{self.video.public_id}/../{other.public_id}/abc.mp4",
            f"{BASE_URL}/media-library/{self.video.public_id}/sub/abc.mp4",
            self.url() + "?x=1",
            self.url(name="abc.png"),
            "", None, 7,
        )
        for url in bad:
            response, head = self.attach(url)
            self.assertEqual(response.status_code, 400, url)
            head.assert_not_called()
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, "")

    def test_wrong_type_or_too_big_is_refused_and_the_stored_object_is_removed(self):
        for kind, size in (("video/webm", MB), ("text/html", MB), ("video/mp4", 50 * MB + 1), ("video/mp4", 0)):
            deleted = []
            with mock.patch("apps.media_library.store.discard", side_effect=deleted.append):
                response, _ = self.attach(self.url(), kind, size)
            self.assertEqual(response.status_code, 400, (kind, size))
            self.assertIn(f"media-library/{self.video.public_id}/abc.mp4", deleted)
        edge, _ = self.attach(self.url(), size=50 * MB)
        self.assertEqual(edge.status_code, 200)

    def test_a_missing_object_is_502_and_nothing_changes(self):
        with mock.patch(HEAD, side_effect=store.StoreError("gone")):
            response = self.api("video/attach/", {"id": self.video.public_id, "url": self.url()})
        self.assertError(response, 502, "video_not_confirmed")
        self.assertEqual(MediaAsset.objects.get(pk=self.video.pk).file_key, "")

    def test_only_a_video_of_the_template_and_only_staff(self):
        self.assertEqual(self.attach(self.url(self.image), asset=self.image)[0].status_code, 400)
        other = make_asset("Other", asset_type="video", file_key="", template_key="other")
        self.assertEqual(self.attach(self.url(other), asset=other)[0].status_code, 404)
        self.client.cookies.clear()
        self.assertError(self.attach(self.url())[0], 401, "unauthenticated")

    def test_a_video_without_its_file_cannot_be_published_until_attached(self):
        self.assertEqual(self.api("publish/", {"id": self.video.public_id}).status_code, 400)
        self.attach(self.url())
        self.assertEqual(self.api("publish/", {"id": self.video.public_id}).status_code, 200)

    def test_an_attach_is_audited_without_values(self):
        self.attach(self.url())
        entry = self.audit_entry()
        self.assertEqual(entry.detail, {"op": "video_attach"})

    def audit_entry(self):
        from apps.operators.models import OperatorAuditEntry
        return OperatorAuditEntry.objects.filter(action="media_library_authored").latest("pk")


class RequestCapTests(VideoTestCase):
    """Real Content-Length headers through RequestSizeCapMiddleware: nothing here skips the cap."""

    def test_an_image_under_the_cap_is_stored_and_an_oversized_body_is_413(self):
        with FakeStore() as fake:
            small = self.upload(file=png_file(b"\x89PNG\r\n\x1a\n" + b"0" * (9 * MB)))
            self.assertEqual(small.status_code, 200, small.content)
            self.assertEqual(len(fake.files), 1)
            # A body past the cap, with its true length declared, is refused before the route runs.
            body = b"x" * (middleware.MAX_REQUEST_BYTES + 1)
            big = self.client.post(
                "/staff/v1/media/upload/", data=body, content_type="multipart/form-data; boundary=x",
                secure=True, HTTP_HOST=self.upload_host(), HTTP_ORIGIN=self.upload_origin(),
            )
            self.assertEqual(int(big.wsgi_request.META["CONTENT_LENGTH"]), len(body))
            self.assertEqual(big.status_code, 413)
            self.assertEqual(big.json()["error"]["code"], "payload_too_large")
            self.assertEqual(len(fake.files), 1)

    def test_the_client_sends_a_content_length_for_the_image_form(self):
        with FakeStore():
            response = self.upload()
        self.assertGreater(int(response.wsgi_request.META["CONTENT_LENGTH"]), 0)

    def upload_host(self):
        from apps.operators.tests.base import HQ
        return HQ

    def upload_origin(self):
        from apps.operators.tests.test_staff_routes import STAFF
        return STAFF
