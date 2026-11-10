# Database Migrations Runbook

Migrations are an explicit, reviewed release step (see `CLAUDE.md`). They never run automatically on deploy, and they do not run from CI.

## Why not CI

On 2026-11-10 we tested a Railway project token scoped to the staging environment. The token was valid (`railway status` resolved the project and environment), but Railway rejected it for `railway ssh` with "Unauthorized". Running migrations from CI would need an account-level credential that can reach every project in the workspace, so we decided against it. Migrations are run by Coach Ed, or by an agent session after Coach Ed approves that specific run.

## Identifiers (not secrets)

| Item | Value |
|---|---|
| Project | `negosyoonline-backend` (`f94bedb8-86c5-4c66-8ee5-5d9ce9dad172`) |
| Environments | `staging`, `production` |
| Service | `api` |

Access uses your logged-in Railway account and the SSH key registered to it. No token is stored anywhere.

## Preconditions

- The migration file is in a merged pull request. CI already fails when a model change has no migration (`makemigrations --check`).
- The code that expects the new schema is deployed to the target environment, or the migration is backward compatible with the code that is running (add first, remove in a later release).
- Coach Ed has approved running it against this environment.

## Procedure

Replace `<env>` with `staging` or `production`.

1. Show what would run. This changes nothing:

   ```bash
   railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py migrate --plan
   ```

2. Read the plan. Stop if it lists anything you did not review.
3. Apply:

   ```bash
   railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py migrate --noinput
   ```

4. Confirm nothing is pending. Expected output: `No planned migration operations.`

   ```bash
   railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py migrate --plan
   ```

5. Verify the environment:

   ```bash
   scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/
   ```

6. Record the run (date, environment, commit, plan output summary) in the pull request that introduced the migration.

## Notes

- Migrations do not roll back automatically. Keep each release backward compatible with the previous one.
- Production has not been migrated and has no verified health URL yet. Do not run this against `production` until that environment is set up and Coach Ed approves.

## History

- 2026-11-10: the 18 built-in Django migrations (`contenttypes`, `auth`, `admin`, `sessions`) were applied to `staging`. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
- 2026-11-10: the built-in migrations were unapplied on `staging` and re-applied together with `operators.0001_initial`, because the custom user model (`operators.User`) must exist before `auth` and `admin` are migrated. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
- 2026-11-10: `accounts.0001_initial` and `notifications.0001_initial` were applied to `staging`. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
- 2026-11-10: `accounts.0002_externalidentity` was applied to `staging`. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
- 2026-11-10: `sites.0001_initial` was applied to `staging` by Coach Ed from his own terminal, at commit `23c9e32`. The plan read beforehand listed only that migration. Afterwards `migrate --plan` reported no pending operations.
- 2026-11-11: `notifications.0002_sms_gateway` was applied to `staging`, at commit `91aa0a8`. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `SMS_GATEWAY_ENABLED` was not set.
