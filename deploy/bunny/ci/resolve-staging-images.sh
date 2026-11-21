#!/usr/bin/env bash
# resolve-staging-images.sh <image-repo> <full-sha>
# Looks up the digests of the images that bunny-staging.yml already built for <full-sha>
# (<image-repo>:<sha>-api|worker|beat). Never builds or pushes. Fails if any image is missing or
# its digest is not a sha256. Needs a registry login. Writes role=digest lines to $GITHUB_OUTPUT and
# a bullet list to $GITHUB_STEP_SUMMARY when those are set; always prints the bullet list.
set -euo pipefail
repo="${1:?image repo}"; sha="${2:?sha}"
printf '%s' "$sha" | grep -Eq '^[0-9a-f]{40}$' || { echo "sha must be 40 lowercase hex characters" >&2; exit 1; }
for role in api worker beat; do
  digest="$(docker buildx imagetools inspect "$repo:$sha-$role" --format '{{.Manifest.Digest}}' 2>/dev/null || true)"
  printf '%s' "$digest" | grep -Eq '^sha256:[0-9a-f]{64}$' \
    || { echo "no staging-built $role image $repo:$sha-$role (production never rebuilds)" >&2; exit 1; }
  line="- $role: \`$repo:$sha-$role@$digest\`"
  echo "$line"
  if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "$role=$digest" >> "$GITHUB_OUTPUT"; fi
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then echo "$line" >> "$GITHUB_STEP_SUMMARY"; fi
done
