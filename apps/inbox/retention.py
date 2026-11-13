"""The inbox's retention rule. Loaded by InboxConfig.ready().

Not behind RETENTION_ENABLED, and not behind SMS_INBOX_ENABLED: "texts are kept 365 days,
then deleted" holds wherever texts are stored, and a text stored while the inbox was on must
not outlive that because the switch was later turned off. It asks only whether the tables
exist, because the code is released before they are created."""
from datetime import timedelta

from apps.common import retention
from apps.inbox.models import BulkBatch, Contact, ContactGroup, Conversation, ConversationMessage
from apps.inbox.switch import SMS, bulk_tables_exist, contacts_tables_exist, tables_exist

MESSAGE_LIFETIME = timedelta(days=365)


def inbox_messages(now, dry_run):
    """SMS inbox messages, received and sent, 365 days after they were stored; then any
    conversation left with no message. Rows are deleted, not blanked. The count is messages."""
    if not tables_exist():
        return 0
    rows = ConversationMessage.objects.filter(platform=SMS, created_at__lte=now - MESSAGE_LIFETIME)
    count = retention.remove(rows, dry_run)
    if not dry_run:
        Conversation.objects.filter(platform=SMS, messages__isnull=True).delete()
    return count


retention.register("inbox_messages", inbox_messages)


def bulk_batches(now, dry_run):
    """Bulk SMS batches (the text and the counts), 365 days after they were confirmed; their
    numbers, if any are left, go with them. Runs wherever the table exists, whatever the bulk
    switch says. The do-not-text list is not touched by any rule."""
    if not bulk_tables_exist():
        return 0
    return retention.remove(BulkBatch.objects.filter(created_at__lte=now - MESSAGE_LIFETIME), dry_run)


retention.register("bulk_batches", bulk_batches)


# -- site erasure (called by apps.lifecycle; plan z8uq9mb22v, D10) --------------------------------

CONVERSATIONS, MESSAGES = "inbox_conversations", "inbox_messages"
CONTACTS, GROUPS = "inbox_contacts", "inbox_groups"


def erase_site_inbox(sites, dry_run=False):
    """Delete the conversations (and their messages), contacts and contact groups owned by the
    sites in `sites` (a Site queryset). Rows owned by HQ or an account are never touched. The
    do-not-text list is kept: it holds a number and no name, and keeps it until the person asks
    (a message it points at may go; the link is then empty). Runs whatever the inbox switches say,
    because the rows may predate a switch being turned off; a table that does not exist yet counts
    zero. Returns the counts; a dry run only counts. Call it inside a transaction."""
    counts = {CONVERSATIONS: 0, MESSAGES: 0, CONTACTS: 0, GROUPS: 0}
    if tables_exist():
        conversations = Conversation.objects.filter(owner_kind="site", site__in=sites)
        counts[CONVERSATIONS] = conversations.count()
        counts[MESSAGES] = ConversationMessage.objects.filter(conversation__in=conversations).count()
        if not dry_run:
            conversations.delete()  # their messages go with them
    if contacts_tables_exist():
        contacts = Contact.objects.filter(owner_kind="site", site__in=sites)
        groups = ContactGroup.objects.filter(owner_kind="site", site__in=sites)
        counts[CONTACTS], counts[GROUPS] = contacts.count(), groups.count()
        if not dry_run:
            contacts.delete()
            groups.delete()
    return counts
