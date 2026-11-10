"""The only way any app sends email."""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.common.errors import ApiError
from apps.notifications.models import EmailMessage

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
