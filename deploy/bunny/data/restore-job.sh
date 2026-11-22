#!/bin/sh
# Restore job for the Bunny data app. Run it in the worker image (it has pg_restore 18, Django and
# the backup code) as a one-off container, or from a shell in it. It:
#   1. downloads ONE encrypted copy db-backups/<time>.dump.enc from the private Blob store,
#   2. decrypts it with DB_BACKUP_ENCRYPTION_KEY (AES-256-GCM, as the nightly backup wrote it),
#   3. pg_restores it into the data app's PostgreSQL over TLS,
#   4. writes a count summary (table count and a row count per table; no row content) to the
#      private Blob store as ops/restore-summary-<time>.json.
# It prints no secret, no URL and no data. The target database must already exist and be empty.
#
# Env (names only):
#   RESTORE_BACKUP_NAME     db-backups/<UTC time>.dump.enc  (list them: manage.py restore_database --list)
#   RESTORE_TARGET_URL      postgresql://<user>:<password>@<data-app host>:<port>/<db>?sslmode=require
#   DB_BACKUP_ENCRYPTION_KEY, PRIVATE_BLOB_READ_WRITE_TOKEN   as on the worker
#   DJANGO_SECRET_KEY       any value the settings accept
set -eu
: "${RESTORE_BACKUP_NAME:?RESTORE_BACKUP_NAME is not set}"
: "${RESTORE_TARGET_URL:?RESTORE_TARGET_URL is not set}"
: "${DB_BACKUP_ENCRYPTION_KEY:?DB_BACKUP_ENCRYPTION_KEY is not set}"
: "${PRIVATE_BLOB_READ_WRITE_TOKEN:?PRIVATE_BLOB_READ_WRITE_TOKEN is not set}"
case "$RESTORE_TARGET_URL" in
    postgres://*sslmode=require*|postgresql://*sslmode=require*|postgres://*sslmode=verify-*|postgresql://*sslmode=verify-*) ;;
    *) echo "RESTORE_TARGET_URL must be a PostgreSQL URL with sslmode=require" >&2; exit 1 ;;
esac
cd /app
exec python manage.py restore_database --name "$RESTORE_BACKUP_NAME" --target-url "$RESTORE_TARGET_URL" --summary
