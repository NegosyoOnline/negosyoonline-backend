"""The Subscriber's customer inquiries, under /v1/sites/{site_id}/inquiries/:

    GET    .../connection/                      -> {provider, status, page_id, label, token_set, last_test}
    PUT    .../connection/                      {page_id?, access_token?, label?}
    DELETE .../connection/                      disconnects: clears the token
    POST   .../connection/test/                 answers {outcome}
    GET    .../conversations/?cursor=           -> {items, next_cursor}   (read from the provider)
    GET    .../conversations/{ref}/?cursor=     -> the thread, whether a reply may be sent, the replies sent
    POST   .../conversations/{ref}/reply/       {text, idempotency_key} -> {state, error_code, created_at}
    GET    .../channels/                        -> {webchat_enabled, email_enabled, paused_until, chat_limit_reached, webchat_available,
                                                 contact_inbox_available}
    PUT    .../channels/                        {webchat_enabled?, email_enabled?, paused_until?}  (null clears the pause)
    POST   .../conversations/{ref}/close/       -> {ref, status, unread}   (stored channels only)
    POST   .../conversations/{ref}/reopen/      -> {ref, status, unread}
    POST   .../conversations/{ref}/read/        -> {ref, status, unread}

The list merges the Pancake conversations (read from the provider) with the stored channels'
(website chat, read from our own tables), latest first, and adds `sources` ({pancake, stored}: "ok" or
the reason that source gave nothing). A stored channel's refs start `wc_` (`em_` for email, later),
so they never collide with a Pancake ref; the ref chooses the path. Pancake being unconnected or
down is a flag on its source, not a failure of the list, once the site has a stored conversation;
before that the list answers as it always did.

Paging. `cursor` pages Pancake only, as before. The first response carries the newest 30 stored
conversations and `stored_next_cursor` (null when there are no more). Pass it back as
`?stored_cursor=` to get the next stored page alone: {items, next_cursor: null, stored_next_cursor,
sources: {stored}}. The cursor is opaque (last message time and id); a malformed one is 422.

No route returns the page token, not even a mask or a length. While INQUIRIES_ENABLED is off,
or before the tables exist, every route answers 404, signed in or not. The site is found in the
router's sign-in step through site_for(account, site_id, template=SANTE), so a missing, removed,
foreign or non-Santé site answers 404 before any body is read; then the Billing-only rule (403
`billing_only`). A conversation ref that is not this site's connection's answers the same 404.
"""
import re
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.accounts.api import signed_in
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.inquiries import crypto, outcomes, services, stored
from apps.inquiries.models import InquiryConnection
from apps.inquiries.switch import contact_inbox_on, inquiries_on, stored_tables_exist, tables_exist
from apps.sites import templates
from apps.sites.api import _owner_site
from apps.sites.services import site_for

WRITE_LIMIT, TEST_LIMIT, READ_LIMIT, REPLY_LIMIT, WINDOW = 20, 10, 600, 120, 3600  # per account per hour
MAX_REPLY = 2000
PAGE_ID_RE = re.compile(r"^[A-Za-z0-9_]{3,64}$")
LABEL_RE = re.compile(r"^[^\x00-\x1f\x7f]{0,60}$")
TOKEN_RE = re.compile(r"^\S{20,1500}$")
REF_RE = re.compile(r"^[A-Za-z0-9_.:-]{3,128}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
CURSOR_RE = re.compile(r"^[0-9]{1,6}$")


def inquiries_site(request):
    """The router's sign-in step. It finds the site before any body is read."""
    if not inquiries_on() or not tables_exist():
        raise ApiError("not_found", "Not found.", status=404)
    account = signed_in(request)
    if account:
        site_id = request.resolver_match.kwargs["site_id"]
        site_for(account, site_id, template=templates.SANTE)
        request.inquiry_site = _owner_site(request, site_id)
    return account


router = Router(tags=["inquiries"], auth=inquiries_site)


class ConnectionIn(Schema):
    model_config = ConfigDict(extra="forbid")
    page_id: str | None = None
    access_token: str | None = None
    label: str | None = None


class ReplyIn(Schema):
    model_config = ConfigDict(extra="forbid")
    text: str
    idempotency_key: str


class ChannelsIn(Schema):
    model_config = ConfigDict(extra="forbid")
    webchat_enabled: bool | None = None
    email_enabled: bool | None = None
    paused_until: datetime | None = None


def _invalid(field, message):
    return ApiError("invalid", message, status=422, fields={field: [message]})


def _out(site):
    row = services.connection_of(site)
    return {
        "provider": InquiryConnection.PANCAKE,
        "status": row.status if row else InquiryConnection.UNSET,
        "page_id": row.external_page_id if row else "",
        "label": row.label if row else "",
        "token_set": bool(row and row.token_encrypted),
        "last_test": (
            {"at": row.last_test_at.isoformat(), "outcome": row.last_test_outcome}
            if row and row.last_test_at else None
        ),
    }


# What the owner is told for each outcome that stops a read. Fixed text; never the provider's.
FAILURES = {
    outcomes.NOT_CONFIGURED: (409, "not_connected", "Connect your Pancake page first."),
    outcomes.INVALID_CREDENTIALS: (409, "reconnect", "Pancake did not accept the saved token. Save a new one."),
    outcomes.EXPIRED_CREDENTIALS: (409, "reconnect", "Pancake did not accept the saved token. Save a new one."),
    outcomes.FORBIDDEN_PAGE: (409, "forbidden_page", "That token cannot reach this page."),
    outcomes.NOT_FOUND: (404, "not_found", "Not found."),
    outcomes.RATE_LIMITED: (429, "rate_limited", "Too many requests to Pancake. Please wait a moment and try again."),
    outcomes.PROVIDER_DOWN: (502, "provider_down", "Pancake is not answering right now. Please try again later."),
    outcomes.BAD_RESPONSE: (502, "provider_error", "Pancake sent an answer we could not read."),
}


def _refuse(code):
    status, name, message = FAILURES.get(code, FAILURES[outcomes.BAD_RESPONSE])
    return ApiError(name, message, status=status)


def _connected(site):
    row = services.connection_of(site)
    if not services.configured(row):
        raise _refuse(outcomes.NOT_CONFIGURED)
    return row


def _check_ref(ref):
    if not REF_RE.match(ref):
        raise _refuse(outcomes.NOT_FOUND)


@router.get("/sites/{site_id}/inquiries/connection/")
def connection_detail(request, site_id: str):
    return _out(request.inquiry_site)


@router.put("/sites/{site_id}/inquiries/connection/")
def connection_update(request, site_id: str, payload: ConnectionIn):
    hit("inq-write", request.account.pk, limit=WRITE_LIMIT, window=WINDOW)
    site = request.inquiry_site
    page = None if payload.page_id is None else payload.page_id.strip()
    label = None if payload.label is None else payload.label.strip()
    token = (payload.access_token or "").strip()
    if page and not PAGE_ID_RE.match(page):
        raise _invalid("page_id", "That page ID does not look right.")
    if label and not LABEL_RE.match(label):
        raise _invalid("label", "A label is at most 60 characters.")
    if token and not TOKEN_RE.match(token):
        raise _invalid("access_token", "That access token does not look right.")
    encrypted = ""
    if token:
        try:
            encrypted = crypto.encrypt(token)
        except crypto.TokenKeyError:
            raise ApiError("unavailable", "Saving a token is not available right now.", status=503) from None
    row = services.connection_of(site) or InquiryConnection(site=site, provider=InquiryConnection.PANCAKE)
    changed = False
    if page is not None and page != row.external_page_id:
        row.external_page_id, changed = page, True
    if encrypted:
        row.token_encrypted, changed = encrypted, True
    if label is not None:
        row.label = label
    if changed:
        row.last_test_at, row.last_test_outcome = None, ""
        row.status = InquiryConnection.ACTIVE if row.token_encrypted and row.external_page_id else InquiryConnection.UNSET
    row.save()
    return _out(site)


@router.delete("/sites/{site_id}/inquiries/connection/")
def connection_clear(request, site_id: str):
    hit("inq-write", request.account.pk, limit=WRITE_LIMIT, window=WINDOW)
    InquiryConnection.objects.filter(site=request.inquiry_site).update(
        token_encrypted="", status=InquiryConnection.UNSET, last_test_at=None, last_test_outcome="",
    )
    return _out(request.inquiry_site)


@router.post("/sites/{site_id}/inquiries/connection/test/")
def connection_test(request, site_id: str):
    hit("inq-test", request.account.pk, limit=TEST_LIMIT, window=WINDOW)
    row = services.connection_of(request.inquiry_site)
    if not services.configured(row):
        raise ApiError("incomplete", "Save a page ID and an access token first.", status=409)
    return {"outcome": services.run_test(row)}


def _when(value):
    """An item's time as an aware datetime, for ordering the merged list. Unreadable sorts last."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=dt_timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_timezone.utc)


def _stored_on():
    return stored_tables_exist()


@router.get("/sites/{site_id}/inquiries/conversations/")
def conversations(request, site_id: str, cursor: str | None = None, stored_cursor: str | None = None):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    if cursor and not CURSOR_RE.match(cursor):
        raise _invalid("cursor", "That cursor does not look right.")
    if stored_cursor and not stored.STORED_CURSOR_RE.match(stored_cursor):
        raise _invalid("stored_cursor", "That cursor does not look right.")
    site = request.inquiry_site
    stored_on = _stored_on()
    if stored_cursor and stored_on:  # a further page of the stored conversations only
        page, stored_next = stored.list_items(site, after=stored_cursor)
        return {"items": page, "next_cursor": None, "stored_next_cursor": stored_next, "sources": {"stored": "ok"}}
    row = services.connection_of(site)
    if services.configured(row):
        code, data = services.list_conversations(row, cursor)
    else:
        code, data = outcomes.NOT_CONFIGURED, None
    if not stored_on:
        if code != outcomes.OK:
            raise _refuse(code)
        return data
    if cursor:  # further Pancake pages; the first stored page came with the first response
        if code != outcomes.OK:
            raise _refuse(code)
        return {**data, "stored_next_cursor": None, "sources": {"pancake": "ok", "stored": "ok"}}
    if code != outcomes.OK and not stored.has_any(site):
        raise _refuse(code)  # nothing stored to show: the list answers as it always did
    stored_items, stored_next = stored.list_items(site)
    items = [*(data["items"] if data else []), *stored_items]
    items.sort(key=lambda item: _when(item.get("updated_at")), reverse=True)
    return {
        "items": items, "next_cursor": data["next_cursor"] if data else None, "stored_next_cursor": stored_next,
        "sources": {"pancake": "ok" if code == outcomes.OK else code, "stored": "ok"},
    }


@router.get("/sites/{site_id}/inquiries/conversations/{ref}/")
def conversation_thread(request, site_id: str, ref: str, cursor: str | None = None):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    _check_ref(ref)
    if cursor and not CURSOR_RE.match(cursor):
        raise _invalid("cursor", "That cursor does not look right.")
    if stored.is_stored_ref(ref):
        data = stored.thread(request.inquiry_site, ref) if _stored_on() else None
        if data is None:
            raise _refuse(outcomes.NOT_FOUND)
        return data
    code, data = services.thread(_connected(request.inquiry_site), ref, cursor)
    if code != outcomes.OK:
        raise _refuse(code)
    return data


@router.post("/sites/{site_id}/inquiries/conversations/{ref}/reply/")
def conversation_reply(request, site_id: str, ref: str, payload: ReplyIn):
    hit("inq-reply", request.account.pk, limit=REPLY_LIMIT, window=WINDOW)
    _check_ref(ref)
    text = payload.text.strip()
    if not text:
        raise _invalid("text", "Write a reply first.")
    if len(text) > MAX_REPLY:
        raise _invalid("text", f"A reply is at most {MAX_REPLY} characters.")
    if not KEY_RE.match(payload.idempotency_key):
        raise _invalid("idempotency_key", "That key does not look right.")
    if stored.is_stored_ref(ref):
        if not _stored_on():
            raise _refuse(outcomes.NOT_FOUND)
        code, data = stored.reply(request.inquiry_site, request.account, ref, text, payload.idempotency_key)
        if code == stored.CLOSED_CODE:
            raise ApiError("closed", "This conversation is closed. Reopen it to reply.", status=409)
    else:
        code, data = services.reply(
            _connected(request.inquiry_site), request.account, ref, text, payload.idempotency_key,
        )
    if code == "idempotency_conflict":
        raise ApiError("conflict", "That key was already used for another conversation.", status=409)
    if code != outcomes.OK:
        raise _refuse(code)
    return data


# -- stored channels: settings, close and reopen, read -----------------------------------------

def _channels_out(site):
    return {**stored.settings_out(stored.channel_settings(site)), "webchat_available": bool(settings.INQUIRY_WEBCHAT_ENABLED),
            "contact_inbox_available": contact_inbox_on()}


def _need_stored():
    if not _stored_on():
        raise _refuse(outcomes.NOT_FOUND)


@router.get("/sites/{site_id}/inquiries/channels/")
def channels_detail(request, site_id: str):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    _need_stored()
    return _channels_out(request.inquiry_site)


@router.put("/sites/{site_id}/inquiries/channels/")
def channels_update(request, site_id: str, payload: ChannelsIn):
    hit("inq-write", request.account.pk, limit=WRITE_LIMIT, window=WINDOW)
    _need_stored()
    set_pause = "paused_until" in payload.model_fields_set
    until = payload.paused_until
    if until is not None:
        until = until if until.tzinfo else until.replace(tzinfo=dt_timezone.utc)
        now = timezone.now()
        if until <= now or until > now + stored.MAX_PAUSE:
            raise _invalid("paused_until", "Pick a time in the future, within 90 days.")
    stored.update_settings(
        request.inquiry_site, webchat_enabled=payload.webchat_enabled, email_enabled=payload.email_enabled,
        paused_until=until, set_pause=set_pause,
    )
    return _channels_out(request.inquiry_site)


def _stored_change(code, data):
    if code == outcomes.NOT_FOUND:
        raise _refuse(outcomes.NOT_FOUND)
    if code == stored.CONFLICT_CODE:
        raise ApiError("conflict", "That visitor already has another open conversation.", status=409)
    return data


@router.post("/sites/{site_id}/inquiries/conversations/{ref}/close/")
def conversation_close(request, site_id: str, ref: str):
    hit("inq-status", request.account.pk, limit=REPLY_LIMIT, window=WINDOW)
    _check_ref(ref)
    _need_stored()
    return _stored_change(*stored.set_status(request.inquiry_site, ref, "closed"))


@router.post("/sites/{site_id}/inquiries/conversations/{ref}/reopen/")
def conversation_reopen(request, site_id: str, ref: str):
    hit("inq-status", request.account.pk, limit=REPLY_LIMIT, window=WINDOW)
    _check_ref(ref)
    _need_stored()
    return _stored_change(*stored.set_status(request.inquiry_site, ref, "open"))


@router.post("/sites/{site_id}/inquiries/conversations/{ref}/read/")
def conversation_read(request, site_id: str, ref: str):
    hit("inq-mark", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    _check_ref(ref)
    _need_stored()
    return _stored_change(*stored.mark_read(request.inquiry_site, ref))
