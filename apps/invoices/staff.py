"""The register routes of the HQ Finance menu (Billing Build B5, PR 3): the queue, one item,
recording the paper invoice, voiding it, and the reconciliation register.

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/ and
calls each handler with the staff user of a session that passed both steps. Every route is a
POST with a small JSON body and answers as if it did not exist while the register is off.
Scan upload, verify and reject (PR 4) are the same POST routes; the upload alone is a
multipart form, parsed in its view, and the scan file is read through `scan/` only.
"""
import json
from datetime import date

from django.http import HttpResponse, JsonResponse
from django.utils import timezone

from apps.common.errors import ApiError, error_body
from apps.invoices import scans, services
from apps.statements import switch
from apps.statements.managers import acting_operator

MAX_BODY = 4096
FORM_SLACK = 64 * 1024  # the form's own fields and boundaries, beyond the file


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


def _route(view):
    def handler(request, operator):
        try:
            if not switch.register_enabled():
                raise ApiError("not_found", "Not found.", status=404)
            with acting_operator(operator):
                return view(_body(request), operator)
        except ApiError as refusal:
            return JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
    return handler


def _form_route(view):
    """A route whose body is a multipart form. Same switch and error answers as `_route`."""
    def handler(request, operator):
        try:
            if not switch.register_enabled():
                raise ApiError("not_found", "Not found.", status=404)
            with acting_operator(operator):
                return view(request, operator)
        except ApiError as refusal:
            return JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
    return handler


def _scan(scan):
    return {
        "scan": scan.public_id, "status": scan.status, "reject_reason": scan.reject_reason,
        "content_type": scan.content_type, "size_bytes": scan.size,
        "uploaded_at": _iso(scan.uploaded_at), "decided_at": _iso(scan.decided_at),
    }


def _invoice(invoice):
    return {
        "invoice": invoice.public_id, "serial": services.serial_text(invoice),
        "series": invoice.book.series, "serial_number": invoice.serial_number,
        "status": invoice.status, "issued_on": _iso(invoice.issued_on),
        "sale_type": invoice.sale_type, "buyer_name": invoice.buyer_name,
        "buyer_tin": invoice.buyer_tin, "buyer_address": invoice.buyer_address,
        "description": invoice.description, "service_start": _iso(invoice.service_start),
        "service_end": _iso(invoice.service_end),
        "total_sales_centavos": invoice.total_sales_centavos,
        "discount_centavos": invoice.discount_centavos, "discount_kind": invoice.discount_kind,
        "withholding_centavos": invoice.withholding_centavos,
        "amount_due_centavos": invoice.amount_due_centavos, "void_reason": invoice.void_reason,
        "recorded_at": _iso(invoice.recorded_at), "verified_at": _iso(invoice.verified_at),
        "scans": [_scan(scan) for scan in invoice.scans.order_by("pk")],
    }


def queue(body, operator):
    return JsonResponse(
        services.queue(filter_name=body.get("filter", "ready"), limit=body.get("limit", 100))
    )


def detail(body, operator):
    item, invoices, live, books = services.detail(body.get("item"))
    return JsonResponse({
        "item": item.public_id, "status": item.status, "is_test": item.is_test,
        "site": item.site_public_id, "site_name": item.site_name, "plan": item.plan_code,
        "period_start": _iso(item.period_start), "period_end": _iso(item.period_end),
        "paid_at": _iso(item.paid_at), "sale_date": _iso(item.sale_date),
        "age_days": (services.manila_date(timezone.now()) - item.sale_date).days,
        "total_centavos": item.total_centavos, "lines": item.lines,
        "payment": item.source_ref, "statement": item.statement.number if item.statement_id else None,
        "notice": services.NOTICE,
        "suggested": services.suggested_text(item),
        "exceptions": services.exceptions(item, live),
        "invoices": [{**_invoice(i), "exceptions": services.exceptions(item, i)} for i in invoices],
        "books": [
            {"series": b.series, "serial_from": b.serial_from, "serial_to": b.serial_to,
             "serial_width": b.serial_width, "next_expected_hint": services.next_expected(b)}
            for b in books
        ],
        "events": [
            {"code": e.code, "at": _iso(e.recorded_at), "by_staff": e.actor_id is not None}
            for e in item.events.order_by("pk")
        ],
    })


def record(body, operator):
    return JsonResponse(_invoice(services.record_invoice(operator, body)), status=201)


def void(body, operator):
    return JsonResponse(_invoice(services.void_invoice(operator, body.get("invoice"), body.get("reason"))))


def scan(body, operator):
    """The scan file itself, in this answer only. It has no address. Each read is audited."""
    content, content_type = services.scan_for_operator(operator, body.get("scan"))
    response = HttpResponse(content, content_type=content_type)
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = f'inline; filename="{scans.FILE_NAMES[content_type]}"'
    return response


def upload(request, operator):
    """A multipart form: `invoice` (the invoice id) and `scan` (the file). The size is judged
    before the file is read."""
    if int(request.META.get("CONTENT_LENGTH") or 0) > scans.MAX_BYTES + FORM_SLACK:
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"scan": ["The file is too large."]},
        )
    for name in [*request.POST, *request.FILES]:
        if name not in ("invoice", "scan"):
            raise ApiError(
                "invalid_request", "Some values are missing or invalid.",
                fields={name: ["This value is not accepted."]},
            )
    upload_file = request.FILES.get("scan")
    if upload_file is None:
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"scan": ["A scan is needed."]},
        )
    if upload_file.size > scans.MAX_BYTES:
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"scan": ["The file is too large."]},
        )
    created = services.upload_scan(operator, request.POST.get("invoice"), upload_file.read())
    return JsonResponse(_scan(created), status=201)


def verify(body, operator):
    return JsonResponse(_scan(services.verify_scan(operator, body.get("scan"))))


def reject(body, operator):
    return JsonResponse(_scan(services.reject_scan(operator, body.get("scan"), body.get("reason"))))


def reconcile(body, operator):
    bounds = {}
    for key in ("from", "to"):
        if body.get(key) is not None:
            try:
                bounds[key] = date.fromisoformat(body[key])
            except (TypeError, ValueError):
                raise ApiError(
                    "invalid_request", "Some values are missing or invalid.",
                    fields={key: ["Send a date as YYYY-MM-DD."]},
                ) from None
    return JsonResponse(services.reconcile(issued_from=bounds.get("from"), issued_to=bounds.get("to")))


ROUTES = {
    "finance/invoices/queue/": _route(queue),
    "finance/invoices/detail/": _route(detail),
    "finance/invoices/record/": _route(record),
    "finance/invoices/void/": _route(void),
    "finance/invoices/scan/": _route(scan),
    "finance/invoices/upload/": _form_route(upload),
    "finance/invoices/verify/": _route(verify),
    "finance/invoices/reject/": _route(reject),
    "finance/invoices/reconcile/": _route(reconcile),
}
