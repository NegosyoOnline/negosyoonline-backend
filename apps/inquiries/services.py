"""The inquiry flows: connection test, conversation list, thread and reply.

Every function takes the Site the router already resolved for the signed-in owner, never a raw
conversation id: a conversation ref is an opaque string that is only ever sent to the provider
with the credentials of this site's own connection. A reply's text is passed to the provider
and forgotten: no row, log line or exception holds it."""
import hashlib

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.inquiries import crypto, limiter, outcomes
from apps.inquiries.adapters import get as get_adapter
from apps.inquiries.adapters.base import Credentials, Result
from apps.inquiries.models import InquiryConnection, InquiryConversation, InquiryReply

CACHE_SECONDS = 25  # absorbs refreshes under the provider's rate limit
LIST_LIMIT = 30
RECENT_REPLIES = 20


def connection_of(site):
    return InquiryConnection.objects.filter(site=site, provider=InquiryConnection.PANCAKE).first()


def configured(connection):
    """True when a provider call could be tried: an adapter, a page id and a saved token."""
    return bool(
        connection and get_adapter(connection.provider) and connection.external_page_id
        and connection.token_encrypted and connection.status != InquiryConnection.DISABLED
    )


def _credentials(connection):
    try:
        return Credentials(connection.external_page_id, crypto.decrypt(connection.token_encrypted))
    except crypto.TokenKeyError:
        return None


def _mark_invalid(connection):
    if connection.status != InquiryConnection.INVALID:
        connection.status = InquiryConnection.INVALID
        connection.save(update_fields=["status", "updated_at"])


def call(connection, method, *args):
    """One adapter call for this connection. Returns (Result, blocked_here): blocked_here is True
    when our own per-connection limiter refused the call and the provider was not contacted.
    A connection already marked invalid answers without calling the provider."""
    adapter = get_adapter(connection.provider)
    if adapter is None or not configured(connection):
        return Result(outcomes.NOT_CONFIGURED), False
    if connection.status == InquiryConnection.INVALID:
        return Result(outcomes.INVALID_CREDENTIALS), False
    creds = _credentials(connection)
    if creds is None:
        return Result(outcomes.NOT_CONFIGURED), False
    result = Result(outcomes.RATE_LIMITED)
    for _ in range(2):  # one bounded retry when the provider itself says 429
        if not limiter.acquire(connection.pk):
            return Result(outcomes.RATE_LIMITED), True
        result = getattr(adapter, method)(creds, *args)
        if result.code != outcomes.RATE_LIMITED:
            break
    if result.code in outcomes.CREDENTIAL_FAILURES:
        _mark_invalid(connection)
    return result, False


def run_test(connection):
    """Test the saved connection and record only the outcome code. A connection marked invalid
    is tried again, because a test is how the owner finds out a new token works."""
    was_invalid = connection.status == InquiryConnection.INVALID
    if was_invalid:
        connection.status = InquiryConnection.ACTIVE
    result, _ = call(connection, "test_connection")
    fields = ["last_test_at", "last_test_outcome", "updated_at"]
    connection.last_test_at, connection.last_test_outcome = timezone.now(), result.code
    if result.ok:
        connection.status = InquiryConnection.ACTIVE
        fields.append("status")
    elif was_invalid:
        connection.status = InquiryConnection.INVALID  # still not working; the row already says so
    connection.save(update_fields=fields)
    return result.code


def _key(connection, kind, *parts):
    digest = hashlib.sha256("|".join((connection.external_page_id, *parts)).encode()).hexdigest()[:24]
    return f"inq:{connection.site_id}:{connection.pk}:{kind}:{digest}"


def list_conversations(connection, cursor):
    """(outcome code, {items, next_cursor} or None). Fetched from the provider, cached briefly."""
    key = _key(connection, "list", cursor or "")
    cached = cache.get(key)
    if cached is not None:
        return outcomes.OK, cached
    result, _ = call(connection, "list_conversations", cursor, LIST_LIMIT)
    if not result.ok:
        return result.code, None
    page = result.value
    data = {
        "items": [
            {
                "ref": c.ref, "display_name": c.display_name, "snippet": c.snippet,
                "updated_at": c.updated_at, "channel": c.channel,
            }
            for c in page.items
        ],
        "next_cursor": page.next_cursor,
    }
    cache.set(key, data, CACHE_SECONDS)
    return outcomes.OK, data


def _handle(connection, adapter, ref, name=""):
    """The thin conversation row a reply points at, made on first use."""
    handle, _ = InquiryConversation.objects.get_or_create(
        site=connection.site, provider=connection.provider, external_id=ref,
        defaults={
            "connection": connection, "stores_history": adapter.stores_history, "display_name": name[:120],
        },
    )
    return handle


def _reply_out(reply):
    return {"state": reply.state, "error_code": reply.error_code, "created_at": reply.created_at.isoformat()}


def thread(connection, ref, cursor):
    """(outcome code, thread dict or None)."""
    if not get_adapter(connection.provider).ref_ok(connection.external_page_id, ref):
        return outcomes.NOT_FOUND, None
    key = _key(connection, "thread", ref, cursor or "")
    data = cache.get(key)
    if data is None:
        result, _ = call(connection, "get_thread", ref, cursor)
        if not result.ok:
            return result.code, None
        found = result.value
        adapter = get_adapter(connection.provider)
        data = {
            "ref": found.ref, "display_name": found.display_name,
            "messages": [
                {
                    "id": m.id, "direction": m.direction, "sender_kind": m.sender_kind, "text": m.text,
                    "attachments": list(m.attachments), "at": m.at,
                }
                for m in found.messages
            ],
            "reply": {"open": found.window.open, "reason": found.window.reason},
            "provider_url": adapter.provider_url(_credentials(connection), ref),
            "next_cursor": found.next_cursor,
        }
        cache.set(key, data, CACHE_SECONDS)
    handle = _handle(connection, get_adapter(connection.provider), ref, data["display_name"])
    InquiryConversation.objects.filter(pk=handle.pk).update(
        reply_open=data["reply"]["open"], reply_checked_at=timezone.now(),
    )
    replies = InquiryReply.objects.filter(conversation=handle)[:RECENT_REPLIES]
    return outcomes.OK, {**data, "replies": [_reply_out(r) for r in replies]}


def reply(connection, account, ref, text, idempotency_key):
    """Send one human reply as the page. Returns (outcome code, dict). The code is NOT_CONFIGURED
    when nothing could be tried (no audit row); otherwise the dict holds the audit row's state."""
    site = connection.site
    if not configured(connection):
        return outcomes.NOT_CONFIGURED, None
    if not get_adapter(connection.provider).ref_ok(connection.external_page_id, ref):
        return outcomes.NOT_FOUND, None
    existing = InquiryReply.objects.filter(site=site, idempotency_key=idempotency_key).select_related("conversation").first()
    if existing:
        return _earlier(existing, ref)
    adapter = get_adapter(connection.provider)
    handle = _handle(connection, adapter, ref)
    try:
        with transaction.atomic():
            row = InquiryReply.objects.create(
                site=site, conversation=handle, author=account, idempotency_key=idempotency_key,
                body_length=len(text),
            )
    except IntegrityError:  # the same key arrived twice at once: the first one wins
        existing = InquiryReply.objects.select_related("conversation").get(site=site, idempotency_key=idempotency_key)
        return _earlier(existing, ref)
    try:
        _deliver(connection, handle, row, ref, text)
    except Exception:  # noqa: BLE001 - leave an honest row; the text is not in any message
        row.state, row.error_code = InquiryReply.FAILED, outcomes.BAD_RESPONSE
        row.save(update_fields=["state", "error_code"])
    return outcomes.OK, _reply_out(row)


def _earlier(existing, ref):
    if existing.conversation.external_id != ref:
        return "idempotency_conflict", None
    return outcomes.OK, _reply_out(existing)


def _finish(row, state, code="", ref=""):
    row.state, row.error_code, row.provider_message_ref = state, code, ref
    row.save(update_fields=["state", "error_code", "provider_message_ref"])


def _deliver(connection, handle, row, ref, text):
    # The window is the provider's own answer, read fresh immediately before the send.
    result, blocked_here = call(connection, "get_thread", ref, None)
    if not result.ok:
        if blocked_here:
            return _finish(row, InquiryReply.BLOCKED_RATE, outcomes.RATE_LIMITED)
        return _finish(row, InquiryReply.FAILED, result.code)
    window = result.value.window
    InquiryConversation.objects.filter(pk=handle.pk).update(
        reply_open=window.open, reply_checked_at=timezone.now(),
    )
    if not window.open:
        return _finish(row, InquiryReply.BLOCKED_WINDOW, outcomes.WINDOW_CLOSED)
    result, blocked_here = call(connection, "send_reply", ref, text)
    if result.ok:
        cache.delete(_key(connection, "thread", ref, ""))
        return _finish(row, InquiryReply.SENT, "", result.value or "")
    if blocked_here:
        return _finish(row, InquiryReply.BLOCKED_RATE, outcomes.RATE_LIMITED)
    return _finish(row, InquiryReply.FAILED, result.code)
