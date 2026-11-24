"""The daily audit of the media library ledger against the public store.

It lists the store under media-library/ only (never the private store, never another folder of
the public one) and compares the objects with the ledger rows:

- orphans: an object with no live row (for instance a browser video upload nobody attached);
- ghosts: a live row whose object is gone from the store and which a row of the library still
  names. A ghost is only counted: it is never removed and never changes a bill;
- pending releases: a live row no row of the library names any more (a delete that failed). Each is
  released again (ledger.release), which also closes a row whose object is already gone.

An object or row younger than GRACE is left alone: a file is stored before its row commits, and a
listing may lag a new upload. The report holds counts only: no key, site or asset id, in the
`security` log and (as an error line, when something needs a look) in Sentry.
"""
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from apps.catalog import media
from apps.common import file_refs

from . import ledger, store
from .models import MediaObject

logger = logging.getLogger(__name__)
security = logging.getLogger("security")

PREFIX = f"{store.FOLDER}/"
GRACE = timedelta(hours=1)


def _uploaded_at(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def run(now=None):
    """One audit. Returns the counts, or {"skipped": "switched off"} with no store call while
    MEDIA_LEDGER_AUDIT_ENABLED is off. Raises media.StoreError when the store cannot be listed
    (the task logs it by type; nothing is counted from a partial listing)."""
    if not settings.MEDIA_LEDGER_AUDIT_ENABLED:
        return {"skipped": "switched off"}
    now = now or timezone.now()
    stored = {
        blob["pathname"]: _uploaded_at(blob["uploaded_at"])
        for blob in list(media.list_prefix(PREFIX))  # read whole before anything is compared
        if blob["pathname"].startswith(PREFIX)
    }
    live = {
        key: added
        for key, added in MediaObject.objects.filter(removed_at__isnull=True).values_list("key", "added_at")
        if key.startswith(PREFIX)
    }
    orphans = sum(
        1 for key, uploaded in stored.items()
        if key not in live and (uploaded is None or now - uploaded >= GRACE)
    )
    ghosts = retried = released = 0
    for key, added in live.items():
        if now - added < GRACE:
            continue
        if file_refs.is_referenced(key):
            if key not in stored:
                ghosts += 1
            continue
        retried += 1  # no row names it any more: a delete that did not finish
        released += bool(ledger.release(key, now))
    counts = {
        "listed": len(stored), "live_rows": len(live), "orphans": orphans, "ghosts": ghosts,
        "retried": retried, "released": released, "still_pending": retried - released,
    }
    security.warning(
        "media_ledger_audit listed=%(listed)d live_rows=%(live_rows)d orphans=%(orphans)d "
        "ghosts=%(ghosts)d retried=%(retried)d released=%(released)d", counts,
    )
    if orphans or ghosts or counts["still_pending"]:
        logger.error(
            "Media ledger audit found differences: orphans=%d ghosts=%d still_pending=%d.",
            orphans, ghosts, counts["still_pending"],
        )
    return counts
