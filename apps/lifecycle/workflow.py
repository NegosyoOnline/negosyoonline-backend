"""The workflow of the site deletion executor: propose, approve, cancel, hold, run
(plan z8uq9mb22v, sections 5 and 6.2 to 6.5).

Every entry point needs SITE_ERASURE_ENABLED and answers 404 while it is off, before any
lifecycle table is read. A proposal freezes a list of sites; a person approves that list by typing
`erase batch <id> <n> sites`; the run (a command or the Celery task) erases what was approved and
nothing else, item by item, each in its own transaction. Nothing here sends an email or writes a
personal value: ids, dates, counts and codes only.
"""
import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.common import private_files
from apps.common.errors import ApiError
from apps.lifecycle import dry_run, eligibility, erase
from apps.lifecycle.models import ErasureBatch, ErasureHold, ErasureItem
from apps.operators import audit
from apps.sites.models import Site

logger = logging.getLogger(__name__)

BATCH_MIN, BATCH_MAX = 1, 25
PROPOSAL_LIFETIME = timedelta(days=7)
MAX_ATTEMPTS = 5
REASON_MAX = 300
TARGET = "erasure_batch"

BatchStatus, ItemStatus = ErasureBatch.Status, ErasureItem.Status
# Batches whose pending and failed items a new proposal must not list again.
OPEN_BATCHES = (
    BatchStatus.PROPOSED, BatchStatus.APPROVED, BatchStatus.RUNNING, BatchStatus.NEEDS_ATTENTION,
)
RUNNABLE = (BatchStatus.APPROVED, BatchStatus.RUNNING)
STORE_UNAVAILABLE, ERROR = "store_unavailable", "error"


def require_on():
    """The whole workflow is closed while SITE_ERASURE_ENABLED is off."""
    if not settings.SITE_ERASURE_ENABLED:
        raise ApiError("not_found", "Not found.", status=404)


def _invalid(field, message):
    return ApiError(
        "invalid_request", "Some values are missing or invalid.", fields={field: [message]}
    )


def _text(value, field, *, required, limit):
    """A note or a reference: plain text with no address in it (the `--request-ref` check)."""
    value = value.strip() if isinstance(value, str) else ""
    if required and not value:
        raise _invalid(field, f"Give the {field}, at most {limit} characters.")
    if len(value) > limit or "@" in value:
        raise _invalid(field, f"At most {limit} characters, and no email address.")
    return value


def _ids(values, field, *, minimum=0, maximum=BATCH_MAX):
    if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
        raise _invalid(field, "Give a list of ids.")
    unique = list(dict.fromkeys(values))
    if len(unique) < minimum or len(unique) > maximum:
        raise _invalid(field, f"Give {minimum} to {maximum} different ids.")
    return unique


def _batch(public_id, *, lock=False):
    rows = ErasureBatch.objects.select_for_update() if lock else ErasureBatch.objects
    batch = rows.filter(public_id=public_id if isinstance(public_id, str) else "").first()
    if batch is None:
        raise ApiError("not_found", "No such batch.", status=404)
    return batch


def _digest(rows):
    """One value for the whole frozen list: site, subscription and deletion date of each item."""
    lines = sorted(f"{site}|{sub}|{due.isoformat()}" for site, sub, due in rows)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def propose(operator, site_ids, now):
    """Freeze a list of 1 to 25 eligible sites as a batch awaiting approval. Deletes nothing.

    Each site must be on today's eligible list, in no other open batch, and, if erasing it would
    dispose of its account, RETENTION_ENABLED must be on (refused here, not half done at the run).
    One refusal refuses the whole proposal and names each site with its code."""
    require_on()
    ids = _ids(site_ids, "sites", minimum=BATCH_MIN)
    found = eligibility.classify(now)
    eligible = {c.site.public_id: c for c in found.eligible}
    refused = {r.subscription.site.public_id: r.reason_code for r in found.refused}
    taken = set(
        ErasureItem.objects.filter(
            batch__status__in=OPEN_BATCHES, status__in=(ItemStatus.PENDING, ItemStatus.FAILED)
        ).values_list("site__public_id", flat=True)
    )
    selected = {eligible[i].site.pk for i in ids if i in eligible}
    problems = []
    for public_id in ids:
        candidate = eligible.get(public_id)
        if public_id in taken:
            problems.append(f"{public_id}: already_in_batch")
        elif candidate is None:
            problems.append(f"{public_id}: {refused.get(public_id, 'not_due')}")
        elif not _retention_ok(candidate.site, selected):
            problems.append(f"{public_id}: {dry_run.RETENTION_OFF}")
    if problems:
        raise ApiError(
            "not_proposable", "Some sites cannot be proposed.", status=409,
            fields={"sites": problems},
        )
    with transaction.atomic():
        rows = [eligible[i] for i in ids]
        batch = ErasureBatch.objects.create(
            proposed_by=operator, proposed_at=now, expires_at=now + PROPOSAL_LIFETIME,
            list_digest=_digest(
                (c.site.public_id, c.subscription.pk, c.subscription.deletion_due_at) for c in rows
            ),
        )
        for candidate in rows:
            counts, files = dry_run.planned_counts(candidate.site)
            action, why = dry_run.account_step(candidate, selected, found.erased_site_ids)
            ErasureItem.objects.create(
                batch=batch, site=candidate.site, subscription_id=candidate.subscription.pk,
                deletion_due_at=candidate.subscription.deletion_due_at,
                planned_counts={
                    "counts": counts, "private_files": files, "account_action": action,
                    "account_reason": why,
                },
            )
        audit.record(
            operator, "erasure_batch_proposed", target_type=TARGET, target_id=batch.public_id,
            detail={"items": len(rows), "digest": batch.list_digest},
        )
    return batch


def _retention_ok(site, selected):
    return erase.would_dispose_account(site, selected) is False or settings.RETENTION_ENABLED


def _cancel(batch, operator, reason, now):
    batch.status = BatchStatus.CANCELLED
    batch.save(update_fields=["status"])
    audit.record(
        operator, "erasure_batch_cancelled", target_type=TARGET, target_id=batch.public_id,
        reason=reason, detail={"batch": batch.public_id, "reason": reason},
    )


def approve(operator, batch_id, *, confirmation, exclude, reason, now):
    """Approve a frozen list. `confirmation` must be exactly `erase batch <id> <n> sites`, with
    n the number of sites that remain after `exclude` (items to drop; none can be added).
    An expired proposal is cancelled instead and cannot be approved."""
    require_on()
    reason = _text(reason, "reason", required=True, limit=REASON_MAX)
    exclude = _ids(exclude if exclude is not None else [], "exclude")
    expired = False
    with transaction.atomic():
        batch = _batch(batch_id, lock=True)
        if batch.status != BatchStatus.PROPOSED:
            raise ApiError("wrong_status", "This batch is not waiting for approval.", status=409)
        if batch.expires_at <= now:
            _cancel(batch, operator, "expired", now)
            expired = True
        else:
            items = list(batch.items.select_for_update().select_related("site"))
            by_site = {item.site.public_id: item for item in items}
            unknown = [i for i in exclude if i not in by_site]
            if unknown:
                raise _invalid("exclude", "Only sites of this batch can be excluded.")
            kept = [item for item in items if item.site.public_id not in exclude]
            if not kept:
                raise _invalid("exclude", "Every site was excluded. Cancel the batch instead.")
            expected = f"erase batch {batch.public_id} {len(kept)} sites"
            if (confirmation.strip() if isinstance(confirmation, str) else "") != expected:
                raise ApiError(
                    "confirmation_mismatch",
                    "The confirmation does not match the batch and the number of sites.",
                    status=409,
                )
            for item in items:
                if item.site.public_id in exclude:
                    item.status = ItemStatus.EXCLUDED
                    item.save(update_fields=["status"])
            batch.status, batch.approved_by = BatchStatus.APPROVED, operator
            batch.approved_at, batch.approval_reason = now, reason
            batch.save(update_fields=["status", "approved_by", "approved_at", "approval_reason"])
            audit.record(
                operator, "erasure_batch_approved", target_type=TARGET, target_id=batch.public_id,
                reason=reason,
                detail={
                    "kept": len(kept), "excluded": len(exclude), "digest": batch.list_digest,
                },
            )
    if expired:
        raise ApiError("proposal_expired", "This proposal expired. Make the list again.", status=409)
    return batch


def cancel(operator, batch_id, *, reason, now):
    """Cancel a batch that is proposed, approved or needs attention. A running one cannot be."""
    require_on()
    reason = _text(reason, "reason", required=True, limit=REASON_MAX)
    with transaction.atomic():
        batch = _batch(batch_id, lock=True)
        if batch.status not in (
            BatchStatus.PROPOSED, BatchStatus.APPROVED, BatchStatus.NEEDS_ATTENTION
        ):
            raise ApiError("wrong_status", "This batch cannot be cancelled now.", status=409)
        _cancel(batch, operator, reason, now)
    return batch


def _site(public_id):
    site = Site.objects.filter(public_id=public_id if isinstance(public_id, str) else "").first()
    if site is None:
        raise _invalid("site", "No such site.")
    return site


def place_hold(operator, site_id, *, reason_code, reference, now):
    """Stop the erasure of a site for a reason a person gives. At most one open hold per site.
    Takes the site's row lock, so a run that is about to erase it sees the hold."""
    require_on()
    if reason_code not in ErasureHold.Reason.values:
        raise _invalid("reason_code", "Choose one of the listed reasons.")
    reference = _text(reference, "reference", required=False, limit=100)
    site = _site(site_id)
    try:
        with transaction.atomic():
            Site.objects.select_for_update().get(pk=site.pk)
            hold = ErasureHold.objects.create(
                site=site, reason_code=reason_code, reference=reference, placed_by=operator,
                placed_at=now,
            )
            audit.record(
                operator, "erasure_hold_set", target_type="site", target_id=site.public_id,
                reason=reason_code, detail={"reason_code": reason_code, "reference": reference},
            )
    except IntegrityError:
        raise ApiError("already_held", "This site already has an open hold.", status=409) from None
    return hold


def release_hold(operator, site_id, *, now):
    require_on()
    site = _site(site_id)
    with transaction.atomic():
        Site.objects.select_for_update().get(pk=site.pk)
        hold = ErasureHold.objects.filter(site=site, released_at__isnull=True).first()
        if hold is None:
            raise ApiError("not_held", "This site has no open hold.", status=409)
        hold.released_by, hold.released_at = operator, now
        hold.save(update_fields=["released_by", "released_at"])
        audit.record(
            operator, "erasure_hold_released", target_type="site", target_id=site.public_id,
            reason=hold.reason_code,
            detail={"reason_code": hold.reason_code, "reference": hold.reference},
        )
    return hold


# -- the run ------------------------------------------------------------------------------------

def _fail(item_pk, code):
    """Record a failed attempt. The erasure's own transaction was rolled back first."""
    with transaction.atomic():
        ErasureItem.objects.filter(
            pk=item_pk, status__in=(ItemStatus.PENDING, ItemStatus.FAILED)
        ).update(status=ItemStatus.FAILED, reason_code=code, attempts=F("attempts") + 1)
    return ItemStatus.FAILED


def _run_item(batch_pk, item_pk, now):
    """One item in its own transaction. The batch row is locked first, so two runs of one batch
    take turns; the site's locks (in `erase`) serialise runs of two batches."""
    try:
        with transaction.atomic():
            batch = ErasureBatch.objects.select_for_update().get(pk=batch_pk)
            if batch.status not in RUNNABLE:
                return None
            item = (
                ErasureItem.objects.select_for_update(of=("self",))
                .select_related("site__account").get(pk=item_pk)
            )
            if item.status not in (ItemStatus.PENDING, ItemStatus.FAILED) or (
                item.attempts >= MAX_ATTEMPTS
            ):
                return None
            return erase.erase_item(batch, item, now)
    except private_files.StoreError:
        logger.error("Site erasure item %s failed: the private file store.", item_pk)
        return _fail(item_pk, STORE_UNAVAILABLE)
    except Exception as exc:
        logger.error("Site erasure item %s failed (%s).", item_pk, type(exc).__name__)
        return _fail(item_pk, ERROR)


def _settle(batch_pk):
    """done when nothing is left to do, needs_attention when only exhausted failures are."""
    with transaction.atomic():
        batch = ErasureBatch.objects.select_for_update().get(pk=batch_pk)
        if batch.status not in RUNNABLE:
            return batch.status
        items = batch.items.all()
        waiting = items.filter(status=ItemStatus.PENDING).exists() or items.filter(
            status=ItemStatus.FAILED, attempts__lt=MAX_ATTEMPTS
        ).exists()
        exhausted = items.filter(status=ItemStatus.FAILED, attempts__gte=MAX_ATTEMPTS).exists()
        if waiting:
            batch.status = BatchStatus.RUNNING
        else:
            batch.status = BatchStatus.NEEDS_ATTENTION if exhausted else BatchStatus.DONE
        batch.save(update_fields=["status"])
        return batch.status


def run_batch(batch_id, *, now=None):
    """Run the approved items of one batch, and retry its failed ones (up to 5 attempts each).

    Returns {"batch", "status", "erased", "skipped", "failed"}. A batch that is not approved is
    refused; an approved batch whose 7 days passed before it ever started is cancelled."""
    require_on()
    now = now or timezone.now()
    with transaction.atomic():
        batch = _batch(batch_id, lock=True)
        if batch.status not in RUNNABLE:
            raise ApiError("not_approved", "This batch is not approved to run.", status=409)
        if batch.status == BatchStatus.APPROVED and batch.expires_at <= now:
            _cancel(batch, None, "expired", now)
            return {"batch": batch.public_id, "status": batch.status, "erased": 0, "skipped": 0,
                    "failed": 0}
        batch.status = BatchStatus.RUNNING
        batch.save(update_fields=["status"])
    item_pks = list(
        batch.items.filter(
            status__in=(ItemStatus.PENDING, ItemStatus.FAILED), attempts__lt=MAX_ATTEMPTS
        ).order_by("pk").values_list("pk", flat=True)
    )
    outcome = {ItemStatus.ERASED: 0, ItemStatus.SKIPPED: 0, ItemStatus.FAILED: 0}
    for item_pk in item_pks:
        status = _run_item(batch.pk, item_pk, now)
        if status in outcome:
            outcome[status] += 1
    return {
        "batch": batch.public_id, "status": _settle(batch.pk), "erased": outcome[ItemStatus.ERASED],
        "skipped": outcome[ItemStatus.SKIPPED], "failed": outcome[ItemStatus.FAILED],
    }


def run_approved(now=None):
    """What the Celery task does: cancel proposals past their 7 days, then run every approved or
    running batch. Off, it returns at once and reads no lifecycle table. Counts only."""
    if not settings.SITE_ERASURE_ENABLED:
        return {"skipped": "off"}
    now = now or timezone.now()
    expired = 0
    for batch in list(ErasureBatch.objects.filter(status=BatchStatus.PROPOSED, expires_at__lte=now)):
        with transaction.atomic():
            batch = ErasureBatch.objects.select_for_update().get(pk=batch.pk)
            if batch.status == BatchStatus.PROPOSED:
                _cancel(batch, None, "expired", now)
                expired += 1
    totals = {"batches": 0, "erased": 0, "skipped": 0, "failed": 0, "expired": expired}
    for public_id in list(
        ErasureBatch.objects.filter(status__in=RUNNABLE).order_by("pk").values_list("public_id", flat=True)
    ):
        try:
            result = run_batch(public_id, now=now)
        except ApiError:
            continue
        totals["batches"] += 1
        for key in ("erased", "skipped", "failed"):
            totals[key] += result[key]
    return totals


# -- what the staff routes show -----------------------------------------------------------------

def _iso(moment):
    return moment.isoformat() if moment else None


def batch_as_dict(batch):
    return {
        "id": batch.public_id,
        "status": batch.status,
        "proposed_at": _iso(batch.proposed_at),
        "expires_at": _iso(batch.expires_at),
        "approved_at": _iso(batch.approved_at),
        "approval_reason": batch.approval_reason,
        "digest": batch.list_digest,
        "items": [
            {
                "site": item.site.public_id,
                "status": item.status,
                "reason_code": item.reason_code,
                "attempts": item.attempts,
                "deletion_due_at": _iso(item.deletion_due_at),
                "planned": item.planned_counts,
                "counts": item.counts,
                "erased_at": _iso(item.erased_at),
                "account_disposed": item.account_disposed,
            }
            for item in batch.items.select_related("site").order_by("pk")
        ],
    }


def overview():
    """The batches (newest first) and the open holds, for the HQ list. Ids, dates, counts, codes."""
    return {
        "batches": [
            batch_as_dict(b) for b in ErasureBatch.objects.order_by("-pk")[:50]
        ],
        "holds": [
            {
                "site": hold.site.public_id, "reason_code": hold.reason_code,
                "reference": hold.reference, "placed_at": _iso(hold.placed_at),
            }
            for hold in ErasureHold.objects.filter(released_at__isnull=True)
            .select_related("site").order_by("pk")
        ],
    }
