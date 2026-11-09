#!/usr/bin/env bash
# Usage: scripts/wait_railway_deploy.sh <before-deployment-id>
# Waits until Railway staging has a NEWER deployment than <before-deployment-id> and it finishes.
# Exit 0: newest deployment is SUCCESS. Exit 2: it ended FAILED/CRASHED/REMOVED/SKIPPED.
# Exit 3: still not finished at the cap (~20 min). Prints "<STATE> <id> <commit>".
# Reads Railway only. Never changes anything. Pass the project, environment and service explicitly.
set -uo pipefail

before="${1:-}"
case "$before" in ""|*"<"*|*">"*) echo "EMPTY_BEFORE: pass the pre-merge deployment id" >&2; exit 1;; esac

project="negosyoonline-backend"
environment="staging"
service="api"
attempts=80
delay=15

for i in $(seq 1 "$attempts"); do
  out="$(railway deployment list -p "$project" -e "$environment" -s "$service" --limit 1 --json 2>/dev/null || true)"
  line="$(printf '%s' "$out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)[0]
    print(d.get("id", ""), d.get("status", ""), (d.get("meta") or {}).get("commitHash", "")[:12])
except Exception:
    print("")
' 2>/dev/null)"
  id="$(echo "$line" | awk '{print $1}')"
  status="$(echo "$line" | awk '{print $2}')"
  commit="$(echo "$line" | awk '{print $3}')"
  if [ -n "$id" ] && [ "$id" != "$before" ]; then
    case "$status" in
      SUCCESS) echo "SUCCESS $id $commit"; exit 0;;
      FAILED|CRASHED|REMOVED|SKIPPED) echo "$status $id $commit"; exit 2;;
    esac
  fi
  sleep "$delay"
done

echo "STILL_WAITING ${id:-none} ${status:-unknown} ${commit:-}"
exit 3
