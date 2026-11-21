#!/usr/bin/env bash
# plan-production.sh: decides which commit a "Bunny production release" run deploys, or refuses.
# Run inside a full clone (all branches fetched). Inputs (environment):
#   EVENT_NAME       push | workflow_dispatch | workflow_run
#   REF              GITHUB_REF of the run
#   RUN_SHA          GITHUB_SHA of the run (for workflow_run this is main's tip, not the release)
#   INPUT_SHA        workflow_dispatch input `sha` (optional)
#   RUN_HEAD_SHA     workflow_run.head_sha (the commit "Promote to production" ran for)
#   IMAGE_BASE       image repository base (staging's), lower-cased on output
#   PRODUCTION_URL   API base URL; its public /healthz/ commit tells whether production already runs the sha
# Rules:
#   push / dispatch  only from refs/heads/production; dispatch `sha` (redeploy) must have been on production.
#   workflow_run     the sha must be exactly the HEAD of origin/production (what the promotion pushed).
#   all              40 lowercase hex characters, and a commit on origin/main.
# Outputs (to $GITHUB_OUTPUT if set, else stdout): sha, mode (release|redeploy), image_repo, skip.
# skip=true when production already serves the sha (so a second trigger for the same release, e.g. a
# push run plus a workflow_run run, stops before asking for another approval).
set -euo pipefail
event="${EVENT_NAME:?}"; ref="${REF:-}"; run_sha="${RUN_SHA:?}"; mode=release
case "$event" in
  push|workflow_dispatch)
    [ "$ref" = "refs/heads/production" ] \
      || { echo "production releases run only from the production branch (got $ref)" >&2; exit 1; }
    sha="${INPUT_SHA:-$run_sha}"
    if [ "$sha" != "$run_sha" ]; then mode=redeploy; fi
    ;;
  workflow_run)
    sha="${RUN_HEAD_SHA:?}"
    ;;
  *) echo "unsupported event $event" >&2; exit 1 ;;
esac
printf '%s' "$sha" | grep -Eq '^[0-9a-f]{40}$' || { echo "sha must be 40 lowercase hex characters" >&2; exit 1; }
if ! git cat-file -e "$sha^{commit}" 2>/dev/null || ! git merge-base --is-ancestor "$sha" "origin/main"; then
  echo "$sha is not a commit on main" >&2; exit 1
fi
if [ "$event" = workflow_run ]; then
  head="$(git rev-parse --verify --quiet origin/production || true)"
  [ "$head" = "$sha" ] \
    || { echo "$sha is not the head of the production branch (${head:-no production branch}); a newer promotion superseded it" >&2; exit 1; }
elif [ "$mode" = redeploy ] && ! git merge-base --is-ancestor "$sha" "origin/production"; then
  echo "$sha was never on the production branch" >&2; exit 1
fi
skip=false
if [ -n "${PRODUCTION_URL:-}" ]; then
  got="$(curl --silent --max-time 15 "${PRODUCTION_URL%/}/healthz/" 2>/dev/null | jq -r '.commit // empty' 2>/dev/null || true)"
  if [ -n "$got" ] && [ "$got" = "${sha:0:12}" ]; then skip=true; fi
fi
base="$(printf '%s' "${IMAGE_BASE:-}" | tr '[:upper:]' '[:lower:]')"
{
  echo "sha=$sha"
  echo "mode=$mode"
  echo "image_repo=$base"
  echo "skip=$skip"
} >> "${GITHUB_OUTPUT:-/dev/stdout}"
if [ "$skip" = true ]; then
  msg="Production already runs ${sha:0:12}: nothing to deploy (a second trigger for the same release stops here)."
else
  msg="Production release $sha ($mode)"
fi
echo "$msg" >> "${GITHUB_STEP_SUMMARY:-/dev/stderr}"
