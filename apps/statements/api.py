"""Owner routes for the billing profile and the Billing Statements (Billing Build B5, PR 7).

Every route is signed-in only and reaches its site through sites.services.site_for, so a
missing, removed or foreign site answers the same 404, and so does a number that is not the
caller's. While BILLING_STATEMENTS_ENABLED is off every route answers that 404 before any table
is read. Nothing here changes a statement: they are insert-only and no route edits them.
"""
import re
from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponse
from ninja import Router, Schema
from pydantic import ConfigDict, StrictStr

from apps.accounts.api import signed_in
from apps.common.errors import ApiError
from apps.common.ids import is_public_id
from apps.common.pagination import DEFAULT_LIMIT, MAX_LIMIT
from apps.common.throttle import hit
from apps.sites.services import site_for
from apps.statements import documents, switch
from apps.statements.models import BillingProfile, Statement

router = Router(tags=["statements"], auth=signed_in)

PROFILE_LIMIT, PROFILE_WINDOW = 20, 3600  # profile saves per account per hour
NUMBER = re.compile(r"^NO(BS|CN)-\d{4}-\d{6}$")
PDF_FILE_NAME = "statement.pdf"


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def _site(request, site_id):
    """The caller's site. 404 while statements are off (nothing is read), and for a site that
    is missing, removed or not theirs."""
    if not switch.statements_enabled():
        raise _not_found()
    return site_for(request.account, site_id)


def _statements(site):
    return Statement.objects.filter(subscription__site=site)


def _statement(site, number):
    row = _statements(site).filter(number=number).first() if NUMBER.match(number) else None
    if row is None:
        raise _not_found()
    return row


# The billing profile

class ProfileOut(Schema):
    company_name: str
    billing_email: str
    address: str
    tin: str
    updated_at: datetime | None


class ProfileIn(Schema):
    model_config = ConfigDict(extra="forbid")

    company_name: StrictStr
    billing_email: StrictStr
    address: StrictStr
    tin: StrictStr


def _profile_out(profile):
    if profile is None:
        return {
            "company_name": "", "billing_email": "", "address": "", "tin": "",
            "updated_at": None,
        }
    return {
        "company_name": profile.company_name, "billing_email": profile.billing_email,
        "address": profile.address, "tin": profile.tin, "updated_at": profile.updated_at,
    }


def _clean(payload):
    values = {name: getattr(payload, name).strip() for name in ProfileIn.model_fields}
    fields = {}
    limits = {"company_name": 160, "address": 255, "tin": 17, "billing_email": 254}
    if not values["company_name"]:
        fields["company_name"] = ["A company or business name is needed."]
    for name, limit in limits.items():
        if len(values[name]) > limit:
            fields[name] = [f"Use at most {limit} characters."]
    if "billing_email" not in fields:
        try:
            validate_email(values["billing_email"])
        except ValidationError:
            fields["billing_email"] = ["Enter a valid email address."]
    if fields:
        raise ApiError("invalid_request", "Some values are missing or invalid.", fields=fields)
    return values


@router.get("/sites/{site_id}/billing/profile/", response=ProfileOut)
def billing_profile(request, site_id: str):
    """The billing profile, or the same keys empty (`updated_at` null) when none was saved."""
    site = _site(request, site_id)
    return _profile_out(BillingProfile.objects.filter(site=site).first())


@router.put("/sites/{site_id}/billing/profile/", response=ProfileOut)
def billing_profile_save(request, site_id: str, payload: ProfileIn):
    """Save the profile: exactly `company_name`, `billing_email`, `address`, `tin`. It changes
    no statement already issued."""
    site = _site(request, site_id)
    hit("billing-profile", request.account.pk, limit=PROFILE_LIMIT, window=PROFILE_WINDOW)
    values = _clean(payload)
    profile, _ = BillingProfile.objects.update_or_create(site=site, defaults=values)
    return _profile_out(profile)


# Statements

class StatementRowOut(Schema):
    number: str
    kind: str
    document_type: str
    issued_at: datetime
    period_start: datetime
    period_end: datetime
    total_centavos: int
    currency: str
    is_test: bool
    delivery_state: str


class StatementsOut(Schema):
    items: list[StatementRowOut]
    next_cursor: str | None


class LineOut(Schema):
    position: int
    kind: str
    description: str
    quantity: int
    unit_centavos: int
    amount_centavos: int


class StatementOut(StatementRowOut):
    label: str
    original_number: str | None
    payment_reference: str
    issuer: dict
    bill_to: dict
    lines: list[LineOut]


def _delivery_state(statement):
    """`none` until a delivery attempt exists, then the status of the latest attempt."""
    latest = max(statement.deliveries.all(), key=lambda d: d.attempt, default=None)
    return latest.status if latest else "none"


def _row(statement):
    return {
        "number": statement.number, "kind": statement.kind,
        "document_type": statement.document_type, "issued_at": statement.issued_at,
        "period_start": statement.period_start, "period_end": statement.period_end,
        "total_centavos": statement.total_centavos, "currency": statement.currency,
        "is_test": statement.is_test, "delivery_state": _delivery_state(statement),
    }


@router.get("/sites/{site_id}/billing/statements/", response=StatementsOut)
def billing_statements(request, site_id: str, limit: int = DEFAULT_LIMIT, cursor: str = ""):
    """Newest first. `cursor` is the `next_cursor` of the previous page: the public id of the
    last statement already returned."""
    site = _site(request, site_id)
    rows = _statements(site).order_by("-pk").prefetch_related("deliveries")
    if cursor:
        last = None
        if is_public_id(cursor, "stm"):
            last = _statements(site).filter(public_id=cursor).first()
        if last is None:
            raise ApiError("invalid_cursor", "That page marker is not valid.")
        rows = rows.filter(pk__lt=last.pk)
    limit = max(1, min(limit, MAX_LIMIT))
    items = list(rows[: limit + 1])
    more = len(items) > limit
    items = items[:limit]
    return {
        "items": [_row(item) for item in items],
        "next_cursor": items[-1].public_id if more else None,
    }


@router.get("/sites/{site_id}/billing/statements/{number}/", response=StatementOut)
def billing_statement(request, site_id: str, number: str):
    """One statement as stored: the totals and lines are the frozen ones."""
    site = _site(request, site_id)
    statement = _statement(site, number)
    return {
        **_row(statement), "label": statement.label,
        "original_number": statement.original.number if statement.original_id else None,
        "payment_reference": statement.payment_reference,
        "issuer": statement.issuer, "bill_to": statement.bill_to,
        "lines": [
            {
                "position": line.position, "kind": line.kind, "description": line.description,
                "quantity": line.quantity, "unit_centavos": line.unit_centavos,
                "amount_centavos": line.amount_centavos,
            }
            for line in statement.lines.all()
        ],
    }


@router.get("/sites/{site_id}/billing/statements/{number}/pdf/")
def billing_statement_pdf(request, site_id: str, number: str):
    """The stored file itself, to the Subscriber it belongs to only. It has no other address."""
    site = _site(request, site_id)
    statement = _statement(site, number)
    try:
        content = documents.read_pdf(statement)
    except documents.StoreUnavailable:
        raise ApiError(
            "statement_store_unavailable",
            "The statement could not be read. Try again in a moment.", status=503,
        ) from None
    response = HttpResponse(content, content_type=documents.CONTENT_TYPE)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = f'inline; filename="{PDF_FILE_NAME}"'
    return response
