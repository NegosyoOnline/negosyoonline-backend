"""The daily retention run, on the existing worker. Beat queues it at 03:00 Manila time.

It runs every registered rule once and writes the run log (one `retention_run` audit entry).
The rules behind `RETENTION_ENABLED` do nothing while it is off and are named in the entry.
"""
from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from apps.common import retention

_LOCK_KEPT = 2 * 24 * 3600


@shared_task(name="retention.run", ignore_result=True)
def run():
    """Run every retention rule, once per Manila day: a queued-twice or re-delivered task finds
    the day's lock taken and does nothing. Returns the report's counts for the worker log
    (rule names and numbers only, never a personal value)."""
    if not cache.add(f"retention:all:{timezone.localdate():%Y%m%d}", 1, _LOCK_KEPT):
        return {"skipped": "already ran today"}
    report = retention.run_all()
    return {
        "counts": report.counts,
        "switched_off": report.switched_off,
        "errors": report.errors,
    }
