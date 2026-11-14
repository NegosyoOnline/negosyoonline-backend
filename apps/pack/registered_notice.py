"""The "your Santé account is created" email (ClickUp z8uq9meyqj; docs/pack-registered-notice.md).

The event is the Subscriber's own "Mark registered" (registration.mark_registered), which writes
one PackRegisteredNotice per registration in the same transaction. This module sends it after
commit and, for a failed send, again from the hourly sweep (`pack.registered_notice`).

The email goes from the store's own sender, to the member's VERIFIED email only. It says the
Santé account is created, that product orders are placed through the Santé Mobile App, and links
the instruction video (PACK_ORDERING_VIDEO_URL; empty: "coming soon" and no link). It carries no
password, sign-in code, token or Santé ID. Failed tries are kept on the row (count, last error
code); a failed send is tried again every 6 hours, at most 6 retries, then given up and logged
once. Log lines carry row numbers only. Never the address or the text."""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.members import messaging
from apps.notifications.services import EmailNotSent
from apps.notifications.store_email import send_store_email
from apps.pack import constants as C
from apps.pack.models import PackRegisteredNotice

logger = logging.getLogger(__name__)

RETRY_AFTER = timedelta(hours=6)
MAX_RETRIES = 6
CATEGORY = "pack_registered"
SUBJECT = "Your Santé account is ready"
BODY = (
    "Hi {first},\n\nYour Santé account has been created by {store}.\n\n"
    "Product orders are placed through the Santé Mobile App.\n\n{video}"
)
VIDEO_WITH_LINK = "Watch this short video to learn how to order in the app:\n{url}"
VIDEO_SOON = "A video guide on how to order in the app is coming soon."
VIDEO_LABEL = "Watch the video guide"
SIGN = "\n\n{store}"

NO_EMAIL, NOT_SENT, BAD_SENDER, ERROR = "no_verified_email", "email_not_sent", "bad_sender", "error"


def video_url():
    """The approved instruction video, or "" (unset, or not an http(s) link)."""
    url = (settings.PACK_ORDERING_VIDEO_URL or "").strip()
    return url if url.startswith(("https://", "http://")) and not any(c in url for c in " \r\n<>\"'") else ""


def texts(member, site):
    """(subject, body, cta): the member's first name and the store's name, nothing else about
    them."""
    parts = (member.full_name or "").split()
    store, url = messaging.store_name(site), video_url()
    video = VIDEO_WITH_LINK.format(url=url) if url else VIDEO_SOON
    body = (BODY + SIGN).format(first=parts[0] if parts else "", store=store, video=video)
    return SUBJECT, body, ((VIDEO_LABEL, url) if url else None)


def _give_up(row, now):
    row.status, row.gave_up_at = C.NOTICE_GAVE_UP, now
    logger.error(
        "Pack registered notice given up: site %s, registration %s, last error %s.",
        row.site_id, row.registration_id, row.last_error_code,
    )


def _try(row, now):
    """One send under the row's lock; returns the error code, or "" when it went."""
    from apps.pack import registration

    member = row.registration.member
    if registration.past_deletion_point(row.site_id):
        return "site_closed"
    if not (member.email and member.email_state == "verified"):
        return NO_EMAIL
    subject, body, cta = texts(member, row.site)
    try:
        send_store_email(row.site, to=member.email, category=CATEGORY, subject=subject, body=body, cta=cta)
    except EmailNotSent:
        return NOT_SENT
    except ValueError:
        return BAD_SENDER
    except Exception as exc:  # noqa: BLE001 - recorded as a code; the type name only is logged
        logger.warning("Pack registered notice error (%s): registration %s.", type(exc).__name__, row.registration_id)
        return ERROR
    return ""


def send(notice_pk, now=None):
    """Try one notice once, under its lock, and record the outcome. Returns "sent", "failed",
    "gave_up" or "" (nothing to do: already sent or given up, or its retry time has not come)."""
    now = now or timezone.now()
    with transaction.atomic():
        row = (
            PackRegisteredNotice.objects.select_for_update()
            .select_related("registration__member", "site__account").get(pk=notice_pk)
        )
        if row.status in (C.NOTICE_SENT, C.NOTICE_GAVE_UP):
            return ""
        if row.last_attempt_at is not None and now < row.last_attempt_at + RETRY_AFTER:
            return ""
        row.attempts += 1
        row.last_attempt_at = now
        code = _try(row, now)
        if not code:
            row.status, row.sent_at, row.last_error_code = C.NOTICE_SENT, now, ""
            row.save()
            return "sent"
        row.last_error_code = code
        logger.warning(
            "Pack registered notice not sent (%s): site %s, registration %s; try %s.",
            code, row.site_id, row.registration_id, row.attempts,
        )
        if code == "site_closed" or row.attempts > MAX_RETRIES:
            _give_up(row, now)
            row.save()
            return "gave_up"
        row.status = C.NOTICE_FAILED
        row.save()
        return "failed"


def run(now=None):
    """The sweep: try every notice that is due again (failed, or still pending after a missed
    first try). Counts by outcome; a failed row is logged with row numbers only and the loop goes
    on. Does nothing while PACK_ENABLED is off."""
    if not settings.PACK_ENABLED:
        return {"skipped": "off"}
    now = now or timezone.now()
    due = Q(last_attempt_at__lte=now - RETRY_AFTER) | Q(last_attempt_at__isnull=True, created_at__lte=now - RETRY_AFTER)
    counts = {}
    for pk in list(
        PackRegisteredNotice.objects.filter(due, status__in=(C.NOTICE_PENDING, C.NOTICE_FAILED))
        .order_by("pk").values_list("pk", flat=True)
    ):
        try:
            outcome = send(pk, now)
        except Exception as exc:  # noqa: BLE001
            outcome = "error"
            logger.error("Pack registered notice sweep failed: notice %s (%s).", pk, type(exc).__name__)
        if outcome:
            counts[outcome] = counts.get(outcome, 0) + 1
    return counts
