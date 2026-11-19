"""(Messenger conversations are stored conversations: the same rules cover them, and the customer's
page-scoped id is one of the personal fields.) Reply audit rows are deleted 12 months after they were written. Stored-channel messages (website
chat) and the conversation's personal fields are deleted or blanked MESSAGE_RETENTION after the
conversation's last message. The reply rule waits for INQUIRIES_ENABLED; the message rule only
deletes, so it runs whatever that switch says (stored text must not outlive its period), and
both wait for RETENTION_ENABLED and for the tables to exist. Account and site disposal and site erasure blank the connection token, the customer names
and everything personal a stored channel holds."""
from datetime import timedelta

from django.db.models import Q

from apps.common import retention
from apps.inquiries import comment_media, profile
from apps.inquiries.models import (
    InquiryChannelSettings, InquiryComment, InquiryCommentSettings, InquiryConnection, InquiryConversation,
    InquiryMessage, InquiryPost, InquiryReply,
)
from apps.notifications.models import EmailMessage
from apps.inquiries.switch import comment_tables_exist, inquiries_on, stored_tables_exist, tables_exist
from apps.sites.models import Site

REPLY_RETENTION = timedelta(days=365)
# Code only: the Privacy wording for it (D1) is still open. Counted from the last message.
MESSAGE_RETENTION = timedelta(days=180)
COMMENT_RETENTION = timedelta(days=180)  # counted from the comment's time (Meta's created_time)

# The personal fields of a stored conversation, blanked together.
PERSONAL = ("display_name", "contact_name", "contact_email", "contact_mobile", "visitor_hash", "thread_token", "psid")


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
    if not stored_tables_exist():  # no INQUIRIES_ENABLED gate: this rule only deletes
        return 0
    conversations = list(_expired_stored(now).values_list("pk", flat=True))
    messages = InquiryMessage.objects.filter(conversation_id__in=conversations)
    count = messages.count() + len(conversations)
    if dry_run or not conversations:
        return count
    messages.delete()
    profile.forget(conversations)  # a Messenger customer's cached photo goes with their messages
    InquiryConversation.objects.filter(pk__in=conversations, replies__isnull=True).delete()
    InquiryConversation.objects.filter(pk__in=conversations).update(
        status=InquiryConversation.CLOSED, unread=False, **{field: "" for field in PERSONAL},
    )
    return count


@retention.behind_switch
def old_comments(now, dry_run):
    """Delete Facebook comments 180 days after they were made (with their action rows), then the
    posts that no longer have a comment. No INQUIRIES_ENABLED gate: this rule only deletes."""
    if not comment_tables_exist():
        return 0
    cutoff = now - COMMENT_RETENTION
    comments = InquiryComment.objects.filter(created_at__lte=cutoff)
    # posts left with no comment once the old ones are gone (and old enough themselves)
    posts = InquiryPost.objects.filter(created_at__lte=cutoff).exclude(comments__created_at__gt=cutoff)
    count = comments.count() + posts.count()
    if dry_run:
        return count
    comment_media.forget(list(comments.values_list("pk", flat=True)))  # the cached photos go with the rows
    comments.delete()
    posts.delete()
    return count


EMAIL_CATEGORY = "inquiry_reply"  # the category of an email reply (stored.EMAIL_CATEGORY)


@retention.behind_switch
def old_email_records(now, dry_run):
    """The send records of inquiry email replies (they hold the customer's address) go on the
    message clock, MESSAGE_RETENTION, instead of the generic 2-year rule of the notifications
    app; whichever deletes a row first wins, so the shorter period holds."""
    # no INQUIRIES_ENABLED gate: this rule only deletes, and the EmailMessage table always exists
    rows = EmailMessage.objects.filter(category=EMAIL_CATEGORY, created_at__lte=now - MESSAGE_RETENTION)
    return retention.remove(rows, dry_run)


retention.register("inquiry_replies", old_replies)
retention.register("inquiry_messages", old_messages)
retention.register("inquiry_email_records", old_email_records)
retention.register("inquiry_comments", old_comments)


# -- account disposal (called by apps.accounts.retention) ---------------------------------------

CONNECTIONS, CONVERSATIONS = "inquiry_connections", "inquiry_conversations"
MESSAGES, CHANNELS = "inquiry_messages", "inquiry_channel_settings"
EMAIL_RECORDS = "inquiry_email_records"
COMMENTS = "inquiry_comments"


def blanked():
    """The field names a disposal blanks, for its audit entry. Reply rows hold a length and a
    code, no personal data, and stay until their own retention rule removes them."""
    return {
        CONNECTIONS: ["token_encrypted", "external_page_id", "label", "meta_user_id"],
        CONVERSATIONS: list(PERSONAL),
        MESSAGES: ["all rows deleted"],
        EMAIL_RECORDS: ["inquiry_reply rows to the sites' customers deleted"],
        COMMENTS: ["all comment, post and comment-action rows deleted"],
        CHANNELS: ["email_route_token", "webchat_enabled", "email_enabled", "paused_until"],
    }


def blank_inquiries_of(account, dry_run=False):
    """Blank the connection token of the account's sites, the customer names on their conversations and everything personal their stored channels hold. Runs whatever the switch says,
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
        # EmailMessage has no site column: find the records by the customers' addresses, before
        # the conversations are blanked. A recipient that is also a customer of another site loses
        # its inquiry_reply records too (errs on the side of deleting).
        addresses = list(
            conversations.exclude(contact_email="").values_list("contact_email", flat=True).distinct()
        )
        email_records = EmailMessage.objects.filter(category=EMAIL_CATEGORY, recipient__in=addresses)
        counts.update({MESSAGES: messages.count(), CHANNELS: channels.count(), EMAIL_RECORDS: email_records.count()})
    comment_rows = None
    if comment_tables_exist():
        comment_rows = InquiryComment.objects.filter(site__in=sites)
        found = comment_rows.count()
        if found:  # a site with no comments keeps the report's shape as it was
            counts[COMMENTS] = found
    if not any(counts.values()):
        return {}
    if not dry_run:
        if comment_rows is not None:
            comment_media.forget(list(comment_rows.values_list("pk", flat=True)))
            comment_rows.delete()
            InquiryPost.objects.filter(site__in=sites).delete()
            InquiryCommentSettings.objects.filter(site__in=sites).update(feed_subscribed=False, page_id="")
        connections.update(token_encrypted="", status=InquiryConnection.UNSET)
        if stored:
            # A Messenger Page leaves the site with its token (D5 frees it for another site).
            InquiryConnection.objects.filter(site__in=sites, provider="messenger").update(
                external_page_id="", label="", meta_user_id="",
            )
            profile.forget(conversations.filter(provider="messenger").values_list("pk", flat=True))
            messages.delete()
            email_records.delete()
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
