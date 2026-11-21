#!/usr/bin/env bash
# require-staging-release.sh <owner/repo> <full-sha>
# Production only deploys what Bunny staging already ran. Succeeds only when the "Bunny staging
# release" workflow has a successful push run for exactly <full-sha> (checks, images, deploy and
# verify all passed there). Manual redeploy runs of that workflow do not count (event must be push).
# Needs GH_TOKEN (the workflow token, actions: read). Prints no secrets.
set -euo pipefail
repo="${1:?owner/repo}"; sha="${2:?sha}"
printf '%s' "$sha" | grep -Eq '^[0-9a-f]{40}$' || { echo "sha must be 40 lowercase hex characters" >&2; exit 1; }
n="$(gh api "repos/$repo/actions/workflows/bunny-staging.yml/runs?head_sha=$sha&event=push&per_page=100" \
  --jq '[.workflow_runs[] | select(.head_sha == "'"$sha"'" and .event == "push" and .conclusion == "success")] | length')"
if [ "${n:-0}" -lt 1 ]; then
  echo "no successful 'Bunny staging release' push run exists for $sha: it was not built and deployed on staging, so it cannot go to production" >&2
  exit 1
fi
echo "staging release run for $sha: success"
