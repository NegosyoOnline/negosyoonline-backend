#!/bin/sh
# Bunny worker container: same command as scripts/start_worker.sh. The image already runs as
# `nobody`, so there is no privilege drop. Waits until the schema is migrated, so a new worker
# never runs against the old schema while the api container is still migrating.
set -eu
. /app/deploy/bunny/_wait_db.sh
sh /app/deploy/bunny/wait-migrated.sh
exec celery -A config worker --loglevel=info --concurrency=2 "$@"
