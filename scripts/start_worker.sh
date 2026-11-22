#!/bin/sh
# Task z8uq9mevfy: start the Celery worker as a non-root user.
# Why: Celery warns when run as root, and a bug in a task would then run as root.
# No file in this repository sets a service's start command, so the
# start command of the `worker` service is `sh scripts/start_worker.sh`.
# If the user `nobody` is missing, or we are not root, it starts without the drop.
set -eu

if [ "$(id -u)" = "0" ] && id nobody >/dev/null 2>&1; then
  exec uv run celery -A config worker --loglevel=info --concurrency=2 --uid=nobody --gid=nogroup "$@"
fi

echo "start_worker: not dropping privileges (not root, or user nobody missing)" >&2
exec uv run celery -A config worker --loglevel=info --concurrency=2 "$@"
