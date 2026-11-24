"""The Finance routes: where an operator reviews bank transfer proofs in the HQ staff app.

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/
and calls each handler with the staff user of a session that passed both steps; the allowed
Origin, the method and the session are checked there and nowhere else. A handler answers as
if it did not exist while bank transfer is off, and reads no billing table then.

Every route is a POST with a small JSON body, the ones that only read too: the staff app
puts no value in an address or a query string. Nothing here settles anything itself:
`services.confirm_transfer` and `services.reject_transfer` do, and write the audit entries.
"""
import json

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone

from apps.billing import accounts, plan, services, usage
from apps.billing.models import BankTransferPayment
from apps.common.errors import ApiError, error_body
from apps.common.pagination import page

MAX_BODY = 2048
PROOF_FILE_NAMES = {
    "image/jpeg": "proof.jpg", "image/png": "proof.png", "application/pdf": "proof.pdf",
}
Status = BankTransferPayment.Status


def _now():
    return timezone.now()


def _iso(value):
    return value.isoformat() if value is not None else None


def _invalid(field, message):
    return ApiError(
        "invalid_request", "Some values are missing or invalid.", fields={field: [message]}
    )


def _body(request):
    """The JSON object sent, or {} when there is none, it is too large or it is not one."""
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_BODY:
            return {}
        data = json.loads(request.body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _route(view):
    """A handler as staff_views calls it: closed while bank transfer is off, and a refusal
    from a service answered in the one error shape."""
    def handler(request, operator):
        try:
            if not plan.collection_live():
                raise ApiError("not_found", "Not found.", status=404)
            return view(_body(request), operator)
        except ApiError as refusal:
            return JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
    return handler


def _usage_route(view):
    """The usage review routes: closed (404) while no usage switch is on, and a refusal
    answered in the one error shape. They read no usage table then."""
    def handler(request, operator):
        try:
            if not plan.any_usage_enabled():
                raise ApiError("not_found", "Not found.", status=404)
            return view(_body(request), operator)
        except ApiError as refusal:
            return JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
    return handler


def _accounts_route(view):
    """The receiving account routes: closed (404) only while BILLING_ENABLED is off, and they
    read no billing table then. They do not wait for `collection_live()`: that needs an
    account, so the first one could never be entered."""
    def handler(request, operator):
        try:
            if not settings.BILLING_ENABLED:
                raise ApiError("not_found", "Not found.", status=404)
            return view(_body(request), operator)
        except ApiError as refusal:
            return JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
    return handler


def _payment_of(body):
    payment_id = body.get("payment")
    if not isinstance(payment_id, str) or not payment_id:
        raise _invalid("payment", "Name the payment.")
    return services.transfer_for_operator(payment_id)


def _payment(payment):
    """A payment as Finance sees it. Never where the file is kept or its hash."""
    if payment.plan_code == plan.USAGE_ONLY:
        bought = None
    else:
        bought = plan.version_of(payment.plan_code, payment.plan_version or 1)
    site = payment.subscription.site
    rejected = payment.status == Status.REJECTED
    cycle = payment.cycle
    return {
        "id": payment.public_id,
        "status": payment.status,
        "site": {"id": site.public_id, "name": site.display_name},
        "plan": (
            {"code": bought.code, "name": bought.name, "months": bought.months} if bought
            else {
                "code": plan.USAGE_ONLY, "months": 0,
                "name": {
                    "storage": "Storage usage", "orders": "Orders over 500",
                }.get(usage.overage_name(payment), "Usage overage"),
            }
        ),
        "amount_centavos": payment.amount_centavos,
        "currency": payment.currency,
        "transferred_on": payment.transferred_on.isoformat(),
        "transfer_reference": payment.transfer_reference,
        "transferred_at": _iso(payment.transferred_at),
        "sender_bank": payment.sender_bank,
        "sender_first_name": payment.sender_first_name,
        "sender_last_name": payment.sender_last_name,
        "sender_account_number": payment.sender_account_number,
        "amount_sent_centavos": payment.amount_sent_centavos,
        # What the Subscriber typed is not the amount due: staff see it before deciding.
        "amount_sent_differs": (
            payment.amount_sent_centavos is not None
            and payment.amount_sent_centavos != payment.amount_centavos
        ),
        "submitted_at": _iso(payment.submitted_at),
        "decided_at": _iso(payment.decided_at),
        "decided_by": payment.decided_by.username if payment.decided_by else None,
        "amount_received_centavos": payment.amount_received_centavos,
        "reject_reason": payment.reject_reason if rejected else None,
        "reject_note": payment.reject_note if rejected else None,
        "proof": {"content_type": payment.proof_content_type, "size_bytes": payment.proof_size},
        "cycle": cycle and {
            "sequence": cycle.sequence, "start_at": _iso(cycle.start_at),
            "end_at": _iso(cycle.end_at),
        },
    }


@_route
def queue(body, operator):
    status = body.get("status")
    if not isinstance(status, str) or status not in Status.values:
        raise _invalid("status", "Choose one of the listed statuses.")
    limit = body.get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
        raise _invalid("limit", "Enter a whole number.")
    cursor = body.get("cursor") or ""
    if not isinstance(cursor, str):
        raise ApiError("invalid_cursor", "That page marker is not valid.")
    items, next_cursor = page(
        services.review_queue(status), limit=limit, cursor=cursor, prefix="btp"
    )
    return JsonResponse({
        "items": [_payment(payment) for payment in items],
        "next_cursor": next_cursor,
        "under_review_count": services.review_queue(Status.SUBMITTED).count(),
    })


@_route
def detail(body, operator):
    payment = _payment_of(body)
    subscription = payment.subscription
    return JsonResponse({
        **_payment(payment),
        # Shown here only, never in the queue: a wrong amount is settled with the Subscriber
        # outside the system (Coach Ed, 2026-11-11).
        "subscriber_email": subscription.site.account.email,
        "subscription": {
            "state": subscription.state,
            "trial_end_at": _iso(subscription.trial_end_at),
            "service_ended_at": _iso(subscription.service_ended_at),
        },
        "history": [
            {"code": event.code, "actor": event.actor, "at": _iso(event.recorded_at)}
            for event in payment.events.order_by("pk")
        ],
    })


@_route
def proof(body, operator):
    """The file itself, in this answer only. It has no address. Each read is audited."""
    payment = _payment_of(body)
    content, content_type = services.proof_for_operator(payment, operator)
    response = HttpResponse(content, content_type=content_type)
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = f'inline; filename="{PROOF_FILE_NAMES[content_type]}"'
    return response


@_route
def confirm(body, operator):
    payment = _payment_of(body)
    amount = body.get("amount_received_centavos")
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise _invalid("amount_received_centavos", "Enter the amount received, in centavos.")
    services.confirm_transfer(payment, operator, amount_received_centavos=amount, now=_now())
    return JsonResponse(_payment(services.transfer_for_operator(payment.public_id)))


@_route
def reject(body, operator):
    payment = _payment_of(body)
    reason, note = body.get("reason"), body.get("note", "")
    if not isinstance(reason, str):
        raise _invalid("reason", "Choose one of the listed reasons.")
    services.reject_transfer(payment, operator, reason=reason, note=note, now=_now())
    return JsonResponse(_payment(services.transfer_for_operator(payment.public_id)))


def _entry(entry):
    """A usage record as Finance sees it: no personal value and no producer detail."""
    return {
        "entry": entry.source_reference,
        "kind": entry.kind,
        "status": entry.status,
        "reason": entry.reason,
        "site": {
            "id": entry.subscription.site.public_id, "name": entry.subscription.site.display_name,
        },
        "cycle": entry.cycle.sequence if entry.cycle else None,
        "period_index": entry.period_index,
        "period_end_at": _iso(entry.occurred_at),
        "received_at": _iso(entry.received_at),
        "measured_bytes": entry.quantity,
    }


@_usage_route
def usage_list(body, operator):
    limit, cursor = body.get("limit"), body.get("cursor") or ""
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
        raise _invalid("limit", "Enter a whole number.")
    if not isinstance(cursor, str):
        raise ApiError("invalid_cursor", "That page marker is not valid.")
    limit = max(1, min(limit if limit is not None else 20, 100))
    rows = usage.review_rows()
    if cursor:
        last = rows.filter(source_reference=cursor).first()
        if last is None:
            raise ApiError("invalid_cursor", "That page marker is not valid.")
        rows = rows.filter(pk__gt=last.pk)
    items = list(rows[: limit + 1])
    more = len(items) > limit
    items = items[:limit]
    return JsonResponse({
        "items": [_entry(entry) for entry in items],
        "next_cursor": items[-1].source_reference if more else None,
        "needs_review_count": usage.review_rows().count(),
    })


@_usage_route
def usage_detail(body, operator):
    entry = usage.entry_for_review(body.get("entry"))
    return JsonResponse({
        **_entry(entry),
        "history": [
            {"code": event.code, "actor": event.actor, "at": _iso(event.recorded_at)}
            for event in entry.events.order_by("pk")
        ],
    })


def _decided(body, operator, action):
    entry, _ = action(body.get("entry"), operator, body.get("reason"), now=_now())
    entry = usage.entry_for_review(entry.source_reference)
    return JsonResponse({
        **_entry(entry),
        "history": [
            {"code": event.code, "actor": event.actor, "at": _iso(event.recorded_at)}
            for event in entry.events.order_by("pk")
        ],
    })


@_usage_route
def usage_admit(body, operator):
    return _decided(body, operator, usage.admit)


@_usage_route
def usage_dismiss(body, operator):
    return _decided(body, operator, usage.dismiss)


@_accounts_route
def accounts_list(body, operator):
    return JsonResponse(accounts.listing())


@_accounts_route
def accounts_add(body, operator):
    return JsonResponse(accounts.as_dict(accounts.add(operator, body)))


@_accounts_route
def accounts_edit(body, operator):
    return JsonResponse(accounts.as_dict(accounts.edit(operator, body)))


@_accounts_route
def accounts_active(body, operator):
    return JsonResponse(accounts.as_dict(accounts.set_active(operator, body)))


ROUTES = {
    "finance/payments/list/": queue,
    "finance/payments/detail/": detail,
    "finance/payments/proof/": proof,
    "finance/payments/confirm/": confirm,
    "finance/payments/reject/": reject,
    "finance/usage/review/list/": usage_list,
    "finance/usage/review/detail/": usage_detail,
    "finance/usage/review/admit/": usage_admit,
    "finance/usage/review/dismiss/": usage_dismiss,
    "finance/accounts/list/": accounts_list,
    "finance/accounts/add/": accounts_add,
    "finance/accounts/edit/": accounts_edit,
    "finance/accounts/active/": accounts_active,
}
