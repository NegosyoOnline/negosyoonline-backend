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
