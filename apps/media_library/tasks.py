"""The daily media ledger audit, on the existing worker (beat queues it at 05:00 Manila time).

Switched off (MEDIA_LEDGER_AUDIT_ENABLED), it returns at once and makes no store call.
"""
import logging

from celery import shared_task
from django.conf import settings

from . import audit

logger = logging.getLogger(__name__)


@shared_task(name="media_library.ledger_audit", ignore_result=True)
def ledger_audit():
    if not settings.MEDIA_LEDGER_AUDIT_ENABLED:
        return {"skipped": "switched off"}
    try:
        return audit.run()
    except Exception as error:  # noqa: BLE001 - logged by type only
        logger.error("Media ledger audit failed: %s", type(error).__name__)
        return {"error": type(error).__name__}
