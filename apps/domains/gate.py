"""The payment gate (design R4.4 item 2, ruling SD1): a site may buy or re-attach a domain only
while its subscription is ACTIVE with at least one PAID cycle (the first confirmed subscription
payment of any plan; a trial never counts, an early first payment does), the site is a Santé
site that is not deleted and not on operator hold, and it is not billing-exempt.

`refusal(site)` returns "" when the gate holds, else a short code. It reads, writes nothing, and
fails closed (billing off, no subscription, an error all refuse). It is read at the owner's
Check and Buy, in the purchase step 1 under the lock, and in the worker before every setup step.
"""
import logging

from django.conf import settings
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import Subscription
from apps.sites import templates

logger = logging.getLogger(__name__)

MESSAGES = {
    "site_off": "This site is not available.",
    "on_hold": "This site is on hold.",
    "trial": "Your domain unlocks after your first payment is approved.",
    "not_active": "Your domain is available while your subscription is active.",
    "no_paid_cycle": "Your domain unlocks after your first payment is approved.",
    "exempt": "Your domain is not part of this plan.",
    "billing_off": "Your domain is not available yet.",
}


def refusal(site, now=None):
    try:
        if site.deleted_at is not None or site.template != templates.SANTE:
            return "site_off"
        if site.operator_hold:
            return "on_hold"
        if not settings.BILLING_ENABLED:
            return "billing_off"
        subscription = Subscription.objects.filter(site=site).first()
        if subscription is None:
            return "billing_off"
        if subscription.billing_exempt:
            return "exempt"
        position = billing.position(site, at=now or timezone.now())
        if position.state == Subscription.State.TRIAL_ACTIVE:
            return "trial"
        if position.state != Subscription.State.ACTIVE:
            return "not_active"
        if not billing.has_paid_cycle(subscription):
            return "no_paid_cycle"
        return ""
    except Exception:  # noqa: BLE001 - unknown fails closed
        logger.exception("domains gate could not be read for site %s", getattr(site, "pk", None))
        return "billing_off"


def message(code):
    return MESSAGES.get(code, "Your domain is not available yet.")
