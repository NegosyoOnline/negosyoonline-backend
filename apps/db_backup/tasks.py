"""The nightly database backup, on the existing worker (beat entry `db-backup-daily`).

Switched off (DB_BACKUP_ENABLED), it returns at once and builds no client. Once per Manila day
(a re-delivered task finds the day's lock taken) and one run at a time. Logs the exception type
and counts only, never a pathname, a key or any output of pg_dump.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.db_backup import clients, constants, dump, service

logger = logging.getLogger(__name__)


def _day_key():
    return constants.DAY_KEY.format(date=f"{timezone.localdate():%Y%m%d}")


def execute(now=None):
    """One backup run, used by the task and the command. Raises on any failure."""
    now = now or timezone.now()
    result = service.run_backup(clients.build_store(), clients.build_key(), dump.dump_database, now)
    cache.set(constants.LAST_OK_KEY, result["at"], None)
    cache.delete(constants.PROBE_FAILED_KEY)
    return result


@shared_task(name="db_backup.run", ignore_result=True)
def run():
    if not settings.DB_BACKUP_ENABLED:
        return {"skipped": "switched off"}
    if not cache.add(_day_key(), 1, constants.DAY_KEPT_SECONDS):
        return {"skipped": "already ran today"}
    if not cache.add(constants.RUNNING_KEY, 1, constants.RUN_LOCK_SECONDS):
        return {"skipped": "already running"}
    try:
        result = execute()
    except Exception as error:  # noqa: BLE001 - type only; no detail can carry a secret
        logger.error("Database backup failed: %s", type(error).__name__)
        cache.delete(_day_key())  # a failed run may be started again
        return {"error": type(error).__name__}
    finally:
        cache.delete(constants.RUNNING_KEY)
    counts = {k: v for k, v in result.items() if k != "at"}
    logger.info("Database backup done: %s", counts)
    return counts
