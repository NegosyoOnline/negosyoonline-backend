"""The day-76 email to a paid Membership Pack buyer (ClickUp z8uq9mep8p; plan
2026-11-13-buyer-portal-z8uq9mbk0m section 7, Coach Ed's block C, 2026-11-13).

14 x 24 hours before a site's deletion point, each member with a verified order gets one email
saying when the order view closes. The text is block C's, word for word. The hourly task
`pack.buyer_notice` runs it, behind PACK_BUYER_NOTICE_ENABLED, and it sends nothing unless
PACK_PURGE_ENABLED is on too: the email promises what the purge does.

Once per (site, member, deletion date): `PackBuyerNotice`. A failed send is tried again every 6
hours, at most 6 retries, and only while the point has not been reached. Before each send the
subscription is read again: the notice goes only while the service has ended and the point is
still the recorded one (a reactivated site sends nothing). A window missed because of an outage
(the point already passed) sends nothing and is logged once. No text message. Log lines carry
row numbers only.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.billing import services as billing
from apps.common.disposal import is_disposed_address
from apps.notifications.services import EmailNotSent, send_email
from apps.pack import constants as C, portal
from apps.pack.models import PackBuyerNotice, PackOrder
from apps.sites.models import Site

logger = logging.getLogger(__name__)

LEAD = timedelta(days=portal.NOTICE_DAYS)  # 14 x 24 hours
RETRY_AFTER = timedelta(hours=6)
MAX_RETRIES = 6
CATEGORY = "pack_buyer_notice"

SUBJECT = "Your {store} order view closes on {date} at {time}."
BODY = (
    "{store} stopped taking orders on {service_end}. You can still sign in to see your Membership "
    "Pack order, payment status and delivery status until {time} Philippine time on {date}. "
    "After that this view closes. " + portal.DELETION_LINE + " Records that must be kept for "
    "legal, payment, accounting, fraud or audit purposes are kept and are not available to you, "
    "the store or this page. Please save anything you need before {time} on {date}."
)


def enabled():
    return settings.PACK_BUYER_NOTICE_ENABLED and settings.PACK_PURGE_ENABLED and settings.PACK_ENABLED


def texts(store, service_end, closes_at):
    """(subject, body), the times in Manila time and equal to the deletion point."""
    fields = {
        "store": store, "service_end": portal.day_text(service_end),
        "date": portal.day_text(closes_at), "time": portal.time_text(closes_at),
    }
    return SUBJECT.format(**fields), BODY.format(**fields)


def due_site_ids(now):
    """Sites whose subscription has ended with a stored deletion date at most 14 days away, or
    already past (a missed window is logged once)."""
    return list(
        billing.Subscription.objects.filter(
            billing_exempt=False, state__in=billing.ENDED_STATES, deletion_due_at__isnull=False,
            deletion_due_at__lte=now + LEAD,
        ).order_by("pk").values_list("site_id", flat=True)
    )


def buyers(site_id):
    """The members of the site with a verified order, each once, by row number."""
    return list(
        PackOrder.objects.filter(site_id=site_id, payment_status__in=C.PAYMENT_OK)
        .order_by("member_id").values_list("member_id", flat=True).distinct()
    )


def _give_up(row, now, why):
    row.gave_up_at = now
    row.save(update_fields=["gave_up_at"])
    logger.error("Pack buyer notice given up (%s): site %s, member %s.", why, row.site_id, row.member_id)


def notify(site_id, member_id, due_at, now):
    """Try the notice of one buyer once, under the row's lock. Returns "sent", "failed", "gave_up"
    or "" (nothing to do now)."""
    with transaction.atomic():
        PackBuyerNotice.objects.get_or_create(site_id=site_id, member_id=member_id, deletion_due_at=due_at)
        row = PackBuyerNotice.objects.select_for_update().get(
            site_id=site_id, member_id=member_id, deletion_due_at=due_at
        )
        if row.sent_at is not None or row.gave_up_at is not None:
            return ""
        if now >= due_at:
            _give_up(row, now, "the deletion point passed first")
            return "gave_up"
        if now < due_at - LEAD:
            return ""
        if row.last_attempt_at is not None and now < row.last_attempt_at + RETRY_AFTER:
            return ""
        site = Site.objects.get(pk=site_id)
        ended = billing.end_dates(site, now)  # the subscription again, before every send
        if ended is None or ended[1] != due_at:
            return ""  # reactivated, or its dates changed: nothing is sent for this date
        member = row.member
        if is_disposed_address(member.email):
            _give_up(row, now, "the address is disposed")
            return "gave_up"
        subject, body = texts(site.display_name, ended[0], due_at)
        row.attempts += 1
        row.last_attempt_at = now
        try:
            send_email(to=member.email, category=CATEGORY, subject=subject, body=body)
        except EmailNotSent:
            row.save(update_fields=["attempts", "last_attempt_at"])
            logger.warning("Pack buyer notice not sent: site %s, member %s; it is tried again.", site_id, member_id)
            if row.attempts > MAX_RETRIES:
                _give_up(row, now, "retries used up")
                return "gave_up"
            return "failed"
        row.sent_at = now
        row.save(update_fields=["attempts", "last_attempt_at", "sent_at"])
        return "sent"


def run(now=None):
    """Send every notice that is due. Counts by outcome; a failed site or buyer is logged with
    row numbers only and the loop goes on. Does nothing while a switch is off."""
    if not enabled():
        return {"skipped": "off"}
    now = now or timezone.now()
    counts = {}
    for site_id in due_site_ids(now):
        ended = billing.end_dates(site_id, now)
        if ended is None or ended[1] is None:
            continue
        for member_id in buyers(site_id):
            try:
                outcome = notify(site_id, member_id, ended[1], now)
            except Exception as exc:
                outcome = "error"
                logger.error(
                    "Pack buyer notice failed: site %s, member %s (%s).", site_id, member_id, type(exc).__name__
                )
            if outcome:
                counts[outcome] = counts.get(outcome, 0) + 1
    return counts
