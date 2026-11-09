# NegosyoOnline Deploy Flow Design

Date: 2026-11-09
Status: Draft for review. Nothing in this document has been implemented.
Scope: backend (this repo) and frontend (`negosyoonline-frontend`) delivery to staging and production.

## Goals

- Code reaches staging automatically only after CI passes.
- Production changes only through an explicit, recorded approval by Coach Ed.
- Database migrations stay an explicit, reviewed step (per `CLAUDE.md`).
- Every deploy is verified against the intended environment, not assumed from a green build.
- The flow is the same for the backend and the three frontend apps.

## Current state (verified 2026-11-09)

- CI exists in both repos and runs on pull requests and pushes to `main`.
  - Backend: dependency install, `makemigrations --check`, `check`, tests, `check --deploy`, and a PostgreSQL and Redis job.
  - Frontend: `pnpm lint` and `pnpm build` for site, dash and storefronts.
- `main` is protected in both repos: required checks, enforced for admins, zero required reviews.
- No CD. Staging Vercel builds were triggered through the API. Railway staging deploys separately.
- The backend repo has a GitHub `staging` environment. The frontend repo has none.
- `railway.toml` starts Gunicorn and deliberately does not run migrations.

## Decisions

1. **Environment model:** `main` deploys to staging automatically. Production is a deliberate promotion of a commit already verified on staging.
2. **Trigger:** platform git integration (Railway and Vercel watch branches). No deploy tokens in the main deploy path.
3. **Promotion:** version tag plus an approval gate in a GitHub `production` environment.
4. **Migrations:** manual `migrate` workflows per environment, each behind that environment's approval.

## 1. The flow

```text
feature branch -> PR -> CI green (required) -> merge to main
  -> Railway staging and staging Vercel projects deploy from main
  -> verify staging (health endpoint, smoke checks)
  -> if models changed: run "migrate staging" workflow (approval)
  -> tag vX.Y.Z on the verified commit
  -> tag workflow waits for approval in the GitHub `production` environment
  -> workflow fast-forwards the `production` branch to the tag
  -> Railway production and live Vercel projects deploy from `production`
  -> if models changed: run "migrate production" workflow (approval)
  -> verify production
```

Branch tracking:

| Environment | Backend (Railway) | Frontend (Vercel) |
|---|---|---|
| Staging | staging environment tracks `main` | `negosyoonline-site-staging`, `-dash-staging`, `negosyoonline-storefronts` track `main` |
| Production | production environment tracks `production` | live `negosyoonline-site` and `negosyoonline-dash` track `production` |

Only the approved tag workflow moves `production`.

Storefronts: only one project, `negosyoonline-storefronts`, exists and it currently serves the staging hosts. A project cannot track both branches, so a separate production storefronts project (for `live4moreonline.store`) must be created before the first production storefronts release. Until then, storefronts has no production deploy path. This is a required follow-up decision, not part of this design.

## 2. Gates

- **Before merge:** required CI checks on `main`. Platform "wait for CI" options are a second layer, not the primary protection.
- **Before production:** the tag must reference a commit already on `main`. The `production` GitHub environment requires Coach Ed's approval. The approval is recorded in GitHub.
- **Migrations:** run only by the manual workflows. Never on deploy.
- Backend and frontend are promoted independently, since they are separate repositories.

## 3. Verification and rollback

- Backend: `GET /healthz/` returns `{"status": "ok"}` on the intended host.
- Frontend: HTTP 200 on the environment's hosts (staging: `www-staging`, `dash-staging`, and the storefronts tenant hosts).
- A green build or green CI run is never treated as proof of deployment.
- Rollback is code only: point `production` at the previous tag, or redeploy the earlier deployment on the platform.
- Migrations do not roll back automatically. Migrations must be backward compatible with the previous release (add first, remove in a later release).

## 4. One-time setup required (not done)

1. Link the frontend repo to the three staging Vercel projects with `main` as the production branch. Link the live projects with `production`. Auto-deploy on push is unconfirmed today.
2. Find out why the storefronts project did not attach its domains on deploy (a manual promotion was needed) and fix it.
3. Create the `production` branch and a `production` GitHub environment in both repos, with Coach Ed as required reviewer. Add the tag promotion workflow.
4. Add the manual migrate workflows and scope Railway tokens as environment secrets.
5. Point the Railway staging environment at `main` and create the production environment tracking `production`. Enable Railway's wait-for-CI option.

## 5. Build and reproducibility

- CI builds verify the code and discard the result. The deployed artifact is rebuilt by Vercel (`next build`) and Railway (`uv sync --frozen`) from the same commit. Staging and production therefore share the commit and locked dependencies, not a byte-identical artifact.
- Builds must use locked dependencies only: `uv sync --frozen` and `pnpm install --frozen-lockfile` on the platforms as well as CI.
- Runtime versions must match between CI and the platforms. CI pins Node 22.23.1 and Python 3.12. The Vercel projects are set to Node `22.x`. The Railway Python version has not been checked. Align them.
- Frontend `NEXT_PUBLIC_*` values are baked in at build time, so they must be set per environment before a build. No frontend env vars exist yet.
- Expose the running commit or tag so that "verify the intended environment" is concrete, for example in the `/healthz/` response (it must stay safe, fast and unauthenticated) and in a frontend build stamp.

## Out of scope

- Feature work, models and integrations (blocked until MVP scope is approved).
- The production wildcard certificate for tenant hosts and `live4moreonline.store`. That depends on a separate DNS decision.
- Frontend API client, CORS and tenant resolution.
- Background workers and webhook processing.

## Risks

- Vercel does not natively wait for GitHub Actions, so the protected-`main` PR gate carries the CI guarantee for the frontend.
- A single approver is a bottleneck and a single point of failure. A second reviewer can be added to the `production` environment later.
- Platform-only build failures can appear after merge. Staging catches them before production.
