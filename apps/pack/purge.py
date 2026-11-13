"""The day-90 purge of Membership Pack buyer data (ClickUp z8uq9mep8p; plan
2026-11-13-buyer-portal-z8uq9mbk0m section 8, Coach Ed's block B, 2026-11-13).

At a site's deletion point (`billing.deletion_point`) the personal details of its Pack buyers are
cleared, whether or not the site is served, a removed site included. The rows stay (orders are
never deleted and the Subscriber's books must add up); what identifies a person goes. The work is
assignment to empty values, so a second pass changes nothing, and rows made after a restore are
cleared by the next cycle. It runs only through the hourly task `pack.purge`, behind
`PACK_PURGE_ENABLED` (off by default; Coach Ed's word, per environment).

Left out, by decision: the Subscriber's receiving accounts (`apps.payments.retention` blanks them
per account, not per site, so there is no per-site helper to reuse; no new slice here), the holds
and run tables, and anything Xendit. The date-of-birth erase of `apps.pack.retention` (ruling D3)
is a separate rule on its own switch and is kept as it is.

Proof files are deleted only while `PROOF_EXPORT_READY_FROM` allows it (see `files_deletable`).
"""
import logging
from datetime import date

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.billing import services as billing
from apps.common import private_files
from apps.lifecycle.models import ErasureHold
from apps.members.models import Member, MemberContactAudit, MemberSession
from apps.pack import portal
from apps.pack.models import (
    CheckoutToken, PackOrder, PackProofSubmission, SanteRegistration, SanteRegistrationEvent,
)
from apps.sites.models import Site

logger = logging.getLogger(__name__)

# The first Manila day on which the Subscriber's data export (`export_pack_files`, ClickUp
# z8uq9mbu0p) held the proof images: the day after the verified staging deploy of that archive. A
# proof image may be deleted only for a site whose service ended on or after this day, so the
# export held it for the site's whole 30-day window; a site that ended earlier keeps its images
# (Finance-only, no route, no automatic deletion; L72). Setting it deletes nothing by itself:
# deletion also needs PACK_PURGE_ENABLED or an approved executor batch. None fails closed.
PROOF_EXPORT_READY_FROM: date | None = date(2026, 11, 14)

BUYER_PLACEHOLDER = "Deleted buyer"
PROOF_TEXT_FIELDS = (
    "sender_name", "sender_account_number", "sender_bank", "ip_advisory", "reference_number",
    "account_public_id", "account_name", "account_number_last4",
)


def enabled():
    return settings.PACK_PURGE_ENABLED and settings.PACK_ENABLED


def files_deletable(service_end):
    """Whether the proof images of a site whose service ended at `service_end` may be deleted: the
    export held images from a day not later than the start of its 30-day window. Unknown is no."""
    ready = PROOF_EXPORT_READY_FROM
    if ready is None or service_end is None:
        return False
    return ready <= portal.manila(service_end).date()


def due_site_ids(now):
    """The sites whose subscription has ended and whose stored deletion date has passed. Served
    or not, removed or not. A reactivated site has no stored date, so it is never listed."""
    return list(
        billing.Subscription.objects.filter(
            billing_exempt=False, state__in=billing.ENDED_STATES, deletion_due_at__isnull=False,
            deletion_due_at__lte=now,
        ).order_by("pk").values_list("site_id", flat=True)
    )


def _clear_details(site_id, now):
    registrations = SanteRegistration.objects.filter(site_id=site_id)
    registrations.filter(
        Q(date_of_birth__isnull=False) | ~Q(desired_username="") | ~Q(alternate_username="")
        | Q(details_purged_at__isnull=True)
    ).update(
        date_of_birth=None, desired_username="", alternate_username="",
        details_purged_at=now,
    )
    # Append-only tables refuse queryset writes; the purge is the sanctioned exception, through
    # the plain base manager, as an account disposal does.
    events = SanteRegistrationEvent._base_manager.filter(site_id=site_id)
    events.exclude(note="").update(note="")
    events.filter(actor_type="member").exclude(actor_label="").update(actor_label="")
    orders = PackOrder.objects.filter(site_id=site_id)
    orders.exclude(buyer_name=BUYER_PLACEHOLDER).update(buyer_name=BUYER_PLACEHOLDER)
    orders.exclude(delivery_address={}).update(delivery_address={})
    Member.objects.filter(site_id=site_id).exclude(delivery_address={}).update(delivery_address={})
    CheckoutToken.objects.filter(site_id=site_id).delete()
    MemberSession.objects.filter(site_id=site_id).exclude(ip_advisory="").update(ip_advisory="")
    MemberContactAudit.objects.filter(site_id=site_id).exclude(ip_advisory="").update(ip_advisory="")
    proofs = PackProofSubmission._base_manager.filter(site_id=site_id)
    proofs.update(**{name: "" for name in PROOF_TEXT_FIELDS}, transferred_at=F("submitted_at"))


def _delete_files(site_id):
    """Each proof file, then its key. A key stays until its file is gone, so a store that is down
    leaves the key for the next hourly run; deleting a file that is already gone succeeds."""
    proofs = PackProofSubmission._base_manager.filter(site_id=site_id).exclude(file_key="")
    for pk, key in list(proofs.values_list("pk", "file_key")):
        private_files.delete(key)
        PackProofSubmission._base_manager.filter(pk=pk).update(file_key="")


def purge_site(site_id, now=None):
    """Purge one site if its deletion point has been reached, under the site's row lock. Returns
    True when the site was due (even if there was nothing left to clear). It re-reads the point
    itself, so a site reactivated since the list was made is left alone.

    A site with an open erasure hold (a legal, fraud or dispute reason a person set, plan D15) is
    left alone too and the answer is False. Holds are read only while SITE_ERASURE_ENABLED is on,
    so no lifecycle table is read, or needed, while it is off."""
    now = now or timezone.now()
    with transaction.atomic():
        Site.objects.select_for_update().get(pk=site_id)
        if settings.SITE_ERASURE_ENABLED and ErasureHold.objects.filter(
            site_id=site_id, released_at__isnull=True
        ).exists():
            return False
        ended = billing.end_dates(site_id, now)
        if ended is None or ended[1] is None or now < ended[1]:
            return False
        _clear_details(site_id, now)
        service_end = ended[0]
    if files_deletable(service_end):
        with transaction.atomic():
            Site.objects.select_for_update().get(pk=site_id)
            _delete_files(site_id)
    return True


def run(now=None):
    """Purge every due site, one transaction each. A failed site is logged with its row number
    only (never a value) and the loop goes on. Returns {"purged": n, "failed": n}; it does
    nothing while the switch is off."""
    if not enabled():
        return {"skipped": "off"}
    now = now or timezone.now()
    purged = failed = 0
    for site_id in due_site_ids(now):
        try:
            purged += bool(purge_site(site_id, now))
        except Exception as exc:
            failed += 1
            logger.error("Pack purge failed for site %s (%s).", site_id, type(exc).__name__)
    return {"purged": purged, "failed": failed}
