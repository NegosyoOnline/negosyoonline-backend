"""The member of a Membership Pack order that has none yet (hotfix z8uq9mgpt5, Coach Ed 2026-11-19).

A Xendit order is placed with no Member: it keeps the buyer's typed mobile and email and the used
onboarding challenge that proved the mobile, and the member is created here when the payment is
confirmed (apps.pack.xendit.apply_state). A checkout that never pays therefore holds no account and
no contact. Written so a later change can call `attach_member` for any order that has no member, for
example when a bank transfer is verified; nothing calls it for a bank order today.

`attach_member` runs inside the caller's transaction, under the Site row lock and the order row lock.
It never merges: a contact someone else holds in the meantime is returned as a refusal for the caller
to put the order under review."""
from django.db import transaction

from apps.common.errors import ApiError
from apps.members import services as members
from apps.members.models import MemberOtpChallenge
from apps.pack import checkout

KEPT = ("member", "buyer_mobile", "buyer_email", "onboarding_challenge_id")


def kept_challenge(order):
    """The used onboarding challenge this order kept for its mobile, if it is still unbound and for
    this number; None when it is gone (retention deletes expired ones) or was bound elsewhere."""
    if not order.onboarding_challenge_id:
        return None
    return MemberOtpChallenge.objects.select_for_update().filter(
        pk=order.onboarding_challenge_id, site_id=order.site_id, purpose="onboarding",
        target=order.buyer_mobile, used_at__isnull=False, member__isnull=True,
    ).first()


def _resolve(order):
    site, mobile, email = order.site, order.buyer_mobile, order.buyer_email
    proof = kept_challenge(order)
    returning = checkout.match_existing(site, mobile, email)  # raises contact_in_use
    if returning is not None:  # the same buyer, no benefits and no live order: only with the proved number
        if proof is None:
            raise ApiError("mobile_not_verified", members.PROOF_ERROR, fields={"mobile": [members.PROOF_ERROR]})
        members.claim_onboarding_proof(site, returning, mobile=mobile, proof=proof)
        return returning
    checkout.release_abandoned(site, mobile, email)
    return members.create_member(
        site, full_name=order.buyer_name, mobile=mobile, email=email,
        delivery_address=members.address_only(order.delivery_address), mobile_proof=proof,
    )


def attach_member(order, *, state_bump=True):
    """(member, None) with the member attached to the order and the kept contact cleared, or
    (None, refusal) with the order untouched. An order that already has a member returns it."""
    if order.member_id:
        return order.member, None
    try:
        with transaction.atomic():  # a refusal leaves no half-made member behind
            member = _resolve(order)
    except ApiError as exc:
        return None, exc
    order.member, order.buyer_mobile, order.buyer_email, order.onboarding_challenge_id = member, "", "", None
    if state_bump:
        order.state_version += 1
    order.save(update_fields=[*KEPT, "state_version", "updated_at"])
    return member, None


def refusal_detail(exc):
    """The order event's reason: the refusal's code and the first field it names."""
    fields = list(exc.fields or {})
    return {"reason": exc.code, "field": fields[0] if fields else ""}
