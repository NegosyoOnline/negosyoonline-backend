"""Which ended sites are due for erasure (plan z8uq9mb22v, section 4). Reads only.

The rule is read from the stored `Subscription` dates; the 30 and 90 days are never recomputed
here (`apps.billing.plan` owns them). Nothing in this module writes, locks or deletes.

The lifecycle tables (holds, erased items) are read only while SITE_ERASURE_ENABLED is on, so
that the code can be released, and the dry run used, before `lifecycle.0001` is applied.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings

from apps.billing.models import BankTransferPayment, BillingNotice, ChargeLine, Subscription
from apps.billing.services import ENDED_STATES
from apps.lifecycle.models import ErasureHold, ErasureItem

NEAR_WINDOW = timedelta(days=14)
State = Subscription.State

# Refusal codes.
ALREADY_ERASED, HELD = "already_erased", "held"
PAYMENT_UNDER_REVIEW, UNPAID_USAGE = "payment_under_review", "unpaid_usage"
# The service-end notice, as the dry run shows it (plan D12: shown, never a block).
NOTICE_SENT, NOTICE_NOT_SENT, NO_NOTICE_FOR_STATE = "sent", "not_sent", "no_notice_for_state"


@dataclass
class Candidate:
    subscription: Subscription
    notice: str
    usage_to_write_off: bool = False

    @property
    def site(self):
        return self.subscription.site


@dataclass
class Refused:
    subscription: Subscription
    reason_code: str
    hold_reason: str = ""


@dataclass
class Classification:
    now: object
    lifecycle_checked: bool
    eligible: list = field(default_factory=list)
    near: list = field(default_factory=list)
    refused: list = field(default_factory=list)
    erased_site_ids: set = field(default_factory=set)


def lifecycle_read():
    """Whether the lifecycle tables may be read."""
    return settings.SITE_ERASURE_ENABLED


def export_notice_key(subscription):
    """The key of the service-end email (apps.billing.notices._export_notice_emails)."""
    return f"export_notice:{subscription.pk}:{int(subscription.service_ended_at.timestamp())}"


def _notice(subscription, sent_keys):
    if subscription.state not in (State.CANCELLED, State.CANCELLED_NO_PAYMENT_METHOD):
        return NO_NOTICE_FOR_STATE
    return NOTICE_SENT if export_notice_key(subscription) in sent_keys else NOTICE_NOT_SENT


def classify(now):
    """Sort every ended, non-exempt subscription with a stored deletion date.

    eligible: the date has passed and nothing refuses it. near: due within NEAR_WINDOW.
    refused: the date has passed but a hold, a payment under review, an open charge on a cancelled
    site, or an earlier erasure stops it. An open charge on a suspended site does not refuse: the
    executor writes it off first (`usage.write_off_unreturned`), so it is flagged instead.
    """
    checked = lifecycle_read()
    result = Classification(now=now, lifecycle_checked=checked)
    rows = list(
        Subscription.objects.filter(
            billing_exempt=False, state__in=list(ENDED_STATES), service_ended_at__isnull=False,
            service_ended_at__lte=now, deletion_due_at__isnull=False,
            deletion_due_at__lte=now + NEAR_WINDOW,
        ).select_related("site__account").order_by("pk")
    )
    due = [row for row in rows if row.deletion_due_at <= now]
    result.near = [row for row in rows if row.deletion_due_at > now]
    if not due:
        return result
    ids = [row.pk for row in due]
    site_ids = [row.site_id for row in due]
    submitted = set(
        BankTransferPayment.objects.filter(
            subscription_id__in=ids, status=BankTransferPayment.Status.SUBMITTED
        ).values_list("subscription_id", flat=True)
    )
    open_charge = set(
        ChargeLine.objects.filter(subscription_id__in=ids, status=ChargeLine.Status.OPEN)
        .values_list("subscription_id", flat=True)
    )
    sent_keys = set(
        BillingNotice.objects.filter(subscription_id__in=ids, code="export_notice")
        .values_list("idempotency_key", flat=True)
    )
    holds, erased = {}, set()
    if checked:
        holds = dict(
            ErasureHold.objects.filter(site_id__in=site_ids, released_at__isnull=True)
            .values_list("site_id", "reason_code")
        )
        erased = set(
            ErasureItem.objects.filter(site_id__in=site_ids, status=ErasureItem.Status.ERASED)
            .values_list("site_id", flat=True)
        )
        result.erased_site_ids = erased
    for row in due:
        if row.site_id in erased:
            result.refused.append(Refused(row, ALREADY_ERASED))
        elif row.site_id in holds:
            result.refused.append(Refused(row, HELD, holds[row.site_id]))
        elif row.pk in submitted:
            result.refused.append(Refused(row, PAYMENT_UNDER_REVIEW))
        elif row.pk in open_charge and row.state != State.SUSPENDED:
            result.refused.append(Refused(row, UNPAID_USAGE))
        else:
            result.eligible.append(
                Candidate(row, _notice(row, sent_keys), usage_to_write_off=row.pk in open_charge)
            )
    return result
