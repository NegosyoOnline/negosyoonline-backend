#!/bin/sh
set -eu

: "${DATABASE_URL:?Set DATABASE_URL to the isolated Bunny staging database}"
: "${REDIS_URL:?Set REDIS_URL to the isolated Bunny staging Redis instance}"

attempt=0
until python -c 'import os, psycopg; psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2).close()' 2>/dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Bunny staging database did not become ready" >&2
        exit 1
    fi
    sleep 2
done

python manage.py migrate --noinput
exec "$@"
