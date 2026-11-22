#!/bin/sh
# PostgreSQL data app entrypoint. Runs as root, prepares TLS and pg_hba, then hands over to the
# official image entrypoint (which initialises the cluster on an empty volume and drops to postgres).
#
# Env:
#   POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB   required on first start (official image)
#   PG_LISTEN_PORT   TCP port, default 15432 (not the default 5432)
#   PG_ALLOW_CIDRS   optional, comma-separated source ranges allowed to connect, e.g.
#                    "203.0.113.0/24,2001:db8::/32". Empty = any source, still TLS + SCRAM only.
#   TLS_SAN          optional extra subjectAltName entries for the self-signed certificate,
#                    e.g. "DNS:db.example.com,IP:192.0.2.10"
#   PG_TLS_DIR       default /var/lib/postgresql/tls (on the volume)
# Secrets are never printed.
set -eu

PORT="${PG_LISTEN_PORT:-15432}"
TLS_DIR="${PG_TLS_DIR:-/var/lib/postgresql/tls}"
HBA="/etc/postgresql-negosyo/pg_hba.conf"
case "$PORT" in ''|*[!0-9]*) echo "PG_LISTEN_PORT must be a number" >&2; exit 1 ;; esac

if [ "$(id -u)" = 0 ] && [ "${1:-}" = "postgres" ]; then
    umask 077
    mkdir -p "$TLS_DIR"
    if [ ! -s "$TLS_DIR/server.key" ] || [ ! -s "$TLS_DIR/server.crt" ]; then
        san="DNS:localhost,IP:127.0.0.1"
        [ -z "${TLS_SAN:-}" ] || san="$san,$TLS_SAN"
        openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
            -subj "/CN=negosyoonline-data-postgres" -addext "subjectAltName=$san" \
            -keyout "$TLS_DIR/server.key" -out "$TLS_DIR/server.crt" >/dev/null 2>&1
        echo "postgres: created a self-signed TLS certificate (kept on the volume)"
    fi
    chown postgres:postgres "$TLS_DIR" "$TLS_DIR/server.key" "$TLS_DIR/server.crt"
    chmod 0700 "$TLS_DIR"
    chmod 0600 "$TLS_DIR/server.key"
    chmod 0644 "$TLS_DIR/server.crt"

    mkdir -p "$(dirname "$HBA")"
    chmod 0755 "$(dirname "$HBA")"
    {
        echo "# generated at start; TLS + SCRAM only"
        echo "local all all scram-sha-256"
        allow="${PG_ALLOW_CIDRS:-}"
        if [ -z "$allow" ]; then allow="0.0.0.0/0,::/0"; fi
        old_ifs="$IFS"
        IFS=","
        for cidr in $allow; do
            cidr="$(printf '%s' "$cidr" | tr -d ' ')"
            [ -n "$cidr" ] || continue
            case "$cidr" in *[!0-9a-fA-F:./]*) echo "PG_ALLOW_CIDRS has an invalid entry" >&2; exit 1 ;; esac
            echo "hostssl all all $cidr scram-sha-256"
        done
        IFS="$old_ifs"
        echo "hostnossl all all 0.0.0.0/0 reject"
        echo "hostnossl all all ::/0 reject"
    } > "$HBA"
    chown postgres:postgres "$HBA"
    chmod 0644 "$HBA"

    umask 022   # the official entrypoint creates the data directories: do not hand it a 077 umask
    # PGPORT: the init-time temporary server and psql inside the official entrypoint use it too.
    export PGPORT="$PORT"
    shift
    set -- postgres \
        -c "listen_addresses=*" \
        -c "ssl=on" -c "ssl_cert_file=$TLS_DIR/server.crt" -c "ssl_key_file=$TLS_DIR/server.key" \
        -c "ssl_min_protocol_version=TLSv1.2" \
        -c "password_encryption=scram-sha-256" \
        -c "hba_file=$HBA" "$@"
fi
exec docker-entrypoint.sh "$@"
