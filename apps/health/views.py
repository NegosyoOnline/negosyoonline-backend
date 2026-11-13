"""`/readyz/`: can this deployment do its work. `/healthz/` (config/urls.py) stays liveness only.

The answer is booleans only: no versions, counts, hosts or error text. It is 200 when every
check passes and 503 otherwise, so an external uptime check can alert on the status code.
"""
import threading
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.common import throttle
from apps.common.errors import ApiError
from apps.health.tasks import HEARTBEAT_KEY

CHECK_TIMEOUT = 2  # seconds each check may take
BEAT_MAX_AGE = timedelta(minutes=3)
SMS_QUEUE_MAX_AGE = timedelta(minutes=15)
READYZ_LIMIT = (30, 60)  # attempts per IP per window (seconds)


def _client_ip(request):
    """The address the proxy saw: the last entry Railway appended to X-Forwarded-For."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    last = forwarded.split(",")[-1].strip()
    return last or request.META.get("REMOTE_ADDR", "") or "unknown"


def _bounded(check):
    """Run a check that needs no database in a thread, and call it failed after the timeout."""
    result = []

    def run():
        try:
            result.append(bool(check()))
        except Exception:
            result.append(False)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(CHECK_TIMEOUT)
    return bool(result and result[0])


def check_db():
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                if connection.vendor == "postgresql":
                    cursor.execute(f"SET LOCAL statement_timeout = {CHECK_TIMEOUT * 1000}")
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
    except Exception:
        return False


def _cache_round_trip():
    key = f"health:probe:{uuid.uuid4().hex}"
    cache.set(key, "ok", 30)
    return cache.get(key) == "ok"


def check_cache():
    return _bounded(_cache_round_trip)


def _heartbeat_fresh():
    beat = cache.get(HEARTBEAT_KEY)
    return beat is not None and timezone.now().timestamp() - float(beat) < BEAT_MAX_AGE.total_seconds()


def check_beat():
    # With no Redis the queue runs tasks in-process and there is no worker or beat to prove.
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return True
    return _bounded(_heartbeat_fresh)


def check_sms_queue():
    # Counts only while the SMS gateway is on: off, no text is queued and none is expected.
    if not settings.SMS_GATEWAY_ENABLED:
        return True
    from apps.notifications.models import SmsMessage

    try:
        cutoff = timezone.now() - SMS_QUEUE_MAX_AGE
        return not SmsMessage.objects.filter(
            status=SmsMessage.Status.QUEUED, created_at__lte=cutoff
        ).exists()
    except Exception:
        return False


@require_GET
def readyz(request):
    limit, window = READYZ_LIMIT
    try:
        throttle.hit("readyz", _client_ip(request), limit=limit, window=window)
    except ApiError:
        return JsonResponse({"ok": False}, status=429)
    except Exception:
        pass  # the cache is down: the cache check below says so
    checks = {
        "db": check_db(),
        "cache": check_cache(),
        "beat": check_beat(),
        "sms_queue": check_sms_queue(),
    }
    ok = all(checks.values())
    return JsonResponse({"ok": ok, **checks}, status=200 if ok else 503)
