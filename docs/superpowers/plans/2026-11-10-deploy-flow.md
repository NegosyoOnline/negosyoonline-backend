# Deploy Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved deploy flow in `docs/superpowers/specs/2026-11-09-deploy-flow-design.md`: `main` deploys to staging, and production moves only through an approved version tag.

**Architecture:** Railway and Vercel track git branches (`main` for staging, `production` for production). A GitHub Actions tag workflow, gated by a `production` environment approval, is the only thing that moves the `production` branch. Migrations are run manually through a runbook, with approval for each run. A commit stamp in `/healthz/` and a verify script prove which build is running.

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
| `docs/runbooks/migrations.md` | backend | Manual, approval-gated migration procedure |
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

- [x] **Step 6: Investigate the storefronts domain attach problem.** Cause: Vercel "Auto-assign Custom Domains" was off on `negosyoonline-storefronts`, so new production deployments were not aliased to the tenant hosts and the hosts kept serving the manually promoted build. Fixed 2026-11-10 by setting `autoAssignCustomDomains: true`; frontend PR 3 deploy `6c654b3` carried all tenant aliases and the hosts returned 200.
- [x] **Step 6b: wildcard certificate (staging).** Fixed 2026-11-10. Cause: Cloudflare already delegated `_acme-challenge.storefronts-staging.negosyonline.com` to `ns1/ns2.vercel-dns.com`, but Vercel had no DNS zone for `negosyonline.com` (`zone: false`), so its nameservers refused the dns-01 challenge. Fix: Coach Ed enabled Vercel DNS for the domain in Team Domains (nameservers stay on Cloudflare; ignore the banner asking to switch them), then removed and re-added the wildcard domain on `negosyoonline-storefronts` to trigger issuance (Refresh does not retry). Verified: never-attached subdomains return 200 with a `*.storefronts-staging.negosyonline.com` Let's Encrypt certificate.
- [ ] **Step 6c (pre-launch): production wildcard.** Repeat for the production storefronts domain: delegate `_acme-challenge.<wildcard base>` NS to `ns1.vercel-dns.com` and `ns2.vercel-dns.com` at the DNS provider, enable Vercel DNS for the apex in Team Domains, add the wildcard domain to the production storefronts project, and confirm a never-attached subdomain loads over HTTPS. Needs Coach Ed's approval for the DNS change.

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

### Task 4: Migrations runbook (replaces the CI migrate workflow)

**Files:**
- Create: `docs/runbooks/migrations.md`

**Decision (2026-11-10):** a Railway project token scoped to staging was valid but rejected for `railway ssh` ("Unauthorized"), so CI cannot run migrations without an account-level credential. Migrations stay manual and approval-gated. No `migrate.yml` and no `RAILWAY_TOKEN` secret.

- [x] **Step 1:** Prove the Railway CLI route on staging with the read-only `migrate --plan` (done, exit 0; 18 built-in migrations were pending).
- [x] **Step 2:** Test a project-scoped token for CI (done, rejected for ssh).
- [x] **Step 3:** Apply the 18 built-in migrations on staging with Coach Ed's approval (done; `migrate --plan` then reported no pending operations).
- [ ] **Step 4:** Commit `docs/runbooks/migrations.md` (procedure, identifiers, preconditions, history).

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
- [x] **Step 3: Create the `production` GitHub environment** in both repos and a `staging` environment in the frontend repo. Required reviewers is unavailable for private repos on Team (422, no UI section), so restrict `production` to deployment tag rule `v*` instead. Backend done 2026-11-10. Frontend pending PR 1 merge. Done in both repos, checked 2026-11-10: `production` and `staging` environments exist in backend and frontend.
- [x] **Step 4: Restrict tag creation** with a repo ruleset so only admins can create, move or delete `v*` tags. This is the interim approval gate. Backend done 2026-11-10 (ruleset `Restrict version tags`). Frontend pending. Done in both repos, checked 2026-11-10: ruleset `Restrict version tags` exists in backend and frontend.
- [ ] **Step 5: Link the frontend repo to Vercel.** Staging projects (`negosyoonline-site-staging`, `negosyoonline-dash-staging`, `negosyoonline-storefronts`) track `main`. Live `negosyoonline-site` and `negosyoonline-dash` track `production`. Verify by checking each project's production branch, then confirm a push triggers a deploy. Confirmed 2026-11-10: the three staging projects are linked to the frontend repo and a push of the PR 1 branch deployed all three to READY, with `Vercel – <project>` commit statuses reporting success. The live `negosyoonline-site` and `negosyoonline-dash` are not linked to this repo yet.
- [ ] **Step 6: Investigate the storefronts domain attach problem.** A deploy did not attach the project's domains until it was promoted by hand. Find the cause in the project's Domains and Git settings, fix it, and confirm a new deploy aliases the tenant hosts without a manual promote.
- [ ] **Step 7: Decide the production storefronts project** for `live4moreonline.store`. A project cannot track both `main` and `production`, so a separate project is required. This is a separate decision, not part of this flow.
- [ ] **Step 8: Railway.** Staging half done 2026-11-10: the staging `api` service tracks `main` and `/healthz/` reports the real commit (`RAILWAY_GIT_COMMIT_SHA` works). Wait-for-CI stays OFF: Railway waits for every check suite, and `Verify staging deploy` itself waits for the deploy, so the two deadlock and Railway marks the deployment SKIPPED (seen on PR 9). Production half DEFERRED by Coach Ed on 2026-11-10 until the MVP is complete, to avoid paying for idle services. The Railway `production` environment exists but is intentionally empty. When built: Postgres, Redis and `api` tracking the `production` branch, own `DATABASE_URL`/`REDIS_URL`, a new `DJANGO_SECRET_KEY` (never copied from staging), and the decided production API hostname `api.negosyoonline.com` (two o's; staging uses `negosyonline.com`, one o). Steps 6c, 7 and Task 7 step 3 wait on this.
- [x] **Step 9: Align runtime versions.** CI pins Node 22.23.1 and Python 3.12. Vercel staging projects use `22.x` (compatible). Railway defaulted to Python 3.13, so `.python-version` now pins 3.12.

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
