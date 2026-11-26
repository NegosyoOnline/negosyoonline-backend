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
from apps.billing import clock, meters, plan, services, usage
from apps.billing.models import (
    BankTransferPayment, BillingCycle, BillingNotice, ChargeLine, Subscription,
    SubscriptionTransition,
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

def _transferred(payment):
    """What the Subscriber transfers: the amount due less any Subscription Credit (Billing B6)."""
    return payment.amount_centavos - payment.credit_centavos


def _credit_line(payment):
    """One line when Subscription Credit paid part of the bill (draft wording, Billing B6)."""
    if not payment.credit_centavos:
        return ""
    from apps.billing import credits  # noqa: PLC0415

    return f" Subscription Credit applied: \u2212{credits.money(payment.credit_centavos)}."


def _proof_received(payment, hi):
    return "We received your bank transfer proof", (
        f"{hi} we received the proof of your bank transfer of {_money(_transferred(payment))} "
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
    if cycle is None:  # a payment of overage alone buys no term
        what = f"we approved your payment of {_money(_transferred(payment))} for {site}."
        extra = _credit_line(payment)
    else:
        bought = plan.version_of(payment.plan_code, payment.plan_version or 1)
        what = (
            f"we approved your payment of {_money(_transferred(payment))} for {site}: "
            f"{bought.name}, {_day(cycle.start_at)} to {_day(cycle.end_at)}."
        )
        extra = _credit_line(payment) + _approved_next(payment)
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
        f"({_money(_transferred(payment))}). Reason: {reason}.{retry} If you think this is a "
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


def _export_notice(subscription, hi):
    """The service-end export notice (G4b, wording B approved in the plan, section 9)."""
    site = subscription.site.display_name
    if subscription.state == State.CANCELLED_NO_PAYMENT_METHOD:
        reason = (
            f"The free trial for {site} ended on {_day_time(subscription.service_ended_at)}, "
            f"and no payment was approved."
        )
    else:
        reason = f"Your subscription for {site} ended on {_day_time(subscription.service_ended_at)}."
    until = _day_time(subscription.download_until)
    deleted = _day_time(subscription.deletion_due_at)
    return "Your NegosyoOnline store data: how to ask for your export", (
        f"{hi}\n\n{reason}\n\n"
        f"Your store data\n"
        f"You can ask for an export of your store data for 30 days after your service ended. "
        f"To ask, email {SUPPORT_EMAIL} from the email address on your account. We will send "
        f"the export to that address. Please ask by {until}.\n\n"
        f"What happens after\n"
        f"After {deleted}, as our Terms say, we delete or anonymise the store data of {site}. "
        f"This includes the receiving-account details you saved for your store (account name, "
        f"account number and QR code). We do not delete what the law requires us to keep, or "
        f"what we must hold for a dispute or a fraud check. Billing records are kept as "
        f"required.\n\n"
        f"Bringing your site back\n"
        f"Until {deleted} you can still bring your site back: pay by bank transfer and send "
        f"the proof on your Billing page: {_billing_link()}. You can still sign in and open "
        f"your Billing page.\n\n"
        f"Questions: {SUPPORT_EMAIL}.\n\n"
        f"All dates are Manila time.\n\n{SIGN_OFF}"
    )


def order_meter_text(level, hi):
    """The OV-6 owner emails: at 80% of the 500 orders included in a billing month, and at 100%.
    Wording from the task, approved with the plan (Coach Ed, 2026-11-23); local preview first."""
    included = plan.ORDERS_INCLUDED_PER_MONTH
    unit = plan.pesos(plan.ORDER_OVERAGE_CENTAVOS_PER_ORDER)
    tail = (
        f"Orders above {included} are billed at {plan.CURRENCY} {unit} each on your next bill. "
        f"Your store keeps taking orders as usual."
    )
    if level == "80":
        used = included * 4 // 5
        return f"You've used {used} of your {included} orders this month", (
            f"{hi} You have used {used} of the {included} orders included this month. "
            f"{tail}\n\n{SIGN_OFF}"
        )
    return f"You've reached your plan's {included} orders", (
        f"{hi} You have reached your plan's {included} orders this month. {tail}\n\n{SIGN_OFF}"
    )


def _orders_bill(line, hi, due_at):
    """The usage-only bill of an annual or Launch Promo term for one counted month of orders."""
    site = line.subscription.site.display_name
    first, last = usage.period_bounds(line.cycle, line.period_index, line.subscription.anchor_day)
    label = usage.line_label(line.kind, line.quantity, line.amount_centavos)
    return "Your NegosyoOnline orders-over-500 bill", (
        f"{hi} {site} had more than the {plan.ORDERS_INCLUDED_PER_MONTH} orders included from "
        f"{_day(first)} to {_day(last)}. {label} = {_money(line.amount_centavos)}. You can pay "
        f"it on its own by bank transfer and send the proof on your Billing page: "
        f"{_billing_link()}. Please pay by {_day(due_at)}. Until it is paid it is added to your "
        f"next payment.\n\n{SIGN_OFF}"
    )


def _staff_alert(payment):
    where = (
        f"Open the Finance page in HQ: {settings.HQ_STAFF_ORIGIN}"
        if settings.HQ_STAFF_ORIGIN else "Open the Finance page in HQ."
    )
    return f"Bank transfer proof waiting: {payment.public_id}", (
        f"A bank transfer proof is waiting for review: {_money(_transferred(payment))}, "
        f"{_plan_name(payment)}, sent {_day_time(payment.submitted_at)}. {where} No customer "
        f"details are in this email."
    )


def _plan_name(payment):
    if payment.plan_code == plan.USAGE_ONLY:
        return {
            "storage": "storage overage", "orders": "orders over 500",
        }.get(usage.overage_name(payment), "usage overage")
    return plan.version_of(payment.plan_code, payment.plan_version or 1).name


def _payable(subscription, now):
    """Whether the Billing page offers a payment now and no proof is waiting."""
    options = services.payment_options(subscription, now)
    return options["payable"] and options["under_review"] is None


def hold_notice(site, *, paused, key):
    """The owner's email when HQ suspends or resumes the store by hand, once for `key`. The reason
    is never in it. Sent after the change is saved: a failure is logged and changes nothing."""
    to = site.account.email
    if site.deleted_at is not None or site.account.closed_at is not None or is_disposed_address(to):
        return False
    name = site.display_name
    if paused:
        code, subject = "hq_site_paused", "Your store is paused"
        text = (
            f"{_greeting(site.account)} your store {name} has been paused by NegosyoOnline under "
            f"our Terms of Service. Your site and checkout are offline. Reply to this email or "
            f"message us to resolve it: {SUPPORT_EMAIL}.\n\n{SIGN_OFF}"
        )
    else:
        code, subject = "hq_site_resumed", "Your store is back online"
        text = f"{_greeting(site.account)} your store {name} is back online.\n\n{SIGN_OFF}"
    subscription = Subscription.objects.filter(site=site).first()
    try:
        if subscription is not None:
            return _send_once(subscription, key, code, to, subject, text, timezone.now())
        send_email(to=to, category=f"billing_{code}", subject=subject, body=text)
        return True
    except EmailNotSent:
        return False


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


def _order_meter_emails(now):
    """OV-6: one email at 400 of the 500 orders and one at 500, once per billing month, for a
    paid month that is chargeable (OV-8). None for a trial, an exempt site or a month before the
    notice date. Off with the order switch or OWNER_NOTICES_ENABLED: nothing is read."""
    sent = Counter()
    if not (plan.orders_enabled() and settings.OWNER_NOTICES_ENABLED) or plan.charged_from() is None:
        return sent
    paid_now = Subscription.objects.select_related("site__account").filter(
        billing_exempt=False, cycles__settlement=BillingCycle.Settlement.PAID,
        cycles__start_at__lte=now, cycles__end_at__gt=now,
    ).distinct().order_by("pk")
    included = plan.ORDERS_INCLUDED_PER_MONTH
    for sub in paid_now:
        found = services.allowance_period_at(sub, now)
        if found is None or not plan.orders_chargeable(found["start_at"]):
            continue
        to = _reachable(sub)
        if not to:
            continue
        count = meters.orders_in(sub.site, found["start_at"], found["end_at"])
        level = "100" if count >= included else "80" if count >= included * 4 // 5 else None
        if level is None:
            continue
        subject, body = order_meter_text(level, _greeting(sub.site.account))
        key = f"orders_{level}:{sub.pk}:{found['cycle'].pk}:{found['index']}"
        if _send_once(sub, key, f"orders_{level}", to, subject, body, now):
            sent[f"orders_{level}"] += 1
    return sent


def _orders_bill_emails(now):
    """OV-3: a multi-month term (annual, Launch Promo) is billed for orders alone, after each
    counted month: this is the trigger. One email per line, due 7 days after it was made, only
    while the Billing page offers that payment on its own. Off with the order switch or
    OWNER_NOTICES_ENABLED. A line is looked at for 48 hours after its count."""
    sent = Counter()
    if not (plan.orders_enabled() and settings.OWNER_NOTICES_ENABLED):
        return sent
    lines = ChargeLine.objects.filter(
        kind=ChargeLine.Kind.OVERAGE_ORDERS, status=ChargeLine.Status.OPEN,
        entry__received_at__gte=now - WINDOW, cycle__term_months__gt=1,
        subscription__billing_exempt=False,
    ).select_related("subscription__site__account", "cycle", "entry").order_by("pk")
    for line in lines:
        sub = line.subscription
        to = _reachable(sub)
        if not to or _has_proof_waiting(sub):
            continue
        if not services.payment_options(sub, now)["usage"]["payable_alone"]:
            continue  # it rides on the term's own payment
        subject, body = _orders_bill(line, _greeting(sub.site.account), line.entry.received_at + usage.ORDER_BILL_DUE)
        if _send_once(sub, f"orders_bill:{line.pk}", "orders_bill", to, subject, body, now):
            sent["orders_bill"] += 1
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


def _announceable_at(subscription):
    """When the service end became known to the owner, or None while the dates are not set."""
    if subscription.download_until is None or subscription.deletion_due_at is None:
        return None
    at = subscription.download_until - plan.DOWNLOAD_WINDOW  # the service end, or the clocks start
    ended = subscription.transitions.filter(
        code__in=("cancelled", "trial_ended_no_method")
    ).order_by("-effective_at").first()
    if ended is not None and ended.effective_at > at:
        at = ended.effective_at  # a cancel while suspended counts from the cancellation
    return at


def _export_notice_emails(now):
    """The service-end export notice for each ended subscription, once per service end.

    Off with OWNER_NOTICES_ENABLED: nothing is read. Only a service end announceable in the
    last 48 hours is sent, so switching on mails no backlog.
    """
    sent = Counter()
    if not settings.OWNER_NOTICES_ENABLED:
        return sent
    subscriptions = Subscription.objects.select_related("site__account").filter(
        billing_exempt=False,
        state__in=(State.CANCELLED, State.CANCELLED_NO_PAYMENT_METHOD),
        service_ended_at__isnull=False,
        service_ended_at__lte=now,
        download_until__isnull=False,
        deletion_due_at__isnull=False,
    ).order_by("pk")
    for sub in subscriptions:
        announceable = _announceable_at(sub)
        if announceable is None or not (announceable <= now < announceable + WINDOW):
            continue
        to = _reachable(sub)
        if not to:
            continue
        subject, body = _export_notice(sub, _greeting(sub.site.account))
        key = f"export_notice:{sub.pk}:{int(sub.service_ended_at.timestamp())}"
        if _send_once(sub, key, "export_notice", to, subject, body, now):
            sent["export_notice"] += 1
    return sent


def run(now=None):
    """Send every billing email that is due. Returns a count per code. Safe to run twice."""
    if not (settings.BILLING_ENABLED and plan.collection_live()):
        return {}
    now = now or timezone.now()
    sent = Counter()
    for scan in (
        _payment_emails, _reminder_emails, _transition_emails, _export_notice_emails,
        _order_meter_emails, _orders_bill_emails,
    ):
        try:
            sent.update(scan(now))
        except Exception:
            logger.exception("Billing notices scan failed (%s)", scan.__name__)
    result = dict(sorted(sent.items()))
    logger.info("Billing notices: %s", result or "nothing sent")
    return result
