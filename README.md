# NegosyoOnline Backend

Clean Django foundation for the NegosyoOnline API.

## Local setup

1. Use the safe default SQLite database for basic local checks. See `.env.example` for the variables required when connecting to another environment. The initial foundation reads operating-system variables only.
2. Run `uv run python manage.py migrate` only when a developer explicitly needs a local database.
3. Run `uv run python manage.py runserver`.

Railway staging owns PostgreSQL and Redis. Its credentials must be configured later as Railway environment variables, not committed here.

## Layout

- `config/api.py`: the single API, served under `/v1/`. Its OpenAPI description is at `/v1/openapi.json` and is the contract the frontend builds against.
- `apps/common`: shared code with no business meaning (public ids, the error shape, the origin allowlist).
- `apps/operators`: the staff user model, admin two-step sign-in, hq, the admin lists and the operator audit record (`audit.record()` is the only way to write it). Subscribers are not Django users. See `docs/runbooks/admin-two-step.md`.
- `API_ALLOWED_ORIGINS`: the site and dashboard origins allowed to call the API from a browser. Never list a storefront origin.
- `apps/accounts`: subscribers (`Account`), their sessions, email-code, Google and Facebook sign-in, and account settings. Other apps call `apps/accounts/services.py`, never the tables.
- hq, the admin's console: Django pages in `apps/operators` (`hq_urls.py`, `hq_views.py`, `templates/hq/`), served only on the host named by `HQ_HOST` and nowhere when it is empty. That host serves nothing else except the admin lists (the Django admin) at `/admin/`, which open only for an hq session that passed both steps. No other host serves `/admin/`, and there is no username-and-password sign-in.
- `apps/notifications`: `send_email()` is the only way to send email. With no `EMAIL_HOST` set, email is printed to the service log.

## Railway staging

The `api` service's start command is set in its Railway Settings and recorded in `docs/runbooks/billing.md`. It starts Gunicorn and deliberately does not run database migrations. Migrations stay an explicit reviewed release action.

Before a staging deploy, configure these Railway variables through the service UI:

- `DJANGO_SECRET_KEY`: a unique staging-only secret.
- `DJANGO_DEBUG=false`
- `DJANGO_ALLOWED_HOSTS`: the generated Railway domain plus `healthcheck.railway.app`.
- `CSRF_TRUSTED_ORIGINS`: the generated Railway HTTPS domain.
- `DATABASE_URL` and `REDIS_URL`: references to the staging Postgres and Redis services.
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`: the Google OAuth client for this environment. Without them the Google sign-in endpoints answer 404.
- `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET`: the Meta app for this environment. Without them the Facebook sign-in endpoints and the data deletion callback answer 404.
- `ADMIN_SECRETS_KEY` and `HQ_HOST`: admin two-step sign-in, hq and the admin lists. Leave both unset until `docs/runbooks/admin-two-step.md` is followed.
- `SMS_GATEWAY_ENABLED`: `1` turns on the SMS gateway (`send_sms` in `apps/notifications`, and the admin's SMS lists). Leave it unset until the `apps/notifications` SMS migration has been applied and `REDIS_URL` is set; unset, no text is queued and the SMS tables are not read or written.
- `RETENTION_ENABLED`: `1` lets the account-side retention rules delete and blank (`manage.py run_retention`, and `manage.py dispose_account` for one account on request). Leave it unset until Coach Ed approves it for the environment, following `docs/runbooks/retention.md`; unset, a run of those rules changes nothing and only the dry run counts.
- `BILLING_ENABLED`: `1` turns on billing (`apps/billing`: the subscription lifecycle, the billing endpoints, and `manage.py billing_sweep`, `billing_backfill_subscriptions` and `billing_exempt`). Leave it unset until the `apps/billing` migration has been applied and Coach Ed approves it for the environment, following `docs/runbooks/billing.md`; unset, nothing reads or writes the billing tables and the sweep does nothing.
- `STOREFRONT_ENABLED` and `STOREFRONT_SERVER_SECRET`: the storefront surface, `/storefront/v1/`, called only by the storefronts server (`apps/storefront`). `1` turns it on, and only while the secret holds at least 32 characters; unset, every path under it answers 404. Leave both unset until Coach Ed approves each for the environment, following `docs/runbooks/storefront.md`. Coach Ed sets the secret himself; it is never printed or committed.
- `DASHBOARD_URL` and `SITE_URL`: where the browser lands after a Google sign-in that worked (the dashboard) or did not (the public site's sign-in popup). No trailing slashes.

After a deploy, `/healthz/` must return HTTP 200 over HTTPS: `scripts/verify_deploy.sh` checks it. Railway checks it too, at the start of each deployment: the staging `api` service has the health path `/healthz/`. Railway's check calls over plain HTTP, so `/healthz/`, and only that exact path, is exempt from the app's HTTPS redirect (`SECURE_REDIRECT_EXEMPT` in `config/settings.py`); every other path still redirects. See "Worker and beat" in `docs/runbooks/billing.md`.
