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
from django.views.decorators.http import require_safe

from apps.common import throttle
from apps.common.errors import ApiError
from apps.file_backup.constants import LAST_OK_KEY as FILES_BACKUP_KEY
from apps.health.tasks import HEARTBEAT_KEY

CHECK_TIMEOUT = 2  # seconds each check may take
BEAT_MAX_AGE = timedelta(minutes=3)
SMS_QUEUE_MAX_AGE = timedelta(minutes=15)
# A Meta delivery is normally done within about 12 minutes: a page event makes 3 attempts 60 seconds
# apart (about 2 minutes); an order event the first dispatch missed is re-queued by meta_ads.sweep,
# which runs every 5 minutes and acts on rows older than 5 minutes (about 10 minutes), then makes
# its attempts the same way. 30 minutes is safely above that, so only a stuck row (worker or beat
# down, a lost task) trips it.
READYZ_META_QUEUE_MINUTES = 30
READYZ_EMAIL_SAMPLE = 20  # newest email rows read by check_email
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


def check_meta_queue():
    """False when a Meta Conversions delivery has been queued or retrying for longer than
    READYZ_META_QUEUE_MINUTES. True while Meta events are off. One `.exists()` query."""
    from apps.meta_ads import services
    from apps.meta_ads.models import MetaDelivery

    if not services.enabled():
        return True
    try:
        cutoff = timezone.now() - timedelta(minutes=READYZ_META_QUEUE_MINUTES)
        return not MetaDelivery.objects.filter(
            outcome__in=MetaDelivery.OPEN, created_at__lte=cutoff
        ).exists()
    except Exception:
        return False


def check_email():
    """False when the newest emails inside the window are all failures: at least the threshold
    failed and none sent. One query by primary key (no index on created_at or status needed)."""
    from apps.notifications.models import EmailMessage

    try:
        cutoff = timezone.now() - timedelta(minutes=settings.READYZ_EMAIL_WINDOW_MINUTES)
        rows = EmailMessage.objects.order_by("-pk").values_list("status", "created_at")[:READYZ_EMAIL_SAMPLE]
        statuses = [status for status, created_at in rows if created_at >= cutoff]
        failed = statuses.count(EmailMessage.Status.FAILED)
        sent = statuses.count(EmailMessage.Status.SENT)
        return not (failed >= settings.READYZ_EMAIL_FAILURES and sent == 0)
    except Exception:
        return False


def _files_backup_fresh():
    last = cache.get(FILES_BACKUP_KEY)
    return last is not None and timezone.now().timestamp() - float(last) < settings.FILE_BACKUP_STALE_HOURS * 3600


def check_files_backup():
    """True while the file backup is switched off (no client is built or called). Switched on,
    true while the last fully good run is younger than FILE_BACKUP_STALE_HOURS: one cache read."""
    if not settings.FILE_BACKUP_ENABLED:
        return True
    return _bounded(_files_backup_fresh)


@require_safe
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
        "email": check_email(),
        "files_backup": check_files_backup(),
        "meta_queue": check_meta_queue(),
    }
    ok = all(checks.values())
    return JsonResponse({"ok": ok, **checks}, status=200 if ok else 503)
