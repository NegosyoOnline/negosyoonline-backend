"""The owner's BIR registration badge (the Registered shield with its QR and RSN): one image per site,
uploaded by the site owner, kept as a private file and shown in the storefront footer. The file's key
lives on the business profile and is never answered to a browser; the image is served by the API."""
from django.db import transaction

from apps.accounts.services import record_event
from apps.common import private_files
from apps.common.errors import ApiError
from apps.sites.models import Onboarding, Site, SiteBusinessProfile
from apps.sites.services import site_for

SEAL_MAX = 2 * 1024 * 1024
SEAL_FOLDER = "sites/seal"  # then site-<row id>/<random name>
ERRORS = {
    private_files.WRONG_TYPE: "Upload a JPEG, PNG or WebP image.",
    private_files.TOO_LARGE: "The file is too large. The limit is 2 MB.",
}
NOT_SAVED = "The image could not be saved. Try again in a moment."


def _invalid(message):
    return ApiError("invalid_request", "Some values are missing or invalid.", fields={"seal": [message]})


def _locked_profile(account, site):
    """The profile under the lock every site write of this account takes. Inside a transaction."""
    Onboarding.objects.select_for_update().filter(account=account).first()
    checked = site_for(account, site.public_id)
    Site.objects.select_for_update().get(pk=checked.pk)
    return SiteBusinessProfile.objects.select_for_update().get(site=checked), checked


def set_seal(account, site, upload):
    """Store a new badge (or replace the current one); the old file goes once the change is committed."""
    if upload is None:
        raise _invalid("Choose an image to upload.")
    try:
        content, content_type = private_files.check(upload, allowed=private_files.IMAGE_TYPES, max_bytes=SEAL_MAX)
    except private_files.FileRefused as refused:
        raise _invalid(ERRORS[refused.reason]) from None
    try:
        key = private_files.save(f"{SEAL_FOLDER}/site-{site.pk}", content, content_type)
    except private_files.StoreError:
        raise _invalid(NOT_SAVED) from None
    try:
        with transaction.atomic():
            profile, checked = _locked_profile(account, site)
            old = profile.seal_key
            profile.seal_key = key
            profile.save(update_fields=["seal_key", "updated_at"])
            record_event(account, "business_seal_set", site=checked.public_id)
            if old:
                transaction.on_commit(lambda: private_files.discard(old))
    except BaseException:
        private_files.discard(key)
        raise
    return profile


def remove_seal(account, site):
    with transaction.atomic():
        profile, checked = _locked_profile(account, site)
        old = profile.seal_key
        if old:
            profile.seal_key = ""
            profile.save(update_fields=["seal_key", "updated_at"])
            record_event(account, "business_seal_removed", site=checked.public_id)
            transaction.on_commit(lambda: private_files.discard(old))
    return profile


def image_of(site):
    """(bytes, content type) of the site's badge, or None when there is none or it cannot be read."""
    profile = SiteBusinessProfile.objects.filter(site=site).exclude(seal_key="").first()
    if profile is None:
        return None
    try:
        data = private_files.read(profile.seal_key)
    except Exception:  # noqa: BLE001 - an unreadable object reads as missing
        return None
    content_type = private_files.sniff(data[:16])
    return (data, content_type) if content_type.startswith("image/") else None
