"""What the storefront surface answers. Reads only; nothing here writes."""
from apps.common.phones import normalise_ph_mobile
from apps.sites import templates
from apps.sites.api_public import served
from apps.sites.models import SiteBusinessProfile


def served_sante_site(host):
    """The Santé site this host serves right now, or None. The decision is `served`, the one
    function GET /v1/public/sites/by-host/ uses, so the two can never disagree."""
    found = served(host)
    if found is None or found[0].template != templates.SANTE:
        return None
    return found[0]


def _public_mobile(raw):
    try:
        return normalise_ph_mobile(raw)
    except ValueError:
        return ""


def site_chrome(site):
    """The parts every Santé page of this site shows: the store, its registration card and
    its distributor card. Never the owner's email or any other account field."""
    profile = SiteBusinessProfile.objects.filter(site=site).first()
    return {
        "id": site.public_id,
        "template": site.template,
        "display_name": site.display_name,
        "accent_color": site.accent_color,
        "facebook_url": site.facebook_url,
        "instagram_url": site.instagram_url,
        "tiktok_url": site.tiktok_url,
        "business": {
            "registered_name": profile.registered_name if profile else "",
            "tin": profile.tin if profile else "",
            "dti_business_number": profile.dti_business_number if profile else "",
            "contact_email": site.contact_email
            or (profile.privacy_contact_email if profile else ""),
            "contact_mobile": site.contact_mobile,
            "has_seal": False,  # private files arrive with slice 8
        },
        "distributor": {
            "name": site.account.name.strip() or site.display_name,
            "mobile": _public_mobile(site.contact_mobile),
            "has_photo": False,  # private files arrive with slice 8
        },
        "policy_links": [],  # filled when the policy pages exist
        "free_shipping_banner": False,  # on only once the shipping setting exists
    }
