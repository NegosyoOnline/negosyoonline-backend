"""The backup run: copy every object of the private store to the bucket, encrypted, once.

Layout in the bucket (under `root`, empty for the real backup, "selftest/" for the self-test):

    index/latest                      text: name of the newest index object
    index/<run time>.json.gz.enc      the index, encrypted: pathname -> entry
    data/<pathname>                   the encrypted bytes
    status.json                       the last run's counts (no pathnames)

The bucket is the only state: no database table. A run is incremental and idempotent. A key in
the store is never overwritten (names are random and `put` never replaces), so an object is
copied once; etag and size are compared as a safety net. The index is saved after the data
object is stored, so a crash leaves at worst an orphan data object that the next run replaces.

An object deleted at the source is kept, marked `deleted_seen_at`, and removed after
`keep_days`. A safety brake stops that deletion work when the source looks wrong (an empty
store, a wrong token) so a bad listing cannot erase the backup.

Nothing here logs or returns a pathname, a token or content; callers see counts only.
"""
import gzip
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from apps.common import private_files
from apps.file_backup import crypto
from apps.file_backup.destination import DestinationError
from apps.file_backup.source import SourceError

DATA, INDEX, LATEST, STATUS = "data/", "index/", "index/latest", "status.json"
KEEP_INDEXES = 30
SAVE_EVERY = 500
BRAKE_FRACTION = 0.2
BRAKE_MINIMUM = 10  # fewer missing objects than this never trip the brake (small stores)


@dataclass
class Config:
    key: bytes
    root: str = ""
    prefix: str = ""
    keep_days: int = 35
    max_bytes: int = 50 * 1024 * 1024
    budget_seconds: float = 25 * 60
    rate_per_second: float = 10
    sample: int = 20


def _iso(moment):
    return moment.isoformat()


def top_folder(pathname):
    """The first path segment: how a count is grouped without showing a pathname."""
    return pathname.split("/", 1)[0] if "/" in pathname else "(root)"


# -- the index ---------------------------------------------------------------------------------

def load_index(dest, cfg):
    """The newest index as {pathname: entry}, or {} when there is none yet."""
    try:
        name = dest.get(cfg.root + LATEST).decode().strip()
    except Exception:  # noqa: BLE001 - no pointer yet is the first run
        return {}
    full = cfg.root + INDEX + name
    return json.loads(gzip.decompress(crypto.decrypt(cfg.key, full, dest.get(full))))


def save_index(dest, cfg, index, name):
    full = cfg.root + INDEX + name
    packed = gzip.compress(json.dumps(index, sort_keys=True).encode())
    dest.put(full, crypto.encrypt(cfg.key, full, packed))
    dest.put(cfg.root + LATEST, name.encode())


def _prune_indexes(dest, cfg, current):
    names = sorted(
        key[len(cfg.root + INDEX):] for key, _ in dest.list(cfg.root + INDEX)
        if key != cfg.root + LATEST
    )
    for name in names[:-KEEP_INDEXES]:
        if name != current:
            dest.delete(cfg.root + INDEX + name)


def read_status(dest, cfg):
    """The last run's status dict, or None."""
    try:
        return json.loads(dest.get(cfg.root + STATUS))
    except Exception:  # noqa: BLE001
        return None


# -- the run -----------------------------------------------------------------------------------

def _copy_one(source, dest, cfg, pathname, obj, now):
    content, content_type = source.fetch(pathname)
    content_type = content_type or private_files.sniff(content[:16]) or "application/octet-stream"
    dest.put(cfg.root + DATA + pathname, crypto.encrypt(cfg.key, pathname, content))
    return {
        "content_type": content_type,
        "size": len(content),
        "source_size": obj["size"],
        "uploaded_at": obj["uploaded_at"],
        "etag": obj["etag"],
        "sha256": hashlib.sha256(content).hexdigest(),
        "backed_up_at": _iso(now),
        "deleted_seen_at": None,
        "verified_at": None,
    }


def _unchanged(entry, obj):
    return bool(entry) and entry["etag"] == obj["etag"] and entry["source_size"] == obj["size"]


def run(source, dest, cfg, *, now=None, clock=time.monotonic, sleep=time.sleep):
    """One backup run. Returns the status dict (counts only). Source and destination errors on
    one object count as `failed` and the run goes on; an error listing either side raises."""
    now = now or timezone.now()
    started = clock()
    run_name = f"{now:%Y%m%dT%H%M%SZ}.json.gz.enc"
    counts = dict.fromkeys(
        ("copied", "skipped", "failed", "tombstoned", "purged", "oversize", "remaining",
         "verified", "verify_failed", "copied_bytes"), 0,
    )
    index = load_index(dest, cfg)
    listed = {o["pathname"]: o for o in source.list_objects(cfg.prefix)}
    have = {
        key[len(cfg.root + DATA):]
        for key, _ in dest.list(cfg.root + DATA + cfg.prefix)
    }
    dirty, since_save, last_copy = False, 0, None
    interval = 1 / cfg.rate_per_second if cfg.rate_per_second else 0

    for pathname in sorted(listed):
        obj, entry = listed[pathname], index.get(pathname)
        if _unchanged(entry, obj) and pathname in have:
            counts["skipped"] += 1
            if entry["deleted_seen_at"]:  # it is back at the source: it is live again
                entry["deleted_seen_at"], dirty = None, True
            continue
        if obj["size"] > cfg.max_bytes:
            counts["oversize"] += 1
            continue
        if clock() - started > cfg.budget_seconds:
            counts["remaining"] += 1
            continue
        if last_copy is not None and interval:
            wait = interval - (clock() - last_copy)
            if wait > 0:
                sleep(wait)
        last_copy = clock()
        try:
            index[pathname] = _copy_one(source, dest, cfg, pathname, obj, now)
        except (SourceError, DestinationError):
            counts["failed"] += 1
            continue
        counts["copied"] += 1
        counts["copied_bytes"] += index[pathname]["size"]
        dirty, since_save = True, since_save + 1
        if since_save >= SAVE_EVERY:
            save_index(dest, cfg, index, run_name)
            since_save = 0

    # Deletion work, behind the brake.
    in_scope = [p for p in index if p.startswith(cfg.prefix)]
    live = [p for p in in_scope if not index[p]["deleted_seen_at"]]
    missing = [p for p in live if p not in listed]
    anomaly = bool(live) and (
        not listed or (len(missing) > BRAKE_MINIMUM and len(missing) > BRAKE_FRACTION * len(live))
    )
    if not anomaly:
        for pathname in missing:
            index[pathname]["deleted_seen_at"] = _iso(now)
            counts["tombstoned"] += 1
            dirty = True
        limit = now - timedelta(days=cfg.keep_days)
        for pathname in in_scope:
            seen = index[pathname]["deleted_seen_at"]
            if seen and datetime.fromisoformat(seen) <= limit:
                try:
                    dest.delete(cfg.root + DATA + pathname)
                except DestinationError:
                    counts["failed"] += 1
                    continue
                del index[pathname]
                counts["purged"] += 1
                dirty = True

    # Rolling integrity sample: the least recently verified entries, downloaded and re-hashed.
    for pathname in sorted(index, key=lambda p: index[p]["verified_at"] or "")[: cfg.sample]:
        entry = index[pathname]
        try:
            plain = crypto.decrypt(cfg.key, pathname, dest.get(cfg.root + DATA + pathname))
            good = hashlib.sha256(plain).hexdigest() == entry["sha256"]
        except (DestinationError, crypto.CryptoError):
            good = False
        counts["verified"] += 1
        if good:
            entry["verified_at"], dirty = _iso(now), True
        else:
            counts["verify_failed"] += 1
            counts["failed"] += 1

    if dirty:
        save_index(dest, cfg, index, run_name)
    _prune_indexes(dest, cfg, run_name)

    live_now = [p for p in index if p.startswith(cfg.prefix) and not index[p]["deleted_seen_at"]]
    status = {
        "started_at": _iso(now),
        "finished_at": _iso(timezone.now()),
        "ok": not (anomaly or counts["failed"] or counts["oversize"] or counts["remaining"]),
        "anomaly": anomaly,
        "source_count": len(listed),
        "source_bytes": sum(o["size"] for o in listed.values()),
        "backup_count": len([p for p in live_now if p in listed]),
        **counts,
    }
    dest.put(cfg.root + STATUS, json.dumps(status).encode())
    return status
