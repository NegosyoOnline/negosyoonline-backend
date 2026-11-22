#!/bin/sh
# Redis data app entrypoint. Runs as root, prepares TLS and a private config file, then hands over
# to the official image entrypoint (chown /data, drop to the redis user).
#
# Env:
#   REDIS_PASSWORD   required (requirepass). Written only to a 0600 config file, never to argv or logs.
#   REDIS_TLS_PORT   default 16379 (the plain port is off: `port 0`)
#   TLS_SAN          optional extra subjectAltName entries for the self-signed certificate
#   REDIS_TLS_DIR    default /data/tls
# Redis has no source-address filter, so there is no allow-list here: the TLS port plus the
# password are the controls (the Postgres image has PG_ALLOW_CIDRS).
set -eu

: "${REDIS_PASSWORD:?REDIS_PASSWORD is not set}"
PORT="${REDIS_TLS_PORT:-16379}"
TLS_DIR="${REDIS_TLS_DIR:-/data/tls}"
case "$PORT" in ''|*[!0-9]*) echo "REDIS_TLS_PORT must be a number" >&2; exit 1 ;; esac
case "$REDIS_PASSWORD" in *[[:space:]\"\\]*) echo "REDIS_PASSWORD must not contain spaces, quotes or backslashes" >&2; exit 1 ;; esac

if [ "$(id -u)" = 0 ] && [ "${1:-}" = "redis-server" ]; then
    umask 077
    mkdir -p "$TLS_DIR"
    if [ ! -s "$TLS_DIR/redis.key" ] || [ ! -s "$TLS_DIR/redis.crt" ]; then
        san="DNS:localhost,IP:127.0.0.1"
        [ -z "${TLS_SAN:-}" ] || san="$san,$TLS_SAN"
        openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
            -subj "/CN=negosyoonline-data-redis" -addext "subjectAltName=$san" \
            -keyout "$TLS_DIR/redis.key" -out "$TLS_DIR/redis.crt" >/dev/null 2>&1
        echo "redis: created a self-signed TLS certificate (kept in $TLS_DIR)"
    fi
    chown -R redis:redis "$TLS_DIR"
    chmod 0700 "$TLS_DIR"
    chmod 0600 "$TLS_DIR/redis.key"
    chmod 0644 "$TLS_DIR/redis.crt"

    CONF=/run/negosyo-redis.conf
    : > "$CONF"
    chmod 0600 "$CONF"
    {
        echo "port 0"
        echo "tls-port $PORT"
        echo "tls-cert-file $TLS_DIR/redis.crt"
        echo "tls-key-file $TLS_DIR/redis.key"
        echo "tls-ca-cert-file $TLS_DIR/redis.crt"
        echo "tls-auth-clients no"
        echo "tls-protocols \"TLSv1.2 TLSv1.3\""
        echo "requirepass $REDIS_PASSWORD"
        echo "appendonly yes"
        echo "dir /data"
        echo "protected-mode yes"
    } > "$CONF"
    chown redis:redis "$CONF"
    umask 022   # the official entrypoint creates the data directories: do not hand it a 077 umask
    shift
    set -- redis-server "$CONF" "$@"
fi
unset REDIS_PASSWORD
exec docker-entrypoint.sh "$@"
