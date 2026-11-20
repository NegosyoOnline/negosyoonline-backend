#!/bin/sh
# Locked, retry-safe migration. Safe to run any number of times, from any container, at once:
# a PostgreSQL session advisory lock (fixed key) lets exactly one run migrate; the others wait
# (up to MIGRATE_LOCK_WAIT_SECONDS, default 600), then find nothing to do. Exits non-zero on any failure.
# Prints no environment values.
set -eu
exec python /app/deploy/bunny/migrate_locked.py
