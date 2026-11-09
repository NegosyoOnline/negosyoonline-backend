# Deploy Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved deploy flow in `docs/superpowers/specs/2026-11-09-deploy-flow-design.md`: `main` deploys to staging, and production moves only through an approved version tag.

**Architecture:** Railway and Vercel track git branches (`main` for staging, `production` for production). A GitHub Actions tag workflow, gated by a `production` environment approval, is the only thing that moves the `production` branch. A manual migrate workflow, gated per environment, runs migrations. A commit stamp in `/healthz/` and a verify script prove which build is running.

**Tech Stack:** Django 5.2, GitHub Actions, Railway, Vercel, uv, pnpm.

**Rules from CLAUDE.md that bind this plan**
- Work on a feature branch and open a PR. `main` is protected. Never bypass branch protection.
- No migrations, seed data, Railway variable changes, deploys or production-state changes without Coach Ed's explicit approval. Every step marked **APPROVAL** below stops and asks first.
- Never print or commit secrets.
- Nothing is copied from TWCinc.

**Repos:** Tasks 1 to 4 are in the backend repo (this worktree, branch `claude/negosyoonline-staging-proof-eecb2a`). Task 5 is in `/Users/edgeronilla/Dev/negosyoonline/negosyoonline-frontend` on its own feature branch. Task 6 is platform setup.

---

## File structure

| File | Repo | Responsibility |
|---|---|---|
| `config/settings.py` | backend | Add `RELEASE_COMMIT` setting read from the environment |
| `config/urls.py` | backend | `healthz` returns the short commit |
| `apps/health/tests.py` | backend | Tests for the stamp |
| `scripts/verify_deploy.sh` | backend | Poll a health URL until it reports the expected commit |
| `.github/workflows/promote-production.yml` | backend | Verify then promote a tag to the `production` branch |
| `.github/workflows/migrate.yml` | backend | Manual, approval-gated migrations |
| `.github/workflows/promote-production.yml` | frontend | Same promotion flow, checking the frontend CI |

---

### Task 1: Commit stamp in /healthz/

**Files:**
- Modify: `config/settings.py` (append near the other env-driven settings)
- Modify: `config/urls.py:22-23`
- Modify: `apps/health/tests.py:7-11`

`/healthz/` must stay safe, fast and unauthenticated (CLAUDE.md). A 12-character commit prefix leaks nothing sensitive and is what makes "verify the intended environment" concrete. Railway exposes the deployed commit as `RAILWAY_GIT_COMMIT_SHA`; Task 6 verifies that on the real service.

- [ ] **Step 1: Write the failing test**

Replace the first test in `apps/health/tests.py` and add imports:

```python
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


class HealthCheckTests(TestCase):
    @override_settings(RELEASE_COMMIT="0123456789ab")
    def test_healthz_returns_ok_with_commit(self):
        response = self.client.get(reverse("healthz"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content, {"status": "ok", "commit": "0123456789ab"}
        )

    @override_settings(RELEASE_COMMIT="unknown")
    def test_healthz_reports_unknown_commit_when_unset(self):
        response = self.client.get(reverse("healthz"), secure=True)

        self.assertJSONEqual(response.content, {"status": "ok", "commit": "unknown"})

    def test_default_cache_round_trip(self):
        cache_key = "health-test-cache-key"

        cache.set(cache_key, "ok", timeout=30)

        self.assertEqual(cache.get(cache_key), "ok")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python manage.py test apps.health`
Expected: FAIL with `AttributeError` or a JSON mismatch, because `RELEASE_COMMIT` and the `commit` key do not exist yet.

- [ ] **Step 3: Add the setting**

Append to `config/settings.py`:

```python
# Short commit of the running release, used by /healthz/ to prove which build is live.
RELEASE_COMMIT = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown")[:12]
```

- [ ] **Step 4: Update the view**

In `config/urls.py` change the imports and view:

```python
from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path


def healthz(_request):
    return JsonResponse({"status": "ok", "commit": settings.RELEASE_COMMIT})
```

Leave `urlpatterns` as it is.

- [ ] **Step 5: Run the tests and the deploy check**

Run: `uv run python manage.py test apps.health`
Expected: PASS (3 tests).

Run: `DJANGO_DEBUG=false DJANGO_SECRET_KEY=ci-only-long-randomized-value-5rYxP2nQ8kL7mV4cD9aF1wH6sT3uJ0bE uv run python manage.py check --deploy`
Expected: exactly two warnings, `security.W005` (HSTS include-subdomains) and `security.W021` (HSTS preload), both pre-existing and deliberately deferred per CLAUDE.md. No other issues.

- [ ] **Step 6: Commit**

```bash
git add config/settings.py config/urls.py apps/health/tests.py
git commit -m "feat: report release commit from healthz"
```

---

### Task 2: Deploy verification script

**Files:**
- Create: `scripts/verify_deploy.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Usage: scripts/verify_deploy.sh <healthz-url> [expected-commit-prefix]
# Polls the health endpoint until it returns status ok (and the expected commit, if given).
set -euo pipefail

url="${1:?usage: verify_deploy.sh <healthz-url> [expected-commit-prefix]}"
expected="${2:-}"
attempts=30
delay=10

for i in $(seq 1 "$attempts"); do
  body="$(curl -fsS --max-time 10 "$url" || true)"
  if echo "$body" | grep -q '"status": "ok"'; then
    if [ -z "$expected" ] || echo "$body" | grep -q "\"commit\": \"${expected}"; then
      echo "OK: $body"
      exit 0
    fi
  fi
  echo "attempt $i/$attempts: not ready ($body)"
  sleep "$delay"
done

echo "FAILED: $url did not report the expected release" >&2
exit 1
```

- [ ] **Step 2: Make it executable and sanity-check against staging (read-only)**

Run: `chmod +x scripts/verify_deploy.sh && scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/`
Expected: `OK: {"status": "ok"}` now (the stamp appears after Task 1 reaches staging). A non-200 or timeout must exit non-zero after the retries.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_deploy.sh
git commit -m "chore: add deploy verification script"
```

---

### Task 3: Production promotion workflow (backend)

**Files:**
- Create: `.github/workflows/promote-production.yml`

Two jobs so the approval prompt only appears once the checks pass. The push is non-forced, so it fails if `production` is not an ancestor of the tag.

- [ ] **Step 1: Write the workflow**

```yaml
name: Promote to production

on:
  push:
    tags:
      - "v[0-9]+.[0-9]+.[0-9]+"

permissions:
  contents: write
  actions: read

concurrency:
  group: promote-production
  cancel-in-progress: false

jobs:
  verify:
    name: Verify tagged commit
    runs-on: ubuntu-24.04
    steps:
      - name: Check out source
        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5
        with:
          fetch-depth: 0
      - name: Require the tagged commit to be on main
        run: |
          git fetch origin main
          git merge-base --is-ancestor "$GITHUB_SHA" origin/main
      - name: Require a passing CI run for the tagged commit
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          passed=$(gh run list --repo "$GITHUB_REPOSITORY" --workflow "Backend CI" \
            --commit "$GITHUB_SHA" --status success --json databaseId --jq 'length')
          test "$passed" -ge 1

  promote:
    name: Fast-forward production branch
    needs: verify
    runs-on: ubuntu-24.04
    environment: production
    steps:
      - name: Check out source
        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5
        with:
          fetch-depth: 0
      - name: Fast-forward production to the tag (no force)
        run: git push origin "$GITHUB_SHA:refs/heads/production"
```

- [ ] **Step 2: Lint the YAML**

Run: `uvx --from yamllint yamllint -d "{extends: relaxed, rules: {line-length: disable}}" .github/workflows/promote-production.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/promote-production.yml
git commit -m "ci: add approval-gated production promotion workflow"
```

---

### Task 4: Manual migrate workflow (backend)

**Files:**
- Create: `.github/workflows/migrate.yml`

CLAUDE.md keeps migrations an explicit reviewed release step. This workflow only runs when someone dispatches it and the environment approves. It runs from the runner against the target database, so the target needs a `DATABASE_URL` environment secret reachable from GitHub. Railway's internal URL is not reachable from a runner, so **Step 1 is a decision gate before any code is written.**

- [ ] **Step 1: APPROVAL gate. Choose and prove the database access method**

Ask Coach Ed to pick one:
1. A Railway TCP proxy (public) `DATABASE_URL`, stored as an environment secret per environment. Simplest, but exposes the database to the internet behind a password.
2. Run the migration inside Railway via the Railway CLI with a scoped project token. Keeps the database private, but needs the CLI syntax proven on staging first.

Do not store either value or run anything against a database until Coach Ed approves the choice and the staging dry run in Step 4. The workflow below assumes option 1. If option 2 is chosen, replace the two `manage.py` steps with the proven Railway CLI command and update this task before continuing.

- [ ] **Step 2: Write the workflow**

```yaml
name: Migrate database

on:
  workflow_dispatch:
    inputs:
      target:
        description: Environment to migrate
        type: choice
        required: true
        options:
          - staging
          - production

permissions:
  contents: read

concurrency:
  group: migrate-${{ inputs.target }}
  cancel-in-progress: false

jobs:
  migrate:
    name: Migrate ${{ inputs.target }}
    runs-on: ubuntu-24.04
    environment: ${{ inputs.target }}
    env:
      DJANGO_SECRET_KEY: ci-only-long-randomized-value-5rYxP2nQ8kL7mV4cD9aF1wH6sT3uJ0bE
      DJANGO_DEBUG: "false"
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
    steps:
      - name: Check out the branch deployed to this environment
        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5
        with:
          ref: ${{ inputs.target == 'production' && 'production' || 'main' }}
      - name: Set up Python
        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0
        with:
          python-version: "3.12"
      - name: Install locked dependencies
        run: python -m pip install uv && uv sync --frozen
      - name: Record the migration plan
        run: |
          {
            echo "### Migration plan for ${{ inputs.target }} ($(git rev-parse --short HEAD))"
            echo '```'
            uv run python manage.py migrate --plan
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"
      - name: Apply migrations
        run: uv run python manage.py migrate --noinput
```

- [ ] **Step 3: Lint the YAML**

Run: `uvx --from yamllint yamllint -d "{extends: relaxed, rules: {line-length: disable}}" .github/workflows/migrate.yml`
Expected: no errors.

- [ ] **Step 4: APPROVAL. Staging dry run**

After the PR merges and Coach Ed has stored the staging `DATABASE_URL` environment secret and approved running it, dispatch with `target: staging`, then verify:

Run: `scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/`
Expected: `OK:` and the job summary shows the plan. With no models yet the plan lists the Django built-in migrations only.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/migrate.yml
git commit -m "ci: add approval-gated manual migrate workflow"
```

---

### Task 5: Frontend promotion workflow

**Repo:** `/Users/edgeronilla/Dev/negosyoonline/negosyoonline-frontend`. Create a feature branch first: `git switch -c ci/production-promotion`.

**Files:**
- Create: `.github/workflows/promote-production.yml`

- [ ] **Step 1: Write the workflow** (identical flow, frontend CI name)

```yaml
name: Promote to production

on:
  push:
    tags:
      - "v[0-9]+.[0-9]+.[0-9]+"

permissions:
  contents: write
  actions: read

concurrency:
  group: promote-production
  cancel-in-progress: false

jobs:
  verify:
    name: Verify tagged commit
    runs-on: ubuntu-24.04
    steps:
      - name: Check out source
        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5
        with:
          fetch-depth: 0
      - name: Require the tagged commit to be on main
        run: |
          git fetch origin main
          git merge-base --is-ancestor "$GITHUB_SHA" origin/main
      - name: Require a passing CI run for the tagged commit
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          passed=$(gh run list --repo "$GITHUB_REPOSITORY" --workflow "Frontend CI" \
            --commit "$GITHUB_SHA" --status success --json databaseId --jq 'length')
          test "$passed" -ge 1

  promote:
    name: Fast-forward production branch
    needs: verify
    runs-on: ubuntu-24.04
    environment: production
    steps:
      - name: Check out source
        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5
        with:
          fetch-depth: 0
      - name: Fast-forward production to the tag (no force)
        run: git push origin "$GITHUB_SHA:refs/heads/production"
```

- [ ] **Step 2: Lint the YAML**

Run: `uvx --from yamllint yamllint -d "{extends: relaxed, rules: {line-length: disable}}" .github/workflows/promote-production.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/promote-production.yml
git commit -m "ci: add approval-gated production promotion workflow"
```

---

### Task 6: Platform setup (all APPROVAL-gated, no code)

Each item changes shared infrastructure. Do one at a time, state what will change, wait for Coach Ed's yes, then verify.

- [ ] **Step 1: Open PRs for Tasks 1 to 5** (backend and frontend). Merge only after required CI passes. Do not bypass branch protection.
- [ ] **Step 2: Create the `production` branch** in both repos from the current `main`: `git push origin origin/main:refs/heads/production`. Verify with `git ls-remote origin production`.
- [ ] **Step 3: Create the `production` GitHub environment** in both repos with Coach Ed as required reviewer, and a `staging` environment in the frontend repo. Verify in repo Settings, Environments.
- [ ] **Step 4: Restrict tag creation** with a repo ruleset so only Coach Ed (or admins) can create `v*` tags.
- [ ] **Step 5: Link the frontend repo to Vercel.** Staging projects (`negosyoonline-site-staging`, `negosyoonline-dash-staging`, `negosyoonline-storefronts`) track `main`. Live `negosyoonline-site` and `negosyoonline-dash` track `production`. Verify by checking each project's production branch, then confirm a push triggers a deploy. Auto-deploy on push is unconfirmed today.
- [ ] **Step 6: Investigate the storefronts domain attach problem.** A deploy did not attach the project's domains until it was promoted by hand. Find the cause in the project's Domains and Git settings, fix it, and confirm a new deploy aliases the tenant hosts without a manual promote.
- [ ] **Step 7: Decide the production storefronts project** for `live4moreonline.store`. A project cannot track both `main` and `production`, so a separate project is required. This is a separate decision, not part of this flow.
- [ ] **Step 8: Railway.** Point the staging service at `main`, create the production environment tracking `production`, and enable wait-for-CI. Confirm Railway provides `RAILWAY_GIT_COMMIT_SHA` by checking `/healthz/` reports a real commit after the next staging deploy. If it reports `unknown`, set the variable name accordingly and update Task 1.
- [ ] **Step 9: Align runtime versions.** CI pins Node 22.23.1 and Python 3.12. Check the Vercel projects (`22.x`) and the Railway Python version and match them.

---

### Task 7: First end-to-end dry run (staging only)

- [ ] **Step 1:** Merge a trivial docs change to `main` through a PR. Confirm Railway staging and the three Vercel staging projects deploy on their own.
- [ ] **Step 2:** Run `scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <merge-commit-prefix>` and expect `OK:` with that commit. Check the staging web hosts return 200.
- [ ] **Step 3: APPROVAL.** Only with Coach Ed's explicit go-ahead, tag `v0.1.0` on the verified commit, approve the `production` environment, and watch the promotion workflow fast-forward `production`. Then verify production health with the verify script. Skip this step until production services exist.

---

## Self-review against the spec

- Goals and decisions (staging from `main`, tag plus approval, manual migrations): Tasks 3, 4, 5, 6.
- Gates (required CI, tag on `main`, `production` environment approval, no automatic migrations): Tasks 3 to 5.
- Verification and rollback: Tasks 1, 2 and 7. Rollback is re-pointing `production` at an earlier tag with `git push origin <old-tag-commit>:refs/heads/production --force-with-lease`, which is a force push and must be explicitly approved when needed. It is deliberately not automated.
- One-time setup items 1 to 5 in the spec: Task 6, steps 2 to 8.
- Build and reproducibility (locked installs, version parity, commit stamp, build variables): Tasks 1, 6 step 9. Per-environment frontend build variables are deferred until the frontend reads any, as the spec states.
- Out of scope items are not planned.
- Known unverified assumptions, each with a task step that checks it: Railway sets `RAILWAY_GIT_COMMIT_SHA` (Task 6 step 8); a push made with the workflow token still triggers Railway and Vercel git integrations (Task 7 step 3); the migrate database access method (Task 4 step 1).
