#!/bin/sh
# Entrypoint of the api image: migrate under the advisory lock (Bunny has no one-off job API), then start.
# MIGRATE_ON_START=0 skips the migration (default 1).
set -eu
. /app/deploy/bunny/_wait_db.sh
if [ "${MIGRATE_ON_START:-1}" = "1" ]; then
    sh /app/deploy/bunny/migrate.sh
fi
exec sh /app/deploy/bunny/start-api.sh
