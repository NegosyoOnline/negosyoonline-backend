"""What account disposal and account export do to an account's sites.

Called only by apps.accounts.retention. This is not store disposal: a site has no order,
customer or media row yet. The rows stay; only their personal fields go, and the site's web
address and custom host are retired.
"""
from apps.common import private_files
from apps.sites import custom_hosts
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, SiteCustomHost, Subdomain

SITE_PERSONAL = ("contact_email", "contact_mobile", "facebook_url", "instagram_url", "tiktok_url")
PROFILE_PERSONAL = (
    "business_name", "registered_name", "tin", "dti_business_number", "bir_rsn",
    "business_address", "complaints_contact_name", "privacy_contact_email",
)
SHIPPING = (
    "shipping_processing_time", "shipping_metro_manila_estimate", "shipping_luzon_estimate",
    "shipping_visayas_estimate", "shipping_mindanao_estimate", "shipping_other_areas_estimate",
    "shipping_estimates_confirmed",
)
# The field names a disposal blanks, for its audit entry.
BLANKED = {
    "site": [*SITE_PERSONAL, "display_name"],
    "site_business_profile": list(PROFILE_PERSONAL),
}


def _iso(moment):
    return moment.isoformat() if moment else None


def blank_sites_of(account, now, dry_run=False):
    """Blank the personal fields of every site of the account, close each site and retire its
    web address and its custom host. Returns the counts; a dry run only counts. Call it inside a transaction."""
    return blank_sites(Site.objects.filter(account=account), now, dry_run)


def blank_sites(sites, now, dry_run=False):
    """The same for the sites in `sites` (a Site queryset), whatever their accounts. Active
    custom hosts (SiteCustomHost) are retired with the web addresses, under the same lock; where
    the table does not exist yet (before sites.0004) there is nothing to retire and the count is 0."""
    profiles = SiteBusinessProfile.objects.filter(site__in=sites)
    active_names = Subdomain.objects.filter(site__in=sites, state=Subdomain.State.ACTIVE)
    hosts = (
        SiteCustomHost.objects.filter(site__in=sites, state=SiteCustomHost.State.ACTIVE)
        if custom_hosts._table_ready()
        else SiteCustomHost.objects.none()
    )
    if not dry_run:
        # The lock every site write of this account takes. No row is made for it here.
        list(Onboarding.objects.select_for_update().filter(account__in=sites.values("account")))
    counts = {
        "sites": sites.count(),
        "business_profiles": profiles.count(),
        "web_addresses_retired": active_names.count(),
        "custom_hosts_retired": hosts.count(),
    }
    if dry_run:
        return counts
    active_names.update(state=Subdomain.State.RETIRED, retired_at=now)
    hosts.update(state=SiteCustomHost.State.RETIRED, retired_at=now)
    for key in list(profiles.exclude(seal_key="").values_list("seal_key", flat=True)):
        private_files.delete(key)  # a file that cannot be deleted undoes the disposal, as for payments
    profiles.update(seal_key="", **{name: "" for name in PROFILE_PERSONAL})
    for pk, deleted_at in list(sites.values_list("pk", "deleted_at")):
        Site.objects.filter(pk=pk).update(
            display_name=f"Deleted site #{pk}",
            deleted_at=deleted_at or now,
            **{name: "" for name in SITE_PERSONAL},
        )
    return counts


def export_sites_of(account):
    """The account's sites as plain values, for the account export. Reads only."""
    record = Onboarding.objects.filter(account=account).first()
    sites = []
    for site in Site.objects.filter(account=account).order_by("pk"):
        profile = SiteBusinessProfile.objects.filter(site=site).first()
        sites.append(
            {
                "public_id": site.public_id,
                "template": site.template,
                "display_name": site.display_name,
                **{name: getattr(site, name) for name in SITE_PERSONAL},
                "created_at": _iso(site.created_at),
                "deleted_at": _iso(site.deleted_at),
                "web_addresses": [
                    {
                        "name": name.name,
                        "state": name.state,
                        "created_at": _iso(name.created_at),
                        "retired_at": _iso(name.retired_at),
                    }
                    for name in site.subdomains.order_by("pk")
                ],
                "business_profile": None
                if profile is None
                else {name: getattr(profile, name) for name in (*PROFILE_PERSONAL, *SHIPPING)},
            }
        )
    onboarding = None
    if record is not None:
        onboarding = {
            "started_at": _iso(record.started_at),
            "minimum_completed_at": _iso(record.minimum_completed_at),
        }
    return {"onboarding": onboarding, "sites": sites}
