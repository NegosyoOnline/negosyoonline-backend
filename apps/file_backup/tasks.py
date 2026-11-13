"""The daily file backup, on the existing worker. Beat queues it at 04:00 Manila time.

Switched off (FILE_BACKUP_ENABLED), it returns at once and builds no client. Once per Manila
day (a re-delivered task finds the day's lock taken), one run at a time (a running lock), and a
run that stops at its time budget queues the next chunk itself, at most MAX_CHAIN times.
Logs counts only, never a pathname.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.file_backup import clients, constants, service

logger = logging.getLogger(__name__)


def _day_key():
    return constants.DAY_KEY.format(date=f"{timezone.localdate():%Y%m%d}")


@shared_task(name="file_backup.run", ignore_result=True)
def run(chain=0):
    if not settings.FILE_BACKUP_ENABLED:
        return {"skipped": "switched off"}
    if chain == 0 and not cache.add(_day_key(), 1, constants.DAY_KEPT_SECONDS):
        return {"skipped": "already ran today"}
    if not cache.add(constants.RUNNING_KEY, 1, constants.RUN_LOCK_SECONDS):
        return {"skipped": "already running"}
    try:
        status = service.run(clients.build_source(), clients.build_destination(), clients.build_config())
    except Exception as error:  # noqa: BLE001 - logged by type only; no name or token in it
        logger.error("File backup run failed: %s", type(error).__name__)
        cache.delete(_day_key())  # a failed run may be started again
        return {"error": type(error).__name__}
    finally:
        cache.delete(constants.RUNNING_KEY)
    counts = {k: v for k, v in status.items() if isinstance(v, (int, bool))}
    if status["ok"]:
        cache.set(constants.LAST_OK_KEY, timezone.now().timestamp(), None)
    elif status["remaining"] and not status["anomaly"] and chain < constants.MAX_CHAIN:
        run.apply_async(kwargs={"chain": chain + 1}, countdown=constants.CHAIN_COUNTDOWN)
    else:
        logger.error("File backup run finished with problems: %s", counts)
    return counts
