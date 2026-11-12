"""Store policy pages' data (port of TWCinc's negosyoonline_sante_pack/policies.py). Five read-only
legal pages on the store host, one set for every Santé store, filled from that store's own business
profile: Subscribers are separate businesses, so the pages print the store owner's own details. A page
is live only when every value it prints is filled; until then it answers the unknown-route 404 and
has no footer link. The storefronts server renders the pages from this data.

POLICY_VERSION is one number for the whole set. Per the launch-day-only rule (Coach Ed 2026-10-30)
the effective date is set once on launch day: while POLICY_EFFECTIVE_DATE is None no date is printed.
The TIN is an internal tax identifier and is never part of the data."""
from django.conf import settings

from apps.sites.models import SiteBusinessProfile

POLICY_VERSION = "1"
POLICY_EFFECTIVE_DATE = None  # set once, on launch day, as a date such as "November 5, 2026"
POLICY_HISTORY = {"1": "First published set: Terms, Privacy, Shipping, Return & Refund and Payment (Coach Ed 2026-10-31)."}

# slug (the page's path) -> link label and page title
PAGES = {
    "terms-and-conditions": "Website Terms & Conditions",
    "privacy-policy": "Privacy & Cookies Policy",
    "shipping-policy": "Shipping & Delivery Policy",
    "return-refund-policy": "Return & Refund Policy",
    "payment-policy": "Payment Policy",
}
TERMS_SLUG = "terms-and-conditions"

# Platform-wide retention standards, TWCinc's wording (Coach Ed 2026-10-31).
PRIVACY = {
    "order_retention_period": "5 years, computed under applicable BIR rules, with legally required extensions",
    "account_retention_period": "Retained while the account is active and reviewed after 2 years of inactivity",
    "support_retention_period": "2 years after resolution, unless longer retention is justified",
    "marketing_retention_period": "Retained only while necessary, including relevant consent and withdrawal evidence",
    "security_retention_period": "90 days, except when needed for investigations",
    "analytics_retention_period": "A maximum of 14 months, subject to the provider's actual capabilities",
}
SHIPPING_REQUIRED = (
    "shipping_processing_time", "shipping_metro_manila_estimate", "shipping_luzon_estimate",
    "shipping_visayas_estimate", "shipping_mindanao_estimate",
)
SHIPPING_FIELDS = (*SHIPPING_REQUIRED, "shipping_other_areas_estimate")


def host_for(site):
    subdomain = site.subdomains.filter(state="active").order_by("pk").first()
    if subdomain is None or not settings.SITE_PARENT_HOST:
        return ""
    return f"{subdomain.name}.{settings.SITE_PARENT_HOST}"


def _profile(site):
    return SiteBusinessProfile.objects.filter(site=site).first()


def business_data(site, profile):
    dti = profile.dti_business_number if profile else ""
    host = host_for(site)
    return {
        "name": profile.registered_name if profile else "",
        "address": profile.business_address if profile else "",
        "email": site.contact_email, "phone": site.contact_mobile,
        "website_url": f"https://{host}" if host else "",
        "privacy_email": profile.privacy_contact_email if profile else "",
        "complaints_contact": profile.complaints_contact_name if profile else "",
        "registration_details": f"DTI Business Number {dti}" if dti else "",
    }


def shipping_data(profile):
    return {
        name.removeprefix("shipping_"): (getattr(profile, name) if profile else "")
        for name in SHIPPING_FIELDS
    }


def available(site):
    """Slugs (in display order) whose required store fields are all filled."""
    profile = _profile(site)
    d = business_data(site, profile)
    identity = all((d["name"], d["address"], d["email"], d["phone"], d["website_url"]))
    shipping = bool(profile and profile.shipping_estimates_confirmed) and all(
        getattr(profile, n) for n in SHIPPING_REQUIRED
    )
    ready = {
        "terms-and-conditions": identity,
        "privacy-policy": identity and bool(d["privacy_email"]),
        "shipping-policy": identity and shipping,
        "return-refund-policy": identity, "payment-policy": identity,
    }
    return [slug for slug in PAGES if ready[slug]]


def links(site):
    """[{label, path}] for the footer and the checkout: live pages only, none while the pack is off."""
    if not settings.PACK_ENABLED:
        return []
    return [{"label": PAGES[slug], "path": f"/{slug}/"} for slug in available(site)]


def page(site, slug):
    """The data of one live page, or None when it is not live."""
    if not settings.PACK_ENABLED or slug not in available(site):
        return None
    profile = _profile(site)
    return {
        "slug": slug, "title": PAGES[slug], "version": POLICY_VERSION,
        "effective_date": POLICY_EFFECTIVE_DATE, "business": business_data(site, profile),
        "shipping": shipping_data(profile), "privacy": PRIVACY,
        "links": links(site),
    }


def accepted_version(site):
    """The version recorded on an order: set only when the buyer could see at least one live page."""
    return POLICY_VERSION if links(site) else ""
