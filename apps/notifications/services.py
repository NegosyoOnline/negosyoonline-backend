"""The only way any app sends an email or a text."""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.common.errors import ApiError
from apps.common.phones import normalise_ph_mobile
from apps.notifications import sms
from apps.notifications.models import EmailMessage, SmsMessage
from apps.operators import background

logger = logging.getLogger(__name__)


class EmailNotSent(ApiError):
    def __init__(self):
        super().__init__(
            "email_not_sent", "We could not send the email. Try again shortly.", status=503
        )


def send_email(*, to, category, subject, body):
    message = EmailMessage.objects.create(recipient=to, category=category)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to])
    except Exception:
        logger.exception("Email send failed (category=%s)", category)
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
