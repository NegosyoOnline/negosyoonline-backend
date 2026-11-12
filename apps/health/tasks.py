"""The beat heartbeat. Beat queues it every minute and the worker runs it, so a fresh value
in the cache proves beat, the broker and the worker all work (read by /readyz/)."""
from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

HEARTBEAT_KEY = "health:heartbeat"
HEARTBEAT_EVERY_SECONDS = 60


@shared_task(name="health.heartbeat", ignore_result=True)
def heartbeat():
    cache.set(HEARTBEAT_KEY, timezone.now().timestamp(), 600)
