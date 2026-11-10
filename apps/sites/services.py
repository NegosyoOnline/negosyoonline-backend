"""Site creation and settings. Other apps call these functions, never the tables."""
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from apps.accounts.services import record_event
from apps.common.errors import ApiError
from apps.common.phones import normalise_ph_mobile
from apps.sites import onboarding, subdomains, templates
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain

BIR_SERIAL = re.compile(r"^\d{3}[A-Z]{2}\d{14}$")  # e.g. 113RC20260000017598
SOCIAL_HOSTS = {
    "facebook_url": ("Facebook", {"facebook.com", "www.facebook.com", "m.facebook.com"}),
    "instagram_url": ("Instagram", {"instagram.com", "www.instagram.com"}),
    "tiktok_url": ("TikTok", {"tiktok.com", "www.tiktok.com"}),
}
# accent_color is stored but not editable here: no colour control while ruling R3 stands.
SITE_FIELDS = (
    "display_name", "contact_email", "contact_mobile", "facebook_url", "instagram_url", "tiktok_url",
)
SHIPPING_FIELDS = (
    "shipping_processing_time", "shipping_metro_manila_estimate", "shipping_luzon_estimate",
    "shipping_visayas_estimate", "shipping_mindanao_estimate", "shipping_other_areas_estimate",
)
PROFILE_TEXT_FIELDS = (
    "business_name", "registered_name", "tin", "dti_business_number", "bir_rsn",
    "business_address", "complaints_contact_name", "privacy_contact_email", *SHIPPING_FIELDS,
)
PROFILE_FIELDS = (*PROFILE_TEXT_FIELDS, "shipping_estimates_confirmed")
MAX_LENGTHS = {
    "business_name": 160, "registered_name": 120, "complaints_contact_name": 120,
    "business_address": 255, **{name: 60 for name in SHIPPING_FIELDS},
}
NOT_EDITABLE = "This cannot be changed here."
# The onboarding minimum. Once set, a blank value would put the site back into setup.
REQUIRED = {
    "business_name": "Enter your business name.",
    "contact_email": "Enter your store email.",
    "contact_mobile": "Enter your store mobile.",
}


def _invalid(fields):
    return ApiError(
        "invalid_request",
        "Some values are missing or invalid.",
        fields={name: [message] for name, message in fields.items()},
    )


def _subdomain_error(status):
    return ApiError(
        f"subdomain_{status}",
        subdomains.MESSAGES[status],
        status=409 if status == subdomains.TAKEN else 400,
        fields={"subdomain": [subdomains.MESSAGES[status]]},
    )


def _is_email(value):
    """A valid address that also fits the column: validate_email alone accepts longer ones."""
    if len(value) > 254:
        return False
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def site_for(account, site_id, template=None):
    """The only way to reach a Site from a request. Missing, deleted and foreign all answer 404.

    `template` is the capability check: a Santé-only feature passes `templates.SANTE`, and a
    site whose stored template differs answers the same 404, so it never shows the site exists.
    """
    sites = Site.objects.filter(public_id=site_id, account=account, deleted_at__isnull=True)
    if template is not None:
        sites = sites.filter(template=template)
    site = sites.first()
    if site is None:
        raise ApiError("not_found", "Not found.", status=404)
    return site


def active_subdomain(site):
    return site.subdomains.get(state=Subdomain.State.ACTIVE).name


def _locked_record(account):
    """The account's Onboarding row, locked. Serialises every site write for one account."""
    Onboarding.objects.get_or_create(account=account)
    return (
        Onboarding.objects.select_for_update(of=("self",))
        .select_related("first_site")
        .get(account=account)
    )


@transaction.atomic
def create_first_site(account, *, template, subdomain, display_name):
    """Create the account's first Site with its Subdomain and business profile, or nothing."""
    if account.email_verified_at is None:
        raise ApiError("email_not_verified", "Confirm your email to continue.", status=403)
    record = _locked_record(account)
    if record.first_site is not None and record.first_site.deleted_at is not None:
        raise ApiError(
            "first_site_removed", "This site was removed. Please contact support.", status=409
        )
    if Site.objects.filter(account=account).count() >= settings.SITES_PER_ACCOUNT_LIMIT:
        raise ApiError("site_limit_reached", "You can't add another site yet.", status=409)
    if template not in templates.TEMPLATES:
        raise ApiError(
            "invalid_template", "Choose a site type.", fields={"template": ["Choose a site type."]}
        )
    display_name = (display_name or "").strip()
    if not 1 <= len(display_name) <= 80:
        raise _invalid({"display_name": "Use 1 to 80 characters."})
    name, status = subdomains.check(subdomain)
    if status != subdomains.AVAILABLE:
        raise _subdomain_error(status)
    site = Site.objects.create(account=account, template=template, display_name=display_name)
    try:
        with transaction.atomic():
            Subdomain.objects.create(name=name, site=site)
    except IntegrityError:  # another account took the name between the check and the insert
        raise _subdomain_error(subdomains.TAKEN) from None
    SiteBusinessProfile.objects.create(site=site)
    record.first_site = site
    record.save(update_fields=["first_site", "updated_at"])
    onboarding.stamp(account)
    record_event(account, "site_created", site=site.public_id, subdomain=name, template=template)
    return site


def _clean_site(data):
    cleaned, errors = {}, {}
    for name, raw in data.items():
        if name not in SITE_FIELDS:
            errors[name] = NOT_EDITABLE
            continue
        value = (raw or "").strip() if isinstance(raw, str) or raw is None else raw
        if not isinstance(value, str):
            errors[name] = "Enter text."
        elif name == "display_name":
            if not 1 <= len(value) <= 80:
                errors[name] = "Use 1 to 80 characters."
        elif not value:
            if name in REQUIRED:
                errors[name] = REQUIRED[name]  # the social links may be cleared
        elif name == "contact_email":
            value = value.lower()
            if not _is_email(value):
                errors[name] = "Enter a valid email address."
        elif name == "contact_mobile":
            try:
                value = normalise_ph_mobile(value)
            except ValueError as exc:
                errors[name] = str(exc)
        else:
            label, hosts = SOCIAL_HOSTS[name]
            parts = urlsplit(value)
            if parts.scheme != "https" or parts.hostname not in hosts or len(value) > 200:
                errors[name] = f"Enter the full https link to your {label} page."
        cleaned[name] = value
    return cleaned, errors


def _tin(value):
    digits = re.sub(r"\D", "", value)
    if len(digits) == 9:
        return "-".join((digits[:3], digits[3:6], digits[6:]))
    if len(digits) == 14:
        return "-".join((digits[:3], digits[3:6], digits[6:9], digits[9:]))
    raise ValueError("Enter the 9-digit TIN, or 9 digits plus the 5-digit branch code.")


def _clean_profile(data):
    cleaned, errors = {}, {}
    for name, raw in data.items():
        if name == "shipping_estimates_confirmed":
            if isinstance(raw, bool):
                cleaned[name] = raw
            else:
                errors[name] = "Choose yes or no."
            continue
        if name not in PROFILE_TEXT_FIELDS:
            errors[name] = NOT_EDITABLE
            continue
        if raw is not None and not isinstance(raw, str):
            errors[name] = "Enter text."
            continue
        value = " ".join((raw or "").split())
        try:
            if not value:
                if name in REQUIRED:
                    raise ValueError(REQUIRED[name])  # any other blank value clears the field
            elif name in MAX_LENGTHS:
                if len(value) > MAX_LENGTHS[name]:
                    raise ValueError(f"Use up to {MAX_LENGTHS[name]} characters.")
            elif name == "privacy_contact_email":
                value = value.lower()
                if not _is_email(value):
                    raise ValueError("Enter a valid email address.")
            elif name == "tin":
                value = _tin(value)
            elif name == "dti_business_number":
                value = re.sub(r"[\s-]", "", value)
                if not value.isdigit() or not 4 <= len(value) <= 15:
                    raise ValueError("Enter the DTI business name number, digits only (4 to 15).")
            else:  # bir_rsn
                value = re.sub(r"\s", "", value).upper()
                if not BIR_SERIAL.fullmatch(value):
                    raise ValueError(
                        "Enter your BIR Official Control Number (OCN), "
                        "like 113RC20260000017598."
                    )
        except ValueError as exc:
            errors[name] = str(exc)
        cleaned[name] = value
    return cleaned, errors


def _save(row, cleaned, extra=()):
    for name, value in cleaned.items():
        setattr(row, name, value)
    row.save(update_fields=[*cleaned, *extra])


@transaction.atomic
def update_site(account, site, data):
    """Change the editable fields present in `data`. Either every field saves or none does."""
    cleaned, errors = _clean_site(data)
    if errors:
        raise _invalid(errors)
    _locked_record(account)
    site = Site.objects.select_for_update().get(pk=site_for(account, site.public_id).pk)
    if cleaned:
        _save(site, cleaned)
        onboarding.stamp(account)
        record_event(account, "site_updated", site=site.public_id, fields=sorted(cleaned))
    return site


@transaction.atomic
def update_business_profile(account, site, data):
    """Change the profile fields present in `data`. Either every field saves or none does."""
    cleaned, errors = _clean_profile(data)
    if errors:
        raise _invalid(errors)
    _locked_record(account)
    site = site_for(account, site.public_id)
    profile = SiteBusinessProfile.objects.select_for_update().get(site=site)
    if cleaned:
        _save(profile, cleaned, extra=("updated_at",))
        onboarding.stamp(account)
        record_event(
            account, "business_profile_updated", site=site.public_id, fields=sorted(cleaned)
        )
    return profile
