"""A stand-in Bunny Stream for tests: the stream.py calls replaced by a dict, per library.

Every library has its own made-up key; a call with another library's key fails like Bunny's 401.
Nothing here touches the network, and the keys are test values.
"""
import io
import os
from unittest import mock

from apps.training import stream
from apps.training.models import StreamLibrary

KEYS = {"ANA": "ana-local-test-key", "BEN": "ben-local-test-key", "LIVE4MORE": "live-local-test-key"}


class FakeBunny:
    """.videos is {(library id, guid): {"status", "storage", "bytes"}}; .deleted lists removals."""

    def __init__(self):
        self.videos, self.deleted, self.calls, self.sources = {}, [], [], {}
        self.next_status = stream.FINISHED  # what a newly created video reports when asked
        self.fail = set()  # action names that raise StreamError
        self.counter = 0

    def _guard(self, action, library):
        self.calls.append((action, library.id))
        if action in self.fail:
            raise stream.StreamError(f"Video {action} failed (500).")
        if KEYS[StreamLibrary.objects.get(library_id=library.id).key_ref] != library.key:
            raise stream.StreamError(f"Video {action} failed (401).")

    def create_video(self, library, title):
        self._guard("create", library)
        self.counter += 1
        guid = f"{library.id:06d}-guid-{self.counter:06d}"
        self.videos[(library.id, guid)] = {"status": stream.CREATED, "storage": 0, "bytes": b""}
        return guid

    def get_video(self, library, guid):
        self._guard("check", library)
        video = self.videos[(library.id, guid)]
        status = video["status"]
        if status == stream.CREATED and video["bytes"]:
            status = self.next_status
        if "status_override" in video:
            status = video["status_override"]
        return {"status": status, "length": 12, "storage_size": video["storage"] or (len(video["bytes"]) or 1000)}

    def delete_video(self, library, guid):
        self._guard("delete", library)
        self.videos.pop((library.id, guid), None)
        self.deleted.append((library.id, guid))

    def upload_bytes(self, library, guid, source, length):
        self._guard("upload", library)
        data = source.read()
        assert len(data) == length
        self.videos[(library.id, guid)]["bytes"] = data

    def download_source(self, library, guid):
        self.calls.append(("download", library.id))  # a CDN read: no key involved
        if "download" in self.fail:
            raise stream.StreamError("Video download failed.")
        data = self.sources[(library.id, guid)]
        return io.BytesIO(data), len(data)

    def put_ready(self, library_id, guid, data=b"video-bytes"):
        """A video that already exists and is ready, as a source (or an upload that finished)."""
        self.videos[(library_id, guid)] = {"status": stream.FINISHED, "storage": 2048, "bytes": data}
        self.sources[(library_id, guid)] = data

    def __enter__(self):
        self.env = mock.patch.dict(os.environ, {f"BUNNY_STREAM_KEY_{k}": v for k, v in KEYS.items()})
        self.env.start()
        self.patches = [
            mock.patch.object(stream, name, getattr(self, name))
            for name in ("create_video", "get_video", "delete_video", "upload_bytes", "download_source")
        ]
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()
        self.env.stop()


def set_library(site, library_id, key_ref, cdn_host="vz-test.b-cdn.net"):
    return StreamLibrary.objects.update_or_create(
        site=site, defaults={"library_id": library_id, "cdn_host": cdn_host, "key_ref": key_ref}
    )[0]
