"""Restore from the backup into the live private store, and the urgent purge.

`restore` never overwrites: a pathname the live store already has is skipped and counted.
Objects marked deleted at the source are skipped unless `include_deleted` (they were removed on
purpose, often by an erasure). Without `apply` nothing is read from the bucket or written. A
copy whose sha256 does not match its index entry is refused, not written. Counts only.
"""
import hashlib

from django.utils import timezone

from apps.file_backup import crypto
from apps.file_backup.destination import DestinationError
from apps.file_backup.service import DATA, load_index, save_index, top_folder
from apps.file_backup.source import SourceError


def _chosen(index, *, prefix, keys):
    keys = set(keys or ())
    for pathname, entry in sorted(index.items()):
        if prefix and not pathname.startswith(prefix):
            continue
        if keys and pathname not in keys:
            continue
        yield pathname, entry


def listing(dest, cfg, prefix=""):
    """{folder: [objects, bytes, deleted objects]} of what the backup holds. No pathnames."""
    out = {}
    for pathname, entry in load_index(dest, cfg).items():
        if not pathname.startswith(prefix):
            continue
        row = out.setdefault(top_folder(pathname), [0, 0, 0])
        row[0] += 1
        row[1] += entry["size"]
        row[2] += 1 if entry["deleted_seen_at"] else 0
    return out


def restore(source, dest, cfg, *, prefix="", keys=(), include_deleted=False, apply=False):
    """Copy backed-up objects the live store lacks back into it. Returns counts."""
    counts = dict.fromkeys(
        ("considered", "exists", "tombstoned_skipped", "would_restore", "restored", "mismatch",
         "failed"), 0,
    )
    index = load_index(dest, cfg)
    live = {o["pathname"] for o in source.list_objects(prefix)}
    for pathname, entry in _chosen(index, prefix=prefix, keys=keys):
        counts["considered"] += 1
        if pathname in live:
            counts["exists"] += 1
            continue
        if entry["deleted_seen_at"] and not include_deleted:
            counts["tombstoned_skipped"] += 1
            continue
        if not apply:
            counts["would_restore"] += 1
            continue
        try:
            content = crypto.decrypt(cfg.key, pathname, dest.get(cfg.root + DATA + pathname))
        except (DestinationError, crypto.CryptoError):
            counts["failed"] += 1
            continue
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            counts["mismatch"] += 1
            continue
        try:
            source.put(pathname, content, entry["content_type"])
        except SourceError:
            counts["failed"] += 1
            continue
        counts["restored"] += 1
    return counts


def purge_prefix(source, dest, cfg, prefix, *, apply=False):
    """Remove the copies and index entries under `prefix` now (an urgent erasure). An object the
    live store still has is not touched: the next run would copy it again. Returns counts."""
    counts = {"matched": 0, "still_live": 0, "purged": 0, "failed": 0}
    if not prefix:
        raise ValueError("A purge needs a prefix.")
    index = load_index(dest, cfg)
    live = {o["pathname"] for o in source.list_objects(prefix)}
    for pathname in [p for p in index if p.startswith(prefix)]:
        counts["matched"] += 1
        if pathname in live:
            counts["still_live"] += 1
            continue
        if not apply:
            continue
        try:
            dest.delete(cfg.root + DATA + pathname)
        except DestinationError:
            counts["failed"] += 1
            continue
        del index[pathname]
        counts["purged"] += 1
    if apply and counts["purged"]:
        save_index(dest, cfg, index, f"{timezone.now():%Y%m%dT%H%M%SZ}.json.gz.enc")
    return counts
