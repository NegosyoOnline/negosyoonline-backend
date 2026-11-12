"""Direct-proof review (port of TWCinc's negosyoonline_sante_pack/review.py). Approve and reject
lock the order row (then the decision), check state_version, and write an append-only decision.
Only approve clears the lock; reject sets it; resubmission keeps it. There is no timer and no
other writer. The Subscriber who owns the site reviews, never staff."""
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.common import private_files
from apps.common.errors import ApiError
from apps.meta_ads import services as meta_ads
from apps.pack import constants as C, notices, proofs, registration
from apps.pack.models import PackOrder, PackProofDecision


def subscriber_actor(account):
    return ("subscriber", account.pk, account.email)


def _lock(site, order_id, state_version):
    order = PackOrder.objects.select_for_update().filter(site=site, pk=order_id).first()
    if order is None:
        raise ApiError("not_found", "Not found.", status=404)
    if state_version != order.state_version:
        raise ApiError("stale", C.MSG_ORDER_STALE, status=409)
    return order


def _wrong_status():
    return ApiError("wrong_status", C.MSG_PAYMENT_WRONG_STATUS, status=409)


def _latest(order):
    return order.submissions.order_by("-seq").first()


def _save(order, fields):
    order.state_version += 1
    order.save(update_fields=[*fields, "state_version", "updated_at"])


def approve(account, site, order_id, *, state_version):
    kind, ref, label = subscriber_actor(account)
    with transaction.atomic():
        order = _lock(site, order_id, state_version)
        sub = _latest(order)
        if (
            order.payment_status not in (C.PENDING_REVIEW, C.REJECTED) or sub is None
            or sub.decisions.filter(decision="approved").exists()
        ):
            raise _wrong_status()
        decision = PackProofDecision.objects.create(
            site_id=order.site_id, order=order, submission=sub, decision="approved",
            actor_type=kind, actor_ref=ref, actor_label=label,
        )
        order.payment_status, order.verified_at = C.VERIFIED, timezone.now()
        order.rejection_lock_active = False
        _save(order, ["payment_status", "verified_at", "rejection_lock_active"])
        registration.on_payment_verified(order)  # T8
        meta_ads.queue_purchase(order)  # Meta Purchase after commit; does nothing while Meta events are off
        notices.after_commit(notices.payment_verified, decision.pk)
    return order


def clean_reason(reason_code, other_text):
    if reason_code not in C.REASON_LABELS:
        raise ApiError(
            "invalid_reason", C.MSG_CHOOSE_REASON, status=422, fields={"reason_code": [C.MSG_CHOOSE_REASON]}
        )
    text = (other_text or "").strip() if reason_code == "other" else ""
    if reason_code == "other" and not 1 <= len(text) <= 500:
        raise ApiError(
            "reason_text_required", C.EXPLAIN_REASON, status=422, fields={"other_text": [C.EXPLAIN_REASON]}
        )
    return text


def reject(account, site, order_id, *, state_version, reason_code, other_text=""):
    text = clean_reason(reason_code, other_text)
    kind, ref, label = subscriber_actor(account)
    with transaction.atomic():
        order = _lock(site, order_id, state_version)
        sub = _latest(order)
        if order.payment_status != C.PENDING_REVIEW or sub is None or sub.decisions.exists():
            raise _wrong_status()
        decision = PackProofDecision.objects.create(
            site_id=order.site_id, order=order, submission=sub, decision="rejected",
            reason_code=reason_code, other_text=text, actor_type=kind, actor_ref=ref, actor_label=label,
        )
        order.payment_status = C.REJECTED
        order.rejection_lock_active = True
        _save(order, ["payment_status", "rejection_lock_active"])
        notices.after_commit(notices.proof_rejected, decision.pk)
    return order


def resubmit(site, member, *, data, upload, ip=None):
    """A rejected member sends a new proof: only from `rejected`, seq + 1, and the lock stays
    until the Subscriber approves."""
    proof = proofs.clean(site, data or {}, upload)
    file_key = private_files.save(C.PROOF_FOLDER.format(site.pk), proof.content, proof.content_type)
    try:
        with transaction.atomic():
            order = (
                PackOrder.objects.select_for_update()
                .filter(site=site, member=member, payment_status__in=C.LIVE_STATUSES).first()
            )
            if order is None or order.payment_status != C.REJECTED:
                raise ApiError("not_rejected", C.MSG_NOT_REJECTED, status=409)
            seq = (order.submissions.aggregate(n=Max("seq"))["n"] or 0) + 1
            sub = proofs.write(order, proof, file_key, seq=seq, ip=ip)
            order.payment_status = C.PENDING_REVIEW  # the lock stays
            _save(order, ["payment_status"])
            notices.after_commit(notices.review_needed, sub.pk)
    except BaseException:
        private_files.discard(file_key)
        raise
    return order


def member_reason(order):
    """(label, other text) of the latest rejection, shown only to the member after sign-in."""
    dec = order.decisions.filter(decision="rejected").order_by("-decided_at", "-pk").first()
    if dec is None:
        return "", ""
    return C.REASON_LABELS[dec.reason_code], dec.other_text if dec.reason_code == "other" else ""
