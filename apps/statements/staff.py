"""The issuer and invoice-book routes of the HQ Finance menu (Billing Build B5, PR 2).

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/ and
calls each handler with the staff user of a session that passed both steps. Every route is a
POST with a small JSON body. A handler answers as if it did not exist while its switch is off.
"""
import json

from django.http import JsonResponse

from apps.common.errors import ApiError, error_body
from apps.invoices.models import InvoiceBook
from apps.statements import services, switch
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


ROUTES = {
    "finance/issuer/get/": _route(issuer_get, switch.issuer_enabled),
    "finance/issuer/set/": _route(issuer_set, switch.issuer_enabled),
    "finance/issuer/verify/": _route(issuer_verify, switch.issuer_enabled),
    "finance/invoices/books/": _route(books, switch.register_enabled),
    "finance/invoices/books/set/": _route(books_set, switch.register_enabled),
    "finance/invoices/books/active/": _route(books_active, switch.register_enabled),
}
