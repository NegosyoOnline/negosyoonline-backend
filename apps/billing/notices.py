"""The billing emails (Billing Build B2a-E): one scan every 5 minutes that reads the stored
state and sends what is due, at most once for each key.

Nothing here runs inside a payment action. `submit_transfer`, `confirm_transfer` and
`reject_transfer` are untouched, so a slow mail server never delays a payment. Nothing is
sent unless `plan.collection_live()` is true, and with it false the scan reads no billing
table. A disposed address is never emailed. A send that fails writes no `BillingNotice`, so
the next scan tries again; an event is looked at for 48 hours only, so switching collection
on does not mail a backlog. Wording approved by Coach Ed, 2026-11-13.
"""
import logging
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import SUPPORT_EMAIL
from apps.billing import clock, plan, services
from apps.billing.models import (
    BankTransferPayment, BillingNotice, Subscription, SubscriptionTransition,
)
from apps.common.disposal import is_disposed_address
from apps.notifications.services import EmailNotSent, send_email

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=48)  # an event older than this is no longer announced
TRIAL_REMINDERS = ((timedelta(hours=24), "1d"), (timedelta(days=3), "3d"))  # nearest first
RENEWAL_REMINDERS = ((timedelta(days=3), "3d"), (timedelta(days=7), "7d"))
Status = BankTransferPayment.Status
State = Subscription.State

REASONS = {
    "unreadable": "the file could not be read",
    "amount_mismatch": "the amount does not match the amount due",
    "not_received": "we did not find the transfer in our account",
    "wrong_account": "the transfer was sent to an account that is not NegosyoOnline's",
    "duplicate": "this proof was already sent",
}

SIGN_OFF = "NegosyoOnline"


def _money(centavos):
    return f"{plan.CURRENCY} {centavos / 100:,.2f}"


def _day(moment):
    local = moment.astimezone(clock.MANILA)
    return f"{local.day} {local:%B %Y}"


def _day_time(moment):
    local = moment.astimezone(clock.MANILA)
    return f"{_day(moment)} at {local.hour % 12 or 12}:{local:%M} {local:%p}"


def _billing_link():
    return f"{settings.DASHBOARD_URL}/settings/billing/"


def _greeting(account):
    name = account.name.strip()
    return f"Hello {name}," if name else "Hello,"


def _reachable(subscription):
    """The address to write to, or None: the site or account is gone, or the address is disposed."""
    site = subscription.site
    account = site.account
    if site.deleted_at is not None or account.closed_at is not None:
        return None
    if is_disposed_address(account.email):
        return None
    return account.email


def _send_once(subscription, key, code, to, subject, body, now):
    """Send one email unless `key` was sent. True when it was sent now."""
    with transaction.atomic():
        # The lock the lifecycle's writes take: two scans cannot both pass the check below.
        locked = Subscription.objects.select_for_update().get(pk=subscription.pk)
        if BillingNotice.objects.filter(idempotency_key=key).exists():
            return False
        try:
            send_email(to=to, category=f"billing_{code}", subject=subject, body=body)
        except EmailNotSent:
            logger.warning("Billing email not sent (code=%s); it is tried again.", code)
            return False
        BillingNotice.objects.create(
            subscription=locked, code=code, idempotency_key=key, sent_at=now
        )
    return True


# The texts. Plain, from DEFAULT_FROM_EMAIL, no attachment, nothing about a proof file, a bank
# account, a tax claim or deletion.

def _proof_received(payment, hi):
    return "We received your bank transfer proof", (
        f"{hi} we received the proof of your bank transfer of {_money(payment.amount_centavos)} "
        f"for {payment.subscription.site.display_name}. Our team will review it. Your payment "
        f"counts only after it is approved; we will email you the result. You can see it on "
        f"your Billing page: {_billing_link()}.\n\n{SIGN_OFF}"
    )


def _approved_next(payment):
    cycle = payment.cycle
    if cycle is None:
        return ""
    following = plan.following(cycle)
    if following.code == plan.MONTHLY.code and cycle.plan_code == plan.LAUNCH_PROMO.code:
        return f" Your next payment of {_money(following.price_centavos)} is due on {_day(cycle.end_at)}."
    return f" Your next payment is due on {_day(cycle.end_at)}."


def _payment_approved(payment, hi):
    site = payment.subscription.site.display_name
    cycle = payment.cycle
    if cycle is None:  # a payment of storage overage alone buys no term
        what = f"we approved your payment of {_money(payment.amount_centavos)} for {site}."
        extra = ""
    else:
        bought = plan.version_of(payment.plan_code, payment.plan_version or 1)
        what = (
            f"we approved your payment of {_money(payment.amount_centavos)} for {site}: "
            f"{bought.name}, {_day(cycle.start_at)} to {_day(cycle.end_at)}."
        )
        extra = _approved_next(payment)
        if payment.subscription.transitions.filter(
            code="reactivated", detail__cycle=cycle.sequence
        ).exists():
            extra += " Your site is active again."
    return "Your payment is approved", (
        f"{hi} {what}{extra} View it on your Billing page: {_billing_link()}.\n\n{SIGN_OFF}"
    )


def _payment_rejected(payment, hi, payable):
    site = payment.subscription.site.display_name
    reason = (
        payment.reject_note if payment.reject_reason == "other"
        else REASONS.get(payment.reject_reason, "")
    ).rstrip(". ")
    retry = f" Please send a new proof on your Billing page: {_billing_link()}." if payable else ""
    return "We could not approve your payment proof", (
        f"{hi} we could not approve the proof you sent for {site} "
        f"({_money(payment.amount_centavos)}). Reason: {reason}.{retry} If you think this is a "
        f"mistake, contact {SUPPORT_EMAIL}.\n\n{SIGN_OFF}"
    )


def _trial_reminder(subscription, hi, label):
    site = subscription.site.display_name
    if label == "3d":
        subject = "Your NegosyoOnline trial ends in 3 days"
        when = _day(subscription.trial_end_at)
    else:
        subject = "Your NegosyoOnline trial ends tomorrow"
        when = _day_time(subscription.trial_end_at)
    return subject, (
        f"{hi} your free trial for {site} ends on {when}. To keep your site running, pay "
        f"by bank transfer and send the proof on your Billing page: {_billing_link()}. After "
        f"the trial ends your site is paused until your payment is approved.\n\n{SIGN_OFF}"
    )


def _renewal_reminder(subscription, hi, terms):
    site = subscription.site.display_name
    due = _day(terms["due_at"])
    return f"Your NegosyoOnline payment is due on {due}", (
        f"{hi} your next payment for {site} is {_money(terms['amount_centavos'])}, due on "
        f"{due}. Pay by bank transfer and send the proof on your Billing page: "
        f"{_billing_link()}. If it is not approved by then, your site stays on for a short "
        f"time and is then paused.\n\n{SIGN_OFF}"
    )


def _paused(subscription, hi, first):
    pay_by = subscription.deletion_due_at
    until = f" Payment can be accepted until {_day(pay_by)}." if pay_by else ""
    return first[0], (
        f"{hi} {first[1]} Your storefront, checkout and the dashboard pages for running "
        f"your store are not available. You can still sign in and open your Billing page. To "
        f"bring your site back, pay by bank transfer and send the proof: "
        f"{_billing_link()}.{until} Questions: {SUPPORT_EMAIL}.\n\n{SIGN_OFF}"
    )


def _staff_alert(payment):
    where = (
        f"Open the Finance page in HQ: {settings.HQ_STAFF_ORIGIN}"
        if settings.HQ_STAFF_ORIGIN else "Open the Finance page in HQ."
    )
    return f"Bank transfer proof waiting: {payment.public_id}", (
        f"A bank transfer proof is waiting for review: {_money(payment.amount_centavos)}, "
        f"{_plan_name(payment)}, sent {_day_time(payment.submitted_at)}. {where} No customer "
        f"details are in this email."
    )


def _plan_name(payment):
    if payment.plan_code == plan.USAGE_ONLY:
        return "storage overage"
    return plan.version_of(payment.plan_code, payment.plan_version or 1).name


def _payable(subscription, now):
    """Whether the Billing page offers a payment now and no proof is waiting."""
    options = services.payment_options(subscription, now)
    return options["payable"] and options["under_review"] is None


# The scans. Each returns the number of emails it sent.

def _payments(now, **filters):
    return BankTransferPayment.objects.filter(**filters).select_related(
        "subscription__site__account", "cycle"
    ).order_by("pk")


def _payment_emails(now):
    since = now - WINDOW
    sent = Counter()
    for payment in _payments(now, status=Status.SUBMITTED, submitted_at__gte=since):
        sub = payment.subscription
        to = _reachable(sub)
        if to:
            subject, body = _proof_received(payment, _greeting(sub.site.account))
            if _send_once(sub, f"proof_received:{payment.pk}", "proof_received", to, subject, body, now):
                sent["proof_received"] += 1
        alert = settings.BILLING_STAFF_ALERT_EMAIL
        if alert:
            subject, body = _staff_alert(payment)
            if _send_once(sub, f"staff_proof_waiting:{payment.pk}", "staff_proof_waiting", alert, subject, body, now):
                sent["staff_proof_waiting"] += 1
    for payment in _payments(now, status=Status.CONFIRMED, decided_at__gte=since):
        sub = payment.subscription
        to = _reachable(sub)
        if to:
            subject, body = _payment_approved(payment, _greeting(sub.site.account))
            if _send_once(sub, f"payment_approved:{payment.pk}", "payment_approved", to, subject, body, now):
                sent["payment_approved"] += 1
    for payment in _payments(now, status=Status.REJECTED, decided_at__gte=since):
        sub = payment.subscription
        to = _reachable(sub)
        if to:
            subject, body = _payment_rejected(
                payment, _greeting(sub.site.account), _payable(sub, now)
            )
            if _send_once(sub, f"payment_rejected:{payment.pk}", "payment_rejected", to, subject, body, now):
                sent["payment_rejected"] += 1
    return sent


def _has_proof_waiting(subscription):
    return subscription.transfers.filter(status=Status.SUBMITTED).exists()


def _nearest(remaining, steps):
    """The label of the nearest step that `remaining` has reached, or None."""
    for span, label in steps:
        if remaining <= span:
            return label
    return None


def _reminder_emails(now):
    sent = Counter()
    subscriptions = Subscription.objects.select_related("site__account").filter(
        billing_exempt=False
    )
    trials = subscriptions.filter(
        state=State.TRIAL_ACTIVE, trial_end_at__gt=now, trial_end_at__lte=now + TRIAL_REMINDERS[-1][0]
    )
    for sub in trials:
        label = _nearest(sub.trial_end_at - now, TRIAL_REMINDERS)
        to = _reachable(sub)
        if not label or not to or _has_proof_waiting(sub):
            continue
        subject, body = _trial_reminder(sub, _greeting(sub.site.account), label)
        code = f"trial_reminder_{label}"
        if _send_once(sub, f"trial_reminder:{sub.pk}:{label}", code, to, subject, body, now):
            sent[code] += 1
    for sub in subscriptions.filter(state=State.ACTIVE):
        terms = services.renewal_terms(sub)
        if terms is None or terms["due_at"] <= now:
            continue
        label = _nearest(terms["due_at"] - now, RENEWAL_REMINDERS)
        to = _reachable(sub)
        if not label or not to or _has_proof_waiting(sub) or not _payable(sub, now):
            continue
        sequence = services._latest_paid_cycle(sub).sequence
        subject, body = _renewal_reminder(sub, _greeting(sub.site.account), terms)
        code = f"renewal_reminder_{label}"
        if _send_once(sub, f"renewal_reminder:{sub.pk}:{sequence}:{label}", code, to, subject, body, now):
            sent[code] += 1
    return sent


def _transition_emails(now):
    sent = Counter()
    transitions = SubscriptionTransition.objects.filter(
        code__in=("suspended_no_outcome", "suspended_after_attention", "trial_ended_no_method"),
        effective_at__gte=now - WINDOW,  # the rule's time; the sweep writes it within minutes
        effective_at__lte=now,
    ).select_related("subscription__site__account").order_by("pk")
    for transition in transitions:
        sub = transition.subscription
        if sub.billing_exempt:
            continue
        hi = _greeting(sub.site.account)
        if transition.code == "trial_ended_no_method":
            if sub.state != State.CANCELLED_NO_PAYMENT_METHOD:
                continue
            first = (
                "Your NegosyoOnline trial has ended",
                f"the free trial for {sub.site.display_name} has ended and your site is paused.",
            )
            key, code = f"trial_ended:{transition.pk}", "trial_ended"
        else:
            if sub.state != State.SUSPENDED:  # paid since: nothing to say (G4b)
                continue
            first = (
                "Your NegosyoOnline site is paused",
                f"your site {sub.site.display_name} is paused because we have no approved "
                f"payment for it.",
            )
            key, code = f"suspended:{transition.pk}", "service_paused"
        to = _reachable(sub)
        if not to:
            continue
        subject, body = _paused(sub, hi, first)
        if _send_once(sub, key, code, to, subject, body, now):
            sent[code] += 1
    return sent


def run(now=None):
    """Send every billing email that is due. Returns a count per code. Safe to run twice."""
    if not (settings.BILLING_ENABLED and plan.collection_live()):
        return {}
    now = now or timezone.now()
    sent = Counter()
    for scan in (_payment_emails, _reminder_emails, _transition_emails):
        try:
            sent.update(scan(now))
        except Exception:
            logger.exception("Billing notices scan failed (%s)", scan.__name__)
    result = dict(sorted(sent.items()))
    logger.info("Billing notices: %s", result or "nothing sent")
    return result
