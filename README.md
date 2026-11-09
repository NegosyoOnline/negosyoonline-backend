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
- `apps/operators`: the staff user model used by the Django admin. Subscribers are not Django users.
- `API_ALLOWED_ORIGINS`: the site and dashboard origins allowed to call the API from a browser. Never list a storefront origin.

## Railway staging

`railway.toml` deliberately starts Gunicorn without running database migrations. Migrations stay an explicit reviewed release action.

Before a staging deploy, configure these Railway variables through the service UI:

- `DJANGO_SECRET_KEY`: a unique staging-only secret.
- `DJANGO_DEBUG=false`
- `DJANGO_ALLOWED_HOSTS`: the generated Railway domain plus `healthcheck.railway.app`.
- `CSRF_TRUSTED_ORIGINS`: the generated Railway HTTPS domain.
- `DATABASE_URL` and `REDIS_URL`: references to the staging Postgres and Redis services.

The deployment must return HTTP 200 from `/healthz/` before Railway accepts it as healthy.
