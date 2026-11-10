"""The retention rules of the notifications app. Loaded by NotificationsConfig.ready().

The two SMS rules are not behind RETENTION_ENABLED: "send records are kept 1 year, then
deleted" holds wherever texts are sent. While SMS_GATEWAY_ENABLED is off they touch nothing,
because the code is released before their tables are created.
"""
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import MobileOtpChallenge
from apps.common import retention
from apps.notifications import sms
from apps.notifications.models import EmailMessage, SmsMessage
from apps.operators import background

EMAIL_RECORD_LIFETIME = timedelta(days=730)
SMS_RECORD_LIFETIME = timedelta(days=365)
CHALLENGE_KEPT = timedelta(hours=24)
_LOCK_KEPT = 2 * 24 * 3600


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


def sms_messages(now, dry_run):
    """SMS send records, 1 year after they were written."""
    if not sms.gateway_on():
        return 0
    rows = SmsMessage.objects.filter(created_at__lte=now - SMS_RECORD_LIFETIME)
    return retention.remove(rows, dry_run)


def mobile_challenges(now, dry_run):
    """Mobile verification challenges, 24 hours after they expired."""
    if not sms.gateway_on():
        return 0
    rows = MobileOtpChallenge.objects.filter(expires_at__lte=now - CHALLENGE_KEPT)
    return retention.remove(rows, dry_run)


retention.register("sms_messages", sms_messages)
retention.register("mobile_challenges", mobile_challenges)


DAILY_RULES = ("sms_messages", "mobile_challenges")


def run_daily_rules():
    return retention.run_all(only=DAILY_RULES)


def start_daily():
    """Start the day's run of the two SMS rules, once. Called whenever the phone polls, until
    a beat service exists: the first call of each Manila day takes the lock and runs them in
    the background; the others do nothing. A day with no call is a day with no run, and the
    next run removes everything that is due. Every other rule stays manual."""
    if cache.add(f"retention:daily:{timezone.localdate():%Y%m%d}", 1, _LOCK_KEPT):
        background.run_later(run_daily_rules)
        return True
    return False
