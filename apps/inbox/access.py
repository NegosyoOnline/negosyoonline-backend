"""The one access check of the inbox. Every list, read and send starts from the scope it
answers; nothing reaches a conversation or a message any other way."""
from dataclasses import dataclass

from django.db.models import QuerySet

from apps.inbox.models import Conversation, InboxAccess
from apps.inbox.switch import SMS, inbox_on


@dataclass(frozen=True)
class Scope:
    actor: object
    platform: str
    conversations: QuerySet
    can_send: bool


def scope_for(actor, platform):
    """What this actor may reach on this platform, or None. A platform is always named; no
    scope spans platforms. Admitting a Subscriber later is a new branch here and nowhere else."""
    if platform != SMS or not inbox_on():
        return None
    if actor is None or not (actor.is_authenticated and actor.is_active and actor.is_staff):
        return None
    row = InboxAccess.objects.filter(operator=actor, platform=SMS).first()
    if row is None:
        return None
    conversations = Conversation.objects.filter(platform=SMS, owner_kind=Conversation.Owner.HQ)
    return Scope(actor=actor, platform=SMS, conversations=conversations, can_send=row.can_send)
