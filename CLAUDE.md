# NegosyoOnline Backend

## Purpose

This repository is the clean Django backend foundation for NegosyoOnline. It is independent of TWCinc. TWCinc code may be read and ported, adapted to this Django backend, the Vercel frontend apps, and our models, routes, and conventions; never paste it in unchanged, and never bring over its secrets, dependencies wholesale, or deployment configuration.

## Working rules

- Inspect the affected code and current Git state before changing anything.
- Work on a feature branch. `main` is protected: a pull request, the required CI checks, and a merge through GitHub. Never bypass branch protection. Supervisors and builders open no PR and combine no branches: they report ready branches to the manager, whose two Release Builders (backend, frontend) run every CI check locally and open one combined PR per repository per round, which the manager merges on green (Coach Ed, 2026-11-13; `.claude/briefs/manager.md`, section 4).
- Every build starts in ClickUp. The manager session assigns the task to a Supervisor and starts it with `/clickup-supervisor <TASK_ID>` (Coach Ed may also start one himself), and the Supervisor creates the task for each builder and starts and instructs it as its own sub-agent (the manager starts no builder). Coach Ed gives his approvals in the Manager session, which relays them to the Supervisor quoted and dated, and this includes his OK for a change to CLAUDE.md, `.claude/briefs/*` or `.claude/commands/*`, which then still goes through a reviewed branch and the release train (2026-11-14); once he has approved a specific action the manager carries it out itself (2026-11-13). A session not started that way, other than the manager itself, is either a brainstorming session or a session Coach Ed opened to get data from production. It may read, discuss, plan and report that data, but it does not build a task, and reading production data never extends to changing production state. If asked to build there, say the task needs a detailed ClickUp task and a Supervisor session first.
- Coach Ed's rulings on how sessions work are recorded as comments on ClickUp task z8uq9med6x (the manager's task, the rulings log). Every role reads it, newest entries first, at each start and resume; his latest ruling wins over an older brief line. It is a record, not an approval channel.
- ClickUp statuses: the builder sets only `IN PROGRESS` and `FOR CHECKING`; `FOR CHECKING` stays through every fix round. Coach Ed's visual test is local, before the release PR (Coach Ed, 2026-11-14, rulings log comment 1100430000523022): where there is something to look at, the Supervisor runs its own local visual test, then sends him a local packet through the manager, and reports the branch ready only after his OK, given in the Manager session. The Supervisor sets `ACCEPTED` once the branch is merged and it has verified staging; `ACCEPTED` triggers the builder's cleanup. Completed stays Coach Ed's.
- Keep changes small, scoped, and reversible. Do not mix product work with unrelated cleanup.
- Do not create a migration, seed data, alter Railway variables, deploy, or change production state unless Coach Ed explicitly approves that action. His one standing approval for migrations (2026-11-11): a migration that an approved plan specifies may be written, and run on staging by its Supervisor, as `.claude/briefs/supervisor.md` section 10 sets out. Production is never covered. Staging-only, reversible actions that an approved plan or runbook specifies, and staging feature switches that a ruling of his already approves, the manager decides itself and reports afterwards (Coach Ed, 2026-11-13, rulings log comments 1100430000509378 and 1100430000512065); this replaces "explicitly approves that action" for those staging actions only, and production is never covered.
- Never print, commit, copy, or expose secrets. Use ignored local environment files for development and Railway variables for staging or production.

## Architecture boundaries

- Django owns identity, tenancy, authorization, money, orders, webhook validation, and auditability.
- Railway owns managed PostgreSQL and Redis. Use `DATABASE_URL` and `REDIS_URL`; do not hard-code provider connection details.
- Default local checks use SQLite unless `DATABASE_URL` is intentionally supplied.
- The frontend is a separate repository. Treat browser-visible data as untrusted and never expose private-media URLs, provider credentials, or tenant data to it.

## Deployment and verification

- Use `uv` and this repository's own `.venv`; never reuse TWCinc's environment.
- Each Railway service's build and start commands are set in its Settings in the dashboard and recorded in `docs/runbooks/billing.md`; `api` starts Gunicorn and deliberately does not run migrations. Keep migrations as an explicit reviewed release step.
- Before a deployment review, run the targeted tests plus `uv run python manage.py check --deploy`.
- A commit, green CI run, or Railway build alone does not prove a deployment. Verify the intended environment and its health endpoint.

## Current baseline

- `/healthz/` is the staging health endpoint and must remain safe, fast, and unauthenticated.
- HSTS `includeSubDomains` and preload are intentionally deferred until the final domain inventory is approved.
- Avoid introducing features, models, integrations, or user-facing behavior until their written MVP scope is approved.
