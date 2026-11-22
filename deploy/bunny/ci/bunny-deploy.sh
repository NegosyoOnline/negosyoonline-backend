#!/usr/bin/env bash
# Bunny Magic Containers deploy helper (staging). Used only by .github/workflows/bunny-staging.yml.
#
#   bunny-deploy.sh snapshot <previous.json>                          record the current images of api/worker/beat
#   bunny-deploy.sh apply <sha> <api-dig> <worker-dig> <beat-dig>     point the 3 containers at <sha>
#   bunny-deploy.sh restore <previous.json>                           point the 3 containers back at the snapshot
#
# Touches ONLY imageNamespace / imageName / imageTag / imageDigest of the api, worker and beat
# container templates (PATCH /apps/{appId}/containers/{containerId}). Never touches the postgres or
# redis containers, volumes, env, endpoints or the instance count. Every call must return 2xx.
#
# Data-app guard (ZD-1): Postgres and Redis live in their own Bunny app (BUNNY_DATA_APP_ID) that a
# release never changes. Three refusals, all before any write:
#   1. BUNNY_DATA_APP_ID equal to BUNNY_APP_ID (the release target would be the data app);
#   2. any request other than GET whose path is not exactly /apps/$BUNNY_APP_ID/containers/<id>
#      (so no other app, endpoint, volume or setting can be written), or that names the data app;
#   3. a target container (api, worker, beat) that has a volume: those would stop the pod and
#      cause downtime, so they are not allowed to sit on the stateless app.
# Secrets: BUNNY_API_KEY stays in a 0600 file; no response body is ever printed (Bunny returns the
# container env in them), only a status code and a short error message.
#
# Env: BUNNY_API_KEY BUNNY_APP_ID BUNNY_CONTAINER_API BUNNY_CONTAINER_WORKER BUNNY_CONTAINER_BEAT
#      IMAGE_REPO (e.g. ghcr.io/negosyoonline/negosyoonline-backend-staging)
#      BUNNY_DATA_APP_ID (optional until the data app exists; never written to)
set -euo pipefail

BASE="https://api.bunny.net/mc"
ROLES="api worker beat"
umask 077
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
: "${BUNNY_API_KEY:?}" "${BUNNY_APP_ID:?}" "${IMAGE_REPO:?}"
printf 'header = "AccessKey: %s"\n' "$BUNNY_API_KEY" > "$WORK/curl.cfg"
DATA_APP="${BUNNY_DATA_APP_ID:-}"
if [ -n "$DATA_APP" ] && [ "$DATA_APP" = "$BUNNY_APP_ID" ]; then
  echo "refused: BUNNY_DATA_APP_ID is the same as BUNNY_APP_ID (a release must never target the data app)" >&2
  exit 1
fi

# call METHOD PATH [JSON_FILE] -> response in $WORK/resp.json; fails on non-2xx without printing the body.
call() {
  local method="$1" path="$2" body="${3:-}" code
  if [ "$method" != GET ]; then
    case "$path" in
      /apps/"$BUNNY_APP_ID"/containers/*/*|/apps/"$BUNNY_APP_ID"/containers/) echo "refused: $method is allowed only on /apps/<app>/containers/<id>" >&2; return 1 ;;
      /apps/"$BUNNY_APP_ID"/containers/?*) ;;
      *) echo "refused: $method is allowed only on /apps/<app>/containers/<id>" >&2; return 1 ;;
    esac
    if [ -n "$DATA_APP" ]; then
      case "$path" in *"$DATA_APP"*) echo "refused: the data app is never written to" >&2; return 1 ;; esac
    fi
  fi
  local args=()
  if [ -n "$body" ]; then args=(--header "Content-Type: application/json" --data "@$body"); fi
  code="$(curl --silent --show-error --max-time 60 --config "$WORK/curl.cfg" --request "$method" \
    --output "$WORK/resp.json" --write-out '%{http_code}' ${args[@]+"${args[@]}"} "$BASE$path")"
  case "$code" in
    2??) ;;
    *) echo "Bunny API $method $path -> HTTP $code: $(jq -r '(.message // .title // .detail // "no message")' "$WORK/resp.json" 2>/dev/null | head -c 300)" >&2
       return 1 ;;
  esac
}

fetch_app() { call GET "/apps/$BUNNY_APP_ID"; cp "$WORK/resp.json" "$WORK/app.json"; }

# role -> container id (the variable may hold the id or the name)
cid() {
  local role="$1" var ref
  case "$role" in api) var="$BUNNY_CONTAINER_API";; worker) var="$BUNNY_CONTAINER_WORKER";; beat) var="$BUNNY_CONTAINER_BEAT";; esac
  ref="$(jq -r --arg v "$var" '[.containerTemplates[] | select(.id==$v or .name==$v) | .id] | if length==1 then .[0] else empty end' "$WORK/app.json")"
  [ -n "$ref" ] || { echo "container for role $role not found (or ambiguous) in the app" >&2; return 1; }
  # Bunny container templates carry `volumeMounts` (array of {name, mountPath}); anything but an empty
  # (or absent) array means a stateful container, which does not belong on the release app.
  if jq -e --arg id "$ref" '.containerTemplates[] | select(.id==$id)
        | (.volumeMounts // []) | if type == "array" then length > 0 else true end' "$WORK/app.json" >/dev/null; then
    echo "refused: the $role container has a volume; the release app must be stateless (data lives in the data app)" >&2
    return 1
  fi
  printf '%s' "$ref"
}

current() { jq -r --arg id "$1" --arg f "$2" '.containerTemplates[] | select(.id==$id) | .[$f] // ""' "$WORK/app.json"; }

# patch_image ID NAMESPACE NAME TAG DIGEST: namespace/name are sent only when they differ from the current ones.
patch_image() {
  local id="$1" ns="$2" name="$3" tag="$4" dig="$5"
  jq -n --arg tag "$tag" --arg dig "$dig" --arg ns "$ns" --arg name "$name" \
        --arg cns "$(current "$id" imageNamespace)" --arg cname "$(current "$id" imageName)" '
    {imageTag: $tag}
    + (if $dig != "" then {imageDigest: $dig} else {} end)
    + (if $ns != "" and $ns != $cns then {imageNamespace: $ns} else {} end)
    + (if $name != "" and $name != $cname then {imageName: $name} else {} end)' > "$WORK/body.json"
  call PATCH "/apps/$BUNNY_APP_ID/containers/$id" "$WORK/body.json"
}

readback() { # ID TAG: the app must report the tag we set
  local got
  got="$(current "$1" imageTag)"
  [ "$got" = "$2" ] || { echo "container $1 reports tag '$got', expected '$2'" >&2; return 1; }
}

repo_ns()   { local p="${IMAGE_REPO#*/}"; printf '%s' "${p%/*}"; }
repo_name() { printf '%s' "${IMAGE_REPO##*/}"; }

case "${1:-}" in
  snapshot)
    out="$2"; fetch_app
    for r in $ROLES; do
      id="$(cid "$r")"
      jq -n --arg role "$r" --arg id "$id" --arg ns "$(current "$id" imageNamespace)" --arg name "$(current "$id" imageName)" \
            --arg tag "$(current "$id" imageTag)" --arg dig "$(current "$id" imageDigest)" \
            '{role:$role,id:$id,imageNamespace:$ns,imageName:$name,imageTag:$tag,imageDigest:$dig}'
    done | jq -s '.' > "$out"
    echo "snapshot: $(jq -r '[.[] | "\(.role)=\(.imageName):\(.imageTag)"] | join(" ")' "$out")"
    ;;
  apply)
    sha="$2"; fetch_app
    for r in $ROLES; do
      case "$r" in api) d="$3";; worker) d="$4";; beat) d="$5";; esac
      id="$(cid "$r")"
      patch_image "$id" "$(repo_ns)" "$(repo_name)" "$sha-$r" "$d"
      echo "patched $r -> $sha-$r"
      fetch_app
    done
    for r in $ROLES; do readback "$(cid "$r")" "$sha-$r"; done
    echo "apply: all three containers report $sha"
    ;;
  restore)
    prev="$2"; fetch_app; rc=0
    for r in $ROLES; do
      id="$(cid "$r")"
      row="$(jq -c --arg r "$r" '.[] | select(.role==$r)' "$prev")"
      patch_image "$id" "$(jq -r .imageNamespace <<<"$row")" "$(jq -r .imageName <<<"$row")" \
        "$(jq -r .imageTag <<<"$row")" "$(jq -r .imageDigest <<<"$row")" || rc=1
      echo "restored $r -> $(jq -r .imageTag <<<"$row")"
      fetch_app || rc=1
    done
    exit "$rc"
    ;;
  *) echo "usage: $0 snapshot|apply|restore ..." >&2; exit 2 ;;
esac
