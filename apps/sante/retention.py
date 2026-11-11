"""What account disposal and account export do to an account's Santé rows.

Called only by apps.accounts.retention. While SANTE_SETTINGS_ENABLED is off every function
here answers empty and reads no Santé table.
"""
from apps.sante import services
from apps.sante.models import SanteCatalogOwner, SanteSiteSettings
from apps.sites import templates
from apps.sites.models import Onboarding, Site

TABLE = "sante_site_settings"


def blanked():
    """The field names a disposal blanks, for its audit entry."""
    return {TABLE: ["sponsor_id"]} if services.enabled() else {}


def disposal_refusals(account, dry_run=False):
    """The reasons this account cannot be disposed of: it owns the catalog owner site.

    Call it inside the disposal's transaction, before anything is cancelled or blanked. A real
    run takes the account's site lock and then the owner row's, so that no operator can name
    one of this account's sites the catalog owner while the disposal closes them.
    """
    if not services.enabled():
        return []
    rows = SanteCatalogOwner.objects.filter(pk=services.OWNER_PK)
    if not dry_run:
        Onboarding.objects.select_for_update().filter(account=account).first()
        SanteCatalogOwner.objects.get_or_create(pk=services.OWNER_PK)  # so there is a row to lock
        rows = rows.select_for_update()
    owner = rows.filter(site__account=account).select_related("site").first()
    if owner is None:
        return []
    return [{"reason": services.CATALOG_OWNER, "site": owner.site.public_id}]


def blank_settings_of(account, dry_run=False):
    """Blank the sponsor ID of every site of the account. The rows stay. Returns the count;
    a dry run only counts. Call it inside a transaction."""
    if not services.enabled():
        return {}
    rows = SanteSiteSettings.objects.filter(site__account=account)
    count = rows.count()
    if not dry_run:
        rows.update(sponsor_id="")
    return {TABLE: count}


def export_settings_of(account):
    """The Santé settings of the account's sites as plain values. Reads only."""
    if not services.enabled():
        return {}
    owner_site_id = (
        SanteCatalogOwner.objects.filter(pk=services.OWNER_PK)
        .values_list("site_id", flat=True)
        .first()
    )
    rows = {
        row.site_id: row for row in SanteSiteSettings.objects.filter(site__account=account)
    }
    sites = Site.objects.filter(account=account, template=templates.SANTE).order_by("pk")
    return {
        TABLE: [
            {
                "site": site.public_id,
                "sponsor_id": rows[site.pk].sponsor_id if site.pk in rows else "",
                "updated_at": rows[site.pk].updated_at.isoformat() if site.pk in rows else None,
                "catalog_owner": site.pk == owner_site_id,
            }
            for site in sites
        ]
    }
