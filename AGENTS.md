# NegosyoOnline Backend

## Scope

This repository is the clean Django API and background-services foundation for NegosyoOnline. It is independent from TWCinc. TWCinc may be consulted only as a behavior reference and must never become a runtime dependency or source-copy destination.

## Safety

- Keep credentials only in local ignored environment files or Railway variables. Never commit secrets.
- PostgreSQL and Redis are Railway-managed services. Do not run migrations, seed data, or change production variables without explicit approval.
- Default local development uses SQLite only until a developer intentionally supplies `DATABASE_URL`.
- Preserve tenant isolation, authorization, idempotent webhooks, money integrity, and auditability when features are introduced.
- API errors use one shape, produced by `apps/common/errors.py`. Raise `ApiError`; do not hand-build error responses.
- Never expose a database primary key through the API. Use `apps.common.ids.new_public_id`.
- Signed-in endpoints use `auth=signed_in` from `apps/accounts/api.py` (the `SessionCookieAuth` class). Never read the session cookie by hand.
- Unauthenticated endpoints that change state call `require_allowed_origin(request)` first.
- Never store or log a sign-in code, a session token, or an email body.
- Only `apps/accounts/google.py` talks to Google. Never store or log a provider code, token or response.

## Workflow

- Use `uv` and the repository `.venv`; do not reuse the TWCinc environment.
- Run targeted tests and `python manage.py check --deploy` before a deployment review.
- Deploy only through the approved GitHub and Railway flow. A successful commit is not deployment proof.
