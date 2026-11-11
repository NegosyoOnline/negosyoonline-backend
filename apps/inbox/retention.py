"""The inbox's retention rule. Loaded by InboxConfig.ready().

Not behind RETENTION_ENABLED, and not behind SMS_INBOX_ENABLED: "texts are kept 365 days,
then deleted" holds wherever texts are stored, and a text stored while the inbox was on must
not outlive that because the switch was later turned off. It asks only whether the tables
exist, because the code is released before they are created."""
from datetime import timedelta

from apps.common import retention
from apps.inbox.models import Conversation, ConversationMessage
from apps.inbox.switch import SMS, tables_exist

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
