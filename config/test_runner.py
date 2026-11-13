"""Test runner that gives every parallel worker its own Redis database.

Sign-in codes, send caps and throttles live in the cache, and django-redis `cache.clear()`
is FLUSHDB: it empties the whole Redis database whatever the key prefix. Parallel workers
sharing one database therefore wipe each other's keys. Each worker gets its own database
from 1-15, in worker order, skipping the Celery broker's database (the cache's database
plus one, config/celery.py: database 1 when REDIS_URL ends /0). Database 0 stays with serial
runs and with anything outside tests. Redis has 16 databases by default, so at most 14
workers fit; more fail at startup instead of sharing a database.

Applies only under `manage.py test` (TEST_RUNNER) and only with a Redis cache. The Celery
broker is not rewired: no test enqueues to it (apps/meta_ads/tests/base.py runs tasks
eagerly).
"""

from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.test import runner as django_runner
from django.test.runner import DiscoverRunner, ParallelTestSuite

from config.celery import broker_url

REDIS_DATABASES = 16  # the Redis default; database 0 is left to serial runs


def _redis_location():
    location = settings.CACHES.get("default", {}).get("LOCATION")
    if isinstance(location, str) and location.startswith(("redis://", "rediss://")):
        return location
    return None


def _worker_databases(location):
    """Databases workers may use: 1-15 without the broker's."""
    broker_db = int(urlsplit(broker_url(location)).path.strip("/"))
    return [n for n in range(1, REDIS_DATABASES) if n != broker_db]


def _worker_location(location, worker_id):
    databases = _worker_databases(location)
    if not 1 <= worker_id <= len(databases):
        raise ImproperlyConfigured(
            f"Parallel worker {worker_id} has no Redis database of its own "
            f"(only {len(databases)} are free). Run with --parallel {len(databases)} or fewer."
        )
    return urlunsplit(urlsplit(location)._replace(path=f"/{databases[worker_id - 1]}"))


def _init_worker_with_redis(*args, **kwargs):
    """Django's worker initializer, then this worker's cache moves to its own database."""
    django_runner._init_worker(*args, **kwargs)
    location = _redis_location()
    if location is None:
        return
    caches = {k: dict(v) for k, v in settings.CACHES.items()}
    caches["default"]["LOCATION"] = _worker_location(location, django_runner._worker_id)
    # Changing CACHES resets Django's cache handler, dropping any connection inherited by fork.
    override_settings(CACHES=caches).enable()


class RedisParallelTestSuite(ParallelTestSuite):
    init_worker = _init_worker_with_redis

    def run(self, result, *args, **kwargs):
        location = _redis_location()
        if location:
            # Same check made once up front, so a too-large pool fails before any test runs.
            _worker_location(location, self.processes)
        return super().run(result, *args, **kwargs)


class RedisIsolatedDiscoverRunner(DiscoverRunner):
    parallel_test_suite = RedisParallelTestSuite
