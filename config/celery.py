"""The task queue: one Celery app, used by the `worker` and `beat` services.

    uv run celery -A config worker --loglevel=info --concurrency=2
    uv run celery -A config beat --loglevel=info --schedule=/tmp/celerybeat-schedule

The broker is the Redis server of `REDIS_URL`. There is no result store. The schedule is the
fixed list `CELERY_BEAT_SCHEDULE` in settings, so no table holds it.
"""
import os
from urllib.parse import urlsplit, urlunsplit

from celery import Celery


def broker_url(redis_url):
    """The broker's address: the same Redis server, on the database number after the cache's.

    Clearing the cache flushes its whole Redis database (django-redis `clear()` is FLUSHDB),
    so queued work must not live there. No number in the address means database 0.
    """
    parts = urlsplit(redis_url)
    number = parts.path.strip("/")
    cache_db = int(number) if number.isascii() and number.isdigit() else 0
    return urlunsplit(parts._replace(path=f"/{cache_db + 1}"))


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("negosyoonline")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
