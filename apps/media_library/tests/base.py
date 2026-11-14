"""Helpers shared by the media library tests."""
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from apps.catalog.tests.base import MEDIA, PNG
from apps.media_library.models import MediaAsset

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 32


def video_file(content=MP4, content_type="video/mp4", name="clip.mp4"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def png_file(content=PNG, content_type="image/png", name="photo.png"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def make_asset(title="Barley promo", days_ago=1, **fields):
    values = {
        "asset_type": "image", "file_key": f"media-library/{title.lower().replace(' ', '')}.png",
        "published_at": timezone.now() - timedelta(days=days_ago),
    }
    return MediaAsset.objects.create(title=title, **{**values, **fields})


class FakeStore:
    """The media store replaced by a dict, so no test reaches the network. A context manager;
    `.files` holds what is stored, `.deleted` what was removed."""

    def __init__(self):
        self.files, self.deleted, self.count = {}, [], 0

    def _save(self, content, content_type):
        self.count += 1
        key = f"media-library/{self.count:032x}.{'mp4' if content_type == 'video/mp4' else 'png'}"
        self.files[key] = content
        return key

    def _discard(self, key):
        if key:
            self.deleted.append(key)
            self.files.pop(key, None)

    def __enter__(self):
        self.settings = override_settings(**MEDIA)
        self.settings.enable()
        self.patches = [
            mock.patch("apps.media_library.store.save", side_effect=self._save),
            mock.patch("apps.media_library.store.discard", side_effect=self._discard),
        ]
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()
        self.settings.disable()
