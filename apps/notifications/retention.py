"""The retention rules of the notifications app. Loaded by NotificationsConfig.ready().

SMS send records have their own rule, registered by the SMS build, not here.
"""
from datetime import timedelta

from apps.common import retention
from apps.notifications.models import EmailMessage

EMAIL_RECORD_LIFETIME = timedelta(days=730)


@retention.behind_switch
def email_messages(now, dry_run):
    """Email send records, 2 years after they were written."""
    rows = EmailMessage.objects.filter(created_at__lte=now - EMAIL_RECORD_LIFETIME)
    return retention.remove(rows, dry_run)


retention.register("email_messages", email_messages)


# Sent to an admin identity's address, not to a subscriber: they follow the 2-year rule only.
ADMIN_CATEGORIES = ("admin_signin_code", "admin_lockout")


def _records_for(address):
    return EmailMessage.objects.filter(recipient__iexact=address).exclude(
        category__in=ADMIN_CATEGORIES
    )


def delete_records_for(address, dry_run=False):
    """Delete the send records of one address when its account is disposed of. Returns the
    count; a dry run only counts. Called only by apps.accounts.retention."""
    return retention.remove(_records_for(address), dry_run)


def export_records_for(address):
    """The send records of one address as plain values, for the account export."""
    return [
        {
            "category": row.category,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        }
        for row in _records_for(address).order_by("pk")
    ]
