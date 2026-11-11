# Billing Build B1a: api, worker and beat take their settings from the Railway dashboard (plan)

Date: 2026-11-11. ClickUp task: z8uq9me1un (https://app.clickup.com/t/z8uq9me1un), parent z8uq9m8h46.
Branch: `chore/z8uq9me1un-railway-project-file`, cut from `origin/main` at `1ba9cc6`. The branch keeps its name; only this file was renamed.
Status: **not approved.** A plan for Coach Ed. No code, no `railway*.toml` change, no Railway write.

This file replaces `2026-11-11-backend-billing-b1a-railway-project-file.md` (commit `2a7562b`), which planned the project-file route. Coach Ed ruled "Dashboard settings" on 2026-11-11, so that route is dropped from this task: no `.railway/railway.ts`, no Node package, no CLI update. The first version stays readable at `2a7562b`.

## 1. Sources

| Short name | Source |
|---|---|
| Ruling | ClickUp z8uq9me1un, comment of 2026-11-11: Coach Ed, "Dashboard settings". It outranks the task description. |
| Task | ClickUp z8uq9me1un, description, for everything the ruling leaves unchanged |
| Brief | The coordinator's message to this builder after the ruling, 2026-11-11 |
| Old system | https://docs.railway.com/config-as-code and https://docs.railway.com/config-as-code/reference |
| IaC guide | https://docs.railway.com/infrastructure-as-code, section "Migrating from Config as Code" |
| Settings pages | https://docs.railway.com/deployments/start-command, https://docs.railway.com/builds/build-and-start-commands, https://docs.railway.com/deployments/healthchecks, https://docs.railway.com/deployments/restart-policy, https://docs.railway.com/deployments/staged-changes |
| Code | `railway.toml`, `railway.worker.toml`, `railway.beat.toml`, `apps/billing/tests/test_tasks.py`, `docs/runbooks/billing.md`, `README.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-11-09-deploy-flow-design.md`, at `1ba9cc6` |

All pages were read on 2026-11-11 through a summarising fetch tool, not as raw text. The dashboard itself was not opened.

## 2. Items for Coach Ed

Each Railway write below is his approval, as the ruling says. Beyond those approvals the plan has one item.

### 2.1 Five older documents that mention `railway.toml` are left as they are

- **Checked.** Four plans and one spec mention a `railway*.toml` as part of a dated record (listed in section 6). None is an instruction anyone follows today.
- **Options.** (a) **Recommended.** Leave them. (b) Edit each to name the dashboard.
- **Consequence.** With (a), a search for `railway.toml` still finds five old documents after the task. With (b), dated records are rewritten after the fact.

## 3. The values to type

Each value is copied from the file named in the heading, at `1ba9cc6`. Project `negosyoonline-backend`, environment **staging**, the service's Settings.

**About the field names.** Railway's documentation describes each setting but does not print the label the dashboard shows, except the three restart options. The "Setting" column therefore says what the setting is; the "Label" column gives a label only where a page confirms it. Where it says "not confirmed", find the field by what it does and tell the coordinator the label seen, so the runbook can record it.

### 3.1 `api` (from `railway.toml`)

| Setting | Label in the dashboard | Value |
|---|---|---|
| Build command | Not confirmed (described as the build command override "in your service settings") | `uv sync --frozen` |
| Start command | Not confirmed (a screenshot caption calls it the "custom start command") | `uv run gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --access-logfile - --error-logfile -` |
| Health path | Not confirmed ("Under your service settings, input your health endpoint") | `/healthz/` |
| Health timeout | Not confirmed; a number of seconds on the service settings page. Railway's default is 300. | `120` |
| Restart policy | A dropdown with the options `Always`, `On Failure`, `Never` (confirmed) | `On Failure` |
| Restart retries | Not confirmed. The page names no field for the count; it says the default is `On Failure` with 10 restarts. | `10` |
| Config file path | Not confirmed (the guide says "the config file path field"; the CLI page says "Railway Config File") | empty |

### 3.2 `worker` (from `railway.worker.toml`)

| Setting | Label in the dashboard | Value |
|---|---|---|
| Build command | As 3.1 | `uv sync --frozen` |
| Start command | As 3.1 | `uv run celery -A config worker --loglevel=info --concurrency=2` |
| Health path | As 3.1 | empty |
| Restart policy | As 3.1 | `On Failure` |
| Restart retries | As 3.1 | `10` |
| Config file path | As 3.1 | empty (today: `railway.worker.toml`) |
| Public domain | Networking | none |

### 3.3 `beat` (from `railway.beat.toml`), typed when the service is created

| Setting | Label in the dashboard | Value |
|---|---|---|
| Build command | As 3.1 | `uv sync --frozen` |
| Start command | As 3.1 | `uv run celery -A config beat --loglevel=info --schedule=/tmp/celerybeat-schedule` |
| Health path | As 3.1 | empty |
| Restart policy | As 3.1 | `On Failure` |
| Restart retries | As 3.1 | `10` |
| Number of instances | Not confirmed (the old key was `numReplicas`) | `1` |
| Config file path | As 3.1 | empty |
| Public domain | Networking | none |

Not typed anywhere: a migration command, a pre-deploy command, any variable. "Wait for CI" stays off on all three and is not touched. `$PORT` is typed as written; Railway runs the start command in a shell, so it is expanded at start.

## 4. Order of steps

`api` keeps its start command at every step, because its Settings are made equal to `railway.toml` while `railway.toml` is still the file Railway reads. Until the file is removed, the file wins over the dashboard ("Configuration defined in code will always override values from the dashboard"), so steps 2 to 4 change nothing that runs.

| # | Step | Who | Approval | What runs afterwards |
|---|---|---|---|---|
| 1 | The PR is opened early as a draft: the three files deleted, the test and documents of section 6 changed. **Not merged.** | Builder, when the coordinator says so | The plan's approval | Unchanged |
| 2 | Before typing anything, read and note what `api` and `worker` Settings hold today for every row of 3.1 and 3.2. | Coach Ed or the coordinator, in the dashboard | None (reading) | Unchanged |
| 3 | Type the values of 3.1 into `api` and 3.2 into `worker`, **except** the config file path. Dashboard changes are staged: they take effect when the banner's "Details", then "Deploy" is clicked, which redeploys the service. Holding Alt while clicking "Deploy" saves "without triggering a redeploy". Either is safe here: a redeploy still reads `railway.toml`, which says the same for `api`. | Coach Ed or the coordinator | Coach Ed | `api`: Gunicorn, from `railway.toml`. `worker`: still Gunicorn, from `railway.toml` |
| 4 | Read every value back from each service's Settings and compare with section 3. | Whoever did not type them, if possible | None | Unchanged |
| 5 | Clear the config file path on `worker`; on `api` if step 2 found one set. Read it back as empty. | Coach Ed or the coordinator | Coach Ed | Unchanged. If a deploy after this step fails or `api` changes its start command, stop: restore the path and report. |
| 6 | The coordinator confirms steps 3 to 5 to the builder. The builder updates the branch from `origin/main`, reruns the tests, and the PR is merged. Staging deploys `api` and `worker` with no old file in the repository. | Builder, coordinator | The usual PR gates | `api`: Gunicorn, from its Settings. `worker`: Celery, from its Settings |
| 7 | Checks, all read-only: `https://api-staging.negosyonline.com/healthz/` answers ok; the new `api` deployment shows the Gunicorn start command of 3.1; the `worker` log lists `billing.sweep` under `[tasks]`, then `ready`. | Coordinator | None | Verified |
| 8 | `beat`: created in the dashboard under its own approval, from this repository and the same branch as `api`, with the same variables as `api`, the values of 3.3 typed at creation, no domain. Then the task's `beat` checks: `Scheduler: Sending due task billing-sweep` every 5 minutes, one instance, and the worker log shows the task received and succeeded. | Coach Ed or the coordinator | Coach Ed, separately | `api` and `worker` unchanged |

**Going back.**
- Before step 6: nothing to undo for `api`. The old files still rule, and the typed values equal them. Step 5 is undone by typing `railway.worker.toml` back into `worker`'s path.
- After step 6: reverting the PR puts the three files back. Railway reads `railway.toml` again for services that used it, **until 2026-12-01**. After that date the files are not read at all, and the Settings values are the only source whatever the repository holds. The Settings values are not removed by a revert, so `api` has a start command in both cases.
- If step 7 fails for `api` only: revert the PR first (restores the running state within one deploy), then investigate.

## 5. One pull request

One PR, opened as a draft at step 1 and merged at step 6. With no project file there is nothing for a first PR to add that could merge early: the runbook text describes the state after the files are gone. Opening it early lets the tests run in CI and lets Coach Ed read the runbook table before he types from it.

## 6. Files changed by the PR

| File | Now | Change |
|---|---|---|
| `railway.toml`, `railway.worker.toml`, `railway.beat.toml` | The three old files | Deleted |
| `apps/billing/tests/test_tasks.py` | `RailwayFilesTests` (lines 165 to 196) parses the three files with `tomllib` | The class is replaced by one test: no `railway.toml`, `railway.json` or `railway.*.toml` exists at the repository root. `import tomllib` goes, as nothing else uses it; `ROOT` stays for the new test. |
| `docs/runbooks/billing.md`, "Worker and beat" | A "Config file" column naming the two files; step 2 sets the config file path; "neither service exists" | The section becomes the record. The table gains `api` and lists, per service: build command, start command, health path and timeout, restart policy and retries, instances, domain (the values of section 3). Step 2 becomes: "Type the service's values from the table into its Settings. Leave the config file path empty: Railway stopped reading `railway.toml` files." The opening sentence says `worker` exists and `beat` still needs its approval. A short "After changing a value" note: change the table in the same PR as the dashboard, and read the value back. The labels seen in the dashboard at step 3 are written into the table. |
| `README.md` line 25 | "`railway.toml` deliberately starts Gunicorn without running database migrations." | "The `api` service's start command is set in its Railway Settings and recorded in `docs/runbooks/billing.md`. It starts Gunicorn and deliberately does not run database migrations." Next sentence unchanged. |
| `CLAUDE.md` line 26 | "`railway.toml` starts Gunicorn and deliberately does not run migrations." | "Each Railway service's build and start commands are set in its Settings in the dashboard and recorded in `docs/runbooks/billing.md`; `api` starts Gunicorn and deliberately does not run migrations." Next sentence unchanged. |
| `docs/superpowers/specs/2026-11-09-deploy-flow-design.md` line 23 | The same sentence, in a list of how things stood on 2026-11-09 | The line stays, true for its date. One dated note under it: since Build B1a (2026-11) the settings live in the dashboard. |

Not changed (item 2.1): the plans `2026-11-10-backend-npc-build1-sms-gateway.md`, `2026-11-10-backend-npc-build6-admin-two-step-hq.md`, `2026-11-11-backend-billing-b1-subscription-lifecycle.md`, `2026-11-11-backend-npc-build9-hq-only-admin-door.md`, and the spec `2026-11-10-sms-gateway-mobile-verification-design.md` (line 152). No workflow under `.github/workflows/` names a Railway file.

Open pull requests when checked (2026-11-11): none. Checked again before the PR is opened.

## 7. Accepted when (the task's list, under the ruling)

- Staging `api` deploys from its Settings and `/healthz/` answers ok.
- Staging `worker` logs list `billing.sweep` under `[tasks]`, then `ready`.
- Once `beat` exists: the task's `beat` checks (step 8).
- No `railway*.toml` remains, and no current instruction or test names one.
- Tests pass in CI on SQLite and PostgreSQL.

## 8. What is still unknown

| # | Unknown | How it is settled |
|---|---|---|
| U1 | Whether a service that deployed from an old file uses its dashboard start command once the file is gone. The reference lists service settings as the lowest of three sources, which implies yes; no page says it for a file that was removed. | Step 7. If `api` fails, the PR is reverted. |
| U2 | Whether `worker` deploys correctly with an empty config file path. Its path is not honoured today, so clearing it should change nothing. | Step 5, on the first deploy after it; undone by typing the path back. |
| U3 | The dashboard's labels for build command, start command, health path, health timeout and config file path. No page prints them. | Seen at step 2 and recorded in the runbook. |
| U4 | Whether Settings shows a field for the restart retry count. The page confirms the three policy options and a default of 10 restarts for `On Failure`, and says paid plans "can set any restart policy with any number of restarts", but names no field. | Seen at step 2. If there is no field and the default of 10 applies, the value already equals the old files; the runbook says so. |
| U5 | What `api` and `worker` Settings hold today. Not read: this session ran no command that prints service settings and did not open the dashboard. | Step 2. |
| U6 | Whether these particular settings are staged (need "Deploy") or saved at once. The staged-changes page speaks of changes in general and excepts only networking. | Seen at step 3. |
| U7 | Why `worker` took `railway.toml` although its path names `railway.worker.toml`. The plan does not depend on the cause. | Not needed. |
| U8 | Whether a merge that only deletes files and changes documents and a test triggers a staging deploy of both services. It should, as any push to the deployed branch does. | Step 7 checks the deployment's time; if none started, a redeploy is asked for with approval. |

## 9. Choices made without asking

| Choice | Why | Source |
|---|---|---|
| One PR, opened as a draft early, merged after step 5 | Nothing could merge earlier without describing a state that does not exist yet | Brief, point 3 |
| The test replacement is one test that no Railway config file exists at the root | The smallest check that the old files do not come back; the values now live outside the repository and cannot be tested from it | Brief, point 4 |
| The runbook table includes `api` | The ruling makes the runbook the record of each service's values | Ruling |
| The config file path is cleared after the values are read back, not with them | One change at a time, each with its own read-back | Brief, point 2 |
| Health timeout typed in Settings, not set through the `RAILWAY_HEALTHCHECK_TIMEOUT_SEC` variable | The task allows no variable change | Task rules; healthchecks page |
| Plan file renamed to `…-railway-dashboard-settings.md` | The old title named the dropped route | Brief |
| Branch name kept | Renaming a branch helps no one and the task ID is in it | `/clickup-builder` |
