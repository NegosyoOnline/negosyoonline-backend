"""Emailing an issued statement (Billing Build B5, PR 8; plan section 7.4, choice C12, Q4).

One `StatementDelivery` row per attempt, key `deliver:<statement>:<n>`. The email goes through
`apps.notifications.services` with category `billing_statement`, to the billing profile's
billing email (else the account email), with no attachment: it carries the number, the total,
the period, the exact label and a link to the Billing page. A failed attempt keeps only a code.
The beat task retries with a backoff until 5 attempts have been made, then the delivery stays
failed and shows in HQ for a manual resend. Nothing is sent while the statements switch is off,
and a disposed address is never mailed. Wording approved by Coach Ed, 2026-11-14.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.billing.notices import SIGN_OFF, _billing_link, _day, _money
from apps.common.disposal import is_disposed_address
from apps.notifications.services import EmailNotSent, send_email
from apps.statements import switch
from apps.statements.models import LABEL, BillingProfile, Statement, StatementDelivery

logger = logging.getLogger(__name__)

CATEGORY = "billing_statement"
MAX_ATTEMPTS = 5
# The wait after a failed attempt n (index n - 1) before attempt n + 1.
BACKOFF = (timedelta(minutes=15), timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))
BATCH = 50
# Codes that a retry cannot cure: the beat task leaves them for a person.
TERMINAL = {"disposed_address", "account_gone"}


def key_for(statement, attempt):
    return f"deliver:{statement.public_id}:{attempt}"


def recipient(statement):
    """The billing email of the site's profile, else the account email."""
    site = statement.subscription.site
    profile = BillingProfile.objects.filter(site=site).first()
    if profile is not None and profile.billing_email:
        return profile.billing_email
    return site.account.email


def subject_for(statement):
    return f"Your NegosyoOnline billing statement {statement.number}"


def body_for(statement):
    site = statement.subscription.site
    name = (statement.bill_to or {}).get("name", "").strip()
    hello = f"Hello {name}," if name and "@" not in name else "Hello,"
    return (
        f"{hello} your billing statement for {site.display_name} is ready: total "
        f"{_money(statement.total_centavos)} for {_day(statement.period_start)} to "
        f"{_day(statement.period_end)}. You can view and download it on your Billing page: "
        f"{_billing_link()}. {LABEL} {SIGN_OFF}"
    )


def deliver(statement, *, manual=False):
    """One attempt. Returns the `StatementDelivery` row, or None when nothing was attempted
    (switch off, not a statement, already delivered, or out of attempts). `manual=True` is a
    staff resend: it always makes one more attempt, even after a delivery or five failures."""
    if not switch.statements_enabled() or statement.document_type != Statement.DocumentType.STATEMENT:
        return None
    with transaction.atomic():
        locked = (
            Statement.objects.select_for_update(of=("self",))
            .select_related("subscription__site__account").get(pk=statement.pk)
        )
        rows = list(locked.deliveries.all())
        if not manual:
            if any(r.status == StatementDelivery.Status.SENT for r in rows):
                return None
            if len(rows) >= MAX_ATTEMPTS:
                return None
        attempt = len(rows) + 1
        to = recipient(locked)
        row = StatementDelivery.objects.create(
            statement=locked, to_email=to, attempt=attempt,
            idempotency_key=key_for(locked, attempt),
        )
        site = locked.subscription.site
        if site.deleted_at is not None or site.account.closed_at is not None:
            code = "account_gone"
        elif is_disposed_address(to):
            code = "disposed_address"
        else:
            code = ""
            try:
                send_email(
                    to=to, category=CATEGORY, subject=subject_for(locked), body=body_for(locked)
                )
            except EmailNotSent:
                code = "email_not_sent"
            except Exception:  # noqa: BLE001 - a code is kept, never the message
                logger.exception("Statement email error (statement=%s)", locked.public_id)
                code = "error"
        if code:
            row.status, row.error_code = StatementDelivery.Status.FAILED, code
            row.save(update_fields=["status", "error_code"])
            logger.warning(
                "Statement %s not delivered (attempt=%s, code=%s)", locked.public_id, attempt, code
            )
        else:
            row.status, row.sent_at = StatementDelivery.Status.SENT, timezone.now()
            row.save(update_fields=["status", "sent_at"])
        return row


def deliver_safely(statement):
    """`deliver` for jobs: an error is logged and never stops the others."""
    try:
        return deliver(statement)
    except Exception:  # noqa: BLE001
        logger.exception("Statement delivery failed (statement=%s)", statement.public_id)
        return None


def due(now=None):
    """Statements to try now: never attempted, or last attempt failed with a code a retry can
    cure, fewer than 5 attempts made and the backoff over."""
    now = now or timezone.now()
    found = []
    candidates = (
        Statement.objects.filter(document_type=Statement.DocumentType.STATEMENT)
        .exclude(deliveries__status=StatementDelivery.Status.SENT)
        .prefetch_related("deliveries").order_by("pk")
    )
    for statement in candidates:
        rows = sorted(statement.deliveries.all(), key=lambda r: r.attempt)
        if not rows:
            found.append(statement)
            continue
        last = rows[-1]
        if (
            len(rows) < MAX_ATTEMPTS
            and last.error_code not in TERMINAL
            and last.created_at + BACKOFF[len(rows) - 1] <= now
        ):
            found.append(statement)
        if len(found) >= BATCH:
            break
    return found


def run_due(now=None):
    """The beat task's work. Returns how many statements it tried. Nothing while the switch
    is off."""
    if not switch.statements_enabled():
        return 0
    tried = 0
    for statement in due(now):
        if deliver_safely(statement) is not None:
            tried += 1
    return tried
