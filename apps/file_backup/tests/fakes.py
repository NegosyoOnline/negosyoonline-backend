"""In-memory source and destination for the tests. No network, no bucket, no Vercel."""
import hashlib

from apps.file_backup.destination import DestinationError
from apps.file_backup.source import SourceError


class FakeSource:
    def __init__(self):
        self.objects = {}  # pathname -> (bytes, content type)
        self.calls = []
        self.fail_fetch = set()

    def add(self, pathname, content, content_type="image/png"):
        self.objects[pathname] = (content, content_type)

    def list_objects(self, prefix=""):
        self.calls.append("list")
        for pathname in sorted(self.objects):
            if pathname.startswith(prefix):
                content = self.objects[pathname][0]
                yield {
                    "pathname": pathname, "size": len(content), "uploaded_at": "t",
                    "etag": hashlib.md5(content).hexdigest(),
                }

    def fetch(self, pathname):
        self.calls.append("fetch")
        if pathname in self.fail_fetch or pathname not in self.objects:
            raise SourceError("fetch failed")
        return self.objects[pathname]

    def put(self, pathname, content, content_type):
        self.calls.append("put")
        self.objects[pathname] = (content, content_type)

    def delete(self, pathname):
        self.calls.append("delete")
        self.objects.pop(pathname, None)


class FakeDestination:
    def __init__(self):
        self.objects = {}
        self.calls = []

    def put(self, key, data):
        self.calls.append("put")
        self.objects[key] = bytes(data)

    def get(self, key):
        self.calls.append("get")
        if key not in self.objects:
            raise DestinationError("no such object")
        return self.objects[key]

    def delete(self, key):
        self.calls.append("delete")
        self.objects.pop(key, None)

    def list(self, prefix):
        self.calls.append("list")
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield key, len(self.objects[key])
