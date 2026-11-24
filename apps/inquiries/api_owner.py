"""The Subscriber's customer inquiries, under /v1/sites/{site_id}/inquiries/:

    GET    .../conversations/?stored_cursor=&q=&status=&unread=&answered= -> {items, stored_next_cursor, unread_total}
                                                 (`q`: 2 to 80 characters, else 422 `invalid_query`; matches the name,
                                                 contact email or any message text of this site's conversations)
                                                 (`status`: `open` or `closed` keeps only those; missing or empty is all,
                                                 anything else 422 `invalid_status`; `unread_total` is never filtered)
                                                 (`unread`: `1` keeps only unread, `0` only read; missing or empty is all,
                                                 anything else 422 `invalid_unread`; combines with `q` and `status`)
                                                 (`answered`: `answered` or `unanswered` filters, `all` or empty is all, else 422
                                                 `invalid_answered`; every item has `answered`: latest message is the store's and
                                                 not failed or bounced)
    GET    .../unread/                          -> {count}  (this site's unread conversations)
    GET    .../conversations/{ref}/             -> the thread, whether a reply may be sent, the replies sent
    POST   .../conversations/{ref}/reply/       {text, idempotency_key} -> {state, error_code, created_at}
    GET    .../channels/                        -> {webchat_enabled, email_enabled, paused_until, chat_limit_reached, webchat_available,
                                                 contact_inbox_available}
    PUT    .../channels/                        {webchat_enabled?, email_enabled?, paused_until?}  (null clears the pause)
    POST   .../conversations/{ref}/close/       -> {ref, status, unread}
    POST   .../conversations/{ref}/reopen/      -> {ref, status, unread}
    POST   .../conversations/{ref}/read/        -> {ref, status, unread}

Every conversation is a stored channel's (website chat, refs `wc_`; the contact form as email,
refs `em_`), read from our own tables. A ref that is not a stored ref is not this site's: 404.
There is no provider connection and no route for one.

Paging. The list carries the newest 30 conversations and `stored_next_cursor` (null when there are
no more). Pass it back as `?stored_cursor=` to get the next page. The cursor is opaque (last
message time and id); a malformed one is 422. Before inquiries.0002 is applied the list is empty.

While INQUIRIES_ENABLED is off, or before the tables exist, every route answers 404, signed in or
not. The site is found in the router's sign-in step through site_for(account, site_id,
template=SANTE), so a missing, removed, foreign or non-Santé site answers 404 before any body is
read; then the Billing-only rule (403 `billing_only`).
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
from apps.inquiries import outcomes, sms_inbox, store_bulk, stored
from apps.inquiries.models import InquirySmsSettings
from apps.inquiries.switch import (
    contact_inbox_on, inquiries_on, sms_ready, store_bulk_ready, stored_tables_exist, tables_exist,
)
from apps.sites import templates
from apps.sites.api import _owner_site
from apps.sites.services import site_for

WRITE_LIMIT, READ_LIMIT, REPLY_LIMIT, WINDOW = 20, 1200, 120, 3600  # per account per hour
MAX_REPLY = 2000
REF_RE = re.compile(r"^[A-Za-z0-9_.:-]{3,128}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


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


class ComposeIn(Schema):
    model_config = ConfigDict(extra="forbid")
    member_id: str
    text: str
    idempotency_key: str


class ReplyIn(Schema):
    model_config = ConfigDict(extra="forbid")
    text: str
    idempotency_key: str


class BulkAudienceIn(Schema):
    model_config = ConfigDict(extra="forbid")
    audience: str = "all"


class BulkTextIn(Schema):
    model_config = ConfigDict(extra="forbid")
    text: str


class BulkTestIn(Schema):
    model_config = ConfigDict(extra="forbid")
    text: str
    idempotency_key: str


class BulkConfirmIn(Schema):
    model_config = ConfigDict(extra="forbid")
    text: str
    audience: str = "all"
    idempotency_key: str
    confirm: bool = False
    expected_count: int | None = None


class ChannelsIn(Schema):
    model_config = ConfigDict(extra="forbid")
    webchat_enabled: bool | None = None
    email_enabled: bool | None = None
    sms_enabled: bool | None = None
    paused_until: datetime | None = None


def _invalid(field, message):
    return ApiError("invalid", message, status=422, fields={field: [message]})


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def _check_ref(ref):
    if not REF_RE.match(ref):
        raise _not_found()


def _stored_on():
    return stored_tables_exist()


@router.get("/sites/{site_id}/inquiries/conversations/")
def conversations(request, site_id: str, stored_cursor: str | None = None, q: str | None = None,
                  status: str | None = None, unread: str | None = None,
                  answered: str | None = None):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    if stored_cursor and not stored.STORED_CURSOR_RE.match(stored_cursor):
        raise _invalid("stored_cursor", "That cursor does not look right.")
    query = None
    if q is not None:
        query = stored.valid_query(q)
        if query is None:
            message = f"Search needs {stored.SEARCH_MIN} to {stored.SEARCH_MAX} characters."
            raise ApiError("invalid_query", message, status=422, fields={"q": [message]})
    if status not in (None, "", "open", "closed"):
        message = "Status must be open or closed."
        raise ApiError("invalid_status", message, status=422, fields={"status": [message]})
    if unread not in (None, "", "1", "0"):
        message = "Unread must be 1 or 0."
        raise ApiError("invalid_unread", message, status=422, fields={"unread": [message]})
    if answered not in (None, "", "all", "answered", "unanswered"):
        message = "Answered must be all, answered or unanswered."
        raise ApiError("invalid_answered", message, status=422, fields={"answered": [message]})
    if not _stored_on():
        return {"items": [], "stored_next_cursor": None, "unread_total": 0}
    site = request.inquiry_site
    items, next_cursor = stored.list_items(site, after=stored_cursor or None, query=query, status=status or None,
                                           unread=None if not unread else unread == "1",
                                           answered=None if answered in (None, "", "all") else answered == "answered")
    return {"items": items, "stored_next_cursor": next_cursor, "unread_total": stored.unread_count(site)}


@router.get("/sites/{site_id}/inquiries/unread/")
def unread(request, site_id: str):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    return {"count": stored.unread_count(request.inquiry_site) if _stored_on() else 0}


@router.get("/sites/{site_id}/inquiries/conversations/{ref}/")
def conversation_thread(request, site_id: str, ref: str):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    _check_ref(ref)
    data = stored.thread(request.inquiry_site, ref) if stored.is_stored_ref(ref) and _stored_on() else None
    if data is None:
        raise _not_found()
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
    if not stored.is_stored_ref(ref) or not _stored_on():
        raise _not_found()
    code, data = stored.reply(request.inquiry_site, request.account, ref, text, payload.idempotency_key)
    if code == stored.CLOSED_CODE:
        raise ApiError("closed", "This conversation is closed. Reopen it to reply.", status=409)
    if code == "idempotency_conflict":
        raise ApiError("conflict", "That key was already used for another conversation.", status=409)
    if code != outcomes.OK:
        raise _not_found()
    return data


# -- text (SMS) conversations with the store's members ------------------------------------------

def _need_sms():
    if not sms_ready() or not _stored_on():
        raise _not_found()


@router.post("/sites/{site_id}/inquiries/sms/compose/")
def sms_compose(request, site_id: str, payload: ComposeIn):
    hit("inq-reply", request.account.pk, limit=REPLY_LIMIT, window=WINDOW)
    _need_sms()
    if not KEY_RE.match(payload.idempotency_key):
        raise _invalid("idempotency_key", "That key does not look right.")
    if not REF_RE.match(payload.member_id):
        raise _not_found()
    code, data = sms_inbox.compose(
        request.inquiry_site, request.account, payload.member_id, payload.text, payload.idempotency_key,
    )
    if code == "idempotency_conflict":
        raise ApiError("conflict", "That key was already used for another conversation.", status=409)
    if code != outcomes.OK:
        raise _not_found()
    return data


@router.get("/sites/{site_id}/inquiries/sms/status/")
def sms_status(request, site_id: str):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    _need_sms()
    return sms_inbox.status(request.inquiry_site)


# -- bulk text to the store's own members (STORE-BULK) ------------------------------------------

def _need_bulk():
    if not store_bulk_ready() or not _stored_on():
        raise _not_found()


def _bulk_key(value):
    if not KEY_RE.match(value):
        raise _invalid("idempotency_key", "That key does not look right.")
    return value


@router.get("/sites/{site_id}/inquiries/sms/bulk/status/")
def bulk_status(request, site_id: str):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    _need_bulk()
    return store_bulk.status(request.inquiry_site)


@router.post("/sites/{site_id}/inquiries/sms/bulk/audience/")
def bulk_audience(request, site_id: str, payload: BulkAudienceIn):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    _need_bulk()
    return store_bulk.audience(request.inquiry_site, payload.audience)


@router.post("/sites/{site_id}/inquiries/sms/bulk/preview/")
def bulk_preview(request, site_id: str, payload: BulkTextIn):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    _need_bulk()
    return store_bulk.preview(request.inquiry_site, payload.text)


@router.post("/sites/{site_id}/inquiries/sms/bulk/test/")
def bulk_test(request, site_id: str, payload: BulkTestIn):
    hit("inq-reply", request.account.pk, limit=REPLY_LIMIT, window=WINDOW)
    _need_bulk()
    return store_bulk.test_send(request.inquiry_site, request.account, payload.text, _bulk_key(payload.idempotency_key))


@router.post("/sites/{site_id}/inquiries/sms/bulk/batches/", response={200: dict, 201: dict})
def bulk_confirm(request, site_id: str, payload: BulkConfirmIn):
    hit("inq-reply", request.account.pk, limit=REPLY_LIMIT, window=WINDOW)
    _need_bulk()
    data = payload.model_dump()
    data["idempotency_key"] = _bulk_key(payload.idempotency_key)
    if payload.expected_count is not None and payload.expected_count < 0:
        raise _invalid("expected_count", "That count does not look right.")
    batch, created = store_bulk.confirm(request.inquiry_site, request.account, data)
    return (201 if created else 200), batch


@router.get("/sites/{site_id}/inquiries/sms/bulk/batches/")
def bulk_batches(request, site_id: str, cursor: str | None = None, limit: int | None = None):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    _need_bulk()
    items, following = store_bulk.list_batches(request.inquiry_site, cursor=cursor or "", limit=limit)
    return {"items": items, "next_cursor": following}


@router.get("/sites/{site_id}/inquiries/sms/bulk/batches/{batch_id}/")
def bulk_batch(request, site_id: str, batch_id: str, cursor: str | None = None, limit: int | None = None):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    _need_bulk()
    return store_bulk.open_batch(request.inquiry_site, batch_id, cursor=cursor or "", limit=limit)


@router.post("/sites/{site_id}/inquiries/sms/bulk/batches/{batch_id}/stop/")
def bulk_stop(request, site_id: str, batch_id: str):
    hit("inq-reply", request.account.pk, limit=REPLY_LIMIT, window=WINDOW)
    _need_bulk()
    return store_bulk.stop(request.inquiry_site, request.account, batch_id)


# -- settings, close and reopen, read -----------------------------------------

def _channels_out(site):
    return {**stored.settings_out(stored.channel_settings(site)), "webchat_available": bool(settings.INQUIRY_WEBCHAT_ENABLED),
            "contact_inbox_available": contact_inbox_on(),
            "sms_available": sms_ready(), "sms_enabled": bool(sms_ready() and sms_inbox.enabled(site))}


def _need_stored():
    if not _stored_on():
        raise _not_found()


@router.get("/sites/{site_id}/inquiries/channels/")
def channels_detail(request, site_id: str):
    hit("inq-read", request.account.pk, limit=READ_LIMIT, window=WINDOW)
    _need_stored()
    return _channels_out(request.inquiry_site)


@router.put("/sites/{site_id}/inquiries/channels/")
def channels_update(request, site_id: str, payload: ChannelsIn):
    hit("inq-write", request.account.pk, limit=WRITE_LIMIT, window=WINDOW)
    _need_stored()
    if payload.sms_enabled is not None and not sms_ready():
        raise _not_found()  # the text switch is off, or its tables are not there yet
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
    if payload.sms_enabled is not None:
        InquirySmsSettings.objects.update_or_create(site=request.inquiry_site, defaults={"enabled": payload.sms_enabled})
    return _channels_out(request.inquiry_site)


def _stored_change(code, data):
    if code == outcomes.NOT_FOUND:
        raise _not_found()
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
