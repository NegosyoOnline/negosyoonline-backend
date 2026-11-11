#!/usr/bin/env bash
# Usage: scripts/verify_deploy.sh <healthz-url> [expected-commit-prefix]
# Polls the health endpoint until it returns status ok (and the expected commit, if given).
set -euo pipefail

url="${1:?usage: verify_deploy.sh <healthz-url> [expected-commit-prefix]}"
expected="${2:-}"
attempts=18
delay=10

for i in $(seq 1 "$attempts"); do
  body="$(curl -fsS --max-time 10 "$url" || true)"
  if echo "$body" | grep -q '"status": "ok"'; then
    if [ -z "$expected" ] || echo "$body" | grep -q "\"commit\": \"${expected}"; then
      echo "OK: $body"
      exit 0
    fi
  fi
  echo "attempt $i/$attempts: not ready ($body)"
  sleep "$delay"
done

echo "FAILED: $url did not report the expected release" >&2
exit 1
