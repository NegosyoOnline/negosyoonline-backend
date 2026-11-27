"""A store's bulk text to its own members (STORE-BULK, ClickUp z8uq9mbreu; plan
docs/plans/store-bulk-sms-plan-2026-11-23.md; contract docs/plans/store-bulk-sms-be-api-2026-11-23.md).

The owner texts the store's own members, one text at a time, from the store's own paired phone; a store
entitled to a line with no phone of its own sends from the platform phone (ONE-PHONE, Coach Ed
2026-11-26). Coach Ed's ruling (2026-11-23): "All my members" was the
audience; since 2026-11-25 (ALL-GROUPS-OPTION) "all" means All groups: every contact in any of the
store's groups, each phone once (verified Members are not read). No promo filter, no promo label; no consent nudge; always left out:
the store's STOP list, unverified numbers and repeats; no web links; no forced STOP footer; a daily
cap of 2,000 a store (SmsPolicy.store_bulk_daily_limit); one text every 18 to 25 s, only from 06:00
to 22:00 Manila time.

Nothing here logs or audits a number or a text. The audit entries carry the batch id and counts
only. A recipient's number is held in full only while its batch is active; once the batch is over it
is replaced by its masked form. The batch (with its text) is deleted after 180 days
(apps/inquiries/retention.py).

A batch with a future start time (Schedule SMS) lives in apps/inquiries/store_bulk_schedule.py.

Dark behind STORE_BULK_SMS_ENABLED and the three switches it needs (`switch.store_bulk_ready`)."""
import logging
import re
from datetime import time
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from apps.accounts import services as account_services
from apps.accounts.models import AccountMobile
from apps.common.errors import ApiError
from apps.common.ids import is_public_id
from apps.common.masking import mask_mobile
from apps.inquiries import sms_inbox
from apps.inquiries.models import InquirySmsOptOut
from apps.members.models import Member
from apps.notifications import services, sms
from apps.notifications.models import StoreBulkBatch, StoreBulkRecipient

logger = logging.getLogger(__name__)

MANILA = ZoneInfo("Asia/Manila")
WINDOW = (time(6, 0), time(22, 0))  # texts go only from 06:00 up to, not including, 22:00 Manila time
SECONDS = (18, 25)  # the phone keeps this gap between two texts (apps/notifications/gateway.py)
TEST_KEPT = 24 * 3600
MAX_SKIPS = 100  # members passed over (gone, opted out ...) in one poll before the next poll goes on
PAGE = (20, 100)
# "all" now means All groups (ALL-GROUPS-OPTION, 2026-11-25); the stored value and the model choices are unchanged.
KINDS = (StoreBulkBatch.ALL, StoreBulkBatch.PICK)
CHUNK = 500
TARGET = "store_bulk_batch"
PURPOSE = "store_bulk"

# Why a member is left out of the audience (the keys of `left_out`).
UNVERIFIED, INVALID_NUMBER, OPTED_OUT, DUPLICATES = (
    "unverified", "invalid_number", "opted_out", "duplicates",
)
LEFT_OUT = (UNVERIFIED, INVALID_NUMBER, OPTED_OUT, DUPLICATES)

# Why the whole store cannot start a batch now, or why a running one is ended.
SITE_HELD, BILLING_ONLY, LINE_DISABLED, NO_LINE = "site_held", "billing_only", "line_disabled", "no_line"
BLOCKED_TEXTS = {
    SITE_HELD: "This store is on hold. Nothing can be sent.",
    BILLING_ONLY: "This site is limited to Billing until its subscription continues.",
    LINE_DISABLED: "Your store's phone line is switched off.",
    NO_LINE: "Pair your phone in Settings → SMS Gateway to text many members.",
    sms_inbox.SMS_OFF: sms_inbox.CONFLICT[sms_inbox.SMS_OFF],
    sms_inbox.REPLIES_OFF: sms_inbox.CONFLICT[sms_inbox.REPLIES_OFF],
}
# What the owner reads beside a recipient or an end that carries a reason. Fixed text only.
REASON_TEXTS = {
    "stopped": "Stopped by you.",
    SITE_HELD: BLOCKED_TEXTS[SITE_HELD],
    BILLING_ONLY: BLOCKED_TEXTS[BILLING_ONLY],
    LINE_DISABLED: BLOCKED_TEXTS[LINE_DISABLED],
    "member_removed": "The member is no longer in your list.",
    "contact_removed": "The contact is no longer in your list.",
    "number_changed": "The member's number changed.",
    UNVERIFIED: "The member's mobile is not verified.",
    OPTED_OUT: "The member asked for no more texts (STOP).",
    sms_inbox.TEXT_INVALID: sms_inbox.REASON_TEXTS[sms_inbox.TEXT_INVALID],
    sms_inbox.NOT_SENT: sms_inbox.REASON_TEXTS[sms_inbox.NOT_SENT],
    "expired": sms_inbox.REASON_TEXTS["expired"],
    # A scheduled batch (apps/inquiries/store_bulk_schedule.py) that is cancelled or cannot start.
    "cancelled": "Cancelled by you.",
    "batch_running": "Another batch was still sending at the start time.",
    "empty_audience": "There was nobody to send to at the start time.",
    "outside_window": "The start time came after texts may go out (06:00 to 22:00 Manila time).",
    "daily_cap": "The store's daily bulk text limit was reached at the start time.",
    "phone_offline": "Your phone was offline at the start time.",
    NO_LINE: BLOCKED_TEXTS[NO_LINE],
    sms_inbox.SMS_OFF: BLOCKED_TEXTS[sms_inbox.SMS_OFF],
    sms_inbox.REPLIES_OFF: BLOCKED_TEXTS[sms_inbox.REPLIES_OFF],
}
PAUSED_REASONS = ("outside_window", "daily_cap", "phone_offline")


# -- time and availability ------------------------------------------------------------------

def in_window(now=None):
    """True from 06:00 up to, not including, 22:00 Manila time."""
    local = (now or timezone.now()).astimezone(MANILA).time()
    return WINDOW[0] <= local < WINDOW[1]


def _held(site):
    return bool(site.operator_hold or site.deleted_at)


def _billing_only(site):
    from apps.billing import services as billing  # not at import: billing is a later layer

    return bool(billing.position(site).billing_only)


def blocked(site):
    """Why this store cannot start a batch now: `site_held`, a text-inbox reason (`sms_off`,
    `replies_off`) or `no_line`; "" when it can. (A Billing-only store never gets this far: the
    owner router answers 403 `billing_only` first.)"""
    if _held(site):
        return SITE_HELD
    code = sms_inbox.unavailable(site)
    if code:
        return code
    if sms.bulk_phone(site) is None:
        return NO_LINE
    return ""


def _end_reason(site):
    """Why a running batch must end now: `site_held`, `billing_only`, `line_disabled`; else ""."""
    if _held(site):
        return SITE_HELD
    if _billing_only(site):
        return BILLING_ONLY
    if not sms.site_has_line(site):
        return LINE_DISABLED
    return ""


def _limit():
    return sms.policy().store_bulk_daily_limit


def _at_cap(site):
    return sms.store_bulk_used_today(site) >= _limit()


# -- audience -------------------------------------------------------------------------------

def _invalid_audience(message="Choose All groups, or pick groups or contacts."):
    return ApiError("invalid_audience", message, status=400, fields={"audience": [message]})


def _kind(value):
    if value not in KINDS:
        raise _invalid_audience()
    return value


def _ids(value, what):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
        raise _invalid_audience(f"Choose {what} from your list.")
    return list(dict.fromkeys(value))


def _picked_scope(site, account):
    from apps.inbox import contacts  # not at import: inbox is a later layer for this module

    scope = contacts.site_scope(account, site)
    if scope is None:  # the store-contacts switch is off or the tables are not there
        raise _invalid_audience()
    return scope


def _select_pick(site, account, picked):
    """([(contact pk, name, mobile)], {reason: count}, total). The contacts in the picked groups plus
    the picked contacts, of this store only, in the order they were added; each counted once, under
    the first reason that applies. No consent filter: a contact is the store's own list."""
    groups, contacts = _ids((picked or {}).get("groups"), "groups"), _ids((picked or {}).get("contacts"), "contacts")
    if not groups and not contacts:
        raise _invalid_audience("Pick at least one group or contact.")
    scope = _picked_scope(site, account)
    found_groups = set()
    for i in range(0, len(groups), CHUNK):
        found_groups.update(scope.groups.filter(public_id__in=groups[i:i + CHUNK]).values_list("pk", flat=True))
    if len(found_groups) != len(groups):
        raise _invalid_audience("Choose groups from your list.")
    fields = ("pk", "first_name", "last_name", "mobile")
    people, group_pks = {}, list(found_groups)  # contact pk -> row, so a contact in two groups is read once
    for i in range(0, len(group_pks), CHUNK):  # one query per 500 groups, whatever the size of the groups
        for row in scope.contacts.filter(groups__in=group_pks[i:i + CHUNK]).values_list(*fields):
            people[row[0]] = row
    found_contacts = 0
    for i in range(0, len(contacts), CHUNK):
        got = list(scope.contacts.filter(public_id__in=contacts[i:i + CHUNK]).values_list(*fields))
        found_contacts += len(got)
        people.update((row[0], row) for row in got)
    if found_contacts != len(contacts):
        raise _invalid_audience("Choose contacts from your list.")
    rows = sorted(people.values())
    stopped = set(InquirySmsOptOut.objects.filter(site=site).values_list("mobile", flat=True))
    left = dict.fromkeys(LEFT_OUT, 0)
    chosen, seen = [], set()
    for pk, first, last, mobile in rows:
        if mobile.startswith("+63900"):
            left[INVALID_NUMBER] += 1
        elif mobile in stopped:
            left[OPTED_OUT] += 1
        elif mobile in seen:
            left[DUPLICATES] += 1
        else:
            seen.add(mobile)
            chosen.append((pk, f"{first} {last}".strip(), mobile))
    return chosen, left, len(rows)


def _group_contacts(site, account):
    """[(pk, first_name, last_name, mobile)] of every contact of this store that is in at least one of
    the store's groups, in the order they were added; [] when the store-contacts switch is off or
    the tables are not there (`site_scope` is None)."""
    from apps.inbox import contacts  # not at import: inbox is a later layer for this module

    scope = contacts.site_scope(account, site)
    if scope is None:
        return []
    return list(scope.contacts.filter(groups__isnull=False).values_list("pk", "first_name", "last_name", "mobile").distinct().order_by("pk"))


def _select(site, kind, account=None, picked=None):
    """([(contact id, name, mobile)], {reason: count}, total). "all" means All groups (ALL-GROUPS-OPTION,
    Coach Ed, 2026-11-25): every contact of this store that is in at least one of its groups, in the
    order they were added, each phone once, under the first reason that applies. Members are not
    read at all. With the store-contacts switch off there is nobody. "pick": see `_select_pick`."""
    if kind == StoreBulkBatch.PICK:
        return _select_pick(site, account, picked)
    rows = _group_contacts(site, account)
    stopped = set(InquirySmsOptOut.objects.filter(site=site).values_list("mobile", flat=True))
    left = dict.fromkeys(LEFT_OUT, 0)
    chosen, seen = [], set()
    for pk, first, last, mobile in rows:
        if mobile.startswith("+63900"):
            left[INVALID_NUMBER] += 1
        elif mobile in stopped:
            left[OPTED_OUT] += 1
        elif mobile in seen:
            left[DUPLICATES] += 1
        else:
            seen.add(mobile)
            chosen.append((pk, f"{first} {last}".strip(), mobile))
    return chosen, left, len(rows)


def audience(site, kind, account=None, picked=None):
    """What a confirm would send: the count and the counts left out by reason. Never a number."""
    chosen, left, total = _select(site, _kind(kind), account, picked)
    return {"audience": kind, "total_members": total, "will_send": len(chosen), "left_out": left}


# -- the {first_name} merge field -----------------------------------------------------------

PLACEHOLDER = "{first_name}"
BRACE_MESSAGE = "Only {first_name} can go inside { }."
_SPACES = re.compile(r" {2,}")
_BEFORE_PUNCT = re.compile(r" +([,.!?;:])")


def template_text(site, text):
    """The template as the owner typed it (trimmed): one SMS long counted as typed (`{first_name}` is
    its 12 characters), `{first_name}` the only thing allowed inside braces, and the plain-character
    and no-link rules checked on the text with the placeholder removed. Raises the 422 on `text`."""
    text = text.strip()
    if not text:
        raise sms_inbox._invalid("Write a text first.")
    rest = text.replace(PLACEHOLDER, "")
    if "{" in rest or "}" in rest:
        raise sms_inbox._invalid(BRACE_MESSAGE)
    if len(text) > sms.MAX_LENGTH:
        raise sms_inbox._invalid(f"A text is at most {sms.MAX_LENGTH} characters.")
    problem = sms.text_problem(rest)
    if problem:
        raise sms_inbox._invalid(problem)
    return text


def _without_name(template):
    """The template with the placeholder removed and the spaces tidied."""
    text = _SPACES.sub(" ", template.replace(PLACEHOLDER, ""))
    return _BEFORE_PUNCT.sub(r"\1", text).strip()


def fill_text(template, name):
    """The text one recipient gets: the placeholder replaced by `name`; with no name, or when the
    filled text would be too long or fail the text check, the template without the placeholder.
    Never raises and never cuts a name."""
    if PLACEHOLDER not in template:
        return template
    name = " ".join((name or "").split())
    if name:
        filled = template.replace(PLACEHOLDER, name)
        if not sms.text_problem(filled):
            return filled
    return _without_name(template)


def _first_word(name):
    words = (name or "").split()
    return words[0] if words else ""


def _recipient_first_name(batch, recipient):
    """The first name of the contact or member behind a recipient, read now; "" when gone or blank."""
    if recipient.contact_ref:
        from apps.inbox.models import Contact

        contact = Contact.objects.filter(pk=recipient.contact_ref, owner_kind="site", site=batch.site).first()
        return contact.first_name if contact else ""
    member = Member.objects.filter(pk=recipient.member_ref, site=batch.site).first() if recipient.member_ref else None
    return _first_word(member.full_name) if member else ""


# -- preview and test -----------------------------------------------------------------------

def preview(site, text):
    """The exact text as it will arrive (as typed, nothing added; one SMS, plain characters, no
    web link, nothing added at the end), or the 422 a person can act on."""
    full = template_text(site, text)
    return {"text": full, "length": len(full), "max_length": sms.MAX_LENGTH, "remaining": sms.MAX_LENGTH - len(full)}


def _refuse_if_blocked(site):
    code = blocked(site)
    if code:
        raise ApiError(code, BLOCKED_TEXTS[code], status=409)


def _gateway_error(reason):
    if reason in ("bulk_limit",):
        return ApiError(reason, "This store has reached today's bulk text limit. Try again tomorrow.", status=409)
    if reason in ("no_line", "site_phone_offline"):
        message = BLOCKED_TEXTS[NO_LINE] if reason == "no_line" else sms_inbox.REASON_TEXTS["site_phone_offline"]
        return ApiError(reason, message, status=409)
    if reason in ("paused", "gateway_off", "gateway_offline"):
        return ApiError(reason, sms_inbox.REASON_TEXTS.get(reason, sms_inbox.REASON_TEXTS["gateway_off"]), status=409)
    return ApiError(sms_inbox.NOT_SENT, sms_inbox.REASON_TEXTS[sms_inbox.NOT_SENT], status=409)


def test_send(site, account, text, key):
    """One text to the owner's own account mobile, from the store's phone. It counts as one of the
    day's bulk texts. The same key twice sends once."""
    _refuse_if_blocked(site)
    template = template_text(site, text)
    full = fill_text(template, _first_word(account.name))
    try:
        mobile = account.mobile.number
    except AccountMobile.DoesNotExist:
        mobile = ""
    if not mobile:
        raise ApiError("no_mobile", "Add and verify your own mobile number in your account to send a test.", status=409)
    if not cache.add(f"sbk:test:{site.pk}:{key}", 1, TEST_KEPT):
        return {"state": "queued", "repeated": True}
    try:
        result = services.send_sms(to=mobile, purpose=PURPOSE, text=full, site=site, ttl=services.SMS_STAFF_TEXT_LIFETIME)
    except ValueError:
        cache.delete(f"sbk:test:{site.pk}:{key}")
        raise sms_inbox._invalid("That text cannot be sent.") from None
    if result.outcome != sms.QUEUED:
        cache.delete(f"sbk:test:{site.pk}:{key}")
        raise _gateway_error(result.reason)
    return {"state": "queued", "repeated": False}


# -- the batch ------------------------------------------------------------------------------

def _waiting(batch):
    return max(batch.size - batch.sent - batch.failed - batch.not_sent, 0)


def paused_reason(batch):
    """For an active batch, why nothing is going out right now: `outside_window`, `daily_cap`,
    `phone_offline`; else ""."""
    if batch.state not in StoreBulkBatch.ACTIVE:
        return ""
    if not in_window():
        return "outside_window"
    if _at_cap(batch.site):
        return "daily_cap"
    if not sms.gateway_online(batch.site, sms.STORE_BULK):
        return "phone_offline"
    return ""


def _resendable(batch):
    """The recipients a Continue would send again: not sent only because the batch ended (their reason is
    the batch's own end reason). Never one left out for STOP, a bad or changed number, or a removed person."""
    if batch.state != StoreBulkBatch.ENDED or not batch.end_reason:
        return StoreBulkRecipient.objects.none()
    return batch.recipients.filter(state=StoreBulkRecipient.NOT_SENT, reason=batch.end_reason)


def _cannot_continue(batch):
    """Why an ended batch cannot be continued now: (code, message), or None when it can."""
    if batch.state != StoreBulkBatch.ENDED:
        return "not_ended", "Only an ended batch can continue."
    code = blocked(batch.site) or _end_reason(batch.site)
    if code:
        return code, BLOCKED_TEXTS.get(code) or REASON_TEXTS[code]
    if StoreBulkBatch.objects.filter(site=batch.site, state__in=StoreBulkBatch.ACTIVE).exists():
        return "batch_running", "A batch is already sending. Wait for it to finish or stop it."
    return None


def batch_out(batch):
    waiting = _waiting(batch)
    active = batch.state in StoreBulkBatch.ACTIVE
    continue_count = _resendable(batch).count() if batch.state == StoreBulkBatch.ENDED else 0
    return {
        "id": batch.public_id,
        "state": batch.state,
        "created_at": batch.created_at.isoformat(),
        "scheduled_at": batch.scheduled_at.isoformat() if batch.scheduled_at else None,
        "audience": batch.audience,
        "text": batch.text,
        "size": batch.size,
        "sent": batch.sent,
        "failed": batch.failed,
        "not_sent": batch.not_sent,
        "waiting": waiting,
        "left_out": batch.left_out,
        "end_reason": batch.end_reason,
        "end_message": REASON_TEXTS.get(batch.end_reason, "") if batch.end_reason else "",
        "paused_reason": paused_reason(batch) if active else "",
        "seconds_left": waiting * (SECONDS[0] + SECONDS[1]) // 2 if active else 0,
        "stopped_at": batch.stopped_at.isoformat() if batch.stopped_at else None,
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
        # CONTINUE (2026-11-27): how many of an ended batch's texts can still go, and whether Continue is allowed now.
        "continue_count": continue_count,
        "can_continue": bool(continue_count) and _cannot_continue(batch) is None,
    }


def _audit(batch, event, actor=None, **counts):
    """The audit entry of a batch: its id and counts and a reason code, never the text or a number."""
    account_services.record_event(actor or batch.created_by, event, batch=batch.public_id, site=batch.site.public_id, **counts)


def make_recipients(batch, kind, chosen):
    """The recipient rows of a batch, one per chosen (contact id, name, mobile), in order. Every audience
    ("all" = All groups, and "pick") is made of contacts, so each row carries `contact_ref`; `kind` is kept
    for the callers."""
    StoreBulkRecipient.objects.bulk_create(
        [StoreBulkRecipient(
            batch=batch, position=i, name=name[:120], mobile=mobile,
            contact_ref=pk)
         for i, (pk, name, mobile) in enumerate(chosen)],
        batch_size=500,
    )


def confirm(site, account, data):
    """Start a batch. Returns (the batch dict, created). `data`: text, audience,
    idempotency_key, confirm (true), expected_count (optional)."""
    key = data["idempotency_key"]
    existing = StoreBulkBatch.objects.filter(site=site, request_id=key).first()
    if existing:
        return batch_out(existing), False
    if data.get("confirm") is not True:
        raise ApiError("invalid", "Tick the box to confirm.", status=422, fields={"confirm": ["Tick the box to confirm."]})
    kind = _kind(data["audience"])
    _refuse_if_blocked(site)
    full = template_text(site, data["text"])
    if StoreBulkBatch.objects.filter(site=site, state__in=StoreBulkBatch.ACTIVE).exists():
        raise ApiError("batch_running", "A batch is already sending. Wait for it to finish or stop it.", status=409)
    picked = {"groups": _ids(data.get("groups"), "groups"), "contacts": _ids(data.get("contacts"), "contacts")} if kind == StoreBulkBatch.PICK else {}
    chosen, left, _ = _select(site, kind, account, picked)
    if not chosen:
        raise ApiError("empty_audience", "There is nobody to send to.", status=409)
    expected = data.get("expected_count")
    if expected is not None and expected != len(chosen):
        message = "Your members changed since you checked. Check the count again."
        raise ApiError(
            "audience_changed", message, status=409,
            body={"error": {"code": "audience_changed", "message": message, "will_send": len(chosen)}},
        )
    try:
        with transaction.atomic():
            batch = StoreBulkBatch.objects.create(
                site=site, created_by=account, request_id=key, text=full,
                audience=kind, picked=picked, size=len(chosen), left_out=left,
            )
            make_recipients(batch, kind, chosen)
            _audit(batch, "store_bulk_confirmed", size=batch.size, audience=kind, left_out=left)
    except IntegrityError:
        again = StoreBulkBatch.objects.filter(site=site, request_id=key).first()
        if again:
            return batch_out(again), False
        raise ApiError("batch_running", "A batch is already sending. Wait for it to finish or stop it.", status=409) from None
    return batch_out(batch), True


# -- the driver -----------------------------------------------------------------------------

def _mask_numbers(batch):
    for recipient in batch.recipients.exclude(mobile__contains="*"):
        recipient.mobile = mask_mobile(recipient.mobile)
        recipient.save(update_fields=["mobile"])


def settle(batch):
    """Bring the batch up to date with what the phone reported, write its counts and finish it when
    nothing is waiting. Idempotent; the caller holds the batch's row lock."""
    recipients = batch.recipients
    queued = list(recipients.filter(state=StoreBulkRecipient.QUEUED).values_list("pk", "send_record"))
    if queued:
        found = services.sms_statuses([record for _, record in queued])
        for pk, record in queued:
            status, reason = found.get(record, ("gone", "record_gone"))
            if status == "sent":
                recipients.filter(pk=pk).update(state=StoreBulkRecipient.SENT)
            elif status in ("failed", "expired", "refused", "gone"):
                recipients.filter(pk=pk).update(state=StoreBulkRecipient.FAILED, reason=(reason or status)[:40])
    totals = {row["state"]: row["n"] for row in recipients.values("state").annotate(n=Count("pk"))}
    batch.sent = totals.get("sent", 0)
    batch.failed = totals.get("failed", 0)
    batch.not_sent = totals.get("not_sent", 0)
    fields = ["sent", "failed", "not_sent"]
    over = not totals.get("waiting") and not totals.get("queued")
    finished = over and batch.state in StoreBulkBatch.ACTIVE
    if finished:
        batch.state, batch.finished_at = StoreBulkBatch.FINISHED, timezone.now()
        fields += ["state", "finished_at"]
    batch.save(update_fields=fields)
    if over:
        _mask_numbers(batch)
    if finished:
        _audit(batch, "store_bulk_ended", reason="finished", size=batch.size, sent=batch.sent, failed=batch.failed, not_sent=batch.not_sent)


def _end(batch, reason, state, *, actor=None):
    """Give up the rest of an active batch with `reason`: waiting texts are not sent, texts the
    phone has not taken are taken back. The caller holds the batch's row lock."""
    batch.recipients.filter(state=StoreBulkRecipient.WAITING).update(state=StoreBulkRecipient.NOT_SENT, reason=reason)
    for recipient in batch.recipients.filter(state=StoreBulkRecipient.QUEUED):
        if services.cancel_sms(recipient.send_record):
            recipient.state, recipient.reason = StoreBulkRecipient.NOT_SENT, reason
            recipient.save(update_fields=["state", "reason"])
    batch.state, batch.end_reason = state, reason
    now = timezone.now()
    if state == StoreBulkBatch.STOPPED:
        batch.stopped_at = now
    else:
        batch.finished_at = now
    batch.save(update_fields=["state", "end_reason", "stopped_at", "finished_at"])
    settle(batch)  # counts, and the masking of numbers no text is waiting for any more
    counts = {"size": batch.size, "sent": batch.sent, "failed": batch.failed, "not_sent": batch.not_sent}
    if state == StoreBulkBatch.STOPPED:
        _audit(batch, "store_bulk_stopped", actor=actor or batch.created_by, **counts)
    else:
        _audit(batch, "store_bulk_ended", reason=reason, **counts)


def _skip_reason(batch, recipient):
    """Why this member is not texted any more, or "" when they still are: the member is still the
    store's, with the same verified number and not on the STOP list."""
    if recipient.contact_ref:
        from apps.inbox.models import Contact

        contact = Contact.objects.filter(pk=recipient.contact_ref, owner_kind="site", site=batch.site).first()
        if contact is None:
            return "contact_removed"
        if contact.mobile != recipient.mobile:
            return "number_changed"
        return OPTED_OUT if sms_inbox.opted_out(batch.site, recipient.mobile) else ""
    member = Member.objects.filter(pk=recipient.member_ref, site=batch.site).first() if recipient.member_ref else None
    if member is None:
        return "member_removed"
    if member.mobile != recipient.mobile:
        return "number_changed"
    if member.mobile_state != "verified":
        return UNVERIFIED
    if sms_inbox.opted_out(batch.site, recipient.mobile):
        return OPTED_OUT
    return ""


def _drive(batch, now):
    settle(batch)
    if batch.state not in StoreBulkBatch.ACTIVE:
        return
    reason = _end_reason(batch.site)
    if reason:
        _end(batch, reason, StoreBulkBatch.ENDED)
        return
    if not in_window(now):
        return  # waits for the morning
    if batch.recipients.filter(state=StoreBulkRecipient.QUEUED).exists():
        return  # one at a time: the last is not reported yet
    if _at_cap(batch.site):
        return  # waits for tomorrow's room
    for _ in range(MAX_SKIPS):
        following = batch.recipients.filter(state=StoreBulkRecipient.WAITING).order_by("position").first()
        if following is None:
            settle(batch)
            return
        skip = _skip_reason(batch, following)
        if skip:
            following.state, following.reason = StoreBulkRecipient.NOT_SENT, skip
            following.save(update_fields=["state", "reason"])
            continue
        break
    else:
        settle(batch)
        return
    text = fill_text(batch.text, _recipient_first_name(batch, following)) if PLACEHOLDER in batch.text else batch.text
    try:
        result = services.send_sms(
            to=following.mobile, purpose=PURPOSE, text=text, site=batch.site,
            ttl=services.SMS_STAFF_TEXT_LIFETIME,
        )
    except ValueError:
        following.state, following.reason = StoreBulkRecipient.NOT_SENT, sms_inbox.TEXT_INVALID
        following.save(update_fields=["state", "reason"])
        settle(batch)
        return
    if result.outcome == sms.QUEUED and result.message is not None:
        following.state, following.send_record = StoreBulkRecipient.QUEUED, result.message.public_id
        following.save(update_fields=["state", "send_record"])
        if batch.state == StoreBulkBatch.WAITING:
            batch.state = StoreBulkBatch.SENDING
            batch.save(update_fields=["state"])
    elif result.reason in ("bulk_limit", "paused", "gateway_offline", "gateway_off", "site_phone_offline", "no_line"):
        return  # nothing is lost: the next poll tries again
    else:
        following.state, following.reason = StoreBulkRecipient.NOT_SENT, (result.reason or sms_inbox.NOT_SENT)[:40]
        following.save(update_fields=["state", "reason"])
    settle(batch)


def _settle_over(site):
    """Settle the batches of the store that are over but whose last texts the phone had not reported
    yet, so their numbers are masked once those are in."""
    holding = StoreBulkRecipient.objects.filter(
        batch__site=site, state=StoreBulkRecipient.QUEUED).exclude(batch__state__in=StoreBulkBatch.ACTIVE)
    for batch in StoreBulkBatch.objects.select_for_update().select_related("site", "created_by").filter(
            pk__in=holding.values("batch_id")):
        settle(batch)


def feed(site, now=None):
    """Queue the next text of the store's active batch. Runs inside the store's phone's poll."""
    from apps.inquiries import store_bulk_schedule  # not at import: that module imports this one

    now = now or timezone.now()
    store_bulk_schedule.start_due(site=site, now=now)  # a due scheduled batch starts on the phone's pull
    with transaction.atomic():
        batch = StoreBulkBatch.objects.select_for_update().select_related("site", "created_by").filter(
            site=site, state__in=StoreBulkBatch.ACTIVE).first()
        if batch is not None:
            _drive(batch, now)
        else:
            _settle_over(site)


PLATFORM_FEED_SITES = 50  # stores fed in one poll of the platform phone


def feed_platform(device, now=None):
    """The platform phone is asking (ONE-PHONE, 2026-11-26): queue the next text of each store whose
    bulk goes from this phone, i.e. a store with an active (or due scheduled) batch and no phone of
    its own. One query for the stores, each fed once. A store with its own phone is fed by its own
    phone's poll and never here. (The beat task `inquiries.store_bulk_start_due` also starts due
    scheduled batches; they are included here so a store with no batch yet is reached too.)"""
    from apps.sites.models import Site

    now = now or timezone.now()
    live = StoreBulkBatch.objects.filter(state__in=StoreBulkBatch.ACTIVE).values_list("site_id", flat=True)
    due = StoreBulkBatch.objects.filter(
        state=StoreBulkBatch.SCHEDULED, scheduled_at__lte=now).values_list("site_id", flat=True)
    ids = sorted(set(live.distinct()) | set(due.distinct()))
    done = 0
    for site in Site.objects.filter(pk__in=ids[:PLATFORM_FEED_SITES * 4]).order_by("pk"):
        if done >= PLATFORM_FEED_SITES:
            break
        if sms.bulk_phone(site) == device:
            feed(site, now)
            done += 1
    return done


# -- stop, read -----------------------------------------------------------------------------

def _find(site, public_id, *, lock=False):
    if not isinstance(public_id, str) or not is_public_id(public_id, "sbk"):
        raise ApiError("not_found", "Not found.", status=404)
    rows = StoreBulkBatch.objects.select_related("site", "created_by").filter(site=site, public_id=public_id)
    batch = (rows.select_for_update() if lock else rows).first()
    if batch is None:
        raise ApiError("not_found", "Not found.", status=404)
    return batch


def stop(site, account, public_id):
    with transaction.atomic():
        batch = _find(site, public_id, lock=True)
        if batch.state not in StoreBulkBatch.ACTIVE:
            raise ApiError("not_running", "That batch is not sending.", status=409)
        settle(batch)
        if batch.state not in StoreBulkBatch.ACTIVE:  # it finished while the owner looked
            raise ApiError("not_running", "That batch is not sending.", status=409)
        _end(batch, "stopped", StoreBulkBatch.STOPPED, actor=account)
    return batch_out(batch)


def _current_numbers(site, recipients):
    """{recipient pk: the person's current full mobile} for the recipients whose contact (or member) is still
    the store's and whose current number still matches the masked one kept in the batch; the others are left out."""
    from apps.inbox.models import Contact

    contact_refs = {r.contact_ref for r in recipients if r.contact_ref}
    member_refs = {r.member_ref for r in recipients if r.member_ref and not r.contact_ref}
    contacts, members = {}, {}
    refs = sorted(contact_refs)
    for i in range(0, len(refs), CHUNK):
        contacts.update(Contact.objects.filter(pk__in=refs[i:i + CHUNK], owner_kind="site", site=site).values_list("pk", "mobile"))
    refs = sorted(member_refs)
    for i in range(0, len(refs), CHUNK):
        members.update(Member.objects.filter(pk__in=refs[i:i + CHUNK], site=site).values_list("pk", "mobile"))
    found = {}
    for r in recipients:
        mobile = contacts.get(r.contact_ref) if r.contact_ref else members.get(r.member_ref)
        if mobile and not mobile.startswith("+63900") and mask_mobile(mobile) == r.mobile:
            found[r.pk] = mobile
    return found


def continue_batch(site, account, public_id):
    """Send the rest of an ENDED batch (STORE-BULK-CONTINUE, Coach Ed 2026-11-27). Only the recipients not
    sent because the batch ended go back to waiting, with their number read again from the contact (or
    member) behind them; the same row becomes active again. The usual per-text checks apply at send time."""
    with transaction.atomic():
        batch = _find(site, public_id, lock=True)
        settle(batch)
        refusal = _cannot_continue(batch)
        if refusal:
            raise ApiError(refusal[0], refusal[1], status=409)
        rows = list(_resendable(batch).order_by("position"))
        numbers = _current_numbers(site, rows)
        back = [r for r in rows if r.pk in numbers]
        if not back:
            raise ApiError("nothing_to_continue", "There is nothing left to send.", status=409)
        for i in range(0, len(back), CHUNK):
            for r in back[i:i + CHUNK]:
                r.mobile, r.state, r.reason = numbers[r.pk], StoreBulkRecipient.WAITING, ""
            StoreBulkRecipient.objects.bulk_update(back[i:i + CHUNK], ["mobile", "state", "reason"])
        batch.state, batch.end_reason, batch.finished_at = StoreBulkBatch.WAITING, "", None
        batch.save(update_fields=["state", "end_reason", "finished_at"])
        settle(batch)  # the counts, in place
        _audit(batch, "store_bulk_continued", actor=account, count=len(back), size=batch.size, sent=batch.sent, failed=batch.failed, not_sent=batch.not_sent)
    return batch_out(batch)


def refresh(site):
    """Settle the store's active batch without queuing anything, so a read is true with the phone away.
    A scheduled batch of the store that is due starts first (so it works with no beat running)."""
    from apps.inquiries import store_bulk_schedule  # not at import: that module imports this one

    store_bulk_schedule.start_due(site=site)
    with transaction.atomic():
        batch = StoreBulkBatch.objects.select_for_update().select_related("site", "created_by").filter(
            site=site, state__in=StoreBulkBatch.ACTIVE).first()
        if batch is not None:
            settle(batch)
        else:
            _settle_over(site)


def _limit_of(value):
    return max(1, min(value if value is not None else PAGE[0], PAGE[1]))


def active_batch(site):
    batch = StoreBulkBatch.objects.filter(site=site, state__in=StoreBulkBatch.ACTIVE).select_related("site").first()
    return batch_out(batch) if batch else None


COST_NOTE = "Texts go out from your own phone and SIM. Your carrier's normal SMS rates apply."


def status(site):
    """The bulk screen's state: the gateway's numbers and room, why a batch cannot start,
    the window and the pace, and the active batch."""
    refresh(site)
    base = sms_inbox.status(site)
    cap = services.sms_store_bulk_status(site)
    return {
        "available": True, "blocked": blocked(site), "blocked_message": BLOCKED_TEXTS.get(blocked(site), ""),
        "phone_online": cap["phone_online"], "daily_limit": cap["daily_limit"], "used_today": cap["used_today"],
        "left_today": cap["left_today"], "prefix": base["prefix"], "max_length": base["max_length"],
        "window": {"start": WINDOW[0].strftime("%H:%M"), "end": WINDOW[1].strftime("%H:%M"), "timezone": "Asia/Manila"},
        "in_window": in_window(), "seconds_between": {"min": SECONDS[0], "max": SECONDS[1]},
        # ONE-PHONE (2026-11-26): empty for a store whose bulk goes from the platform phone.
        "cost_note": COST_NOTE if sms._lined(site) is not None else "",
        "active_batch": active_batch(site),
    }


def list_batches(site, *, cursor="", limit=None):
    refresh(site)
    rows = StoreBulkBatch.objects.filter(site=site).exclude(
        state__in=(StoreBulkBatch.SCHEDULED, StoreBulkBatch.CANCELLED)).select_related("site").order_by("-pk")
    if cursor:
        last = StoreBulkBatch.objects.filter(site=site, public_id=cursor).first() if is_public_id(cursor, "sbk") else None
        if last is None:
            raise ApiError("invalid_cursor", "That page marker is not valid.", status=422)
        rows = rows.filter(pk__lt=last.pk)
    size = _limit_of(limit)
    items = list(rows[: size + 1])
    more = len(items) > size
    items = items[:size]
    from apps.inquiries import store_bulk_schedule  # not at import: that module imports this one

    return store_bulk_schedule.with_names(site, items), (items[-1].public_id if more else None)


def open_batch(site, public_id, *, cursor="", limit=None):
    refresh(site)
    with transaction.atomic():
        batch = _find(site, public_id, lock=True)
        if batch.state in (StoreBulkBatch.SCHEDULED, StoreBulkBatch.CANCELLED):  # see store_bulk_schedule
            raise ApiError("not_found", "Not found.", status=404)
        settle(batch)
    answer = batch_out(batch)
    if cursor and (not isinstance(cursor, str) or not cursor.isdigit() or len(cursor) > 9):
        raise ApiError("invalid_cursor", "That page marker is not valid.", status=422)
    size = _limit_of(limit)
    items = list(batch.recipients.filter(position__gt=int(cursor) if cursor else -1).order_by("position")[: size + 1])
    more = len(items) > size
    items = items[:size]
    answer["results"] = [
        {"name": r.name, "mobile": mask_mobile(r.mobile), "state": r.state, "reason": r.reason,
         "reason_text": REASON_TEXTS.get(r.reason, "") if r.reason else ""}
        for r in items
    ]
    answer["next"] = str(items[-1].position) if more else None
    return answer
