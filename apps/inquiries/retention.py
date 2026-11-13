"""Reply audit rows are deleted 12 months after they were written. Touches nothing while
INQUIRIES_ENABLED is off or before the tables exist. Account and site disposal blank the
provider token and the customer names kept on conversation handles."""
from datetime import timedelta

from apps.common import retention
from apps.inquiries.models import InquiryConnection, InquiryConversation, InquiryReply
from apps.inquiries.switch import inquiries_on, tables_exist
from apps.sites.models import Site

REPLY_RETENTION = timedelta(days=365)


@retention.behind_switch
def old_replies(now, dry_run):
    if not inquiries_on() or not tables_exist():
        return 0
    return retention.remove(InquiryReply.objects.filter(created_at__lte=now - REPLY_RETENTION), dry_run)


retention.register("inquiry_replies", old_replies)


# -- account disposal (called by apps.accounts.retention) ---------------------------------------

CONNECTIONS, CONVERSATIONS = "inquiry_connections", "inquiry_conversations"


def blanked():
    """The field names a disposal blanks, for its audit entry. Reply rows hold a length and a
    code, no personal data, and stay until their own retention rule removes them."""
    return {CONNECTIONS: ["token_encrypted"], CONVERSATIONS: ["display_name"]}


def blank_inquiries_of(account, dry_run=False):
    """Blank the provider token of the account's sites and the customer names on their
    conversation handles. Runs whatever the switch says, because the rows may predate it being
    turned off; answers empty when the tables are not there yet or the account has none.
    Call it inside a transaction."""
    return blank_inquiries(Site.objects.filter(account=account), dry_run)


def blank_inquiries(sites, dry_run=False):
    """The same for the sites in `sites` (a Site queryset)."""
    if not tables_exist():
        return {}
    connections = InquiryConnection.objects.filter(site__in=sites).exclude(token_encrypted="")
    conversations = InquiryConversation.objects.filter(site__in=sites).exclude(display_name="")
    counts = {CONNECTIONS: connections.count(), CONVERSATIONS: conversations.count()}
    if not any(counts.values()):
        return {}
    if not dry_run:
        connections.update(token_encrypted="", status=InquiryConnection.UNSET)
        conversations.update(display_name="")
    return counts
