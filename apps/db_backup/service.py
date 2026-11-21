"""Nightly encrypted database copy in the private Blob store, and its retention.

A copy is `db-backups/<UTC time>.dump.enc`: version | nonce | AES-256-GCM(pg_dump -Fc), with the
pathname as associated data (apps/file_backup/crypto.py), so a copy cannot be swapped under
another name. Retention keeps the newest copy of each of the 14 newest days that have a copy and
the newest copy of each of the 8 newest weeks (counted from the newest copy, not from today, so a
stopped backup never empties the store). Only names this module makes are ever deleted.
Nothing here logs a pathname, a key or content.
"""
import re
from datetime import datetime, timezone

from apps.db_backup import constants
from apps.file_backup import crypto

NAME = re.compile(r"^" + re.escape(constants.PREFIX) + r"(\d{8}T\d{6}Z)" + re.escape(constants.SUFFIX) + r"$")
STAMP = "%Y%m%dT%H%M%SZ"


class BackupError(Exception):
    pass


def pathname_for(moment):
    return f"{constants.PREFIX}{moment.astimezone(timezone.utc):{STAMP}}{constants.SUFFIX}"


def parse_name(pathname):
    """The UTC time in a copy's name, or None if this is not one of ours."""
    found = NAME.match(pathname or "")
    return datetime.strptime(found.group(1), STAMP).replace(tzinfo=timezone.utc) if found else None


def list_copies(store):
    """[(pathname, time)] newest first, only names this module makes."""
    copies = []
    for item in store.list_objects(constants.PREFIX):
        moment = parse_name(item["pathname"])
        if moment:
            copies.append((item["pathname"], moment))
    copies.sort(key=lambda c: c[1], reverse=True)
    return copies


def select_keep(copies, daily=constants.KEEP_DAILY, weekly=constants.KEEP_WEEKLY):
    """The set of pathnames to keep, from copies sorted newest first."""
    keep, days, weeks = set(), [], []
    for pathname, moment in copies:
        day = moment.date()
        if day not in days and len(days) < daily:
            days.append(day)
            keep.add(pathname)
        iso = moment.isocalendar()[:2]
        if iso not in weeks and len(weeks) < weekly:
            weeks.append(iso)
            keep.add(pathname)
    return keep


def apply_retention(store, copies=None):
    copies = list_copies(store) if copies is None else copies
    keep = select_keep(copies)
    deleted = 0
    for pathname, _ in copies:
        if pathname not in keep:
            store.delete(pathname)
            deleted += 1
    return deleted


def newest_copy_time(store):
    copies = list_copies(store)
    return copies[0][1] if copies else None


def run_backup(store, key, dump, now):
    """Dump, encrypt, upload, then apply retention. Returns counts only."""
    data = dump()
    if not data:
        raise BackupError("The dump was empty.")
    pathname = pathname_for(now)
    stored = crypto.encrypt(key, pathname, data)
    store.put(pathname, stored, "application/octet-stream")
    deleted = apply_retention(store)
    return {"uploaded": 1, "bytes": len(stored), "deleted": deleted, "at": now.timestamp()}


def fetch_plain(store, key, pathname):
    """Download one named copy and decrypt it."""
    if not NAME.match(pathname or ""):
        raise BackupError("That is not a database backup name.")
    stored, _ = store.fetch(pathname)
    return crypto.decrypt(key, pathname, stored)
