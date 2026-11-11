"""Creating a member and the contact rules. Other apps call these functions, never the tables.

Uniqueness is per site, protected by the site row lock, a pre-check and the unique constraints. A
contact held on another live site is refused too, by pre-check only, so one person never reaches
Santé registration twice. A member never marks a contact verified by typing it; neither does a
Subscriber. There is no public sign-up: the order slice creates the member.
"""
from django.conf import settings
from django.db import IntegrityError, transaction

from apps.common.errors import ApiError
from apps.sites.models import Site

from . import contacts, hooks
from .models import (
    ONBOARDING, PENDING, UNVERIFIED, VERIFIED, Member, MemberContactAudit, MemberOtpChallenge,
)

ADDRESS_KEYS = ("line", "barangay", "city", "province", "zip", "landmark")  # landmark is kept apart from the street line
ADDRESS_MAX = 200
IN_USE = {"mobile": "This mobile number is already registered.", "email": "This email is already registered."}
ADDRESS_ERROR = "Enter the delivery address."
NAME_ERROR = "Enter the member's full name."
PROOF_ERROR = "Verify the mobile number first."


def enabled():
    return settings.MEMBERS_ENABLED


def _invalid(field, message, code="invalid_request"):
    return ApiError(code, "Some values are missing or invalid.", fields={field: [message]})


def contact_in_use(field, key=None):
    return ApiError("contact_in_use", IN_USE[field], status=409, fields={key or field: [IN_USE[field]]})


def contact_taken(site, field, value, *, exclude=None):
    rows = Member.objects.filter(site=site, **{field: value})
    if exclude is not None:
        rows = rows.exclude(pk=exclude.pk)
    return rows.exists()


def require_free_elsewhere(site, field, value, key=None):
    """Raises when a member of another live site holds this contact. A member whose contacts were
    released never counts, and the refusal never names the site."""
    rows = Member.objects.filter(site__deleted_at__isnull=True, **{field: value}).exclude(site=site)
    if any(not hooks.contact_released(member) for member in rows):
        raise contact_in_use(field, key)


def address_only(data):
    """The address keys of a stored delivery_address; the saved receiver rides beside them."""
    return {k: v for k, v in (data or {}).items() if k in ADDRESS_KEYS}


def clean_address(data):
    if not isinstance(data, dict) or set(data) - set(ADDRESS_KEYS):
        raise _invalid("delivery_address", ADDRESS_ERROR, "invalid_address")
    out = {k: str(data.get(k) or "").strip() for k in ADDRESS_KEYS}
    if any(len(v) > ADDRESS_MAX for v in out.values()):  # refused, never cut short
        raise _invalid("delivery_address", ADDRESS_ERROR, "invalid_address")
    if not (out["line"] and out["city"] and out["province"]):
        raise _invalid("delivery_address", ADDRESS_ERROR, "invalid_address")
    return out


def in_use_error(exc, key="{}"):
    """The in-use refusal for the two named unique violations only (PostgreSQL names the constraint,
    SQLite the column); None for any other IntegrityError, which the caller must re-raise."""
    text = str(exc)
    if "members_member_site_mobile" in text or "members_member.mobile" in text:
        field = "mobile"
    elif "members_member_site_email" in text or "members_member.email" in text:
        field = "email"
    else:
        return None
    return contact_in_use(field, key.format(field))


def _consume_onboarding_proof(site, mobile, proof):
    if proof is None:
        raise ApiError("mobile_proof_required", PROOF_ERROR, fields={"mobile": [PROOF_ERROR]})
    row = (
        MemberOtpChallenge.objects.select_for_update()
        .filter(pk=proof.pk, site=site, purpose="onboarding", target=mobile, used_at__isnull=False,
                member__isnull=True)
        .first()
    )
    if row is None:
        raise ApiError("mobile_not_verified", PROOF_ERROR, fields={"mobile": [PROOF_ERROR]})
    return row


def create_member(site, *, full_name, mobile, email, delivery_address=None, source=ONBOARDING,
                  mobile_proof=None, email_state=PENDING):
    """A new member of this site, in one transaction under the site row lock. With `mobile_proof`
    (the used onboarding challenge for this number) the mobile starts verified, otherwise pending.
    The email starts pending: the first code sent to it proves it. The delivery address is optional
    for an onboarding member. Raises ApiError: invalid_request for a bad value, contact_in_use (409)
    when the mobile or the email is already a member here, or of another live site, not_found when
    the site is gone."""
    name = (full_name or "").strip()
    if not 1 <= len(name) <= 120:
        raise _invalid("full_name", NAME_ERROR)
    mobile, email = contacts.clean_mobile(mobile), contacts.clean_email(email)
    if source != ONBOARDING:
        raise ValueError(f"unknown member source {source!r}")
    if email_state not in (PENDING, UNVERIFIED):
        raise ValueError(f"a new member's email cannot start {email_state!r}")
    address = clean_address(delivery_address) if delivery_address else {}
    with transaction.atomic():
        locked = Site.objects.select_for_update().filter(pk=site.pk, deleted_at__isnull=True).first()
        if locked is None:
            raise ApiError("not_found", "Not found.", status=404)
        proof = _consume_onboarding_proof(locked, mobile, mobile_proof) if mobile_proof is not None else None
        states = {"email_state": email_state}
        if proof is not None:
            states.update(mobile_state=VERIFIED, mobile_verified_at=proof.used_at)
        for field, value in (("mobile", mobile), ("email", email)):
            if contact_taken(locked, field, value):
                raise contact_in_use(field)
            require_free_elsewhere(locked, field, value)
        try:
            with transaction.atomic():
                member = Member.objects.create(
                    site=locked, full_name=name, mobile=mobile, email=email,
                    delivery_address=address, source=source, **states,
                )
        except IntegrityError as exc:
            raise in_use_error(exc) or exc
        if proof is not None:
            MemberOtpChallenge.objects.filter(pk=proof.pk).update(member=member)
    return member


def confirm_pending_mobile(site, member, challenge, *, ip=None):
    """A buyer's post-payment code. The number on file changes only here, once the code proves the
    new one (a buyer may fix a typo), so an unproven number never replaces anything. Refuses a
    number another member now holds."""
    with transaction.atomic():
        m = Member.objects.select_for_update().get(pk=member.pk, site=site)
        if m.mobile_state != PENDING:
            return m
        old = m.mobile
        m.mobile, m.mobile_state, m.mobile_verified_at = challenge.target, VERIFIED, challenge.used_at
        try:
            with transaction.atomic():
                m.save(update_fields=["mobile", "mobile_state", "mobile_verified_at", "updated_at"])
        except IntegrityError as exc:
            raise in_use_error(exc) or exc
        same = old == m.mobile
        MemberContactAudit.objects.create(
            site=site, member=m, kind="first_verification" if same else "self_service", field="mobile",
            old_masked="" if same else contacts.mask("mobile", old),
            new_masked=contacts.mask("mobile", m.mobile), actor_type="member", actor_ref=m.pk,
            ip_advisory=(ip or "")[:45],
        )
    return m


def contacts_released(member):
    """True for a member whose contacts release_contacts gave up: the tombstone email names the
    released domain."""
    return (member.email or "").endswith("@released.invalid")


def release_contacts(site, member):
    """A checkout that ended unpaid holds no contact. The row and its orders stay, for the record,
    under contacts nobody can type, so the real owner of a mistyped number or email can buy."""
    from . import sessions
    with transaction.atomic():
        m = Member.objects.select_for_update().get(pk=member.pk, site=site)
        old = {"mobile": m.mobile, "email": m.email}
        m.mobile, m.email = f"+63900{m.pk % 10_000_000:07d}", f"released-{m.pk}@released.invalid"
        m.save(update_fields=["mobile", "email", "updated_at"])
        sessions.revoke_sessions(m, "contact_change")
        for field, value in old.items():
            MemberContactAudit.objects.create(
                site=site, member=m, kind="self_service", field=field,
                old_masked=contacts.mask(field, value),
                new_masked=contacts.mask(field, getattr(m, field)),
                actor_type="system", actor_label="Released: checkout ended unpaid",
            )
    return m


def verified_contacts(member):
    out = []
    if member.mobile_state == VERIFIED:
        out.append(("mobile", member.mobile))
    if member.email_state == VERIFIED:
        out.append(("email", member.email))
    return out


def default_channel(member):
    """Email once verified, else mobile."""
    return "email" if member.email_state == VERIFIED else "mobile"


def money_otp_targets(member):
    """For payouts: only verified contacts, default first. Pending and unverified never appear."""
    out = []
    if member.email_state == VERIFIED:
        out.append(("email", member.email))
    if member.mobile_state == VERIFIED:
        out.append(("mobile", member.mobile))
    return out


def claim_onboarding_proof(site, member, *, mobile, proof):
    """The returning buyer's new used onboarding challenge is consumed for the existing member under
    the same row lock and filter as create_member, so one challenge never backs two members or two
    orders. Call inside the caller's transaction."""
    mobile = contacts.clean_mobile(mobile)
    if mobile != member.mobile or member.site_id != site.pk:
        raise ApiError("mobile_not_verified", PROOF_ERROR, fields={"mobile": [PROOF_ERROR]})
    row = _consume_onboarding_proof(site, mobile, proof)
    MemberOtpChallenge.objects.filter(pk=row.pk, member__isnull=True).update(member=member)
    return row
