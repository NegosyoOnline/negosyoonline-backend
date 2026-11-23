"""What the storefront surface answers. The site call only reads. The contact call sends one
email and keeps nothing of the visitor's message, except that with INQUIRY_CONTACT_INBOX_ENABLED
on it also stores a copy in the store's Inbox (apps/inquiries/stored.py)."""
import ipaddress
import logging
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from apps.common import throttle
from apps.common.errors import ApiError
from apps.common.phones import normalise_ph_mobile
from apps.meta_ads import services as meta_ads
from apps.notifications.services import send_email
from apps.pack import policies
from apps.sites import templates
from apps.sites.api_public import served
from apps.sites.models import SiteBusinessProfile

logger = logging.getLogger(__name__)


def served_sante_site(host, include_held=False):
    """The Santé site this host serves right now, or None. The decision is `served`, the one
    function GET /v1/public/sites/by-host/ uses, so the two can never disagree (a held store
    answers the member routes only, with `include_held`)."""
    found = served(host, include_held=include_held)
    if found is None or found[0].template != templates.SANTE:
        return None
    return found[0]


def _public_mobile(raw):
    try:
        return normalise_ph_mobile(raw)
    except ValueError:
        return ""


def store_contact_email(site, profile):
    """The store's own contact email, as its pages show it. Never the owner's account email."""
    return site.contact_email or (profile.privacy_contact_email if profile else "")


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
            "contact_email": store_contact_email(site, profile),
            "contact_mobile": site.contact_mobile,
            "has_seal": bool(profile and profile.seal_key),
        },
        "distributor": {
            "name": site.account.name.strip() or site.display_name,
            "mobile": _public_mobile(site.contact_mobile),
            "has_photo": False,  # private files arrive with slice 8
        },
        "policy_links": policies.links(site),  # live pages only, none while the pack is off
        "free_shipping_banner": False,  # on only once the shipping setting exists
        "meta_pixel_id": meta_ads.pixel_id_for(site),  # "" unless Meta events are on and the site set one
    }


# --- "Send a Message" ------------------------------------------------------------------------

CONTACT_CATEGORY = "storefront_contact"
CONTACT_LIMIT = 10  # messages from one visitor address ...
CONTACT_WINDOW = 60 * 60  # ... in this many seconds, whichever stores they were sent to
MAX_NAME, MAX_EMAIL, MAX_SUBJECT, MAX_MESSAGE = 120, 254, 200, 4000
_BREAKS = ("Cc", "Zl", "Zp")  # control characters and the Unicode line and paragraph breaks


def _one_line(value):
    """The value as one line: every line break and control character becomes a space, so the
    text can sit in an email header."""
    plain = "".join(" " if unicodedata.category(char) in _BREAKS else char for char in value or "")
    return " ".join(plain.split())


def _visitor_key(visitor_ip):
    try:
        return str(ipaddress.ip_address((visitor_ip or "").strip()))
    except ValueError:
        return "unknown"  # every visitor with no usable address shares one count


def _contact_values(fields):
    values = {
        "name": _one_line(fields.get("name"))[:MAX_NAME],
        "email": (fields.get("email") or "").strip().lower()[:MAX_EMAIL],
        "subject": _one_line(fields.get("subject"))[:MAX_SUBJECT],
        "message": (fields.get("message") or "").strip()[:MAX_MESSAGE],
    }
    errors = {}
    if not values["name"]:
        errors["name"] = ["Please enter your name."]
    try:
        validate_email(values["email"])
    except ValidationError:
        errors["email"] = ["Please enter a valid email address."]
    if not values["subject"]:
        errors["subject"] = ["Please enter a subject."]
    if not values["message"]:
        errors["message"] = ["Please enter a message."]
    if errors:
        raise ApiError("invalid_request", "Some values are missing or invalid.", fields=errors)
    return values


def send_contact_message(site, fields, visitor_ip):
    """Email a visitor's "Send a Message" form to the store. Raises ApiError when it was not
    sent. Nothing of the message is logged. It is stored only when INQUIRY_CONTACT_INBOX_ENABLED
    is on (see `_keep_in_inbox`); otherwise the email is the only copy.

    `visitor_ip` is believed only because the call carried the server credential."""
    if fields.get("website"):  # the hidden field people never see: answered as sent, not sent
        return
    throttle.hit(
        "storefront-contact", _visitor_key(visitor_ip), limit=CONTACT_LIMIT, window=CONTACT_WINDOW
    )
    values = _contact_values(fields)
    to = store_contact_email(site, SiteBusinessProfile.objects.filter(site=site).first())
    if not to:
        raise ApiError("no_contact_email", "This store has no contact email.", status=409)
    store = _one_line(site.display_name)
    send_email(
        to=to,
        category=CONTACT_CATEGORY,
        subject=f"[{store}] {values['subject']} (from {values['name']})",
        body=(
            f"New message from the {store} website contact form.\n\n"
            f"Name: {values['name']}\nEmail: {values['email']}\n"
            f"Subject: {values['subject']}\n\n{values['message']}\n"
        ),
        sender_name=store,
        reply_to=values["email"],
    )
    _keep_in_inbox(site, values)


def _keep_in_inbox(site, values):
    """With INQUIRY_CONTACT_INBOX_ENABLED on and this site's email_enabled on, keep a copy as a stored email conversation, after
    the email has gone. Off, or with the inquiries tables not yet there, nothing is read or
    written. A failure here never breaks the form: only the error's class name is logged."""
    from apps.inquiries import stored
    from apps.inquiries.switch import contact_inbox_on, stored_tables_exist

    if not contact_inbox_on():
        return
    try:
        if stored_tables_exist():
            row = stored.channel_settings(site)
            if not (row and row.email_enabled):  # the site's own switch (Settings > Inbox)
                return
            stored.add_contact_message(
                site, name=values["name"], email=values["email"], subject=values["subject"],
                message=values["message"],
            )
    except Exception as error:  # noqa: BLE001 - the visitor's message has been emailed already
        logger.error("Contact message not kept in the Inbox (%s).", type(error).__name__)
