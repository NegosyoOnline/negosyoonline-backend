"""What a screen may be shown. Masking is done here, by the backend: an answer never carries
a full number, and the list never carries a text. Reading a text always goes through
`open_conversation`, which writes the audit entry in the same transaction."""
from django.db import transaction
from django.db.models import OuterRef, Subquery

from apps.common.ids import is_public_id
from apps.common.masking import HIDDEN, mask_mobile
from apps.common.phones import normalise_ph_mobile
from apps.inbox.models import ConversationMessage
from apps.operators import audit

TARGET = "conversation"
# A sender with this many digits is somebody's number, whether or not it can be replied to.
NUMBER_DIGITS = 7


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


def _row(conversation):
    return {
        "id": conversation.public_id,
        "contact": shown_contact(conversation),
        "can_reply": conversation.can_reply,
        "unread": conversation.unread_count,
        "last_message_at": conversation.last_message_at.isoformat(),
        "last_direction": conversation.last_direction,
    }


def _listed(queryset):
    latest = ConversationMessage.objects.filter(conversation=OuterRef("pk")).order_by("-created_at", "-pk")
    rows = queryset.annotate(last_direction=Subquery(latest.values("direction")[:1]))
    return [_row(conversation) for conversation in rows.order_by("-last_message_at", "-pk")]


def conversations(scope):
    """The inbox list. No text, not even a preview. Paging is added by the route that calls it."""
    return _listed(scope.conversations)


def find(scope, raw):
    """The conversations of one full number, typed by the staff member. Never a fragment.

    Each lookup of a full number is one audit entry, with or without a match: it confirms
    whether that person wrote to us. The entry holds the count and never the number."""
    try:
        number = normalise_ph_mobile(raw if isinstance(raw, str) else "")
    except ValueError:
        return []
    rows = _listed(scope.conversations.filter(contact=number, can_reply=True))
    audit.record(scope.actor, "sms_inbox_find", detail={"platform": scope.platform, "matches": len(rows)})
    return rows


def open_conversation(scope, public_id):
    """One conversation with its texts, or None when it is not in the scope. Writes the audit
    entry and clears the unread count."""
    if not is_public_id(public_id, "conv"):
        return None
    with transaction.atomic():
        conversation = scope.conversations.select_for_update().filter(public_id=public_id).first()
        if conversation is None:
            return None
        messages = [
            {
                "id": message.public_id,
                "direction": message.direction,
                "body": message.body,
                "clipped": message.clipped,
                "at": (message.occurred_at or message.created_at).isoformat(),
            }
            for message in conversation.messages.order_by("created_at", "pk")
        ]
        conversation.unread_count = 0
        conversation.save(update_fields=["unread_count"])
        audit.record(
            scope.actor, "sms_inbox_open", target_type=TARGET, target_id=conversation.public_id,
            detail={"platform": scope.platform, "owner_kind": conversation.owner_kind, "messages": len(messages)},
            personal={"contact": shown_contact(conversation)},
        )
    return {
        "id": conversation.public_id,
        "contact": shown_contact(conversation),
        "can_reply": conversation.can_reply,
        "messages": messages,
    }
