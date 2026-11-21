#!/usr/bin/env bash
# list-migrations.sh <production-base-url|""> <full-sha>
# Prints (markdown) the migration files the release would add on top of what production runs now,
# so the approver sees them before the gate. Production's commit comes from <url>/healthz/ (public).
# Run inside a full clone (fetch-depth 0). Reads no database; it compares files, so it is a list of
# what the release brings, not proof of what is applied. Unknown production commit = every
# migration in the release is listed.
set -euo pipefail
url="${1-}"; sha="${2:?sha}"
glob=':(glob)**/migrations/*.py'
prev=""
if [ -n "$url" ]; then
  prev="$(curl --silent --max-time 15 "${url%/}/healthz/" 2>/dev/null | jq -r '.commit // empty' 2>/dev/null || true)"
fi
full=""
if printf '%s' "$prev" | grep -Eq '^[0-9a-f]{7,40}$'; then
  full="$(git rev-parse --verify --quiet "$prev^{commit}" 2>/dev/null || true)"
fi
echo "### Migrations this release would apply"
if [ -z "$full" ]; then
  echo "Production's commit could not be read (${prev:-no answer}). Every migration file in the release is listed; some may already be applied."
  echo
  git ls-tree -r --name-only "$sha" | { grep -E '/migrations/[^/]+\.py$' || true; } | { grep -v '/__init__\.py$' || true; } \
    | while IFS= read -r f; do
        # shellcheck disable=SC2016
        printf -- '- `%s`\n' "$f"; done
  exit 0
fi
if [ "$full" = "$sha" ]; then
  echo "Production already runs this commit (\`${full:0:12}\`). No migrations to apply."
  exit 0
fi
base="$full"
if ! git merge-base --is-ancestor "$full" "$sha"; then
  base="$(git merge-base "$full" "$sha" || true)"
  echo "Production runs \`${full:0:12}\`, which is not an ancestor of this release (a rollback or a diverged commit). Compared from the common ancestor."
  echo
fi
list="$(git diff --name-status --no-renames "${base:-$full}" "$sha" -- "$glob" | { grep -v '/__init__\.py$' || true; })"
echo "Production runs \`${full:0:12}\`; release \`${sha:0:12}\`."
echo
if [ -z "$list" ]; then
  echo "No migration files added or changed. The api start will find nothing to apply."
  exit 0
fi
warn=0
while IFS=$'\t' read -r st path; do
  case "$st" in
    A) echo "- NEW \`$path\`" ;;
    *) echo "- **$st (an existing migration changed or removed: review before approving)** \`$path\`"; warn=1 ;;
  esac
done <<< "$list"
echo
echo "The api container applies these on start under the advisory lock; approving this run approves them (a rollback restores images only and never reverses a migration)."
if [ "$warn" = 1 ]; then echo; echo "**WARNING: an existing migration file was modified or deleted.**"; fi
