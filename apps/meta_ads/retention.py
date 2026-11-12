"""The browser identifiers kept for an order (MetaOrderContext) are deleted 30 days after they
were written, or sooner, when the order's Purchase is sent (apps.meta_ads.services). Touches
nothing while META_EVENTS_ENABLED is off."""
from datetime import timedelta

from django.conf import settings

from apps.common import retention
from apps.meta_ads.models import MetaOrderContext

CONTEXT_RETENTION = timedelta(days=30)


@retention.behind_switch
def order_contexts(now, dry_run):
    if not settings.META_EVENTS_ENABLED:
        return 0
    return retention.remove(MetaOrderContext.objects.filter(created_at__lte=now - CONTEXT_RETENTION), dry_run)


retention.register("meta_order_contexts", order_contexts)
