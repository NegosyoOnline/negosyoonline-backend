"""The Meta events route of the storefront surface (registered by one line in
apps/storefront/api.py):

    POST /storefront/v1/meta/events/  {host, events: [{name, id}], url, fbp, fbc, ip, user_agent,
        visitor_ref?, consent} -> {status}: "queued" or "ignored"
    POST /storefront/v1/meta/purchase/  {host, visitor_ip, token, consent} -> {status, event_id,
        first, value, currency, content_ids}: the buyer opened the onboarding verify page. Django
        resolves the order from the checkout token (site-scoped) and queues Purchase once per
        order; `first` is true only on the call that created it, so the browser fires only then.

Only PageView, ViewContent and InitiateCheckout are accepted here; AddPaymentInfo and Purchase
are built inside Django from an order and are refused by name. No value, currency or custom
data is read from the caller. Nothing is stored or sent for a visitor without `consent: true`.

user_data (SHA-256 hashed except the address, user agent, fbp and fbc, and only with consent):
external_id, and em and ph from the visit's saved checkout contact, from the visit the optional
`visitor_ref` names, when that visit exists on this site (a bad, unknown or foreign ref is ignored,
never an error). AddPaymentInfo and Purchase carry external_id from the visit linked to the order
(none when there is none) and em and ph from the member.
While META_EVENTS_ENABLED is off, and for an unknown, foreign or non-Santé host, the route
answers the 404 of an unknown site.
"""
import ipaddress
from typing import Any

from django.conf import settings
from ninja import Router, Schema

from apps.common import throttle
from apps.common.errors import ApiError
from apps.meta_ads import events, services
from apps.storefront.services import served_sante_site

router = Router()

IP_LIMIT = ("meta-events-ip", 120, 600)
SITE_LIMIT = ("meta-events-site", 3000, 600)
MAX_EVENTS = 5


class EventIn(Schema):
    name: str = ""
    id: str = ""


class EventsIn(Schema):
    host: str = ""
    events: list[EventIn] = []
    url: str = ""
    fbp: str = ""
    fbc: str = ""
    ip: str = ""
    user_agent: str = ""
    visitor_ref: Any = ""  # anything but a valid ref is ignored, never an error
    consent: bool = False


class EventsOut(Schema):
    status: str


def _ip(value):
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return ""


@router.post("/meta/events/", response=EventsOut)
def meta_events(request, body: EventsIn):
    site = served_sante_site(body.host) if settings.META_EVENTS_ENABLED else None
    if site is None:
        raise ApiError("not_found", "Not found.", status=404)
    if len(body.events) > MAX_EVENTS or any(e.name not in events.PAGE_EVENTS for e in body.events):
        raise ApiError("event_refused", "That event cannot be sent from here.", status=422)
    ip = _ip(body.ip)
    throttle.hit(IP_LIMIT[0], ip or "unknown", limit=IP_LIMIT[1], window=IP_LIMIT[2])
    throttle.hit(SITE_LIMIT[0], site.pk, limit=SITE_LIMIT[1], window=SITE_LIMIT[2])
    items = [
        (e.name, e.id) for e in body.events
        if events.ID_RE.match(e.id) and e.id.startswith(events.PAGE_EVENTS[e.name] + "_")
    ]
    if not body.consent or not items:
        return {"status": "ignored"}
    ids = services.clean_ids(body.fbp, body.fbc, body.user_agent)
    queued = services.queue_page_events(
        site, items, ip=ip, user_agent=ids["user_agent"], fbp=ids["fbp"], fbc=ids["fbc"],
        url=services.clean_url(body.url, body.host), visitor_ref=body.visitor_ref,
    )
    return {"status": "queued" if queued else "ignored"}


class PurchaseIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    token: str = ""
    consent: bool = False


class PurchaseOut(Schema):
    status: str
    event_id: str = ""
    first: bool = False
    value: float = 0
    currency: str = ""
    content_ids: list[str] = []


PURCHASE_IP_LIMIT = ("meta-purchase-ip", 60, 600)


@router.post("/meta/purchase/", response=PurchaseOut)
def meta_purchase(request, body: PurchaseIn):
    """The buyer opened /funnel/membership-pack/onboarding/verify/. The order comes from the
    checkout token and the site only, never from a field the browser chooses."""
    from apps.pack.api import _funnel  # the same token and site resolution as the onboarding state call

    if not settings.META_EVENTS_ENABLED:
        raise ApiError("not_found", "Not found.", status=404)
    throttle.hit(
        PURCHASE_IP_LIMIT[0], _ip(body.visitor_ip) or "unknown", limit=PURCHASE_IP_LIMIT[1], window=PURCHASE_IP_LIMIT[2],
    )
    _site, _token, order, _member = _funnel(body.host, body.token)
    queued = services.queue_onboarding_purchase(order, consent=body.consent)
    if queued is None:
        return {"status": "ignored"}
    delivery, created = queued
    return {
        "status": "queued" if created else "ignored", "event_id": delivery.event_id, "first": created,
        "value": order.price_centavos / 100, "currency": order.currency, "content_ids": [events.CONTENT_ID],
    }
