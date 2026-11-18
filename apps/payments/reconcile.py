"""Server-to-server Xendit reconcile (spec v1.12 7.2). Every 15 minutes: for each order that still
waits for payment, read the invoice with that site's own key and apply the same rules as the
webhook, under the order row lock. A last sweep expires every order past its read window, key or
no key. Adapted from TWCinc negosyoonline_orders/reconcile.py. Gated on XENDIT_ENABLED only (the
worker has no PAYMENT_METHODS_ENABLED) and it reads each site's credential directly. Task
arguments carry nothing; logs carry site ids only."""
import hashlib
import logging

from django.conf import settings
from django.utils import timezone

from apps.payments import services as payments, webhooks, xendit_client
from apps.payments.models import XenditEvent
from apps.sites.models import Site

logger = logging.getLogger(__name__)
FINAL = ("PAID", "SETTLED", "EXPIRED")


def _pack():
    from apps.pack import xendit as pack_xendit
    return pack_xendit


def run(now=None):
    if not settings.XENDIT_ENABLED:
        return 0
    pack, now, applied = _pack(), now or timezone.now(), 0
    for site_pk, invoice_id, external_id, invoiceless_due in list(pack.pending(now)):
        site = Site.objects.filter(pk=site_pk, deleted_at__isnull=True).first()
        if site is None:
            continue
        key = payments.api_key_for(site)
        if not key:
            continue  # nothing can be read without the site's key; the window sweep expires what stays unread
        try:
            doc = xendit_client.get_invoice(key, invoice_id) if invoice_id else xendit_client.find_invoice(key, external_id)
        except xendit_client.XenditError as exc:
            logger.warning("xendit reconcile read failed site=%s kind=%s", site_pk, exc.kind)
            if exc.kind == "unauthorized":
                try:
                    payments.mark_key_failed(site)
                    if invoiceless_due and not invoice_id:
                        pack.handle_invoiceless(site, external_id, None)  # bounded: expires after 2 hours
                except Exception:  # noqa: BLE001
                    logger.exception("xendit reconcile key-failed handling failed site=%s", site_pk)
            continue  # an unavailable read does not expire the order; the window sweep ends it in time
        except Exception:  # noqa: BLE001 - a 2xx body of the wrong shape skips this order, never the run
            logger.exception("xendit reconcile read failed site=%s", site_pk)
            continue
        if invoiceless_due and not invoice_id:
            try:
                pack.handle_invoiceless(site, external_id, doc)
            except Exception:  # noqa: BLE001 - logged, retried next run
                logger.exception("xendit invoice-less expiry failed site=%s", site_pk)
                continue
        if not doc:
            continue
        applied += apply_invoice(site, external_id, doc) == "applied"
    expire_overdue(now)
    return applied


def expire_overdue(now):
    """An order past its read window is never read again, so it is expired locally here, with no key
    needed. A late authenticated PAID still applies. One failure is logged and retried next run."""
    pack = _pack()
    for site_pk, external_id in list(pack.overdue(now)):
        site = Site.objects.filter(pk=site_pk).first()
        if site is None:
            continue
        try:
            pack.expire_overdue(site, external_id, now)
        except Exception:  # noqa: BLE001
            logger.exception("xendit reconcile window expiry failed site=%s", site_pk)


def apply_invoice(site, external_id, doc):
    """Apply one invoice document read from Xendit with the webhook's rules; the outcome string, or
    None when the invoice is not final, is already recorded, or the apply failed (logged, retried by
    the next read)."""
    try:
        state = xendit_client.parse_invoice(doc)
    except (AttributeError, TypeError, ValueError):
        logger.warning("xendit reconcile unreadable invoice site=%s", site.pk)
        return None
    if state.external_id != external_id or state.status not in FINAL:
        return None
    event_id = f"{state.invoice_id}:{state.status}"[:128]
    if XenditEvent.objects.filter(site=site, provider_event_id=event_id).exists():
        return None
    try:
        outcome, _ = webhooks.record_event(
            site, state, event_id=event_id, event_type="invoice.reconcile", source="reconcile",
            body_sha256=hashlib.sha256(repr(sorted(doc.items())).encode()).hexdigest(),
        )
    except webhooks.DuplicateEvent:
        return None
    except Exception:  # noqa: BLE001 - logged, retried next run
        logger.exception("xendit reconcile apply failed site=%s", site.pk)
        return None
    return outcome


def verify_invoice(site, invoice_id, external_id):
    """Read ONE invoice from Xendit with the site's own key and apply it (the buyer's return page
    asks for this, so a payment shows without waiting for the webhook or the 15-minute run). Nothing
    the browser sends reaches here. Never raises: no key, an unreachable Xendit or a bad key just
    leaves the order for the webhook and the next run."""
    if not settings.XENDIT_ENABLED:
        return None
    key = payments.api_key_for(site)
    if not key or not invoice_id:
        return None
    try:
        doc = xendit_client.get_invoice(key, invoice_id)
    except xendit_client.XenditError:
        return None
    if not doc or not isinstance(doc, dict):
        return None
    return apply_invoice(site, external_id, doc)
