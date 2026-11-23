"""A store's text (SMS) conversations with its own members, in the dash Inbox (SMS-OWNER, task
z8uq9mhbf4; plan hq-plan-sms-owner-z8uq9mh4f7).

A conversation is a stored one (`provider="sms"`, refs `sm_`), one per store and mobile number. The
store writes only to a verified member of the store, one member at a time; a text goes through the
SMS gateway as the purposes `store_compose` and `store_reply` and is paced and capped per store
(apps/notifications/sms.py). An incoming text on the shared phone goes to the store that texted
that number last (route_incoming), else to HQ as before.

Nothing here logs or audits a number or a text. A number is held only in the conversation's
`contact_mobile`, which the retention rules blank with the rest of the personal fields, and in
InquirySmsOptOut. The thread shows the number masked.

Everything is dark behind INQUIRY_SMS_ENABLED (apps/inquiries/switch.py): off, or before
inquiries.0010 is applied, no function here reads or writes a text table except the disposal ones
in retention.py, which only delete."""
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.common.errors import ApiError
from apps.common.masking import mask_mobile
from apps.inbox.switch import inbox_on
from apps.members import promo
from apps.inquiries import live, outcomes
from apps.inquiries.models import (
    InquiryConversation, InquiryMessage, InquiryReply, InquirySmsOptOut, InquirySmsSettings,
)
from apps.inquiries.switch import sms_ready
from apps.members.messaging import NEUTRAL_NAME, sms_store_name
from apps.members.models import Member
from apps.notifications import services, sms
from apps.notifications.models import SmsMessage

logger = logging.getLogger(__name__)

NAME_LIMIT = 30  # characters of the store's name in the text's prefix
ROUTE_WITHIN = timedelta(days=7)  # an incoming text goes to the store that texted that number in the last 7 days
ROUTED_PURPOSES = ("store_reply", "store_compose", "inbox_reply", "inbox_compose", "inbox_bulk")
LIVE_STATUSES = (SmsMessage.Status.QUEUED, SmsMessage.Status.DISPATCHED, SmsMessage.Status.SENT)
MAX_BODY = 2000
CUSTOMER = "Customer"

# What the owner is told when a text did not go, fixed text per code. Never a provider's words.
OPTED_OUT, SMS_OFF, REPLIES_OFF = "opted_out", "sms_off", "replies_off"
SITE_HELD = "site_held"  # HQ has suspended the store (Site.operator_hold)
TEXT_INVALID, NOT_SENT = "text_invalid", "not_sent"
REASON_TEXTS = {
    "gateway_offline": "The store's phone is offline right now. Try again shortly.",
    "site_phone_offline": "Your phone is offline right now. Nothing was sent. Open the app on your phone and try again.",
    "gateway_off": "Texting is not available right now.",
    "paused": "Texting is paused right now. Try again later.",
    "site_limit": "This store has reached today's text limit. Try again tomorrow.",
    "site_hour_limit": "This store has reached this hour's text limit. Try again in the next hour.",
    "stores_limit": "Stores have reached today's text limit. Try again tomorrow.",
    "staff_limit": "Texting has reached today's limit. Try again tomorrow.",
    "expired": "The text was not sent in time.",
    TEXT_INVALID: "That text cannot be sent.",
    NOT_SENT: "The text could not be sent.",
}
# The audit row's code (InquiryReply.error_code has a fixed list of choices).
AUDIT_CODE = {
    "gateway_offline": outcomes.PROVIDER_DOWN, "site_phone_offline": outcomes.PROVIDER_DOWN, "gateway_off": outcomes.PROVIDER_DOWN, "paused": outcomes.PROVIDER_DOWN,
    "site_limit": outcomes.RATE_LIMITED, "site_hour_limit": outcomes.RATE_LIMITED,
    "stores_limit": outcomes.RATE_LIMITED, "staff_limit": outcomes.RATE_LIMITED,
    "expired": outcomes.PROVIDER_DOWN, TEXT_INVALID: outcomes.BAD_RESPONSE, NOT_SENT: outcomes.PROVIDER_DOWN,
}
CONFLICT = {OPTED_OUT: "That number has asked for no more texts from this store.",
            SMS_OFF: "Text conversations are switched off for this store.",
            REPLIES_OFF: "Texting is not available right now.",
            SITE_HELD: "Texting is stopped while your store is paused by NegosyoOnline."}


# -- availability ---------------------------------------------------------------------------

def enabled(site):
    row = InquirySmsSettings.objects.filter(site=site).first()
    return bool(row and row.enabled)


def incoming_ready():
    """True when the shared phone passes on the texts it receives, so a customer's answer can arrive."""
    return bool(inbox_on() and services.sms_phone_forwards())


def unavailable(site):
    """Why the store cannot write now: `site_held`, `sms_off`, `replies_off`, or "" when it can."""
    if site.operator_hold:
        return SITE_HELD
    if not enabled(site):
        return SMS_OFF
    if not incoming_ready():
        return REPLIES_OFF
    return ""


def opted_out(site, mobile):
    return bool(mobile) and InquirySmsOptOut.objects.filter(site=site, mobile=mobile).exists()


def prefix_of(site):
    """The words every text of this store starts with, "Name: ". A name that makes the text
    unsendable is swapped for the neutral one; None when even that cannot be sent."""
    for name in (sms_store_name(site)[:NAME_LIMIT].strip(), NEUTRAL_NAME):
        prefix = f"{name}: "
        if not sms.text_problem(prefix + "x"):
            return prefix
    return None


def max_length(site):
    prefix = prefix_of(site)
    return None if prefix is None else sms.MAX_LENGTH - len(prefix)


def status(site):
    """The composer's status for a store: the gateway's numbers, the prefix and the room left."""
    prefix = prefix_of(site)
    return {
        **services.sms_store_status(site), "enabled": enabled(site), "prefix": prefix or "",
        "max_length": None if prefix is None else sms.MAX_LENGTH - len(prefix), "incoming_ready": incoming_ready(),
    }


# -- conversations --------------------------------------------------------------------------

def _member_of(site, mobile):
    return Member.objects.filter(site=site, mobile=mobile).first()


def _conversation(site, mobile, member=None, now=None):
    """(conversation, created): the store's text conversation with this number. A race for the
    one row the unique constraint allows is settled by reading the winner's."""
    found = InquiryConversation.objects.filter(site=site, provider=InquiryConversation.SMS, contact_mobile=mobile).first()
    if found:
        return found, False
    member = member or _member_of(site, mobile)
    name = member.full_name if member else ""
    try:
        with transaction.atomic():
            return InquiryConversation.objects.create(
                site=site, provider=InquiryConversation.SMS, external_id=_new_ref(), stores_history=True,
                display_name=name, contact_name=name, contact_mobile=mobile,
                status=InquiryConversation.OPEN, last_message_at=now,
            ), True
    except IntegrityError:
        return InquiryConversation.objects.get(
            site=site, provider=InquiryConversation.SMS, contact_mobile=mobile), False


def _new_ref():
    from apps.inquiries import stored
    return stored.new_ref(InquiryConversation.SMS)


def _sendable_member(site, member_id):
    """The verified member this store may write to, or None (404 for anything else)."""
    member = Member.objects.filter(site=site, public_id=member_id or "").first()
    if member is None or member.mobile_state != "verified" or member.mobile.startswith("+63900"):
        return None
    return member


# -- sending --------------------------------------------------------------------------------

def _invalid(message):
    return ApiError("invalid", message, status=422, fields={"text": [message]})


def _full_text(site, text):
    """The text as sent, prefix included. Raises the 422 a person can act on."""
    prefix = prefix_of(site)
    if prefix is None:
        raise _invalid("This store's name cannot be used in a text.")
    text = text.strip()
    if not text:
        raise _invalid("Write a text first.")
    room = sms.MAX_LENGTH - len(prefix)
    if len(text) > room:
        raise _invalid(f"A text is at most {room} characters (the store's name takes the rest).")
    problem = sms.text_problem(prefix + text)
    if problem:
        raise _invalid(problem)
    return prefix + text


def _reply_out(reply, message=None):
    message = message or reply.message
    code = message.error_code if message else ""
    state = "failed" if reply.state == InquiryReply.FAILED else "sending"
    out = {"state": state, "error_code": code, "created_at": reply.created_at.isoformat()}
    if code:
        out["message"] = REASON_TEXTS.get(code, REASON_TEXTS[NOT_SENT])
    return out


def _earlier(existing, mobile):
    if existing.conversation.contact_mobile != mobile:
        return "idempotency_conflict", None
    return outcomes.OK, existing


def _send(site, account, conversation, text, key, purpose):
    """Write the outgoing message and audit row, then hand the text to the gateway. Returns
    (OK, reply row) or ("idempotency_conflict", None). A text the gateway refuses is a failed
    message, not an error: the owner sees the fixed reason."""
    existing = InquiryReply.objects.filter(site=site, idempotency_key=key).select_related("conversation", "message").first()
    if existing:
        return _earlier(existing, conversation.contact_mobile)
    now = timezone.now()
    try:
        with transaction.atomic():
            message = InquiryMessage.objects.create(
                site=site, conversation=conversation, direction=InquiryMessage.OUT,
                sender_kind=InquiryMessage.OWNER, body=text, created_at=now,
            )
            row = InquiryReply.objects.create(
                site=site, conversation=conversation, author=account, idempotency_key=key,
                body_length=len(text), message=message,
            )
    except IntegrityError:  # the same key arrived twice at once: the first one wins
        existing = InquiryReply.objects.select_related("conversation", "message").get(site=site, idempotency_key=key)
        return _earlier(existing, conversation.contact_mobile)
    reason = ""
    queued = None
    try:
        result = services.send_sms(
            to=conversation.contact_mobile, purpose=purpose, text=text, site=site, ttl=services.SMS_STAFF_TEXT_LIFETIME,
        )
        if result.outcome == sms.QUEUED and result.message is not None:
            queued = result.message
        else:
            reason = result.reason or NOT_SENT
    except ValueError:
        reason = TEXT_INVALID
    except Exception as error:  # noqa: BLE001 - only the class name is kept
        logger.error("Store text not queued (%s).", type(error).__name__)
        reason = NOT_SENT
    if queued is not None:
        InquiryMessage.objects.filter(pk=message.pk).update(external_id=queued.public_id)
        InquiryConversation.objects.filter(pk=conversation.pk).update(
            last_message_at=now, unread=False, status=InquiryConversation.OPEN,
        )
        row.state = InquiryReply.SENT
        row.save(update_fields=["state"])
        live.publish(site, conversation.external_id, live.KIND_MESSAGE, now)
    else:
        InquiryMessage.objects.filter(pk=message.pk).update(delivery_state="failed", error_code=reason)
        row.state, row.error_code = InquiryReply.FAILED, AUDIT_CODE.get(reason, outcomes.PROVIDER_DOWN)
        row.save(update_fields=["state", "error_code"])
        message.error_code, message.delivery_state = reason, "failed"
        live.publish(site, conversation.external_id, None, event=live.MESSAGE_STATUS)
    row.message = message
    message.error_code = reason
    return outcomes.OK, row


def _refuse_if_blocked(site, mobile):
    code = unavailable(site)
    if code:
        raise ApiError(code, CONFLICT[code], status=409)
    if opted_out(site, mobile):
        raise ApiError(OPTED_OUT, CONFLICT[OPTED_OUT], status=409)


def compose(site, account, member_id, text, key):
    """Start, or continue, the text conversation with one verified member of the store. Returns
    (code, dict): NOT_FOUND for a member that is not the store's or cannot be texted; otherwise
    {ref, new_conversation, state, error_code, message?, created_at}."""
    member = _sendable_member(site, member_id)
    if member is None:
        return outcomes.NOT_FOUND, None
    existing = InquiryReply.objects.filter(site=site, idempotency_key=key).select_related("conversation", "message").first()
    if existing:
        code, row = _earlier(existing, member.mobile)
        if row is None:
            return code, None
        return outcomes.OK, {"ref": existing.conversation.external_id, "new_conversation": False, **_reply_out(row)}
    _refuse_if_blocked(site, member.mobile)
    body = _full_text(site, text)
    conversation, created = _conversation(site, member.mobile, member)
    code, row = _send(site, account, conversation, body, key, "store_compose" if created or not _has_inbound(conversation) else "store_reply")
    if row is None:
        return code, None
    return outcomes.OK, {"ref": conversation.external_id, "new_conversation": created, **_reply_out(row)}


def _has_inbound(conversation):
    return InquiryMessage.objects.filter(conversation=conversation, direction=InquiryMessage.IN).exists()


def reply(site, account, conversation, text, key):
    """The owner's reply on an existing text conversation (called from stored.reply). Returns
    (OK, dict) or ("idempotency_conflict", None)."""
    existing = InquiryReply.objects.filter(site=site, idempotency_key=key).select_related("conversation", "message").first()
    if existing:
        code, row = _earlier(existing, conversation.contact_mobile)
        return (code, None) if row is None else (outcomes.OK, _reply_out(row))
    _refuse_if_blocked(site, conversation.contact_mobile)
    body = _full_text(site, text)
    code, row = _send(site, account, conversation, body, key, "store_reply")
    if row is None:
        return code, None
    return outcomes.OK, _reply_out(row)


# -- reading --------------------------------------------------------------------------------

def refresh(conversation, messages):
    """Bring the outgoing texts still waiting for the phone up to date with the gateway's records,
    in one query, and write the result back to the messages. A message not waiting is left alone."""
    waiting = [m for m in messages if m.direction == InquiryMessage.OUT and not m.delivery_state and m.external_id]
    if not waiting:
        return
    found = services.sms_statuses([m.external_id for m in waiting])
    for message in waiting:
        state = found.get(message.external_id)
        if state is None:
            continue
        gateway_status, reason = state
        if gateway_status in LIVE_STATUSES and gateway_status != SmsMessage.Status.SENT:
            continue  # queued or with the phone: still sending
        if gateway_status == SmsMessage.Status.SENT:
            delivery, code = "sent", ""
        else:
            delivery, code = "failed", reason if reason in REASON_TEXTS else NOT_SENT
            if gateway_status == SmsMessage.Status.EXPIRED and not reason:
                code = "expired"
        InquiryMessage.objects.filter(pk=message.pk).update(delivery_state=delivery, error_code=code)
        message.delivery_state, message.error_code = delivery, code
        if delivery == "failed":
            InquiryReply.objects.filter(message=message, state=InquiryReply.SENT).update(
                state=InquiryReply.FAILED, error_code=AUDIT_CODE.get(code, outcomes.PROVIDER_DOWN),
            )


def message_error(message):
    """The owner-facing text for an outgoing message that failed, or ""."""
    return REASON_TEXTS.get(message.error_code, REASON_TEXTS[NOT_SENT]) if message.delivery_state == "failed" else ""


def thread_extras(site, conversation):
    """What an owner's thread of a text conversation adds: the masked number, the room, the
    opt-out flag and whether a reply can be written now."""
    out_of = opted_out(site, conversation.contact_mobile)
    blocked = unavailable(site)
    reason = ""
    if conversation.status != InquiryConversation.OPEN:
        reason = "closed"
    elif out_of:
        reason = OPTED_OUT
    elif blocked:
        reason = blocked
    return {
        "contact_mobile": mask_mobile(conversation.contact_mobile), "max_length": max_length(site),
        "prefix": prefix_of(site) or "", "opted_out": out_of, "reply": {"open": not reason, "reason": reason},
    }


# -- incoming -------------------------------------------------------------------------------

def route_incoming(number, now=None):
    """The Site whose conversation an incoming text from `number` belongs to, or None (the text
    goes to HQ as before). Only while the switches are on and the tables are there. The last text
    sent to the number in the last 7 days among the store's and HQ's reply purposes decides: it
    must be a store text, to a store that is not closed and has text conversations on. Codes are
    never looked at, so a code sent after a store text does not take the reply from the store."""
    if not sms_ready():
        return None
    now = now or timezone.now()
    last = (
        SmsMessage.objects.filter(
            recipient=number, purpose__in=ROUTED_PURPOSES, status__in=LIVE_STATUSES,
            created_at__gte=now - ROUTE_WITHIN,
        )
        .annotate(at=Coalesce("sent_at", "dispatched_at", "created_at"))
        .order_by("-at", "-pk").select_related("site").first()
    )
    if last is None or last.purpose not in sms.STORE_PURPOSES or last.site is None:
        return None
    site = last.site
    if getattr(site, "deleted_at", None) is not None or not enabled(site):
        return None
    return site


def seen_refs(device, refs):
    """The gateway refs of these texts already stored in a store's conversation, for the dedupe."""
    if not sms_ready() or not refs:
        return set()
    wanted = {f"sms:{device.pk}:{ref}": ref for ref in refs}
    found = InquiryMessage.objects.filter(
        direction=InquiryMessage.IN, external_id__in=list(wanted),
    ).values_list("external_id", flat=True)
    return {wanted[key] for key in found}


def store_incoming(site, device, number, ref, text, occurred_at, stop):
    """Store one incoming text in the store's conversation. Returns False when this text was
    stored already. A STOP also puts the number on the store's own opt-out list (never HQ's)."""
    conversation, _ = _conversation(site, number, now=occurred_at)
    try:
        with transaction.atomic():
            message = InquiryMessage.objects.create(
                site=site, conversation=conversation, direction=InquiryMessage.IN,
                sender_kind=InquiryMessage.CUSTOMER, body=text[:MAX_BODY],
                external_id=f"sms:{device.pk}:{ref}", created_at=occurred_at,
            )
    except IntegrityError:
        return False
    if stop:
        InquirySmsOptOut.objects.get_or_create(site=site, mobile=number, defaults={"message": message})
        promo.opt_out_mobile(site, number)  # PROMO-CONSENT: a STOP also ends the member's promotional yes
    latest = conversation.last_message_at
    InquiryConversation.objects.filter(pk=conversation.pk).update(
        unread=True, status=InquiryConversation.OPEN, last_inbound_at=occurred_at,
        last_message_at=max(latest, occurred_at) if latest else occurred_at,
    )
    live.publish(site, conversation.external_id, live.KIND_MESSAGE, occurred_at)
    return True
