"""The Pancake adapter (Pancake public API, Facebook Messenger pages).

Written from the shapes TWCinc's client showed (read as a behavioural reference only, nothing
copied) and Pancake's public reference. Every field name marked UNCONFIRMED below has not been
seen in a real payload yet; the real-account gate on staging confirms or corrects it, before any
channel is promised.

The page token is a query parameter on every Pancake URL, so it appears only inside `_call`,
the one function that builds a URL. No URL, no exception text and no body from Pancake is
logged, returned or kept: a failure becomes one fixed outcome code. Success is read from the
answer body (`success`), not from the HTTP status, because Pancake answers 200 on a bad token.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from apps.inquiries import outcomes
from apps.inquiries.adapters.base import (
    ConversationSummary,
    Message,
    Page,
    ProviderAdapter,
    ReplyWindow,
    Result,
    Thread,
    redact,
)

BASE = "https://pages.fm/api/public_api/v1"
TIMEOUT_SECONDS = 6
MAX_BODY = 1_000_000
LIST_LIMIT = 30
MAX_TEXT = 2000
MAX_NAME = 120
MAX_SNIPPET = 200
# Pancake requires since/until on the conversation list, in unix seconds, UTC; 30 days is the
# Supervisor's recorded routine decision on z8uq9md3n6.
LIST_WINDOW_DAYS = 30
# `page_number` is required on the conversation list (live answer 2026-11-17: 200 with
# {"success": false, "message": "page_number is required!"} when it is missing). It is 1-based.
MAX_PAGE = 9999

INVALID_CODES = (102,)  # Pancake error_code for a bad token (seen in TWCinc)
EXPIRED_CODES = ()  # UNCONFIRMED: no distinct expired-token code seen yet
CURSOR_RE = re.compile(r"^[0-9]{1,6}$")
PAGE_RE = re.compile(r"^[0-9]{1,4}$")
REF_RE = re.compile(r"^[A-Za-z0-9_.:-]{3,128}$")
PAGE_ID_RE = re.compile(r"^[A-Za-z0-9_]{3,64}$")


def _open(request, timeout):
    """The single place the network is touched, so tests replace it."""
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - a fixed https address


def _now():
    """The current moment, aware UTC; tests replace it."""
    return datetime.now(timezone.utc)


def _window():
    """The since/until query for the conversation list: integer unix seconds, UTC."""
    until = int(_now().timestamp())
    return {"since": until - LIST_WINDOW_DAYS * 86400, "until": until}


def _page(cursor):
    """The 1-based page number a list cursor stands for; anything unusable is page 1."""
    if isinstance(cursor, str) and PAGE_RE.match(cursor) and 1 <= int(cursor) <= MAX_PAGE:
        return int(cursor)
    return 1


def _read(handle):
    return handle.read(MAX_BODY + 1)


def _interpret(status, raw):
    """(outcome code, parsed body or None) from an HTTP status and the raw body."""
    if status == 429:
        return outcomes.RATE_LIMITED, None
    if status >= 500:
        return outcomes.PROVIDER_DOWN, None
    if status == 401:
        return outcomes.INVALID_CREDENTIALS, None
    if status == 403:
        return outcomes.FORBIDDEN_PAGE, None
    if status == 404:
        return outcomes.NOT_FOUND, None
    if not 200 <= status < 300 or raw is None or len(raw) > MAX_BODY:
        return outcomes.BAD_RESPONSE, None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return outcomes.BAD_RESPONSE, None
    if not isinstance(body, dict) or not isinstance(body.get("success"), bool):
        return outcomes.BAD_RESPONSE, None
    if body["success"]:
        return outcomes.OK, body
    code = body.get("error_code")
    if isinstance(code, int) and not isinstance(code, bool):
        if code in INVALID_CODES:
            return outcomes.INVALID_CREDENTIALS, None
        if code in EXPIRED_CODES:
            return outcomes.EXPIRED_CREDENTIALS, None
    return outcomes.BAD_RESPONSE, None


def _call(method, path, token, query=None, body=None):
    """The one place a URL with the token is built. Returns (outcome code, parsed body or None);
    never raises, and nothing about the request or the failure leaves this function."""
    try:
        params = dict(query or {})
        params["page_access_token"] = token
        url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            url, data=data, method=method, headers={"Content-Type": "application/json"},
        )
        try:
            with _open(request, TIMEOUT_SECONDS) as response:
                status, raw = response.status, _read(response)
        except urllib.error.HTTPError as error:
            status = error.code
            try:
                raw = _read(error)
            except Exception:  # noqa: BLE001 - an unreadable answer is just "no body"
                raw = None
        return _interpret(status, raw)
    except Exception:  # noqa: BLE001 - timeout, DNS, TLS: the text may hold the URL and is dropped
        return outcomes.PROVIDER_DOWN, None


def _text(value, limit):
    return redact(value)[:limit] if isinstance(value, str) else ""


def _stamp(value):
    """An ISO 8601 UTC string from Pancake's timestamp, or None. Pancake's format is confirmed
    (live, 2026-11-17): naive ISO "YYYY-MM-DDTHH:MM:SS.ffffff", read as UTC."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _sender(item):
    sender = item.get("from")
    return sender if isinstance(sender, dict) else {}


class PancakeAdapter(ProviderAdapter):
    provider = "pancake"
    stores_history = False

    def _path(self, creds, suffix=""):
        return f"/pages/{urllib.parse.quote(creds.page_id, safe='')}{suffix}"

    def ref_ok(self, page_id, ref):
        """Pancake builds a conversation id as "{page_id}_{customer id}"; any other ref is not
        this page's, and is refused before a request or a row is made."""
        return bool(REF_RE.match(ref)) and ref.startswith(f"{page_id}_")

    def _conversation_path(self, creds, ref, suffix=""):
        if not self.ref_ok(creds.page_id, ref):
            return None
        return self._path(creds, f"/conversations/{urllib.parse.quote(ref, safe='')}{suffix}")

    def test_connection(self, creds):
        # The cheapest valid read: one row of page 1 (page_number is required, 2026-11-17).
        query = {"page_number": 1, "page_size": 1, **_window()}
        code, _ = _call("GET", self._path(creds, "/conversations"), creds.token, query)
        return Result(code)

    def list_conversations(self, creds, cursor, limit):
        page = _page(cursor)
        query = {"page_number": page, "page_size": LIST_LIMIT, **_window()}
        # `since`, `until` and `page_number` are required here, and `page_size` is accepted
        # (confirmed by live answers, 2026-11-17). Also confirmed live, 2026-11-17: this endpoint
        # and the field names `conversations`, `id`, `snippet`, `updated_at`, `customers[].name`.
        code, body = _call("GET", self._path(creds, "/conversations"), creds.token, query)
        if code != outcomes.OK:
            return Result(code)
        rows = body.get("conversations")
        if not isinstance(rows, list):
            return Result(outcomes.BAD_RESPONSE)
        items = []
        for row in rows[: min(limit, LIST_LIMIT)]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not REF_RE.match(row["id"]):
                continue
            customers = row.get("customers")
            name = customers[0].get("name") if isinstance(customers, list) and customers and isinstance(customers[0], dict) else _sender(row).get("name")
            items.append(ConversationSummary(
                ref=row["id"], display_name=_text(name, MAX_NAME), snippet=_text(row.get("snippet"), MAX_SNIPPET),
                updated_at=_stamp(row.get("updated_at")), channel=self.channel_of(row["id"]),
            ))
        more = len(rows) >= LIST_LIMIT and page < MAX_PAGE  # a full page of the size we asked for
        return Result(outcomes.OK, Page(tuple(items), str(page + 1) if more else None))

    def get_thread(self, creds, ref, cursor):
        path = self._conversation_path(creds, ref, "/messages")
        if path is None:
            return Result(outcomes.NOT_FOUND)
        query = {"current_count": cursor} if cursor and CURSOR_RE.match(cursor) else {}
        # `current_count` paging is confirmed by Pancake's public reference. Confirmed live
        # (2026-11-17): the field names `messages`, `id`, `message`, `inserted_at`, `from.id`,
        # `from.name`, `attachments[].type`, and `can_inbox` at the top level. `has_more` is
        # absent from the live answer, so this next_cursor stays None; the check below is kept.
        code, body = _call("GET", path, creds.token, query)
        if code != outcomes.OK:
            return Result(code)
        rows = body.get("messages")
        if not isinstance(rows, list):
            return Result(outcomes.BAD_RESPONSE)
        messages, name = [], ""
        for row in rows:
            if not isinstance(row, dict):
                continue
            sender = _sender(row)
            from_page = str(sender.get("id", "")) == creds.page_id
            if not from_page and not name:
                name = _text(sender.get("name"), MAX_NAME)
            attachments = row.get("attachments")
            kinds = tuple(
                a["type"][:20] for a in (attachments if isinstance(attachments, list) else [])
                if isinstance(a, dict) and isinstance(a.get("type"), str)
            )
            messages.append(Message(
                id=str(row.get("id", ""))[:128], direction="out" if from_page else "in",
                sender_kind="page" if from_page else "customer", text=_text(row.get("message"), MAX_TEXT),
                attachments=kinds, at=_stamp(row.get("inserted_at")),
            ))
        more = len(rows) > 0 and body.get("has_more") is True  # UNCONFIRMED: `has_more`
        offset = int(cursor) if cursor and CURSOR_RE.match(cursor) else 0
        return Result(outcomes.OK, Thread(
            ref=ref, display_name=name, messages=tuple(messages), window=self.reply_window(body),
            next_cursor=str(offset + len(rows)) if more else None,
        ))

    def reply_window(self, body):
        """Open only when Pancake itself says so (`can_inbox`). Anything else is closed."""
        flag = body.get("can_inbox")
        if flag is True:
            return ReplyWindow(True, None, "provider_open")
        if flag is False:
            return ReplyWindow(False, None, "provider_closed")
        return ReplyWindow(False, None, "unknown")

    def send_reply(self, creds, ref, text):
        path = self._conversation_path(creds, ref, "/messages")
        if path is None:
            return Result(outcomes.NOT_FOUND)
        # `message` and `content_ids` must not be sent together (Pancake answers 500); the
        # sender is the page itself, so `sender_id` is left out.
        code, body = _call("POST", path, creds.token, None, {"action": "reply_inbox", "message": text})
        if code != outcomes.OK:
            return Result(code)
        ref_out = body.get("message_id", body.get("id", ""))  # UNCONFIRMED: which key carries it
        ref_out = ref_out if isinstance(ref_out, str) and re.match(r"^[A-Za-z0-9_.:=-]{1,128}$", ref_out) else ""
        return Result(outcomes.OK, ref_out)

    def provider_url(self, creds, ref):
        return None  # UNCONFIRMED: no verified deep-link pattern into Pancake yet

    def channel_of(self, ref):
        return "facebook"  # Pancake pages here are Facebook Messenger pages; nothing else is promised
