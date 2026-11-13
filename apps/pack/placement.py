"""Pack placement, TWCinc `place_pack_order` steps 1-12 without Xendit and without a billing
meter (port of negosyoonline_sante_pack/placement.py).

One transaction under the Site row lock, keyed by UniqueConstraint(site, idempotency_key): a
replay returns the first result and writes nothing. The order exists only with its first proof,
benefits are on from placement, status pending_review. Amounts are the server's constants; nothing
posted except the proof fields is read. The proof file is stored BEFORE the transaction and
discarded on any rollback."""
import hashlib
import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.billing import services as billing
from apps.common import private_files
from apps.common.errors import ApiError
from apps.members import contacts, services as members
from apps.members.models import MemberOtpChallenge
from apps.members.otp import binding_hash
from apps.meta_ads import services as meta_ads
from apps.payments import services as payments
from apps.pack import checkout, constants as C, notices, policies, portal, proofs, records, registration
from apps.pack.models import CheckoutToken, PackOrder
from apps.sites.models import Site

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Placed:
    order: PackOrder
    replay: bool


def idempotency_key(token, submit_key):
    submit_key = (submit_key or "").strip()
    if not 1 <= len(submit_key) <= 64:
        raise ApiError("no_submit_key", C.MSG_CHECKOUT_EXPIRED, status=409)
    return hashlib.sha256(f"{token.token_hash}:{submit_key}".encode()).hexdigest()


def _replay(site, key):
    return PackOrder.objects.filter(site=site, idempotency_key=key).first()


def _next_number(site):
    return "SMP%05d" % (PackOrder.objects.filter(site=site).count() + 1)


def _policy_version(site):
    """The store policy version shown at checkout, recorded only when a policy page is live."""
    return policies.accepted_version(site)


def _proof_challenge(site, token, raw):
    ch = None
    if token.onboarding_challenge_id and token.verified_mobile:
        ch = MemberOtpChallenge.objects.filter(
            pk=token.onboarding_challenge_id, site=site, purpose="onboarding",
            binding_hash=binding_hash(raw), target=token.verified_mobile, used_at__isnull=False,
        ).first()
    if ch is None:
        raise ApiError(
            "mobile_not_verified", C.MSG_VERIFY_FIRST, status=409,
            fields={"mobile": [C.MSG_VERIFY_FIRST]},
        )
    return ch


def _member(site, token, raw):
    contact, mobile = token.pending_contact, token.requested_mobile
    returning = checkout.match_existing(site, mobile, contact["email"])
    if returning is not None:  # placing signs the buyer in as this member, so the number must be proved first
        members.claim_onboarding_proof(site, returning, mobile=mobile, proof=_proof_challenge(site, token, raw))
        return returning
    checkout.release_abandoned(site, mobile, contact["email"])
    proof = _proof_challenge(site, token, raw) if token.verified_mobile else None
    return members.create_member(
        site, full_name=contact["full_name"], mobile=mobile, email=contact["email"],
        delivery_address=contact["delivery_address"], mobile_proof=proof,
    )


def place_pack_order(site, token, raw, *, submit_key, data=None, upload=None, ip=None, meta=None):
    key = idempotency_key(token, submit_key)
    first = _replay(site, key)
    if first is not None:
        return Placed(first, True)
    portal.require_sale_open()  # L45, before any file is stored
    if not token.pending_contact:
        raise ApiError("no_contact", C.MSG_NO_CONTACT, status=409)
    records.require_current(token)  # a held agreement only; before any file is stored, so a refused order leaves nothing behind
    records.require_enrollment(token)
    proof = proofs.clean(site, data or {}, upload)
    file_key = _store(site, proof)
    try:
        with transaction.atomic():
            locked = Site.objects.select_for_update().get(pk=site.pk)
            again = _replay(locked, key)
            if again is not None:
                transaction.on_commit(lambda: private_files.discard(file_key))
                return Placed(again, True)
            if locked.deleted_at or locked.operator_hold or not billing.position(locked).storefront_served:
                # The storefront's own refusal, asked again under the lock: a request that passed it
                # and then lost service (sweep, cancellation, due date, operator hold) creates nothing.
                raise ApiError("not_found", "Not found.", status=404)
            if not payments.any_method(locked) or not payments.active_accounts(locked).filter(
                pk=proof.account.pk
            ).exists():
                raise ApiError("method_unavailable", C.MSG_PAYMENT_UNAVAILABLE, status=409)
            tok = CheckoutToken.objects.select_for_update().get(pk=token.pk, site=locked)
            if tok.order_ref:
                raise ApiError("checkout_used", C.MSG_CHECKOUT_EXPIRED, status=409)
            version, accepted_at = records.require_current(tok, lock=True)  # the second check, under the locks
            enrolled_at = records.require_enrollment(tok)
            member = _member(locked, tok, raw)
            now = timezone.now()
            order = PackOrder.objects.create(
                site=locked, member=member, order_number=_next_number(locked),
                price_centavos=C.PRICE_CENTAVOS, buyer_name=tok.pending_contact["full_name"],
                delivery_address=tok.pending_contact["delivery_address"],
                payment_status=C.PENDING_REVIEW, benefits_active_at=now, idempotency_key=key,
                policy_version=_policy_version(locked),
            )
            order.add_event(
                C.EVENT_PLACED, new={"method": C.PAYMENT_METHOD}, actor_type="member",
                actor_ref=member.pk, actor_label=contacts.mask("mobile", member.mobile),
            )
            if version is not None:  # posted by an older storefront; otherwise accepted at the details step
                records.write_acceptance(order, version, accepted_at)
            registration.activate(order, now)  # T1
            records.write_enrollment(order, enrolled_at)
            tok.order_ref, tok.pending_contact = order.pk, {}  # cleared after placement
            tok.save(update_fields=["order_ref", "pending_contact"])
            submission = proofs.write(order, proof, file_key, seq=1, ip=ip)
            notices.after_commit(notices.order_received, order.pk)
            notices.after_commit(notices.review_needed, submission.pk)
            meta_ads.after_placement(order, meta, ip)  # Meta AddPaymentInfo, with consent only; never fails the order
    except BaseException:
        private_files.discard(file_key)
        raise
    return Placed(order, False)


def _store(site, proof):
    """The proof file, stored before the transaction. A store that cannot take it refuses the order."""
    try:
        return private_files.save(
            C.PROOF_FOLDER.format(site.pk), proof.content, proof.content_type
        )
    except private_files.StoreError:
        raise ApiError(
            "store_unavailable", "We could not save your proof. Please try again shortly.", status=503
        ) from None
