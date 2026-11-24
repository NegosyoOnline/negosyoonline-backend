"""The retention rules of the notifications app. Loaded by NotificationsConfig.ready().

The SMS rules are not behind RETENTION_ENABLED: "send records are kept 1 year, then
deleted" holds wherever texts are sent. While SMS_GATEWAY_ENABLED is off the two rules here
touch nothing, because the code is released before their tables are created. The third daily
rule, `inbox_messages`, is registered by apps.inbox and named in DAILY_RULES below (as are `bulk_batches` and
`store_bulk_batches`, the latter registered by apps.inquiries).
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import MobileOtpChallenge
from apps.common import retention
from apps.notifications import sms
from apps.notifications.models import EmailMessage, SmsMessage
from apps.operators import background

MANILA = ZoneInfo("Asia/Manila")
EMAIL_RECORD_LIFETIME = timedelta(days=730)
SMS_RECORD_LIFETIME = timedelta(days=365)
CHALLENGE_KEPT = timedelta(hours=24)
_LOCK_KEPT = 2 * 24 * 3600


# The payment emails to a Subscriber: the records of them are billing records (Privacy,
# section 8), kept under BILLING_RECORD_YEARS below, not the 2-year rule. Named here once; the
# codes are the `code` passed to `_send_once` in apps/billing/notices.py, sent as `billing_<code>`.
# Only the emails tied to a payment and its Billing Statement. The trial and renewal reminders,
# trial_ended, service_paused and export_notice are notices, not billing records, so they stay
# on the 2-year rule ("2 years, then deleted"); so do billing_staff_proof_waiting (a staff
# alert) and invoice_queue_alert (internal). The statements and invoices apps send no email.
BILLING_DOCUMENT_CATEGORIES = (
    "billing_proof_received", "billing_payment_approved", "billing_payment_rejected",
)

# Privacy: kept at least 5 years, counted from the day after the filing deadline of the tax
# return for the year of the entry. No model computes this today (statements and invoices only
# say "kept five years" and have no delete path), so it is computed here from the record's own
# date: the return for year Y is due 15 April of Y+1; the deadline is taken as 30 April, a
# margin for a weekend or holiday move, so the period is never shorter than Privacy's. The
# 5 years start on 1 May of Y+1 and end on 1 May of Y+6 (Manila dates).
BILLING_RECORD_YEARS = 5
BILLING_DEADLINE = (4, 30)


def _billing_cutoff(now):
    """Billing email records created before this instant have outlived their period."""
    local = now.astimezone(MANILA)
    month, day = BILLING_DEADLINE
    due_year = local.year - BILLING_RECORD_YEARS - 1  # last record year whose period is over
    if (local.month, local.day) <= (month, day):
        due_year -= 1  # day after the deadline not yet reached
    return datetime(due_year + 1, 1, 1, tzinfo=MANILA)


@retention.behind_switch
def email_messages(now, dry_run):
    """Email send records, 2 years after they were written. Billing emails (see
    BILLING_DOCUMENT_CATEGORIES) follow their own period instead."""
    rows = EmailMessage.objects.filter(
        ~Q(category__in=BILLING_DOCUMENT_CATEGORIES),
        created_at__lte=now - EMAIL_RECORD_LIFETIME,
    ) | EmailMessage.objects.filter(
        category__in=BILLING_DOCUMENT_CATEGORIES, created_at__lt=_billing_cutoff(now),
    )
    return retention.remove(rows, dry_run)


retention.register("email_messages", email_messages)


# Sent to an admin identity's address, not to a subscriber: they follow the 2-year rule only.
ADMIN_CATEGORIES = ("admin_signin_code", "admin_lockout")


def _records_for(address):
    return EmailMessage.objects.filter(recipient__iexact=address).exclude(
        category__in=ADMIN_CATEGORIES
    )


def _without_billing_documents(rows):
    """Account disposal leaves the records of BILLING_DOCUMENT_CATEGORIES where they are: they
    are billing records, kept at least 5 years (Privacy, section 8), and `email_messages`
    removes them at the end of that period. The export does not use this: it still lists them."""
    return rows.exclude(category__in=BILLING_DOCUMENT_CATEGORIES)


def delete_records_for(address, dry_run=False):
    """Delete the send records of one address when its account is disposed of, except the
    admin ones and the billing-document ones (see `_without_billing_documents`). Returns the
    count; a dry run only counts. Called only by apps.accounts.retention."""
    return retention.remove(_without_billing_documents(_records_for(address)), dry_run)


# The notice of an email change goes to the old address just after the change.
CHANGE_NOTICE = "email_changed_notice"
CHANGE_NOTICE_WITHIN = timedelta(hours=24)


def delete_earlier_records_for(periods, dry_run=False):
    """Delete the send records of the addresses an account used before an email change.

    `periods` is (address, changed_at) pairs: the old address of each change and when the
    account left it. Only records written up to that moment go, plus the change notice sent
    to the old address: afterwards the address may belong to another account. Admin
    categories and billing-document records stay (see `_without_billing_documents`). Returns
    the count; a dry run only counts. Called only by apps.accounts.retention.
    """
    match = Q()
    for address, changed_at in periods:
        match |= Q(recipient__iexact=address) & (
            Q(created_at__lte=changed_at)
            | Q(category=CHANGE_NOTICE, created_at__lte=changed_at + CHANGE_NOTICE_WITHIN)
        )
    if not match:
        return 0
    rows = EmailMessage.objects.filter(match).exclude(category__in=ADMIN_CATEGORIES)
    return retention.remove(_without_billing_documents(rows), dry_run)


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


DAILY_RULES = ("sms_messages", "mobile_challenges", "inbox_messages", "bulk_batches", "store_bulk_batches")


def run_daily_rules():
    return retention.run_all(only=DAILY_RULES)


def start_daily():
    """Start the day's run of the SMS rules, once. Called whenever the phone polls. This is the
    fallback where no beat service runs (production today); where beat runs, `retention.run`
    (`apps/common/tasks.py`) also runs these rules at 03:00 and finds nothing left due. The
    first call of each Manila day takes the lock and runs them in the background; the others
    do nothing. A day with no call is a day with no run, and the next run removes everything
    that is due. The other rules run only from the daily `retention.run` or by hand."""
    if cache.add(f"retention:daily:{timezone.localdate():%Y%m%d}", 1, _LOCK_KEPT):
        background.run_later(run_daily_rules)
        return True
    return False
