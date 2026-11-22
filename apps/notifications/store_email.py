"""What a store's own members and buyers are emailed: `send_store_email` is the one call.

With STORE_EMAILS_BRANDED off it is exactly the plain-text email every caller always sent. On,
the same words go out as plain text plus a branded HTML layout, from the store's own address when
STORE_SENDERS names one. Nothing to a Subscriber, an operator or an account holder comes through
here: those stay on NegosyoOnline. The text of a body is the caller's approved wording; this
module only wraps it. Bodies and codes are never logged."""
import logging
import re
from email.utils import parseaddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.template.loader import render_to_string

from apps.notifications.services import send_email

logger = logging.getLogger(__name__)

NEUTRAL_NAME = "Your store"
DEFAULT_COLOR = "#0b3d91"  # the NegosyoOnline blue
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_URL = re.compile(r"https?://[^\s<>\"']+")

# A store name that reads as one of these is not used as the From name (see `safe_from_name`).
# Whole words, any case. Santé is deliberately not listed: its distributors are the first clients.
BLOCKED_NAME_WORDS = (
    "negosyoonline", "negosyo online", "paypal", "gcash", "paymaya", "maya bank", "maya wallet", "bdo", "bpi", "metrobank",
    "unionbank", "landbank", "security bank", "pnb", "rcbc", "chinabank", "grabpay", "shopeepay",
)
_BLOCKED_NAME = re.compile(
    r"(?<![0-9a-z])(?:" + "|".join(re.escape(w) for w in BLOCKED_NAME_WORDS) + r")(?![0-9a-z])", re.I
)


def safe_from_name(name):
    """The From display name: `name`, or NEUTRAL_NAME when it names NegosyoOnline, a bank or an
    e-wallet. Applies to the display name only; the From address never comes from the store."""
    if _BLOCKED_NAME.search(name or ""):
        logger.warning("Store From name replaced by the neutral name (blocked word).")
        return NEUTRAL_NAME
    return name


def parse_senders(raw):
    """({key: address}, [problems]) from the STORE_SENDERS text. Bad pairs are skipped."""
    senders, problems = {}, []
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, _, address = pair.partition("=")
        key, address = key.strip().lower(), address.strip()
        try:
            if not key or "\r" in pair or "\n" in pair or parseaddr(address)[1] != address:
                raise ValidationError("shape")
            validate_email(address)
        except ValidationError:
            problems.append(key or pair)
            continue
        senders[key] = address
    return senders, problems


def sender_address(site):
    """The store's own From address from STORE_SENDERS, or "". Matches the site id or any active
    web-address name of the store."""
    senders, _ = parse_senders(settings.STORE_SENDERS)
    if not senders:
        return ""
    keys = [site.public_id.lower()]
    keys += [name.lower() for name in site.subdomains.filter(state="active").values_list("name", flat=True)]
    for key in keys:
        if key in senders:
            return senders[key]
    return ""


def domain_sender(site):
    """The From address on the store's own custom domain, or "" (the platform address). Keyed on
    the site's own claim row, never on a hostname from a request. It is built here, at send time,
    as `<sender_local_part>@<the site's succeeded registration name>` and only while all of this
    holds: DOMAINS_ENABLED and STORE_EMAILS_BRANDED are on, the claim is in a holding state (not
    being removed), its name is the registration's name, Resend's status is `verified` and was
    read within 72 hours (the daily read; `temporary_failure` and `failed` fall back at once).
    Never raises; any doubt is the platform address."""
    if not (settings.DOMAINS_ENABLED and settings.STORE_EMAILS_BRANDED):
        return ""
    try:
        from datetime import timedelta

        from django.utils import timezone

        from apps.domains.models import (
            HOLDING_STATES, ClaimState, Domain, DomainRegistration, EmailState, RegistrationState,
        )

        claim = Domain.objects.filter(
            site=site, state__in=[s for s in HOLDING_STATES if s != ClaimState.REMOVAL_PENDING],
            email_state__in=(EmailState.VERIFIED, EmailState.ACTIVE), resend_status="verified",
        ).exclude(resend_domain_id="").first()
        if claim is None or claim.resend_checked_at is None:
            return ""
        if timezone.now() - claim.resend_checked_at > timedelta(hours=72):
            return ""
        reg = DomainRegistration.objects.filter(site=site, state=RegistrationState.SUCCEEDED).first()
        if reg is None or not reg.name or reg.name != claim.name:
            return ""
        address = f"{claim.sender_local_part}@{reg.name}"
        if parseaddr(address)[1] != address:
            return ""
        validate_email(address)
        return address
    except Exception:  # noqa: BLE001 - a doubt is the platform address, never a failed email
        logger.warning("Store domain sender lookup failed; using the platform address.")
        return ""


def safe_color(value):
    """`#rrggbb` or the NegosyoOnline blue. Checked before it can enter CSS."""
    return value if _HEX_COLOR.match(value or "") else DEFAULT_COLOR


def button_text_color(background):
    """White on a dark colour, near-black on a pale one."""
    r, g, b = (int(background[i:i + 2], 16) for i in (1, 3, 5))
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#ffffff" if luminance < 0.6 else "#111827"


def brand_for(site):
    """The store's own brand, from data that exists. Never the owner's account email."""
    from apps.members import messaging
    from apps.sites.models import SiteBusinessProfile
    from apps.storefront.services import store_contact_email

    profile = SiteBusinessProfile.objects.filter(site=site).first()
    return {
        "name": messaging.store_name(site),
        "color": site.accent_color,
        "email": store_contact_email(site, profile),
        "mobile": site.contact_mobile,
        "address": profile.business_address if profile else "",
    }


def _lines(text, code):
    """Paragraphs of lines of (text, kind) parts, kind "code", "url" or "text". Escaping is left
    to the template engine."""
    paragraphs = []
    for block in re.split(r"\n\s*\n", text.strip("\n")):
        lines = []
        for line in block.split("\n"):
            parts, rest = [], line
            if code and code in rest:
                before, _, rest = rest.partition(code)
                parts += _split_urls(before) + [(code, "code")]
            parts += _split_urls(rest)
            lines.append([p for p in parts if p[0]])
        paragraphs.append(lines)
    return paragraphs


def _split_urls(text):
    parts, pos = [], 0
    for match in _URL.finditer(text):
        url = match.group(0).rstrip(".,;)")
        parts.append((text[pos:match.start()], "text"))
        parts.append((url, "url"))
        pos = match.start() + len(url)
    parts.append((text[pos:], "text"))
    return parts


def default_footer_reason(brand):
    return f"You are receiving this because of your order or account with {brand['name']}."


def footer_lines(brand, footer_reason=None):
    """The footer, one line each, only what is present. `footer_reason` replaces the sentence
    about why the person is receiving this; None keeps the order-or-account sentence."""
    lines = [brand["name"]]
    lines += [v for v in (brand["email"], brand["mobile"], brand["address"]) if v]
    lines.append(footer_reason or default_footer_reason(brand))
    return lines


def render_store(brand, *, subject, body, code="", cta=None, footer_reason=None):
    """(text, html). `text` is `body` plus the footer lines; `html` is one 600px table layout.
    `code` is shown large in the sentence that holds it, once. `cta` is an optional
    (label, https url) button, HTML only."""
    color = safe_color(brand.get("color"))
    if cta and not str(cta[1]).startswith(("https://", "http://")):
        cta = None
    footer = footer_lines(brand, footer_reason)
    text = body.rstrip("\n") + "\n\n--\n" + "\n".join(footer) + "\n"
    html = render_to_string("notifications/email/store.html", {
        "subject": subject,
        "brand": brand,
        "color": color,
        "color_text": button_text_color(color),
        "paragraphs": _lines(body, code),
        "cta": cta,
        "footer_contact": [v for v in (brand["email"], brand["mobile"], brand["address"]) if v],
        "footer_reason": footer_reason or default_footer_reason(brand),
        "preheader": re.sub(r"\s+", " ", body.replace(code, "") if code else body).strip()[:90],
    })
    return text, html


def send_store_email(
    site, *, to, category, subject, body, code="", cta=None,
    reply_to=None, footer_reason=None, branded=None, log_detail=True,
):
    """The one call for an email to a store's member or buyer. Returns the EmailMessage record.
    Raises what `send_email` raises (EmailNotSent) and ValueError for a bad sender or Reply-To,
    as the callers already handle.

    The four optional arguments are for the inquiry replies and change nothing when left out:
    `reply_to` (default: the owner's verified email) is checked like the default one;
    `footer_reason` replaces the footer's reason sentence; `branded=True` uses the branded
    layout and the store's own sender whatever STORE_EMAILS_BRANDED says (None follows it);
    `log_detail=False` keeps the failure's exception text out of the log."""
    from apps.members import messaging

    from_name, owner_email = messaging.sender(site)
    reply_to = owner_email if reply_to is None else reply_to
    if branded is None:
        branded = settings.STORE_EMAILS_BRANDED
    extra = {} if log_detail else {"log_detail": False}
    if not branded:
        return send_email(
            to=to, category=category, subject=subject, body=body, sender_name=from_name, reply_to=reply_to,
            **extra,
        )
    text, html = render_store(
        brand_for(site), subject=subject, body=body, code=code, cta=cta, footer_reason=footer_reason,
    )
    return send_email(
        to=to, category=category, subject=subject, body=text, html_body=html,
        sender_name=safe_from_name(from_name), reply_to=reply_to, from_address=sender_address(site) or domain_sender(site),
        **extra,
    )
