"""The Domains staff routes: the operator list, retry, disconnect, "cancel request" and
"confirmed registered" (design R4.7, R4.14.3 items 6 and 7) and the bridge import of a hand-bought
domain (`bridge.py`).

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
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone

from apps.common.errors import ApiError, error_body
from apps.domains import bridge, events, machine, providers, purchase
from apps.domains.models import ActorType, ClaimState, Domain, DomainEvent, DomainRegistration
from apps.operators import audit
from apps.sites.models import Site

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


def _confirm_row(registration):
    last = purchase.last_confirm(registration)
    if last is None:
        return None
    state = {"confirm_requested": "pending", "conflict_confirmed_registered": "done"}.get(last.kind, "refused")
    return {"state": state, "reason": last.detail.get("reason", ""), "at": _iso(last.created_at)}


def _claim_row(claim, registration):
    return {
        "confirm": _confirm_row(registration),
        "claim": claim.public_id, "site": claim.site.public_id, "site_name": claim.site.display_name,
        "name": claim.name, "state": claim.state,
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
        "eligible_sites": [{"id": e.site.public_id, "name": e.site.display_name} for e in eligible],
        "counters": _counters(now),
        "imports": bridge.import_rows(),
    }


def _audit(operator, action, claim, **detail):
    audit.record(operator, action, target_type="site", target_id=claim.site.public_id,
                 detail={"claim": claim.public_id, **detail})


@_route
def retry(body, operator):
    claim = _claim(body)
    if not machine.retry(claim):
        raise ApiError("nothing_to_retry", "Only a failed claim can be retried.", status=409)
    events.log(claim.site, "operator_retry", claim=claim, actor_type=ActorType.OPERATOR, actor_id=operator.pk)
    _audit(operator, "domains_retry", claim)
    return {"claim": claim.public_id, "state": claim.state}


@_route
def disconnect(body, operator):
    claim = _claim(body)
    if not machine.request_removal(claim, reason="operator_disconnect"):
        raise ApiError("already_ending", "This claim is already being removed.", status=409)
    events.log(claim.site, "operator_disconnect", claim=claim, actor_type=ActorType.OPERATOR, actor_id=operator.pk)
    _audit(operator, "domains_disconnect", claim)
    return {"claim": claim.public_id, "state": claim.state}


@_route
def cancel_request(body, operator):
    claim = _claim(body)
    purchase.cancel_request(operator, claim)  # refuses anything but an unsent `intent` row
    claim.refresh_from_db()
    _audit(operator, "domains_request_cancelled", claim)
    return {"claim": claim.public_id, "state": claim.state}


def confirmed_registered(request, operator):
    """202: validates, writes the request and enqueues the worker task. The provider read, the lock
    and the move to `succeeded` happen in the worker (the api holds no Registrar token)."""
    try:
        if not settings.DOMAINS_ENABLED:
            raise ApiError("not_found", "Not found.", status=404)
        claim = _claim(_body(request))
        outcome = purchase.request_confirmed_registered(operator, claim)
        if outcome == "requested":
            _audit(operator, "domains_confirm_requested", claim)
        _enqueue_confirm(claim.pk)
        response = JsonResponse({"claim": claim.public_id, "status": outcome}, status=202)
    except ApiError as refusal:
        response = JsonResponse(error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status)
    response["Cache-Control"] = "no-store"
    return response


def _enqueue_confirm(claim_id):
    def run():
        try:
            from apps.domains import tasks  # noqa: PLC0415

            tasks.confirm_registered.delay(claim_id)
        except Exception:  # noqa: BLE001 - the minute sweep picks the request up
            pass

    transaction.on_commit(run, robust=True)


def import_bridge(request, operator):
    """202: the bridge import of a hand-bought domain for one site (`bridge.request_import`). The
    registrar read and the move to `registered` happen in the worker. Body: {"site", "name"}."""
    try:
        if not settings.DOMAINS_ENABLED:
            raise ApiError("not_found", "Not found.", status=404)
        body = _body(request)
        public_id = body.get("site")
        site = Site.objects.filter(public_id=public_id).first() if isinstance(public_id, str) else None
        if site is None:
            raise ApiError("not_found", "Not found.", status=404)
        outcome = bridge.request_import(operator, site, body.get("name"))
        if outcome == "requested":
            audit.record(operator, "domains_import_requested", target_type="site", target_id=site.public_id,
                         detail={"name": bridge.last_import(site).detail.get("name", "")})
        response = JsonResponse({"site": site.public_id, "name": bridge.last_import(site).detail.get("name", ""),
                                 "status": outcome}, status=202)
    except ApiError as refusal:
        response = JsonResponse(error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status)
    response["Cache-Control"] = "no-store"
    return response


ROUTES = {
    "domains/list/": domain_list,
    "domains/retry/": retry,
    "domains/disconnect/": disconnect,
    "domains/cancel-request/": cancel_request,
    "domains/confirmed-registered/": confirmed_registered,
    "domains/import-bridge/": import_bridge,
}
