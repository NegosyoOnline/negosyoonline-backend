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
from apps.pack.models import PackOrder, PackProofSubmission

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
