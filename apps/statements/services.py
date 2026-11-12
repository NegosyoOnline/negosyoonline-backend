"""The issuer profile and the invoice books (Billing Build B5, PR 2).

The issuer is versioned: a change is a new row, verified once by a staff member against the
certificate of registration (COR). The newest verified row is the current issuer. Nothing here
holds a value from the COR: staff enter them. Every change writes an audit entry that names
the record and never repeats its values.
"""
from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.common.errors import ApiError
from apps.invoices.models import InvoiceBook
from apps.operators import audit
from apps.statements.managers import Immutable
from apps.statements.models import IssuerProfile

# What a statement and an invoice need from the issuer, and the longest each may be.
ISSUER_FIELDS = {
    "legal_name": 160, "tin": 17, "branch_code": 10, "registered_address": 255,
    "cor_number": 40, "contact_email": 254,
}
ISSUER_OPTIONAL = {"trade_name": 160}


def _invalid(field, message):
    return ApiError(
        "invalid_request", "Some values are missing or invalid.", fields={field: [message]}
    )


def _text(body, field, limit, *, required=True):
    value = body.get(field, "")
    if not isinstance(value, str):
        raise _invalid(field, "Send text.")
    value = value.strip()
    if required and not value:
        raise _invalid(field, "This is required.")
    if len(value) > limit:
        raise _invalid(field, f"At most {limit} characters.")
    return value


def _day(body, field):
    value = body.get(field)
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise _invalid(field, "Send a date as YYYY-MM-DD.") from None


def _int(body, field, *, minimum):
    value = body.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _invalid(field, f"Send a whole number of at least {minimum}.")
    return value


def current_issuer():
    """The newest verified issuer profile, or None."""
    return (
        IssuerProfile.objects.filter(verified_at__isnull=False)
        .order_by("-verified_at", "-pk").first()
    )


def pending_issuer():
    """The newest unverified profile made after the current one, or None."""
    current = current_issuer()
    rows = IssuerProfile.objects.filter(verified_at__isnull=True)
    if current is not None:
        rows = rows.filter(pk__gt=current.pk)
    return rows.order_by("-pk").first()


def set_issuer(operator, body):
    """Record a new issuer profile, unverified. Returns it."""
    values = {f: _text(body, f, n) for f, n in ISSUER_FIELDS.items()}
    values.update({f: _text(body, f, n, required=False) for f, n in ISSUER_OPTIONAL.items()})
    if "@" not in values["contact_email"]:
        raise _invalid("contact_email", "Send an email address.")
    values["cor_date"] = _day(body, "cor_date")
    with transaction.atomic():
        issuer = IssuerProfile.objects.create(created_by=operator, **values)
        audit.record(
            operator, "billing_issuer_set", target_type="issuer", target_id=issuer.public_id,
        )
    return issuer


def verify_issuer(operator, public_id, note=""):
    """Mark a profile as checked against the COR. A profile is verified once."""
    if not isinstance(public_id, str) or not public_id:
        raise _invalid("issuer", "Name the issuer profile.")
    if not isinstance(note, str) or len(note) > 300:
        raise _invalid("note", "At most 300 characters.")
    with transaction.atomic():
        try:
            issuer = IssuerProfile.objects.select_for_update().get(public_id=public_id)
        except IssuerProfile.DoesNotExist:
            raise ApiError("not_found", "Not found.", status=404) from None
        if issuer.verified_at is not None:
            raise ApiError("already_verified", "This profile is already verified.", status=409)
        issuer.verified_at, issuer.verified_by = timezone.now(), operator
        issuer.verification_note = note.strip()
        issuer.save()
        audit.record(
            operator, "billing_issuer_verified", target_type="issuer",
            target_id=issuer.public_id, reason=issuer.verification_note,
        )
    return issuer


def add_book(operator, body):
    """Record one paper booklet, once, under the current verified issuer. Its range may not
    overlap another book of the same series; the current issuer's row is locked around that
    check so two requests cannot both pass it."""
    series = _text(body, "series", 20, required=False)
    serial_from = _int(body, "serial_from", minimum=0)
    serial_to = _int(body, "serial_to", minimum=0)
    if serial_to < serial_from:
        raise _invalid("serial_to", "The last serial cannot be before the first.")
    values = {
        "series": series, "serial_from": serial_from, "serial_to": serial_to,
        "serial_width": _int(body, "serial_width", minimum=1),
        "atp_reference": _text(body, "atp_reference", 40),
        "atp_date": _day(body, "atp_date"),
        "printer_name": _text(body, "printer_name", 160, required=False),
    }
    with transaction.atomic():
        current = current_issuer()
        if current is None:
            raise ApiError("issuer_not_verified", "Verify the issuer profile first.", status=409)
        IssuerProfile.objects.select_for_update().get(pk=current.pk)  # the lock
        book = InvoiceBook(issuer=current, **values)
        try:
            book.save()
        except Immutable:  # save() refuses an overlapping range
            raise ApiError(
                "serial_range_overlaps",
                "That serial range overlaps another book of the same series.", status=409,
            ) from None
        audit.record(
            operator, "billing_invoice_book_set", target_type="invoice_book",
            target_id=f"{book.series}:{book.serial_from}", detail={"action": "added"},
        )
    return book


def set_book_active(operator, series, serial_from, active):
    """Switch a book on or off. Nothing else about a book ever changes."""
    if not isinstance(series, str):
        raise _invalid("series", "Send text.")
    if isinstance(serial_from, bool) or not isinstance(serial_from, int):
        raise _invalid("serial_from", "Send a whole number.")
    if not isinstance(active, bool):
        raise _invalid("active", "Send true or false.")
    with transaction.atomic():
        try:
            book = InvoiceBook.objects.select_for_update().get(
                series=series, serial_from=serial_from
            )
        except InvoiceBook.DoesNotExist:
            raise ApiError("not_found", "Not found.", status=404) from None
        if book.active != active:
            book.active = active
            book.save()
            audit.record(
                operator, "billing_invoice_book_set", target_type="invoice_book",
                target_id=f"{book.series}:{book.serial_from}",
                detail={"action": "activated" if active else "deactivated"},
            )
    return book
