"""The shop's emails (port of TWCinc's negosyoonline_storefront/notices.py wording, email only: no
buyer SMS). Plain text through notifications.send_email, after commit, never failing or undoing
the committed order; a failure is logged. The buyer's email is the one typed at checkout, or the
member's own; none means no buyer email (the done page shows the status). The Subscriber's
emails go only to an owner whose email is verified. A cancellation email never carries the
Subscriber's note."""
import logging

from django.db import transaction

from apps.members import messaging
from apps.notifications.services import send_email
from apps.shop.models import ShopOrder

logger = logging.getLogger(__name__)

_HI = "Hi {first},\n\n"
_SIGN = "\n\n{name}"
PLACED = (
    "Your {name} order {no}",
    _HI + "Thank you for your order. We received your payment proof for order {no} (total {total}).\n\n"
    "The store will review your payment. We will email you when it is confirmed and when your order "
    "ships." + _SIGN,
)
VERIFIED = (
    "Payment confirmed: {no}",
    _HI + "The store confirmed your payment for order {no}. We will let you know when it ships." + _SIGN,
)
SHIPPED = (
    "Your order {no} has shipped",
    _HI + "Your order {no} is on its way.{courier}" + _SIGN,
)
DELIVERED = (
    "Your order {no} was delivered",
    _HI + "Your order {no} was marked delivered. Thank you for shopping with us." + _SIGN,
)
CANCELLED = (
    "Order {no} was cancelled",
    _HI + "Order {no} was cancelled by the store. Contact the store if you have questions." + _SIGN,
)
NEW_ORDER = (
    "New order {no} for {store}",
    "{who} placed order {no} on {store} (total {total}). Review it in your NegosyoOnline Dashboard "
    "under Orders.",
)
REVIEW = (
    "Payment proof to review: {no}",
    "{who} sent a payment proof for order {no} on {store}. Review it in your NegosyoOnline Dashboard "
    "under Orders.",
)


def after_commit(fn, *args, **kwargs):
    """Runs after commit, and never fails or undoes the committed change."""
    def run():
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a notice never breaks a money or status change
            logger.exception("shop notice failed: %s", getattr(fn, "__name__", "notice"))

    transaction.on_commit(run, robust=True)


def money(centavos):
    return f"₱{centavos / 100:,.2f}"


def _first(name):
    parts = (name or "").split()
    return parts[0] if parts else ""


def _who(order):
    """Subscriber emails name the buyer by first name and last initial ("Maria S.")."""
    parts = (order.receiver_name or "").split()
    return f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else (parts[0] if parts else "A buyer")


def _buyer_email(order):
    return order.contact_email or (order.member.email if order.member_id else "")


def _buyer(order_pk, texts, category, **extra):
    order = ShopOrder.objects.select_related("site__account", "member").get(pk=order_pk)
    to = _buyer_email(order)
    if not to:
        return None
    site = order.site
    subject, body = texts
    values = {
        "name": messaging.store_name(site), "first": _first(order.receiver_name),
        "no": order.order_number, "total": money(order.total_centavos), **extra,
    }
    from_name, reply_to = messaging.sender(site)
    return send_email(
        to=to, category=category, subject=subject.format(**values), body=body.format(**values),
        sender_name=from_name, reply_to=reply_to,
    )


def order_placed(order_pk):
    return _buyer(order_pk, PLACED, "shop_order_placed")


def payment_verified(order_pk):
    return _buyer(order_pk, VERIFIED, "shop_verified")


def order_shipped(order_pk):
    order = ShopOrder.objects.get(pk=order_pk)
    detail = ""
    if order.courier:
        detail = f" Courier: {order.courier}."
        if order.tracking_no:
            detail += f" Tracking number: {order.tracking_no}."
    return _buyer(order_pk, SHIPPED, "shop_shipped", courier=detail)


def order_delivered(order_pk):
    return _buyer(order_pk, DELIVERED, "shop_delivered")


def order_cancelled(order_pk):
    return _buyer(order_pk, CANCELLED, "shop_cancelled")


def _owner(order_pk, texts, category):
    order = ShopOrder.objects.select_related("site__account").get(pk=order_pk)
    owner = order.site.account
    if not (owner.email and owner.email_verified_at):
        return None
    subject, body = texts
    values = {
        "no": order.order_number, "store": messaging.store_name(order.site), "who": _who(order),
        "total": money(order.total_centavos),
    }
    return send_email(
        to=owner.email, category=category, subject=subject.format(**values), body=body.format(**values)
    )


def new_order(order_pk):
    return _owner(order_pk, NEW_ORDER, "shop_new_order")


def review_needed(order_pk):
    return _owner(order_pk, REVIEW, "shop_review_needed")
