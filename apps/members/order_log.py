"""The member's own log of the Santé app orders they placed (ClickUp z8uq9meze0, M5').

Display only: nothing here creates a web order or moves money. Every read and write goes through
`rows_of(session)`, which filters by the session's own site and member, so another member's id
or another site's id answers the same 404 as a missing one. Write the table through here.

TWCinc source for the page this feeds: app/merchants/templates/merchants/orders.html (layout) and
app/merchants/services/orders.py:527-532 (status tiles). No TWCinc data and no import.
"""
import csv
import io
import re
import unicodedata
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone

from apps.common.errors import ApiError

from .models import ORDER_PAYMENTS, ORDER_STATUSES, Member, MemberOrderLog

PAGE_SIZE = 25
CAP = 2000
EXPORT_MAX = CAP
SEARCH_MAX = 60
NUMBER_MAX, NAME_MAX, CITY_MAX = 32, 60, 80
TOTAL_MAX_CENTAVOS = 1_000_000_000
EARLIEST = date(2007, 1, 1)
STATUSES = tuple(key for key, _ in ORDER_STATUSES)
PAYMENTS = tuple(key for key, _ in ORDER_PAYMENTS)
STATUS_LABELS, PAYMENT_LABELS = dict(ORDER_STATUSES), dict(ORDER_PAYMENTS)
NEEDS_ACTION = ("for_approval", "for_booking")
NUMBER_RE = re.compile(r"^PH[A-Z0-9-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONEY_RE = re.compile(r"^\d+(\.\d{1,2})?$")
CSV_HEADER = ["PH #", "Date", "Customer", "City", "Payment", "Total", "Status"]
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
INVALID_MSG = "Some values are missing or invalid."
FULL_MSG = f"Your log holds {CAP:,} orders, the most it can keep. Delete some to add more."
TAKEN_MSG = "This order number is already in your log."
MESSAGES = {
    "order_number": "Enter the PH order number, for example PH-000123 (letters, digits and dashes).",
    "ordered_on": "Enter a date from 2007-01-01 up to today, as YYYY-MM-DD.",
    "customer_first_name": f"Enter the customer's first name, 1 to {NAME_MAX} characters.",
    "city": f"Enter the city, 1 to {CITY_MAX} characters.",
    "payment": "Choose COD or Prepaid.",
    "total": "Enter the total in pesos, from 0 to 10,000,000.00, with at most 2 decimals.",
    "status": "Choose one of the listed statuses.",
    "q": f"Search with at most {SEARCH_MAX} characters.",
    "date_from": "Enter the date as YYYY-MM-DD.",
    "date_to": "Enter the date as YYYY-MM-DD.",
}
FIELDS = ("order_number", "ordered_on", "customer_first_name", "city", "payment", "total", "status")


def _not_found():
    return ApiError("not_found", "Not found.", status=404, event="object_not_found", reason="missing")


def _invalid(names):
    return ApiError("invalid_order", INVALID_MSG, fields={n: [MESSAGES[n]] for n in names})


def _taken():
    return ApiError("order_number_taken", TAKEN_MSG, status=409, fields={"order_number": [TAKEN_MSG]})


def rows_of(session):
    """The session member's own rows of their own site, the only way to reach the table."""
    return MemberOrderLog.objects.filter(site_id=session.site_id, member_id=session.member_id)


def pesos(centavos):
    return f"{centavos // 100}.{centavos % 100:02d}"


def order_out(row):
    return {
        "id": row.public_id, "order_number": row.order_number, "ordered_on": row.ordered_on,
        "customer_first_name": row.customer_first_name, "city": row.city, "payment": row.payment,
        "total": pesos(row.total_centavos), "status": row.status,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


# checking ---------------------------------------------------------------------

def _text(value, limit):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not 1 <= len(text) <= limit or any(unicodedata.category(c) == "Cc" for c in text):
        return None
    return text


def _date(value):
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _centavos(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = str(value).replace(",", "").strip()
    if not MONEY_RE.match(text):
        return None
    centavos = int(Decimal(text) * 100)
    return centavos if centavos <= TOTAL_MAX_CENTAVOS else None


def clean(data):
    """The checked values of a create or update body (every field is required), or ApiError 400
    `invalid_order` naming every wrong field."""
    out, bad = {}, []
    number = data.get("order_number")
    number = number.strip().upper() if isinstance(number, str) else None
    if number and len(number) <= NUMBER_MAX and NUMBER_RE.match(number) and any(c.isdigit() for c in number):
        out["order_number"] = number
    else:
        bad.append("order_number")
    day = _date(data.get("ordered_on"))
    if day is not None and EARLIEST <= day <= timezone.localdate():
        out["ordered_on"] = day
    else:
        bad.append("ordered_on")
    for name, limit in (("customer_first_name", NAME_MAX), ("city", CITY_MAX)):
        text = _text(data.get(name), limit)
        if text is None:
            bad.append(name)
        else:
            out[name] = text
    for name, allowed in (("payment", PAYMENTS), ("status", STATUSES)):
        if data.get(name) in allowed:
            out[name] = data[name]
        else:
            bad.append(name)
    centavos = _centavos(data.get("total"))
    if centavos is None:
        bad.append("total")
    else:
        out["total_centavos"] = centavos
    if bad:
        raise _invalid([n for n in FIELDS if n in bad])
    return out


# writing ----------------------------------------------------------------------

def create(session, data):
    values = clean(data)
    with transaction.atomic():
        Member.objects.select_for_update().filter(pk=session.member_id).first()  # one writer at a time
        mine = rows_of(session)
        if mine.count() >= CAP:
            raise ApiError("order_log_full", FULL_MSG, status=409)
        if mine.filter(order_number=values["order_number"]).exists():
            raise _taken()
        try:
            with transaction.atomic():
                return MemberOrderLog.objects.create(
                    site_id=session.site_id, member_id=session.member_id, **values
                )
        except IntegrityError:
            raise _taken() from None


def get(session, order_id):
    row = rows_of(session).filter(public_id=order_id).first()
    if row is None:
        raise _not_found()
    return row


def update(session, order_id, data):
    values = clean(data)
    with transaction.atomic():
        row = rows_of(session).select_for_update().filter(public_id=order_id).first()
        if row is None:
            raise _not_found()
        if rows_of(session).filter(order_number=values["order_number"]).exclude(pk=row.pk).exists():
            raise _taken()
        for name, value in values.items():
            setattr(row, name, value)
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            raise _taken() from None
    return row


def delete(session, order_id):
    with transaction.atomic():
        row = rows_of(session).select_for_update().filter(public_id=order_id).first()
        if row is None:
            raise _not_found()
        row.delete()


# reading ----------------------------------------------------------------------

def filtered(session, *, q=None, date_from=None, date_to=None, status=None):
    """The member's rows narrowed by the list's filters, or ApiError 400 `invalid_order`."""
    bad, rows = [], rows_of(session)
    if q is not None and len(q.strip()) > SEARCH_MAX:
        bad.append("q")
    start = _date(date_from) if date_from else None
    end = _date(date_to) if date_to else None
    if date_from and start is None:
        bad.append("date_from")
    if date_to and end is None:
        bad.append("date_to")
    if status and status not in STATUSES:
        bad.append("status")
    if bad:
        raise _invalid(bad)
    if q and q.strip():
        term = q.strip()
        rows = rows.filter(Q(order_number__icontains=term) | Q(customer_first_name__icontains=term))
    if start:
        rows = rows.filter(ordered_on__gte=start)
    if end:
        rows = rows.filter(ordered_on__lte=end)
    if status:
        rows = rows.filter(status=status)
    return rows


def tiles(session):
    """Rows per status over the whole log, unfiltered."""
    counts = rows_of(session).aggregate(**{s: Count("pk", filter=Q(status=s)) for s in STATUSES})
    return {s: counts[s] for s in STATUSES}


def header(session, today=None):
    """The month figures, for the Asia/Manila calendar month of `today` by ordered_on."""
    today = today or timezone.localdate()
    first = today.replace(day=1)
    following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    month = Q(ordered_on__gte=first, ordered_on__lt=following)
    found = rows_of(session).aggregate(
        orders=Count("pk", filter=month),
        sales=Sum("total_centavos", filter=month & ~Q(status="closed")),
        delivered=Count("pk", filter=month & Q(status="delivered")),
        needs=Count("pk", filter=Q(status__in=NEEDS_ACTION)),
    )
    return {
        "orders_this_month": found["orders"], "sales_this_month": pesos(found["sales"] or 0),
        "delivered_this_month": found["delivered"], "needs_action": found["needs"],
    }


def listing(session, *, page=1, **filters):
    rows = filtered(session, **filters)
    count = rows.count()
    pages = max(1, -(-count // PAGE_SIZE))
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE
    return {
        "orders": [order_out(r) for r in rows[offset:offset + PAGE_SIZE]],
        "page": page, "pages": pages, "count": count,
        "tiles": tiles(session), "header": header(session),
    }


# export -----------------------------------------------------------------------

def _cell(value):
    text = str(value)
    return "'" + text if text.startswith(FORMULA_STARTS) else text


def export_csv(session, **filters):
    """Every filtered row (at most EXPORT_MAX) as a UTF-8 CSV with a BOM, so Excel reads "Santé"."""
    rows = filtered(session, **filters)[:EXPORT_MAX]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for row in rows:
        writer.writerow([_cell(v) for v in (
            row.order_number, row.ordered_on.isoformat(), row.customer_first_name, row.city,
            PAYMENT_LABELS[row.payment], pesos(row.total_centavos), STATUS_LABELS[row.status],
        )])
    response = HttpResponse("﻿" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="orders-{timezone.localdate().isoformat()}.csv"'
    return response
