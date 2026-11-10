"""Site, business profile and onboarding endpoints. Shapes here are the frontend's contract.

Every endpoint is signed-in only, and every site is reached through services.site_for, so
another account's site answers the same 404 as one that does not exist.
"""
from datetime import datetime

from django.conf import settings
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.accounts.api import signed_in
from apps.common.pagination import DEFAULT_LIMIT, page
from apps.common.throttle import hit
from apps.sites import onboarding, services, subdomains, templates
from apps.sites.models import Site, Subdomain

router = Router(tags=["sites"], auth=signed_in)

CHECK_LIMIT, CHECK_WINDOW = 60, 600  # subdomain checks per account per 10 minutes
CREATE_LIMIT, CREATE_WINDOW = 10, 86400  # site creation attempts per account per day


class StrictSchema(Schema):
    """A request body. An unknown key is refused, so no server-owned field can be sent."""

    model_config = ConfigDict(extra="forbid")


class SubdomainCheckIn(StrictSchema):
    subdomain: str


class SubdomainCheckOut(Schema):
    subdomain: str
    status: str
    message: str


class SiteCreateIn(StrictSchema):
    template: str
    subdomain: str
    display_name: str


class SitePatch(StrictSchema):
    display_name: str | None = None
    contact_email: str | None = None
    contact_mobile: str | None = None
    instagram_url: str | None = None
    tiktok_url: str | None = None


class SiteOut(Schema):
    id: str
    template: str
    template_label: str
    display_name: str
    subdomain: str
    host: str | None
    business_name: str
    contact_email: str
    contact_mobile: str
    accent_color: str
    instagram_url: str
    tiktok_url: str
    created_at: datetime


class SiteListOut(Schema):
    items: list[SiteOut]
    next_cursor: str | None = None


class BusinessProfilePatch(StrictSchema):
    business_name: str | None = None
    registered_name: str | None = None
    tin: str | None = None
    dti_business_number: str | None = None
    bir_rsn: str | None = None
    business_address: str | None = None
    complaints_contact_name: str | None = None
    privacy_contact_email: str | None = None
    shipping_processing_time: str | None = None
    shipping_metro_manila_estimate: str | None = None
    shipping_luzon_estimate: str | None = None
    shipping_visayas_estimate: str | None = None
    shipping_mindanao_estimate: str | None = None
    shipping_other_areas_estimate: str | None = None
    shipping_estimates_confirmed: bool = False


class BusinessProfileOut(Schema):
    business_name: str
    registered_name: str
    tin: str
    dti_business_number: str
    bir_rsn: str
    business_address: str
    complaints_contact_name: str
    privacy_contact_email: str
    shipping_processing_time: str
    shipping_metro_manila_estimate: str
    shipping_luzon_estimate: str
    shipping_visayas_estimate: str
    shipping_mindanao_estimate: str
    shipping_other_areas_estimate: str
    shipping_estimates_confirmed: bool
    updated_at: datetime


class TemplateOut(Schema):
    id: str
    label: str


class FirstSiteOut(Schema):
    id: str
    subdomain: str
    display_name: str
    deleted: bool


class SetupOut(Schema):
    state: str
    blocked_reason: str | None
    missing: list[str]
    first_site: FirstSiteOut | None
    can_create_site: bool
    templates: list[TemplateOut]
    site_host_suffix: str
    started_at: datetime | None
    minimum_completed_at: datetime | None


def _subdomain_of(site):
    """The site's active name, from the prefetched rows when there are any."""
    for row in site.subdomains.all():
        if row.state == Subdomain.State.ACTIVE:
            return row.name
    return ""


def site_out(site):
    subdomain = _subdomain_of(site)
    parent = settings.SITE_PARENT_HOST
    return {
        "id": site.public_id,
        "template": site.template,
        "template_label": templates.TEMPLATES.get(site.template, site.template),
        "display_name": site.display_name,
        "subdomain": subdomain,
        "host": f"{subdomain}.{parent}" if parent and subdomain else None,
        "business_name": site.business_profile.business_name,
        "contact_email": site.contact_email,
        "contact_mobile": site.contact_mobile,
        "accent_color": site.accent_color,
        "instagram_url": site.instagram_url,
        "tiktok_url": site.tiktok_url,
        "created_at": site.created_at,
    }


@router.post("/sites/subdomain-check/", response=SubdomainCheckOut)
def subdomain_check(request, payload: SubdomainCheckIn):
    hit("subdomain-check", request.account.pk, limit=CHECK_LIMIT, window=CHECK_WINDOW)
    name, status = subdomains.check(payload.subdomain)
    return {"subdomain": name, "status": status, "message": subdomains.MESSAGES[status]}


@router.get("/sites/", response=SiteListOut)
def site_list(request, limit: int = DEFAULT_LIMIT, cursor: str | None = None):
    mine = (
        Site.objects.filter(account=request.account, deleted_at__isnull=True)
        .select_related("business_profile")
        .prefetch_related("subdomains")
    )
    items, next_cursor = page(mine, limit=limit, cursor=cursor, prefix="site")
    return {"items": [site_out(site) for site in items], "next_cursor": next_cursor}


@router.post("/sites/", response={201: SiteOut})
def site_create(request, payload: SiteCreateIn):
    hit("site-create", request.account.pk, limit=CREATE_LIMIT, window=CREATE_WINDOW)
    site = services.create_first_site(
        request.account,
        template=payload.template,
        subdomain=payload.subdomain,
        display_name=payload.display_name,
    )
    return 201, site_out(site)


@router.get("/sites/{site_id}/", response=SiteOut)
def site_detail(request, site_id: str):
    return site_out(services.site_for(request.account, site_id))


@router.patch("/sites/{site_id}/", response=SiteOut)
def site_update(request, site_id: str, payload: SitePatch):
    site = services.site_for(request.account, site_id)
    return site_out(
        services.update_site(request.account, site, payload.dict(exclude_unset=True))
    )


@router.get("/sites/{site_id}/business-profile/", response=BusinessProfileOut)
def business_profile_detail(request, site_id: str):
    return services.site_for(request.account, site_id).business_profile


@router.patch("/sites/{site_id}/business-profile/", response=BusinessProfileOut)
def business_profile_update(request, site_id: str, payload: BusinessProfilePatch):
    site = services.site_for(request.account, site_id)
    return services.update_business_profile(
        request.account, site, payload.dict(exclude_unset=True)
    )


@router.get("/account/setup/", response=SetupOut)
def account_setup(request):
    """The onboarding state, derived from what is stored. It takes no input and writes nothing."""
    setup = onboarding.evaluate(request.account)
    site = setup.first_site
    return {
        "state": setup.state,
        "blocked_reason": setup.blocked_reason,
        "missing": setup.missing,
        "first_site": site and {
            "id": site.public_id,
            "subdomain": _subdomain_of(site),
            "display_name": site.display_name,
            "deleted": site.deleted_at is not None,
        },
        "can_create_site": setup.state == onboarding.NOT_STARTED,
        "templates": templates.offered(),
        "site_host_suffix": settings.SITE_PARENT_HOST,
        "started_at": setup.started_at,
        "minimum_completed_at": setup.minimum_completed_at,
    }
