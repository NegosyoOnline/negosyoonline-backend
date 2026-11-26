#!/usr/bin/env bash
# verify-hq.sh <hq-base-url> [timeout-seconds]
# The HQ host has no /healthz/ (it serves apps.operators.hq_urls only). Poll GET <hq-base-url>/ without following
# redirects until it answers 303 whose redirect path is exactly /sign-in/, 3 polls in a row.
# Any other answer resets the count. Exits non-zero on timeout.
# Prints only the http code and the redirect path (no host, no query string, no body).
set -euo pipefail
url="${1:?hq base url}"; limit="${2:-300}"
url="${url%/}"; start=$SECONDS; streak=0
echo "waiting for the HQ host to answer 303 to /sign-in/"
while :; do
  out="$(curl --silent --max-time 10 -o /dev/null -w '%{http_code} %{redirect_url}' "$url/" || true)"
  code="${out%% *}"; loc="${out#* }"
  [ "$loc" = "$out" ] && loc=""
  path=""
  if [ -n "$loc" ]; then
    path="${loc#*://}"; path="${path#*/}"; path="/${path}"   # drop scheme and host
    path="${path%%\?*}"; path="${path%%#*}"                   # drop query and fragment
  fi
  if [ "$code" = "303" ] && [ "$path" = "/sign-in/" ]; then streak=$((streak + 1)); else streak=0; fi
  [ "$streak" -ge 3 ] && break
  if [ $((SECONDS - start)) -ge "$limit" ]; then
    echo "timeout: HQ host answered ${code:-none} ${path:-(no redirect)}, wanted 303 /sign-in/" >&2; exit 1
  fi
  sleep 10
done
echo "hq ok: 303 /sign-in/"
