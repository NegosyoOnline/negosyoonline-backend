"""Adjusting an issued statement (Billing Build B5, PR 9; plan section 7.3).

An adjustment reduces a statement: negative lines, a reason code, the original it reduces. It is
its own document (`NOCN-YYYY-######`, its own series row) with its own PDF, rendered once and
stored at creation, and it never touches the original. The sum of the reductions of one original
may never pass the original's total: the original's row is locked while that is checked, so two
adjustments made at once cannot both fit.

There is no service for an increase. Plan 7.3 says an increase is a new ordinary statement with
`original` set; nothing in section 15 or 9.2 asks a route or a service to make one.
"""
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.billing import clock, credits
from apps.common.errors import ApiError
from apps.common.ids import is_public_id
from apps.operators import audit
from apps.statements import documents, switch
from apps.statements.issue import _next_number
from apps.statements.models import Statement, StatementLine

PREFIX = "NOCN"
MAX_LINES = 10
# reason code -> the words the PDF shows
REASONS = {
    "billing_error": "Billing error",
    "duplicate_payment": "Duplicate payment",
    "service_not_delivered": "Service not delivered",
    "goodwill": "Goodwill reduction",
    "other": "Other",
}


def _invalid(field, message):
    return ApiError(
        "invalid_request", "Some values are missing or invalid.", fields={field: [message]}
    )


def _lines(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_LINES:
        raise _invalid("lines", f"Send between 1 and {MAX_LINES} lines.")
    lines = []
    for row in raw:
        description = row.get("description") if isinstance(row, dict) else None
        amount = row.get("amount_centavos") if isinstance(row, dict) else None
        if not isinstance(description, str) or not description.strip():
            raise _invalid("lines", "Each line needs a description.")
        if len(description.strip()) > 300:
            raise _invalid("lines", "A description is at most 300 characters.")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount >= 0:
            raise _invalid("lines", "Each amount is a negative whole number of centavos.")
        lines.append((description.strip(), amount))
    return lines


def remaining(original):
    """What of the original's total no adjustment has reduced yet, in centavos."""
    reduced = (
        Statement.objects.filter(original=original, document_type="adjustment")
        .aggregate(total=Sum("total_centavos"))["total"] or 0
    )
    return original.total_centavos + reduced


def adjust(operator, original_id, reason, raw_lines, *, now=None):
    """Make one adjustment of the statement `original_id`. Returns it."""
    if not switch.statements_enabled():
        raise ApiError("not_found", "Not found.", status=404)
    if reason not in REASONS:
        raise _invalid("reason", "Choose one of the listed reasons.")
    lines = _lines(raw_lines)
    total = sum(amount for _, amount in lines)
    if not is_public_id(original_id, "stm"):
        raise ApiError("not_found", "Not found.", status=404)
    with transaction.atomic():
        original = (
            Statement.objects.select_for_update(of=("self",))
            .filter(public_id=original_id).first()
        )
        if original is None:
            raise ApiError("not_found", "Not found.", status=404)
        if original.document_type != Statement.DocumentType.STATEMENT:
            raise ApiError("not_adjustable", "An adjustment cannot be adjusted.", status=409)
        if -total > remaining(original):
            raise ApiError(
                "exceeds_remaining", "That is more than is left of the statement.", status=409
            )
        sequence = Statement.objects.filter(original=original).count() + 1
        issued_at = now or timezone.now()
        number = _next_number(PREFIX, issued_at.astimezone(clock.MANILA).year)
        document = {
            "number": number, "kind": "adjustment", "issuer": original.issuer,
            "bill_to": original.bill_to, "period_start": original.period_start.isoformat(),
            "period_end": original.period_end.isoformat(), "total_centavos": total,
            "is_test": original.is_test, "payment_reference": original.payment_reference,
            "issued_at": issued_at.isoformat(), "original_number": original.number,
            "reason_text": REASONS[reason],
            "lines": [
                {"kind": "adjustment", "description": d, "quantity": 1, "unit_centavos": a,
                 "amount_centavos": a}
                for d, a in lines
            ],
        }
        try:
            stored = documents.store_document(document)
        except documents.StoreUnavailable:
            raise ApiError(
                "store_unavailable", "The statement store is not available now.", status=503
            ) from None
        try:
            adjustment = Statement.objects.create(
                number=number, document_type=Statement.DocumentType.ADJUSTMENT,
                kind="adjustment", subscription=original.subscription, cycle=original.cycle,
                source_key=f"adjustment:{original.public_id}:{sequence}", original=original,
                adjustment_reason=reason, period_start=original.period_start,
                period_end=original.period_end, total_centavos=total, is_test=original.is_test,
                issuer=original.issuer, bill_to=original.bill_to,
                payment_reference=original.payment_reference, pdf_key=stored.key,
                pdf_sha256=stored.sha256, pdf_size=stored.size, issued_at=issued_at,
            )
            StatementLine.objects.bulk_create([
                StatementLine(
                    statement=adjustment, position=position, kind="adjustment", description=d,
                    quantity=1, unit_centavos=a, amount_centavos=a,
                )
                for position, (d, a) in enumerate(lines, start=1)
            ])
            audit.record(
                operator, "billing_statement_adjusted", target_type="statement",
                target_id=adjustment.public_id, reason=reason,
                detail={
                    "number": number, "original": original.public_id,
                    "original_number": original.number, "total_centavos": total,
                },
            )
            # Billing B6: a billing error or duplicate payment takes back the credit the payment earned.
            credits.reverse_safely(adjustment, original, operator)
        except Exception:
            documents.discard(stored.key)
            raise
    return adjustment
