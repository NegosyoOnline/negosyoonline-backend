"""The issuer and invoice-book routes of the HQ Finance menu (Billing Build B5, PR 2).

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/ and
calls each handler with the staff user of a session that passed both steps. Every route is a
POST with a small JSON body. A handler answers as if it did not exist while its switch is off.
"""
import json

from django.http import HttpResponse, JsonResponse

from apps.common.errors import ApiError, error_body
from apps.common.ids import is_public_id
from apps.common.pagination import page
from apps.invoices.models import InvoiceBook
from apps.operators import audit
from apps.statements import adjust as adjustments, documents, services, switch
from apps.statements.models import Statement
from apps.statements.services import ISSUER_FIELDS, ISSUER_OPTIONAL

MAX_BODY = 4096


def _iso(value):
    return value.isoformat() if value is not None else None


def _body(request):
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_BODY:
            return {}
        data = json.loads(request.body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _route(view, enabled):
    def handler(request, operator):
        try:
            if not enabled():
                raise ApiError("not_found", "Not found.", status=404)
            return view(_body(request), operator)
        except ApiError as refusal:
            return JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
    return handler


def _issuer(issuer):
    if issuer is None:
        return None
    return {
        "issuer": issuer.public_id,
        **{f: getattr(issuer, f) for f in (*ISSUER_FIELDS, *ISSUER_OPTIONAL)},
        "tax_type": issuer.tax_type,
        "cor_date": _iso(issuer.cor_date),
        "created_at": _iso(issuer.created_at),
        "verified_at": _iso(issuer.verified_at),
        "verification_note": issuer.verification_note,
    }


def _book(book):
    return {
        "series": book.series, "serial_from": book.serial_from, "serial_to": book.serial_to,
        "serial_width": book.serial_width, "atp_reference": book.atp_reference,
        "atp_date": _iso(book.atp_date), "printer_name": book.printer_name,
        "active": book.active, "issuer": book.issuer.public_id,
    }


def issuer_get(body, operator):
    return JsonResponse({
        "current": _issuer(services.current_issuer()),
        "pending": _issuer(services.pending_issuer()),
    })


def issuer_set(body, operator):
    return JsonResponse(_issuer(services.set_issuer(operator, body)), status=201)


def issuer_verify(body, operator):
    return JsonResponse(
        _issuer(services.verify_issuer(operator, body.get("issuer"), body.get("note", "")))
    )


def books(body, operator):
    rows = InvoiceBook.objects.select_related("issuer").order_by("series", "serial_from")
    return JsonResponse({"items": [_book(b) for b in rows]})


def books_set(body, operator):
    return JsonResponse(_book(services.add_book(operator, body)), status=201)


def books_active(body, operator):
    book = services.set_book_active(
        operator, body.get("series"), body.get("serial_from"), body.get("active")
    )
    return JsonResponse(_book(book))


def _statement(statement):
    """A statement as Finance sees it. Never where the file is kept or its hash."""
    site = statement.subscription.site
    return {
        "statement": statement.public_id, "number": statement.number,
        "document_type": statement.document_type, "kind": statement.kind,
        "site": {"id": site.public_id, "name": site.display_name},
        "total_centavos": statement.total_centavos, "currency": statement.currency,
        "is_test": statement.is_test, "issued_at": _iso(statement.issued_at),
        "period_start": _iso(statement.period_start), "period_end": _iso(statement.period_end),
        "payment_reference": statement.payment_reference,
        "original": statement.original.public_id if statement.original_id else None,
        "original_number": statement.original.number if statement.original_id else None,
        "adjustment_reason": statement.adjustment_reason,
    }


def _statement_of(body):
    statement_id = body.get("statement")
    if not isinstance(statement_id, str) or not statement_id:
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"statement": ["Name the statement."]},
        )
    statement = None
    if is_public_id(statement_id, "stm"):
        statement = (
            Statement.objects.select_related("subscription__site", "original")
            .filter(public_id=statement_id).first()
        )
    if statement is None:
        raise ApiError("not_found", "Not found.", status=404)
    return statement


def _int_or_none(body, field):
    value = body.get(field)
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={field: ["Enter a whole number."]},
        )
    return value


def statements_list(body, operator):
    cursor = body.get("cursor") or ""
    if not isinstance(cursor, str):
        raise ApiError("invalid_cursor", "That page marker is not valid.")
    rows = Statement.objects.select_related("subscription__site", "original")
    site = body.get("site")
    if site is not None:
        if not isinstance(site, str):
            raise ApiError(
                "invalid_request", "Some values are missing or invalid.",
                fields={"site": ["Send text."]},
            )
        rows = rows.filter(subscription__site__public_id=site)
    document_type = body.get("document_type")
    if document_type is not None:
        if document_type not in Statement.DocumentType.values:
            raise ApiError(
                "invalid_request", "Some values are missing or invalid.",
                fields={"document_type": ["Choose statement or adjustment."]},
            )
        rows = rows.filter(document_type=document_type)
    items, next_cursor = page(
        rows, limit=_int_or_none(body, "limit"), cursor=cursor, prefix="stm"
    )
    return JsonResponse({"items": [_statement(s) for s in items], "next_cursor": next_cursor})


def statements_detail(body, operator):
    statement = _statement_of(body)
    lines = statement.lines.order_by("position")
    return JsonResponse({
        **_statement(statement),
        "lines": [
            {"position": line.position, "kind": line.kind, "description": line.description,
             "quantity": line.quantity, "unit_centavos": line.unit_centavos,
             "amount_centavos": line.amount_centavos}
            for line in lines
        ],
        "adjustments": [
            {"statement": c.public_id, "number": c.number, "total_centavos": c.total_centavos,
             "reason": c.adjustment_reason}
            for c in statement.corrections.filter(document_type="adjustment").order_by("pk")
        ],
        "remaining_centavos": (
            adjustments.remaining(statement) if statement.document_type == "statement" else None
        ),
        "reasons": list(adjustments.REASONS),
    })


def statements_pdf(body, operator):
    """The file itself, in this answer only. It has no address. Each read is audited."""
    statement = _statement_of(body)
    try:
        content = documents.read_pdf(statement)
    except documents.StoreUnavailable:
        raise ApiError(
            "store_unavailable", "The statement file is not available now.", status=503
        ) from None
    audit.record(
        operator, "billing_statement_viewed", target_type="statement",
        target_id=statement.public_id, detail={"number": statement.number},
    )
    response = HttpResponse(content, content_type=documents.CONTENT_TYPE)
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = f'inline; filename="{statement.number}.pdf"'
    return response


def statements_adjust(body, operator):
    made = adjustments.adjust(
        operator, body.get("statement"), body.get("reason"), body.get("lines")
    )
    return JsonResponse(_statement(Statement.objects.select_related(
        "subscription__site", "original").get(pk=made.pk)), status=201)


ROUTES = {
    "finance/issuer/get/": _route(issuer_get, switch.issuer_enabled),
    "finance/issuer/set/": _route(issuer_set, switch.issuer_enabled),
    "finance/issuer/verify/": _route(issuer_verify, switch.issuer_enabled),
    "finance/invoices/books/": _route(books, switch.register_enabled),
    "finance/invoices/books/set/": _route(books_set, switch.register_enabled),
    "finance/invoices/books/active/": _route(books_active, switch.register_enabled),
    "finance/statements/list/": _route(statements_list, switch.statements_enabled),
    "finance/statements/detail/": _route(statements_detail, switch.statements_enabled),
    "finance/statements/pdf/": _route(statements_pdf, switch.statements_enabled),
    "finance/statements/adjust/": _route(statements_adjust, switch.statements_enabled),
}
