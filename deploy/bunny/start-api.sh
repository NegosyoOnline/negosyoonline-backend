#!/bin/sh
# Bunny api container: wait for the database, then run Gunicorn. NEVER runs migrate
# (see init-api.sh / migrate.sh). Same command as the production api service (docs/runbooks/billing.md).
set -eu
. /app/deploy/bunny/_wait_db.sh
: "${REDIS_URL:?REDIS_URL is not set}"
exec gunicorn config.asgi:application -k uvicorn_worker.UvicornWorker \
    --bind "0.0.0.0:${PORT:-8000}" --access-logfile - --error-logfile -
