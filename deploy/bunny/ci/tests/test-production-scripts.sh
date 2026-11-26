#!/usr/bin/env bash
# Local tests for the production release scripts, with fakes only (no network, no Bunny, no GHCR):
#   require-staging-release.sh   fake `gh`
#   resolve-staging-images.sh    fake `docker`
#   list-migrations.sh           a throw-away repository + the fake app /healthz/
#   verify-hq.sh                 a fake HQ host (fake_hq.py)
#   bunny-deploy.sh + verify-release.sh   the fake Bunny API (a happy release and a rolled-back one)
# Run from anywhere:  deploy/bunny/ci/tests/test-production-scripts.sh
# Needs bash, jq, curl, python3 and the version-control CLI. Exit 0 = all passed.
# shellcheck disable=SC2015
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CI="$(cd "$HERE/.." && pwd)"
T="$(mktemp -d)"
PIDS=""
cleanup() { for p in $PIDS; do kill "$p" 2>/dev/null || true; wait "$p" 2>/dev/null || true; done; rm -rf "$T"; }
trap cleanup EXIT
pass=0; fail=0
ok()  { pass=$((pass + 1)); echo "ok   - $1"; }
bad() { fail=$((fail + 1)); echo "FAIL - $1"; [ -n "${2:-}" ] && echo "       $2"; }
expect_ok()   { local n="$1"; shift; if out="$("$@" 2>&1)"; then ok "$n"; else bad "$n" "$out"; fi; }
expect_fail() { local n="$1"; shift; if out="$("$@" 2>&1)"; then bad "$n (should have failed)" "$out"; else ok "$n"; fi; }
contains()    { case "$2" in *"$3"*) ok "$1";; *) bad "$1" "missing '$3' in: $2";; esac; }
lacks()       { case "$2" in *"$3"*) bad "$1" "found '$3'";; *) ok "$1";; esac; }

# ---------- stubs ----------
BIN="$T/bin"; mkdir -p "$BIN"
cat > "$BIN/gh" <<'EOF'
#!/usr/bin/env bash
# fake gh api <path> --jq <expr>: serves $FAKE_GH_JSON through jq, like the real --jq flag
expr=""; while [ $# -gt 0 ]; do [ "$1" = "--jq" ] && expr="$2"; shift; done
jq -r "$expr" "$FAKE_GH_JSON"
EOF
cat > "$BIN/docker" <<'EOF'
#!/usr/bin/env bash
# fake docker buildx imagetools inspect <ref> --format ...
ref="$4"
case " ${FAKE_MISSING:-} " in *" ${ref##*-} "*) exit 1;; esac
if [ "${FAKE_BAD_DIGEST:-}" = "${ref##*-}" ]; then echo "not-a-digest"; exit 0; fi
printf 'sha256:%064d\n' "$(printf '%s' "${ref##*-}" | cksum | cut -d' ' -f1)"
EOF
cat > "$BIN/sleep" <<'EOF'
#!/usr/bin/env bash
exec /bin/sleep 0.1
EOF
cat > "$BIN/curl" <<'EOF'
#!/usr/bin/env bash
# Sends the hard-coded Bunny API base to the fake server; everything else passes through.
args=(); for a in "$@"; do args+=("${a/https:\/\/api.bunny.net\/mc/http://127.0.0.1:$FAKE_PORT/mc}"); done
exec /usr/bin/curl "${args[@]}"
EOF
chmod +x "$BIN"/*
export PATH="$BIN:$PATH"

SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
REPO=ghcr.io/negosyoonline/negosyoonline-backend-staging

echo "== require-staging-release.sh"
cat > "$T/runs-ok.json" <<EOF
{"workflow_runs":[{"head_sha":"$SHA","event":"push","conclusion":"failure"},{"head_sha":"$SHA","event":"workflow_dispatch","conclusion":"success"},{"head_sha":"$SHA","event":"push","conclusion":"success"}]}
EOF
cat > "$T/runs-no.json" <<EOF
{"workflow_runs":[{"head_sha":"$SHA","event":"push","conclusion":"failure"},{"head_sha":"$SHA","event":"workflow_dispatch","conclusion":"success"},{"head_sha":"$SHA","event":"push","conclusion":null}]}
EOF
FAKE_GH_JSON="$T/runs-ok.json" expect_ok "a successful staging push run passes" "$CI/require-staging-release.sh" o/r "$SHA"
FAKE_GH_JSON="$T/runs-no.json" expect_fail "failed/in-progress/dispatch-only runs do not pass" "$CI/require-staging-release.sh" o/r "$SHA"
FAKE_GH_JSON="$T/runs-ok.json" expect_fail "a short sha is refused" "$CI/require-staging-release.sh" o/r abc123

echo "== resolve-staging-images.sh"
out="$(GITHUB_OUTPUT="$T/gho" GITHUB_STEP_SUMMARY="$T/ghs" "$CI/resolve-staging-images.sh" "$REPO" "$SHA" 2>&1)"; rc=$?
[ $rc -eq 0 ] && ok "all three images resolve" || bad "all three images resolve" "$out"
[ "$(grep -c '^\(api\|worker\|beat\)=sha256:[0-9]\{64\}$' "$T/gho")" = 3 ] && ok "three role=digest outputs written" || bad "outputs" "$(cat "$T/gho")"
contains "summary lists the image by tag and digest" "$(cat "$T/ghs")" "$REPO:$SHA-beat@sha256:"
FAKE_MISSING="worker" expect_fail "a missing image fails the run" "$CI/resolve-staging-images.sh" "$REPO" "$SHA"
FAKE_BAD_DIGEST="beat" expect_fail "a malformed digest fails the run" "$CI/resolve-staging-images.sh" "$REPO" "$SHA"
expect_fail "a short sha is refused (images)" "$CI/resolve-staging-images.sh" "$REPO" abc

echo "== fake Bunny + app"
python3 -I "$HERE/fake_bunny.py" "$T/port" & PIDS="$PIDS $!"
for _ in $(seq 50); do [ -s "$T/port" ] && break; /bin/sleep 0.1; done
export FAKE_PORT; FAKE_PORT="$(cat "$T/port")"
URL="http://127.0.0.1:$FAKE_PORT"

echo "== list-migrations.sh"
R="$T/repo"; mkdir -p "$R/apps/a/migrations"; cd "$R" || exit 1
git init -q . && git config user.email t@t && git config user.name t && git config commit.gpgsign false
touch apps/a/migrations/__init__.py; echo 1 > apps/a/migrations/0001_initial.py; git add -A; git commit -qm c1
C1="$(git rev-parse HEAD)"
echo 2 > apps/a/migrations/0002_more.py; echo x > apps/a/other.py; git add -A; git commit -qm c2
C2="$(git rev-parse HEAD)"
echo changed >> apps/a/migrations/0001_initial.py; git add -A; git commit -qm c3
C3="$(git rev-parse HEAD)"
# unknown production (empty url): everything listed, __init__ excluded
out="$("$CI/list-migrations.sh" "" "$C2")"
contains "unknown production lists every migration" "$out" "apps/a/migrations/0001_initial.py"
lacks "__init__.py is never listed" "$out" "__init__"
contains "says why everything is listed" "$out" "could not be read"
# production serves a commit: fake app reports the api tag's first 12 chars, so set the tag via the API
patch() { /usr/bin/curl -s -o /dev/null -X PATCH -H "AccessKey: test-key" -H 'Content-Type: application/json' -d "{\"imageTag\":\"$1\"}" "$URL/mc/apps/app-prod/containers/c-api"; }
patch "${C1:0:12}-api"
out="$("$CI/list-migrations.sh" "$URL" "$C2")"
contains "new migration since production is listed as NEW" "$out" "NEW \`apps/a/migrations/0002_more.py\`"
lacks "an already-applied migration is not listed" "$out" "0001_initial"
lacks "no warning for a clean release" "$out" "WARNING"
out="$("$CI/list-migrations.sh" "$URL" "$C3")"
contains "a modified existing migration is flagged" "$out" "WARNING: an existing migration file was modified"
patch "${C3:0:12}-api"
out="$("$CI/list-migrations.sh" "$URL" "$C3")"
contains "same commit: nothing to apply" "$out" "already runs this commit"
patch "${C2:0:12}-api"
out="$("$CI/list-migrations.sh" "$URL" "$C2")"
contains "same commit (2): nothing to apply" "$out" "already runs this commit"
patch "${C3:0:12}-api"
out="$("$CI/list-migrations.sh" "$URL" "$C2")"
contains "production ahead of the release (rollback) is explained" "$out" "not an ancestor"
lacks "a rollback lists no new migration" "$out" "NEW"
patch "deadbeef0000-api"
out="$("$CI/list-migrations.sh" "$URL" "$C2")"
contains "production commit not in history falls back to the full list" "$out" "could not be read"

echo "== plan-production.sh"
cd "$R" || exit 1
git update-ref refs/remotes/origin/main "$C3"
git update-ref refs/remotes/origin/production "$C2"
plan() { # event ref run_sha input_sha run_head_sha -> prints outputs; honours PRODUCTION_URL
  EVENT_NAME="$1" REF="$2" RUN_SHA="$3" INPUT_SHA="$4" RUN_HEAD_SHA="$5" IMAGE_BASE="ghcr.io/Negosyo/Repo-staging" \
    GITHUB_OUTPUT=/dev/stdout GITHUB_STEP_SUMMARY=/dev/null "$CI/plan-production.sh"
}
patch "feedface0000-api"   # production serves something else
export PRODUCTION_URL="$URL"
out="$(plan push refs/heads/production "$C2" "" "")"
contains "push: release of the production head" "$out" "mode=release"
contains "push: sha is the pushed commit" "$out" "sha=$C2"
contains "image repo is lower-cased" "$out" "image_repo=ghcr.io/negosyo/repo-staging"
contains "not deployed yet: no skip" "$out" "skip=false"
expect_fail "push from another branch is refused" plan push refs/heads/main "$C2" "" ""
out="$(plan workflow_run refs/heads/main "$C3" "" "$C2")"
contains "workflow_run: deploys the promoted sha, not main's tip" "$out" "sha=$C2"
contains "workflow_run: mode release" "$out" "mode=release"
expect_fail "workflow_run: a sha that is not the production head is refused" plan workflow_run refs/heads/main "$C3" "" "$C1"
expect_fail "workflow_run: a sha not on main is refused" plan workflow_run refs/heads/main "$C3" "" "0000000000000000000000000000000000000001"
expect_fail "workflow_run: missing head sha is refused" plan workflow_run refs/heads/main "$C3" "" ""
out="$(plan workflow_dispatch refs/heads/production "$C2" "$C1" "")"
contains "dispatch with an earlier production sha is a redeploy" "$out" "mode=redeploy"
expect_fail "dispatch with a sha never on production is refused" plan workflow_dispatch refs/heads/production "$C2" "$C3" ""
expect_fail "dispatch from main is refused" plan workflow_dispatch refs/heads/main "$C3" "" ""
expect_fail "another event is refused" plan pull_request refs/heads/production "$C2" "" ""
patch "${C2:0:12}-api"   # production already serves the release
out="$(plan workflow_run refs/heads/main "$C3" "" "$C2")"
contains "second trigger for the same release: skip=true" "$out" "skip=true"
out="$(plan push refs/heads/production "$C2" "" "")"
contains "push after workflow_run: skip=true too" "$out" "skip=true"
out="$(PRODUCTION_URL="" plan push refs/heads/production "$C2" "" "")"
contains "no production URL: never skips" "$out" "skip=false"
unset PRODUCTION_URL
cd "$HERE" || exit 1

echo "== dry walk-through of the deploy job against the fake Bunny API"
export BUNNY_API_KEY=test-key BUNNY_APP_ID=app-prod BUNNY_CONTAINER_API=c-api BUNNY_CONTAINER_WORKER=worker BUNNY_CONTAINER_BEAT=c-beat IMAGE_REPO="$REPO"
D="$CI/bunny-deploy.sh"
# reset the fake to a known "previous release" (tag only; the repo name stays the pilot one)
for r in api worker beat; do
  cid="c-$r"
  /usr/bin/curl -s -o /dev/null -X PATCH -H "AccessKey: test-key" -H 'Content-Type: application/json' -d "{\"imageTag\":\"1111111111111111111111111111111111111111-$r\",\"imageDigest\":\"sha256:$(printf '1%.0s' $(seq 64))\"}" "$URL/mc/apps/app-prod/containers/$cid"
done
NEW=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
DG="sha256:$(printf 'b%.0s' $(seq 64))"
out="$($D snapshot "$T/prev.json" 2>&1)"; rc=$?
[ $rc -eq 0 ] && ok "snapshot records the previous images" || bad "snapshot" "$out"
[ "$(jq length "$T/prev.json")" = 3 ] && ok "snapshot holds exactly api, worker, beat" || bad "snapshot size"
lacks "snapshot never prints container env" "$out" "SECRET-SHOULD-NEVER-PRINT"
out="$($D apply "$NEW" "$DG" "$DG" "$DG" 2>&1)"; rc=$?
[ $rc -eq 0 ] && ok "apply patches the three containers" || bad "apply" "$out"
lacks "apply never prints container env" "$out" "SECRET-SHOULD-NEVER-PRINT"
log="$(/usr/bin/curl -s "$URL/_ctl/log")"
contains "only the three container ids were patched (api)" "$log" "containers/c-api"
contains "(worker)" "$log" "containers/c-worker"
contains "(beat)" "$log" "containers/c-beat"
lacks "postgres was not touched" "$log" "c-pg"
lacks "redis was not touched" "$log" "c-redis"
lacks "only image fields are sent" "$log" '"env"'
expect_ok "verify passes on a good release" "$CI/verify-release.sh" "$URL" "$NEW" 30
# a bad release: readiness fails -> verify fails -> restore previous
/usr/bin/curl -s -o /dev/null -X POST "$URL/_ctl/broken"
expect_fail "verify fails when /readyz/ reports beat down" "$CI/verify-release.sh" "$URL" "$NEW" 2
out="$($D restore "$T/prev.json" 2>&1)"; rc=$?
[ $rc -eq 0 ] && ok "restore puts the previous images back" || bad "restore" "$out"
/usr/bin/curl -s -o /dev/null -X POST "$URL/_ctl/fixed"
expect_ok "rollback verifies as the previous commit" "$CI/verify-release.sh" "$URL" "1111111111111111111111111111111111111111" 30
expect_fail "wrong BUNNY_API_KEY is refused" env BUNNY_API_KEY=wrong "$D" snapshot "$T/x.json"

echo "== data-app guard (ZD-1): the data app is never written to; a volume on a target container is refused"
export BUNNY_DATA_APP_ID=app-data
log0="$(/usr/bin/curl -s "$URL/_ctl/log" | jq 'length')"
# the release still works with the data app configured
GOOD=cccccccccccccccccccccccccccccccccccccccc
DG2="sha256:$(printf 'c%.0s' $(seq 64))"
out="$($D apply "$GOOD" "$DG2" "$DG2" "$DG2" 2>&1)"; rc=$?
[ $rc -eq 0 ] && ok "apply works with BUNNY_DATA_APP_ID set" || bad "apply with data app id set" "$out"
log="$(/usr/bin/curl -s "$URL/_ctl/log")"
lacks "the data app was never patched or read by a release" "$log" "app-data"
lacks "the data containers were never patched" "$log" "d-pg"
# the same id for the app and the data app is refused before any call
before="$(/usr/bin/curl -s "$URL/_ctl/log" | jq 'length')"
expect_fail "BUNNY_APP_ID equal to BUNNY_DATA_APP_ID is refused (snapshot)" env BUNNY_APP_ID=app-data BUNNY_DATA_APP_ID=app-data "$D" snapshot "$T/y.json"
expect_fail "BUNNY_APP_ID equal to BUNNY_DATA_APP_ID is refused (apply)" env BUNNY_APP_ID=app-data BUNNY_DATA_APP_ID=app-data "$D" apply "$GOOD" "$DG2" "$DG2" "$DG2"
[ "$(/usr/bin/curl -s "$URL/_ctl/log" | jq 'length')" = "$before" ] && ok "no Bunny call was made by the refused runs" || bad "a refused run still called Bunny"
# pointing the release at the data app by mistake: the containers are not found, nothing is written
out="$(env BUNNY_APP_ID=app-data "$D" apply "$GOOD" "$DG2" "$DG2" "$DG2" 2>&1)"; rc=$?
[ $rc -ne 0 ] && ok "a release aimed at the data app (api/worker/beat not there) fails" || bad "release aimed at the data app passed" "$out"
lacks "...and wrote nothing to it" "$(/usr/bin/curl -s "$URL/_ctl/log")" "DATA APP"
# a volume on a target container refuses the release before it patches anything
/usr/bin/curl -s -o /dev/null -X POST "$URL/_ctl/volume-on"
before="$(/usr/bin/curl -s "$URL/_ctl/log" | jq 'length')"
out="$($D apply "$GOOD" "$DG2" "$DG2" "$DG2" 2>&1)"; rc=$?
[ $rc -ne 0 ] && ok "a target container with a volume fails the deploy" || bad "volume on api did not fail the deploy" "$out"
contains "...with the reason" "$out" "has a volume"
after="$(/usr/bin/curl -s "$URL/_ctl/log" | jq '[.[] | select(startswith("PATCH"))] | length')"
patched_before="$(/usr/bin/curl -s "$URL/_ctl/log" | jq '[.[0:'"$before"'][] | select(startswith("PATCH"))] | length')"
[ "$after" = "$patched_before" ] && ok "...and nothing was patched" || bad "a patch went out with a volume present"
expect_fail "snapshot refuses it too" "$D" snapshot "$T/z.json"
/usr/bin/curl -s -o /dev/null -X POST "$URL/_ctl/volume-off"
expect_ok "without the volume the snapshot passes again" "$D" snapshot "$T/z.json"
unset BUNNY_DATA_APP_ID

echo "== honest restore, bounded retry (5xx / transport), no bodies"
export BUNNY_RETRY_SLEEP="0 0"
fault()  { /usr/bin/curl -s -o /dev/null -X POST "$URL/_ctl/fail?target=$1&status=$2&count=$3"; }
nofault() { /usr/bin/curl -s -o /dev/null -X POST "$URL/_ctl/reset-faults"; }
attempts() { /usr/bin/curl -s "$URL/_ctl/log" | jq --arg t "$1" '[.[] | select(startswith("ATTEMPT " + $t + " "))] | length'; }
reports() { /usr/bin/curl -s -H "AccessKey: test-key" "$URL/mc/apps/app-prod" | jq -r --arg id "$1" '.containerTemplates[] | select(.id==$id) | .imageTag'; }
OLD=dddddddddddddddddddddddddddddddddddddddd
NEW2=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
DG3="sha256:$(printf 'e%.0s' $(seq 64))"
PREV_DG="sha256:$(printf 'd%.0s' $(seq 64))"
$D apply "$OLD" "$PREV_DG" "$PREV_DG" "$PREV_DG" >/dev/null 2>&1
$D snapshot "$T/prev2.json" >/dev/null 2>&1
$D apply "$NEW2" "$DG3" "$DG3" "$DG3" >/dev/null 2>&1

# a 500 on one container during restore (persistent): that role FAILED, the others restored, exit non-zero, read-back shows it
nofault; fault c-api 500 -1
out="$($D restore "$T/prev2.json" 2>&1)"; rc=$?
[ $rc -ne 0 ] && ok "restore exits non-zero when one container cannot be restored" || bad "restore exit code on a 500" "$out"
contains "the failed role is reported FAILED" "$out" "FAILED to restore api (wanted $OLD-api)"
lacks "the failed role is not reported restored" "$out" "restored api"
contains "worker is still restored" "$out" "restored worker -> $OLD-worker"
contains "beat is still restored" "$out" "restored beat -> $OLD-beat"
contains "read-back shows the stale api tag" "$out" "api now reports $NEW2-api"
contains "read-back shows the restored worker tag" "$out" "worker now reports $OLD-worker"
contains "read-back flags the mismatch" "$out" "MISMATCH api"
[ "$(attempts c-api)" = 3 ] && ok "persistent 500: exactly 3 attempts" || bad "persistent 500 attempts" "got $(attempts c-api)"
lacks "no response body in the output" "$out" "SECRET-SHOULD-NEVER-PRINT"
lacks "no response body field in the output" "$out" "injected fault"
lacks "the key is not in the output" "$out" "test-key"
nofault

# a 500 once, then 2xx: the retry succeeds and restore is fully honest and green
$D apply "$NEW2" "$DG3" "$DG3" "$DG3" >/dev/null 2>&1
fault c-beat 500 1
out="$($D restore "$T/prev2.json" 2>&1)"; rc=$?
[ $rc -eq 0 ] && ok "a 500 once then 2xx: restore succeeds after the retry" || bad "restore with one transient 500" "$out"
contains "...the retry is shown as a status line" "$out" "HTTP 500 (attempt 1 of 3), retrying"
contains "...and the role is restored" "$out" "restored beat -> $OLD-beat"
contains "...and the read-back agrees" "$out" "beat now reports $OLD-beat"
[ "$(attempts c-beat)" = 1 ] && ok "...only one attempt failed" || bad "transient attempts" "got $(attempts c-beat)"
lacks "...no body in the output" "$out" "SECRET-SHOULD-NEVER-PRINT"
nofault

# apply with a transient 500 on a GET of the app
fault app 502 1
out="$($D apply "$NEW2" "$DG3" "$DG3" "$DG3" 2>&1)"; rc=$?
[ $rc -eq 0 ] && ok "a transient 502 on GET is retried (apply)" || bad "apply with transient GET 502" "$out"
[ "$(reports c-api)" = "$NEW2-api" ] && ok "...and the result is correct" || bad "api tag after apply"
nofault

# persistent 500 on apply: exactly 3 attempts, then failure
fault c-worker 500 -1
out="$($D apply "$OLD" "$PREV_DG" "$PREV_DG" "$PREV_DG" 2>&1)"; rc=$?
[ $rc -ne 0 ] && ok "apply fails on a persistent 500" || bad "apply on persistent 500" "$out"
[ "$(attempts c-worker)" = 3 ] && ok "...after exactly 3 attempts" || bad "apply attempts" "got $(attempts c-worker)"
lacks "...with no body in the output" "$out" "SECRET-SHOULD-NEVER-PRINT"
nofault

# a 4xx is never retried
fault c-api 404 -1
out="$($D apply "$OLD" "$PREV_DG" "$PREV_DG" "$PREV_DG" 2>&1)"; rc=$?
[ $rc -ne 0 ] && ok "apply fails on a 404" || bad "apply on 404" "$out"
[ "$(attempts c-api)" = 1 ] && ok "a 4xx is not retried (1 attempt)" || bad "4xx attempts" "got $(attempts c-api)"
nofault
fault c-api 400 -1
$D apply "$OLD" "$PREV_DG" "$PREV_DG" "$PREV_DG" >/dev/null 2>&1
[ "$(attempts c-api)" = 1 ] && ok "a 400 is not retried either" || bad "400 attempts" "got $(attempts c-api)"
nofault

# transport error: nothing listens on the port; 3 attempts, no hang, no key printed
out="$(BUNNY_RETRY_SLEEP="0 0" FAKE_PORT=1 $D snapshot "$T/t.json" 2>&1)"; rc=$?
[ $rc -ne 0 ] && ok "a transport error fails after the retries" || bad "transport error passed" "$out"
contains "...3 attempts were made" "$out" "attempt 3 of 3), giving up"
lacks "...the key is not printed" "$out" "test-key"
unset BUNNY_RETRY_SLEEP
$D apply "$OLD" "$PREV_DG" "$PREV_DG" "$PREV_DG" >/dev/null 2>&1

echo "== static: only bunny-deploy.sh writes to Bunny; workflows hold no Bunny write"
ROOT="$(cd "$CI/../../.." && pwd)"
hits="$(grep -rIl 'api\.bunny\.net' "$ROOT/.github" "$ROOT/deploy/bunny/ci" "$ROOT/scripts" 2>/dev/null | grep -v '/tests/' | sed "s#$ROOT/##" | sort | tr '\n' ' ')"
[ "$hits" = "deploy/bunny/ci/bunny-deploy.sh " ] && ok "only deploy/bunny/ci/bunny-deploy.sh names the Bunny API" || bad "other files name the Bunny API" "$hits"
if grep -rEn 'curl[^|]*(-X|--request)[ =]*(PATCH|POST|PUT|DELETE)' "$ROOT/.github/workflows" >/dev/null 2>&1; then bad "a workflow sends a write with curl"; else ok "no workflow sends a write with curl"; fi
for wf in bunny-staging bunny-production; do
  uses="$(grep -n 'BUNNY_DATA_APP_ID' "$ROOT/.github/workflows/$wf.yml" | grep -v 'BUNNY_DATA_APP_ID: \${{ vars.BUNNY_DATA_APP_ID }}' | grep -v '^[0-9]*: *#')"
  [ -z "$uses" ] && ok "$wf.yml only passes BUNNY_DATA_APP_ID through to the guard" || bad "$wf.yml uses BUNNY_DATA_APP_ID elsewhere" "$uses"
  grep -q 'BUNNY_DATA_APP_ID: ${{ vars.BUNNY_DATA_APP_ID }}' "$ROOT/.github/workflows/$wf.yml" && ok "$wf.yml passes BUNNY_DATA_APP_ID to the deploy job" || bad "$wf.yml does not pass BUNNY_DATA_APP_ID"
  grep -Eq 'bunny-deploy\.sh (snapshot|apply|restore)' "$ROOT/.github/workflows/$wf.yml" && ok "$wf.yml changes Bunny only through bunny-deploy.sh" || bad "$wf.yml does not call bunny-deploy.sh"
done
for f in "$CI"/*.sh; do
  [ "$(basename "$f")" = bunny-deploy.sh ] && continue
  if grep -Eq -- '--request|-X ' "$f"; then bad "$(basename "$f") sends a request method"; else ok "$(basename "$f") sends no write"; fi
done

echo "== verify-hq.sh (the HQ host has no /healthz/: 303 to /sign-in/, 3 polls in a row)"
python3 -I "$HERE/fake_hq.py" "$T/hqport" & PIDS="$PIDS $!"
for _ in $(seq 50); do [ -s "$T/hqport" ] && break; /bin/sleep 0.1; done
HQ="http://127.0.0.1:$(cat "$T/hqport")"
expect_ok   "HQ: 303 to /sign-in/ passes" "$CI/verify-hq.sh" "$HQ/ok" 30
expect_fail "HQ: 404 fails" "$CI/verify-hq.sh" "$HQ/miss" 2
expect_fail "HQ: 200 fails" "$CI/verify-hq.sh" "$HQ/fine" 2
expect_fail "HQ: 303 to another path fails" "$CI/verify-hq.sh" "$HQ/other" 2
expect_fail "HQ: an interrupted streak never reaches 3" "$CI/verify-hq.sh" "$HQ/flaky" 2
expect_fail "HQ: a missing argument is a usage error" "$CI/verify-hq.sh"
out="$("$CI/verify-hq.sh" "$HQ/ok" 30 2>&1)"
lacks "HQ: output has no host" "$out" "127.0.0.1"
lacks "HQ: output has no query string" "$out" "secret"

echo
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
