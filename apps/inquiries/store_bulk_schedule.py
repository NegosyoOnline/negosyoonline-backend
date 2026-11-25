"""Schedule SMS: a store's bulk batch with a future start time (ClickUp z8uq9mj9q4, part of #13 round 9).

A scheduled batch holds the text, the audience and `scheduled_at` but no recipients; its `size` and
`left_out` are a count for display, replaced by the recount at start. The text, audience, exclusions,
cap, window and pace are Bulk's (apps/inquiries/store_bulk.py). `start_due` turns a due batch into an
ordinary `waiting` batch, or ends it with a reason code; it runs under the batch's row lock, from the
beat task, the phone's pull, and the store's reads, and is idempotent.

Nothing here logs or audits a text, a name or a number: audit entries carry the batch id and counts."""
import logging
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.common.errors import ApiError
from apps.common.ids import is_public_id
from apps.common.masking import mask_mobile
from apps.inquiries import sms_inbox, store_bulk
from apps.inquiries.store_bulk import MANILA, _audit, _find, _ids, _kind, _select, batch_out, make_recipients
from apps.notifications import sms
from apps.notifications.models import StoreBulkBatch

logger = logging.getLogger(__name__)

MIN_AHEAD = timedelta(minutes=10)
MAX_AHEAD = timedelta(days=30)
MAX_SCHEDULED = 20
RUN_LIMIT = 200  # due batches handled in one run


def _now():
    return timezone.now()


def _invalid(field, message):
    return ApiError("invalid", message, status=422, fields={field: [message]})


def parse_time(value):
    """An ISO datetime with a timezone; without one it is Asia/Manila time."""
    try:
        when = datetime.fromisoformat(value.strip()) if isinstance(value, str) else None
    except ValueError:
        when = None
    if when is None:
        raise _invalid("scheduled_at", "Choose a date and time.")
    return when if when.tzinfo else when.replace(tzinfo=MANILA)


def _check_time(when, now):
    if when < now + MIN_AHEAD:
        raise ApiError("too_soon", "Choose a time at least 10 minutes from now.", status=422, fields={"scheduled_at": ["Too soon."]})
    if not store_bulk.in_window(when):
        raise ApiError("outside_window", "Texts go out only from 08:00 to 22:00 Manila time.", status=422, fields={"scheduled_at": ["Outside 08:00 to 22:00."]})
    if when > now + MAX_AHEAD:
        raise ApiError("too_far", "Choose a time within the next 30 days.", status=422, fields={"scheduled_at": ["Too far ahead."]})


def _picked(kind, groups, contacts):
    return {"groups": _ids(groups, "groups"), "contacts": _ids(contacts, "contacts")} if kind == StoreBulkBatch.PICK else {}


def _count(site, account, kind, picked):
    chosen, left, _ = _select(site, kind, account, picked)
    if not chosen:
        raise ApiError("empty_audience", "There is nobody to send to.", status=409)
    return len(chosen), left


def create(site, account, data, now=None):
    """Schedule a batch. Returns (the batch dict, created). `data`: text, audience, scheduled_at,
    idempotency_key, groups, contacts."""
    now = now or _now()
    key = data["idempotency_key"]
    existing = StoreBulkBatch.objects.filter(site=site, request_id=key).first()
    if existing:
        return batch_out(existing), False
    kind = _kind(data["audience"])
    full = store_bulk.template_text(site, data["text"])
    when = parse_time(data["scheduled_at"])
    _check_time(when, now)
    if StoreBulkBatch.objects.filter(site=site, state=StoreBulkBatch.SCHEDULED).count() >= MAX_SCHEDULED:
        raise ApiError("schedule_limit", "You can have at most 20 scheduled texts. Cancel one first.", status=409)
    picked = _picked(kind, data.get("groups"), data.get("contacts"))
    size, left = _count(site, account, kind, picked)
    try:
        with transaction.atomic():
            batch = StoreBulkBatch.objects.create(
                site=site, created_by=account, request_id=key, text=full, audience=kind, picked=picked,
                size=size, left_out=left, state=StoreBulkBatch.SCHEDULED, scheduled_at=when,
            )
            _audit(batch, "store_bulk_scheduled", size=size, audience=kind, left_out=left)
    except IntegrityError:
        again = StoreBulkBatch.objects.filter(site=site, request_id=key).first()
        if again:
            return batch_out(again), False
        raise
    return batch_out(batch), True


def _find_scheduled(site, public_id, *, lock=False):
    batch = _find(site, public_id, lock=lock)
    if batch.scheduled_at is None:
        raise ApiError("not_found", "Not found.", status=404)
    return batch


def _started():
    return ApiError("already_started", "That text has already started or was cancelled, so it cannot be changed.", status=409)


def edit(site, account, public_id, data, now=None):
    """Change a scheduled batch (any of text, audience, groups, contacts, scheduled_at), same checks."""
    now = now or _now()
    with transaction.atomic():
        batch = _find_scheduled(site, public_id, lock=True)
        if batch.state != StoreBulkBatch.SCHEDULED:
            raise _started()
        if "text" in data:
            batch.text = store_bulk.template_text(site, data["text"])
        if "scheduled_at" in data:
            when = parse_time(data["scheduled_at"])
            _check_time(when, now)
            batch.scheduled_at = when
        if any(k in data for k in ("audience", "groups", "contacts")):
            kind = _kind(data.get("audience", batch.audience))
            old = batch.picked or {}
            batch.audience = kind
            batch.picked = _picked(kind, data.get("groups", old.get("groups")), data.get("contacts", old.get("contacts")))
        batch.size, batch.left_out = _count(site, account, batch.audience, batch.picked)
        batch.save(update_fields=["text", "scheduled_at", "audience", "picked", "size", "left_out"])
        _audit(batch, "store_bulk_schedule_edited", actor=account, size=batch.size, audience=batch.audience, left_out=batch.left_out)
    return batch_out(batch)


def cancel(site, account, public_id, now=None):
    """Cancel a scheduled batch. A second cancel answers the same."""
    now = now or _now()
    with transaction.atomic():
        batch = _find_scheduled(site, public_id, lock=True)
        if batch.state == StoreBulkBatch.CANCELLED:
            return batch_out(batch)
        if batch.state != StoreBulkBatch.SCHEDULED:
            raise _started()
        batch.state, batch.end_reason, batch.stopped_at = StoreBulkBatch.CANCELLED, "cancelled", now
        batch.save(update_fields=["state", "end_reason", "stopped_at"])
        batch.refresh_from_db()
        _audit(batch, "store_bulk_schedule_cancelled", actor=account, size=batch.size)
    return batch_out(batch)


def picked_names(site, batches):
    """{batch pk: (groups, contacts)} for a page of batches of one store. For a "pick" batch: the
    picked groups ({id, name}) and contacts ({id, first_name, last_name, mobile_masked}), of the
    store only, in the picked order; ids that are gone are skipped. "all" batches get ([], []).
    At most two queries for the whole page; never a full number; no audit entry."""
    from apps.inbox import contacts as inbox_contacts  # not at import: inbox is a later layer

    wanted = {b.pk: (b.picked or {}) for b in batches if b.audience == StoreBulkBatch.PICK}
    group_ids = list(dict.fromkeys(i for p in wanted.values() for i in p.get("groups") or []))
    contact_ids = list(dict.fromkeys(i for p in wanted.values() for i in p.get("contacts") or []))
    scope = inbox_contacts.site_scope(None, site) if (group_ids or contact_ids) else None
    found_groups, found = {}, {}
    if scope is not None:
        if group_ids:
            found_groups = {g[0]: g for g in scope.groups.filter(public_id__in=group_ids).values_list("public_id", "name")}
        if contact_ids:
            found = {c[0]: c for c in scope.contacts.filter(public_id__in=contact_ids).values_list(
                "public_id", "first_name", "last_name", "mobile")}
    out = {}
    for b in batches:
        p = wanted.get(b.pk, {})
        out[b.pk] = (
            [{"id": i, "name": found_groups[i][1]} for i in p.get("groups") or [] if i in found_groups],
            [{"id": i, "first_name": found[i][1], "last_name": found[i][2], "mobile_masked": mask_mobile(found[i][3])}
             for i in p.get("contacts") or [] if i in found],
        )
    return out


def with_names(site, batches):
    """The `batch_out` of each batch plus its `groups` and `contacts` (see `picked_names`)."""
    names = picked_names(site, batches)
    rows = []
    for b in batches:
        row = batch_out(b)
        row["groups"], row["contacts"] = names[b.pk]
        rows.append(row)
    return rows


def get(site, public_id):
    store_bulk.refresh(site)
    return with_names(site, [_find_scheduled(site, public_id)])[0]


def list_scheduled(site, *, cursor="", limit=None):
    """Every batch of the store that has a scheduled time, soonest first."""
    store_bulk.refresh(site)
    rows = StoreBulkBatch.objects.filter(site=site, scheduled_at__isnull=False).select_related("site").order_by("scheduled_at", "pk")
    if cursor:
        last = None
        if isinstance(cursor, str) and is_public_id(cursor, "sbk"):
            last = StoreBulkBatch.objects.filter(site=site, public_id=cursor, scheduled_at__isnull=False).first()
        if last is None:
            raise ApiError("invalid_cursor", "That page marker is not valid.", status=422)
        rows = rows.filter(Q(scheduled_at__gt=last.scheduled_at) | Q(scheduled_at=last.scheduled_at, pk__gt=last.pk))
    size = store_bulk._limit_of(limit)
    items = list(rows[: size + 1])
    more = len(items) > size
    items = items[:size]
    return with_names(site, items), (items[-1].public_id if more else None)


# -- the runner -----------------------------------------------------------------------------

def _end(batch, reason, now):
    """The batch never started: it ends with `reason`, nothing was queued."""
    batch.state, batch.end_reason, batch.finished_at, batch.not_sent = StoreBulkBatch.ENDED, reason, now, batch.size
    batch.save(update_fields=["state", "end_reason", "finished_at", "not_sent"])
    _audit(batch, "store_bulk_ended", reason=reason, size=batch.size, sent=0, failed=0, not_sent=batch.size)


def _start_reason(batch, now):
    """Why a due batch cannot start now ("" when it can), by the existing reason codes."""
    site = batch.site
    return (
        store_bulk._end_reason(site)  # site_held, billing_only, line_disabled
        or store_bulk.blocked(site)  # sms_off, replies_off, no_line
        or ("" if store_bulk.in_window(now) else "outside_window")
        or ("daily_cap" if store_bulk._at_cap(site) else "")
        or ("" if sms.gateway_online(site, sms.STORE_BULK) else "phone_offline")
    )


def _start_one(pk, now):
    batch = StoreBulkBatch.objects.select_for_update().select_related("site", "created_by").filter(
        pk=pk, state=StoreBulkBatch.SCHEDULED, scheduled_at__lte=now).first()
    if batch is None:  # cancelled, edited later or started by another run
        return
    if StoreBulkBatch.objects.filter(site=batch.site, state__in=StoreBulkBatch.ACTIVE).exists():
        if not store_bulk.in_window(now):
            _end(batch, "batch_running", now)
        return  # tried again on the next run
    reason = _start_reason(batch, now)
    if reason:
        _end(batch, reason, now)
        return
    try:
        chosen, left, _ = _select(batch.site, batch.audience, batch.created_by, batch.picked)
    except ApiError:  # a picked group or contact is gone
        chosen, left = [], batch.left_out
    if not chosen:
        _end(batch, "empty_audience", now)
        return
    try:
        with transaction.atomic():
            batch.size, batch.left_out, batch.state = len(chosen), left, StoreBulkBatch.WAITING
            batch.save(update_fields=["size", "left_out", "state"])
            make_recipients(batch, batch.audience, chosen)
            _audit(batch, "store_bulk_schedule_started", size=batch.size, left_out=left)
    except IntegrityError:  # an ordinary batch took the place a moment ago: wait for the next run
        pass


def start_due(site=None, now=None):
    """Start every due scheduled batch (of one store, or of all). Safe to call at any time and from
    any number of callers. Returns how many were handled."""
    from apps.inquiries.switch import store_bulk_ready

    if not store_bulk_ready():
        return 0
    now = now or _now()
    rows = StoreBulkBatch.objects.filter(state=StoreBulkBatch.SCHEDULED, scheduled_at__lte=now)
    if site is not None:
        rows = rows.filter(site=site)
    done = 0
    for pk in list(rows.order_by("scheduled_at", "pk").values_list("pk", flat=True)[:RUN_LIMIT]):
        try:
            with transaction.atomic():
                _start_one(pk, now)
            done += 1
        except Exception as error:  # one batch never stops the others; only the id and class are logged
            logger.error("store bulk schedule start failed batch=%s error=%s", pk, type(error).__name__)
    return done
