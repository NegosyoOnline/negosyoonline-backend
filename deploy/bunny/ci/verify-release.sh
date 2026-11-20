#!/usr/bin/env bash
# verify-release.sh <base-url> <full-sha> [timeout-seconds]
# 1. Poll <base-url>/healthz/ until "commit" equals the first 12 characters of <full-sha>, 3 polls in a row.
# 2. Poll <base-url>/readyz/ until it answers 200 with every check true (including beat).
# Exits non-zero on timeout. Prints only the commit and the names of failing checks.
set -euo pipefail
url="${1:?base url}"; want="${2:?sha}"; want="${want:0:12}"; limit="${3:-900}"
url="${url%/}"; start=$SECONDS; streak=0
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
echo "waiting for $url to report commit $want"
while :; do
  got="$(curl --silent --max-time 10 "$url/healthz/" | jq -r '.commit // empty' 2>/dev/null || true)"
  if [ "$got" = "$want" ]; then streak=$((streak + 1)); else streak=0; fi
  [ "$streak" -ge 3 ] && break
  if [ $((SECONDS - start)) -ge "$limit" ]; then
    echo "timeout: $url reports commit '${got:-none}', wanted $want" >&2; exit 1
  fi
  sleep 10
done
echo "healthz ok: commit $want"
while :; do
  code="$(curl --silent --max-time 10 --output "$tmp" --write-out '%{http_code}' "$url/readyz/" || true)"
  if [ "$code" = "200" ] && jq -e '[.[]] | all(. == true)' "$tmp" >/dev/null 2>&1; then
    echo "readyz ok: all checks true"; exit 0
  fi
  if [ $((SECONDS - start)) -ge "$limit" ]; then
    echo "timeout: readyz HTTP $code; failing checks: $(jq -r 'to_entries | map(select(.value != true) | .key) | join(",")' "$tmp" 2>/dev/null || echo unknown)" >&2
    exit 1
  fi
  sleep 15
done
