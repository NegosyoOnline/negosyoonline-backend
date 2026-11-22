"""The Domains staff routes: the operator list, retry, disconnect, "cancel request" and
"confirmed registered" (design R4.7, R4.14.3 items 6 and 7).

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/ and calls
each handler with the staff user of a session that passed both steps. A handler answers as if it
did not exist (404) while DOMAINS_ENABLED is off, before any domains table is read. Every route is
a POST with a small JSON body and names a claim by its public id. Every action is audited
(`apps.operators.audit`, and a `DomainEvent` with the operator as actor). There is no "I registered
it", no "allow one more send" and no manual purchase route, and nothing here sends a `register`.
"""
import json
from datetime import timedelta

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

from apps.common.errors import ApiError, error_body
from apps.domains import events, machine, providers, purchase
from apps.domains.models import ActorType, ClaimState, Domain, DomainEvent, DomainRegistration
from apps.operators import audit

MAX_BODY = 2048
LIST_LIMIT = 100


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
            if not settings.DOMAINS_ENABLED:
                raise ApiError("not_found", "Not found.", status=404)
            response = JsonResponse(view(_body(request), operator))
        except ApiError as refusal:
            response = JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
        response["Cache-Control"] = "no-store"
        return response
    return handler


def _claim(body):
    public_id = body.get("claim")
    claim = Domain.objects.select_related("site").filter(public_id=public_id).first() if isinstance(public_id, str) else None
    if claim is None:
        raise ApiError("not_found", "Not found.", status=404)
    return claim


def _iso(value):
    return value.isoformat() if value else None


def _claim_row(claim, registration):
    return {
        "claim": claim.public_id, "site": claim.site.public_id, "name": claim.name, "state": claim.state,
        "reason": claim.reason, "state_changed_at": _iso(claim.state_changed_at),
        "registration": registration.state if registration else None,
        "registration_name": registration.name if registration else None,
        "email_state": claim.email_state,
    }


def _counters(now):
    recent = list(DomainEvent.objects.filter(kind="register_sent", created_at__gte=now - purchase.CAP_WINDOW))
    return {
        "purchases_24h": len(recent),
        "spend_24h_cents": sum(int(e.detail.get("check_registration_cost_cents") or 0) for e in recent),
        "purchase_cap": settings.DOMAINS_DAILY_PURCHASE_CAP,
        "spend_cap_cents": settings.DOMAINS_DAILY_SPEND_CAP_CENTS,
        "purchase_enabled": settings.DOMAINS_PURCHASE_ENABLED,
    }


@_route
def domain_list(body, operator):
    now = timezone.now()
    claims = list(Domain.objects.select_related("site").order_by("-pk")[:LIST_LIMIT])
    regs = {r.site_id: r for r in DomainRegistration.objects.filter(site_id__in=[c.site_id for c in claims])}
    eligible = DomainEvent.objects.filter(kind="eligible").select_related("site").order_by("-pk")[:LIST_LIMIT]
    return {
        "claims": [_claim_row(c, regs.get(c.site_id)) for c in claims],
        "eligible_sites": [e.site.public_id for e in eligible],
        "counters": _counters(now),
    }


def _audit(operator, action, claim, **detail):
    audit.record(operator, action, target_type="site", target_id=claim.site.public_id,
                 detail={"claim": claim.public_id, **detail})


@_route
def retry(body, operator):
    claim = _claim(body)
    if not machine.retry(claim):
        raise ApiError("nothing_to_retry", "Only a failed claim can be retried.", status=409)
    events.record(claim.site, "operator_retry", claim=claim, actor_type=ActorType.OPERATOR, actor_id=operator.pk)
    _audit(operator, "domains_retry", claim)
    return {"claim": claim.public_id, "state": claim.state}


@_route
def disconnect(body, operator):
    claim = _claim(body)
    if not machine.request_removal(claim, reason="operator_disconnect"):
        raise ApiError("already_ending", "This claim is already being removed.", status=409)
    events.record(claim.site, "operator_disconnect", claim=claim, actor_type=ActorType.OPERATOR, actor_id=operator.pk)
    _audit(operator, "domains_disconnect", claim)
    return {"claim": claim.public_id, "state": claim.state}


@_route
def cancel_request(body, operator):
    claim = _claim(body)
    purchase.cancel_request(operator, claim)  # refuses anything but an unsent `intent` row
    claim.refresh_from_db()
    _audit(operator, "domains_request_cancelled", claim)
    return {"claim": claim.public_id, "state": claim.state}


@_route
def confirmed_registered(body, operator):
    claim = _claim(body)
    try:
        purchase.confirmed_registered(operator, claim, providers.current())
    except ApiError as refusal:
        _audit(operator, "domains_confirm_refused", claim, reason=(refusal.fields or {}).get("reason", [""])[0])
        raise
    claim.refresh_from_db()
    _audit(operator, "domains_confirmed_registered", claim)
    return {"claim": claim.public_id, "state": claim.state}


ROUTES = {
    "domains/list/": domain_list,
    "domains/retry/": retry,
    "domains/disconnect/": disconnect,
    "domains/cancel-request/": cancel_request,
    "domains/confirmed-registered/": confirmed_registered,
}
