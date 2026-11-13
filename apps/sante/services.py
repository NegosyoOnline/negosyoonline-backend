"""Santé site settings and the catalog owner. Other apps call these functions, never the tables.

Everything here is behind SANTE_SETTINGS_ENABLED. While it is off the routes answer 404 and the
two tables are not read or written: the code is released
before its tables are created.

Lock order, after the Account and Onboarding rows the caller may hold: the catalog owner row,
then a Site row, then a settings row.
"""
import re

from django.conf import settings
from django.db import transaction

from apps.accounts.services import record_event
from apps.common.errors import ApiError
from apps.operators import audit
from apps.sante.models import SanteCatalogOwner, SanteSiteSettings
from apps.sites import templates
from apps.sites.models import Onboarding, Site
from apps.sites.services import site_for

SPONSOR_RE = re.compile(r"^[A-Za-z0-9-]{1,32}$")
SPONSOR_MSG = "Use 1 to 32 letters, digits or hyphens."
SETTINGS_FIELDS = ("sponsor_id",)
OWNER_PK = 1
OWNER_TARGET = "sante_catalog_owner"
CATALOG_OWNER = "catalog_owner"  # why a site cannot be disposed of
NOT_LIVE_SANTE = "The catalog owner must be a live Santé site."


def enabled():
    return settings.SANTE_SETTINGS_ENABLED


def clean_settings(data):
    """(cleaned, errors) for the settings present in `data`. An empty sponsor ID clears it."""
    cleaned, errors = {}, {}
    if "sponsor_id" in data:
        value = (data["sponsor_id"] or "").strip()
        if value and not SPONSOR_RE.fullmatch(value):
            errors["sponsor_id"] = SPONSOR_MSG
        else:
            cleaned["sponsor_id"] = value
    return cleaned, errors


def registration_ready(site):
    """Member registration needs the site's own sponsor ID. A missing one blocks nothing else."""
    return SanteSiteSettings.objects.filter(site=site).exclude(sponsor_id="").exists()


def is_catalog_writer(site, *, lock=False):
    """True only for the designated catalog owner while it is a live Santé site.

    Reads only the site id on the one owner row, so no other site's row is read or locked.
    With `lock`, inside a transaction, the row lock makes this wait for set_catalog_owner.
    """
    rows = SanteCatalogOwner.objects.filter(pk=OWNER_PK)
    if lock:
        rows = rows.select_for_update()
    owner_site_id = rows.values_list("site_id", flat=True).first()
    return (
        owner_site_id is not None
        and owner_site_id == site.pk
        and site.deleted_at is None
        and site.template == templates.SANTE
    )


def settings_out(site):
    """What the Subscriber reads. A site with no row yet reads as a blank sponsor ID."""
    row = SanteSiteSettings.objects.filter(site=site).first()
    sponsor_id = row.sponsor_id if row else ""
    return {
        "sponsor_id": sponsor_id,
        "registration_ready": bool(sponsor_id),
        "catalog_owner": is_catalog_writer(site),
    }


@transaction.atomic
def update_settings(account, site, data):
    """Change the settings present in `data`, under the site's row lock."""
    cleaned, errors = clean_settings(data)
    if errors:
        raise ApiError(
            "invalid_request",
            "Some values are missing or invalid.",
            fields={name: [message] for name, message in errors.items()},
        )
    # The lock every site write of this account takes; a disposal takes it too.
    Onboarding.objects.select_for_update().filter(account=account).first()
    checked = site_for(account, site.public_id, template=templates.SANTE)
    site = Site.objects.select_for_update().get(pk=checked.pk)
    if cleaned:
        row, _ = SanteSiteSettings.objects.select_for_update().get_or_create(site=site)
        row.sponsor_id = cleaned["sponsor_id"]
        row.save(update_fields=["sponsor_id", "updated_at"])
        # The field names only: the sponsor ID itself never reaches an audit record.
        record_event(account, "sante_settings_updated", site=site.public_id, fields=sorted(cleaned))
    return site


def _public_id(site_pk):
    if site_pk is None:
        return None
    return Site.objects.values_list("public_id", flat=True).get(pk=site_pk)


@transaction.atomic
def set_catalog_owner(site, *, operator):
    """Name the catalog owner site, or clear it with None. Operators only; there is no
    Subscriber path. One row, one UPDATE: the previous owner is replaced in the same statement.
    """
    SanteCatalogOwner.objects.get_or_create(pk=OWNER_PK)
    row = SanteCatalogOwner.objects.select_for_update().get(pk=OWNER_PK)
    if site is not None:
        # Read again under the owner lock, which a disposal also takes before it closes a site.
        site = (
            Site.objects.select_for_update()
            .filter(pk=site.pk, template=templates.SANTE, deleted_at__isnull=True)
            .first()
        )
        if site is None:
            raise ValueError(NOT_LIVE_SANTE)
    old = _public_id(row.site_id)
    row.site = site
    row.save()
    audit.record(
        operator,
        "sante_catalog_owner_set",
        target_type=OWNER_TARGET,
        target_id=OWNER_PK,
        detail={"old_site": old, "new_site": site.public_id if site else None},
    )
    return row


def catalog_owner_site():
    row = SanteCatalogOwner.objects.select_related("site").filter(pk=OWNER_PK).first()
    return row.site if row else None


def refuses_disposal(site):
    """Why this site can never be disposed of, or None. The catalog owner site is kept until
    an operator names another one."""
    if SanteCatalogOwner.objects.filter(site_id=site.pk).exists():
        return CATALOG_OWNER
    return None
