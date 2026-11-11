"""The member routes of the storefront surface, added to it by one line in apps/storefront/api.py:

    POST /storefront/v1/member/signin/request/  {host, visitor_ip, email}
    POST /storefront/v1/member/signin/verify/   {host, visitor_ip, email, code}  -> {token, member}
    POST /storefront/v1/member/me/              {host, token}                    -> {member}
    POST /storefront/v1/member/signout/         {host, token}

Every call carries the storefronts server's credential (the surface checks it). The session
token travels as data: verify answers it once, for the storefronts server to put in its own
host-only cookie. Django sets no cookie here. While MEMBERS_ENABLED is off, and for an unknown,
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


class RequestIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    email: str = ""


@router.post("/signin/request/", response=StatusOut)
def signin_request(request, body: RequestIn):
    signin.request_code(_site(body.host), body.email, _ip(body.visitor_ip))
    return {"status": "sent"}  # the same for a member and for anyone else


class VerifyIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    email: str = ""
    code: str = ""


class VerifyOut(Schema):
    token: str
    member: MemberOut


@router.post("/signin/verify/", response=VerifyOut)
def signin_verify(request, body: VerifyIn):
    member, token = signin.verify_code(_site(body.host), body.email, body.code, _ip(body.visitor_ip))
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
