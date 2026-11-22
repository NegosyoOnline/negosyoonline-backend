"""Eligibility and the first-payment hook (design R4.4 item 2, R4.14.5, ruling SD1).

`after_confirm(site)` is the one line `confirm_transfer` calls: it registers a robust
`on_commit` callback, so a domain bug never fails or undoes a Finance confirmation, and nothing
runs for a confirmation that is refused or rolled back. `on_first_payment(site)` is idempotent:
it writes the `eligible` event once, only when the gate holds, and notifies the Subscriber once
("choose your domain"). It buys nothing. `safety_net()` finds the sites the hook missed.
Nothing here reads a domains table while DOMAINS_ENABLED is off.
"""
import logging
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.models import Subscription
from apps.domains import events, gate, names
from apps.domains.models import DomainEvent, DomainTldPrice
from apps.notifications.services import EmailNotSent, send_email
from apps.sites.models import Site

logger = logging.getLogger(__name__)

PRICE_FRESH = timedelta(hours=36)
NOTICE = (
    "Choose your domain",
    "Hello,\n\nYour first payment was approved. You can now choose your domain name in your "
    "NegosyoOnline Dashboard under Settings, then Domain.\n\nNegosyoOnline",
)


def after_confirm(site_id):
    """Called inside `confirm_transfer`. Runs `on_first_payment` after the commit, never failing it."""
    transaction.on_commit(partial(_run_hook, site_id), robust=True)


def _run_hook(site_id):
    try:
        on_first_payment(Site.objects.get(pk=site_id))
    except Exception:  # noqa: BLE001 - a domain bug never breaks a money confirmation
        logger.exception("domains first-payment hook failed site_id=%s", site_id)


def on_first_payment(site, now=None):
    """Mark the site eligible once and tell the Subscriber. Returns a short word."""
    if not settings.DOMAINS_ENABLED:
        return "off"
    if gate.refusal(site, now):
        return "not_eligible"
    try:
        with transaction.atomic():  # a savepoint: a duplicate insert is "already eligible"
            events.log(site, "eligible", now=now)
    except IntegrityError:
        return "already"
    _notify(site)
    return "marked"


def _notify(site):
    account = site.account
    try:
        send_email(to=account.email, category="domains_choose", subject=NOTICE[0], body=NOTICE[1])
    except EmailNotSent:
        logger.warning("Domain notice not sent for site %s", site.public_id)


def safety_net(now=None):
    """Sites that pass the gate and have no `eligible` event (a missed hook). Returns the count marked."""
    if not settings.DOMAINS_ENABLED or not settings.BILLING_ENABLED:
        return 0
    marked = 0
    already = DomainEvent.objects.filter(kind="eligible").values("site_id")
    candidates = (
        Site.objects.filter(
            subscription__state=Subscription.State.ACTIVE, subscription__billing_exempt=False,
            subscription__cycles__settlement="paid", deleted_at__isnull=True,
        )
        .exclude(pk__in=already).distinct().select_related("account")
    )
    for site in candidates[:200]:
        if on_first_payment(site, now) == "marked":
            marked += 1
    return marked


def tld_of(name):
    return names.public_suffix(name) or name.rsplit(".", 1)[-1]


def offered_tlds(now=None):
    """The extensions on offer: allowed by settings, allowed by the latest sentinel reading, and
    read less than 36 hours ago (a stale reading withdraws the extension, fail closed)."""
    now = now or timezone.now()
    rows = DomainTldPrice.objects.filter(
        allowed=True, read_at__gte=now - PRICE_FRESH, tld__in=list(settings.DOMAINS_ALLOWED_TLDS)
    )
    return sorted(row.tld for row in rows)
