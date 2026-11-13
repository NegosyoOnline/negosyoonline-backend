"""The buyer portal: what a paid Membership Pack buyer can still see of their own order after the
Santé site they bought from has stopped being served (ClickUp z8uq9menvu; plan
2026-11-13-buyer-portal-z8uq9mbk0m, sections 3 to 7).

The portal is a separate set of routes (apps/pack/api_portal.py). `served()`, the member routes and
the pack routes are untouched, so an unserved site still answers them the neutral 404. This module
decides which site opens the portal, when it closes, and what a card may hold.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from apps.billing import services as billing
from apps.common.errors import ApiError
from apps.members import sessions
from apps.pack import constants as C, panels
from apps.pack.models import PackOrder, PackProofSubmission, SanteRegistration
from apps.sites import templates
from apps.sites.api_public import site_for_host

logger = logging.getLogger(__name__)

# L45: no Membership Pack is sold unless the buyer portal exists. A code constant, not a switch:
# turning it off after sales would lock a paid buyer out, and a default-off switch would close the
# sale on staging until someone set it. It is True in the change that adds the portal routes.
BUYER_PORTAL_READY = True

NOTICE_DAYS = 14
NEUTRAL_BANNER = "This store is not taking orders."
# Block C (Coach Ed, 2026-11-13): the deletion sentence of the day-76 email, added to the banner
# only while PACK_PURGE_ENABLED is on.
DELETION_LINE = (
    "On or shortly after that time, the personal registration and delivery details held for your "
    "order (such as your date of birth, usernames and delivery address) are deleted or anonymised."
)


def not_found():
    return ApiError("not_found", "Not found.", status=404, event="site_not_found", reason="missing")


def require_sale_open():
    """The sale gate: refuses with the unserved-site 404 while the portal does not exist."""
    if not BUYER_PORTAL_READY:
        raise not_found()


@dataclass(frozen=True)
class Open:
    site: object
    now: datetime
    service_end: datetime | None
    closes_at: datetime | None  # None: no clock has started, the portal has no end yet


def portal_site(host):
    """The `Open` portal of the site this host names, or None (the caller answers the neutral
    404). Every doubt, including a read error, is None; it never says "deleted"."""
    if not (settings.MEMBERS_ENABLED and settings.PACK_ENABLED):
        return None
    site = site_for_host(host)
    if site is None or site.template != templates.SANTE:
        return None
    position = billing.position(site)  # a read error is logged there and answers not served
    if not position.billing_only:
        return None
    now = position.at or timezone.now()
    try:
        point = billing.deletion_point(site, now)
        ended = billing.end_dates(site, now)
    except Exception:
        logger.exception("Buyer portal could not read the deletion point of site %s", site.pk)
        return None
    if point is not None and now >= point:
        return None
    return Open(site, now, ended[0] if ended else None, point)


def open_site(host):
    found = portal_site(host)
    if found is None:
        raise not_found()
    return found


def _manila(stamp):
    return timezone.localtime(stamp, ZoneInfo(settings.TIME_ZONE))


def _day(stamp):
    local = _manila(stamp)
    return f"{local.strftime('%B')} {local.day}, {local.year}"


def _time(stamp):
    local = _manila(stamp)
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


manila, day_text, time_text = _manila, _day, _time  # the same Manila wording, for the purge and the day-76 email


def banner(found):
    """The neutral line, and, in the last 14 days, when the page closes. It says nothing about
    deletion or about why the store is off."""
    if found.closes_at is None or found.service_end is None:
        return NEUTRAL_BANNER
    if found.now < found.closes_at - timedelta(days=NOTICE_DAYS):
        return NEUTRAL_BANNER
    text = (
        f"This store stopped taking orders on {_day(found.service_end)}. You can view your order "
        f"here until {_time(found.closes_at)} Philippine time on {_day(found.closes_at)}. "
        "After that this page closes."
    )
    if settings.PACK_PURGE_ENABLED:  # the deletion line is true only while the purge runs (block C)
        text += " " + DELETION_LINE
    return text


def closes_at_text(found):
    return None if found.closes_at is None else _manila(found.closes_at).isoformat()


def site_out(found):
    site = found.site
    return {
        "display_name": site.display_name, "accent_color": site.accent_color,
        "closes_at": closes_at_text(found), "banner": banner(found),
    }


# -- who is asking ------------------------------------------------------------------------------

def paid_orders(site, member):
    return list(
        PackOrder.objects.filter(
            site=site, member=member, payment_status__in=C.PAYMENT_OK
        ).order_by("-created_at", "-pk")
    )


def session_of(site, token):
    session = sessions.find_session(site, token)
    if session is None:
        raise ApiError("member_session_invalid", "Not signed in.", status=401)
    return session


def buyer(site, token):
    """(member, their verified orders) for a live session of this site. A member with no verified
    order answers the neutral 404, as an unknown site does."""
    member = session_of(site, token).member
    orders = paid_orders(site, member)
    if not orders:
        raise not_found()
    return member, orders


def proof_of(site, member, proof_id):
    return PackProofSubmission.objects.filter(
        site=site, public_id=proof_id, order__member=member, order__payment_status__in=C.PAYMENT_OK,
    ).first()


# -- the card -------------------------------------------------------------------------------------

def _date(value):
    return value.isoformat() if value else None


def _steps(order):
    shipped = order.shipment_recorded_at is not None
    delivered = order.delivery_confirmed_at is not None
    done = [True, True, shipped, shipped and delivered]  # a portal order is always verified
    dates = [
        _manila(order.created_at).date(), order.verified_at and _manila(order.verified_at).date(),
        order.shipped_on, order.delivered_on,
    ]
    labels = ("Order Placed", "Payment Verified", "Shipped", "Delivered")
    rows, current_seen = [], False
    for number, label in enumerate(labels, 1):
        finished = done[number - 1]
        status = "done" if finished else ("todo" if current_seen else "current")
        current_seen = current_seen or status == "current"
        rows.append({
            "num": number, "label": label, "status": status,
            "date": _date(dates[number - 1]) if finished else None,
        })
    return rows


def _registration(member):
    reg = SanteRegistration.objects.filter(member=member).first()
    if reg is None:
        return None
    return {
        "label": panels.MEMBER_REG_LABELS.get(reg.status, panels.IN_PROGRESS),
        "line": panels.member_reg_line(reg),
    }


def card(member, order):
    """One of the buyer's own verified orders, read-only. Never the address, the receiver, the
    registration details, the name beyond the first, the contacts or any billing fact."""
    shipped = order.shipment_recorded_at is not None
    return {
        "id": order.public_id, "order_number": order.order_number,
        "created_at": order.created_at.isoformat(),
        "total_centavos": order.price_centavos,
        "total_text": f"{order.price_centavos / 100:,.2f}",
        "payment_method_label": panels.METHOD_LABEL,
        "payment_status": order.payment_status,
        "payment_label": dict(C.PAYMENT_STATUSES)[order.payment_status],
        "verified_at": _date(order.verified_at),
        "steps": _steps(order),
        "courier": order.courier_name if shipped else "",
        "tracking": order.tracking_number if shipped else "",
        "shipped_on": _date(order.shipped_on),
        "delivered_on": _date(order.delivered_on),
        "proofs": [
            {
                "id": s.public_id, "seq": s.seq, "submitted_at": s.submitted_at.isoformat(),
                "amount_sent_centavos": s.amount_sent_centavos,
                "reference_number": s.reference_number, "content_type": s.content_type,
            }
            for s in order.submissions.all()
        ],
        "registration": _registration(member),
    }
