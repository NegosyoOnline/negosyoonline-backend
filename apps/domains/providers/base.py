"""What every provider adapter shares: the error types, the transport and the allowlist check.

An adapter reaches the network only through `_call`, which first refuses (before any network)
a paused provider, a missing token, and any method and path that is not on the adapter's
allowlist. A transport is any callable `(method, url, headers, body) -> (status, data)`; tests
and the fakes pass their own, so no test touches the network. A token is only ever placed in the
Authorization header: it is never in an exception, a log record or a returned value.
"""
import json
import logging
import re
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1_000_000


class ProviderError(Exception):
    """A provider answered with a failure. `code` is one of a short fixed set; no body, no token."""

    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class Refused(ProviderError):
    """Our own guard refused the call before any network call."""

    def __init__(self, code):
        super().__init__(code, retryable=False)


class Paused(Refused):
    def __init__(self):
        super().__init__("paused")


class NotConfigured(Refused):
    def __init__(self, what="not_configured"):
        super().__init__(what)


_STATUS_CODES = {
    400: ("bad_request", False), 401: ("auth", False), 402: ("payment_required", False),
    403: ("auth", False), 404: ("not_found", False), 409: ("conflict", False),
    429: ("rate_limited", True),
}


def map_error(status):
    code, retryable = _STATUS_CODES.get(status, ("unavailable", True) if status >= 500 else ("bad_request", False))
    return ProviderError(code, retryable)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def default_transport(method, url, headers, body):
    """The only real network path. https only; redirects are not followed; the body is capped.
    Never called by a test or by a local run (providers `off` and `fake`)."""
    if not url.startswith("https://"):
        raise ProviderError("bad_request")
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status, raw = response.status, response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read(MAX_RESPONSE_BYTES)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError("unavailable", retryable=True) from None
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = {}
    return status, parsed


class Adapter:
    BASE = ""
    ALLOWED = ()  # (method, compiled regex of the path) pairs

    def __init__(self, token, transport=None):
        self._token = token or ""
        self._transport = transport or default_transport

    def _preflight(self):
        if settings.DOMAINS_PROVIDER_PAUSED:
            raise Paused()
        if not self._token:
            raise NotConfigured("no_token")

    def _call(self, method, path, body=None, query=None):
        return self._call_status(method, path, body, query)[1]

    def _call_status(self, method, path, body=None, query=None):
        """Like `_call`, but also returns the 2xx status (the Registrar tells 201 from 202)."""
        self._preflight()
        if not any(method == m and rx.fullmatch(path) for m, rx in self.ALLOWED):
            raise Refused("not_allowlisted")
        url = self.BASE + path
        if query:
            from urllib.parse import urlencode
            url += "?" + urlencode(query)
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        status, data = self._transport(method, url, headers, body)
        if not 200 <= status < 300:
            raise map_error(status)
        return status, (data if isinstance(data, dict) else {})

    def __repr__(self):
        return f"<{type(self).__name__}>"  # never shows the token


def allow(method, pattern):
    return method, re.compile(pattern)


HOSTNAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
