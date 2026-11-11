"""Member sign-in. Every well-formed input gets the same answer, the same limits, a challenge row (a
decoy when there is no match) and the same padded timing (the API pads). Whether the SMS channel
may be used is decided from the typed contact and the counters before any member lookup.

The channel rule: an email matches only while it is verified or pending (an unverified email is no
channel); a mobile matches a member whose number is no longer an unconfirmed onboarding one, and
goes by SMS unless the email is verified and the mobile is not (then by email), or a template says
text first. The SMS branch is the hook the SMS slice fills (hooks.register_sms_transport).
"""
import time
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.members import contacts, hooks, messaging, otp, sessions
from apps.members.models import Member, MemberContactAudit

GENERIC = "If this matches a member of this store, we sent a code."
OFFER_EMAIL = "We can email your code instead."
MIN_SECONDS = 1.5
_clock, _sleep = time.monotonic, time.sleep


@dataclass(frozen=True)
class Answer:
    step: str
    message: str
    offer_email: bool = False


def clock():
    return _clock()


def pad(started):
    remaining = MIN_SECONDS - (_clock() - started)
    if remaining > 0:
        _sleep(remaining)


def _mobile_prechecks(site, mobile):
    messaging.require_transport()
    precheck = hooks.sms_transport().get("precheck")
    if precheck:
        precheck(site, mobile)


def _match(site, kind, value):
    """(member, channel, target, sms_category) or None. An unverified email is no channel."""
    if kind == "email":
        m = Member.objects.filter(site=site, email=value, email_state__in=("verified", "pending")).first()
        return (m, "email", m.email, None) if m else None
    m = Member.objects.filter(site=site, mobile=value).first()
    if m is None:
        return None
    if m.source == "onboarding" and m.mobile_state == "pending":
        return None  # a typed, unconfirmed number gets no code; the buyer's way back is the secure link in the after-payment email
    if m.email_state == "verified" and hooks.sign_in_sms_first(site):
        return (m, "sms", m.mobile, "otp_email_fallback") if m.mobile_state in ("verified", "pending") else None
    if m.email_state == "verified":  # only a verified matching contact reaches the email
        return (m, "email", m.email, None) if m.mobile_state == "verified" else None
    return m, "sms", m.mobile, "otp_no_fallback"


def _member_for(site, requested):
    """'Text me' and the accept-email step resolve the member afresh from the typed contact with
    the same rule, never from challenge.member (null when the target cap wrote a decoy)."""
    kind = "email" if "@" in requested else "mobile"
    match = _match(site, kind, requested)
    return match[0] if match else None


def _send_email_later(ch, code, defer):
    """A signed-out sign-in email is sent after the response work, so the request time never depends
    on the mail provider. The code lives only in this closure. Without a defer hook (service tests,
    callers outside a request) it is sent at once."""
    if defer is None:
        messaging.deliver_code(ch, code)
    else:
        defer(lambda: messaging.deliver_code(ch, code))


def _deliver_signed_out(ch, code, *, category, defer):
    if ch.channel == "email":
        _send_email_later(ch, code, defer)
        return "deferred"
    return messaging.deliver_code(ch, code, sms_category=category)


def request_code(site, raw, *, binding_raw, ip=None, defer=None):
    kind, value = contacts.parse_identifier(raw)
    otp.throttle_signed_out(site, ip)
    otp.check_send_allowed(site, purpose="signin", requested=value, binding_raw=binding_raw)
    if kind == "mobile":
        _mobile_prechecks(site, value)
    match = _match(site, kind, value)
    if match is None:
        otp.open_challenge(site, purpose="signin", channel="email" if kind == "email" else "sms", target=value,
                           requested=value, binding_raw=binding_raw, decoy=True)
        return Answer("code", GENERIC)
    member, channel, target, category = match
    ch, code = otp.open_challenge(site, purpose="signin", channel=channel, target=target, requested=value,
                                  binding_raw=binding_raw, member=member, reuse_code=True)
    if code is None:  # silent target bucket full: a decoy was written, same answer
        return Answer("code", GENERIC)
    outcome = _deliver_signed_out(ch, code, category=category or "otp_no_fallback", defer=defer)
    if outcome in ("paused", "refused_not_test_number"):  # a race past the pre-checks
        raise messaging.outcome_error(outcome)
    return Answer("code", GENERIC)  # a mail failure on this signed-out step answers the same


def resend(site, *, binding_raw, ip=None, defer=None):
    ch = otp.latest_recent(site, purpose="signin", binding_raw=binding_raw)
    if ch is None:
        raise otp.error("expired")
    return request_code(site, ch.requested_contact, binding_raw=binding_raw, ip=ip, defer=defer)


def text_instead(site, *, binding_raw, ip=None):
    """'Text me instead'. Uniform for every input: with the counter already full the answer is the
    pause for every input and nothing is sent. The email offer appears only from a real offer_email
    outcome. Needs the SMS channel."""
    ch = otp.latest_recent(site, purpose="signin", binding_raw=binding_raw)
    if ch is None:
        raise otp.error("expired")
    otp.throttle_signed_out(site, ip)
    messaging.require_transport()
    precheck = hooks.sms_transport().get("precheck")
    if precheck:
        precheck(site, ch.target)
    member = _member_for(site, ch.requested_contact)  # never ch.member
    if member is None or member.mobile_state not in ("verified", "pending"):
        otp.open_challenge(site, purpose="signin", channel="sms", target=ch.target, requested=ch.requested_contact,
                           binding_raw=binding_raw, decoy=True, resend_wait=False,
                           channel_wait=hooks.sign_in_sms_first(site))
        return Answer("code", GENERIC)
    category = "otp_email_fallback" if member.email_state == "verified" else "otp_no_fallback"
    new, code = otp.open_challenge(site, purpose="signin", channel="sms", target=member.mobile,
                                   requested=ch.requested_contact, binding_raw=binding_raw, member=member,
                                   resend_wait=False, channel_wait=hooks.sign_in_sms_first(site), reuse_code=True)
    if code is None:
        return Answer("code", GENERIC)
    outcome = messaging.deliver_code(new, code, sms_category=category)
    if outcome == "offer_email":
        return Answer("code", OFFER_EMAIL, offer_email=True)
    if outcome == "paused":
        raise messaging.outcome_error("paused")
    return Answer("code", GENERIC)  # refused_not_test_number answers like a decoy


def accept_email(site, *, binding_raw, ip=None, defer=None):
    """The email offer accepted: a fresh email challenge to the verified email (a decoy when there is none)."""
    ch = otp.latest_recent(site, purpose="signin", binding_raw=binding_raw)
    if ch is None:
        raise otp.error("expired")
    otp.throttle_signed_out(site, ip)
    member = _member_for(site, ch.requested_contact)  # never ch.member
    if member is not None and member.email_state == "verified":
        new, code = otp.open_challenge(site, purpose="signin", channel="email", target=member.email,
                                       requested=ch.requested_contact, binding_raw=binding_raw, member=member,
                                       resend_wait=False, channel_wait=hooks.sign_in_sms_first(site), reuse_code=True)
        if code is not None:
            _send_email_later(new, code, defer)  # a mail failure answers the neutral line
    else:
        otp.open_challenge(site, purpose="signin", channel="email", target=ch.target, requested=ch.requested_contact,
                           binding_raw=binding_raw, decoy=True, resend_wait=False,
                           channel_wait=hooks.sign_in_sms_first(site))
    return Answer("code", GENERIC)


def verify(site, *, binding_raw, code, ip=None):
    """(member, raw session token), or ApiError. A pending target's first code verifies it. The
    session row is created inside the member-locked transaction; the storefronts server only puts
    the token in its cookie. Never call it inside an atomic block: a wrong guess must stay counted."""
    otp.throttle_verify(ip)
    ch = otp.verify_challenge(site, purpose="signin", binding_raw=binding_raw, code=code)
    field = "email" if ch.channel == "email" else "mobile"
    now = timezone.now()
    with transaction.atomic():
        m = Member.objects.select_for_update().filter(pk=ch.member_id, site=site).first()
        if m is None or getattr(m, field) != ch.target:
            raise otp.error("expired")  # the contact changed after the code was sent
        state = getattr(m, f"{field}_state")
        if state == "pending":
            setattr(m, f"{field}_state", "verified")
            setattr(m, f"{field}_verified_at", now)
            m.save(update_fields=[f"{field}_state", f"{field}_verified_at", "updated_at"])
            MemberContactAudit.objects.create(site=site, member=m, kind="first_verification", field=field,
                                              new_masked=contacts.mask(field, ch.target), actor_type="member",
                                              actor_ref=m.pk, ip_advisory=(ip or "")[:45])
        elif state != "verified":
            raise otp.error("expired")
        _, raw = sessions.create_session(site, m, ip=ip, now=now)
    return m, raw
