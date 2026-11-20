# Bunny staging release (CI/CD)

Task z8uq9mh7dt. Bunny Magic Containers app `negosyoonline-backend-staging` is the staging release target. Railway is no longer the release target (it stays running until Coach Ed retires it). Production on Bunny is not provisioned and is not covered here.

## Release flow

Every push to `main` runs `.github/workflows/bunny-staging.yml`:

1. **plan**: the commit SHA (full 40 characters).
2. **checks**: `ci.yml` for that exact commit ("Django checks and tests", "PostgreSQL and Redis configuration").
3. **images**: three private images from `deploy/bunny/Dockerfile`, `ghcr.io/<owner>/<repo>-staging:<full-sha>-api|-worker|-beat` (never `latest`), smoke-checked with `manage.py check`, digests recorded in the run summary. `APP_COMMIT_SHA=<sha>` is baked into each image.
4. **deploy** (GitHub Environment `bunny-staging`; the owner adds required reviewers there, who approve each release): snapshot of the images now running (artifact `bunny-staging-previous-images`, run summary), then one `PATCH` per container (api, worker, beat) setting only the image tag and digest; then verification; then automatic rollback of the three containers if anything after the snapshot fails.
5. **verify**: `/healthz/` must report `commit` = first 12 characters of the SHA (3 polls in a row), then `/readyz/` must answer 200 with every check true, beat included. Same for `BUNNY_HQ_URL` when set.

A failed step fails the run, which turns the main commit red. The workflow never touches the postgres or redis containers, volumes, env, endpoints or instance count (scripts: `deploy/bunny/ci/`). Releases are serialised (`concurrency`, never cancelled mid-way); if two merges queue, the older queued one is skipped and the newest wins.

## Settings (names only; no values in the repo)

Environment `bunny-staging`:

| Kind | Name | Use |
| --- | --- | --- |
| secret | `BUNNY_API_KEY` | Bunny account API key (`AccessKey` header). Account key; Bunny has no narrower scope. Never printed. |
| variable | `BUNNY_APP_ID` | Magic Containers app id |
| variable | `BUNNY_CONTAINER_API`, `BUNNY_CONTAINER_WORKER`, `BUNNY_CONTAINER_BEAT` | container id or name of the three app containers |
| variable | `BUNNY_STAGING_URL` | API base URL checked by verify (before DNS swap `https://api-bunny-staging.negosyonline.com`; after, `https://api-staging.negosyonline.com`) |
| variable | `BUNNY_HQ_URL` | optional; HQ API base URL, also verified |

Repository variable `BUNNY_STAGING_URL` (optional, same value) is read by the drift check. GHCR login uses the workflow's own `GITHUB_TOKEN` (`packages: write` on the images job only). The Bunny app must already hold a registry credential that can pull the private `<repo>-staging` package.

Bunny containers must keep `RAILWAY_ENVIRONMENT_NAME=staging` (read by Xendit key saving and staff test-order deletion; unchanged on purpose). `RAILWAY_GIT_COMMIT_SHA` on Bunny is now ignored because the image's `APP_COMMIT_SHA` wins (chain: `APP_COMMIT_SHA`, `RAILWAY_GIT_COMMIT_SHA`, `unknown`; used by `/healthz/` and Sentry's release).

## Migrations and the lock

Bunny has no one-off job API, so the **api image's entrypoint** (`init-api.sh`) runs `migrate.sh`, then `start-api.sh` (which never migrates). `migrate.sh` takes a PostgreSQL session advisory lock (fixed key, waits up to 600 s), runs `migrate --noinput`, then `migrate --check`, and exits non-zero on any failure (the container then restarts and retries; a crashed run frees the lock with its connection). Repeats and concurrent starts are safe: later runs find nothing to apply. The worker and beat start scripts wait (up to 15 min) until `migrate --check` passes, so they never run new code on the old schema. `MIGRATE_ON_START=0` skips migration.

**Migrations must be backward-compatible** with the previous release (add before use, remove one release after the code stops using it): rollback restores images only and never reverses a migration or touches data.

## Verify a release by hand

```
curl -s https://api-bunny-staging.negosyonline.com/healthz/        # {"status":"ok","commit":"<first 12 of sha>"}
curl -s https://api-bunny-staging.negosyonline.com/readyz/         # 200, every value true, "beat" included
git rev-parse --short=12 origin/main                               # must equal the commit above
```

Evidence of a release: the green `Bunny staging release` run for the SHA (summary lists digests and the previous images). **Drift:** `bunny-staging-drift.yml` runs hourly and goes red when Bunny's commit is not main's HEAD and no release run is in progress; Actions > "Bunny staging release" shows every main commit's result.

## Rollback

Automatic: a failure after the snapshot restores the previous tags and digests of the three containers. Manual (to any earlier commit on main whose images exist): Actions > Bunny staging release > Run workflow, `sha` = the full SHA. That skips checks and build, redeploys those images to the three containers and verifies. The previous images of any release are in its `bunny-staging-previous-images` artifact. The database is never rolled back.

## First deploy (Coach Ed approves; not yet run)

1. Create the `bunny-staging` environment with the secret and variables above; add required reviewers.
2. Record the current image names and tags of the three containers (Bunny dashboard) for a manual fallback.
3. Merge this branch through the normal release train. The run builds and publishes images; on approval it switches the three containers from the pilot image to `<repo>-staging:<sha>-<role>` (the first PATCH also sets image namespace/name), the api container migrates under the lock, and verify must pass.
4. Confirm `/healthz/` commit equals main, `/readyz/` all true, the worker/beat heartbeat (`beat` check), and the Postgres and Redis containers and volume unchanged. After the DNS swap, change `BUNNY_STAGING_URL`.
5. Unverified until then (no Bunny access was used): the PATCH fields accepted for a private GHCR image, that a PATCH starts a rolling rollout by itself, and the pull credential for the new package name.

## Bunny API used

Base `https://api.bunny.net/mc`, header `AccessKey`; spec `https://api-mc.opsbunny.net/docs/public/swagger.json`, docs `https://bunny.net/docs/magic-containers/api-reference`:

- `GET /apps/{appId}`: list container templates, read current image fields.
- `PATCH /apps/{appId}/containers/{containerId}` (Patch Container Template): `imageTag`, `imageDigest`, and `imageNamespace`/`imageName` only when they differ.

The temporary SMS-token activation startup command is not part of these images: the entrypoints above are the only startup logic, and none touches device tokens.
