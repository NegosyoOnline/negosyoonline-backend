#!/bin/sh
# Bunny beat container: same command as scripts/start_beat.sh (schedule file in /tmp), after the schema is migrated.
set -eu
. /app/deploy/bunny/_wait_db.sh
sh /app/deploy/bunny/wait-migrated.sh
exec celery -A config beat --loglevel=info --schedule=/tmp/celerybeat-schedule "$@"
