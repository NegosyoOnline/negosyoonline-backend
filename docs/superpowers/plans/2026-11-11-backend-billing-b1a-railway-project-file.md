# Billing Build B1a: move api, worker and beat to Railway's project file (plan)

Date: 2026-11-11. ClickUp task: z8uq9me1un (https://app.clickup.com/t/z8uq9me1un), parent z8uq9m8h46.
Branch: `chore/z8uq9me1un-railway-project-file`, cut from `origin/main` at `1ba9cc6`.
Status: **not approved.** This is step 1 of the task: a plan for Coach Ed. No `.railway/` file exists, no `railway*.toml` is changed, and nothing was written to Railway.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9me1un, description (no comments, attachments, checklists or custom fields; status Open when read) |
| Parent | ClickUp z8uq9m8h46: its 4 comments read in full; of its description (35,401 characters) only the headings and a search for "railway", "worker", "beat", "celery" were read. The search found nothing, so the parent sets no rule for this task beyond the task's own text. |
| Brief | The coordinator's brief for this builder, 2026-11-11 |
| IaC guide | https://docs.railway.com/infrastructure-as-code (read 2026-11-11) |
| IaC reference | https://docs.railway.com/infrastructure-as-code/reference |
| CLI page | https://docs.railway.com/cli/config |
| Old system | https://docs.railway.com/config-as-code |
| SDK types | npm package `railway` 3.16.0 (repository `railwayapp/railway-ts-sdk`), file `dist/index-CweUhAOc.d.ts`, read on unpkg.com |
| Installed CLI | `railway --version`, `railway config --help` and the `--help` of `plan`, `apply`, `init`, `pull`; `railway status` |
| Code | `railway.toml`, `railway.worker.toml`, `railway.beat.toml`, `apps/billing/tests/test_tasks.py`, `docs/runbooks/billing.md`, `README.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-11-09-deploy-flow-design.md`, `.gitignore`, at `1ba9cc6` |

The documentation pages were read through a summarising fetch tool, not as raw text. Field names quoted below were each confirmed by a second, targeted read. Wording is paraphrased unless in quotation marks.

## 2. Items for Coach Ed

The recommended answer is what the rest of the plan assumes. Nothing here is a ruling until he gives it.

### 2.1 The file is a named partial that declares the three services only

- **Checked.** IaC guide: in a one-file project with no partial name, a resource left out of the file is deleted on apply. A file that exports a partial name "manages only the resources it declares", deletes only what it owns, and leaves unowned resources alone. The guide recommends one file with no partial name for a one-repository project.
- **Options.**
  - (a) **Recommended.** `export const partial = "backend"`. The file declares `api`, `worker`, `beat` and nothing else. PostgreSQL and Redis are not in it and are not owned by it.
  - (b) No partial name. The file must then also declare PostgreSQL, Redis, every variable (as `preserve()`, names only) and every domain, or apply deletes them.
- **Consequence.** (a) keeps the task's rule that the file declares no variable, database, domain or volume, and goes against the guide's default for one repository. (b) breaks that rule and makes every future variable a file change. With (a), whether a declared service's own variables and domains are left alone when the file gives no `env` and no `domains` is not documented (section 8, U1); the first `railway config plan` answers it, and a plan that shows any delete or any variable or domain change is stopped and reported.

### 2.2 Update the Railway CLI from 5.30.3 to the current release before the first plan

- **Checked.** Installed: 5.30.3 (Homebrew reports 5.71.0 as current). It has `railway config plan | apply | init | pull` and no `migrate` and no `partials` subcommand; `plan` has no `--out` and `apply` has no `--plan`, all of which the CLI page documents. The reference ties one newer field to "CLI 5.63.0 or newer". Whether 5.30.3 honours a partial name at all is unknown; if it ignored it, the file would be treated as the whole project.
- **Options.** (a) **Recommended.** `brew upgrade railway` on Coach Ed's computer, by him or by the coordinator with his approval. (b) Stay on 5.30.3.
- **Consequence.** (a) changes a tool on his computer. (b) leaves the partial name, the safety of 2.1, resting on an unverified version.

### 2.3 The Node package the file needs lives inside `.railway/`

- **Checked.** The file imports `railway/iac`, and the CLI evaluates it through a runner, `railway-iac-ts`, which is a binary of the npm package `railway` (needs Node 22 or newer; this computer has 22.23.1). The runner is not installed here. The guide installs the package at the repository root. This repository has no `package.json`.
- **Options.**
  - (a) **Recommended.** `.railway/package.json` (private, one dev dependency, `railway` pinned to `3.16.0`) and `.railway/package-lock.json`, with `.railway/node_modules/` added to `.gitignore`. The runner is then `.railway/node_modules/.bin/railway-iac-ts`, passed with `--runner` if the CLI does not find it.
  - (b) `package.json` at the repository root, as the guide shows.
- **Consequence.** (b) puts a Node marker at the root of a Python service, where Railway's builder could start treating the repository as a Node project. (a) avoids that; whether the CLI finds a runner there by itself is unknown (U5), and `--runner` covers it. Either way the repository gains two small files and CI is unchanged.

### 2.4 `beat` is created the way `worker` was, then added to the file

- **Checked.** Applying a file that declares a service that does not exist creates it. A new service needs a source (repository and branch) and variables to run; declaring those is what the task forbids without approval. Omitting `source` is documented as managing a service's settings "without declaring a repo or image".
- **Options.**
  - (a) **Recommended.** The file merged first declares `api` and `worker`. When Coach Ed approves creating `beat`, it is created in the dashboard as the runbook describes (same repository and branch as `api`, same variables, no domain), and the `beat` block of section 5 is added in a small follow-up PR and applied.
  - (b) The file declares `beat` with a source and variable references, and apply creates it.
- **Consequence.** (a) keeps the file free of variables and keeps the creation a separate approval. (b) needs the plan to name every variable `beat` gets.

### 2.5 Two pull requests

- **Recommended.** PR 1 adds the project file, its package files, its tests and the runbook section, and leaves the three `railway*.toml` files in place: no service changes. PR 2 removes the three files and updates the documents and tests that name them, and merges only after step 4 of section 6. One PR could carry both, but then the removal could not be reverted alone.

## 3. What the documentation says

### 3.1 Environment scope

- `plan`, `apply` and `pull` act on the project and environment linked to the current directory (`railway link`). Plan output names the environment. No in-file setting that pins a file to one environment is documented. A partial's ownership is stored on the Railway environment. (IaC guide.)
- The program receives a context with `ctx.environment`, `ctx.environmentName` and `ctx.isEnvironment(name)`. (IaC reference; SDK types.)
- This worktree is linked to project `negosyoonline-backend`, environment `staging`, service `worker` (`railway status`).
- **Plan:** the file refuses any environment but staging: it throws unless `ctx.isEnvironment("staging")`. The runbook also has the reader run `railway status` first. Production has no services and is never linked.

### 3.2 Per-service settings

| Setting | Old file key | Project file | Source |
|---|---|---|---|
| Build command | `build.buildCommand` | `build: "…"` | IaC reference |
| Start command | `deploy.startCommand` | `start: "…"` | IaC reference |
| Health path | `deploy.healthcheckPath` | `healthcheck: "/healthz/"` | IaC reference |
| Health timeout | `deploy.healthcheckTimeout` | `healthcheckTimeout: 120` (unit not stated; the old key was seconds) | IaC reference |
| Restart policy | `deploy.restartPolicyType`, `deploy.restartPolicyMaxRetries` | `deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 10 }` | **SDK types only.** The documentation pages do not mention restart policy at all. |
| Replica count | `deploy.numReplicas` | `replicas: 1` | IaC reference |

Not declared, so not managed by the file: `source` (repository, branch and the "Wait for CI" switch, which the SDK types spell `checkSuites` on the source), `env`, `domains`, `volumeMounts`, region.

### 3.3 What a file can leave unmanaged

- **Databases and other services:** with a partial name, left alone (2.1). Without one, deleted.
- **Volumes:** the reference calls deleting or detaching a volume destructive; nothing in this file declares or mounts one.
- **Variables of a declared service, with no `env` in the file:** not documented. `preserve()` exists to keep one variable's value on Railway without writing it in the file, and `pull` writes existing variables that way, which suggests variables may be compared when a service is declared. U1.
- **Domains of a declared service, with no `domains` in the file:** not documented. U1.
- **Safety nets that are documented:** plan is read-only and redacts values; deletes are marked before confirmation; a run without a prompt needs `--confirm-destructive` to delete anything; apply makes a fresh plan and rejects a stale one.

### 3.4 Moving off the old files

- A service cannot be managed by both systems. While a service "still has a Config as Code file", `railway config plan` stops and names it. (IaC guide.)
- An old file is read at deploy and overrides the dashboard values, until 2026-12-01. (IaC guide; Old system.)
- The guide's manual order: write the file, delete the old files, clear the config file path in each service's Settings ("otherwise deploys may keep reading the file"), plan, apply.
- `railway config migrate --apply` does the same in one command, including clearing the path. Not used here: the installed CLI lacks it, and one command that writes settings and clears paths together cannot be approved step by step.
- Railway does not read `.railway/railway.ts` at deploy. Apply writes the service's settings; later deploys use those settings.

## 4. What the installed CLI offers

`railway 5.30.3`, at `/opt/homebrew/bin/railway`.

| Command | Present | Flags (from `--help`) |
|---|---|---|
| `config plan` | Yes | `--file`, `--json`, `--yes`, `--confirm-destructive`, `--decrypt-variables`, `--include-types`, `--runner`, `--verbose`, `--detailed-exit-code`, `--show-values` |
| `config apply` | Yes | the same list |
| `config init` | Yes | `--force` |
| `config pull` | Yes | `--force`, `--json`, `--runner`, `--omit-preserved-variables`, `--agent` |
| `config migrate` | **No** ("unrecognized subcommand") | documented: `--apply`, `--delete-files`, `--service`, `--force`, `--lang` |
| `config partials` | **No** ("unrecognized subcommand") | documented: `list`, `release`, `transfer` |

The runner defaults to `RAILWAY_IAC_TS_BIN` or `railway-iac-ts`; neither is present on this computer. Never to be used here: `--show-values`, `--decrypt-variables`, `pull --include-variables`.

## 5. The proposed file

`.railway/railway.ts`. Every command string is copied from the file named beside it, at `1ba9cc6`.

```ts
import { defineRailway, project, service } from "railway/iac";

// Staging only. This file owns the settings of the three backend services and
// nothing else: no variable, database, domain or volume is declared here.
// Railway does not read this file at deploy. `railway config apply` writes it
// to the service settings. See docs/runbooks/billing.md, "Railway project file".
export const partial = "backend";

const restart = { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 10 } as const;

// From railway.toml. No migration in the start command.
const api = service("api", {
  build: "uv sync --frozen",
  start:
    "uv run gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --access-logfile - --error-logfile -",
  healthcheck: "/healthz/",
  healthcheckTimeout: 120,
  deploy: restart,
});

// From railway.worker.toml. No domain and no health path.
const worker = service("worker", {
  build: "uv sync --frozen",
  start: "uv run celery -A config worker --loglevel=info --concurrency=2",
  deploy: restart,
});

// From railway.beat.toml. Exactly one instance, or every job is queued twice.
const beat = service("beat", {
  build: "uv sync --frozen",
  start: "uv run celery -A config beat --loglevel=info --schedule=/tmp/celerybeat-schedule",
  replicas: 1,
  deploy: restart,
});

export default defineRailway((ctx) => {
  if (!ctx.isEnvironment("staging")) {
    throw new Error("This file is for the staging environment only.");
  }
  return project("negosyoonline-backend", { resources: [api, worker, beat] });
});
```

- Under 2.4 (a), PR 1 carries this file without the `beat` block and with `resources: [api, worker]`; the block is added once `beat` exists.
- `"$PORT"` is inside an ordinary string, so TypeScript does not expand it.
- `api` and `worker` declare no `replicas`, as their old files declare none.
- Beside it: `.railway/package.json`, `.railway/package-lock.json` (2.3), and `.railway/node_modules/` in `.gitignore`.

## 6. Order of steps

`api` keeps a start command at every step because its service settings are made equal to `railway.toml` **before** `railway.toml` is removed, while the old file is still the one Railway reads.

| # | Step | Who | Approval | State of `api` afterwards |
|---|---|---|---|---|
| 1 | PR 1: project file, package files, tests, runbook section. The three `railway*.toml` stay. Merge; staging deploys as today. | Builder opens; merge as usual | None beyond the PR | Reads `railway.toml`, unchanged |
| 2 | Update the CLI (2.2). In the worktree: `npm ci` inside `.railway/`, `railway status` (must say staging), then `railway config plan --verbose`. | Coordinator | Coach Ed, for the CLI update and for running plan | Unchanged; plan is read-only |
| 3 | Read the plan output. Stop and report if it shows any delete, any variable, domain, volume or source change, or any service other than `api` and `worker`. | Coordinator, builder reads | None | Unchanged |
| 4a | If plan ran: `railway config apply`, which writes build, start, health and restart settings to `api` and `worker`. | Coordinator | Coach Ed, with the plan output in hand | Settings now equal `railway.toml`; deploys still read `railway.toml`, which says the same |
| 4b | If plan stopped and named a service still on the old system: the same values are typed into each service's Settings in the dashboard instead (build command, start command, health path and timeout for `api`, restart policy). | Coach Ed or the coordinator | Coach Ed | As 4a |
| 5 | In each service's Settings, clear the config file path: `worker` has `railway.worker.toml` set, which Railway does not honour today; `api` is checked and cleared if set. Then read back every setting of step 4. | Coach Ed or the coordinator, in the dashboard | Coach Ed | Unchanged: settings and `railway.toml` agree |
| 6 | PR 2: delete the three `railway*.toml`, update the tests and documents of section 7. Merge only after step 5 is confirmed. Staging deploys `api` and `worker` with no old file. | Builder opens; merge as usual | None beyond the PR | Starts Gunicorn from its service settings |
| 7 | Check: `/healthz/` on `https://api-staging.negosyonline.com` answers ok; the new `api` deployment shows the Gunicorn start command; `worker` logs list `billing.sweep` under `[tasks]`, then `ready`. | Coordinator | None (read-only) | Verified |
| 8 | `railway config plan` again. Expected: nothing to change. If step 4b was used, this is the first plan that can run; apply then records the partial as owner. | Coordinator | Coach Ed for any apply | Unchanged |
| 9 | `beat`: created under its own approval (2.4), block added to the file in a follow-up PR, plan, apply, then the task's `beat` checks. | Coordinator; builder for the PR | Coach Ed, each | Unchanged |

Going back: before step 6, nothing needs undoing; the old files still rule. After step 6, reverting PR 2 restores the old files, which Railway reads again until 2026-12-01; the service settings stay equal to them either way. "Wait for CI" is not touched at any step, and step 3 checks that plan shows no source change.

## 7. Files that name a `railway*.toml`

| File | Now | Change | PR |
|---|---|---|---|
| `apps/billing/tests/test_tasks.py` | `RailwayFilesTests` parses the three files with `tomllib` (5 uses, lines 165 to 196) | PR 1 adds tests that read `.railway/railway.ts` as text: the three start commands exactly, the build command, `/healthz/` and `120`, `replicas: 1` for `beat` once its block exists, the restart values, the staging guard, and that the file contains no `migrate`, `env`, `domains`, `volume`, `postgres(` or `redis(`. PR 2 removes the `tomllib` tests and adds one that no `railway*.toml` exists at the root. | 1 and 2 |
| `docs/runbooks/billing.md` | "Worker and beat": a "Config file" column naming the two files; step 2 sets the config file path | PR 1 adds a section "Railway project file": install, check the linked environment, preview, apply, the stop conditions of step 3, and the checks of step 7. PR 2 drops the "Config file" column, replaces step 2 with "the service's settings come from `.railway/railway.ts`; leave the config file path empty", and removes the sentence that neither service exists, for `worker`. | 1 and 2 |
| `README.md` line 25 | "`railway.toml` deliberately starts Gunicorn without running database migrations." | "`.railway/railway.ts` holds the start command of each Railway service. The `api` service starts Gunicorn and deliberately does not run database migrations." Next sentence unchanged. | 2 |
| `CLAUDE.md` line 26 | "`railway.toml` starts Gunicorn and deliberately does not run migrations." | "`.railway/railway.ts` holds each Railway service's settings; `api` starts Gunicorn and deliberately does not run migrations. Railway does not read the file at deploy: it is applied with `railway config apply`, which needs Coach Ed's approval each time." Next sentence unchanged. | 2 |
| `docs/superpowers/specs/2026-11-09-deploy-flow-design.md` line 23 | The same sentence, in a list of how things stood on 2026-11-09 | The line stays true for its date. One dated note is added under it: since Build B1a the settings live in `.railway/railway.ts`. | 2 |

Named for completeness and **not** changed, because each is a dated record or describes behaviour, not a current instruction: the plans `2026-11-10-backend-npc-build1-sms-gateway.md`, `2026-11-10-backend-npc-build6-admin-two-step-hq.md`, `2026-11-11-backend-billing-b1-subscription-lifecycle.md`, `2026-11-11-backend-npc-build9-hq-only-admin-door.md`, and the spec `2026-11-10-sms-gateway-mobile-verification-design.md` (line 152). The task's "no document names one as current" is read as covering current instructions; if Coach Ed wants these five edited too, that is one more line in PR 2. No workflow under `.github/workflows/` names a Railway file.

Open pull requests when checked (2026-11-11): none, so no overlap.

## 8. What is still unknown

| # | Unknown | How it is settled |
|---|---|---|
| U1 | Whether a declared service with no `env` and no `domains` keeps its variables and domains, or has them proposed for removal. | The plan output of step 2. Any such line stops the work. The fallback would be `preserve()` for each variable name, which needs Coach Ed's approval because the file would then name variables. |
| U2 | Whether the restart policy is accepted as `deploy: { restartPolicyType, restartPolicyMaxRetries }`. It is in the SDK's types and in no documentation page. | The runner rejects or accepts it at step 2. `railway config pull --json` on staging would show how Railway itself writes it, but may print variable names, so it needs approval. |
| U3 | Whether plan stops while `railway.toml` is in the repository, and whether it judges that from the local folder or from Railway's last deployment. | Step 2; steps 4a and 4b cover both answers. |
| U4 | Whether CLI 5.30.3 honours a partial name. | Avoided by 2.2. |
| U5 | Whether the CLI finds the runner under `.railway/node_modules/`. | `--runner` at step 2. |
| U6 | Whether a setting the file does not declare (region, replica count of `api` and `worker`, builder) is left as it is or reset. | The plan output of step 2 lists every change. |
| U7 | What the `api` service's Settings hold today (config file path, dashboard start command). Not read: this session ran no command that prints service settings. | Read in the dashboard at step 5, before anything is cleared. |
| U8 | The unit of `healthcheckTimeout` in the project file. The old key was seconds and the value is carried as 120. | The plan output shows the value Railway would store. |
| U9 | Why `worker` took `railway.toml` although its path names `railway.worker.toml`. The task records the fact; the cause was not investigated and the plan does not depend on it. | Not needed. |

## 9. Choices made without asking

| Choice | Why | Source |
|---|---|---|
| Staging guard that throws in any other environment | The only in-file way to scope found; fails closed | IaC reference, `ctx.isEnvironment` |
| No `source` on any service | Leaves repository, branch and "Wait for CI" unmanaged | IaC reference; task rule on "Wait for CI" |
| SDK pinned to exactly `3.16.0` with a lock file | A reproducible plan; the version whose types were read | npm registry |
| Tests read the file as text | No TypeScript parser in the Python tests; CI stays as it is | Task: no CI change beyond what the tests need |
| `railway config migrate` not used | Absent from the installed CLI; does several writes in one command | CLI `--help`; IaC guide |
| Partial named `backend` | Names the repository's services as a group; allowed characters | IaC guide |
| Branch `chore/z8uq9me1un-railway-project-file` | Task ID in the branch name | `/clickup-builder` |
