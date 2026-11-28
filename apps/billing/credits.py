"""Subscription Credit (Billing B6, piece BE-2): the append-only ledger, how a credit is earned,
reserved, spent, released and reversed, and what an account or HQ reads of it.

Every figure is whole centavos. The balance of an account is the sum of its `CreditEntry`
rows and is what can be spent; the credit a waiting payment holds is out of it (`reserve`)
until the payment is confirmed (`apply`) or ends (`release`). Credit is not cash and is not
moved between accounts.

Rules (Coach Ed, 2026-11-21, relayed; plan billing-plan-b6-referral-credits):
- Per account, one balance. It pays any NegosyoOnline charge on the account, the earliest-due
  open site bill first (rules 1 and 3).
- R-1: a referrer earns on the referred business's first confirmed payment only, 20% of the
  base amount actually paid (after any credit, excluding usage), to the nearest peso.
- Rule 6: the referrer earns only while at least one of its sites is `active`.
- R-2: a bill the credit covers in full settles at once, with no proof and no staff click.

Everything that earns, reserves or spends is behind BILLING_REFERRALS_ENABLED. A reserve that
exists when the switch is turned off is still resolved (applied, or released), so no credit is
ever left held. Callers hold the subscription's row lock; this module locks the account row
after it, always in that order. No ledger (FAS) posting is made.
"""
import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Sum

from apps.accounts.models import Account
from apps.billing.models import BankTransferPayment, CreditEntry, Subscription
from apps.common.errors import ApiError
from apps.operators import audit
from apps.sites.models import Site

logger = logging.getLogger(__name__)

Kind = CreditEntry.Kind
EARN_PERCENT = 20
MAX_REASON = 300
MAX_CORRECTION_CENTAVOS = 2_147_483_647
# A credit note with one of these reasons reverses the credit its payment earned (plan 4.4).
REVERSING_REASONS = ("billing_error", "duplicate_payment")


def enabled():
    return bool(settings.BILLING_ENABLED and settings.BILLING_REFERRALS_ENABLED)


def money(centavos):
    """"₱1,000" for whole pesos, else "₱1,000.50"."""
    sign = "-" if centavos < 0 else ""
    centavos = abs(centavos)
    if centavos % 100 == 0:
        return f"{sign}₱{centavos // 100:,}"
    return f"{sign}₱{centavos / 100:,.2f}"


def _account_of(payment):
    return payment.subscription.site.account


def lock(account):
    """The account row, locked until the transaction ends. Held only after a subscription lock."""
    return Account.objects.select_for_update().get(pk=account.pk)


def balance(account):
    """What the account can spend now, in centavos. Never below zero."""
    total = CreditEntry.objects.filter(account=account).aggregate(t=Sum("amount_centavos"))["t"]
    return max(total or 0, 0)


def reserved(account):
    """What waiting payments of the account hold, in centavos."""
    total = BankTransferPayment.objects.filter(
        subscription__site__account=account, status=BankTransferPayment.Status.SUBMITTED
    ).aggregate(t=Sum("credit_centavos"))["t"]
    return total or 0


def earned_count(account):
    return CreditEntry.objects.filter(account=account, kind=Kind.EARN).count()


def _write(account, kind, amount, key, *, payment=None, reason="", operator=None, detail=None):
    """One entry under its key. A key already written returns that entry and writes nothing."""
    entry, _ = CreditEntry.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "account": account, "kind": kind, "amount_centavos": amount, "payment": payment,
            "reason": reason, "operator": operator, "detail": detail or {},
        },
    )
    return entry


# Spending

def _due_at(subscription):
    """When the subscription's next payment falls due, the key rule 1 orders open bills by."""
    from apps.billing import services  # noqa: PLC0415 - services imports this module

    latest = services._latest_cycle(subscription)
    state = subscription.state
    if state == Subscription.State.TRIAL_ACTIVE:
        return subscription.trial_end_at
    if state == Subscription.State.PAYMENT_REQUIRES_ATTENTION:
        return latest.start_at
    return latest.end_at


def _payable(subscription, now):
    from apps.billing import services  # noqa: PLC0415

    if subscription.billing_exempt or subscription.state == (
        Subscription.State.CANCELLED_NO_PAYMENT_METHOD
    ):
        return False
    try:
        services._refuse_unpayable(subscription, now)
    except ApiError:
        return False
    return True


def is_first_in_line(subscription, account, now):
    """Rule 1: when the account has two or more site bills open, its credit is held for the
    earliest-due one. True when no other payable site of the account is due before this one
    (a tie goes to the older subscription)."""
    mine = (_due_at(subscription), subscription.pk)
    others = (
        Subscription.objects.filter(site__account=account, site__deleted_at__isnull=True)
        .exclude(pk=subscription.pk)
    )
    for other in others:
        if _payable(other, now) and (_due_at(other), other.pk) < mine:
            return False
    return True


def credit_for(subscription, account, amount_centavos, now):
    """The credit a payment of `amount_centavos` for this subscription may take, in centavos:
    the spendable balance, at most the amount, and only for the earliest-due bill. Locks the
    account row. Zero while the switch is off."""
    if not enabled() or amount_centavos <= 0:
        return 0
    lock(account)
    available = balance(account)
    if available <= 0 or not is_first_in_line(subscription, account, now):
        return 0
    return min(available, amount_centavos)


def reserve(payment, account):
    """Take `payment.credit_centavos` out of the spendable balance for the waiting payment."""
    if payment.credit_centavos <= 0:
        return None
    lock(account)
    if balance(account) < payment.credit_centavos:
        raise ApiError("credit_changed", "The Subscription Credit changed. Try again.", status=409)
    return _write(
        account, Kind.RESERVE, -payment.credit_centavos, f"reserve:{payment.pk}", payment=payment,
        detail={"payment": payment.public_id},
    )


def release(payment):
    """A payment that will not settle gives its credit back. Idempotent."""
    if payment.credit_centavos <= 0:
        return None
    account = _account_of(payment)
    lock(account)
    return _write(
        account, Kind.RELEASE, payment.credit_centavos, f"release:{payment.pk}", payment=payment,
        detail={"payment": payment.public_id},
    )


def apply(payment):
    """The confirmed payment has spent its reserve. Moves no balance. Idempotent."""
    if payment.credit_centavos <= 0:
        return None
    return _write(
        _account_of(payment), Kind.APPLY, 0, f"apply:{payment.pk}", payment=payment,
        detail={"payment": payment.public_id, "applied_centavos": payment.credit_centavos},
    )


# Earning

def earn_amount(base_paid_centavos):
    """20% of the base amount paid, rounded to the nearest whole peso (half up), in centavos."""
    if base_paid_centavos <= 0:
        return 0
    return (base_paid_centavos * EARN_PERCENT + 5_000) // 10_000 * 100


def base_paid(payment):
    """The base fee actually paid: the plan price after any credit, usage excluded. The credit
    is taken off the base first (a payment of overage alone has no base)."""
    from apps.billing import plan  # noqa: PLC0415

    if payment.plan_code == plan.USAGE_ONLY:
        return 0
    base = payment.amount_centavos - payment.usage_centavos
    return max(base - payment.credit_centavos, 0)


def _referrer_in_good_standing(referrer, now):
    """Rule 6: at least one site of the referrer is `active` now."""
    from apps.billing import services  # noqa: PLC0415

    for site in Site.objects.filter(account=referrer, deleted_at__isnull=True):
        if services.position(site, now).state == Subscription.State.ACTIVE:
            return True
    return False


def earn(payment, now):
    """The credit a confirmed payment earns its payer's referrer, or None. Call inside the
    confirmation, with the payment confirmed and its cycle settled."""
    if not enabled():
        return None
    payer = _account_of(payment)
    if payer.referred_by_id is None or payer.referred_by_id == payer.pk:
        return None
    key = f"earn:{payment.pk}"
    existing = CreditEntry.objects.filter(idempotency_key=key).first()
    if existing is not None:
        return existing
    amount = earn_amount(base_paid(payment))
    if amount <= 0:
        return None
    referrer = lock(payer.referred_by)
    # R-1, judged after the referrer's lock so two payments of one business cannot both be first.
    earlier = (
        BankTransferPayment.objects.filter(
            subscription__site__account=payer, status=BankTransferPayment.Status.CONFIRMED
        ).exclude(pk=payment.pk)
    )
    if earlier.exists() or not _referrer_in_good_standing(referrer, now):
        return None
    return _write(
        referrer, Kind.EARN, amount, key, payment=payment,
        detail={"base_paid_centavos": base_paid(payment), "payment": payment.public_id},
    )


def earn_safely(payment, now):
    """`earn` in its own savepoint: whatever goes wrong here never blocks a confirmation."""
    try:
        with transaction.atomic():
            return earn(payment, now)
    except Exception:  # noqa: BLE001 - a confirmation must never fail because of a credit
        logger.exception("credit not earned for payment %s", payment.public_id)
        return None


# Reversal

def reverse_for_adjustment(adjustment, original, operator):
    """A credit note (NOCN) that reduces a referred payment's statement takes back the same
    share of the credit that payment earned, from the referrer's spendable balance only: the
    balance never goes below zero, and any part already spent is written off with an audit
    entry. Only billing errors and duplicate payments reverse (plan 4.4). Idempotent."""
    if adjustment.adjustment_reason not in REVERSING_REASONS or original.total_centavos <= 0:
        return None
    key = f"reverse:{adjustment.public_id}"
    if CreditEntry.objects.filter(idempotency_key=key).exists():
        return None
    payment_id = original.source_key.removeprefix("payment:")
    earn_entry = (
        CreditEntry.objects.select_related("payment")
        .filter(kind=Kind.EARN, payment__public_id=payment_id).first()
    )
    if earn_entry is None:
        return None
    referrer = lock(earn_entry.account)
    done = -(
        CreditEntry.objects.filter(kind=Kind.REVERSE, detail__earn=earn_entry.pk)
        .aggregate(t=Sum("amount_centavos"))["t"] or 0
    )
    taken = done + _written_off_so_far(earn_entry)
    share = (earn_entry.amount_centavos * -adjustment.total_centavos * 2 + original.total_centavos) // (
        2 * original.total_centavos
    )
    want = min(share, earn_entry.amount_centavos - taken)
    if want <= 0:
        return None
    take = min(want, balance(referrer))
    lost = want - take
    entry = None
    if take > 0:
        entry = _write(
            referrer, Kind.REVERSE, -take, key, payment=earn_entry.payment,
            detail={
                "earn": earn_entry.pk, "adjustment": adjustment.public_id,
                "written_off": lost,
            },
        )
    if lost > 0:
        audit.record(
            operator, "billing_credit_written_off", target_type="account",
            target_id=referrer.public_id, reason=adjustment.adjustment_reason,
            detail={
                "adjustment": adjustment.public_id, "earn": earn_entry.pk,
                "written_off_centavos": lost, "reversed_centavos": take,
            },
        )
    return entry


def _written_off_so_far(earn_entry):
    """Centavos of this earn already written off by earlier adjustments (from the audit rows)."""
    from apps.operators.models import OperatorAuditEntry  # noqa: PLC0415

    rows = OperatorAuditEntry.objects.filter(
        action="billing_credit_written_off", target_id=earn_entry.account.public_id,
        detail__earn=earn_entry.pk,
    )
    return sum(row.detail.get("written_off_centavos", 0) for row in rows)


def reverse_safely(adjustment, original, operator):
    """`reverse_for_adjustment` in its own savepoint: a credit never blocks a credit note."""
    try:
        with transaction.atomic():
            return reverse_for_adjustment(adjustment, original, operator)
    except Exception:  # noqa: BLE001
        logger.exception("credit not reversed for adjustment %s", adjustment.public_id)
        return None


# HQ

def correct(operator, account, amount_centavos, reason, *, request_id=None):
    """One audited manual correction (`correction`), only on Coach Ed's word for each case. A
    reason is required. A reduction can never take the spendable balance below zero. Returns
    the entry; the same `request_id` again returns the entry already written."""
    fields = {}
    if isinstance(amount_centavos, bool) or not isinstance(amount_centavos, int) or (
        amount_centavos == 0 or abs(amount_centavos) > MAX_CORRECTION_CENTAVOS
    ):
        fields["amount_centavos"] = ["Enter a whole number of centavos, not zero."]
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        fields["reason"] = ["A reason is needed."]
    elif len(reason) > MAX_REASON:
        fields["reason"] = ["The reason is too long."]
    if request_id is not None and (
        not isinstance(request_id, str) or not 1 <= len(request_id) <= 60
    ):
        fields["request_id"] = ["The request id is not valid."]
    if fields:
        raise ApiError("invalid_request", "Some values are missing or invalid.", fields=fields)
    key = f"correction:{request_id or uuid.uuid4().hex}"
    with transaction.atomic():
        account = lock(account)
        existing = CreditEntry.objects.filter(idempotency_key=key).first()
        if existing is not None:
            if existing.account_id != account.pk:
                raise ApiError("reference_reused", "This request id is already used.", status=409)
            return existing
        if amount_centavos < 0 and balance(account) < -amount_centavos:
            raise ApiError(
                "exceeds_balance", "That is more than the spendable balance.", status=409
            )
        entry = _write(
            account, Kind.CORRECTION, amount_centavos, key, reason=reason, operator=operator,
            detail={},
        )
        audit.record(
            operator, "billing_credit_correction", target_type="account",
            target_id=account.public_id, reason=reason,
            detail={"amount_centavos": amount_centavos, "entry": entry.pk},
        )
    return entry


def _entry_row(entry):
    return {
        "id": entry.pk, "kind": entry.kind, "amount_centavos": entry.amount_centavos,
        "payment": entry.payment.public_id if entry.payment_id else None,
        "reason": entry.reason, "operator": entry.operator.username if entry.operator_id else None,
        "recorded_at": entry.recorded_at.isoformat(),
    }


def hq_view(account, operator):
    """The read-only Referrals view of one account: who referred it, the businesses it
    referred, its balance and its ledger rows. Ids and counts only; no email or phone."""
    referred = (
        Account.objects.filter(referred_by=account).order_by("pk")
        .annotate(payments=Count("sites__subscription__transfers"))
    )
    rows = list(
        CreditEntry.objects.filter(account=account).select_related("payment", "operator")
        .order_by("-pk")[:200]
    )
    audit.record(
        operator, "hq_referrals_viewed", target_type="account", target_id=account.public_id,
        detail={"entries": len(rows)},
    )
    return {
        "account": account.public_id,
        "referral_code": account.referral_code,
        "referred_by": account.referred_by.public_id if account.referred_by_id else None,
        "referred": [
            {"account": row.public_id, "joined_at": row.created_at.isoformat()}
            for row in referred
        ],
        "balance_centavos": balance(account),
        "reserved_centavos": reserved(account),
        "earned_count": earned_count(account),
        "entries": [_entry_row(entry) for entry in rows],
    }


def account_overview(account):
    """What the signed-in owner reads. `started` is false while the switch is off; the figures
    are then zero and nothing about the ledger is read."""
    link = f"{settings.SITE_URL}/?ref={account.referral_code}"
    if not enabled():
        return {
            "started": False, "code": account.referral_code, "link": link,
            "balance": 0, "reserved": 0, "earned_count": 0,
        }
    return {
        "started": True, "code": account.referral_code, "link": link,
        "balance": balance(account), "reserved": reserved(account),
        "earned_count": earned_count(account),
    }
