"""The member routes of the storefront surface, added to it by one line in apps/storefront/api.py:

    POST /storefront/v1/member/signin/request/  {host, visitor_ip, binding, contact}
    POST /storefront/v1/member/signin/resend/   {host, visitor_ip, binding}
    POST /storefront/v1/member/signin/accept-email/ {host, visitor_ip, binding}
    POST /storefront/v1/member/signin/verify/   {host, visitor_ip, binding, code}  -> {token, member}
    POST /storefront/v1/member/me/              {host, token}                    -> {member}
    POST /storefront/v1/member/signout/         {host, token}

Every call carries the storefronts server's credential (the surface checks it). The session
token travels as data: verify answers it once, for the storefronts server to put in its own
host-only cookie. So does the binding of a sign-in attempt to one browser: the storefronts server
keeps a random pre-sign-in value in its own cookie and passes it as `binding` (16 to 200
characters); a code is good only with the binding it was sent with. Django sets no cookie here. While MEMBERS_ENABLED is off, and for an unknown,
foreign or non-Santé host, every route answers the same 404 before any member table is read.
"""
import ipaddress

from ninja import Router, Schema

from apps.common.errors import ApiError
from apps.members import services, sessions, signin
from apps.storefront.services import served_sante_site

router = Router()


def _site(host):
    site = served_sante_site(host) if services.enabled() else None
    if site is None:
        raise ApiError("not_found", "Not found.", status=404)
    return site


def _ip(value):
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return ""


class MemberOut(Schema):
    id: str
    full_name: str
    email: str
    mobile: str
    email_verified: bool
    mobile_verified: bool


def _member_out(member):
    return {
        "id": member.public_id,
        "full_name": member.full_name,
        "email": member.email,
        "mobile": member.mobile,
        "email_verified": member.email_state == "verified",
        "mobile_verified": member.mobile_state == "verified",
    }


class StatusOut(Schema):
    status: str


class AnswerOut(Schema):
    step: str
    message: str
    offer_email: bool


def _answer(answer):
    return {"step": answer.step, "message": answer.message, "offer_email": answer.offer_email}


def _binding(value):
    value = (value or "").strip()
    if not 16 <= len(value) <= 200:
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"binding": ["A browser binding of 16 to 200 characters is required."]},
        )
    return value


def _paced(call):
    """Run a sign-in step. The code email is held until the challenge rows are written, sent, and
    only then is the response time padded to the floor, so neither the answer nor the time says
    whether a contact is a member and the mail provider never sets the request time."""
    started = signin.clock()
    deferred = []
    answer = call(deferred.append)
    for send in deferred:
        send()
    signin.pad(started)
    return _answer(answer)


class RequestIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    binding: str = ""
    contact: str = ""


@router.post("/signin/request/", response=AnswerOut)
def signin_request(request, body: RequestIn):
    site, binding, ip = _site(body.host), _binding(body.binding), _ip(body.visitor_ip)
    return _paced(lambda defer: signin.request_code(site, body.contact, binding_raw=binding, ip=ip, defer=defer))


class BindingIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    binding: str = ""


@router.post("/signin/resend/", response=AnswerOut)
def signin_resend(request, body: BindingIn):
    site, binding, ip = _site(body.host), _binding(body.binding), _ip(body.visitor_ip)
    return _paced(lambda defer: signin.resend(site, binding_raw=binding, ip=ip, defer=defer))


@router.post("/signin/accept-email/", response=AnswerOut)
def signin_accept_email(request, body: BindingIn):
    site, binding, ip = _site(body.host), _binding(body.binding), _ip(body.visitor_ip)
    return _paced(lambda defer: signin.accept_email(site, binding_raw=binding, ip=ip, defer=defer))


class VerifyIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    binding: str = ""
    code: str = ""


class VerifyOut(Schema):
    token: str
    member: MemberOut


@router.post("/signin/verify/", response=VerifyOut)
def signin_verify(request, body: VerifyIn):
    site, binding = _site(body.host), _binding(body.binding)
    member, token = signin.verify(site, binding_raw=binding, code=body.code, ip=_ip(body.visitor_ip))
    return {"token": token, "member": _member_out(member)}


class SessionIn(Schema):
    host: str = ""
    token: str = ""


class MeOut(Schema):
    member: MemberOut


def _session(site, token):
    session = sessions.find_session(site, token)
    if session is None:
        raise ApiError("member_session_invalid", "Not signed in.", status=401)
    return session


@router.post("/me/", response=MeOut)
def me(request, body: SessionIn):
    return {"member": _member_out(_session(_site(body.host), body.token).member)}


@router.post("/signout/", response=StatusOut)
def signout(request, body: SessionIn):
    session = sessions.find_session(_site(body.host), body.token)
    if session is not None:  # signing out twice, or with an old token, is not an error
        sessions.revoke_session(session)
    return {"status": "signed_out"}
