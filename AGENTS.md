# NegosyoOnline Backend

## Scope

This repository is the clean Django API and background-services foundation for NegosyoOnline. It is independent from TWCinc. TWCinc may be consulted only as a behavior reference and must never become a runtime dependency or source-copy destination.

## Safety

- Keep credentials only in local ignored environment files or Railway variables. Never commit secrets.
- PostgreSQL and Redis are Railway-managed services. Do not run migrations, seed data, or change production variables without explicit approval.
- Default local development uses SQLite only until a developer intentionally supplies `DATABASE_URL`.
- Preserve tenant isolation, authorization, idempotent webhooks, money integrity, and auditability when features are introduced.

## Workflow

- Use `uv` and the repository `.venv`; do not reuse the TWCinc environment.
- Run targeted tests and `python manage.py check --deploy` before a deployment review.
- Deploy only through the approved GitHub and Railway flow. A successful commit is not deployment proof.
