"""Meta Pixel and Conversions API: settings, queueing, delivery and the sweep.

Every function here does nothing while META_EVENTS_ENABLED is off. A failure in this app never
breaks a page, a placed order or an approval: the hooks run in a savepoint and log only the
event name and the site id."""
import logging
import secrets
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.meta_ads import crypto, events, send
from apps.meta_ads.models import MetaDelivery, MetaOrderContext, MetaSettings

logger = logging.getLogger(__name__)

RETRY = "retry"
MAX_ATTEMPTS = 3
MAX_EVENT_AGE = timedelta(days=7)  # Meta refuses an older event
SWEEP_AFTER = timedelta(minutes=5)
RETRY_DELAY_SECONDS = 60
TEST_OUTCOMES = (
    "ok", "bad_token", "bad_pixel", "rate_limited", "meta_down", "token_unreadable",
)


def enabled():
    return settings.META_EVENTS_ENABLED


def config(site):
    return MetaSettings.objects.filter(site=site).first()


def ready_config(site):
    """The site's settings when events may be sent for it (switch on, Pixel ID and token set)."""
    if not enabled():
        return None
    row = config(site)
    return row if row is not None and row.pixel_id and row.token_encrypted else None


def pixel_id_for(site):
    """The Pixel ID the storefront draws, or "" (switch off, or none set)."""
    if not enabled():
        return ""
    row = config(site)
    return row.pixel_id if row is not None else ""


def _safely(label, site_id, fn):
    """Run a hook in a savepoint: on any failure undo only its own writes and log a short line."""
    try:
        with transaction.atomic():
            return fn()
    except Exception:  # noqa: BLE001 - Meta delivery never breaks a page, an order or an approval
        logger.error("meta %s failed for site %s", label, site_id)
        return None


# -- what the storefronts server passes in -----------------------------------------------------


def clean_url(url, host):
    """The page address without query or fragment, only when it is on the site's own host."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return ""
    own = (host or "").strip().lower().split(":")[0]
    if parts.scheme not in ("http", "https") or not own or (parts.hostname or "").lower() != own:
        return ""
    return parts._replace(query="", fragment="").geturl()[:300]


def clean_ids(fbp, fbc, user_agent):
    return {
        "fbp": fbp if events.FBP_RE.match(fbp or "") else "",
        "fbc": fbc if events.FBC_RE.match(fbc or "") else "",
        "user_agent": (user_agent or "").strip()[:400],
    }


# -- queueing -----------------------------------------------------------------------------------


def _dispatch_page(delivery_id, event):
    from apps.meta_ads import tasks

    try:
        tasks.send_page_event.delay(delivery_id, event)
    except Exception:  # noqa: BLE001 - a broker that is down loses only this page event
        logger.error("meta page event not queued, delivery %s", delivery_id)


def _dispatch_order(delivery_id):
    from apps.meta_ads import tasks

    try:
        tasks.send_order_event.delay(delivery_id)
    except Exception:  # noqa: BLE001 - the sweep queues it again
        logger.error("meta order event not queued, delivery %s", delivery_id)


def visit_for(site, visitor_ref):
    """The visit a ref names on this site, or None (a bad shape, an unknown or a foreign ref)."""
    from apps.pack.models import PackVisit
    from apps.pack.visits import REF_RE

    if not isinstance(visitor_ref, str) or not REF_RE.match(visitor_ref):
        return None
    return PackVisit.objects.filter(site=site, visitor_ref=visitor_ref).first()


def queue_page_events(site, items, *, ip, user_agent, fbp, fbc, url, visitor_ref=""):
    """Page events from the storefronts server: [(name, id)]. The caller has checked consent.
    `visitor_ref` is used only when a visit with it exists on this site. A bot or a request without an address or user agent leaves a skipped row and is not sent."""
    row = ready_config(site)
    if row is None:
        return 0
    now, queued = timezone.now(), 0
    skip = None
    if events.is_bot(user_agent):
        skip = MetaDelivery.SKIPPED_BOT
    elif not ip or not user_agent:
        skip = MetaDelivery.SKIPPED_NO_CLIENT_DATA
    visit = None if skip else visit_for(site, visitor_ref)
    for name, event_id in items:
        delivery, created = MetaDelivery.objects.get_or_create(
            site=site, event_name=name, event_id=event_id,
            defaults={"is_test": bool(row.test_event_code), "created_at": now},
        )
        if not created:
            continue
        if skip:
            MetaDelivery.objects.filter(pk=delivery.pk).update(outcome=skip, finished_at=now)
            continue
        event = events.page_event(
            name, event_id, now=now, url=url, ip=ip, user_agent=user_agent, fbp=fbp, fbc=fbc,
            email=visit.contact_email if visit else "", mobile=visit.contact_mobile if visit else "",
        )
        transaction.on_commit(lambda pk=delivery.pk, ev=event: _dispatch_page(pk, ev), robust=True)
        queued += 1
    return queued


def capture_order_context(order, meta, ip):
    """Keep the visitor's browser identifiers for the order. Only with consent, on a site with a
    Pixel and a token. `meta` is the place call's extras: consent, host, url, fbp, fbc, user_agent."""
    if not meta or meta.get("consent") is not True or ready_config(order.site) is None:
        return None
    ids = clean_ids(meta.get("fbp"), meta.get("fbc"), meta.get("user_agent"))
    return MetaOrderContext.objects.get_or_create(
        order=order,
        defaults={
            "site": order.site, "client_ip": ip or None, "source_url": clean_url(meta.get("url"), meta.get("host")),
            **ids,
        },
    )[0]


def _queue_order_event(order, name, prefix):
    """(delivery, created) for an order event, or None when nothing may be sent."""
    row = ready_config(order.site)
    if row is None or not MetaOrderContext.objects.filter(order=order).exists():
        return None  # no Pixel or token, or no consent was given: nothing is sent
    delivery, created = MetaDelivery.objects.get_or_create(
        site=order.site, event_name=name, event_id=f"{prefix}_{order.public_id}",
        defaults={"is_test": bool(row.test_event_code)},
    )
    if created:
        transaction.on_commit(lambda: _dispatch_order(delivery.pk), robust=True)
    return delivery, created


def after_placement(order, meta, ip):
    """Called inside the placing transaction: save the context, queue AddPaymentInfo."""
    def run():
        if capture_order_context(order, meta, ip) is not None:
            _queue_order_event(order, "AddPaymentInfo", events.ORDER_EVENTS["AddPaymentInfo"])

    if enabled():
        _safely("placement", order.site_id, run)


def queue_onboarding_purchase(order, *, consent):
    """The buyer opened the onboarding verify page for a placed order. Returns the Purchase
    delivery and whether this was its first time, or None when nothing may be sent: the switch is
    off, there is no Pixel or token, the visitor has not consented on this visit, or the order has
    no saved context (no consent at placement). The delivery's unique event id (pur_<public id>)
    makes it once per order; only the first call registers a send, after commit."""
    if not enabled() or consent is not True:
        return None
    return _safely("purchase", order.site_id, lambda: _queue_order_event(order, "Purchase", events.ORDER_EVENTS["Purchase"]))


# -- delivery -----------------------------------------------------------------------------------


def _record(delivery, result):
    """What Meta's answer leaves on the row: status, numeric error code, events_received, trace id."""
    delivery.http_status, delivery.meta_error_code = result.http_status, result.error_code
    delivery.events_received, delivery.fbtrace_id = result.events_received, result.fbtrace_id


def _finish(delivery, outcome, result=None):
    delivery.outcome = outcome
    delivery.finished_at = timezone.now()
    if result is not None:
        _record(delivery, result)
    delivery.save(update_fields=[
        "outcome", "finished_at", "http_status", "meta_error_code", "events_received", "fbtrace_id", "attempts",
    ])
    return outcome


def _order_event(delivery):
    """The event of an order delivery rebuilt from rows, or None."""
    from apps.pack.models import PackOrder

    prefix = events.ORDER_EVENTS.get(delivery.event_name)
    public_id = delivery.event_id[len(prefix) + 1:] if prefix else ""
    order = (
        PackOrder.objects.select_related("member").filter(site_id=delivery.site_id, public_id=public_id).first()
    )
    context = MetaOrderContext.objects.filter(order=order).first() if order else None
    if order is None or context is None:
        return None
    return events.order_event(delivery, order, context)


def deliver(delivery_id, event=None):
    """Send one queued delivery. `event` is the page event the task carried; order events are
    rebuilt from rows. Returns the outcome, RETRY when it should be tried again, or None when
    there was nothing to do. The row stays locked during the call (5 seconds at most), so two
    workers never send the same event."""
    if not enabled():
        return None
    with transaction.atomic():
        delivery = MetaDelivery.objects.select_for_update().filter(pk=delivery_id).first()
        if delivery is None or delivery.outcome not in MetaDelivery.OPEN:
            return None
        now = timezone.now()
        if event is None:
            event = _order_event(delivery)
            if event is None:
                return _finish(delivery, MetaDelivery.SKIPPED_NO_CLIENT_DATA)
        if now - datetime.fromtimestamp(event["event_time"], tz=dt_timezone.utc) > MAX_EVENT_AGE:
            return _finish(delivery, MetaDelivery.EXPIRED)
        row = config(delivery.site)
        if row is None or not row.pixel_id or not row.token_encrypted:
            return _finish(delivery, MetaDelivery.FAILED)
        try:
            token = crypto.decrypt(row.token_encrypted)
        except crypto.TokenKeyError:
            return _finish(delivery, MetaDelivery.FAILED)
        delivery.attempts += 1
        result = send.post_event(row.pixel_id, token, event, row.test_event_code)
        del token
        if result.outcome == send.OK:
            outcome = _finish(delivery, MetaDelivery.SENT, result)
            if delivery.event_name == "Purchase":  # the saved identifiers have done their job
                MetaOrderContext.objects.filter(site_id=delivery.site_id, order__public_id=delivery.event_id[4:]).delete()
            return outcome
        if result.outcome in send.RETRYABLE and delivery.attempts < MAX_ATTEMPTS:
            delivery.outcome = MetaDelivery.RETRYING
            _record(delivery, result)
            delivery.save(update_fields=[
                "outcome", "attempts", "http_status", "meta_error_code", "events_received", "fbtrace_id",
            ])
            return RETRY
        return _finish(delivery, MetaDelivery.FAILED, result)


def sweep(now=None):
    """Re-queue Purchase and AddPaymentInfo deliveries still waiting after five minutes, and
    expire any delivery still open after Meta's seven days. Returns the counts."""
    if not enabled():
        return {}
    now = now or timezone.now()
    open_rows = MetaDelivery.objects.filter(outcome__in=MetaDelivery.OPEN)
    expired = open_rows.filter(created_at__lte=now - MAX_EVENT_AGE).update(
        outcome=MetaDelivery.EXPIRED, finished_at=now,
    )
    due = open_rows.filter(
        event_name__in=list(events.ORDER_EVENTS), created_at__lte=now - SWEEP_AFTER,
        created_at__gt=now - MAX_EVENT_AGE,
    )
    ids = list(due.values_list("pk", flat=True))
    for pk in ids:
        _dispatch_order(pk)
    return {"requeued": len(ids), "expired": expired}


# -- the Subscriber's connection test -----------------------------------------------------------


def run_test(row, *, ip, user_agent):
    """Send one flagged PageView with the saved test event code, now, and record the outcome.
    Returns an outcome from TEST_OUTCOMES; never Meta's own message."""
    now = timezone.now()
    delivery = MetaDelivery.objects.create(
        site=row.site, event_name="PageView", event_id=f"tc_{secrets.token_hex(12)}", is_test=True,
        attempts=1,
    )
    try:
        token = crypto.decrypt(row.token_encrypted)
    except crypto.TokenKeyError:
        outcome, result = "token_unreadable", None
    else:
        event = events.page_event(
            "PageView", delivery.event_id, now=now, url="", ip=ip, user_agent=user_agent, fbp="", fbc="",
        )
        result = send.post_event(row.pixel_id, token, event, row.test_event_code)
        del token
        outcome = {
            send.OK: "ok", send.BAD_TOKEN: "bad_token", send.RATE_LIMITED: "rate_limited",
            send.META_DOWN: "meta_down",
        }.get(result.outcome, "bad_pixel")
    delivery.outcome = MetaDelivery.SENT if outcome == "ok" else MetaDelivery.FAILED
    delivery.finished_at = timezone.now()
    if result is not None:
        _record(delivery, result)
    delivery.save()
    MetaSettings.objects.filter(pk=row.pk).update(last_test_at=now, last_test_outcome=outcome)
    return outcome
