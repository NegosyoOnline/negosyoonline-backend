"""The Subscriber's Domain screen, under /v1/sites/{site_id}/domain/. Ids are public ids.

    GET  .../domain/          -> {eligible, locked_reason, extensions, registered_name, free_until, domain}
    (free_until: "YYYY-MM-DD" for a registered domain, else null; no price)
    POST .../domain/check/    {name} -> the same answer; the Check itself runs in the worker
    POST .../domain/buy/      {name} -> the same answer; Buy only writes rows, it never buys here
    POST .../domain/retry/    -> the same answer (a setup failure goes back to the setup steps)

404 for every route while DOMAINS_ENABLED is off, signed in or not. Then the owner session, the
site found first through site_for (404 for a missing, foreign or non-Santé site), and the
Billing-only rule (403). Errors: 400 invalid_request (`fields.name`), 409 not_eligible,
domain_exists, domain_in_progress, request_in_progress, purchase_not_open, check_first,
quote_expired, name_taken, nothing_to_retry; 429 rate_limited. The answer never holds a price, a
provider id or a token. Every answer is Cache-Control: no-store.
"""
from django.conf import settings
from django.http import JsonResponse
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.accounts.api import signed_in
from apps.common.errors import ApiError
from apps.domains import owner
from apps.sites import templates
from apps.sites.api import _owner_site
from apps.sites.services import site_for


def domain_site(request):
    """The router's sign-in step. It finds the site before any body is read."""
    if not settings.DOMAINS_ENABLED:
        raise ApiError("not_found", "Not found.", status=404)
    account = signed_in(request)
    if account:
        site_id = request.resolver_match.kwargs["site_id"]
        site_for(account, site_id, template=templates.SANTE)
        request.domain_site = _owner_site(request, site_id)
    return account


router = Router(tags=["domains"], auth=domain_site)


class NameIn(Schema):
    model_config = ConfigDict(extra="forbid")
    name: str


def _json(data):
    response = JsonResponse(data)
    response["Cache-Control"] = "no-store"
    return response


@router.get("/sites/{site_id}/domain/")
def domain_status(request, site_id: str):
    return _json(owner.status(request.domain_site))


@router.post("/sites/{site_id}/domain/check/")
def domain_check(request, site_id: str, payload: NameIn):
    return _json(owner.check(request.account, request.domain_site, payload.name))


@router.post("/sites/{site_id}/domain/buy/")
def domain_buy(request, site_id: str, payload: NameIn):
    return _json(owner.buy(request.account, request.domain_site, payload.name))


@router.post("/sites/{site_id}/domain/retry/")
def domain_retry(request, site_id: str):
    return _json(owner.retry(request.account, request.domain_site))
