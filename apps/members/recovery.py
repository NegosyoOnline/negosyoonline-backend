"""Subscriber-assisted recovery. The Subscriber records the fixed checklist; only a passed, unused,
unexpired check of this member lets them save a replacement contact, which is stored pending
verification. Saving starts the 24-hour hold, revokes every session and notifies the previous
verified contacts, naming the hold end. The Subscriber routes are apps/members/api_owner.py."""
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.errors import ApiError
from apps.common.masking import mask_email
from apps.common.phones import normalise_ph_mobile

from . import contacts, holds, hooks, messaging, services as members, sessions
from .models import Member, MemberContactAudit, MemberRecoveryCheck

logger = logging.getLogger(__name__)

CHECK_TTL = timedelta(minutes=15)
ITEM_KEYS = ("full_name", "delivery_address", "pack_order_number", "payout_last4")
APPLICABLE = ("full_name", "delivery_address")  # the order slice adds pack_order_number, the payout slice payout_last4
ANSWERS = frozenset({"matched", "not_matched"})
PASS_RULES = {"all_applicable_matched":
              lambda items: all(v == "matched" for v in items.values() if v != "not_applicable_yet")}  # 
PASS_RULE = "all_applicable_matched"  # OQ-2; another ruling adds its rule to PASS_RULES and names it here
SAME = {"mobile": "This is already the member's mobile number.", "email": "This is already the member's email."}


_extra_items = {}  # key -> applicable_for(member) -> bool (pack_order_number, payout_last4)


_references = {}  # key -> reference(member), shown to the Subscriber for an applicable item; never compared


def register_checklist_item(key, applicable_for, reference=None):
    if key not in ITEM_KEYS or key in APPLICABLE:
        raise ValueError(f"unknown or always-applicable checklist item {key!r}")
    _extra_items[key] = applicable_for
    if reference is None:
        _references.pop(key, None)
    else:
        _references[key] = reference


def applicable_for(member):
    return tuple(APPLICABLE) + tuple(k for k in ITEM_KEYS if k in _extra_items and _extra_items[k](member))


def checklist_items(member=None):
    keys = applicable_for(member) if member is not None else APPLICABLE
    out = []
    for k in ITEM_KEYS:
        item = {"key": k, "applicable": k in keys}
        if member is not None and k in keys and k in _references:
            item["reference"] = _references[k](member)
        out.append(item)
    return out


def search(qs, q):
    q = (q or "").strip()
    try:
        return qs.filter(mobile=normalise_ph_mobile(q)).order_by("full_name", "pk")
    except ValueError:
        pass
    if "@" in q:
        return qs.filter(email=q.lower()).order_by("full_name", "pk")
    if len(q) >= 2:
        return qs.filter(full_name__icontains=q).order_by("full_name", "pk")
    return qs.none()


def is_closed(member):
    """A member whose account was disposed (retention) or who no longer holds their contacts."""
    from .retention import _placeholder_mobile

    return member.mobile == _placeholder_mobile(member) or hooks.contact_released(member)


def assert_open(member):
    if is_closed(member):
        raise ApiError("member_closed", "This member's account is closed.", status=409)


def record_checklist(account, site, member, submitted):
    applicable = applicable_for(member)
    if set(submitted) != set(applicable) or any(v not in ANSWERS for v in submitted.values()):
        raise ApiError("invalid_items", "Answer each checklist item that applies.", status=422)
    items = {k: (submitted[k] if k in applicable else "not_applicable_yet") for k in ITEM_KEYS}
    passed = PASS_RULES[PASS_RULE](items)  # the named constant decides
    now = timezone.now()
    return MemberRecoveryCheck.objects.create(site=site, member=member, actor_account_id=account.pk,
                                              actor_email_masked=mask_email(account.email), items=items,
                                              passed=passed, created_at=now, expires_at=now + CHECK_TTL)


_follow_ups = []  # (run(member), says(member)) of a later slice, for a member the recovery leaves with no code to type
NEXT_SIGN_IN = "The new contact must be verified at the member's next sign-in."


def register_follow_up(run, says):
    """run(member) happens after the commit and the notice; says(member) is the sentence the Subscriber reads in
    place of the sign-in one, or None for the default."""
    _follow_ups.append((run, says))


def saved_message(hold, member=None):
    sentences = [s for s in (says(member) for _, says in _follow_ups) if s] if member is not None else []
    return ("Saved. The member's sessions have ended. A 24-hour security hold runs until "
            f"{holds.display(hold.ends_at)}. {sentences[0] if sentences else NEXT_SIGN_IN}")


def recover_contact(account, site, member_id, *, check_id, new_mobile=None, new_email=None, ip=None):
    """(member, hold), or None when the member is not this site's."""
    wanted = {}
    if new_mobile:
        wanted["mobile"] = contacts.clean_mobile(new_mobile, "new_mobile")
    if new_email:
        wanted["email"] = contacts.clean_email(new_email, "new_email")
    if not wanted:
        raise ApiError("nothing_to_change", "Enter a new mobile number or email.", status=422)
    now = timezone.now()
    with transaction.atomic():
        m = Member.objects.select_for_update().filter(pk=member_id, site=site).first()
        if m is None:
            return None
        assert_open(m)
        check = MemberRecoveryCheck.objects.select_for_update().filter(pk=check_id, site=site, member=m).first()
        if check is None or not check.passed or check.used_at is not None or check.expires_at <= now:
            raise ApiError("recovery_check_invalid",
                           "Complete the recovery checklist again before changing a contact.", status=409)
        for field, value in wanted.items():
            if value == getattr(m, field):
                raise ApiError("same_contact", SAME[field], fields={f"new_{field}": [SAME[field]]})
            if members.contact_taken(site, field, value, exclude=m):
                raise members.contact_in_use(field, f"new_{field}")
            members.require_free_elsewhere(site, field, value, f"new_{field}")
        prior = members.verified_contacts(m)
        olds = {field: getattr(m, field) for field in wanted}
        check.used_at = now
        check.save(update_fields=["used_at"])
        update = ["updated_at"]
        for field, value in wanted.items():
            setattr(m, field, value)
            setattr(m, f"{field}_state", "pending")
            setattr(m, f"{field}_verified_at", None)
            update += [field, f"{field}_state", f"{field}_verified_at"]
        try:
            with transaction.atomic():
                m.save(update_fields=update)
        except IntegrityError as exc:
            raise members.in_use_error(exc, "new_{}") or exc
        audits = [MemberContactAudit.objects.create(
            site=site, member=m, kind="subscriber_recovery", field=field, old_masked=contacts.mask(field, olds[field]),
            new_masked=contacts.mask(field, wanted[field]), actor_type="subscriber", actor_ref=account.pk,
            actor_label=mask_email(account.email), recovery_check=check, ip_advisory=(ip or "")[:45])
            for field in wanted]
        hold = holds.start_recovery_hold(m, audits[0], now=now)
        sessions.revoke_sessions(m, "recovery", now=now)
    messaging.send_contact_notice(site, audits[0], kind="recovery", prior_verified=prior, hold_end=hold.ends_at)
    for run, _ in list(_follow_ups):
        try:
            run(m)
        except Exception:  # noqa: BLE001 - the recovery is committed; a later slice's follow-up never fails it
            logger.exception("member recovery follow-up failed")
    return m, hold
