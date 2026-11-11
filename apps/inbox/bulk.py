"""Bulk SMS: one announcement text to a list of numbers, sent one text at a time.

Every function starts from a `BulkScope`. The numbers of a batch are rows only while it needs
them. No audit entry, log line or error made here holds a number, a group's name or the text.
Off (`SMS_BULK_ENABLED`) or before inbox.0003, every route answers as if it did not exist.
"""
import csv
import io
import re
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef
from django.utils import timezone

from apps.common.errors import ApiError
from apps.common.ids import is_public_id
from apps.common.masking import mask_mobile
from apps.common.phones import normalise_ph_mobile
from apps.inbox.access import scope_for
from apps.inbox.models import BulkBatch, BulkRecipient, Contact, ContactGroup, DoNotText, InboxAccess
from apps.inbox.switch import (
    SMS, bulk_on, bulk_tables_exist, contacts_on, contacts_tables_exist, dnt_tables_exist,
)
from apps.notifications import services, sms
from apps.operators import audit

OPT_OUT = " Reply STOP to opt out"
TEXT_MAX = 160 - len(OPT_OUT)  # 138
MAX_SOURCE_BYTES = 1_048_576
MAX_REFUSED_SHOWN = 200
MAX_GROUPS = 50
SECONDS = (18, 25)
CHUNK = 500
TARGET = "bulk_batch"
NOT_A_MOBILE = "The mobile number is not a Philippine mobile."
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_BLOCKED = {
    "running": "A batch is already sending. Wait for it to finish or stop it.",
    "incoming_off": "Incoming texts are not being stored, so a STOP reply would be lost.",
    "no_room": "There is no room left for bulk texts today.",
}


@dataclass(frozen=True)
class BulkScope:
    actor: object


def bulk_scope(actor):
    """What this actor may do in Bulk SMS, or None: while the switch is off, while the tables
    are not there, and for anyone without the send and bulk permissions."""
    if not (bulk_on() and bulk_tables_exist()):
        return None
    scope = scope_for(actor, SMS)
    if scope is None or not scope.can_send:
        return None
    if not InboxAccess.objects.filter(operator=actor, platform=SMS, can_bulk=True).exists():
        return None
    return BulkScope(actor=actor)


def running_for(actor):
    """True when this actor may use Bulk SMS and a batch is waiting or sending. Reads no bulk
    table for anyone else."""
    if bulk_scope(actor) is None:
        return False
    return BulkBatch.objects.filter(state__in=BulkBatch.ACTIVE).exists()


def _invalid(code="invalid_request", **fields):
    return ApiError(code, "Some values are missing or invalid.", fields={n: [m] for n, m in fields.items()})


def _text(value):
    """The staff text as typed, or an ApiError saying why it cannot be sent."""
    if not isinstance(value, str) or not value.strip():
        raise _invalid("invalid_text", text="Type the text.")
    if len(value) > TEXT_MAX:
        raise _invalid("invalid_text", text=f"The text can be up to {TEXT_MAX} characters.")
    problem = services.sms_text_problem(value + OPT_OUT)
    if problem:
        raise _invalid("invalid_text", text=problem)
    return value


def _request_id(value):
    if not isinstance(value, str) or not _REQUEST_ID.match(value):
        raise _invalid(request_id="Send a request id of 16 to 64 letters and digits.")
    return value


# The source ------------------------------------------------------------------------------


def _lines(data):
    """(line number, first cell) of each line of a pasted list or a CSV file."""
    source = data.get("source")
    if source == "pasted":
        text = data.get("numbers")
        if not isinstance(text, str):
            raise _invalid(numbers="Paste the numbers.")
        if len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ApiError("file_too_large", "The list is over 1 MB.", status=413)
        for number, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                yield number, line.strip()
        return
    text = data.get("csv")
    if not isinstance(text, str):
        raise _invalid(csv="Choose a CSV file.")
    if len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ApiError("file_too_large", "The file is over 1 MB.", status=413)
    try:
        for number, row in enumerate(csv.reader(io.StringIO(text.removeprefix("﻿"))), start=1):
            first = row[0].strip() if row else ""
            if not first:
                continue
            if number == 1 and not any(character.isdigit() for character in first):
                continue  # a header row
            yield number, first  # only the first cell is read; the others are never looked at
    except csv.Error:
        raise ApiError("invalid_file", "The file could not be read as a CSV file.") from None


@dataclass
class Source:
    numbers: list  # Philippine mobiles, source order, repeats dropped
    lines_read: int
    refused_count: int
    refused: list  # [(line, reason)], the first MAX_REFUSED_SHOWN
    duplicates: int
    groups: int


def read_source(data):
    source = data.get("source")
    if source in ("pasted", "file"):
        numbers, seen, refused, refused_count, repeated, lines = [], set(), [], 0, 0, 0
        for line, cell in _lines(data):
            lines += 1
            try:
                mobile = normalise_ph_mobile(cell)
            except ValueError:
                refused_count += 1
                if len(refused) < MAX_REFUSED_SHOWN:
                    refused.append((line, NOT_A_MOBILE))
                continue
            if mobile in seen:
                repeated += 1
            else:
                seen.add(mobile)
                numbers.append(mobile)
        return Source(numbers, lines, refused_count, refused, repeated, 0)
    if source == "groups":
        ids = data.get("groups")
        if (
            not isinstance(ids, list) or not ids or len(ids) > MAX_GROUPS
            or not all(isinstance(i, str) for i in ids)
        ):
            raise _invalid(groups="Choose the groups.")
        if not (contacts_on() and contacts_tables_exist()):
            raise _invalid(source="Groups are not available.")
        ids = list(dict.fromkeys(ids))
        found = ContactGroup.objects.filter(owner_kind="hq", public_id__in=ids).count()
        if found != len(ids) or not all(is_public_id(i, "grp") for i in ids):
            raise ApiError("not_found", "Not found.", status=404)
        mobiles = (
            Contact.objects.filter(owner_kind="hq", groups__public_id__in=ids)
            .order_by("pk").values_list("pk", "mobile")
        )
        seen, numbers = set(), []
        for pk, mobile in mobiles:
            if pk not in seen:
                seen.add(pk)
                numbers.append(mobile)
        return Source(numbers, len(numbers), 0, [], 0, len(ids))
    raise _invalid(source="Choose where the numbers come from.")


# The check -------------------------------------------------------------------------------


def _listed(numbers):
    if not dnt_tables_exist():
        return set()
    found = set()
    for start in range(0, len(numbers), CHUNK):
        found.update(
            DoNotText.objects.filter(platform=SMS, mobile__in=numbers[start:start + CHUNK])
            .values_list("mobile", flat=True)
        )
    return found


def _incoming_on():
    from apps.inbox.switch import inbox_on

    phone = sms.pick_device()
    return bool(inbox_on() and phone is not None and phone.forwards_incoming)


def _blocked(room):
    if BulkBatch.objects.filter(state__in=BulkBatch.ACTIVE).exists():
        return "running"
    if not _incoming_on():
        return "incoming_off"
    if room <= 0:
        return "no_room"
    return ""


def check(scope, data):
    """What a confirm would do. Returns (the answer, the numbers to text, the numbers left
    out). Stores, logs and audits nothing."""
    text = _text(data.get("text"))
    source = read_source(data)
    listed = _listed(source.numbers)
    eligible = [n for n in source.numbers if n not in listed]
    used, room = services.sms_room_for_staff()
    size = min(len(eligible), room)
    answer = {
        "lines_read": source.lines_read,
        "duplicates": source.duplicates,
        "refused_count": source.refused_count,
        "refused": [{"line": n, "reason": r} for n, r in source.refused],
        "opted_out": len(source.numbers) - len(eligible),
        "eligible": len(eligible),
        "used_today": used,
        "left_today": room,
        "size": size,
        "left_out": len(eligible) - size,
        "seconds_min": size * SECONDS[0],
        "seconds_max": size * SECONDS[1],
        "text_length": len(text) + len(OPT_OUT),
        "blocked": _blocked(room),
        "groups": source.groups,
    }
    return answer, eligible[:size], eligible[size:]


def check_answer(scope, data):
    answer = check(scope, data)[0]
    answer.pop("groups")
    return answer


def left_out(scope, data):
    """The numbers beyond today's room, one per line, for the staff user who ran the check."""
    answer, _, rest = check(scope, data)
    if not rest:
        raise ApiError("nothing_left_out", "Nothing is left out.", status=409)
    audit.record(
        scope.actor, "sms_inbox_bulk_left_out", target_type=TARGET,
        detail={"platform": SMS, "source": data["source"], "count": len(rest)},
    )
    return "".join(f"{number}\n" for number in rest)


# The batch -------------------------------------------------------------------------------


def _seconds_left(batch, waiting):
    return waiting * (SECONDS[0] + SECONDS[1]) // 2 if batch.state in BulkBatch.ACTIVE else 0


def _row(batch):
    waiting = max(batch.size - batch.sent - batch.failed - batch.not_sent, 0)
    return {
        "batch": batch.public_id,
        "state": batch.state,
        "when": batch.created_at.isoformat(),
        "who": batch.created_by.get_username(),
        "source": batch.source,
        "size": batch.size,
        "sent": batch.sent,
        "failed": batch.failed,
        "not_sent": batch.not_sent,
        "waiting": waiting,
        "left_out": batch.left_out,
        "text_length": len(batch.text) + len(OPT_OUT),
        "seconds_left": _seconds_left(batch, waiting),
    }


def confirm(scope, data):
    """Start a batch. Takes the same source as the check, again, with the typed count. Returns
    (the batch row, created)."""
    request_id = _request_id(data.get("request_id"))
    count = data.get("count")
    if isinstance(count, bool) or not isinstance(count, int):
        raise _invalid(count="Type the number of texts.")
    existing = BulkBatch.objects.filter(created_by=scope.actor, request_id=request_id).select_related("created_by").first()
    if existing:
        return _row(existing), False
    answer, chosen, _ = check(scope, data)
    blocked = answer["blocked"]
    if blocked:
        code = "batch_running" if blocked == "running" else blocked
        raise ApiError(code, _BLOCKED[blocked], status=409)
    if answer["size"] == 0 or answer["size"] != count:
        raise ApiError("changed", "The list changed since the check. Check it again.", status=409)
    try:
        with transaction.atomic():
            batch = BulkBatch.objects.create(
                created_by=scope.actor, request_id=request_id, text=_text(data["text"]),
                source=data["source"], group_count=answer["groups"], local_day=timezone.localdate(),
                lines_read=answer["lines_read"], duplicates=answer["duplicates"],
                refused=answer["refused_count"], opted_out=answer["opted_out"], size=answer["size"],
                left_out=answer["left_out"],
            )
            BulkRecipient.objects.bulk_create(
                [BulkRecipient(batch=batch, position=i, mobile=n) for i, n in enumerate(chosen)],
                batch_size=CHUNK,
            )
            audit.record(
                scope.actor, "sms_inbox_bulk", target_type=TARGET, target_id=batch.public_id,
                detail={
                    "platform": SMS, "batch": batch.public_id, "source": batch.source,
                    "groups": batch.group_count, "lines_read": batch.lines_read,
                    "duplicates": batch.duplicates, "refused": batch.refused,
                    "opted_out": batch.opted_out, "size": batch.size, "left_out": batch.left_out,
                    "text_length": len(batch.text) + len(OPT_OUT),
                },
            )
    except IntegrityError:
        # Either another batch started first, or the same request id raced in.
        again = BulkBatch.objects.filter(created_by=scope.actor, request_id=request_id).select_related("created_by").first()
        if again:
            return _row(again), False
        raise ApiError("batch_running", _BLOCKED["running"], status=409) from None
    return _row(batch), True


# The driver ------------------------------------------------------------------------------


def _end_rest(batch, reason):
    batch.recipients.filter(state="waiting").update(state="not_sent", reason=reason)


def settle(batch, now=None):
    """Bring the batch up to date with what the phone reported, write its counts and finish it
    when nothing is waiting. Idempotent; the batch is locked by the caller."""
    recipients = batch.recipients
    if not recipients.exists():
        return
    queued = list(recipients.filter(state="queued").values_list("pk", "send_record"))
    if queued:
        found = services.sms_statuses([record for _, record in queued])
        for pk, record in queued:
            status, reason = found.get(record, ("gone", "record_gone"))
            if status == "sent":
                recipients.filter(pk=pk).update(state="sent")
            elif status in ("failed", "expired", "refused", "gone"):
                recipients.filter(pk=pk).update(state="failed", reason=(reason or status)[:40])
    totals = {row["state"]: row["n"] for row in recipients.values("state").annotate(n=Count("pk"))}
    batch.sent = totals.get("sent", 0)
    batch.failed = totals.get("failed", 0)
    batch.not_sent = totals.get("not_sent", 0)
    fields = ["sent", "failed", "not_sent"]
    over = not totals.get("waiting") and not totals.get("queued")
    if over and batch.state in BulkBatch.ACTIVE:
        batch.state, batch.finished_at = "finished", timezone.now()
        fields += ["state", "finished_at"]
    batch.save(update_fields=fields)
    if over:
        recipients.all().delete()


def settle_pending():
    """Settle every batch that is over or stopped but still holds a number with the phone."""
    with transaction.atomic():
        # No join and no DISTINCT: PostgreSQL refuses FOR UPDATE with DISTINCT.
        holding = BulkRecipient.objects.filter(batch=OuterRef("pk"))
        for batch in BulkBatch.objects.select_for_update().filter(
            Exists(holding), state__in=("stopped", "finished")
        ):
            settle(batch)


def feed(now):
    """Queue the next text of the running batch. Runs inside a phone's poll."""
    if not bulk_tables_exist():
        return
    with transaction.atomic():
        batch = BulkBatch.objects.select_for_update().filter(state__in=BulkBatch.ACTIVE).first()
        if batch is None:
            pass
        else:
            _drive(batch, now)
            return
    settle_pending()


def _drive(batch, now):
    settle(batch, now)
    if batch.state not in BulkBatch.ACTIVE:
        return
    if batch.local_day != timezone.localdate():
        _end_rest(batch, "day_ended")
        settle(batch, now)
        return
    if batch.recipients.filter(state="queued").exists():
        return  # one at a time: the last is not reported yet
    following = batch.recipients.filter(state="waiting").order_by("position").first()
    if following is None:
        return
    result = services.send_sms(
        to=following.mobile, purpose="inbox_bulk", text=batch.text + OPT_OUT,
        ttl=services.SMS_STAFF_TEXT_LIFETIME,
    )
    if result.outcome == services.SMS_QUEUED:
        following.state, following.send_record = "queued", result.message.public_id
        following.save(update_fields=["state", "send_record"])
        if batch.state == "waiting":
            batch.state = "sending"
            batch.save(update_fields=["state"])
    elif result.reason == "staff_limit":
        _end_rest(batch, "staff_limit")
    elif result.reason in ("paused", "gateway_offline", "gateway_off"):
        return  # try again at the next poll
    else:
        following.state, following.reason = "not_sent", result.reason[:40]
        following.save(update_fields=["state", "reason"])
    settle(batch, now)


def _batch(public_id, *, lock=False):
    if not isinstance(public_id, str) or not is_public_id(public_id, "bulk"):
        raise ApiError("not_found", "Not found.", status=404)
    rows = BulkBatch.objects.select_related("created_by").filter(platform=SMS, public_id=public_id)
    batch = (rows.select_for_update() if lock else rows).first()
    if batch is None:
        raise ApiError("not_found", "Not found.", status=404)
    return batch


def stop(scope, public_id):
    with transaction.atomic():
        batch = _batch(public_id, lock=True)
        if batch.state not in BulkBatch.ACTIVE:
            raise ApiError("not_running", "That batch is not sending.", status=409)
        _end_rest(batch, "stopped")
        for recipient in batch.recipients.filter(state="queued"):
            if services.cancel_sms(recipient.send_record):
                recipient.state, recipient.reason = "not_sent", "stopped"
                recipient.save(update_fields=["state", "reason"])
        batch.state, batch.stopped_by, batch.stopped_at = "stopped", scope.actor, timezone.now()
        batch.save(update_fields=["state", "stopped_by", "stopped_at"])
        settle(batch)
        audit.record(
            scope.actor, "sms_inbox_bulk_stop", target_type=TARGET, target_id=batch.public_id,
            detail={
                "platform": SMS, "batch": batch.public_id, "sent": batch.sent,
                "waiting": 0, "not_sent": batch.not_sent,
            },
        )
    return _row(batch)


# Reading ---------------------------------------------------------------------------------


def _page(queryset, *, cursor, limit, prefix, newest_first):
    limit = max(1, min(limit if limit is not None else 20, 100))
    rows = queryset.order_by("-pk" if newest_first else "pk")
    if cursor:
        last = queryset.filter(public_id=cursor).first() if is_public_id(cursor, prefix) else None
        if last is None:
            raise ApiError("invalid_cursor", "That page marker is not valid.")
        rows = rows.filter(pk__lt=last.pk) if newest_first else rows.filter(pk__gt=last.pk)
    items = list(rows[: limit + 1])
    more = len(items) > limit
    items = items[:limit]
    return items, (items[-1].public_id if more else None)


def status(scope):
    used, room = services.sms_room_for_staff()
    return {
        "bulk": True,
        "running": BulkBatch.objects.filter(state__in=BulkBatch.ACTIVE).exists(),
        "used_today": used,
        "left_today": room,
        "reserved_for_codes": sms.RESERVED_FOR_CODES,
        "opt_out_line": OPT_OUT,
        "text_max": TEXT_MAX,
    }


def list_batches(scope, *, cursor="", limit=None):
    settle_pending()
    feed_settle()
    batches = BulkBatch.objects.filter(platform=SMS).select_related("created_by")
    items, following = _page(batches, cursor=cursor, limit=limit, prefix="bulk", newest_first=True)
    return [_row(b) for b in items], following


def feed_settle():
    """Settle the running batch without queuing anything, so a list is true with the phone away."""
    with transaction.atomic():
        batch = BulkBatch.objects.select_for_update().filter(state__in=BulkBatch.ACTIVE).first()
        if batch is not None:
            settle(batch)


def open_batch(scope, public_id, *, cursor="", limit=None):
    with transaction.atomic():
        batch = _batch(public_id, lock=True)
        settle(batch)
    answer = _row(batch)
    rows, following = _recipients(batch, cursor, limit)
    answer["results"] = [
        {"number": mask_mobile(r.mobile), "state": r.state, "reason": r.reason} for r in rows
    ]
    answer["next"] = following
    return answer


def _recipients(batch, cursor, limit):
    """Pages of a batch's numbers by position. The cursor is a position as text."""
    limit = max(1, min(limit if limit is not None else 20, 100))
    if cursor and (not isinstance(cursor, str) or not cursor.isdigit() or len(cursor) > 9):
        raise ApiError("invalid_cursor", "That page marker is not valid.")
    after = int(cursor) if cursor else -1
    items = list(batch.recipients.filter(position__gt=after).order_by("position")[: limit + 1])
    more = len(items) > limit
    items = items[:limit]
    return items, (str(items[-1].position) if more else None)
