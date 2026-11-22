# Bunny staging release (CI/CD)

Task z8uq9mh7dt. Bunny Magic Containers app `negosyoonline-backend-staging` is the staging release target. Railway is no longer the release target (it stays running until Coach Ed retires it). Production on Bunny is not provisioned and is not covered here.

## Release flow

Every push to `main` runs `.github/workflows/bunny-staging.yml`:

1. **plan**: the commit SHA (full 40 characters).
2. **checks**: `ci.yml` for that exact commit ("Django checks and tests", "PostgreSQL and Redis configuration").
3. **images**: three private images from `deploy/bunny/Dockerfile`, `ghcr.io/<owner>/<repo>-staging:<full-sha>-api|-worker|-beat` (never `latest`), smoke-checked with `manage.py check`, digests recorded in the run summary. `APP_COMMIT_SHA=<sha>` is baked into each image.
4. **deploy** (GitHub Environment `bunny-staging`; the owner adds required reviewers there, who approve each release): snapshot of the images now running (artifact `bunny-staging-previous-images`, run summary), then one `PATCH` per container (api, worker, beat) setting only the image tag and digest; then verification; then automatic rollback of the three containers if anything after the snapshot fails.
5. **verify**: `/healthz/` must report `commit` = first 12 characters of the SHA (3 polls in a row), then `/readyz/` must answer 200 with every check true, beat included. Same for `BUNNY_HQ_URL` when set.

The separate `Verify staging deploy` workflow (`verify-staging.yml`) starts on `workflow_run` when this release completes on main, not on push (the release takes 16-30 minutes). If the release did not conclude `success` it fails and names the conclusion, except that a `cancelled` or `skipped` release whose SHA is no longer the main head (a queued release replaced by a newer one) ends the job successfully with a notice, because the newer release's own verify run covers it; on success it checks out the release's `head_sha` and runs `scripts/verify_deploy.sh` against `https://api-staging.negosyonline.com/healthz/` for the first 12 characters of that SHA from outside, as a margin after the release's own verify. A newer release cancels an older verify run (cancelled, not failed). Manual redeploys (`workflow_dispatch`) are skipped there because they verify themselves.

A failed step fails the run, which turns the main commit red. The workflow never touches the postgres or redis containers, volumes, env, endpoints or instance count (scripts: `deploy/bunny/ci/`). Releases are serialised (`concurrency`, never cancelled mid-way); if two merges queue, the older queued one is skipped and the newest wins.

## Settings (names only; no values in the repo)

Environment `bunny-staging`:

| Kind | Name | Use |
| --- | --- | --- |
| secret | `BUNNY_API_KEY` | Bunny account API key (`AccessKey` header). Account key; Bunny has no narrower scope. Never printed. |
| variable | `BUNNY_APP_ID` | Magic Containers app id |
| variable | `BUNNY_CONTAINER_API`, `BUNNY_CONTAINER_WORKER`, `BUNNY_CONTAINER_BEAT` | container id or name of the three app containers |
| variable | `BUNNY_STAGING_URL` | API base URL checked by verify: `https://api-staging.negosyonline.com`. The `api-bunny-staging` names were used only before the 2026-11-20 DNS swap and are removed; the direct Bunny route is `https://mc-4x9f086sdc.bunny.run` |
| variable | `BUNNY_HQ_URL` | optional; HQ API base URL, also verified |

Repository variable `BUNNY_STAGING_URL` (optional, same value) is read by the drift check. GHCR login uses the workflow's own `GITHUB_TOKEN` (`packages: write` on the images job only). The Bunny app must already hold a registry credential that can pull the private `<repo>-staging` package.

Bunny containers must keep `RAILWAY_ENVIRONMENT_NAME=staging` (read by Xendit key saving and staff test-order deletion; unchanged on purpose). `RAILWAY_GIT_COMMIT_SHA` on Bunny is now ignored because the image's `APP_COMMIT_SHA` wins (chain: `APP_COMMIT_SHA`, `RAILWAY_GIT_COMMIT_SHA`, `unknown`; used by `/healthz/` and Sentry's release).

## Migrations and the lock

Bunny has no one-off job API, so the **api image's entrypoint** (`init-api.sh`) runs `migrate.sh`, then `start-api.sh` (which never migrates). `migrate.sh` takes a PostgreSQL session advisory lock (fixed key, waits up to 600 s), runs `migrate --noinput`, then `migrate --check`, and exits non-zero on any failure (the container then restarts and retries; a crashed run frees the lock with its connection). Repeats and concurrent starts are safe: later runs find nothing to apply. The worker and beat start scripts wait (up to 15 min) until `migrate --check` passes, so they never run new code on the old schema. `MIGRATE_ON_START=0` skips migration.

**Migrations must be backward-compatible** with the previous release (add before use, remove one release after the code stops using it): rollback restores images only and never reverses a migration or touches data.

## Verify a release by hand

```
curl -s https://api-staging.negosyonline.com/healthz/        # {"status":"ok","commit":"<first 12 of sha>"}
curl -s https://api-staging.negosyonline.com/readyz/         # 200, every value true, "beat" included
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

## Layout with a separate data app (ZD-1)

Task z8uq9mhgmy; plan `gates-zero-downtime-plan-2026-11-21.md` (Z1), approved by Coach Ed on 2026-11-22. The repository part is built here. The Bunny cutover is a separate, announced step done by the Supervisor on the Manager's GO. Until it is done, staging still runs as one app, and the sections above describe it.

Why: Bunny stops the whole pod of an app that has volumes before it starts the new one, so a release that only changed api, worker and beat also restarted Postgres and Redis (a 1 to 3 minute 503). Postgres and Redis now live in their own app that a release never touches. The release app has no volume, so Bunny starts the new pod, waits until it is healthy and then removes the old one.

| App | Containers | Volumes | Changed by |
| --- | --- | --- | --- |
| `negosyoonline-data-staging` (data app) | postgres (18), redis (7) | the Postgres volume, optionally a Redis volume | never by a release; only by a version or settings change, in a quiet window |
| `negosyoonline-backend-staging` (release app) | api, worker, beat | none | every release (`bunny-staging.yml`) |

Guards, all tested by `deploy/bunny/ci/tests/test-production-scripts.sh`: `bunny-deploy.sh` is the only thing that writes to Bunny, and it writes only `PATCH /apps/<BUNNY_APP_ID>/containers/<id>` for api, worker and beat. It refuses when `BUNNY_DATA_APP_ID` equals `BUNNY_APP_ID`, when a write path names the data app, and when a target container has a non-empty `volumeMounts`. The workflows only pass the `BUNNY_DATA_APP_ID` variable through to that guard. Add it as a variable on `bunny-staging` (later `bunny-production`) when the data app is created; it is optional until then.

### Data app

Images are built by `.github/workflows/bunny-data-images.yml` into the private GHCR as `<repo>-data-postgres:<sha>` and `<repo>-data-redis:<sha>`, with the digests in the run summary. The workflow never deploys, and it first runs `deploy/bunny/data/tests/tls-check.sh`.

- **Postgres** (`deploy/bunny/data/Dockerfile.postgres`): TLS 1.2 or newer with a self-signed certificate made on first start and kept in `/var/lib/postgresql/tls`. Mount the volume at `/var/lib/postgresql` so the data and the certificate both persist. `password_encryption=scram-sha-256`. `pg_hba.conf` is generated at start with `hostssl ... scram-sha-256` lines and `hostnossl ... reject`, never a plain `host` line. Env names: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (first start), `PG_LISTEN_PORT` (default 15432, not 5432), `PG_ALLOW_CIDRS` (optional comma-separated source ranges; empty means any source, still TLS plus password), `TLS_SAN` (optional extra names or addresses in the certificate).
- **Redis** (`deploy/bunny/data/Dockerfile.redis`): `port 0` (plain port off), `tls-port` from `REDIS_TLS_PORT` (default 16379), `requirepass` from `REDIS_PASSWORD` (written to a 0600 file, never to a command line or a log), append-only file on, self-signed certificate made on first start in `/data/tls`. Redis has no source filter, so it has no allow-list: TLS plus the password are its controls.

Reaching it: Bunny documents no private network between apps, so the data app is reached through its Anycast TCP endpoints (one for the Postgres port, one for the Redis TLS port), which are public addresses. The passwords are therefore random values of at least 32 characters, generated into 0600 files and handed to the Manager, never printed or committed. If Bunny publishes a stable list of Magic Containers egress addresses, set `PG_ALLOW_CIDRS` to it.

App settings on api, worker and beat (names only):

- `DATABASE_URL=postgresql://<user>:<password>@<data endpoint>:<port>/<db>?sslmode=require`. `settings.py` already refuses `disable`, `allow` and `prefer` for a non-local host. `require` encrypts but does not check the server's certificate (a self-signed one cannot be checked against a public authority), so an active man in the middle is not excluded; the password stays SCRAM-protected. Pinning (`sslmode=verify-ca` with the server certificate as `sslrootcert`) needs the certificate delivered as a file. It is a follow-up (Coach Ed's call, no code in this change).
- `REDIS_URL=rediss://:<password>@<data endpoint>:<port>/0`. For a `rediss://` URL, `apps/common/redis_tls.py` sets the TLS options for both the cache (django-redis) and Celery. With `REDIS_TLS_CA_FILE` (a path) or `REDIS_TLS_CA_PEM` (the certificate text) set, that one certificate is the only trusted anchor (`ssl_cert_reqs=CERT_REQUIRED`; the hostname check is off because the anchor is the pinned certificate and the data app is reached by an address). With neither set it uses `ssl_cert_reqs=CERT_NONE`, an explicit choice: encrypted and password-protected, but the server is not authenticated. To pin, read `/data/tls/redis.crt` (a public certificate) from the Redis container after its first start and put its text in `REDIS_TLS_CA_PEM` on api, worker and beat. The Celery broker uses database number cache plus 1, as before.

### Restore job (data app)

`deploy/bunny/data/restore-job.sh` runs in the worker image (it has `pg_restore` 18 and the backup code), as a one-off container or from a shell in it. Env: `RESTORE_BACKUP_NAME=db-backups/<time>.dump.enc`, `RESTORE_TARGET_URL=postgresql://...?sslmode=require` (the data app's database, which must exist and be empty), plus the worker's `DB_BACKUP_ENCRYPTION_KEY` and `PRIVATE_BLOB_READ_WRITE_TOKEN`. It downloads that one encrypted copy from the private Blob store, decrypts it, runs `pg_restore`, and writes `ops/restore-summary-<time>.json` to the private Blob store: the table count, the total, and the row count of each table (names and numbers only). It prints no URL, key or row. List the copies first with `manage.py restore_database --list`. The same job restores a backup after a data-app loss (`docs/runbooks/backups-and-restore.md`).

### Cutover (Supervisor, on the Manager's GO, 02:00 to 05:00 HKT, announced to SANTE)

1. Take a verified backup first (newest `db-backups/` copy under 30 hours old, `/readyz/` `db_backup` true).
2. Create the data app from the two image digests, with its volume(s) and Anycast TCP endpoints; set the secrets from the 0600 files.
3. Run the restore job into it and compare the summary with the live database's own counts.
4. Read the Redis certificate from the data app's redis container (`/data/tls/redis.crt`, public) and, in the same env update that sets `DATABASE_URL` and `REDIS_URL` on api, worker and beat, set `REDIS_TLS_CA_PEM` to its text. `CERT_NONE` (no `REDIS_TLS_CA_PEM`) is only the documented fallback. Set `DATABASE_URL` and `REDIS_URL` to the data app. Remove postgres, redis and their volumes from the release app. Add probes (api: startup and readiness on `/readyz/`, liveness on `/healthz/`). Expected one-time downtime: 5 to 10 minutes.
5. Keep the old volume until the switch is proven; remove it only afterwards, with Coach Ed's approval.
6. Proof: one real release (or an image re-tag) while a loop polls `/healthz/` once a second from outside. Zero non-200 responses, and `/readyz/` all true.

### Upgrade Postgres or Redis

Only in the quiet window, never from a release.

- **Redis** (cache, sign-in codes, queue, day locks): change the `FROM` tag in `Dockerfile.redis`, run `bunny-data-images.yml`, put the new digest on the data app's redis container. The append-only file carries over. Expect a short outage; workers reconnect by themselves (`CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP`).
- **Postgres, same major version**: the same, with a verified backup first.
- **Postgres, new major version** (a new data directory format): never in place. Create a new data app (or a second Postgres container with a new volume) from the new image, take a fresh backup, run the restore job into it, point `DATABASE_URL` at it, verify, and keep the old volume until the new one is proven. The worker image's `pg_dump` and `pg_restore` major version (worker stage of `deploy/bunny/Dockerfile`) must equal the server's.

### Beat overlap check

During a rolling update the old and new pod run together, so two beat schedulers can exist for up to a minute. Interval tasks then fire from both (each beat keeps its own schedule file in `/tmp`); crontab tasks fire twice at the same minute. Read from the code on 2026-11-22, every beat task is safe to run twice at once, and none needed a change:

| Task | Why it is safe twice |
| --- | --- |
| `billing.sweep` | one transaction per subscription with `select_for_update(skip_locked=True)`; `catch_up` re-reads what is due under the lock; each transition has an idempotency key |
| `billing.notices` | `_send_once` takes the subscription row lock, then checks the `BillingNotice` idempotency key before sending |
| `invoices.ensure_items` | `open_item` returns the existing item (unique source key); a lost race is an `IntegrityError` caught in `open_item_safely` |
| `invoices.daily_alert` | `cache.add` day key in Redis: only one run sends |
| `statements.ensure` | `issue_for_payment` re-checks under the payment row lock; unique `source_key` |
| `statements.deliver` | `deliver` locks the statement row and skips if a delivery was SENT, so no email goes twice. A second run working from an older candidate list can make attempt 2 straight after a failed attempt 1, skipping the backoff. Not changed (reported) |
| `meta_ads.sweep` | re-queues open rows; `deliver` locks the delivery row and ignores a closed one; Meta also de-duplicates by event id |
| `retention.run` | `cache.add` day lock in Redis |
| `file_backup.run` | `cache.add` day lock and running lock in Redis |
| `db_backup.run` | `cache.add` day lock and running lock in Redis |
| `pack.purge` | site row lock; re-reads the deletion point; clearing is idempotent |
| `pack.buyer_notice` | `get_or_create` plus `select_for_update` on the notice row, `sent_at` re-checked under it |
| `pack.registered_notice` | `select_for_update` on the notice row, status and retry time re-checked under it |
| `payments.xendit_reconcile` | unique provider event id (`DuplicateEvent` handled); applies through the webhook's own idempotent handler |
| `lifecycle.run_approved` | batch and item rows locked and status re-checked in `_run_item` |
| `health.heartbeat` | writes one cache value |

The day locks live in Redis, so they now survive a release (Redis is no longer restarted by one). Keep the release app at min 1, max 1 pods so the overlap lasts only for the rolling update.

### Production layout (note)

Production should be created in this shape from day one: a data app (Postgres, Redis, volumes) and a stateless release app (api, worker, beat), with `BUNNY_DATA_APP_ID` set on the `bunny-production` environment so `bunny-production.yml` carries the same guard. This changes the one-app layout in `docs/runbooks/bunny-production.md`; that runbook is not edited here.
