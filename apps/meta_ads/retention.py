"""The browser identifiers kept for an order (MetaOrderContext) are deleted 30 days after they
were written, or sooner, when the order's Purchase is sent (apps.meta_ads.services). Touches
nothing while META_EVENTS_ENABLED is off."""
from datetime import timedelta

from django.conf import settings

from apps.common import retention
from apps.meta_ads.models import MetaDelivery, MetaOrderContext, MetaSettings
from apps.sites.models import Site

CONTEXT_RETENTION = timedelta(days=30)
DELIVERY_RETENTION = timedelta(days=365)  # Coach Ed's ruling D1, 2026-11-14


@retention.behind_switch
def order_contexts(now, dry_run):
    if not settings.META_EVENTS_ENABLED:
        return 0
    return retention.remove(MetaOrderContext.objects.filter(created_at__lte=now - CONTEXT_RETENTION), dry_run)


retention.register("meta_order_contexts", order_contexts)


@retention.behind_switch
def deliveries(now, dry_run):
    return retention.remove(MetaDelivery.objects.filter(created_at__lte=now - DELIVERY_RETENTION), dry_run)


retention.register("meta_deliveries", deliveries)


# -- account disposal (called by apps.accounts.retention) ---------------------------------------

SETTINGS, CONTEXTS = "meta_settings", "meta_order_contexts"


def blanked():
    """The field names a disposal blanks or deletes, for its audit entry. Delivery rows hold no
    personal data and stay."""
    return {SETTINGS: ["token_encrypted", "test_event_code"], CONTEXTS: ["all rows deleted"]}


def blank_meta_of(account, dry_run=False):
    """Blank the token and test event code of the account's sites and delete their saved browser
    identifiers. Runs whatever the switch says, because the rows may predate it being turned off.
    Answers empty when the account has none. Call it inside a transaction."""
    return blank_meta(Site.objects.filter(account=account), dry_run)


def blank_meta(sites, dry_run=False):
    """The same for the sites in `sites` (a Site queryset)."""
    settings_rows = MetaSettings.objects.filter(site__in=sites)
    contexts = MetaOrderContext.objects.filter(site__in=sites)
    counts = {SETTINGS: settings_rows.count(), CONTEXTS: contexts.count()}
    if not any(counts.values()):
        return {}
    if not dry_run:
        settings_rows.update(token_encrypted="", test_event_code="")
        contexts.delete()
    return counts
