"""Onboarding state. Derived from persisted fields on every read; never taken from a client."""
from dataclasses import dataclass, field
from datetime import datetime

from django.utils import timezone

from apps.sites.models import Onboarding, Site

NOT_STARTED, IN_PROGRESS, MINIMUM_COMPLETE, BLOCKED = (
    "not_started", "in_progress", "minimum_complete", "blocked",
)
OWNER_EMAIL_UNVERIFIED, FIRST_SITE_REMOVED = "owner_email_unverified", "first_site_removed"
MISSING_ALL = ("business_name", "site_name", "public_contact")


@dataclass
class Setup:
    state: str
    blocked_reason: str | None = None
    missing: list = field(default_factory=list)
    first_site: Site | None = None
    started_at: datetime | None = None
    minimum_completed_at: datetime | None = None


def evaluate(account):
    """The account's onboarding state. Read-only; the first matching rule wins.

    1. owner email not verified -> blocked
    2. no first site            -> not_started
    3. first site removed       -> blocked
    4. a required field missing -> in_progress
    5. otherwise                -> minimum_complete
    """
    record = (
        Onboarding.objects.filter(account=account)
        .select_related("first_site__business_profile")
        .first()
    )
    site = record.first_site if record else None
    stamps = {
        "first_site": site,
        "started_at": record.started_at if record else None,
        "minimum_completed_at": record.minimum_completed_at if record else None,
    }
    if account.email_verified_at is None:
        return Setup(BLOCKED, OWNER_EMAIL_UNVERIFIED, list(MISSING_ALL), **stamps)
    if site is None:
        return Setup(NOT_STARTED, None, list(MISSING_ALL), **stamps)
    if site.deleted_at is not None:
        return Setup(BLOCKED, FIRST_SITE_REMOVED, [], **stamps)
    missing = []
    if not site.business_profile.business_name:
        missing.append("business_name")
    if not site.display_name:
        missing.append("site_name")
    if not (site.contact_email and site.contact_mobile):
        missing.append("public_contact")
    return Setup(IN_PROGRESS if missing else MINIMUM_COMPLETE, None, missing, **stamps)


def stamp(account):
    """Called by every site write, inside its transaction. Each stamp is set once, never cleared."""
    record = Onboarding.objects.select_for_update().get(account=account)
    now = timezone.now()
    changed = []
    if record.first_site_id and record.started_at is None:
        record.started_at = now
        changed.append("started_at")
    if record.minimum_completed_at is None and evaluate(account).state == MINIMUM_COMPLETE:
        record.minimum_completed_at = now
        changed.append("minimum_completed_at")
    if changed:
        record.save(update_fields=[*changed, "updated_at"])
