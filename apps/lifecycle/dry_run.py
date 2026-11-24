"""The dry run of the site deletion executor (plan z8uq9mb22v, section 6.1).

`erasure_dry_run(now)` reads and counts. It writes no row, not even an audit entry, takes no
lock, and works with SITE_ERASURE_ENABLED off. Its answer holds ids, dates, counts and codes
only: never a name, an email, a number, an address or a file key.
"""
from django.conf import settings

from apps.common import retention
from apps.inbox import retention as inbox_retention
from apps.lifecycle import eligibility
from apps.inquiries import retention as inquiries_retention
from apps.members import retention as member_retention
from apps.media_library import ledger as media_ledger
from apps.meta_ads import retention as meta_retention
from apps.pack import retention as pack_retention
from apps.payments import retention as payment_retention
from apps.sante import retention as sante_retention
from apps.sante.models import SanteCatalogOwner
from apps.shop import retention as shop_retention
from apps.sites import retention as site_retention
from apps.sites.models import Site, SiteBusinessProfile

# The counts that are stored files: the private QR and order proof files, and the media library's
# public objects (G2 ledger). Pack proof files stay (plan D14) and are not counted.
MEDIA_FILES = "media_files"
FILE_KEYS = (payment_retention.QR_FILES, shop_retention.PROOF_FILES, MEDIA_FILES)
SEAL_FILES = "site_seal_files"

CATALOG_OWNER, OTHER_LIVE_SITE = "catalog_owner", "other_live_site"
RETENTION_OFF = "retention_switched_off"


def _iso(moment):
    return moment.isoformat() if moment else None


def planned_counts(site):
    """What an erasure of this site would blank or delete, per table, and how many private files
    it would delete. Every table is read whatever its feature switch says (plan D13). Reads only."""
    sites = Site.objects.filter(pk=site.pk)
    counts = {}
    for part in (
        site_retention.blank_sites(sites, None, dry_run=True),
        sante_retention.blank_settings(sites, dry_run=True, ignore_switch=True),
        payment_retention.blank_payments(sites, None, dry_run=True, ignore_switch=True),
        member_retention.blank_members(sites, dry_run=True, ignore_switch=True),
        pack_retention.blank_packs(sites, dry_run=True, ignore_switch=True, keep_files=True),
        shop_retention.blank_shop(sites, dry_run=True, ignore_switch=True),
        meta_retention.blank_meta(sites, dry_run=True),
        inbox_retention.erase_site_inbox(sites, dry_run=True),
        inquiries_retention.blank_inquiries(sites, dry_run=True),
    ):
        counts.update(part)
    seals = SiteBusinessProfile.objects.filter(site__in=sites).exclude(seal_key="").count()
    if seals:
        counts[SEAL_FILES] = seals
    media_files = media_ledger.live_count(site)
    if media_files:
        counts[MEDIA_FILES] = media_files
    return counts, seals + sum(counts.get(key, 0) for key in FILE_KEYS)


def account_step(candidate, eligible_ids, erased_ids):
    """('dispose' | 'keep', reason). The account goes with its last site (plan D7)."""
    account = candidate.site.account
    if SanteCatalogOwner.objects.filter(site__account=account).exists():
        return "keep", CATALOG_OWNER
    others = Site.objects.filter(account=account).exclude(pk=candidate.site.pk)
    for pk in others.values_list("pk", flat=True):
        if pk not in eligible_ids and pk not in erased_ids:
            return "keep", OTHER_LIVE_SITE
    if not retention.enabled():
        return "keep", RETENTION_OFF
    return "dispose", ""


def _eligible_entry(candidate, now, eligible_ids, erased_ids):
    sub = candidate.subscription
    counts, files = planned_counts(candidate.site)
    action, reason = account_step(candidate, eligible_ids, erased_ids)
    return {
        "site": candidate.site.public_id,
        "template": candidate.site.template,
        "account": candidate.site.account.public_id,
        "state": sub.state,
        "service_ended_at": _iso(sub.service_ended_at),
        "deletion_due_at": _iso(sub.deletion_due_at),
        "days_overdue": (now - sub.deletion_due_at).days,
        "counts": counts,
        "private_files": files,
        "account_action": action,
        "account_reason": reason,
        "notice": candidate.notice,
        "notice_sent": candidate.notice == eligibility.NOTICE_SENT,
        "usage_to_write_off": candidate.usage_to_write_off,
    }


def erasure_dry_run(now):
    """The three lists of section 6.1 at `now`: eligible, near (due within 14 days) and refused
    (held or stopped, each with a reason code). Writes nothing."""
    found = eligibility.classify(now)
    eligible_ids = {c.subscription.site_id for c in found.eligible}
    return {
        "as_of": _iso(now),
        "lifecycle_checked": found.lifecycle_checked,
        "retention_enabled": bool(settings.RETENTION_ENABLED),
        "eligible": [
            _eligible_entry(c, now, eligible_ids, found.erased_site_ids) for c in found.eligible
        ],
        "near": [
            {
                "site": row.site.public_id,
                "deletion_due_at": _iso(row.deletion_due_at),
                "days_until": (row.deletion_due_at - now).days,
            }
            for row in found.near
        ],
        "refused": [
            {
                "site": r.subscription.site.public_id,
                "deletion_due_at": _iso(r.subscription.deletion_due_at),
                "reason_code": r.reason_code,
                **({"hold_reason": r.hold_reason} if r.hold_reason else {}),
            }
            for r in found.refused
        ],
    }
