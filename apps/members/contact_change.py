"""Email verification and self-service contact change. Both the current contact and the replacement
are proven by code, bound to the member session (the raw session token is the binding). A change
revokes every session of the member, the current one included, notifies every previously verified
contact and starts no hold. Service logic only: the signed-in routes are a later slice."""
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.errors import ApiError

from . import contacts, messaging, otp, sessions
from . import services as members
from .models import Member, MemberContactAudit, MemberOtpChallenge

CURRENT_PROOF_WINDOW = timedelta(minutes=10)
EMAIL_VERIFIED = "Your email is verified."
SAME = {"mobile": "This is already your mobile number.", "email": "This is already your email."}
CURRENT_FIRST = "Confirm your current contact first."
NO_VERIFIED = "You have no verified contact to confirm with yet."


def _signed_out():
    return ApiError("signed_out", "Please sign in again.", status=401)


def _raise_for(outcome, field="code"):
    err = messaging.outcome_error(outcome, field=field)
    if err is not None:
        raise err


def _current_target(member, field):
    """The current-contact proof goes only to a verified contact, never to a pending one. A
    mobile change with no verified mobile is proven by the verified email, so a pending mobile has a way out."""
    if field == "email" and member.email_state == "verified":
        return "email", member.email
    if member.mobile_state == "verified":
        return "sms", member.mobile
    if member.email_state == "verified":
        return "email", member.email
    raise ApiError("no_verified_contact", NO_VERIFIED)


def _recent_current_proof(site, member, session_raw, field, now):
    return (MemberOtpChallenge.objects.filter(
        site=site, member=member, purpose="current_proof", replacement_for=field,
        binding_hash=otp.binding_hash(session_raw), used_at__gte=now - CURRENT_PROOF_WINDOW)
        .order_by("-used_at").first())


def change_state(site, member, *, session_raw, field, now=None):
    now = now or timezone.now()
    if _recent_current_proof(site, member, session_raw, field, now) is not None:
        rep = otp.latest_open(site, purpose="replacement_proof", binding_raw=session_raw, now=now)
        return "replacement_code" if rep is not None and rep.replacement_for == field else "replacement"
    cur = otp.latest_open(site, purpose="current_proof", binding_raw=session_raw, now=now)
    return "current_code" if cur is not None and cur.replacement_for == field else "current"


def _send(site, member, *, purpose, channel, target, session_raw, field="", ip=None, throttled=False):
    if channel == "sms":
        messaging.require_transport()
    if not throttled:
        otp.throttle_signed_in(member, ip)
    ch, code = otp.open_challenge(site, purpose=purpose, channel=channel, target=target, binding_raw=session_raw,
                                  member=member, replacement_for=field)
    if code is None:  # signed-in: requested == target, so the visible bucket answered first; defensive only
        raise otp.rate_limited()
    _raise_for(messaging.deliver_code(ch, code, sms_category="otp_no_fallback"))


def email_verification_start(site, member, *, session_raw, ip=None):
    if member.email_state == "verified":
        raise ApiError("already_verified", EMAIL_VERIFIED)
    _send(site, member, purpose="email_verify", channel="email", target=member.email, session_raw=session_raw, ip=ip)


def email_verification_confirm(site, member, *, session, session_raw, code, ip=None):
    otp.throttle_verify(ip)
    ch = otp.verify_challenge(site, purpose="email_verify", binding_raw=session_raw, code=code)
    now = timezone.now()
    with transaction.atomic():
        m = Member.objects.select_for_update().get(pk=member.pk, site=site)
        if not sessions.recheck(session.pk, m.pk, now=now):
            raise _signed_out()
        if m.email != ch.target:
            raise otp.error("expired")
        if m.email_state != "verified":
            m.email_state, m.email_verified_at = "verified", now
            m.save(update_fields=["email_state", "email_verified_at", "updated_at"])
            MemberContactAudit.objects.create(site=site, member=m, kind="first_verification", field="email",
                                              new_masked=contacts.mask("email", m.email), actor_type="member",
                                              actor_ref=m.pk, ip_advisory=(ip or "")[:45])
    return m


def current_proof_start(site, member, *, session_raw, field, ip=None):
    channel, target = _current_target(member, field)
    _send(site, member, purpose="current_proof", channel=channel, target=target, session_raw=session_raw,
          field=field, ip=ip)


def current_proof_confirm(site, member, *, session_raw, field, code, ip=None):
    otp.throttle_verify(ip)
    ch = otp.verify_challenge(site, purpose="current_proof", binding_raw=session_raw, code=code)
    if ch.replacement_for != field or ch.member_id != member.pk:
        raise otp.error("expired")
    return ch


def replacement_start(site, member, *, session_raw, field, raw_value, ip=None):
    if _recent_current_proof(site, member, session_raw, field, timezone.now()) is None:
        raise ApiError("current_proof_required", CURRENT_FIRST)
    clean = contacts.clean_mobile if field == "mobile" else contacts.clean_email
    value = clean(raw_value, "new_value")
    if value == getattr(member, field):
        raise ApiError("same_contact", SAME[field], fields={"new_value": [SAME[field]]})
    otp.throttle_signed_in(member, ip)  # the in-use probe spends the member budget like a send
    if members.contact_taken(site, field, value, exclude=member):
        raise members.contact_in_use(field, "new_value")
    members.require_free_elsewhere(site, field, value, "new_value")
    _send(site, member, purpose="replacement_proof", channel="sms" if field == "mobile" else "email", target=value,
          session_raw=session_raw, field=field, ip=ip, throttled=True)


def replacement_confirm(site, member, *, session, session_raw, field, code, ip=None):
    otp.throttle_verify(ip)
    ch = otp.verify_challenge(site, purpose="replacement_proof", binding_raw=session_raw, code=code)
    now = timezone.now()
    if ch.replacement_for != field or _recent_current_proof(site, member, session_raw, field, now) is None:
        raise ApiError("current_proof_required", CURRENT_FIRST)
    with transaction.atomic():
        m = Member.objects.select_for_update().get(pk=member.pk, site=site)
        if not sessions.recheck(session.pk, m.pk, now=now):
            raise _signed_out()
        prior = members.verified_contacts(m)
        old = getattr(m, field)
        setattr(m, field, ch.target)
        setattr(m, f"{field}_state", "verified")
        setattr(m, f"{field}_verified_at", now)
        try:
            with transaction.atomic():
                m.save(update_fields=[field, f"{field}_state", f"{field}_verified_at", "updated_at"])
        except IntegrityError as exc:
            raise members.in_use_error(exc, "new_value") or exc
        audit = MemberContactAudit.objects.create(site=site, member=m, kind="self_service", field=field,
                                                  old_masked=contacts.mask(field, old),
                                                  new_masked=contacts.mask(field, ch.target), actor_type="member",
                                                  actor_ref=m.pk, ip_advisory=(ip or "")[:45])
        sessions.revoke_sessions(m, "contact_change", now=now)
    messaging.send_contact_notice(site, audit, kind="change", field=field, prior_verified=prior)
    return m
