"""What a screen may be shown. Masking is done here, by the backend: an answer never carries
a full number, and the list never carries a text. Reading a text always goes through
`open_conversation`, which writes the audit entry in the same transaction."""
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery

from apps.common.errors import ApiError
from apps.common.ids import is_public_id
from apps.common.masking import HIDDEN, mask_mobile
from apps.common.pagination import DEFAULT_LIMIT, MAX_LIMIT
from apps.common.phones import normalise_ph_mobile
from apps.inbox.models import ConversationMessage, DoNotText
from apps.inbox.switch import SMS, dnt_tables_exist
from apps.notifications import services
from apps.operators import audit

TARGET = "conversation"
# A sender with this many digits is somebody's number, whether or not it can be replied to.
NUMBER_DIGITS = 7
MESSAGES_PER_PAGE = 50
OUT = ConversationMessage.Direction.OUT


def is_a_number(sender):
    return sum(character.isdigit() for character in sender) >= NUMBER_DIGITS


def shown_contact(conversation):
    """Every number masked. Only a sender name or a short code is shown as received.

    A Philippine mobile keeps the form used everywhere else. Any other number (a foreign
    mobile, a landline) shows its last 4 characters at most: `mask_mobile` would show all of
    a 10-character number."""
    contact = conversation.contact
    if conversation.can_reply:
        return mask_mobile(contact)
    if not is_a_number(contact):
        return contact
    return HIDDEN + contact[-4:] if len(contact) >= 10 else HIDDEN


def _clamped(limit, default):
    return max(1, min(limit if limit is not None else default, MAX_LIMIT))


def _invalid_cursor():
    return ApiError("invalid_cursor", "That page marker is not valid.")


def _row(conversation, platform, statuses):
    status = statuses.get(conversation.last_ref) if conversation.last_direction == OUT else None
    return {
        "id": conversation.public_id,
        "platform": platform,
        "contact": shown_contact(conversation),
        "can_reply": conversation.can_reply,
        "unread": conversation.unread_count,
        "last_message_at": conversation.last_message_at.isoformat(),
        "last_direction": conversation.last_direction,
        "last_status": status[0] if status else None,
    }


def _annotated(queryset):
    latest = ConversationMessage.objects.filter(conversation=OuterRef("pk")).order_by("-created_at", "-pk")
    return queryset.annotate(
        last_direction=Subquery(latest.values("direction")[:1]),
        last_ref=Subquery(latest.values("gateway_ref")[:1]),
    )


def _rows(conversations, platform):
    """The rows for these conversations. The send records of the last outgoing texts are read
    in one query."""
    refs = [c.last_ref for c in conversations if c.last_direction == OUT and c.last_ref]
    statuses = services.sms_statuses(refs)
    return [_row(conversation, platform, statuses) for conversation in conversations]


def conversations(scope, *, state="all", cursor="", limit=None):
    """The inbox list, newest conversation first: (rows, next). No text, not even a preview.
    `cursor` is the id of the last conversation already shown."""
    if state not in ("all", "unread"):
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"state": ["Choose all or unread."]},
        )
    rows = scope.conversations
    if state == "unread":
        rows = rows.filter(unread_count__gt=0)
    if cursor:
        last = scope.conversations.filter(public_id=cursor).first() if is_public_id(cursor, "conv") else None
        if last is None:
            raise _invalid_cursor()
        rows = rows.filter(
            Q(last_message_at__lt=last.last_message_at)
            | Q(last_message_at=last.last_message_at, pk__lt=last.pk)
        )
    size = _clamped(limit, DEFAULT_LIMIT)
    found = list(_annotated(rows).order_by("-last_message_at", "-pk")[: size + 1])
    more = len(found) > size
    found = found[:size]
    return _rows(found, scope.platform), (found[-1].public_id if more and found else None)


def find(scope, raw):
    """The conversations of one full number, typed by the staff member. Never a fragment.

    Each lookup of a full number is one audit entry, with or without a match: it confirms
    whether that person wrote to us. The entry holds the count and never the number."""
    try:
        number = normalise_ph_mobile(raw if isinstance(raw, str) else "")
    except ValueError:
        return []
    found = list(
        _annotated(scope.conversations.filter(contact=number, can_reply=True)).order_by("-last_message_at", "-pk")
    )
    rows = _rows(found, scope.platform)
    audit.record(scope.actor, "sms_inbox_find", detail={"platform": scope.platform, "matches": len(rows)})
    return rows


def shown_message(message, statuses=None):
    """One text as a screen sees it. An outgoing text carries the status of its send record
    (null when the record is gone); an incoming one carries none. `statuses` is the map
    `services.sms_statuses` answers for the page, or None to look this one up."""
    outgoing = message.direction == OUT
    status = None
    if outgoing:
        if statuses is None:
            statuses = services.sms_statuses([message.gateway_ref])
        status = statuses.get(message.gateway_ref)
    return {
        "id": message.public_id,
        "direction": message.direction,
        "body": message.body,
        "clipped": message.clipped,
        "kind": message.kind or None,
        "status": status[0] if status else None,
        "reason": (status[1] or None) if status else None,
        "author": message.author.get_username() if message.author_id else None,
        "at": (message.occurred_at or message.created_at).isoformat(),
    }


def _opted_out(conversation):
    """True when this person replied STOP to a bulk text. A reply or a composed text to them is
    still allowed; only Bulk SMS leaves them out."""
    if not conversation.can_reply or not dnt_tables_exist():
        return False
    return DoNotText.objects.filter(platform=SMS, mobile=conversation.contact).exists()


def open_conversation(scope, public_id, *, cursor="", limit=None):
    """One conversation with a page of its texts, or None when it is not in the scope. The
    newest page is the default; `next` is the id to pass as `cursor` for the page before.
    Every call writes one audit entry and clears the unread count."""
    if not is_public_id(public_id, "conv"):
        return None
    size = _clamped(limit, MESSAGES_PER_PAGE)
    with transaction.atomic():
        conversation = scope.conversations.select_for_update().filter(public_id=public_id).first()
        if conversation is None:
            return None
        texts = conversation.messages.select_related("author")
        if cursor:
            earlier = texts.filter(public_id=cursor).first() if is_public_id(cursor, "msg") else None
            if earlier is None:
                raise _invalid_cursor()
            texts = texts.filter(
                Q(created_at__lt=earlier.created_at) | Q(created_at=earlier.created_at, pk__lt=earlier.pk)
            )
        page = list(texts.order_by("-created_at", "-pk")[: size + 1])
        more = len(page) > size
        page = page[:size][::-1]
        statuses = services.sms_statuses([m.gateway_ref for m in page if m.direction == OUT])
        messages = [shown_message(message, statuses) for message in page]
        conversation.unread_count = 0
        conversation.save(update_fields=["unread_count"])
        audit.record(
            scope.actor, "sms_inbox_open", target_type=TARGET, target_id=conversation.public_id,
            detail={"platform": scope.platform, "owner_kind": conversation.owner_kind, "messages": len(messages)},
            personal={"contact": shown_contact(conversation)},
        )
    return {
        "id": conversation.public_id,
        "platform": scope.platform,
        "contact": shown_contact(conversation),
        "can_reply": conversation.can_reply,
        "opted_out": _opted_out(conversation),
        "messages": messages,
        "next": page[0].public_id if more and page else None,
    }
