"""The two checkout emails (port of TWCinc's notices.py, order_received and review_needed). The
wording is TWCinc's. Each runs after commit and never fails or undoes the committed order; a
failed email is left for the later sweep slice to retry. The buyer's SMS twin belongs to the SMS
slices (PK2b/PK2c), not here.

The Subscriber's "review needed" email goes only to an owner whose email is verified."""
import logging

from django.conf import settings
from django.db import transaction

from apps.members import messaging
from apps.notifications.services import send_email
from apps.pack import access
from apps.pack.models import PackOrder, PackProofDecision, PackProofSubmission

logger = logging.getLogger(__name__)

_HI = "Hi {first},\n\n"
_SIGN = "\n\n{name}"
RECEIVED = (
    "We received your payment proof for order {no}",
    _HI + "Thank you for your order. We received your payment proof for your Membership Pack, order {no} (₱1,488.00).\n\n"
    "The store will review your payment. Once it is verified, your Lifetime Membership turns on: 40% member savings on "
    "eligible Santé products, with no renewal fees, no monthly quotas and no maintenance purchases. Your pack ships "
    "after the payment is verified. You can follow your order in your dashboard: {url}\n\nPlease also add your Santé "
    "membership details in your dashboard so the store can register your membership." + _SIGN,
)
REJECTED = (
    "Your payment for order {no} needs attention",
    _HI + "The store could not verify the payment proof for order {no}. Sign in to your dashboard to see the reason and "
    "send your payment details and proof again: {url}" + _SIGN,
)
VERIFIED = (
    "Your payment for order {no} is verified",
    _HI + "The store verified your payment for order {no}. We will let you know when your pack ships." + _SIGN,
)
SHIPPED = (
    "Your order {no} has shipped",
    _HI + "Your Membership Pack is on its way.\nCourier: {courier}\nTracking number: {tracking}\n"
    "Shipped on: {shipped_on}" + _SIGN,
)
DELIVERED = (
    "Your order {no} was delivered",
    _HI + "The store marked your order {no} as delivered on {delivered_on}. If you have not received it, "
    "reply to this email." + _SIGN,
)
ACCESS_EMAIL = (
    "Your secure link to continue your order",
    _HI + "Use this secure link to continue your order: {url}\n\nIt works once and expires in 7 days." + _SIGN,
)
ACCESS_LINK_NOTE = "\n(This secure link works once and expires in 7 days.)"
REVIEW = (
    "New payment proof to review: {no} ({store})",
    "{who} submitted a payment proof for Membership Pack order {no} on {store}. Review it in your NegosyoOnline "
    "Dashboard under Orders.",
)


def after_commit(fn, *args, **kwargs):
    """Runs after commit, and never fails or undoes the committed change."""
    def run():
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a notice never breaks a money or status change
            logger.exception("pack notice failed: %s", getattr(fn, "__name__", "notice"))

    transaction.on_commit(run, robust=True)


def _first_name(member):
    parts = (member.full_name or "").split()
    return parts[0] if parts else ""


def _who(member):
    """Subscriber emails name the member by first name and last initial ("Maria S.")."""
    parts = (member.full_name or "").split()
    return f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else (parts[0] if parts else "A member")


def dashboard_url(site):
    subdomain = site.subdomains.filter(state="active").order_by("pk").first()
    if subdomain is None or not settings.SITE_PARENT_HOST:
        return "the store's website"
    return f"https://{subdomain.name}.{settings.SITE_PARENT_HOST}/dashboard/"


def host_for(site):
    """The host a buyer reaches this store on, or "" when it has none."""
    subdomain = site.subdomains.filter(state="active").order_by("pk").first()
    if subdomain is None or not settings.SITE_PARENT_HOST:
        return ""
    return f"{subdomain.name}.{settings.SITE_PARENT_HOST}"


def access_link(member, *, limited=True):
    """A new link, to the checkout email only, never to an address the request supplies. The buyer
    asked for it (limited), or a Subscriber's recovery sends it (limited=False)."""
    access.renew(member, limited=limited)
    site, host = member.site, host_for(member.site)
    if not host:
        return None
    link = access.link_for(member, host)
    subject, body = ACCESS_EMAIL
    values = {"name": messaging.store_name(site), "first": _first_name(member), "url": link}
    from_name, reply_to = messaging.sender(site)
    return send_email(
        to=member.email, category="pack_access_link", subject=subject.format(**values),
        body=body.format(**values), sender_name=from_name, reply_to=reply_to,
    )


def order_received(order_pk):
    order = PackOrder.objects.select_related("site__account", "member").get(pk=order_pk)
    site, member = order.site, order.member
    if not member.email:
        return None
    subject, body = RECEIVED
    values = {
        "name": messaging.store_name(site), "first": _first_name(member), "no": order.order_number,
        "url": dashboard_url(site),
    }
    if access.applies(member):  # an unconfirmed mobile cannot sign in: the email carries the way back
        host = host_for(site)
        if host:
            values["url"] = access.link_for(member, host) + ACCESS_LINK_NOTE
    from_name, reply_to = messaging.sender(site)
    return send_email(
        to=member.email, category="pack_order_received", subject=subject.format(**values),
        body=body.format(**values), sender_name=from_name, reply_to=reply_to,
    )


def review_needed(submission_pk):
    sub = PackProofSubmission.objects.select_related("order__site__account", "order__member").get(
        pk=submission_pk
    )
    site, owner = sub.order.site, sub.order.site.account
    if not (owner.email and owner.email_verified_at):
        return None
    subject, body = REVIEW
    values = {
        "no": sub.order.order_number, "store": messaging.store_name(site),
        "who": _who(sub.order.member),
    }
    return send_email(
        to=owner.email, category="pack_review_needed", subject=subject.format(**values),
        body=body.format(**values),
    )


def _decision_email(decision_pk, texts, category):
    """The buyer's email for a decision, to the address typed at checkout, verified or not. The
    rejection names no reason: it shows after sign-in."""
    decision = PackProofDecision.objects.select_related("order__site", "order__member").get(pk=decision_pk)
    order, member = decision.order, decision.order.member
    if not member.email:
        return None
    subject, body = texts
    site = order.site
    values = {
        "name": messaging.store_name(site), "first": _first_name(member), "no": order.order_number,
        "url": dashboard_url(site),
    }
    from_name, reply_to = messaging.sender(site)
    return send_email(
        to=member.email, category=category, subject=subject.format(**values), body=body.format(**values),
        sender_name=from_name, reply_to=reply_to,
    )


def proof_rejected(decision_pk):
    return _decision_email(decision_pk, REJECTED, "pack_proof_rejected")


def payment_verified(decision_pk):
    return _decision_email(decision_pk, VERIFIED, "pack_verified")


def _shipment_email(order_pk, texts, category, extra):
    """The buyer's shipment email, to the address typed at checkout, only while PACK_SHIPPING_ENABLED
    is on. Sent once, after commit; a correction sends nothing."""
    if not settings.PACK_SHIPPING_ENABLED:
        return None
    order = PackOrder.objects.select_related("site", "member").get(pk=order_pk)
    member = order.member
    if not member.email:
        return None
    subject, body = texts
    values = {
        "name": messaging.store_name(order.site), "first": _first_name(member), "no": order.order_number,
        **extra(order),
    }
    from_name, reply_to = messaging.sender(order.site)
    return send_email(
        to=member.email, category=category, subject=subject.format(**values), body=body.format(**values),
        sender_name=from_name, reply_to=reply_to,
    )


def order_shipped(order_pk):
    return _shipment_email(order_pk, SHIPPED, "pack_shipped", lambda o: {
        "courier": o.courier_name, "tracking": o.tracking_number, "shipped_on": o.shipped_on.isoformat(),
    })


def order_delivered(order_pk):
    return _shipment_email(order_pk, DELIVERED, "pack_delivered", lambda o: {
        "delivered_on": o.delivered_on.isoformat(),
    })
