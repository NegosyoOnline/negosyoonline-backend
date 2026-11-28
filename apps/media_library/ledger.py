"""The media library's byte ledger: one MediaObject row per object stored for a site.

This module is the only code that creates a MediaObject or assigns `removed_at` (a test scans the
source for it). Nothing here changes a bill: `bytes_at` and `breakdown_at` read the ledger and
nothing calls them yet (P6.2d will). The display meter (apps/billing/meters.py) is not changed.

The rule of the ledger is that an object counts until it is confirmed gone:
- `record` adds the row in the same transaction that attaches the key to its asset;
- `release` runs after the row change has committed, deletes the object with the raising store
  delete and writes `removed_at` only on success. A failed delete leaves the row live and still
  counting; the daily audit (audit.py) retries it;
- rows are never deleted, and `removed_at` is the only column written after a row is created.

The sum counts the rows with `added_at <= cutoff` and `removed_at` empty or later than the cutoff,
so it can be reproduced from the ledger alone after an asset row is deleted and the store is wiped.
Only COUNTED_ROLES are summed unless the caller passes roles: today that is the file itself, the
same rule as the display meter (thumbnails and posters are owner uploads, not platform renditions;
whether they count is Coach Ed's decision before P6.2d).
"""
import logging

from django.db import transaction
from django.db.models import BigIntegerField, Count, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.common import file_refs

from . import store
from .models import MediaObject

logger = logging.getLogger(__name__)

ROLES = (MediaObject.FILE, MediaObject.THUMB, MediaObject.POSTER)
COUNTED_ROLES = (MediaObject.FILE,)


def record(site, *, asset, role, key, content_type, size, added_at=None):
    """Add the row of one stored object (idempotent on `key`: a key already in the ledger is
    returned as it is). Call it in the transaction that attaches the key to `asset`. `size` is
    the size as stored."""
    if role not in ROLES:
        raise ValueError(f"Unknown media object role: {role!r}")
    if not key or isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("A ledger row needs a key and a size in bytes.")
    row, _ = MediaObject.objects.get_or_create(
        key=key,
        defaults={
            "site": site, "asset": asset, "role": role, "content_type": content_type or "",
            "bytes": size, "added_at": added_at or timezone.now(),
        },
    )
    return row


def live_key_of(site, asset, key):
    """True when `key` has a live row of `asset` of `site` (a Bunny video made for this asset)."""
    return MediaObject.objects.filter(site=site, asset=asset, key=key, removed_at__isnull=True).exists()


def pending_keys(asset, prefix, keep=""):
    """The live keys of `asset` that start with `prefix` and are not `keep`: videos made for it
    (recorded with 0 bytes when the upload was started) that no row names yet."""
    rows = MediaObject.objects.filter(asset=asset, removed_at__isnull=True, key__startswith=prefix)
    return list(rows.exclude(key=keep).order_by("pk").values_list("key", flat=True))


def confirm_bytes(key, size):
    """Give a live row recorded with 0 bytes its real size (a Bunny video once it is finished).
    The one change to a row besides `removed_at`: a row that already has bytes is never changed."""
    return MediaObject.objects.filter(key=key, removed_at__isnull=True, bytes=0).update(bytes=size)


def _mark_removed(rows, when):
    """Write `removed_at` on the live rows of `rows` (a queryset). Never earlier than a row's own
    `added_at` (the table refuses it). Returns how many rows were closed."""
    live = rows.filter(removed_at__isnull=True)
    closed = live.filter(added_at__lte=when).update(removed_at=when)
    return closed + live.filter(added_at__gt=when).update(removed_at=F("added_at"))


def release(key, now=None):
    """Remove one object from the store and close its ledger row. Run after the change that
    dropped the key has committed (transaction.on_commit). True when the object is gone.

    The file_refs guard stays: a key some row still names is not deleted (and its ledger row
    stays live). Never raises: a failed delete leaves the row live and counting."""
    if not key:
        return True
    try:
        if file_refs.is_referenced(key):
            return False
        store.delete(key)
        _mark_removed(MediaObject.objects.filter(key=key), now or timezone.now())
    except Exception:  # noqa: BLE001 - the owner's request is already answered; the audit retries
        logger.warning("A media library release did not finish.")
        return False
    return True


def release_on_commit(keys):
    """Release `keys` once the current transaction has committed (at once outside one)."""
    keys = [key for key in keys if key]
    if keys:
        transaction.on_commit(lambda: [release(key) for key in keys])


def purge_site(site, now):
    """Remove every live object of `site` from the store, then close its rows. The rows stay.
    Idempotent (a second run finds no live row). An absent key counts as gone. A store failure
    raises StoreError and closes nothing for the objects not yet deleted. Returns how many
    objects were removed. The file_refs guard is not used: an ended site's assets still name
    their keys, and the point is to remove them."""
    live = MediaObject.objects.filter(site=site, removed_at__isnull=True)
    keys = list(live.order_by("pk").values_list("key", flat=True))
    done = []
    try:
        for key in keys:
            store.delete(key)
            done.append(key)
    finally:
        if done:
            _mark_removed(MediaObject.objects.filter(site=site, key__in=done), now)
    return len(done)


def live_count(site):
    return MediaObject.objects.filter(site=site, removed_at__isnull=True).count()


def _held_at(site, cutoff):
    """The rows of `site` stored at `cutoff`: added at or before it, not removed by it."""
    return MediaObject.objects.filter(site=site, added_at__lte=cutoff).filter(
        Q(removed_at__isnull=True) | Q(removed_at__gt=cutoff)
    )


def bytes_at(site, cutoff, roles=COUNTED_ROLES):
    """The bytes the library of `site` held at `cutoff` (a time with a time zone), reading the
    ledger only. An object counts from the instant it was added (inclusive) until the instant it
    was removed (exclusive)."""
    total = _held_at(site, cutoff).filter(role__in=roles).aggregate(
        total=Coalesce(Sum("bytes"), 0, output_field=BigIntegerField())
    )["total"]
    return int(total)


def breakdown_at(site, cutoff):
    """{role: {"objects": n, "bytes": b}} for every role at `cutoff`, counted or not."""
    found = {
        row["role"]: row
        for row in _held_at(site, cutoff).values("role").annotate(
            objects=Count("pk"), size=Coalesce(Sum("bytes"), 0, output_field=BigIntegerField())
        )
    }
    return {
        role: {"objects": found[role]["objects"] if role in found else 0,
               "bytes": int(found[role]["size"]) if role in found else 0}
        for role in ROLES
    }
