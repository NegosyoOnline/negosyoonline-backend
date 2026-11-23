"""Promotional text consent (PROMO-CONSENT, ClickUp z8uq9mbreu; contract
docs/plans/promo-consent-be-contract-2026-11-23.md).

A label and a filter only: nothing here stops, gates or footers a text. Dark behind
PROMO_SMS_CONSENT_ENABLED (`enabled()`). The checkbox wording lives here once; any change to it
must bump PROMO_SMS_VERSION (a test pins the pair)."""
from django.conf import settings
from django.utils import timezone

from .models import Member

PROMO_SMS_VERSION = "2026-11-23"
PROMO_SMS_TEXT = "Send me promotions from {store} by text. Reply STOP anytime."


def enabled():
    return bool(settings.PROMO_SMS_CONSENT_ENABLED)


def text_for(site):
    return PROMO_SMS_TEXT.format(store=site.display_name)


def contact_fields(site):
    """The three fields of the checkout/contact answer."""
    if not enabled():
        return {"promo_sms_consent_enabled": False, "promo_sms_version": None, "promo_sms_text": None}
    return {"promo_sms_consent_enabled": True, "promo_sms_version": PROMO_SMS_VERSION,
            "promo_sms_text": text_for(site)}


def set_preference(member, ok, *, at=None, version=None):
    """Set the member's choice now (or at `at`); the version is kept only for a yes."""
    member.promo_sms_ok = bool(ok)
    member.promo_sms_at = at or timezone.now()
    if ok:
        member.promo_sms_version = version or PROMO_SMS_VERSION
    member.save(update_fields=["promo_sms_ok", "promo_sms_at", "promo_sms_version", "updated_at"])
    return member


def opt_in_from_order(order, member):
    """Apply a yes the checkout recorded on the order's placed event (a bank-transfer order has its
    member at once; a Xendit order's member comes at payment). It never clears an existing yes, and never overrides a later choice."""
    from apps.pack import constants as C

    event = order.events.filter(kind=C.EVENT_PLACED).order_by("pk").first()
    version = (event.new or {}).get("promo_sms") if event else None
    if not (version and enabled()):
        return
    if member.promo_sms_at is not None and member.promo_sms_at > event.created_at:
        return  # a later choice (STOP, the member's own toggle) wins over the yes given at checkout
    set_preference(member, True, at=event.created_at, version=version)


def opt_out_mobile(site, mobile):
    """A STOP to the store: every member of this site with that mobile is no longer agreed."""
    now = timezone.now()
    return Member.objects.filter(site=site, mobile=mobile).update(
        promo_sms_ok=False, promo_sms_at=now, updated_at=now)
