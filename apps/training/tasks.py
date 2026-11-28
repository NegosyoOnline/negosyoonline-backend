"""The daily video delivery meter, on the existing worker (beat queues it at 05:30 Manila time).

Switched off (VIDEO_METER_ENABLED), it returns at once and makes no Bunny call.
"""
import logging

from celery import shared_task

from . import video_meter

logger = logging.getLogger(__name__)


@shared_task(name="training.video_meter", ignore_result=True)
def video_meter_run():
    try:
        return video_meter.refresh_all()
    except Exception as error:  # noqa: BLE001 - logged by type only
        logger.error("Video meter failed: %s", type(error).__name__)
        return {"error": type(error).__name__}
