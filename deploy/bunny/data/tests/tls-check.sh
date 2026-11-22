#!/usr/bin/env bash
# Local proof that the two data images accept TLS connections only (needs docker compose).
#
#   deploy/bunny/data/tests/tls-check.sh            build, start, check, tear down
#   KEEP=1 deploy/bunny/data/tests/tls-check.sh    leave the stack running; prints the loopback ports
#                                                  and writes test-only URLs to $ENV_OUT (0600) if set
#
# Throw-away random test passwords; never printed. No real data. Exit 0 = every check passed.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export COMPOSE_PROJECT_NAME="negosyo-data-check"
C=(docker compose -f "$HERE/compose.yml")
pass=0; fail=0
ok()  { pass=$((pass + 1)); echo "ok   - $1"; }
bad() { fail=$((fail + 1)); echo "FAIL - $1"; [ -n "${2:-}" ] && echo "       $2"; }

TEST_PG_PASSWORD="$(openssl rand -hex 16)"; export TEST_PG_PASSWORD
TEST_REDIS_PASSWORD="$(openssl rand -hex 16)"; export TEST_REDIS_PASSWORD
TEST_PG_PORT="${TEST_PG_PORT:-$((20000 + RANDOM % 5000))}"; export TEST_PG_PORT
TEST_REDIS_PORT="${TEST_REDIS_PORT:-$((26000 + RANDOM % 5000))}"; export TEST_REDIS_PORT
cleanup() { [ -n "${KEEP:-}" ] || "${C[@]}" down --volumes --remove-orphans >/dev/null 2>&1; }
trap cleanup EXIT

# Start fresh every run: a volume or container left by KEEP=1 or an interrupted run would make Postgres skip
# init, so the new random test password would not match.
"${C[@]}" down --volumes --remove-orphans >/dev/null 2>&1
docker rm -f negosyo-data-check-allow >/dev/null 2>&1

MASK() { sed 's/password=[^ ]*/password=***/'; }
"${C[@]}" up -d --build >/dev/null 2>&1 || { echo "compose up failed"; "${C[@]}" logs --no-color 2>&1 | MASK | tail -20; exit 1; }

PGC() { "${C[@]}" ps -q postgres; }
RDC() { "${C[@]}" ps -q redis; }
# psql / redis-cli run inside the data containers and connect over TCP to 127.0.0.1, so pg_hba and the
# TLS port apply exactly as for a remote client. Passwords go in the environment, never in argv.
pg() { docker exec -e PGPASSWORD="${PGPW:-$TEST_PG_PASSWORD}" "$(PGC)" psql "$@" 2>&1; }
rcli() { docker exec -e REDISCLI_AUTH="${RPW:-}" "$(RDC)" redis-cli "$@" 2>&1; }
PGURL="host=127.0.0.1 port=15432 user=app dbname=app"

WAIT_TRIES=60; WAIT_SLEEP=2   # 120 s
not_ready() {  # $1 = what; the rest is a command that prints the recent log lines
  echo "$1 not ready after $((WAIT_TRIES * WAIT_SLEEP)) s"
  shift
  "$@" 2>&1 | MASK | tail -20
  exit 1
}
wait_pg() {
  local i ok=
  for i in $(seq "$WAIT_TRIES"); do
    docker exec "$(PGC)" pg_isready -h 127.0.0.1 -p 15432 >/dev/null 2>&1 && { ok=1; break; }
    sleep "$WAIT_SLEEP"
  done
  [ -n "$ok" ] || not_ready "Postgres (pg_isready)" "${C[@]}" logs --no-color --tail 20 postgres
  ok=
  for i in $(seq "$WAIT_TRIES"); do
    pg "$PGURL sslmode=require" -tAc 'select 1' 2>/dev/null | grep -q '^1$' && { ok=1; break; }
    sleep "$WAIT_SLEEP"
  done
  [ -n "$ok" ] || not_ready "Postgres (authenticated select 1 over TLS)" "${C[@]}" logs --no-color --tail 20 postgres
}
wait_redis() {
  local i
  for i in $(seq "$WAIT_TRIES"); do
    [ "$(RPW="$TEST_REDIS_PASSWORD" rcli --tls --insecure -h 127.0.0.1 -p 16379 ping)" = "PONG" ] && return 0
    sleep "$WAIT_SLEEP"
  done
  not_ready "Redis (TLS + password PING)" "${C[@]}" logs --no-color --tail 20 redis
}

wait_pg

echo "== PostgreSQL"
out="$(pg "$PGURL sslmode=disable" -tAc 'select 1')"
if [ "$out" != "1" ]; then ok "non-TLS connection is refused (sslmode=disable)"; else bad "non-TLS connection was accepted"; fi
case "$out" in *"no encryption"*|*reject*|*pg_hba*) ok "...and the server names the rule (reject for unencrypted)";; *) bad "refusal message unexpected" "$out";; esac
out="$(pg "$PGURL sslmode=allow" -tAc 'select ssl from pg_stat_ssl where pid = pg_backend_pid()')"
[ "$out" = "t" ] && ok "sslmode=allow (tries plain first, is rejected) ends up on a TLS session, never plain" || bad "sslmode=allow did not end on TLS" "$out"
out="$(pg "$PGURL sslmode=require" -tAc 'select ssl from pg_stat_ssl where pid = pg_backend_pid()')"
[ "$out" = "t" ] && ok "sslmode=require connects and the session is TLS (pg_stat_ssl)" || bad "TLS session not shown" "$out"
out="$(PGPW=wrong pg "$PGURL sslmode=require" -tAc 'select 1')"
[ "$out" != "1" ] && ok "wrong password is refused over TLS" || bad "wrong password accepted"
out="$(pg "$PGURL sslmode=require" -tAc 'show password_encryption')"
[ "$out" = "scram-sha-256" ] && ok "password_encryption = scram-sha-256" || bad "password_encryption" "$out"
out="$(pg "$PGURL sslmode=require" -tAc "select left(rolpassword, 14) from pg_authid where rolname='app'")"
[ "$out" = "SCRAM-SHA-256\$" ] && ok "the stored password is a SCRAM verifier" || bad "stored password form" "$out"
out="$(pg "host=127.0.0.1 port=5432 user=app dbname=app sslmode=require" -tAc 'select 1')"
[ "$out" != "1" ] && ok "the default port 5432 is not open" || bad "port 5432 answered"
hba="$("${C[@]}" exec -T postgres cat /etc/postgresql-negosyo/pg_hba.conf)"
if printf '%s\n' "$hba" | grep -Eq '^host[[:space:]]'; then bad "pg_hba has a plain 'host' line"; else ok "pg_hba has no plain 'host' (non-TLS accept) line"; fi
printf '%s\n' "$hba" | grep -Eq '^hostssl .* scram-sha-256$' && ok "pg_hba has hostssl ... scram-sha-256" || bad "no hostssl scram line"
fp1="$("${C[@]}" exec -T postgres openssl x509 -in /var/lib/postgresql/tls/server.crt -noout -fingerprint -sha256)"

echo "== PostgreSQL allow-list (PG_ALLOW_CIDRS)"
docker run -d --rm --name negosyo-data-check-allow -e POSTGRES_USER=app -e POSTGRES_PASSWORD="$TEST_PG_PASSWORD" -e POSTGRES_DB=app \
  -e PG_LISTEN_PORT=15433 -e PG_ALLOW_CIDRS=203.0.113.0/24 "$COMPOSE_PROJECT_NAME-postgres" >/dev/null 2>&1
allow_ok=
for _ in $(seq "$WAIT_TRIES"); do
  docker exec negosyo-data-check-allow pg_isready -h 127.0.0.1 -p 15433 >/dev/null 2>&1 && { allow_ok=1; break; }
  sleep "$WAIT_SLEEP"
done
[ -n "$allow_ok" ] || not_ready "Postgres allow-list container (pg_isready)" docker logs --tail 20 negosyo-data-check-allow
out="$(docker exec -e PGPASSWORD="$TEST_PG_PASSWORD" negosyo-data-check-allow psql "host=127.0.0.1 port=15433 user=app dbname=app sslmode=require" -tAc 'select 1' 2>&1)"
case "$out" in *"no pg_hba.conf entry"*) ok "a source outside PG_ALLOW_CIDRS is refused even with TLS + password";; *) bad "allow-list did not refuse" "$out";; esac
docker rm -f negosyo-data-check-allow >/dev/null 2>&1

echo "== Redis"
wait_redis
out="$(rcli -h 127.0.0.1 -p 16379 ping)"
case "$out" in PONG*) bad "plain (non-TLS) connection to the TLS port answered";; *) ok "non-TLS connection to the TLS port is refused";; esac
out="$(rcli -h 127.0.0.1 -p 6379 ping)"
case "$out" in PONG*) bad "plain port 6379 answered";; *) ok "the plain port 6379 is closed (port 0)";; esac
out="$(rcli --tls --insecure -h 127.0.0.1 -p 16379 ping)"
case "$out" in *NOAUTH*|*"Authentication required"*) ok "TLS without a password is refused (NOAUTH)";; *) bad "no-password ping" "$out";; esac
out="$(RPW="$TEST_REDIS_PASSWORD" rcli --tls --insecure -h 127.0.0.1 -p 16379 ping)"
[ "$out" = "PONG" ] && ok "TLS + password answers PONG" || bad "TLS + password ping" "$out"
out="$(RPW="$TEST_REDIS_PASSWORD" rcli --tls --cacert /data/tls/redis.crt -h 127.0.0.1 -p 16379 ping)"
[ "$out" = "PONG" ] && ok "TLS + password verifies against the pinned certificate (--cacert)" || bad "pinned-certificate ping" "$out"
out="$(RPW=wrong rcli --tls --insecure -h 127.0.0.1 -p 16379 ping)"
case "$out" in PONG) bad "wrong password accepted";; *) ok "wrong password is refused over TLS";; esac
out="$(RPW="$TEST_REDIS_PASSWORD" rcli --tls --insecure -h 127.0.0.1 -p 16379 config get appendonly | tr '\n' ' ')"
case "$out" in *"appendonly yes"*) ok "append-only file is on";; *) bad "appendonly" "$out";; esac
out="$(RPW="$TEST_REDIS_PASSWORD" rcli --tls --insecure -h 127.0.0.1 -p 16379 config get port | tr '\n' ' ')"
case "$out" in *"port 0"*) ok "plain port is 0 in the running config";; *) bad "port 0" "$out";; esac
ps_out="$(docker exec "$(RDC)" sh -c 'cat /proc/*/cmdline 2>/dev/null | tr "\0" " "')"
case "$ps_out" in *"$TEST_REDIS_PASSWORD"*) bad "the Redis password is visible in a process command line";; *) ok "the Redis password is in no process command line";; esac
fr1="$("${C[@]}" exec -T redis openssl x509 -in /data/tls/redis.crt -noout -fingerprint -sha256)"

echo "== Certificates survive a restart (kept on the volume)"
"${C[@]}" restart postgres redis >/dev/null 2>&1
wait_pg
wait_redis
fp2="$("${C[@]}" exec -T postgres openssl x509 -in /var/lib/postgresql/tls/server.crt -noout -fingerprint -sha256)"
fr2="$("${C[@]}" exec -T redis openssl x509 -in /data/tls/redis.crt -noout -fingerprint -sha256)"
[ -n "$fp1" ] && [ "$fp1" = "$fp2" ] && ok "Postgres certificate unchanged after restart" || bad "Postgres certificate changed"
[ -n "$fr1" ] && [ "$fr1" = "$fr2" ] && ok "Redis certificate unchanged after restart" || bad "Redis certificate changed"

if [ -n "${KEEP:-}" ] && [ -n "${ENV_OUT:-}" ]; then
  umask 077
  {
    echo "DATABASE_URL=postgresql://app:$TEST_PG_PASSWORD@127.0.0.1:$TEST_PG_PORT/app?sslmode=require"
    echo "REDIS_URL=rediss://:$TEST_REDIS_PASSWORD@127.0.0.1:$TEST_REDIS_PORT/0"
    echo "REDIS_CA_PEM_CMD=docker compose -f $HERE/compose.yml -p $COMPOSE_PROJECT_NAME exec -T redis cat /data/tls/redis.crt"
  } > "$ENV_OUT"
  echo "kept: test URLs written to $ENV_OUT"
fi
echo; echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
