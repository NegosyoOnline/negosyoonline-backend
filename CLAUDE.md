# NegosyoOnline Backend

## Purpose

This repository is the clean Django backend foundation for NegosyoOnline. It is independent of TWCinc. TWCinc may be consulted only to understand a confirmed legacy behavior; never copy its code, dependencies, secrets, or deployment configuration wholesale.

## Working rules

- Inspect the affected code and current Git state before changing anything.
- Work on a feature branch. `main` is protected: open a pull request, pass the required CI checks, and merge through GitHub. Never bypass branch protection.
- Keep changes small, scoped, and reversible. Do not mix product work with unrelated cleanup.
- Do not create a migration, seed data, alter Railway variables, deploy, or change production state unless Coach Ed explicitly approves that action.
- Never print, commit, copy, or expose secrets. Use ignored local environment files for development and Railway variables for staging or production.

## Architecture boundaries

- Django owns identity, tenancy, authorization, money, orders, webhook validation, and auditability.
- Railway owns managed PostgreSQL and Redis. Use `DATABASE_URL` and `REDIS_URL`; do not hard-code provider connection details.
- Default local checks use SQLite unless `DATABASE_URL` is intentionally supplied.
- The frontend is a separate repository. Treat browser-visible data as untrusted and never expose private-media URLs, provider credentials, or tenant data to it.

## Deployment and verification

- Use `uv` and this repository's own `.venv`; never reuse TWCinc's environment.
- `railway.toml` starts Gunicorn and deliberately does not run migrations. Keep migrations as an explicit reviewed release step.
- Before a deployment review, run the targeted tests plus `uv run python manage.py check --deploy`.
- A commit, green CI run, or Railway build alone does not prove a deployment. Verify the intended environment and its health endpoint.

## Current baseline

- `/healthz/` is the staging health endpoint and must remain safe, fast, and unauthenticated.
- HSTS `includeSubDomains` and preload are intentionally deferred until the final domain inventory is approved.
- Avoid introducing features, models, integrations, or user-facing behavior until their written MVP scope is approved.
