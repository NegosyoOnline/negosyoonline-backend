"""The member routes of the storefront surface, added to it by one line in apps/storefront/api.py:

    POST /storefront/v1/member/signin/request/  {host, visitor_ip, binding, contact}
    POST /storefront/v1/member/signin/resend/   {host, visitor_ip, binding}
    POST /storefront/v1/member/signin/accept-email/ {host, visitor_ip, binding}
    POST /storefront/v1/member/signin/verify/   {host, visitor_ip, binding, code}  -> {token, member}
    POST /storefront/v1/member/me/              {host, token}                    -> {member}
    POST /storefront/v1/member/signout/         {host, token}
    POST /storefront/v1/member/address/         {host, visitor_ip, member_token, line, barangay, city, province,
                                                 zip, landmark} -> {home_address, home_address_source}

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
from apps.members import promo, services, sessions, signin
from apps.storefront.services import served_sante_site

router = Router()


def _site(host):
    # A store HQ has suspended by hand (Site.operator_hold) still answers its members: sign-in
    # codes still send and members read their own orders. The public host and checkout stay closed.
    site = served_sante_site(host, include_held=True) if services.enabled() else None
    if site is None:
        raise ApiError("not_found", "Not found.", status=404, event="site_not_found", reason="missing")
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


# -- the member's Account page (TWCinc negosyoonline_members/site_views.py: account, verify_email,
#    change_mobile, change_email). Every call carries {host, token} (the member session) and, where a
#    code is sent or checked, `visitor_ip`. The raw session token is the binding of every code.

from apps.members import contact_change, contacts, holds  # noqa: E402


class AccountIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    token: str = ""


def _hold_banner(member):
    hold = holds.current_hold(member)
    return holds.banner(hold) if hold is not None else ""


class AccountOut(Schema):
    member_name: str
    mobile_masked: str
    mobile_state: str
    email_masked: str
    email_state: str
    default_channel: str
    hold_banner: str


@router.post("/account/", response=AccountOut)
def account(request, body: AccountIn):
    member = _session(_site(body.host), body.token).member
    return {
        "member_name": member.full_name, "mobile_masked": contacts.mask("mobile", member.mobile),
        "mobile_state": member.mobile_state, "email_masked": contacts.mask("email", member.email),
        "email_state": member.email_state, "default_channel": services.default_channel(member),
        "hold_banner": _hold_banner(member),
    }


class VerifyEmailOut(Schema):
    step: str  # send | code | done
    email_masked: str
    message: str


def _email_step(site, member, raw):
    from apps.members import otp
    if member.email_state == "verified":
        return "done"
    return "code" if otp.latest_open(site, purpose="email_verify", binding_raw=raw) else "send"


def _email_out(site, member, raw, message=""):
    return {"step": _email_step(site, member, raw), "email_masked": contacts.mask("email", member.email),
            "message": message}


@router.post("/account/verify-email/", response=VerifyEmailOut)
def verify_email_state(request, body: AccountIn):
    site = _site(body.host)
    return _email_out(site, _session(site, body.token).member, body.token)


@router.post("/account/verify-email/send/", response=VerifyEmailOut)
def verify_email_send(request, body: AccountIn):
    site = _site(body.host)
    member = _session(site, body.token).member
    contact_change.email_verification_start(site, member, session_raw=body.token, ip=_ip(body.visitor_ip))
    return _email_out(site, member, body.token)


class CodeIn(AccountIn):
    code: str = ""


@router.post("/account/verify-email/verify/", response=VerifyEmailOut)
def verify_email_verify(request, body: CodeIn):
    site = _site(body.host)
    session = _session(site, body.token)
    member = contact_change.email_verification_confirm(
        site, session.member, session=session, session_raw=body.token, code=body.code, ip=_ip(body.visitor_ip)
    )
    return _email_out(site, member, body.token, contact_change.EMAIL_VERIFIED)


class ChangeStateOut(Schema):
    field: str
    current_masked: str
    proof_channel: str  # sms | email
    proof_masked: str
    step: str  # current | current_code | replacement | replacement_code


def _field(field):
    if field not in ("mobile", "email"):
        raise ApiError("not_found", "Not found.", status=404)
    return field


def _change_state(site, member, raw, field):
    channel, target = contact_change._current_target(member, field)
    return {
        "field": field, "current_masked": contacts.mask(field, getattr(member, field)),
        "proof_channel": channel, "proof_masked": contacts.mask("mobile" if channel == "sms" else "email", target),
        "step": contact_change.change_state(site, member, session_raw=raw, field=field),
    }


@router.post("/account/change/{field}/", response=ChangeStateOut)
def change_state(request, field: str, body: AccountIn):
    site, field = _site(body.host), _field(field)
    return _change_state(site, _session(site, body.token).member, body.token, field)


class ChangeIn(CodeIn):
    action: str = ""  # send_current | verify_current | send_replacement | verify_replacement
    new_value: str = ""


class ChangeOut(Schema):
    state: ChangeStateOut | None = None
    changed: bool  # true: the contact was replaced and every session of the member is revoked


@router.post("/account/change/{field}/act/", response=ChangeOut)
def change_act(request, field: str, body: ChangeIn):
    """`action` is send_current, verify_current (`code`), send_replacement (`new_value`) or
    verify_replacement (`code`). The last one replaces the contact and revokes every session of the
    member, this one included: the storefronts server clears its cookie and shows the sign-in page."""
    site, field = _site(body.host), _field(field)
    session = _session(site, body.token)
    member, raw, ip = session.member, body.token, _ip(body.visitor_ip)
    if body.action == "send_current":
        contact_change.current_proof_start(site, member, session_raw=raw, field=field, ip=ip)
    elif body.action == "verify_current":
        contact_change.current_proof_confirm(site, member, session_raw=raw, field=field, code=body.code, ip=ip)
    elif body.action == "send_replacement":
        contact_change.replacement_start(site, member, session_raw=raw, field=field, raw_value=body.new_value, ip=ip)
    elif body.action == "verify_replacement":
        contact_change.replacement_confirm(site, member, session=session, session_raw=raw, field=field,
                                           code=body.code, ip=ip)
        return {"state": None, "changed": True}
    else:
        raise ApiError("not_found", "Not found.", status=404)
    return {"state": _change_state(site, member, raw, field), "changed": False}


# -- the member edits their own saved delivery address (apps/members/services.py: save_own_address).
#    The member is the verified session of this host's site, never a field of the body. -------------

import json  # noqa: E402


class AddressIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    member_token: str = ""
    line: str = ""
    barangay: str = ""
    city: str = ""
    province: str = ""
    zip: str = ""
    landmark: str = ""


class AddressOut(Schema):
    home_address: dict
    home_address_source: str


@router.post("/address/", response=AddressOut)
def save_address(request, body: AddressIn):
    site = _site(body.host)
    member = _session(site, body.member_token).member
    posted = {key: getattr(body, key) for key in services.ADDRESS_KEYS}
    # An unknown key is refused by the service (never dropped): the schema drops extras, so read the raw body.
    try:
        raw = json.loads(request.body or b"{}")
    except ValueError:
        raw = {}
    posted.update({k: v for k, v in raw.items() if k not in ("host", "visitor_ip", "member_token")}
                  if isinstance(raw, dict) else {})
    address = services.save_own_address(site, member, posted, ip=_ip(body.visitor_ip))
    return {"home_address": address, "home_address_source": "profile"}


class AddressReadIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    member_token: str = ""


class AddressReadOut(Schema):
    home_address: dict
    home_address_locked: bool


@router.post("/address/read/", response=AddressReadOut)
def read_address(request, body: AddressReadIn):
    """The member's own address for the dashboard form: the one the checkout quote uses (saved
    address, else the latest Membership Package order's), plus whether a recovery hold locks edits."""
    from apps.shop.quote import home_address  # lazy: shop imports members

    site = _site(body.host)
    member = _session(site, body.member_token).member
    address = home_address(member)[0] or {}
    return {
        "home_address": {k: address[k] for k in services.ADDRESS_KEYS if k in address},
        "home_address_locked": holds.active_hold(member, "address_change") is not None,
    }


# -- the member's promotional text preference (PROMO-CONSENT, z8uq9mbreu; apps/members/promo.py).
#    Off (PROMO_SMS_CONSENT_ENABLED), both routes answer 404. A held store stays open, like the
#    other member writes. ---------------------------------------------------------------------------


class PreferencesReadIn(Schema):
    host: str = ""
    visitor_ip: str = ""
    member_token: str = ""


class PreferencesIn(PreferencesReadIn):
    promo_sms_ok: bool = False


class PreferencesOut(Schema):
    promo_sms_ok: bool
    promo_sms_at: str | None


def _preferences(member):
    return {"promo_sms_ok": member.promo_sms_ok,
            "promo_sms_at": member.promo_sms_at.isoformat() if member.promo_sms_at else None}


def _promo_member(body):
    if not promo.enabled():
        raise ApiError("not_found", "Not found.", status=404)
    return _session(_site(body.host), body.member_token).member


@router.post("/preferences/read/", response=PreferencesOut)
def read_preferences(request, body: PreferencesReadIn):
    return _preferences(_promo_member(body))


@router.post("/preferences/", response=PreferencesOut)
def save_preferences(request, body: PreferencesIn):
    member = _promo_member(body)
    promo.set_preference(member, body.promo_sms_ok)
    return _preferences(member)
