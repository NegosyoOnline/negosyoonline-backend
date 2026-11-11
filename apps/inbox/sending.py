"""Replying and composing. Both go through the one send function, `send_sms`. A staff text
is queued, its inbox row written and its audit entry made in one database transaction.

No log line, cache entry or audit entry made here ever holds a full number or a text."""
import re
from dataclasses import dataclass
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.common.errors import ApiError
from apps.common.ids import is_public_id
from apps.common.masking import mask_mobile
from apps.common.phones import normalise_ph_mobile
from apps.inbox import reading, storing
from apps.inbox.models import Conversation, ConversationMessage, InboxAccess
from apps.inbox.switch import SMS
from apps.notifications import services
from apps.operators import audit

HOURLY_LIMIT = 60  # texts one staff user may send in an hour
REQUEST_KEPT = 24 * 3600  # seconds a request id is remembered
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
OUT = ConversationMessage.Direction.OUT
Kind = ConversationMessage.Kind

_OFFLINE = (503, "sms_unavailable", "The gateway phone is offline. Nothing was sent.")
REFUSALS = {
    "paused": (503, "sms_paused", "Text sending is stopped. Nothing was sent."),
    "gateway_offline": _OFFLINE,
    "gateway_off": _OFFLINE,
    "staff_limit": (
        429, "limit_reached",
        f"Today's limit for staff texts is reached. {services.SMS_RESERVED_FOR_CODES} texts "
        "are kept for sign-in codes.",
    ),
    "rate_limited": (429, "rate_limited", "Too many texts in the last hour. Please wait and try again."),
}


@dataclass
class _Sent:
    reason: str = ""
    record: object = None  # the send record, when one was written
    message: object = None  # the inbox row, when the text was queued
    conversation: object = None
    started: bool = False  # a composed text made a new conversation


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def _refused(reason):
    status, code, words = REFUSALS.get(reason, _OFFLINE)
    return ApiError(code, words, status=status)


def _checked(scope, text, request_id):
    if not scope.can_send:
        raise ApiError("forbidden", "You may read the inbox but not send texts.", status=403)
    if not isinstance(request_id, str) or not _REQUEST_ID.match(request_id):
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"request_id": ["Send a request id of 16 to 64 letters and digits."]},
        )
    problem = services.sms_text_problem(text)
    if problem:
        raise ApiError("invalid_text", problem, fields={"text": [problem]})


def _answer(message):
    return {"conversation": message.conversation.public_id, "message": reading.shown_message(message)}


def _once(scope, request_id, send):
    """Run `send` unless this staff user's request id was used already. Answers (the text as
    shown, whether this call sent it)."""
    key = f"inbox:request:{scope.actor.pk}:{request_id}"
    if not cache.add(key, "", REQUEST_KEPT):
        earlier = cache.get(key)
        message = earlier and ConversationMessage.objects.filter(
            conversation__in=scope.conversations, public_id=earlier
        ).first()
        if not message:
            raise ApiError("request_in_progress", "That text is already being sent.", status=409)
        return _answer(message), False
    try:
        message = send()
    except BaseException:
        cache.delete(key)  # refused or failed: the same id may be tried again
        raise
    cache.set(key, message.public_id, REQUEST_KEPT)
    return _answer(message), True


def _lock(scope):
    """One send at a time per staff user, so the hourly count cannot be passed by two calls."""
    if not InboxAccess.objects.select_for_update().filter(operator=scope.actor, platform=SMS).exists():
        raise _not_found()


def _queue(scope, conversation, number, text, kind, purpose):
    """Queue the text and write its inbox row. Inside the caller's transaction."""
    actor, now = scope.actor, timezone.now()
    written = ConversationMessage.objects.filter(
        author=actor, direction=OUT, created_at__gt=now - timedelta(hours=1)
    )
    if written.count() >= HOURLY_LIMIT:
        return _Sent(reason="rate_limited", conversation=conversation)
    result = services.send_sms(
        to=number, purpose=purpose, text=text, ttl=services.SMS_STAFF_TEXT_LIFETIME
    )
    if result.outcome != services.SMS_QUEUED:
        return _Sent(reason=result.reason, record=result.message, conversation=conversation)
    started = False
    if conversation is None:
        line = services.sms_line()
        conversation, started = Conversation.objects.get_or_create(
            platform=SMS, line=line, contact=number, **storing.owner_of(line),
            defaults={"can_reply": True, "last_message_at": now},
        )
    message = ConversationMessage.objects.create(
        conversation=conversation, platform=SMS, direction=OUT, body=text,
        gateway_ref=result.message.public_id, author=actor, kind=kind,
    )
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=now)
    return _Sent(record=result.message, message=message, conversation=conversation, started=started)


def _detail(scope, sent, text):
    detail = {
        "platform": scope.platform, "outcome": "refused" if sent.reason else "queued",
        "length": len(text),
    }
    if sent.reason:
        detail["reason"] = sent.reason
    if sent.message:
        detail["message"] = sent.message.public_id
    if sent.record:
        detail["send_record"] = sent.record.public_id
    return detail


def reply(scope, public_id, text, request_id):
    """Send `text` to the other party of one conversation in the scope."""
    _checked(scope, text, request_id)
    if not is_public_id(public_id, "conv"):
        raise _not_found()

    def send():
        with transaction.atomic():
            _lock(scope)
            conversation = scope.conversations.select_for_update().filter(public_id=public_id).first()
            if conversation is None:
                raise _not_found()
            if not conversation.can_reply:
                raise ApiError("cannot_reply", "This sender cannot be replied to.")
            sent = _queue(scope, conversation, conversation.contact, text, Kind.REPLY, "inbox_reply")
            audit.record(
                scope.actor, "sms_inbox_reply", target_type=reading.TARGET,
                target_id=conversation.public_id, detail=_detail(scope, sent, text),
                personal={"contact": mask_mobile(conversation.contact)},
            )
        if sent.reason:
            raise _refused(sent.reason)
        return sent.message

    return _once(scope, request_id, send)


def compose(scope, to, text, request_id):
    """Send `text` to one Philippine mobile, in its conversation or a new one."""
    _checked(scope, text, request_id)
    try:
        number = normalise_ph_mobile(to if isinstance(to, str) else "")
    except ValueError:
        words = "Enter a valid Philippine mobile number."
        raise ApiError("invalid_mobile", words, fields={"to": [words]}) from None

    def send():
        with transaction.atomic():
            _lock(scope)
            sent = _queue(scope, None, number, text, Kind.COMPOSE, "inbox_compose")
            detail = _detail(scope, sent, text)
            detail["new_conversation"] = sent.started
            audit.record(
                scope.actor, "sms_inbox_compose", target_type=reading.TARGET,
                target_id=sent.conversation.public_id if sent.conversation else "",
                detail=detail, personal={"contact": mask_mobile(number)},
            )
        if sent.reason:
            raise _refused(sent.reason)
        return sent.message

    return _once(scope, request_id, send)

