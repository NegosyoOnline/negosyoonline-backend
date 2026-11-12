"""The Meta events route of the storefront surface (registered by one line in
apps/storefront/api.py):

    POST /storefront/v1/meta/events/  {host, events: [{name, id}], url, fbp, fbc, ip, user_agent,
        consent} -> {status}: "queued" or "ignored"

Only PageView, ViewContent and InitiateCheckout are accepted here; AddPaymentInfo and Purchase
are built inside Django from an order and are refused by name. No value, currency or custom
data is read from the caller. Nothing is stored or sent for a visitor without `consent: true`.
While META_EVENTS_ENABLED is off, and for an unknown, foreign or non-Santé host, the route
answers the 404 of an unknown site.
"""
import ipaddress

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
        url=services.clean_url(body.url, body.host),
    )
    return {"status": "queued" if queued else "ignored"}
