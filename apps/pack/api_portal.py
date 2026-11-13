"""The buyer portal routes of the storefront surface, mounted by one line in apps/storefront/api.py:

    POST /storefront/v1/portal/site/              {host}                        -> {display_name, accent_color, closes_at, banner}
    POST /storefront/v1/portal/signin/request/    {host, visitor_ip, binding, contact}
    POST /storefront/v1/portal/signin/resend/     {host, visitor_ip, binding}
    POST /storefront/v1/portal/signin/accept-email/ {host, visitor_ip, binding}
    POST /storefront/v1/portal/signin/verify/     {host, visitor_ip, binding, code} -> {token}
    POST /storefront/v1/portal/me/                {host, token}                 -> {first_name}
    POST /storefront/v1/portal/signout/           {host, token}                 -> {status}
    POST /storefront/v1/portal/orders/            {host, token}                 -> {cards, closes_at, banner}
    POST /storefront/v1/portal/proof/             {host, token, proof_id}       -> the proof file

For a paid Membership Pack buyer of a Santé site that is off (`apps/pack/portal.py` says which
and until when). Every other host, and every refusal, answers the one neutral 404. Nothing here
writes except the member session and the sign-in rows the member routes already write: there is no
checkout, resubmit, registration, contact change or account page. Sign-in answers a token only
(never the buyer's contacts). Django sets no cookie.
"""
from django.http import HttpResponse
from ninja import Router, Schema

from apps.common import private_files
from apps.common.errors import ApiError
from apps.members import sessions, signin
from apps.members.api import AnswerOut, BindingIn, RequestIn, VerifyIn, _binding, _ip, _paced
from apps.pack import portal

router = Router()


class HostIn(Schema):
    host: str = ""


class SiteOut(Schema):
    display_name: str
    accent_color: str
    closes_at: str | None
    banner: str


@router.post("/site/", response=SiteOut)
def portal_site_view(request, body: HostIn):
    return portal.site_out(portal.open_site(body.host))


@router.post("/signin/request/", response=AnswerOut)
def portal_signin_request(request, body: RequestIn):
    site, binding, ip = portal.open_site(body.host).site, _binding(body.binding), _ip(body.visitor_ip)
    return _paced(lambda defer: signin.request_code(site, body.contact, binding_raw=binding, ip=ip, defer=defer))


@router.post("/signin/resend/", response=AnswerOut)
def portal_signin_resend(request, body: BindingIn):
    site, binding, ip = portal.open_site(body.host).site, _binding(body.binding), _ip(body.visitor_ip)
    return _paced(lambda defer: signin.resend(site, binding_raw=binding, ip=ip, defer=defer))


@router.post("/signin/accept-email/", response=AnswerOut)
def portal_signin_accept_email(request, body: BindingIn):
    site, binding, ip = portal.open_site(body.host).site, _binding(body.binding), _ip(body.visitor_ip)
    return _paced(lambda defer: signin.accept_email(site, binding_raw=binding, ip=ip, defer=defer))


class TokenOut(Schema):
    token: str


@router.post("/signin/verify/", response=TokenOut)
def portal_signin_verify(request, body: VerifyIn):
    site, binding = portal.open_site(body.host).site, _binding(body.binding)
    _, token = signin.verify(site, binding_raw=binding, code=body.code, ip=_ip(body.visitor_ip))
    return {"token": token}


class SessionIn(Schema):
    host: str = ""
    token: str = ""


class MeOut(Schema):
    first_name: str


@router.post("/me/", response=MeOut)
def portal_me(request, body: SessionIn):
    site = portal.open_site(body.host).site
    member, _ = portal.buyer(site, body.token)
    return {"first_name": (member.full_name.split() or [""])[0]}


class StatusOut(Schema):
    status: str


@router.post("/signout/", response=StatusOut)
def portal_signout(request, body: SessionIn):
    site = portal.open_site(body.host).site
    session = sessions.find_session(site, body.token)
    if session is not None:  # signing out twice, or with an old token, is not an error
        sessions.revoke_session(session)
    return {"status": "signed_out"}


class OrdersOut(Schema):
    cards: list[dict]
    closes_at: str | None
    banner: str


@router.post("/orders/", response=OrdersOut)
def portal_orders(request, body: SessionIn):
    found = portal.open_site(body.host)
    member, orders = portal.buyer(found.site, body.token)
    return {
        "cards": [portal.card(member, order) for order in orders],
        "closes_at": portal.closes_at_text(found), "banner": portal.banner(found),
    }


class ProofIn(SessionIn):
    proof_id: str = ""


@router.post("/proof/")
def portal_proof(request, body: ProofIn):
    """The buyer's own proof file. Another buyer's id, or an unknown one, answers the same 404."""
    site = portal.open_site(body.host).site
    member, _ = portal.buyer(site, body.token)
    sub = portal.proof_of(site, member, body.proof_id)
    if sub is None:
        raise ApiError("not_found", "Not found.", status=404)
    response = HttpResponse(private_files.read(sub.file_key), content_type=sub.content_type)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = 'inline; filename="proof"'
    return response
