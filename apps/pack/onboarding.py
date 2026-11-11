"""Pack onboarding after payment, and the code step of a returning buyer's checkout (port of
TWCinc's negosyoonline_sante_pack/onboarding.py, the code and session functions). The codes are
the member challenge rows of apps.members.otp: bound to the raw checkout token while the buyer is
a guest, and to the member after placement. Never call a verify function inside an atomic block: a
wrong guess must stay counted."""
from django.db import transaction
from django.utils import timezone

from apps.common import throttle
from apps.common.errors import ApiError
from apps.members import contacts, messaging, otp, services as members, sessions
from apps.members.models import Member, MemberContactAudit
from apps.pack import access, checkout, constants as C
from apps.pack.models import CheckoutToken


# -- the checkout's own code step (a returning buyer proves the number before payment) ----------

def needs_code(site, token):
    """A first-time buyer pays first and confirms the mobile on the address step. Only a number
    already on a member still proves itself before payment, because placing the order signs the
    buyer in as that member. A contact that stopped being usable stays on the code step;
    placement gives the refusal."""
    contact = token.pending_contact or {}
    try:
        return checkout.match_existing(site, contact.get("mobile"), contact.get("email")) is not None
    except ApiError:
        return True


def send_code(site, token, raw, *, ip=None):
    """queued | held. Pause, stub and unlisted-number outcomes raise; nothing proceeds unverified."""
    if not token.requested_mobile:
        raise ApiError("no_contact", C.MSG_NO_CONTACT, status=409)
    if ip:
        scope, limit, window = C.SEND_IP_LIMIT
        throttle.hit(scope, ip, limit=limit, window=window)
    scope, limit, window = C.TOKEN_SEND_LIMIT
    throttle.hit(scope, token.pk, limit=limit, window=window)
    messaging.require_transport()
    _, outcome = otp.start_challenge(
        site, purpose="onboarding", channel="sms", target=token.requested_mobile, binding_raw=raw,
        sms_category="otp_no_fallback", reuse_code=True,
    )
    err = messaging.outcome_error(outcome, field="mobile")
    if err is not None:
        raise err
    return outcome


def verify_code(site, token, raw, code, *, ip=None):
    otp.throttle_verify(ip)
    ch = otp.verify_challenge(site, purpose="onboarding", binding_raw=raw, code=code)
    if ch.target != token.requested_mobile:
        raise otp.error("expired")
    CheckoutToken.objects.filter(pk=token.pk).update(onboarding_challenge_id=ch.pk, verified_mobile=ch.target)
    return ch


# -- the member's onboarding steps (after the order is placed) -----------------------------------

def member_binding(member):
    return f"pack-member-mobile:{member.pk}"


def email_binding(member):
    return f"pack-member-email:{member.pk}"


def send_member_code(site, member, mobile, *, ip=None):
    """The address step: a code to the number the buyer confirms there, which may fix a typo made
    at checkout."""
    if member.mobile_state != "pending":
        return None
    otp.throttle_signed_in(member, ip)
    target = member.mobile if access.used(member) else contacts.clean_mobile(mobile)
    if members.contact_taken(site, "mobile", target, exclude=member):
        raise members.contact_in_use("mobile")
    members.require_free_elsewhere(site, "mobile", target)
    messaging.require_transport()
    _, outcome = otp.start_challenge(
        site, purpose="onboarding", channel="sms", target=target, binding_raw=member_binding(member),
        member=member, reuse_code=True,
    )
    err = messaging.outcome_error(outcome, field="mobile")
    if err is not None:
        raise err
    return outcome


def usable_challenge(member, ch):
    """`ch` unless verify_member_code would refuse its code: a recovery or a contact change came
    after it, or a spent link pinned the number and the code went to another. The page then offers
    Send Code, not code boxes for a code nobody sent."""
    if ch is None or access.contacts_replaced_since(member, ch.created_at):
        return None
    return None if access.used(member) and ch.target != member.mobile else ch


def verify_member_code(site, member, code, *, ip=None, token=None):
    """The funnel passes its checkout token: a recovery since it began has set the number to
    verify, and the buyer's typo-fix path must not replace it. A code sent before a recovery or a
    contact change is dead: it would put the old number back."""
    otp.throttle_verify(ip)
    ch = otp.verify_challenge(site, purpose="onboarding", binding_raw=member_binding(member), code=code)
    with transaction.atomic():
        m = Member.objects.select_for_update().get(pk=member.pk, site=site)
        if token is not None and access.contacts_replaced_since(m, token.created_at):
            raise ApiError("checkout_stale", C.MSG_CHECKOUT_EXPIRED, status=409)
        if access.contacts_replaced_since(m, ch.created_at):
            raise otp.error("expired")
        if access.used(m) and ch.target != m.mobile:  # a send that landed just after the recovery
            raise otp.error("expired")
        return members.confirm_pending_mobile(site, member, ch, ip=ip)


def send_member_email_code(site, member, *, ip=None):
    """Onboarding confirms the email too, with a code mailed to the address given at checkout."""
    if member.email_state == "verified":
        return None
    otp.throttle_signed_in(member, ip)
    _, outcome = otp.start_challenge(
        site, purpose="onboarding", channel="email", target=member.email,
        binding_raw=email_binding(member), member=member, reuse_code=True,
    )
    err = messaging.outcome_error(outcome, field="email")
    if err is not None:
        raise err
    return outcome


def confirm_member_email(site, member, *, ip=None, actor_label="", target=None):
    """The email is proven (by its code, or by the emailed order-access link that only the inbox
    owner can open). A `target` is the address that was proven: if the email on file changed
    since, nothing is verified."""
    with transaction.atomic():
        m = Member.objects.select_for_update().get(pk=member.pk, site=site)
        if m.email_state != "verified" and (target is None or m.email == target):
            now = timezone.now()
            m.email_state, m.email_verified_at = "verified", now
            m.save(update_fields=["email_state", "email_verified_at", "updated_at"])
            MemberContactAudit.objects.create(
                site=site, member=m, kind="first_verification", field="email",
                new_masked=contacts.mask("email", m.email), actor_type="member", actor_ref=m.pk,
                actor_label=actor_label, ip_advisory=(ip or "")[:45],
            )
    return m


def verify_member_email_code(site, member, code, *, ip=None):
    otp.throttle_verify(ip)
    ch = otp.verify_challenge(site, purpose="onboarding", binding_raw=email_binding(member), code=code)
    if ch.target != member.email:
        raise otp.error("expired")
    return confirm_member_email(site, member, ip=ip, target=ch.target)  # compared on the locked row


def issue_completed_session(site, member, token, *, ip=None):
    """(outcome, raw session token). The outcome is "issued", or why none: "open" (a contact is not
    verified), "stale" (a recovery or a contact change came after this checkout token began, so
    its cookie says nothing about who holds the member now) or "spent" (this token already issued
    one). The session is created inside the member-locked transaction with the contacts re-read
    there, so a racing recovery either refuses this session or revokes it. The token's once-only
    marker is set in the same transaction."""
    with transaction.atomic():
        m = Member.objects.select_for_update().filter(pk=member.pk, site=site).first()
        if m is None or m.mobile_state != "verified" or m.email_state != "verified":
            return "open", ""
        if access.contacts_replaced_since(m, token.created_at):
            return "stale", ""
        if not CheckoutToken.objects.filter(pk=token.pk, session_issued_at__isnull=True).update(
            session_issued_at=timezone.now()
        ):
            return "spent", ""
        _, raw = sessions.issue_session(site, m, ip=ip or "")
    return "issued", raw
