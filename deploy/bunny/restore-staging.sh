#!/bin/sh
set -eu

: "${STAGING_ARCHIVE_URL:?Missing private restore archive URL}"
: "${STAGING_ARCHIVE_KEY:?Missing restore archive key}"
: "${STAGING_ARCHIVE_SHA256:?Missing restore archive checksum}"
: "${PRIVATE_BLOB_READ_WRITE_TOKEN:?Missing private Blob token}"
: "${POSTGRES_DB:?Missing staging database name}"
: "${POSTGRES_USER:?Missing staging database role}"

case "$STAGING_ARCHIVE_URL" in
    https://*.private.blob.vercel-storage.com/*) ;;
    *) echo "Restore URL must be on a private Vercel Blob store" >&2; exit 1 ;;
esac

umask 077
archive_dir=$(mktemp -d)
trap 'rm -rf "$archive_dir"' EXIT

curl --fail --silent --show-error --max-time 120 \
    --header "Authorization: Bearer $PRIVATE_BLOB_READ_WRITE_TOKEN" \
    --output "$archive_dir/staging.dump.enc" "$STAGING_ARCHIVE_URL"

openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -pass env:STAGING_ARCHIVE_KEY \
    -in "$archive_dir/staging.dump.enc" -out "$archive_dir/staging.dump"
printf '%s  %s\n' "$STAGING_ARCHIVE_SHA256" "$archive_dir/staging.dump" |
    sha256sum --check --status

pg_restore --exit-on-error --single-transaction --no-owner --no-privileges \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" "$archive_dir/staging.dump"
printf 'Staging archive restored and checksum verified.\n'
