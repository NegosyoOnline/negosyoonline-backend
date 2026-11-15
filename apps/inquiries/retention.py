"""Reply audit rows are deleted 12 months after they were written. Stored-channel messages (website
chat) and the conversation's personal fields are deleted or blanked MESSAGE_RETENTION after the
conversation's last message. Touches nothing while INQUIRIES_ENABLED is off or before the tables
exist. Account and site disposal and site erasure blank the provider token, the customer names
and everything personal a stored channel holds."""
from datetime import timedelta

from django.db.models import Q

from apps.common import retention
from apps.inquiries.models import (
    InquiryChannelSettings, InquiryConnection, InquiryConversation, InquiryMessage, InquiryReply,
)
from apps.inquiries.switch import inquiries_on, stored_tables_exist, tables_exist
from apps.sites.models import Site

REPLY_RETENTION = timedelta(days=365)
# Code only: the Privacy wording for it (D1) is still open. Counted from the last message.
MESSAGE_RETENTION = timedelta(days=180)

# The personal fields of a stored conversation, blanked together.
PERSONAL = ("display_name", "contact_name", "contact_email", "visitor_hash", "thread_token")


@retention.behind_switch
def old_replies(now, dry_run):
    if not inquiries_on() or not tables_exist():
        return 0
    return retention.remove(InquiryReply.objects.filter(created_at__lte=now - REPLY_RETENTION), dry_run)


def _personal_left():
    left = Q()
    for field in PERSONAL:
        left |= ~Q(**{field: ""})
    return left


def _expired_stored(now):
    """Stored conversations whose last message is older than MESSAGE_RETENTION and that still
    have something to clear: messages, personal fields, or no audit row to keep them for."""
    cutoff = now - MESSAGE_RETENTION
    old = Q(last_message_at__lte=cutoff) | Q(last_message_at__isnull=True, created_at__lte=cutoff)
    return InquiryConversation.objects.filter(old, stores_history=True).filter(
        Q(messages__isnull=False) | _personal_left() | Q(replies__isnull=True)
    ).distinct()


@retention.behind_switch
def old_messages(now, dry_run):
    """Delete the messages of every stored conversation 180 days after its last message, blank its
    personal fields, and delete the conversation unless a reply audit row still points at it (that
    row has its own, longer rule). The count is the messages plus the conversations dealt with."""
    if not inquiries_on() or not stored_tables_exist():
        return 0
    conversations = list(_expired_stored(now).values_list("pk", flat=True))
    messages = InquiryMessage.objects.filter(conversation_id__in=conversations)
    count = messages.count() + len(conversations)
    if dry_run or not conversations:
        return count
    messages.delete()
    InquiryConversation.objects.filter(pk__in=conversations, replies__isnull=True).delete()
    InquiryConversation.objects.filter(pk__in=conversations).update(
        status=InquiryConversation.CLOSED, unread=False, **{field: "" for field in PERSONAL},
    )
    return count


retention.register("inquiry_replies", old_replies)
retention.register("inquiry_messages", old_messages)


# -- account disposal (called by apps.accounts.retention) ---------------------------------------

CONNECTIONS, CONVERSATIONS = "inquiry_connections", "inquiry_conversations"
MESSAGES, CHANNELS = "inquiry_messages", "inquiry_channel_settings"


def blanked():
    """The field names a disposal blanks, for its audit entry. Reply rows hold a length and a
    code, no personal data, and stay until their own retention rule removes them."""
    return {
        CONNECTIONS: ["token_encrypted"],
        CONVERSATIONS: list(PERSONAL),
        MESSAGES: ["all rows deleted"],
        CHANNELS: ["email_route_token", "webchat_enabled", "email_enabled", "paused_until"],
    }


def blank_inquiries_of(account, dry_run=False):
    """Blank the provider token of the account's sites, the customer names on their conversation
    handles and everything personal their stored channels hold. Runs whatever the switch says,
    because the rows may predate it being turned off; answers empty when the tables are not there
    yet or the account has none. Call it inside a transaction."""
    return blank_inquiries(Site.objects.filter(account=account), dry_run)


def blank_inquiries(sites, dry_run=False):
    """The same for the sites in `sites` (a Site queryset)."""
    if not tables_exist():
        return {}
    stored = stored_tables_exist()  # inquiries.0002 may not be applied where this code already runs
    connections = InquiryConnection.objects.filter(site__in=sites).exclude(token_encrypted="")
    conversations = InquiryConversation.objects.filter(site__in=sites)
    conversations = conversations.filter(_personal_left()) if stored else conversations.exclude(display_name="")
    counts = {CONNECTIONS: connections.count(), CONVERSATIONS: conversations.count()}
    messages = channels = None
    if stored:
        messages = InquiryMessage.objects.filter(site__in=sites)
        channels = InquiryChannelSettings.objects.filter(site__in=sites).filter(
            ~Q(email_route_token="") | Q(webchat_enabled=True) | Q(email_enabled=True) | Q(paused_until__isnull=False)
        )
        counts.update({MESSAGES: messages.count(), CHANNELS: channels.count()})
    if not any(counts.values()):
        return {}
    if not dry_run:
        connections.update(token_encrypted="", status=InquiryConnection.UNSET)
        if stored:
            messages.delete()
            # The visitor's secret hash and the thread token go too, so nothing can continue a
            # conversation after the site's data is gone.
            conversations.update(
                status=InquiryConversation.CLOSED, unread=False, **{field: "" for field in PERSONAL},
            )
            channels.update(
                email_route_token="", webchat_enabled=False, email_enabled=False, paused_until=None,
            )
        else:
            conversations.update(display_name="")
    return counts
