"""What the storefront surface answers. The site call only reads. The contact call sends one
email and keeps nothing of the visitor's message."""
import ipaddress
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from apps.common import throttle
from apps.common.errors import ApiError
from apps.common.phones import normalise_ph_mobile
from apps.notifications.services import send_email
from apps.pack import policies
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
            "has_seal": False,  # private files arrive with slice 8
        },
        "distributor": {
            "name": site.account.name.strip() or site.display_name,
            "mobile": _public_mobile(site.contact_mobile),
            "has_photo": False,  # private files arrive with slice 8
        },
        "policy_links": policies.links(site),  # live pages only, none while the pack is off
        "free_shipping_banner": False,  # on only once the shipping setting exists
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
    sent. Nothing of the message is stored or logged: the email is the only copy.

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
