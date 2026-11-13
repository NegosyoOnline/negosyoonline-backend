"""The private Vercel Blob store as the backup reads it: list everything, fetch one object.

This does not use `private_files.BlobStore`: that client rejects any pathname that is not a
key the app makes, and a backup must copy every object. Nothing here changes how files are
uploaded or served. The same read-write token is used (Vercel has no read-only token) and is
sent only as a request header; it appears in no log line or error.

The list call follows Vercel's own client (@vercel/blob, API version 13): GET {api}/?limit=&cursor=
&prefix=, answered with {"blobs": [{pathname, size, uploadedAt, etag, ...}], "cursor", "hasMore"}.
The REST form of list is not in Vercel's docs (only the SDK is), so the first `--measure` run
on staging proves it; if it differs the fix is in `list_objects` only.

`put` and `delete` exist for the restore and the self-test, and write only what they are given.
"""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from apps.common import private_files

logger = logging.getLogger(__name__)

PAGE = 1000
TIMEOUT = 30
TRIES = 3


class SourceError(Exception):
    """The store could not do what was asked. The message never holds the token or a name."""


class BlobSource:
    def __init__(self, token):
        # vercel_blob_rw_<store id>_<secret>: the store id is the fourth part.
        parts = (token or "").split("_")
        if len(parts) < 5 or not parts[3].isalnum():
            raise SourceError("The private store token is not a read-write token.")
        self._token = token
        self._host = private_files.BLOB_HOST.format(store_id=parts[3].lower())

    def _send(self, action, url, *, method="GET", data=None, headers=None, want_headers=False):
        status = "no answer"
        for attempt in range(TRIES):
            request = urllib.request.Request(url, data=data, method=method)
            request.add_header("authorization", f"Bearer {self._token}")
            request.add_header("user-agent", private_files.USER_AGENT)
            for name, value in (headers or {}).items():
                request.add_header(name, value)
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    body = response.read()
                    return (body, response.headers) if want_headers else body
            except urllib.error.HTTPError as error:
                status = error.code
                if error.code < 500 and error.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, OSError):
                status = "no answer"
            time.sleep(1 + attempt)
        # Raised outside the except block, so the failed request (and the header that carries
        # the token) is not chained to this error.
        logger.warning("File backup source %s failed: %s", action, status)
        raise SourceError(f"The private store {action} failed ({status}).")

    def list_objects(self, prefix=""):
        """Every object whose pathname starts with `prefix`, as dicts with pathname, size,
        uploaded_at and etag. Pages of PAGE until the store says there is no more."""
        cursor = None
        while True:
            query = {"limit": PAGE}
            if cursor:
                query["cursor"] = cursor
            if prefix:
                query["prefix"] = prefix
            body = self._send(
                "list", f"{private_files.BLOB_API}/?{urllib.parse.urlencode(query)}",
                headers={"x-api-version": private_files.BLOB_API_VERSION},
            )
            try:
                answer = json.loads(body)
                blobs = answer["blobs"]
                for blob in blobs:
                    yield {
                        "pathname": blob["pathname"],
                        "size": int(blob["size"]),
                        "uploaded_at": str(blob.get("uploadedAt", "")),
                        "etag": str(blob.get("etag", "")),
                    }
                cursor = answer.get("cursor") if answer.get("hasMore") else None
            except (ValueError, KeyError, TypeError):
                logger.warning("File backup source list answered in an unknown shape.")
                raise SourceError("The private store list was not understood.") from None
            if not cursor:
                return

    def _url(self, pathname):
        return f"https://{self._host}/{urllib.parse.quote(pathname)}"

    def fetch(self, pathname):
        """(bytes, content type or "") of one object."""
        body, headers = self._send("get", self._url(pathname), want_headers=True)
        return body, (headers.get("content-type") or "").split(";")[0].strip().lower()

    def put(self, pathname, content, content_type):
        query = urllib.parse.urlencode({"pathname": pathname})
        body = self._send(
            "put", f"{private_files.BLOB_API}/?{query}", method="PUT", data=content,
            headers={
                "x-api-version": private_files.BLOB_API_VERSION,
                "x-vercel-blob-access": "private",
                "x-content-type": content_type or "application/octet-stream",
                "x-add-random-suffix": "0",
            },
        )
        try:
            stored = json.loads(body).get("pathname")
        except (ValueError, AttributeError):
            stored = None
        if stored != pathname:
            raise SourceError("The private store put was not confirmed.")

    def delete(self, pathname):
        self._send(
            "delete", f"{private_files.BLOB_API}/delete", method="POST",
            data=json.dumps({"urls": [self._url(pathname)]}).encode(),
            headers={
                "x-api-version": private_files.BLOB_API_VERSION,
                "content-type": "application/json",
            },
        )
