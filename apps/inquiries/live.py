"""Live Inbox push (LIVE-INBOX, z8uq9mg7ah): the publish side.

Every stored change to a conversation publishes one small event on the Redis channel
`nego:inbox:site:{site_id}`, once the database transaction has committed. The stream view
(apps/inquiries/stream.py) forwards it to the owner's open Inbox as server-sent events, and the
Inbox then reads the list and thread through the normal routes.

An event carries the conversation ref, the last message time and a kind, and nothing else: never
message text, a name, a PSID, a page id, an email or a token. A failure (no REDIS_URL, Redis down,
a slow socket) is swallowed with one fixed-code warning; it never fails a request or a webhook.
"""
import json
import logging
from datetime import timezone as dt_timezone

import redis
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

CONVERSATION_UPDATED = "conversation_updated"
MESSAGE_STATUS = "message_status"
KIND_MESSAGE = "message"
KIND_STATUS = "status"
SOCKET_TIMEOUT = 1  # seconds: a publish never holds a request longer than this

_clients = {}


def channel(site_id):
    return f"nego:inbox:site:{site_id}"


def iso(value):
    """ISO UTC with a Z, or None."""
    if value is None:
        return None
    return value.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _client():
    url = settings.REDIS_URL
    if not url:
        return None
    client = _clients.get(url)
    if client is None:
        client = _clients[url] = redis.Redis.from_url(
            url, socket_timeout=SOCKET_TIMEOUT, socket_connect_timeout=SOCKET_TIMEOUT,
        )
    return client


def _send(site_id, payload):
    try:
        client = _client()
        if client is None:  # no Redis here (local work): live push is simply off
            return
        client.publish(channel(site_id), json.dumps(payload, separators=(",", ":")))
    except Exception as error:  # noqa: BLE001 - only the class name is logged
        logger.warning("Inbox live publish failed (%s)", type(error).__name__)


def publish(site, ref, kind, last_message_at=None, *, event=CONVERSATION_UPDATED):
    """Publish one event for `ref` after the surrounding transaction commits (at once when there is
    none). `kind` is `message` or `status`; a `message_status` event carries the ref only."""
    if event == MESSAGE_STATUS:
        payload = {"type": MESSAGE_STATUS, "ref": ref}
    else:
        payload = {
            "type": CONVERSATION_UPDATED, "ref": ref, "last_message_at": iso(last_message_at), "kind": kind,
        }
    site_id = site.public_id
    transaction.on_commit(lambda: _send(site_id, payload))
