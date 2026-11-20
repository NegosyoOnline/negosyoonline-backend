#!/bin/sh
# Wait (up to WAIT_MIGRATED_SECONDS, default 900) until no migration is unapplied. Read-only: never migrates.
set -eu
limit="${WAIT_MIGRATED_SECONDS:-900}"
waited=0
until python manage.py migrate --check >/dev/null 2>&1; do
    if [ "$waited" -ge "$limit" ]; then
        echo "migrations still pending after ${limit}s; exiting so the platform restarts this container" >&2
        exit 1
    fi
    sleep 5
    waited=$((waited + 5))
done
