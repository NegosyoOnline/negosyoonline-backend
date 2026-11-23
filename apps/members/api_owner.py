"""The Subscriber's help for a member who lost both contacts, under /v1/sites/{site_id}/members/:

    GET  .../members/?q=                          -> {members: [row]} (2+ characters, 20 rows)
    GET  .../members/?promo=1&q=&page=            -> {members, page, has_more}: only members who agreed to promotional
                                                     texts, q optional, 20 per page, newest first (PROMO_SMS_CONSENT_ENABLED)
    GET  .../members/by-order/?ids=a,b            -> {members: {order_id: row}} for Membership Package orders
    GET  .../members/{id}/recovery/               -> {member, items, hold, recent_recoveries}
    POST .../members/{id}/recovery/check/         {answers: {item: matched|not_matched}} -> {check_id, passed, expires_at}
    POST .../members/{id}/recovery/               {check_id, new_email, new_mobile?} -> {saved_message, ...}

Ids are public ids. Only the owner session reaches these; the site is found first through site_for
(404 for a missing, foreign or non-Santé site), then the Billing-only rule (403). While MEMBERS_ENABLED
is off every route answers 404, signed in or not. Nothing here returns a full email or mobile.
Every answer is Cache-Control: no-store.
"""
from django.conf import settings
from django.http import JsonResponse
from ninja import Query, Router, Schema
from pydantic import ConfigDict

from apps.accounts.api import signed_in
from apps.accounts.sessions import client_ip
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.sites import templates
from apps.sites.api import _owner_site
from apps.sites.services import site_for

from . import contacts, holds, promo, recovery, sms
from .models import Member, MemberContactAudit

SEARCH_LIMIT = ("members-search-acct", 60, 600)
CHECK_LIMIT = ("members-check-acct", 10, 3600)
RECOVER_LIMIT = ("members-recover-acct", 5, 86400)
SEARCH_MAX = 20
BY_ORDER_MAX = 200
RECENT = 5
EMAIL_REQUIRED = "Enter the member's new email; they sign in with it."


def member_site(request):
    """The router's sign-in step. It finds the site before any body is read."""
    if not settings.MEMBERS_ENABLED:
        raise ApiError("not_found", "Not found.", status=404)
    account = signed_in(request)
    if account:
        site_id = request.resolver_match.kwargs["site_id"]
        site_for(account, site_id, template=templates.SANTE)
        request.member_site = _owner_site(request, site_id)
    return account


router = Router(tags=["member-recovery"], auth=member_site)


class CheckIn(Schema):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str]


class RecoverIn(Schema):
    model_config = ConfigDict(extra="forbid")
    check_id: str
    new_email: str = ""
    new_mobile: str = ""


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _limit(request, scope):
    hit(scope[0], request.account.pk, limit=scope[1], window=scope[2])


def _member(site, member_id):
    member = Member.objects.filter(site=site, public_id=member_id).first()
    if member is None:
        raise ApiError("not_found", "Not found.", status=404)
    return member


def _hold(member):
    hold = holds.current_hold(member)
    return hold if hold is not None and hold.kind == "recovery" else None


def row(member, hold=None):
    out = {
        "id": member.public_id, "full_name": member.full_name,
        "mobile_masked": contacts.mask("mobile", member.mobile), "email_masked": contacts.mask("email", member.email),
        "mobile_state": member.mobile_state, "email_state": member.email_state,
        "hold_ends_at": hold.ends_at.isoformat() if hold else None,
        "hold_ends_display": holds.display(hold.ends_at) if hold else None,
        "closed": recovery.is_closed(member),
    }
    if promo.enabled():
        out["promo_sms_ok"] = member.promo_sms_ok
    return out


def _address(member):
    a = member.delivery_address or {}
    return ", ".join(str(a[k]) for k in ("line", "barangay", "city", "province", "zip") if a.get(k))


def _items(member):
    """The checklist with what the Subscriber's own records hold for each applicable item."""
    own = {"full_name": member.full_name, "delivery_address": _address(member)}
    out = []
    for item in recovery.checklist_items(member):
        if item["applicable"] and "reference" not in item and item["key"] in own:
            item = {**item, "reference": own[item["key"]]}
        out.append(item)
    return out


def _recent(member):
    audits = MemberContactAudit.objects.filter(member=member, site_id=member.site_id, kind="subscriber_recovery"
                                               ).order_by("-created_at", "-pk")[:RECENT]
    return [{"field": a.field, "old_masked": a.old_masked, "new_masked": a.new_masked,
             "at": a.created_at.isoformat()} for a in audits]


def _detail(member):
    hold = _hold(member)
    return {
        "member": row(member, hold), "items": _items(member), "hold": holds.hold_out(hold),
        "recent_recoveries": _recent(member),
    }


@router.get("/sites/{site_id}/members/")
def members_search(request, site_id: str, q: str = "", promo_filter: str = Query("", alias="promo"), page: int = 1):
    _limit(request, SEARCH_LIMIT)
    if promo_filter == "1" and promo.enabled():
        return _json(_promo_page(request.member_site, q, page))
    found = recovery.search(Member.objects.filter(site=request.member_site), q)[:SEARCH_MAX]
    return _json({"members": [row(m, _hold(m)) for m in found]})


def _promo_page(site, q, page):
    """The members who agreed to promotional texts, newest first, SEARCH_MAX a page. A blank q lists
    them all; otherwise the same search as the plain list is narrowed to them."""
    agreed = Member.objects.filter(site=site, promo_sms_ok=True)
    found = recovery.search(agreed, q) if (q or "").strip() else agreed
    page = max(page, 1)
    found = found.order_by("-created_at", "-pk")[(page - 1) * SEARCH_MAX:page * SEARCH_MAX + 1]
    found = list(found)
    return {"members": [row(m, _hold(m)) for m in found[:SEARCH_MAX]], "page": page,
            "has_more": len(found) > SEARCH_MAX}


@router.get("/sites/{site_id}/members/by-order/")
def members_by_order(request, site_id: str, ids: str = ""):
    """The member of each Membership Package order named in `ids`, so the Pack orders list can show
    the recovery action or the running hold per row. Orders of another site are left out."""
    from apps.pack.models import PackOrder

    wanted = [i for i in dict.fromkeys(x.strip() for x in ids.split(",")) if i][:BY_ORDER_MAX]
    orders = PackOrder.objects.select_related("member").filter(site=request.member_site, public_id__in=wanted)
    # An order with no member yet (a Xendit order not paid) has no recovery action: left out, like another site's.
    return _json({"members": {o.public_id: row(o.member, _hold(o.member)) for o in orders if o.member_id}})


@router.get("/sites/{site_id}/members/{member_id}/recovery/")
def recovery_detail(request, site_id: str, member_id: str):
    return _json(_detail(_member(request.member_site, member_id)))


@router.post("/sites/{site_id}/members/{member_id}/recovery/check/")
def recovery_check(request, site_id: str, member_id: str, payload: CheckIn):
    _limit(request, CHECK_LIMIT)
    member = _member(request.member_site, member_id)
    recovery.assert_open(member)
    check = recovery.record_checklist(request.account, request.member_site, member, payload.answers)
    return _json({"check_id": str(check.pk), "passed": check.passed, "expires_at": check.expires_at.isoformat()})


@router.post("/sites/{site_id}/members/{member_id}/recovery/")
def recovery_save(request, site_id: str, member_id: str, payload: RecoverIn):
    _limit(request, RECOVER_LIMIT)
    member = _member(request.member_site, member_id)
    if not sms.available() and not payload.new_email.strip():
        raise ApiError("email_required", EMAIL_REQUIRED, status=422, fields={"new_email": [EMAIL_REQUIRED]})
    if not payload.check_id.isdigit():
        raise ApiError("recovery_check_invalid",
                       "Complete the recovery checklist again before changing a contact.", status=409)
    result = recovery.recover_contact(
        request.account, request.member_site, member.pk, check_id=int(payload.check_id),
        new_mobile=payload.new_mobile.strip() or None, new_email=payload.new_email.strip() or None,
        ip=client_ip(request))
    if result is None:
        raise ApiError("not_found", "Not found.", status=404)
    member, hold = result
    return _json({"saved_message": recovery.saved_message(hold, member), "hold_ends_at": hold.ends_at.isoformat(),
                  "hold_ends_display": holds.display(hold.ends_at), "email_masked": contacts.mask("email", member.email),
                  "mobile_masked": contacts.mask("mobile", member.mobile)})
