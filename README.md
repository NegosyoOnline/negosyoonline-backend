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

`railway.toml` deliberately starts Gunicorn without running database migrations. Migrations stay an explicit reviewed release action.

Before a staging deploy, configure these Railway variables through the service UI:

- `DJANGO_SECRET_KEY`: a unique staging-only secret.
- `DJANGO_DEBUG=false`
- `DJANGO_ALLOWED_HOSTS`: the generated Railway domain plus `healthcheck.railway.app`.
- `CSRF_TRUSTED_ORIGINS`: the generated Railway HTTPS domain.
- `DATABASE_URL` and `REDIS_URL`: references to the staging Postgres and Redis services.
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`: the Google OAuth client for this environment. Without them the Google sign-in endpoints answer 404.
- `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET`: the Meta app for this environment. Without them the Facebook sign-in endpoints and the data deletion callback answer 404.
- `ADMIN_SECRETS_KEY` and `HQ_HOST`: admin two-step sign-in, hq and the admin lists. Leave both unset until `docs/runbooks/admin-two-step.md` is followed.
- `DASHBOARD_URL` and `SITE_URL`: where the browser lands after a Google sign-in that worked (the dashboard) or did not (the public site's sign-in popup). No trailing slashes.

The deployment must return HTTP 200 from `/healthz/` before Railway accepts it as healthy.
