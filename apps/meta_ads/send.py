"""The one HTTP call to Meta's Conversions API. Standard library only.

The access token travels in the POST body, never the query string. Nothing from Meta's answer
is kept but the HTTP status and the numeric error code; exception text, response bodies and the
request are never logged or returned."""
import json
import urllib.error
import urllib.request
from typing import NamedTuple

# Meta's versions page (developers.facebook.com/docs/graph-api/changelog/versions, read 2026-11-14): Marketing API
# v23.0 expired 2026-06-09, v24.0 expired 2026-10-06, v25.0 is current. The Conversions API is a Marketing API endpoint.
GRAPH_VERSION = "v25.0"
TIMEOUT_SECONDS = 5
MAX_ERROR_BODY = 20_000

OK, BAD_TOKEN, BAD_PIXEL, RATE_LIMITED, META_DOWN, FAILED = (
    "ok", "bad_token", "bad_pixel", "rate_limited", "meta_down", "failed",
)
RETRYABLE = (RATE_LIMITED, META_DOWN)

# Meta's numeric error codes (error.code in an error answer).
TOKEN_CODES = (190, 102, 200)  # invalid or expired token, session, permission
PIXEL_CODES = (100, 803, 2804001)  # unknown or unusable dataset
RATE_CODES = (4, 17, 32, 613, 80004)


class Result(NamedTuple):
    outcome: str
    http_status: int | None = None
    error_code: int | None = None


def _open(request, timeout):
    """The single place the network is touched, so tests replace it."""
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - a fixed https address


def _error_code(error):
    try:
        body = json.loads(error.read(MAX_ERROR_BODY).decode("utf-8", "replace"))
        code = body["error"]["code"]
    except Exception:  # noqa: BLE001 - an unreadable answer is just "no code"
        return None
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def post_event(pixel_id, token, event, test_event_code=""):
    """Send one event. Returns a Result; never raises."""
    payload = {"data": [event], "access_token": token}
    if test_event_code:
        payload["test_event_code"] = test_event_code
    request = urllib.request.Request(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{pixel_id}/events",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _open(request, TIMEOUT_SECONDS) as response:
            return Result(OK, response.status)
    except urllib.error.HTTPError as error:
        status, code = error.code, _error_code(error)
        if status >= 500:
            return Result(META_DOWN, status, code)
        if status == 429 or code in RATE_CODES:
            return Result(RATE_LIMITED, status, code)
        if code in TOKEN_CODES or status in (401, 403):
            return Result(BAD_TOKEN, status, code)
        if code in PIXEL_CODES:
            return Result(BAD_PIXEL, status, code)
        return Result(FAILED, status, code)
    except Exception:  # noqa: BLE001 - timeout, DNS, TLS: the text is never kept
        return Result(META_DOWN)
