"""The video delivery meter's reader (V-1): the bytes Bunny delivered from each store's own library.

Once a day (apps/training/tasks.py) every StreamLibrary row with a pull zone id is read for the
store's current allowance period (the same period the storage and orders meters use) and the latest
reading is kept on the row. The meters route shows it (apps/billing/meters.py). Display only:
nothing here blocks or charges anything.

A store with no pull zone id, no subscription, or any failed read is skipped and keeps its last
reading; nothing is raised and nothing loops. The key (BUNNY_STATS_API_KEY) is read in stream.py only.
Logs hold counts and error types, never a key, a site or a library.
"""
import logging

from django.conf import settings
from django.utils import timezone

from apps.billing import clock, meters
from apps.billing.models import Subscription

from . import stream
from .models import StreamLibrary

logger = logging.getLogger(__name__)


def refresh_row(row, now):
    """Read one library's bandwidth for its store's current period and keep it. True when read."""
    subscription = Subscription.objects.filter(site_id=row.site_id).first()
    if subscription is None:
        return False
    start, end, _ = meters.period_at(subscription, now)
    local_start = start.astimezone(clock.MANILA).date()
    local_end = min(now, end).astimezone(clock.MANILA).date()
    used = stream.pull_zone_bandwidth(row.pull_zone_id, local_start, local_end)
    row.delivered_bytes, row.delivered_period_start, row.delivered_read_at = used, start, now
    row.save(update_fields=["delivered_bytes", "delivered_period_start", "delivered_read_at"])
    return True


def refresh_all(now=None):
    """One daily run. Counts only. {"skipped": reason} with no Bunny call while the switch is off
    or the key is unset."""
    if not settings.VIDEO_METER_ENABLED:
        return {"skipped": "switched off"}
    if not stream.stats_key_set():
        logger.info("Video meter skipped: BUNNY_STATS_API_KEY is not set.")
        return {"skipped": "no statistics key"}
    now = now or timezone.now()
    rows = list(StreamLibrary.objects.filter(pull_zone_id__isnull=False).order_by("pk"))
    read = failed = skipped = 0
    for row in rows:
        try:
            if refresh_row(row, now):
                read += 1
            else:
                skipped += 1
        except stream.StreamError:
            failed += 1  # stream.py logged the status; the row keeps its last reading
        except Exception as error:  # noqa: BLE001 - one store never stops the others
            failed += 1
            logger.warning("Video meter read failed: %s", type(error).__name__)
    if not rows:
        logger.info("Video meter: no library has a pull zone id.")
    return {"read": read, "skipped": skipped, "failed": failed}


def reading_for(site, period_start):
    """(bytes, read_at) the meter shows for `site` in the period starting at `period_start`: the
    latest reading when it was taken in that period, else (0, None)."""
    row = StreamLibrary.objects.filter(site=site).first()
    if row is None or row.delivered_read_at is None or row.delivered_period_start != period_start:
        return 0, None
    return row.delivered_bytes, row.delivered_read_at
