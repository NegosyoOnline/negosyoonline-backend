#!/bin/sh
# Sourced by the start scripts: wait until DATABASE_URL accepts a connection (60 s). Prints no values.
: "${DATABASE_URL:?DATABASE_URL is not set}"
attempt=0
until python -c 'import os, psycopg; psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2).close()' 2>/dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "database did not become ready" >&2
        exit 1
    fi
    sleep 2
done
