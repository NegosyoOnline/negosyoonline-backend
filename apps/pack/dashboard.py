"""The Santé member Dashboard figures and My Orders cards (port of TWCinc's
negosyoonline_sante_pack/dashboard.py), built from the member's own pack orders only: product
orders do not exist yet, so their counts are zero and there are no shipment fields (courier,
tracking, shipped and delivered dates are always empty). Pure reads. Wording is TWCinc's. Money is
centavos (`*_centavos`) with the TWCinc-formatted text beside it."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from apps.pack import constants as C, panels
from apps.pack.models import PackOrder

PROBLEM = ("rejected",)
UNDER_REVIEW = ("pending_review",)
WEEK_LABELS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
MIN_AXIS = 4  # a floor on the chart's axis so one order is not a full bar
STEPS = ("Order Placed", "Payment Verified", "Shipped", "Delivered")
PROBLEM_LINES = {"rejected": "We could not verify your payment."}
HEADLINE = "Santé Membership Package ×1"
ITEM_NAME = "Santé Membership Package · 2 Boxes With Stevia, 20 Sachets"


def money(centavos):
    """TWCinc's `floatformat:2|intcomma` text of a centavo amount."""
    return f"{centavos / 100:,.2f}"


def orders_for(member):
    return list(PackOrder.objects.filter(member=member).order_by("-created_at", "-pk"))


def _local(stamp):
    return timezone.localtime(stamp, ZoneInfo(settings.TIME_ZONE))


def _paid(o):
    return o.payment_status in C.PAYMENT_OK


def summary(orders):
    """The four tiles over every order: Total Spent counts the orders whose payment was verified."""
    spent = sum((o.price_centavos for o in orders if _paid(o)), 0)
    return {
        "total_spent": money(spent), "total_spent_centavos": spent, "total_orders": len(orders),
        "delivered": 0, "needs_attention": sum(1 for o in orders if o.payment_status in PROBLEM),
    }


def order_monitor(orders, now=None):
    """Order Monitoring (Manila weeks and months, Sun to Sat)."""
    tz = ZoneInfo(settings.TIME_ZONE)
    today = timezone.localtime(now or timezone.now(), tz).date()

    def start(day):
        return datetime.combine(day, time.min, tzinfo=tz)

    month_first = today.replace(day=1)
    next_month_first = (month_first + timedelta(days=32)).replace(day=1)
    last_month_first = (month_first - timedelta(days=1)).replace(day=1)
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    stamps = [o.created_at for o in orders]

    def between(a, b):
        return sum(1 for s in stamps if start(a) <= s < start(b))

    counts = [between(sunday + timedelta(days=i), sunday + timedelta(days=i + 1)) for i in range(7)]
    y_max = max(max(counts), MIN_AXIS)
    series = []
    for label, count in zip(WEEK_LABELS, counts):
        pct = count * 100 // y_max
        series.append({"label": label, "count": count, "h": pct - pct % 5})
    this_month, last_month = between(month_first, next_month_first), between(last_month_first, month_first)
    return {
        "awaiting_approval": sum(1 for o in orders if o.payment_status in UNDER_REVIEW),
        "for_booking": sum(1 for o in orders if _paid(o)),  # none is shipped yet
        "for_shipping": 0, "delivered": 0, "delta": this_month - last_month,
        "series": series, "has_orders": bool(orders),
    }


def _steps(o):
    """Four steps, done, current or todo, with a date where there is one."""
    paid = _paid(o)
    done = [True, paid, False, False]
    dates = [_local(o.created_at).date(), o.verified_at and _local(o.verified_at).date() or None, None, None]
    rows, current_seen = [], False
    for i, label in enumerate(STEPS):
        state = "done" if done[i] else ("todo" if current_seen else "current")
        current_seen = current_seen or state == "current"
        rows.append({
            "num": i + 1, "label": label, "state": state,
            "date": dates[i].isoformat() if done[i] and dates[i] else None,
        })
    return rows


def _chip(o):
    """(label, class)."""
    if _paid(o):
        return "Preparing your order", ""
    if o.payment_status in PROBLEM:
        return dict(C.PAYMENT_STATUSES)[o.payment_status], "sf-status-expired"
    return dict(C.PAYMENT_STATUSES)[o.payment_status], ""


def _membership(member, o):
    """The Membership Status panel inside an order card (the registration chip and line)."""
    reg = panels.registration_panel(member) if _paid(o) else None
    if not reg:
        return None
    return {
        "label": reg["label"], "line": reg["line"], "tone": reg["tone"],
        "registered": reg["status"] == "registered", "sante_member_id": reg["sante_member_id"],
        "act": reg["details_form"],
    }


def card(member, o, *, first):
    chip_label, chip_class = _chip(o)
    addr = o.delivery_address or {}
    place = ", ".join(x for x in (addr.get("line"), addr.get("barangay"), addr.get("city"), addr.get("province")) if x)
    return {
        "id": o.public_id, "order_number": o.order_number, "created_at": o.created_at.isoformat(),
        "total": money(o.price_centavos), "total_centavos": o.price_centavos,
        "payment_status": o.payment_status, "payment_label": panels.METHOD_LABEL,
        "chip_label": chip_label, "chip_class": chip_class, "problem": o.payment_status in PROBLEM,
        "problem_line": PROBLEM_LINES.get(o.payment_status, ""), "awaiting_payment": False,
        "under_review": o.payment_status in UNDER_REVIEW, "steps": _steps(o), "courier": "", "tracking": "",
        "note": "", "shipping_label": "FREE", "headline": HEADLINE, "item_count": 1,
        "item_lines": [{"qty": 1, "name": ITEM_NAME}],
        "receiver_name": addr.get("receiver_name") or o.buyer_name,
        "receiver_mobile": addr.get("receiver_mobile", ""),
        "address": place + (f" {addr['zip']}" if addr.get("zip") else ""),
        "membership": _membership(member, o) if first else None,
    }


def cards(member, orders):
    """The membership shows on the newest order only: one registration per member."""
    return [card(member, o, first=i == 0) for i, o in enumerate(orders)]


def home(member):
    orders = orders_for(member)
    return {"summary": summary(orders), "order_monitor": order_monitor(orders)}


def my_orders(member):
    return {"cards": cards(member, orders_for(member))}
