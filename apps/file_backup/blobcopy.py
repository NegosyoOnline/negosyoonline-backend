"""Copy one Vercel Blob store into another, once (P7: staging stores into production's).

Both stores are used through `source.BlobSource`, one for each side, each of the store's own
access (private or public). For every object of the source:

- missing at the destination: download, put under the same pathname with the same content type
  (the destination store's access is the store's own, so private stays private and public stays
  public), read it back and compare sha256 with the source bytes;
- already there: never touched. Same sha256 is `skipped_existing`; a different one is `conflict`
  (reported, not changed).

A put never asks Vercel to overwrite (no allow-overwrite header), so a name that appeared after the
listing is refused by the store and counted as failed. A copy that reads back wrong is deleted
(it is the object this run just made) so a re-run can try again, and is counted as failed.

Before any copy (also in a dry run) `probe_source` and `probe_destination` prove each store is of
the declared access; the destination probe is the one thing a dry run writes, and it is deleted.
The first put or read-back failure at the destination stops an applied run.

Nothing here logs or returns a pathname, URL, token or content: callers see counts only.
"""
import hashlib
import secrets
import time

from apps.file_backup.source import SourceError

COUNT_NAMES = ("listed", "copied", "skipped_existing", "conflict", "failed", "bytes")
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_RATE = 10  # requests a second, counted across both stores
PROBE_PREFIX = "ops/blobguard-probe/"  # the only name the kind probe ever writes


class KindMismatch(Exception):
    """A store is not of the access it was declared as. `probe_deleted` is None (no probe was
    written) or whether the probe object was deleted again. The message holds no name."""

    def __init__(self, message, probe_deleted=None):
        super().__init__(message)
        self.probe_deleted = probe_deleted


class DestinationFailure(Exception):
    """A put or its read-back failed at the destination: the run stops."""


class Throttle:
    """At most `rate` requests a second."""

    def __init__(self, rate, clock=time.monotonic, sleep=time.sleep):
        self._interval = 1 / rate if rate else 0
        self._clock, self._sleep, self._last = clock, sleep, None

    def wait(self):
        if self._interval and self._last is not None:
            gap = self._interval - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._clock()


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def probe_source(source, access, prefix=""):
    """Fetch one listed object through the declared-access host. Returns False when the source
    lists nothing (nothing to copy), True when the fetch worked. A failed fetch means the store
    is not of that access: KindMismatch. Read-only."""
    first = next(iter(source.list_objects(prefix)), None)
    if first is None:
        return False
    try:
        source.fetch(first["pathname"])
    except SourceError:
        raise KindMismatch(f"the source store is not a {access} store.") from None
    return True


def probe_destination(dest, access):
    """Put one probe object with the declared access, read it back through the declared host
    and check the public host (private: must NOT serve it; public: must). The probe is deleted
    in a `finally`, also on failure. Returns whether the delete worked; a mismatch raises
    KindMismatch carrying that flag. This is the only thing a dry run writes."""
    pathname = PROBE_PREFIX + secrets.token_hex(16)
    content = b"blobguard probe " + secrets.token_hex(8).encode()
    problem = None
    try:
        try:
            dest.put(pathname, content, "text/plain")
        except SourceError:
            problem = "the destination store did not take the probe."
        else:
            try:
                back, _ = dest.fetch(pathname)
                served = dest.served_publicly(pathname)
            except SourceError:
                problem = f"the destination store is not a {access} store."
            else:
                if back != content or served != (access == "public"):
                    problem = f"the destination store is not a {access} store."
    finally:
        try:
            dest.delete(pathname)
            deleted = True
        except SourceError:
            deleted = False
    if problem:
        raise KindMismatch(problem, deleted)
    return deleted


def copy_store(source, dest, *, prefix="", apply=False, max_bytes=DEFAULT_MAX_BYTES,
               rate=DEFAULT_RATE, clock=time.monotonic, sleep=time.sleep):
    """Copy (or, without `apply`, only count) every source object missing at the destination.

    Returns a dict of counts. `listed_bytes` is the source total. A dry run fills `would_copy`,
    `existing` and `size_differs` (an existing object whose size differs will be a conflict) and
    downloads nothing. `oversize` objects are also counted in `failed` when applying. An error
    listing either store raises SourceError. A failed fetch (or compare read) of one object counts
    as `failed` and the run goes on; the first failed put or read-back at the destination counts
    as `failed` and stops the run (`aborted`)."""
    throttle = Throttle(rate, clock, sleep)
    counts = dict.fromkeys(COUNT_NAMES, 0)
    listed = {o["pathname"]: o for o in source.list_objects(prefix)}
    there = {o["pathname"]: o for o in dest.list_objects(prefix)}
    counts["aborted"] = False
    counts["listed"] = len(listed)
    counts["listed_bytes"] = sum(o["size"] for o in listed.values())
    counts.update(would_copy=0, existing=0, size_differs=0, oversize=0)

    for pathname in sorted(listed):
        obj, present = listed[pathname], there.get(pathname)
        if not apply:
            if present:
                counts["existing"] += 1
                counts["size_differs"] += present["size"] != obj["size"]
            else:
                counts["would_copy"] += 1
            continue
        different = bool(present) and present["size"] != obj["size"]
        if obj["size"] > max_bytes and not different:
            counts["oversize"] += 1
            counts["failed"] += 1
            continue
        try:
            outcome = _one(source, dest, pathname, obj, present, throttle)
        except DestinationFailure:
            counts["failed"] += 1
            counts["aborted"] = True  # fail fast: nothing more is put into a store that refuses
            break
        counts[outcome] += 1
        if outcome == "copied":
            counts["bytes"] += obj["size"]
    return counts


def _one(source, dest, pathname, obj, present, throttle):
    """The outcome for one object: copied, skipped_existing, conflict or failed. A failed put or
    read-back at the destination raises DestinationFailure."""
    if present and present["size"] != obj["size"]:
        return "conflict"  # different sizes cannot be the same bytes: nothing to download
    throttle.wait()
    try:
        content, content_type = source.fetch(pathname)
        if present:
            throttle.wait()
            theirs, _ = dest.fetch(pathname)
    except SourceError:
        return "failed"
    if present:
        return "skipped_existing" if _sha(theirs) == _sha(content) else "conflict"
    throttle.wait()
    try:
        dest.put(pathname, content, content_type)
    except SourceError:
        raise DestinationFailure from None
    throttle.wait()
    try:
        back, _ = dest.fetch(pathname)
    except SourceError:
        # Cannot be proved; kept, and a re-run compares it as an existing object.
        raise DestinationFailure from None
    if _sha(back) != _sha(content):
        try:
            dest.delete(pathname)
        except SourceError:
            pass
        raise DestinationFailure
    return "copied"
