# Bunny production release (CI/CD)

Task z8uq9mhbep. Authority: Coach Ed, 2026-11-21 (ClickUp z8uq9med6x, comment 1100430000604811): production plan P5 (a `v*` tag, then his GitHub approval, then deploy of the tested image digests) and P5b (his release approval also approves that release's migrations). The workflow and this runbook are built; **nothing is created or run yet**: no production Bunny app, no GitHub environment, no tag, no `production` branch push. Those follow the plan's dated steps, on the Manager's go. Staging is documented in `bunny-staging.md` and is unchanged by this.

## Release flow

1. **Tag.** `vX.Y.Z` on a commit on `main`. `promote-production.yml` (unchanged) checks the commit is on `main`, runs the full Backend CI, and, behind GitHub environment `production`, fast-forwards the `production` branch to the tag.
2. **The release starts** `.github/workflows/bunny-production.yml`, in either of two ways:
   - **`workflow_run`**: "Promote to production" completed successfully. This is the normal path, because a push made with the workflow `GITHUB_TOKEN` (what `promote-production.yml` uses) does not start push-triggered workflows. A workflow_run job runs on main's tip, so every job checks out the promoted commit (`workflow_run.head_sha`) for the scripts. A failed or cancelled promotion deploys nothing.
   - **Push to `production`** by a human or a deploy key (a harmless duplicate of the above).
   - **plan** (`deploy/bunny/ci/plan-production.sh`): push and dispatch run only from `refs/heads/production`; for `workflow_run` the commit must be exactly the head of `production` (a newer promotion supersedes an older queued run); every path requires a commit on `main`. **Duplicate triggers**: a push run and a workflow_run run for the same commit are serialised by `concurrency`; the second reads production's public `/healthz/`, finds it already on the commit and stops with "nothing to deploy" before any approval (`skip`). If the first run failed and rolled back, production is not on the commit, so the second run proceeds and asks for approval again: a fresh attempt, not a double deploy.
   - **staged** (no approval needed, so it appears before the gate):
     - `require-staging-release.sh`: "Bunny staging release" must have a **successful push run for exactly this SHA**. A manual redeploy run does not count. If staging never ran the commit, the release fails here.
     - `resolve-staging-images.sh`: looks up the digests of `ghcr.io/<owner>/<repo>-staging:<sha>-api|worker|beat`. **Nothing is built.** A missing image fails the release.
     - `list-migrations.sh`: the run summary lists the migration files the release adds on top of what production serves now (production's commit is read from its public `/healthz/`; unreadable means every migration file is listed). Modified or deleted existing migrations are flagged as a warning.
   - **deploy** (environment `bunny-production`, **Coach Ed is the only required reviewer; approving it approves the release and the migrations listed in the summary**): snapshot of the images running now (artifact `bunny-production-previous-images`, run summary), then one `PATCH` per container for **api, worker and beat only** (image tag and digest), then verification, then automatic rollback if anything after the snapshot fails.
3. **Verify** (same scripts as staging): `/healthz/` `commit` equals the first 12 characters of the SHA (3 polls in a row), then `/readyz/` answers 200 with every check true, beat included. `BUNNY_HQ_URL` is also checked when set, by `deploy/bunny/ci/verify-hq.sh`: `GET <hq>/` must answer 303 to `/sign-in/`, 3 polls in a row (see "HQ host check" below).
4. **Migrations.** The api container migrates on start under the advisory lock; worker and beat wait for the migrated schema (`bunny-staging.md`, "Migrations and the lock"). Migrations must be backward-compatible: a rollback restores images only.
5. **Quiet window.** Release only in the agreed quiet window, announced to SANTE first.

Releases are serialised (`concurrency: bunny-production-release`, never cancelled mid-way). The workflow never touches the postgres or redis containers, volumes, env, endpoints or instance count (`deploy/bunny/ci/bunny-deploy.sh`, shared with staging).

## HQ host check

- The HQ host has no `/healthz/`: `apps/operators/middleware.py` sends every request on it to a separate urlconf (`apps.operators.hq_urls`). The release therefore checks it by its sign-in redirect (`verify-hq.sh`, which prints only the http code and the path), not with `verify-release.sh`. The API verify before it already proves the commit, since both hosts are the same containers behind one pull zone.
- `BUNNY_HQ_URL` was removed from `bunny-production` on 2026-11-26 (ruling BUNNY-HQ-CHECK-A), after production run 45747324316 failed at this step and rolled back. It is re-added only after this change is live on `main` and on staging.
- Ruling WORKFLOW-RUN-I (Coach Ed, 2026-11-26): the `workflow_run` job runs on `main`, which the `bunny-production` environment refuses. Releases are started with "Run workflow" on ref `production`. The environment's branch rule is not changed.

## Setup (Coach Ed; names only, no values in the repo)

GitHub environment **`bunny-production`** (Settings > Environments):

| Kind | Name | Use |
| --- | --- | --- |
| required reviewer | Coach Ed only | the production gate |
| deployment branches | `production` only | the environment cannot be used from another branch |
| secret | `BUNNY_API_KEY` | Bunny account API key (`AccessKey` header). Account key; Bunny has no narrower scope. Never printed. A production-only value, not copied from staging |
| variable | `BUNNY_APP_ID` | production Magic Containers app id |
| variable | `BUNNY_CONTAINER_API`, `BUNNY_CONTAINER_WORKER`, `BUNNY_CONTAINER_BEAT` | container id or name of the three app containers |
| variable | `BUNNY_PRODUCTION_URL` | optional here; API base URL verified. Default `https://api.negosyonline.com` |
| variable | `BUNNY_HQ_URL` | optional; HQ API base URL, also verified (`https://hq-api.negosyonline.com`) |

**Repository variable `BUNNY_PRODUCTION_URL`** (Settings > Secrets and variables > Actions > Variables) is also needed for two reads that happen **before** the approval, when environment variables are not available: the migration list in the `staged` job (it defaults to `https://api.negosyonline.com` if unset) and the drift check (it does **not** default: unset means "production not configured", so it skips and stays green). Set it to the same value once production exists.

Other prerequisites: the production Bunny app holds a registry credential that can pull the private `<repo>-staging` package (production runs the images staging built); the `production` branch exists (created by the first tag promotion); the `bunny-staging` environment is untouched. The `workflow_run` trigger reads the workflow file from the default branch, so `bunny-production.yml` must be on `main` before the first tag.

**Two environments, two jobs.** `production` (used by `promote-production.yml` to fast-forward the branch) and `bunny-production` (used by this workflow to deploy) are separate GitHub environments. Coach Ed decides whether `production` also has a required reviewer. Recommendation: `bunny-production` stays the single real gate (his reviewer, branch rule `production`); give `production` no reviewer, so a tag does not ask twice for the same release.

The workflow reads `GITHUB_TOKEN` only (`packages: read`, `actions: read` on the `staged` job).

## Releasing

1. Confirm the commit is on `main`, was released to staging by "Bunny staging release" (green push run) and was checked there.
2. Tag: `git tag vX.Y.Z <sha> && git push origin vX.Y.Z` (Manager's go, in the quiet window). `promote-production.yml` runs and, after the `production` environment step, fast-forwards the branch. When it completes successfully, "Bunny production release" starts by itself (`workflow_run`).
3. Open Actions > "Bunny production release" > the run. Read the `staged` summary: the three image digests and the **migration list**. If something is wrong, **reject** the deployment.
4. Approve the `bunny-production` deployment. Wait for the verify steps.
5. Record the evidence (below).

## Verify by hand

```
curl -s https://api.negosyonline.com/healthz/   # {"status":"ok","commit":"<first 12 of sha>"}
curl -s https://api.negosyonline.com/readyz/    # 200, every value true, "beat" included
git rev-parse --short=12 origin/production      # must equal the commit above
```

**Drift:** `bunny-production-drift.yml` runs hourly and goes red when Bunny's commit is not the `production` branch's HEAD and no production release run is in progress. It skips (green) while `BUNNY_PRODUCTION_URL` or the `production` branch does not exist. An unreachable or non-JSON `/healthz/` logs `bunny=unreachable` and fails with the same DRIFT message (it does not abort on a parse error).

## Rollback

- **Automatic**: any failure after the snapshot restores the previous tags and digests of the three containers and verifies them. The summary says `ROLLED BACK`.
- **Manual** (to an earlier release): Actions > "Bunny production release" > Run workflow, **branch `production`**, `sha` = the full SHA of an earlier commit that was on `production` and has a successful staging run. It goes through the same gate (a second approval by Coach Ed), redeploys those staging-built images to the three containers and verifies. The previous images of any release are in its `bunny-production-previous-images` artifact (90 days).
- The database is never rolled back by a release. Data loss or corruption is a restore from the nightly backup (`backups-and-restore.md`).

## Evidence to record per release

- The green (or rolled-back) `Bunny production release` run URL for the SHA.
- The `staged` summary: image digests and migration list; who approved and when.
- The `Previous images` list (the rollback point).
- `/healthz/` commit and `/readyz/` output after verify (all true).
- The quiet window, who was told, and (first release) that Postgres and Redis containers and volumes are unchanged in the Bunny dashboard.

## Local proof of the scripts

`deploy/bunny/ci/tests/test-production-scripts.sh` runs the four new scripts (plan, staging-run check, image lookup, migration list) plus `bunny-deploy.sh` and `verify-release.sh` against fakes (fake `gh`, `docker`, and a fake Bunny API with `/healthz/` and `/readyz/`): a good release, a failed verify and the restore, the postgres and redis containers untouched, no container env printed. Lint: `actionlint` (pinned release 1.7.7) and `shellcheck`.

## Not verified until the first real run

The PATCH fields Bunny accepts for the production app's containers and the pull credential (as for staging's first deploy); that `GITHUB_TOKEN` can read the `<repo>-staging` package from the production workflow; the environment's deployment-branch rule; that the `workflow_run` trigger fires from "Promote to production" (the workflow must be on `main`; fallback: Run workflow on the `production` branch); the `gh` and `docker` calls against the real services (the tests use fakes).
