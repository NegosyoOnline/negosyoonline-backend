"""The only way any app sends an email or a text."""
import logging
from datetime import timedelta

from email.utils import parseaddr

from django.conf import settings
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, send_mail
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.common.disposal import is_disposed_address
from apps.common.errors import ApiError
from apps.common.phones import normalise_ph_mobile
from apps.notifications import sms
from apps.notifications.models import EmailMessage, SmsMessage, SmsPolicy, store_limit_columns_exist
from apps.operators import audit, background

logger = logging.getLogger(__name__)


class EmailNotSent(ApiError):
    def __init__(self):
        super().__init__(
            "email_not_sent", "We could not send the email. Try again shortly.", status=503
        )


def _has_line_break(value):
    return "\r" in value or "\n" in value


def _named_sender(sender_name, from_address=""):
    """`"Store name" <address>`. The address is that of DEFAULT_FROM_EMAIL unless the caller
    gives `from_address` (an operator-set store sender, never user input)."""
    if _has_line_break(sender_name):
        raise ValueError("The sender name holds a line break.")
    name = sender_name.replace("\\", "\\\\").replace('"', '\\"')
    address = from_address or parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
    return f'"{name}" <{address}>'


def _checked_from_address(from_address):
    try:
        if _has_line_break(from_address):
            raise ValidationError("line break")
        validate_email(from_address)
    except ValidationError:
        raise ValueError("The From address is not one valid email address.") from None
    return from_address


def _checked_reply_to(reply_to):
    try:
        if _has_line_break(reply_to):
            raise ValidationError("line break")
        validate_email(reply_to)
    except ValidationError:
        raise ValueError("The Reply-To is not one valid email address.") from None
    return reply_to


def _deliver(to, subject, body, sender_name, reply_to, html_body="", from_address=""):
    if from_address:
        from_address = _checked_from_address(from_address)
    if html_body:
        message = EmailMultiAlternatives(
            subject, body,
            _named_sender(sender_name, from_address) if sender_name else (from_address or settings.DEFAULT_FROM_EMAIL),
            [to],
            reply_to=[_checked_reply_to(reply_to)] if reply_to else None,
        )
        message.attach_alternative(html_body, "text/html")
        message.send()
        return
    if not sender_name and not reply_to and not from_address:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to])
        return
    mail.EmailMessage(
        subject, body,
        _named_sender(sender_name, from_address) if sender_name else (from_address or settings.DEFAULT_FROM_EMAIL),
        [to],
        reply_to=[_checked_reply_to(reply_to)] if reply_to else None,
    ).send()


def send_email(
    *, to, category, subject, body, sender_name="", reply_to="", html_body="", from_address="", log_detail=True,
):
    """`sender_name` is the name shown beside our own From address, `reply_to` the one address
    a reply goes to. Neither may hold a line break, and `reply_to` must be a valid address:
    otherwise nothing is sent and the send is recorded as failed, like any other bad header.
    `html_body`, when given, is sent as the HTML alternative of the plain-text `body`; empty,
    the email is plain text exactly as before. `from_address` replaces the address of
    DEFAULT_FROM_EMAIL for a store's own sender (see store_email.py); it must be one valid
    address with no line break, else nothing is sent and the send is recorded as failed.
    `log_detail=False` logs only the exception's class on a failure, never its text or traceback."""
    if is_disposed_address(to):  # a disposed contact is never emailed, and no record is kept
        logger.warning("Email refused: the recipient is a disposed account (category=%s)", category)
        raise EmailNotSent()
    message = EmailMessage.objects.create(recipient=to, category=category)
    try:
        _deliver(to, subject, body, sender_name, reply_to, html_body, from_address)
    except Exception as error:
        if log_detail:
            logger.exception("Email send failed (category=%s)", category)
        else:
            logger.error("Email send failed (category=%s, error=%s)", category, type(error).__name__)
        message.status = EmailMessage.Status.FAILED
        message.save(update_fields=["status"])
        raise EmailNotSent() from None
    message.status = EmailMessage.Status.SENT
    message.sent_at = timezone.now()
    message.save(update_fields=["status", "sent_at"])
    return message


SMS_QUEUED = sms.QUEUED


def sms_on():
    """Whether texts can be sent at all here. While this is False no app reads or writes a
    table that exists only for texts: the code is released before those tables are created."""
    return sms.gateway_on()


SMS_STAFF_TEXT_LIFETIME = sms.STAFF_TEXT_LIFETIME
SMS_RESERVED_FOR_CODES = sms.RESERVED_FOR_CODES
STAFF_PURPOSES = ("inbox_reply", "inbox_compose", "inbox_bulk")


def sms_text_problem(text):
    """Why this text cannot be one SMS, or "". For a form that shows the reason before sending."""
    return sms.text_problem(text)


def sms_line():
    """The phone that sends, or None."""
    return sms.pick_device()


def sms_status():
    """What a status line may show. Reads no SMS table while the gateway is off."""
    if not sms.gateway_on():
        return {"phone_online": False, "used_today": None, "cap": None, "reserved_for_codes": None}
    return {
        "phone_online": sms.gateway_online(),
        "used_today": sms.platform_count(),
        "cap": sms.policy().platform_daily_limit,
        "reserved_for_codes": sms.RESERVED_FOR_CODES,
    }


def sms_room_for_staff():
    """(used today, texts a staff or bulk text may still use today). Reads no SMS table
    while the gateway is off."""
    if not sms.gateway_on():
        return 0, 0
    used = sms.platform_count()
    return used, max(sms.policy().platform_daily_limit - sms.RESERVED_FOR_CODES - used, 0)


def sms_store_status(site):
    """What a store's composer may show: whether the phone that carries the store's texts is
    online, and the room left today and this hour. Reads no SMS table while the gateway is off."""
    if not sms.gateway_on():
        return {"phone_online": False, "daily_limit": 0, "left_today": 0, "hourly_limit": 0, "left_this_hour": 0}
    rules = sms.policy()
    used_today, used_hour = sms.store_used_today(site), sms.store_used_this_hour(site)
    return {
        "phone_online": sms.gateway_online(site),
        "daily_limit": rules.store_daily_limit,
        "left_today": max(rules.store_daily_limit - used_today, 0),
        "hourly_limit": sms.STORE_HOURLY_LIMIT,
        "left_this_hour": max(sms.STORE_HOURLY_LIMIT - used_hour, 0),
    }


def sms_phone_forwards():
    """Whether the phone that carries texts passes the texts it receives on (the incoming switch)."""
    phone = sms.pick_device()
    return bool(phone and phone.forwards_incoming)


def cancel_sms(public_id):
    """Take back a text still waiting for the phone. True when it was waiting; False when the
    phone has it or it is already over."""
    message = SmsMessage.objects.filter(public_id=public_id).first()
    if message is None:
        return False
    taken = SmsMessage.objects.filter(pk=message.pk, status=SmsMessage.Status.QUEUED).update(
        status=SmsMessage.Status.EXPIRED, reason="stopped"
    )
    if taken:
        sms.forget(message)
    return bool(taken)


def staff_send_records(to):
    """The send records of the staff texts to one number, for the deletion command."""
    return SmsMessage.objects.filter(recipient=to, purpose__in=STAFF_PURPOSES)


def sms_statuses(public_ids):
    """{public id: (status, reason)} of these send records, in one query. A record that is
    gone is missing from the answer. Reads nothing for an empty list."""
    public_ids = [p for p in public_ids if p]
    if not public_ids:
        return {}
    rows = SmsMessage.objects.filter(public_id__in=public_ids).values_list("public_id", "status", "reason")
    return {public_id: (status, reason) for public_id, status, reason in rows}


def forget_sms(message):
    """Drop a waiting text, so a deleted record's text is never handed to the phone."""
    sms.forget(message)


def send_sms(*, to, purpose, text, site=None, fallback=None, ttl=sms.DEFAULT_LIFETIME):
    """Queue one text for the phone, or say why not. Returns an `sms.SmsResult`.

    `fallback` is an `sms.EmailFallback`: the email that goes out when the text cannot. `ttl`
    is how many seconds the text is worth sending. Raises ValueError only for a programming
    mistake: an undeclared purpose, a text that is not one plain SMS or holds a link, a number
    that is not a Philippine mobile, or a status text without its site or its fallback.
    """
    declared = sms.PURPOSES.get(purpose)
    if declared is None:
        raise ValueError(f"SMS purpose {purpose!r} is not declared in apps.notifications.sms.")
    problem = sms.text_problem(text)
    if problem:
        raise ValueError(problem)
    if declared.category == sms.STATUS and (site is None or fallback is None):
        raise ValueError("A status text must name its site and carry an email fallback.")
    if declared.category == sms.STORE and (site is None or fallback is not None):
        raise ValueError("A store text must name its site and has no email fallback.")
    recipient = normalise_ph_mobile(to)

    if not sms.gateway_on():
        # No record: the gateway's tables are not read or written while it is switched off.
        return email_instead(None, "gateway_off", purpose, fallback)

    sms.sweep(timezone.now(), unsent)
    message = SmsMessage.objects.create(
        recipient=recipient,
        category=declared.category,
        purpose=purpose,
        priority=declared.priority,
        site=site,
        expires_at=timezone.now() + timedelta(seconds=ttl),
    )
    reason = sms.refusal(message)
    if reason:
        message.status = SmsMessage.Status.REFUSED
        message.reason = reason
        message.save(update_fields=["status", "reason"])
        return email_instead(message, reason, purpose, fallback)
    sms.hold_text(message, text, ttl)
    if fallback is not None:
        sms.hold_fallback(message, fallback, ttl)
    return sms.SmsResult(sms.QUEUED, message=message)


def email_instead(message, reason, purpose, fallback):
    """The text is not going out: send its email instead, when it has one. `message` is None
    when no record was written."""
    if fallback is None:
        sms.log_not_sent(reason, purpose)
        return sms.SmsResult(sms.REFUSED, reason, message)
    try:
        send_email(to=fallback.to, category=purpose, subject=fallback.subject, body=fallback.body)
    except EmailNotSent:
        sms.log_not_sent(reason, purpose, email_failed=True)
        return sms.SmsResult(sms.REFUSED, reason, message)
    if message is not None:
        message.emailed = True
        message.save(update_fields=["emailed"])
    return sms.SmsResult(sms.EMAILED, reason, message)


def unsent(message, reason):
    """A queued or handed-out text ended unsent: email its fallback, when it has one."""
    fallback = sms.take_fallback(message)
    if fallback is None:
        sms.log_not_sent(reason, message.purpose)
    else:  # in the background, so the caller is not held up by the mail server
        background.run_later(email_instead, message, reason, message.purpose, fallback)


POLICY_FIELDS = ("sending_stopped", "platform_daily_limit", "site_daily_limit")
# The store caps were added after the first three. A body that leaves them out keeps them as
# they are, so a screen built before them still saves.
STORE_POLICY_FIELDS = ("store_daily_limit", "all_stores_daily_limit")
POLICY_LIMIT_MAX = 2147483647  # the largest value the column holds


def policy_fields(values=None):
    """The settings that are read and saved now: the store caps only once notifications.0006 is
    applied, and when they are in `values` (a body that leaves them out keeps them)."""
    extra = STORE_POLICY_FIELDS if store_limit_columns_exist() else ()
    if values is not None:
        extra = tuple(name for name in extra if name in values)
    return POLICY_FIELDS + extra


def policy_errors(values):
    """{field: message} for the SMS settings in `values` that are not valid."""
    errors = {}
    for name in policy_fields(values):
        value = values.get(name)
        if name == "sending_stopped":
            if not isinstance(value, bool):
                errors[name] = "Choose on or off."
        elif isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= POLICY_LIMIT_MAX:
            errors[name] = "Enter a whole number, zero or more."
    return errors


def record_policy_change(operator, was, now):
    """Write the sms_policy_change audit entry for what differs between two SmsPolicy rows
    (before and after). Nothing is written when nothing changed."""
    changed = [name for name in policy_fields() if getattr(was, name) != getattr(now, name)]
    if changed:
        audit.record(
            operator,
            "sms_policy_change",
            target_type="sms_policy",
            target_id=now.pk,
            detail={
                "before": {name: getattr(was, name) for name in changed},
                "after": {name: getattr(now, name) for name in changed},
            },
        )
    return changed


@transaction.atomic
def change_policy(operator, values):
    """Set the SMS settings in `values` and audit the change. Raises ApiError (400) for a bad value.
    Returns the saved row."""
    errors = policy_errors(values)
    if errors:
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={name: [message] for name, message in errors.items()},
        )
    SmsPolicy.objects.get_or_create(pk=1)
    row = SmsPolicy.objects.select_for_update().get(pk=1)
    was = SmsPolicy.objects.get(pk=1)
    fields = policy_fields(values)
    for name in fields:
        setattr(row, name, values[name])
    row.save(update_fields=[*fields, "updated_at"])  # never a column that is not there yet
    record_policy_change(operator, was, row)
    return row
