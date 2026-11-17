"""Live Inbox push (LIVE-INBOX, z8uq9mg7ah): GET /v1/sites/{site_id}/inquiries/stream/.

A plain Django async view (server-sent events) that sits ahead of the ninja `/v1/` include. It
answers with exactly the inbox routes' sign-in and site rules, before any stream starts; then it
holds the owner's connection open and forwards what apps/inquiries/live.py publishes on
`nego:inbox:site:{site_id}`. The wire format is the contract's: `retry`, `ready`, a `: ping` comment
every 15 s, `conversation_updated` and `message_status` events that carry no message text or
personal data. No `id:` lines, no replay: the Inbox reads the list again on every `ready`.

At most MAX_STREAMS open streams per account across all workers (a Redis sorted set whose members
expire, so a crashed worker's streams free themselves). The stream ends after LIFETIME seconds, and
at any ping where the session or the site is no longer the owner's.
"""
import asyncio
import json
import logging
import secrets
import time

import redis.asyncio as aredis
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connections
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_GET

from apps.common.errors import ApiError, error_body
from apps.inquiries import live
from apps.inquiries.api_owner import inquiries_site

logger = logging.getLogger(__name__)

PING_SECONDS = 15
LIFETIME_SECONDS = 600
MAX_STREAMS = 5
RETRY_MS = 5000
CONNECT_TIMEOUT = 1  # seconds, to Redis
START_GRACE = 30  # a stream's slot is held this long before its first ping if it never starts
SLOT_TTL = PING_SECONDS * 3  # ... and this long after each ping
_EVENTS = (live.CONVERSATION_UPDATED, live.MESSAGE_STATUS)

# KEYS[1] zset of this account's open streams (member = stream id, score = when its slot expires);
# ARGV: now, expiry, limit, member, key ttl. Returns 1 when a slot was taken, 0 when over the cap.
_TAKE_SLOT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""


def _error(code, message, status):
    return JsonResponse(error_body(code, message), status=status)


def _slot_key(account_id):
    return f"nego:inbox:conn:{account_id}"


def _drop_connections():
    """Close this thread's database connections so an open stream holds none between pings. A
    connection inside a transaction (a test's) is left alone."""
    for connection in connections.all():
        if not connection.in_atomic_block:
            connection.close()


def _access(request, site_id):
    """The inbox routes' sign-in step, run in a thread. Returns (account id, None) or (None, response).
    The database connection is closed again so a long stream holds none."""
    try:
        account = inquiries_site(request)
        if not account:
            return None, _error("unauthenticated", "Sign in to continue.", 401)
        return account.pk, None
    except ApiError as error:
        return None, _error(error.code, error.message, error.status)
    finally:
        _drop_connections()


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def _forward(raw):
    """One published message as SSE bytes, or None when it is not one of ours. Only the known
    fields are copied, whatever the message held."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("type") not in _EVENTS or not isinstance(payload.get("ref"), str):
        return None
    if payload["type"] == live.MESSAGE_STATUS:
        return _sse(live.MESSAGE_STATUS, {"ref": payload["ref"]})
    return _sse(live.CONVERSATION_UPDATED, {
        "ref": payload["ref"], "last_message_at": payload.get("last_message_at"), "kind": payload.get("kind"),
    })


class _Stream:
    """The response body: an async iterator that frees its Redis slot and connections however it
    ends (finished, client gone, cancelled) and also when it was never started."""

    def __init__(self, request, site_id, account_id, client, member):
        self.request, self.site_id, self.account_id = request, site_id, account_id
        self.client, self.member = client, member
        self._events = self._run()
        self._closed = False
        self._loop = asyncio.get_running_loop()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._events.__anext__()

    async def aclose(self):
        await self._events.aclose()
        await self._release()

    def close(self):
        """Django calls this (in a thread) when the response is finished. Django closes the
        response's own wrapper, not this iterator, so a stream the client left before its first
        read would otherwise keep its slot until the slot expires. A second release is a no-op."""
        if not self._closed and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(lambda: self._loop.create_task(self._release()))

    async def _release(self):
        if self._closed:
            return
        self._closed = True
        try:
            await asyncio.shield(self._cleanup())
        except Exception as error:  # noqa: BLE001
            logger.warning("Inbox stream cleanup failed (%s)", type(error).__name__)

    async def _cleanup(self):
        try:
            await self.client.zrem(_slot_key(self.account_id), self.member)
        finally:
            await self.client.aclose()

    async def _hold(self, seconds):
        await self.client.zadd(_slot_key(self.account_id), {self.member: time.time() + seconds})

    async def _run(self):
        pubsub = self.client.pubsub()
        deadline = time.monotonic() + LIFETIME_SECONDS
        try:
            await pubsub.subscribe(live.channel(self.site_id))
            await self._hold(SLOT_TTL)
            yield f"retry: {RETRY_MS}\n\n".encode()
            yield _sse("ready", {"v": 1})
            next_ping = time.monotonic() + PING_SECONDS
            while True:
                now = time.monotonic()
                if now >= deadline:
                    return
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=max(0.05, min(next_ping, deadline) - now),
                )
                if message and message.get("type") == "message":
                    chunk = _forward(message.get("data"))
                    if chunk:
                        yield chunk
                if time.monotonic() >= next_ping:
                    _, refused = await sync_to_async(_access)(self.request, self.site_id)
                    if refused is not None:
                        return  # signed out, site gone or Billing-only: the Inbox reconnects and is told why
                    await self._hold(SLOT_TTL)
                    yield b": ping\n\n"
                    next_ping = time.monotonic() + PING_SECONDS
        except (aredis.RedisError, OSError) as error:
            logger.warning("Inbox stream ended (%s)", type(error).__name__)
        finally:
            try:
                await pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass
            await self._release()


@require_GET
async def stream(request, site_id):
    account_id, refused = await sync_to_async(_access)(request, site_id)
    if refused is not None:
        return refused
    url = settings.REDIS_URL
    if not url:
        return _error("stream_unavailable", "Live updates are not available.", 503)
    member = secrets.token_hex(8)
    client = aredis.Redis.from_url(url, socket_timeout=CONNECT_TIMEOUT * 2, socket_connect_timeout=CONNECT_TIMEOUT)
    try:
        now = time.time()
        taken = await client.eval(
            _TAKE_SLOT, 1, _slot_key(account_id),
            now, now + START_GRACE, MAX_STREAMS, member, int(LIFETIME_SECONDS * 2),
        )
    except (aredis.RedisError, OSError) as error:
        logger.warning("Inbox stream unavailable (%s)", type(error).__name__)
        await client.aclose()
        return _error("stream_unavailable", "Live updates are not available.", 503)
    if not taken:
        await client.aclose()
        return _error("too_many_streams", "Too many open Inbox windows. Close one and try again.", 429)
    # After the slot is taken the subscription and the pings use no timeout shorter than a ping.
    client.connection_pool.connection_kwargs["socket_timeout"] = None
    response = StreamingHttpResponse(_Stream(request, site_id, account_id, client, member), content_type="text/event-stream; charset=utf-8")
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response
