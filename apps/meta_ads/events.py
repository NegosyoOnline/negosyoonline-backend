"""The event payloads. Page events are built from what the storefronts server passes (never an
amount: the value and currency come from the pack constants). Purchase and AddPaymentInfo are
built only here, from the order row, the member row and the saved browser identifiers.

user_data (hashed with SHA-256, only with the visitor's consent, a blank value left out): em and ph
from the member on order events and from the visit's saved checkout contact on page events."""
import hashlib
import re

from apps.pack import constants as C

CONTENT_ID = "membership-pack"

# PageView is sent by the browser Pixel and the server with one shared pv_ id, deduplicated by Meta
# (Coach Ed, 2026-11-17, ClickUp z8uq9mfzx3; supersedes the browser-only ruling of 2026-11-15).
PAGE_EVENTS = {"PageView": "pv", "ViewContent": "vc", "InitiateCheckout": "ic"}
ORDER_EVENTS = {"AddPaymentInfo": "api", "Purchase": "pur"}
ID_RE = re.compile(r"^(pv|vc|ic)_[0-9a-f]{16,32}$")
FBP_RE = re.compile(r"^fb\.[0-9]\.[0-9]{10,14}\.[A-Za-z0-9_.\-]{1,150}$")
FBC_RE = re.compile(r"^fb\.[0-9]\.[0-9]{10,14}\.[A-Za-z0-9_\-]{1,200}$")

BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|facebookexternalhit|facebookcatalog|meta-externalagent|whatsapp|"
    r"telegram|preview|headless|lighthouse|pingdom|uptime|monitor|curl/|wget/|python-requests|"
    r"httpclient|go-http-client",
    re.IGNORECASE,
)


def is_bot(user_agent):
    return bool(BOT_RE.search(user_agent or ""))


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def hashed_email(email):
    value = (email or "").strip().lower()
    return sha(value) if value else ""


def hashed_mobile(mobile):
    """Digits with the country code: +639171234567 becomes 639171234567."""
    digits = re.sub(r"\D", "", mobile or "")
    return sha(digits) if digits else ""


def user_data(*, ip="", user_agent="", fbp="", fbc="", email="", mobile=""):
    data = {}
    if ip:
        data["client_ip_address"] = ip
    if user_agent:
        data["client_user_agent"] = user_agent
    if fbp:
        data["fbp"] = fbp
    if fbc:
        data["fbc"] = fbc
    if email and hashed_email(email):
        data["em"] = [hashed_email(email)]
    if mobile and hashed_mobile(mobile):
        data["ph"] = [hashed_mobile(mobile)]
    if email or mobile:
        data["country"] = [sha("ph")]
    return data


def pack_data():
    return {
        "content_type": "product", "content_ids": [CONTENT_ID],
        "value": C.PRICE_CENTAVOS / 100, "currency": C.CURRENCY,
    }


def build(name, event_id, *, event_time, url, user, custom=None):
    event = {
        "event_name": name, "event_time": int(event_time), "event_id": event_id,
        "action_source": "website", "user_data": user,
    }
    if url:
        event["event_source_url"] = url
    if custom:
        event["custom_data"] = custom
    return event


def page_event(name, event_id, *, now, url, ip, user_agent, fbp, fbc, email="", mobile=""):
    custom = None if name == "PageView" else pack_data()
    return build(
        name, event_id, event_time=now.timestamp(), url=url, custom=custom,
        user=user_data(ip=ip, user_agent=user_agent, fbp=fbp, fbc=fbc, email=email, mobile=mobile),
    )


def order_event(delivery, order, context):
    """The AddPaymentInfo or Purchase of an order, or None when there is nothing to send it with
    (no saved browser identifiers or no client address). Nothing is read from a request."""
    member = order.member
    user = user_data(
        ip=context.client_ip or "", user_agent=context.user_agent, fbp=context.fbp, fbc=context.fbc,
        email=member.email, mobile=member.mobile,
    )
    if "client_ip_address" not in user or "client_user_agent" not in user:
        return None
    custom = {**pack_data(), "order_id": order.order_number}
    if delivery.event_name == "Purchase":
        custom["value"], custom["currency"] = order.price_centavos / 100, order.currency
        when = delivery.created_at  # the buyer's first onboarding visit, when Purchase was queued
    else:
        when = order.created_at
    if when is None:
        return None
    return build(
        delivery.event_name, delivery.event_id, event_time=when.timestamp(),
        url=context.source_url, user=user, custom=custom,
    )
