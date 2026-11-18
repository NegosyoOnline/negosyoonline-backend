"""Xendit hosted-invoice payment of a Membership Pack order (port of TWCinc
negosyoonline_sante_pack/placement.py invoice steps and payments.py, without metering).

Every call here uses the site's OWN key (apps.payments.services.api_key_for); a site has no
platform key. Only an authenticated webhook (apps.payments.webhooks) or a server-to-server read
of the invoice at Xendit (apps.payments.reconcile) reaches `apply_state`; nothing the browser
sends ever does. The amount is the server's constant. Lock order: the Site row, then the order
row. Logs carry site ids only.
"""
import logging
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.common import throttle
from apps.common.errors import ApiError
from apps.members import services as members
from apps.pack import constants as C, notices, registration
from apps.pack.models import PackOrder
from apps.payments import services as payments, xendit_client
from apps.sites.models import Site

logger = logging.getLogger(__name__)

CLAIM_SECONDS = 60
WAIT_SECONDS, WAIT_STEP = 10, 0.5
INVOICELESS_EXPIRY = timedelta(hours=2)  # no stored invoice this long after placement: one last lookup, then expired
READ_GRACE = timedelta(hours=1)  # reconcile reads an invoice until this long after its expiry


def amount():
    """The server's constant, in pesos."""
    return Decimal(C.PRICE_CENTAVOS) / 100


def new_external_id(site):
    return f"{C.EXTERNAL_PREFIX}{site.pk}-{uuid.uuid4().hex[:24]}"


def _expiry(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return timezone.now() + timedelta(seconds=C.INVOICE_SECONDS)


def _save(order, fields):
    order.state_version += 1
    order.save(update_fields=[*fields, "state_version", "updated_at"])


def _problem(order, kind):
    """A payment that needs a person (a late payment, a wrong amount, several invoices). The event on
    the order is the record; the logs carry ids only."""
    logger.warning("pack xendit payment needs attention site=%s order=%s kind=%s", order.site_id, order.pk, kind)


# ---- the invoice step (placement and retry) ----

def store_invoice(order, inv):
    """Under the caller's order row lock: record the adopted or created invoice."""
    order.xendit_invoice_id = str(inv.get("id") or "")[:64]
    order.xendit_invoice_url = str(inv.get("invoice_url") or "")[:500]
    order.xendit_expires_at = _expiry(inv.get("expiry_date"))
    _save(order, ["xendit_invoice_id", "xendit_invoice_url", "xendit_expires_at"])
    return order


def return_url(site):
    """The storefront page Xendit sends the buyer back to (success and failure alike): built from the
    site's public host, never from the request. "" when the site has none."""
    from apps.sites import custom_hosts
    base = custom_hosts.public_base_url(site)
    return f"{base}{C.RETURN_PATH}" if base else ""


def ensure_invoice(order_pk):
    """Create or adopt the site's Xendit invoice for this order. The calls at Xendit run OUTSIDE the
    order row lock: read, release, call, then lock again only to store. A short cache claim keeps two
    concurrent calls from both creating; the lookup by our reference before every create keeps a retry
    after a timeout from creating a second invoice (several found: the newest unexpired is adopted).
    A missing key or an unreachable Xendit raises 503 xendit_unavailable and the order stays
    awaiting_xendit, so the return page offers Try again. Returns the order."""
    order = PackOrder.objects.select_related("site").get(pk=order_pk)
    if order.xendit_invoice_url or order.payment_status != C.AWAITING_XENDIT:
        return order
    api_key = payments.api_key_for(order.site)
    back = return_url(order.site)
    if not api_key or not back:
        raise ApiError("xendit_unavailable", C.MSG_PAYMENT_UNAVAILABLE, status=503)
    claim = f"pack-invoice-claim:{order.pk}"
    if not cache.add(claim, 1, CLAIM_SECONDS):
        return wait_for_invoice(order.pk)
    try:
        try:
            found = xendit_client.find_invoices(api_key, order.xendit_external_id)
            if len(found) > 1:
                _problem(order, "several_invoices")
            inv = xendit_client.newest_unexpired(found) or xendit_client.create_invoice(
                api_key, external_id=order.xendit_external_id, amount=amount(),
                description=f"Membership Pack {order.order_number}", duration_seconds=C.INVOICE_SECONDS,
                success_url=back, failure_url=back,
            )
        except xendit_client.XenditError as exc:
            if exc.kind == "unauthorized":
                try:
                    payments.mark_key_failed(order.site)
                except Exception:  # noqa: BLE001 - never escapes; the buyer always gets the answer below
                    logger.exception("pack xendit mark_key_failed failed site=%s", order.site_id)
            raise ApiError("xendit_unavailable", C.MSG_PAYMENT_UNAVAILABLE, status=503) from None
        with transaction.atomic():
            order = PackOrder.objects.select_for_update().select_related("site").get(pk=order_pk)
            if order.xendit_invoice_url or order.payment_status != C.AWAITING_XENDIT:
                return order  # a webhook or reconcile got there first
            store_invoice(order, inv)
    finally:
        cache.delete(claim)
    return order


def _sleep(seconds):
    time.sleep(seconds)  # replaced in tests


def wait_for_invoice(order_pk):
    """Another call holds the invoice step for this order: re-read until its stored URL appears (at most
    10 seconds), never treating the order as invoice-less. Not in time: 409 invoice_busy."""
    waited = 0.0
    while True:
        order = PackOrder.objects.select_related("site").get(pk=order_pk)
        if order.xendit_invoice_url or order.payment_status != C.AWAITING_XENDIT:
            return order
        if waited >= WAIT_SECONDS:
            raise ApiError("invoice_busy", C.MSG_INVOICE_BUSY, status=409)
        _sleep(WAIT_STEP)
        waited += WAIT_STEP


def continue_url(order):
    """The order's OWN stored invoice URL while awaiting_xendit and unexpired, else None. Never a
    browser-supplied URL."""
    if order is None or order.payment_status != C.AWAITING_XENDIT or not order.xendit_invoice_url:
        return None
    if order.xendit_expires_at and order.xendit_expires_at <= timezone.now():
        return None
    return order.xendit_invoice_url


def retry_invoice(order):
    """Try again for an invoice-less awaiting_xendit order: the same order, never a new one; 5 per hour
    per order."""
    if order.payment_status != C.AWAITING_XENDIT or order.xendit_invoice_url:
        raise ApiError("nothing_to_retry", C.MSG_CHECKOUT_EXPIRED, status=409)
    scope, limit, window = C.INVOICE_RETRY_LIMIT
    throttle.hit(scope, order.pk, limit=limit, window=window)
    return ensure_invoice(order.pk)


def read_once(order, every=5):
    """Ask Xendit itself (server to server, the site's own key) for the invoice of a waiting order
    instead of waiting for the webhook or the 15-minute run; at most one read per order every few
    seconds however fast the page polls. Never fails the caller. The browser decides nothing."""
    if order.payment_status != C.AWAITING_XENDIT or not order.xendit_invoice_id:
        return order
    if cache.add(f"pack-verify:{order.pk}", 1, every):
        from apps.payments import reconcile
        reconcile.verify_invoice(order.site, order.xendit_invoice_id, order.xendit_external_id)
        order.refresh_from_db()
    return order


# ---- the outcome of an event (webhook or server-side read) ----

def apply_state(site, state, *, now=None):
    """Apply one verified invoice state to the order it names. Runs inside the caller's transaction.
    Site row first, then the order row; "another live order" is read only after both locks, so a late
    payment and a returning member's placement serialize. Matching is by (site, external_id) only.
    Returns (outcome, order pk or None)."""
    Site.objects.select_for_update().filter(pk=site.pk).first()
    order = (
        PackOrder.objects.select_for_update().select_related("member")
        .filter(site=site, xendit_external_id=state.external_id).first()
    )
    if order is None:
        return "no_match", None
    now = now or timezone.now()
    paid_like = state.status in ("PAID", "SETTLED")
    # An event whose reference matches is this order's, even from another invoice.
    other_invoice = bool(order.xendit_invoice_id and state.invoice_id and state.invoice_id != order.xendit_invoice_id)
    # Finality first.
    if order.payment_status == C.VERIFIED:
        if paid_like and other_invoice:
            order.add_event(C.EVENT_XENDIT_SUPERSEDED, new={"invoice_id": state.invoice_id, "second_invoice": True})
            _problem(order, "second_invoice")
            return "superseded", order.pk
        return ("duplicate_state" if paid_like else "ignored"), order.pk
    if state.status == "EXPIRED":
        if order.payment_status == C.AWAITING_XENDIT and not other_invoice:
            order.payment_status = C.XENDIT_EXPIRED
            _save(order, ["payment_status"])
            order.add_event(C.EVENT_XENDIT_EXPIRED, new={"invoice_id": state.invoice_id})
            return "applied", order.pk
        return "ignored", order.pk
    if not paid_like:
        return "ignored", order.pk  # PENDING, FAILED and the rest: the event row is the record
    live = order.payment_status in C.LIVE_STATUSES
    # A payment on a non-live order while the member has another live order: money arrived on a closed
    # order; recorded, never applied (the one-live-order rule).
    newer = (
        PackOrder.objects.filter(member_id=order.member_id, payment_status__in=C.LIVE_STATUSES)
        .exclude(pk=order.pk).first()
    )
    if not live and newer is not None:
        order.add_event(C.EVENT_XENDIT_SUPERSEDED, new={"invoice_id": state.invoice_id, "newer_order": newer.order_number})
        _problem(order, "superseded")
        return "superseded", order.pk
    if not live and members.contacts_released(order.member):  # nobody can be reached on it
        order.add_event(C.EVENT_XENDIT_SUPERSEDED, new={"invoice_id": state.invoice_id, "contacts_released": True})
        _problem(order, "superseded")
        return "superseded", order.pk
    if other_invoice:
        _problem(order, "other_invoice")
    # Amount or currency differs.
    if state.amount != amount() or state.currency != C.CURRENCY:
        if order.payment_status != C.XENDIT_MISMATCH:
            old = order.payment_status
            order.payment_status = C.XENDIT_MISMATCH
            _save(order, ["payment_status"])
            order.add_event(
                C.EVENT_XENDIT_MISMATCH, old={"payment_status": old},
                new={"amount": str(state.amount), "currency": state.currency},
            )
        _problem(order, "amount_mismatch")
        return "amount_mismatch", order.pk
    # The correct amount, from awaiting_xendit, xendit_expired or xendit_mismatch: paid.
    order.payment_status = C.VERIFIED
    order.paid_at = state.paid_at or now
    order.verified_at = now
    order.xendit_invoice_id = state.invoice_id or order.xendit_invoice_id  # the paying invoice id
    order.benefits_active_at = order.benefits_active_at or now
    _save(order, ["payment_status", "paid_at", "verified_at", "xendit_invoice_id", "benefits_active_at"])
    order.add_event(C.EVENT_XENDIT_PAID, new={"invoice_id": state.invoice_id})
    registration.activate(order, now)  # T1: the registration starts when the payment is confirmed
    registration.on_payment_verified(order)  # T8
    notices.after_commit(notices.payment_verified_order, order.pk)
    return "applied", order.pk


def handle(site, state):
    """The hook of the webhook and reconcile: None when the reference is not a pack order's."""
    if not state.external_id.startswith(C.EXTERNAL_PREFIX):
        return None
    return apply_state(site, state)


def handle_invoiceless(site, external_id, doc):
    """Reconcile, for an awaiting order with no stored invoice two hours after placement, after one last
    lookup: an invoice found is adopted, none sets xendit_expired. True when this app owns the id."""
    if not external_id.startswith(C.EXTERNAL_PREFIX):
        return False
    with transaction.atomic():
        Site.objects.select_for_update().filter(pk=site.pk).first()
        order = PackOrder.objects.select_for_update().filter(
            site=site, xendit_external_id=external_id, payment_status=C.AWAITING_XENDIT, xendit_invoice_id=""
        ).first()
        if order is None:
            return True
        if doc:
            store_invoice(order, doc)
        else:
            order.payment_status = C.XENDIT_EXPIRED
            _save(order, ["payment_status"])
            order.add_event(C.EVENT_XENDIT_EXPIRED, new={"reason": "no_invoice"})
            _problem(order, "invoiceless_expired")
    return True


# ---- what reconcile reads ----

def awaiting_count(site):
    return PackOrder.objects.filter(site=site, payment_status=C.AWAITING_XENDIT).count()


def pending(now):
    """Awaiting orders older than 15 minutes, until READ_GRACE after expiry. Yields (site id, invoice
    id, external id, invoiceless_due); invoiceless_due is True for an order with no stored invoice
    placed 2 hours ago or more."""
    rows = (
        PackOrder.objects.filter(payment_status=C.AWAITING_XENDIT, created_at__lte=now - timedelta(minutes=15))
        .filter(Q(xendit_expires_at__isnull=True) | Q(xendit_expires_at__gte=now - READ_GRACE))
        .values_list("site_id", "xendit_invoice_id", "xendit_external_id", "created_at")
    )
    for site_id, invoice_id, external_id, created_at in rows.iterator():
        yield site_id, invoice_id, external_id, (not invoice_id and created_at <= now - INVOICELESS_EXPIRY)


def _past_window(now):
    """The read window has closed, so reconcile will never read the order again: READ_GRACE after the
    invoice's expiry; with no invoice, INVOICELESS_EXPIRY plus READ_GRACE after placement."""
    return (
        Q(xendit_expires_at__lt=now - READ_GRACE)
        | Q(xendit_expires_at__isnull=True, created_at__lt=now - (INVOICELESS_EXPIRY + READ_GRACE))
    )


def overdue(now):
    rows = (
        PackOrder.objects.filter(payment_status=C.AWAITING_XENDIT).filter(_past_window(now))
        .values_list("site_id", "xendit_external_id")
    )
    yield from rows.iterator()


def expire_overdue(site, external_id, now):
    """Expire one overdue order, key or no key. The status and the window are re-checked under the
    lock, so a payment or a stored invoice that landed first wins; a late authenticated PAID still
    applies to the expired order. True when this app owns the id."""
    if not external_id.startswith(C.EXTERNAL_PREFIX):
        return False
    with transaction.atomic():
        Site.objects.select_for_update().filter(pk=site.pk).first()
        order = (
            PackOrder.objects.select_for_update()
            .filter(site=site, xendit_external_id=external_id, payment_status=C.AWAITING_XENDIT)
            .filter(_past_window(now)).first()
        )
        if order is None:
            return True
        order.payment_status = C.XENDIT_EXPIRED
        _save(order, ["payment_status"])
        order.add_event(C.EVENT_XENDIT_EXPIRED, new={"reason": "window_passed"})
        _problem(order, "window_expired" if order.xendit_invoice_id else "invoiceless_expired")
    return True
