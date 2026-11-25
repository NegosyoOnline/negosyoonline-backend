"""Helpers shared by the media library tests. Every asset belongs to a site."""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from apps.accounts.tests.base import ORIGIN
from apps.catalog.tests.base import MEDIA, PNG
from apps.media_library import store
from apps.media_library.models import MediaAsset
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 32
MB = 1024 * 1024
NOT_FOUND = {"error": {"code": "not_found", "message": "Not found."}}
MISSING_SITE = "site_00000000000000000000"
MISSING_ASSET = "mda_00000000000000000000"
RW = "vercel_blob_rw_store123_secretpart"
BASE_URL = "https://store123.public.blob.vercel-storage.com"
RW_SETTINGS = {"PUBLIC_BLOB_READ_WRITE_TOKEN": RW, "PUBLIC_MEDIA_BASE_URL": BASE_URL}
HEAD = "apps.media_library.client_upload.confirm_object"


def rw_blob_url(site_public_id, asset_public_id, name="abc.mp4"):
    """A video URL on a store whose token carries a store id (what the token route needs)."""
    return f"{BASE_URL}/media-library/{site_public_id}/{asset_public_id}/{name}"


def video_file(content=MP4, content_type="video/mp4", name="clip.mp4"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def png_file(content=PNG, content_type="image/png", name="photo.png"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def make_asset(site, title="Barley promo", days_ago=1, **fields):
    values = {
        "asset_type": "image",
        "file_key": f"media-library/{site.public_id}/{title.lower().replace(' ', '')}.png",
        "published_at": timezone.now() - timedelta(days=days_ago),
    }
    return MediaAsset.objects.create(site=site, title=title, **{**values, **fields})


class FakeStore:
    """The media store replaced by a dict, so no test reaches the network. A context manager;
    `.files` holds what is stored, `.deleted` what was removed."""

    def __init__(self):
        self.files, self.deleted, self.count = {}, [], 0
        self.fail_delete = False  # a test sets it to make every ledger delete fail

    def _save(self, asset, content, content_type):
        self.count += 1
        key = f"{store.asset_prefix(asset)}{self.count:032x}.png"
        self.files[key] = content
        return key

    def _discard(self, key):
        if key:
            self.deleted.append(key)
            self.files.pop(key, None)

    def _delete(self, key):
        """store.delete (the ledger's raising delete): an absent key is gone, not an error."""
        if self.fail_delete:
            raise store.StoreError("The media store delete failed (500).")
        self._discard(key)

    def __enter__(self):
        self.settings = override_settings(**MEDIA)
        self.settings.enable()
        self.patches = [
            mock.patch("apps.media_library.store.save", side_effect=self._save),
            mock.patch("apps.media_library.store.discard", side_effect=self._discard),
            mock.patch("apps.media_library.store.delete", side_effect=self._delete),
        ]
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()
        self.settings.disable()


@override_settings(MEMBERS_ENABLED=True)
class MediaOwnerCase(SitesApiTestCase):
    """Ana (self.client, self.site) and Ben (self.ben, self.ben_site) each own a Santé site."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.ben = self.other_client()
        self.ben_site_id = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben's")["id"]
        self.ben_site = Site.objects.get(public_id=self.ben_site_id)
        self.store = FakeStore().__enter__()
        self.addCleanup(self.store.__exit__, None, None, None)

    def url(self, tail="", site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/media/{tail}"

    def req(self, method, path, body=None, client=None):
        if method == "get":
            return self.get(path, client=client)
        return self.send(method, path, body, client=client)

    def upload(self, tail, client=None, site_id=None, file=None, **form):
        data = {"file": file or png_file(), **form}
        return (client or self.client).post(
            self.url(tail, site_id), data=data, secure=True, HTTP_ORIGIN=ORIGIN
        )
