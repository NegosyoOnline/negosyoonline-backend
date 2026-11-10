# NPC Build 6: admin two-step sign-in and hq (plan)

Date: 2026-11-10. ClickUp task: z8uq9mdwdx (https://app.clickup.com/t/z8uq9mdwdx), parent z8uq9mbreu.
Branch: `feat/z8uq9mdwdx-admin-two-step-hq`, cut from `origin/main` at `8f51da3`.
Status: **plan only. Nothing here is approved.** No code, migration, dependency or variable exists yet. Section 7 lists what Coach Ed must approve; section 3 lists the points where the approved design and the new codebase disagree and a ruling is needed.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9mdwdx, description (no comments, no attachments as of 2026-11-10) |
| Parent | ClickUp z8uq9mbreu: rulings D4, D6, R-9 and the wording rules (admin, subscribers, the subscriber's customers) |
| Design | Legacy repository, read-only: `.claude/docs/superpowers/specs/2026-11-06-negosyoonline-npc-phase4-admin-two-step-hq-design.md` (screens S1 to S9) |
| Spec | Same folder: `2026-11-06-negosyoonline-npc-phase4-admin-two-step-hq-spec.md` (sections 3 to 6) |
| Scope doc | `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md` at `8f51da3` (includes PR 39's Account Settings amendment) |
| Code | `apps/operators`, `apps/accounts`, `apps/notifications`, `apps/common`, `config/settings.py`, `config/urls.py` at `8f51da3` |

The legacy build on branch `claude/npc-phase4-admin-two-step-319a73` was read for behaviour in three places only (what `add` does for an existing address, that `enrol` needs an interactive terminal, and the meaning of the two emails). No code is copied.

## 2. Scope, traced

### 2.1 Deliverables

| # | Task item | Detail comes from | Built as (section 4) |
|---|---|---|---|
| 1 | Admin identity with encrypted authenticator secret and recovery codes | Spec 3.2, 3.4 | `AdminIdentity`, `AdminRecoveryCode`; secret encrypted with the secrets key |
| 2 | One shared second-step check for hq and the Django admin: no replay, shared lockout, lockout notice by email | Spec 3.3 | `two_step.verify_second_step()`, called by both surfaces |
| 3 | Terminal command to add, enrol, reset, disable and list; every action except list audited | Spec 3.4, 3.7a "Command actor" | `manage.py admin_identity` |
| 4 | Audit record of admin sign-ins, failures, lockouts and sign-outs | Task item 4; scope doc section 6; Spec 3.10 | `OperatorAuditEntry` and `audit.record()` |
| 5 | Two-step on the Django admin behind a switch, off by default | Spec 3.6, 3.7a | `TwoStepAdminSite`, setting `ADMIN_TWO_STEP_REQUIRED` |
| 6 | hq host routing, S1 to S4, S5, S6, S7, S8 | Design section 2; Spec 3.5, 3.7 | `HqHostMiddleware`, hq views and templates, admin code views |

### 2.2 Acceptance criteria

| Task "Accepted when" | How it is met | Proved by (section 9) |
|---|---|---|
| Both steps required on hq and the Django admin; no hq page reachable without both | hq: stage order `pre cookie -> email_ok -> verified`, checked on every request. Django admin: `has_permission` needs the password session and a valid two-step flag when the switch is on | T-HQ-1 to 6, T-ADM-1 to 5 |
| A wrong, replayed or used code is refused on both | One verifier; `totp_last_step`; recovery `used_at`; email code single use | T-VER-1 to 7, T-HQ-7, T-ADM-6 |
| Five wrong codes lock both surfaces for 15 minutes and end live sessions | 5th failure sets `locked_until`, raises `credential_version`, revokes hq sessions; the Django admin flag carries the old version and is refused | T-VER-8 to 11, T-ADM-7 |
| Enrolment impossible without the command, and impossible with an empty secrets key | No view, form or admin page writes an identity; the command refuses with an empty or invalid key | T-CMD-3, T-CMD-9, T-BND-1 |
| Same answer for a known and an unknown address | The request does the same work for every well-formed address; lookup and mail happen off the request (C1) | T-HQ-8 to 11 |
| Tests pass in CI | Existing two CI jobs (SQLite; PostgreSQL and Redis) | CI run on the PR |
| Coach Ed passes S1 to S8 locally, then on staging, desktop and phone | Section 10 locally; staging needs the later approvals in 7.2 | Coach Ed |

### 2.3 Boundaries

Not built (Task "Not in this task"; Design section 6): the health page, hq settings screens, provider-console two-step, passkeys, any browser path to enrol, reset or disable, IP or device in the activity list, any script or external asset, dark mode. Also not built here: the D4 blanking job (Build 7), and any Django admin list for the new tables (the task does not ask for one).

## 3. Where the approved design and the new codebase disagree

Each needs a ruling. Until ruled, the recommended answer is what the rest of this plan assumes.

| # | Design says | New codebase says | Recommended |
|---|---|---|---|
| C1 | The emailed code and the lockout notice are sent by a named Celery task, never inline, so response time does not reveal a known address (Spec 3.5 item 1, 3.7a) | There is no task queue. Scope doc section 3: the worker and beat services "are added with slice 4, not before". `apps/notifications/services.py:send_email` sends inside the request | Do the address lookup, the challenge row and the send in a short background thread started after the response is decided. The request then does identical work for every address. One function (`background.run_later`) is the only place that knows, so slice 4 can swap it for a task. Risk: a restart of the web process in that moment loses one email; the admin asks for a new code. Not recommended: sending inline (slow, and leaks which address is known), or waiting for slice 4 |
| C2 | The challenge row keeps the code encrypted (`code_ciphertext`) so the task can read it (Spec 3.2) | With C1 the code is handed to the thread in memory | Drop `code_ciphertext`. Only the HMAC of the code is stored. Less secret material at rest |
| C3 | `enrol` prints a QR code using `segno` (Spec 3.4, F4) | `segno` is not in `pyproject.toml` or `uv.lock`, and the task lists only `cryptography` for approval | No second dependency: `enrol` prints the setup key (to type into the authenticator app) and the `otpauth://` setup link. Alternative for Coach Ed: approve `segno` (pure Python, no further dependencies) to get the QR in the terminal |
| C4 | Command actions and the empty-key case raise an operator alert; an hourly task alerts when the switch is off while an enrolled identity exists (Spec 3.4, 3.6, 3.7a) | No alert table and no beat service exist; neither is in the scope doc or the task | Write an ERROR log line with a fixed identifier (the empty-key one at most once an hour), plus the audit row for command actions. `admin_identity list` prints the switch state and warns when it is off while an enrolled identity exists. No hourly job |
| C5 | Challenges are purged 24 h after expiry and sessions 30 days after expiry or revocation by an hourly task (Spec 3.10) | No beat service | Purge in the same background step that issues a new challenge: one cheap delete each. No new job |
| C6 | The identity's link to a user is optional (`twc_user_id` nullable) and Django admin entry needs staff plus an `OperatorGrant` (Spec 3.2, 3.6) | Task: "The identity links to the new `operators.User`". There is no `OperatorGrant`; the Django admin admits any active staff user | Link required: `add EMAIL --user USERNAME`, where the user is an active staff `operators.User`. One active identity per user. "Staff-and-grant" becomes "active staff user" everywhere. `--actor` is an active staff user |
| C7 | Audit rows carry `actor_kind` (`twc_user` or `admin_identity`) and `actor_username` (Spec 3.2, 3.10) | Scope doc section 6: `OperatorAuditEntry`: operator, action, target, reason, at. There is no TWC user | No `actor_kind`. `operator` is the `operators.User`; with C6 every identity has one. Shape in section 8 |
| C8 | Two mounts for the operator admin, on its own host (Spec 3.6 "both mounts", F2) | One Django admin at `/admin/` on whatever host the API answers (`config/urls.py:29`); no host routing exists | One mount. The code page is `/admin/two-step/`, the sign-out is `/admin/two-step/sign-out/`. No operator-host setting is added. hq is the only host-routed surface |
| C9 | Unknown hq paths use "the existing neutral handlers" (Design S9) | None exist | The hq URL set carries its own four handlers, rendered in the hq shell, with the Design S9 sentences ("Page not found." / "This page is not available right now.") |
| C10 | The wording of the two emails is not in the Design or the Spec | - | Proposed text in 7.1 item A6, for Coach Ed's approval |
| C11 | Identity email is at most 150 characters, to fit the legacy audit column (Spec 3.2) | That column does not exist here | 254, the same limit as the S1 field |
| C12 | hq and the operator host must differ from the API hosts (startup check, Spec 3.7) | The API has no host setting; it answers on every allowed host | Startup check: `HQ_HOST` is a bare host name, is not the host of `DASHBOARD_URL`, `SITE_URL` or any `API_ALLOWED_ORIGINS` entry, and if it sits under `SITE_PARENT_HOST` its label is reserved (`hq` already is, `apps/sites/subdomains.py:11`). A failed check logs an error and hq is inert |

Also stale in the scope doc beyond line 32: line 107 lists "the second admin console" under "Not rebuilt from legacy". The amendment in section 6 corrects both. Line 107 is in section 6 of that document (the data model), next to the operators entry.

## 4. Design in the new backend

Everything lives in `apps/operators`. Other apps are not changed. hq uses no Django auth session.

### 4.1 Data (one migration, `operators.0002`, additive)

- `AdminIdentity`: `user` (FK `operators.User`, PROTECT), `email` (unique, lowercase, max 254), `credential_version` (starts 1), `totp_secret_ciphertext`, `totp_last_step`, `failed_attempts`, `locked_until`, `is_active`, `enrolled_at`, `created_at`, `updated_at`. Constraint: one active identity per user.
- `AdminRecoveryCode`: `identity`, `code_hash`, `used_at`.
- `AdminSignInChallenge`: `identity`, `code_hmac`, `nonce`, `created_at`, `expires_at` (10 min), `attempts` (max 5), `consumed_at`.
- `AdminSession`: `identity`, `token_hash` (SHA-256, unique), `credential_version`, `stage` (`email_ok`, `verified`), `created_at`, `verified_at`, `expires_at` (12 h from creation), `revoked_at`.
- `OperatorAuditEntry`: section 8.

Personal data held: the admin's work email (on the identity, and in the `EmailMessage` record that `send_email` already writes for every email).

### 4.2 Modules

| Module | Job |
|---|---|
| `crypto.py` | Encrypt and decrypt with `ADMIN_SECRETS_KEY` (Fernet). `key_ready()` is false for an empty or invalid key |
| `totp.py` | RFC 6238 (SHA-1, 6 digits, 30 s) with the standard library |
| `audit.py` | `record()`, the action registry, `recent_sign_in_events(identity, limit=10)` |
| `two_step.py` | `verify_second_step(identity_id, code, surface)` exactly as Spec 3.3; recovery codes; credential version bump; email challenge issue and check (Spec 3.5 item 2, row lock, tries counted before comparing); hq sessions; the Django admin flag (Spec 3.6, 3.7a) |
| `background.py` | `run_later(fn, *args)`: runs after the response in a thread, closes its database connection, catches and logs every error (C1). Tests replace it with a direct call |
| `middleware.py` | `HqHostMiddleware`: when the request host equals `HQ_HOST`, serve only the hq URL set and add `Cache-Control: no-store` and `X-Robots-Tag: noindex` to every response, including errors and the CSRF 403 |
| `hq_urls.py`, `hq_views.py` | `/sign-in/`, `/code/`, `/verify/`, `/`, `/sign-out/` and the four neutral handlers |
| `admin_site.py` | `TwoStepAdminSite`: `has_permission` and `login` as Spec 3.6 and 3.7a. With the switch off both behave as Django's own |
| `admin_views.py` | `/admin/two-step/` (S7, S8, S4) and `/admin/two-step/sign-out/` (POST) |
| `checks.py` | The `HQ_HOST` startup check (C12) |
| `management/commands/admin_identity.py` | `add EMAIL --user USERNAME --actor USERNAME`, `enrol`, `reset`, `disable` (each with `--actor`), `list`. `enrol` refuses without an interactive terminal or with an empty key. `add` refuses an address that already exists, disabled or not |
| `templates/hq/` | `base.html` (the one shell with the inline style), `sign_in`, `code`, `verify`, `paused`, `home`, `two_step`, `not_set_up`, `neutral`. On-screen text is the Design's, word for word |

### 4.3 Behaviour notes that differ in mechanism only

- Pages are rendered with `render_to_string(template, context)` and an explicit CSRF token, as the Design rulebook says.
- Cookies: `__Host-no_hq` and `__Host-no_hq_pre` (Secure, HttpOnly, SameSite=Strict, Path `/`); the plain names `no_hq` and `no_hq_pre` only under `DEBUG`.
- Limits at S1: 5 per hour per normalised address and 5 per hour per IP, counted before anything else, with a private counter in `apps/operators` (the shared `apps/common/throttle.py` raises a 429, which the Design forbids here). The IP comes from `apps.accounts.sessions.client_ip`.
- The Django admin is switched to the new site class by replacing `'django.contrib.admin'` in `INSTALLED_APPS` with an `AdminConfig` subclass whose `default_site` is `TwoStepAdminSite`. Every existing `admin.site.register` and `@admin.register` keeps working; no other app's `admin.py` is touched.
- Audit on the Django admin covers the code step and sign-out. Password failures on Django's own login form are not audited (the Design audits code steps only).
- Internal surface values are `hq` and `django_admin`; S5 shows them as "Admin console" and "Operator admin".
- Carried from the Design as an accepted risk: at S2 a known address does a little more database work than an unknown one.

## 5. Files

Created, all under `apps/operators/` unless a full path is given:
`crypto.py`, `totp.py`, `audit.py`, `two_step.py`, `background.py`, `middleware.py`, `hq_urls.py`, `hq_views.py`, `admin_site.py`, `admin_views.py`, `checks.py`, `management/__init__.py`, `management/commands/__init__.py`, `management/commands/admin_identity.py`, `migrations/0002_admin_two_step_and_audit.py` (approval A1), `templates/hq/` (9 files, 4.2), `tests/__init__.py`, `tests/test_user.py` (today's `tests.py`, moved unchanged), `tests/test_totp.py`, `tests/test_crypto.py`, `tests/test_audit.py`, `tests/test_two_step.py`, `tests/test_command.py`, `tests/test_hq.py`, `tests/test_admin_two_step.py`, `tests/test_boundary.py`; and `docs/runbooks/admin-two-step.md` (release order, enrol, lost phone, switching on and off).

Changed:

| File | Change |
|---|---|
| `apps/operators/models.py` | The five models in 4.1 |
| `apps/operators/apps.py` | `ready()` imports the checks; adds the `AdminConfig` subclass |
| `apps/operators/tests.py` | Removed (moved to `tests/test_user.py`) |
| `config/settings.py` | Three settings (A3); `HQ_HOST` appended to `ALLOWED_HOSTS` when set; `INSTALLED_APPS` admin entry; `HqHostMiddleware` after `SecurityMiddleware` |
| `config/urls.py` | Two paths before `admin/`: `admin/two-step/` and `admin/two-step/sign-out/` |
| `pyproject.toml`, `uv.lock` | `cryptography` (A2) |
| `.env.example`, `README.md` | The three variables; one layout line for hq |
| `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md` | Section 6 below |

Not changed: `apps/accounts/`, `apps/notifications/`, `apps/common/`, `apps/sites/`, `config/api.py`, `railway.toml`, CI.

Shared files another session may also touch: `config/settings.py`, `config/urls.py`, `pyproject.toml`, `uv.lock`, `README.md`, `.env.example`, the scope doc. Open PRs checked 2026-11-10: none.

## 6. Scope doc amendment (this area only; PR 39's text is kept)

**Line 32**, replace the last sentence ("Operators use one Django admin; the legacy second console is not rebuilt.") with:

> Operators use the Django admin. The admin's console, hq, is rebuilt by Coach Ed's ruling of 2026-11-10 (ClickUp task z8uq9mdwdx): Django renders it on its own host, with no script and no external asset. hq and the Django admin both require two-step sign-in; see section 12.

**Section 6, operators**, replace the two lines with:

> - `User`: Django staff user.
> - `OperatorAuditEntry`: operator, action, target, reason, detail, personal values and when they were blanked, at. Rows are never deleted.
> - `AdminIdentity`: user, email (unique), encrypted authenticator secret, last used time step, failed attempts, locked until, credential version, active, enrolled_at.
> - `AdminRecoveryCode`: identity, code hash, used_at.
> - `AdminSignInChallenge`: identity, code HMAC, nonce, expires_at, attempts, consumed_at.
> - `AdminSession`: identity, token hash, stage, credential version, verified_at, expires_at, revoked_at.

**Line 107**, remove "the second admin console, " from the "Not rebuilt from legacy" list.

**Section 12**, add:

> - 2026-11-10: admin two-step sign-in and hq, from Coach Ed's rulings of 2026-11-10 on ClickUp task z8uq9mdwdx (the NPC work is rebuilt in this backend; hq is rebuilt) and the design he approved on 2026-11-06. Sections 4 and 6 were updated.
>   - hq sign-in is an emailed 6-digit code, then an authenticator code or one recovery code. Django admin sign-in is the password, then an authenticator code or one recovery code. One check serves both.
>   - Sessions last 12 hours; the email code 10 minutes; five wrong codes pause sign-in on both for 15 minutes and end live sessions; ten one-time recovery codes.
>   - Identities are added, enrolled, reset and disabled only with a terminal command. No browser path exists.
>   - The first hq page shows the verified card, the admin's last 10 sign-in events and Sign out. The health page and settings screens are not in the MVP.
>   - Django admin enforcement sits behind `ADMIN_TWO_STEP_REQUIRED`, off by default. hq answers only on `HQ_HOST`; empty means hq is off.
>   - Until slice 4 adds the worker, the sign-in code and the lockout notice are sent from a background thread of the web service.

(The last bullet is included only if C1 is ruled as recommended.)

## 7. Approvals

### 7.1 Needed before any code is written (Coach Ed, relayed by the coordinator)

- **A1. Migration.** One additive migration, `operators.0002_admin_two_step_and_audit`, owned by `apps/operators`: creates the five tables in 4.1, changes no existing table. Approval asked for: creating the file, and applying it to this worktree's local SQLite file for the local check. Applying it to staging is a separate later approval (7.2).
- **A2. Dependency.** `cryptography`, for encrypting the authenticator secret at rest. Nothing in `uv.lock` covers the need: the lock holds Django, django-ninja, django-redis, gunicorn, psycopg, dj-database-url and their dependencies; Django signs values but does not encrypt them, and Python's standard library has no authenticated encryption. Proposed constraint: the current major release at the time of `uv lock`, pinned to that major; the exact version and its two transitive packages (`cffi`, `pycparser`) will be reported from the lock diff. TOTP itself needs no package.
- **A3. Variables.** Names follow this repository's unprefixed style.

  | Name | Meaning | Default | Local value for the check |
  |---|---|---|---|
  | `HQ_HOST` | The one host that serves hq. Empty: hq is off | empty | `hq.negosyotest.com` |
  | `ADMIN_SECRETS_KEY` | Fernet key for authenticator secrets. Empty: enrolment refused, hq sends nothing | empty | generated on Coach Ed's computer into the ignored `.env`; never printed, committed or pasted |
  | `ADMIN_TWO_STEP_REQUIRED` | `1` turns enforcement on for the Django admin | off | `1`, so S7 and S8 can be seen |

  The existing local `DJANGO_ALLOWED_HOSTS` also needs `ggwp.negosyotest.com` for the Django admin check. No Railway variable is touched by this approval.
- **A4. Local reach.** hq at `http://hq.negosyotest.com:8003/` and the Django admin at `http://ggwp.negosyotest.com:8003/admin/`, both served by one local `runserver` on port 8003 from this worktree, local SQLite, environment "local". **`hq.negosyotest.com` is not a designated local name and needs a new `/etc/hosts` line on Coach Ed's computer: `127.0.0.1 hq.negosyotest.com`.** `ggwp.negosyotest.com` is already designated.
- **A5. Rulings on C1 to C12** (section 3). C1, C3, C4 and C6 change what the admin experiences or what protects the sign-in; the rest are mechanical.
- **A6. Email wording** (C10), proposed:
  - Code. Subject: "Your NegosyoOnline admin sign-in code". Body: "Your NegosyoOnline admin sign-in code is <code>. It expires in 10 minutes. If you did not ask for it, ignore this email."
  - Lockout. Subject: "NegosyoOnline admin sign-in paused". Body: "Admin sign-in for this address is paused for 15 minutes after too many wrong codes. If this was not you, tell the admin owner."
- **A7. Delivery in two PRs** (recommended): PR A holds the models, migration, verifier, audit record and command, which is all Build 7 waits for; PR B holds hq and the Django admin enforcement. Nothing is reachable from a browser until PR B and `HQ_HOST` are in place.

### 7.2 Later, each its own approval (not asked for now)

Merging either PR; applying the migration on staging; an hq staging domain (none is proposed here) with its Railway custom domain and DNS; the three Railway variables on staging; running `admin_identity` on staging or any shared environment; setting `ADMIN_TWO_STEP_REQUIRED=1` anywhere shared; everything for production. Release order when those come, from Spec 3.6: deploy and migrate with the switch off; enrol; switch on; check.

## 8. `OperatorAuditEntry`, for Build 7 and ruling D4

| Field | Use |
|---|---|
| `operator` | FK `operators.User`, PROTECT, nullable. Null means the system wrote the row (for example Build 7's own job) |
| `action` | Short literal name, indexed. Must be declared in the registry |
| `target_type`, `target_id` | What was acted on, as text (for example `admin_identity` and its id, or `account` and a public id). Never a personal value |
| `reason` | Free text; blank for sign-in events |
| `detail` | JSON for facts that are not personal (surface, step, counts, before and after of a non-personal field) |
| `personal` | JSON; the only place a personal value may go (for example a subscriber's email an action touched) |
| `personal_blanked_at` | Null until D4 blanking has run on the row |
| `at` | When; indexed |

- Writing: only through `audit.record(operator, action, target_type, target_id, reason="", detail=None, personal=None)`. Rows are append-only: no update or delete path except the blanking below.
- Registry: `audit.ACTIONS` maps every action name to `PERSONAL` or `KEPT`. `record()` refuses an undeclared name, and a test fails if code uses one that is not declared. Build 7 adds its own actions to the same registry.
- This build's actions: `admin_sign_in_ok`, `admin_sign_in_failed`, `admin_locked`, `admin_sign_out` (class `PERSONAL`, per Spec 3.10) and `admin_identity_add`, `admin_identity_enrol`, `admin_identity_reset`, `admin_identity_disable` (class `KEPT`). None of them puts anything in `personal`: no IP, device or address is stored.
- D4 (rows kept, personal values blanked after 1 year) stays possible with one statement and no schema change: for rows older than a year whose action is `PERSONAL` and `personal_blanked_at` is null, set `personal` to empty and stamp `personal_blanked_at`. Whether `reason` is blanked too is Build 7's call; the column allows it. `operator` stays, as the accountability record (Spec 3.10 keeps the actor on purpose). Build 7 owns that job; this build ships only the fields and the registry.

## 9. Tests

Run with `uv run python manage.py test apps.operators`, then the whole suite, `manage.py check`, `makemigrations --check --dry-run` and `check --deploy` with `DJANGO_DEBUG=false`.

- **T-TOTP** known RFC 6238 vectors; window of one step either side; malformed input.
- **T-CRY** round trip; empty key and invalid key are "not ready"; a wrong key fails to decrypt without raising to the caller.
- **T-AUD** `record()` writes the row; an undeclared action is refused; every literal action in `apps/operators` is declared; no update path; `recent_sign_in_events` returns the newest 10 for one identity only.
- **T-VER** 1 right code; 2 wrong code; 3 replayed step; 4 earlier step; 5 recovery code works once, normalised; 6 used recovery code; 7 inactive, unenrolled and locked identities refused; 8 fifth failure locks for 15 minutes; 9 lock raises `credential_version` and revokes hq sessions in one transaction; 10 nothing revives when the lock ends, or after reset then enrol; 11 lockout notice sent once, after commit; 12 success resets the counter; 13 empty or wrong secrets key: generic failure, no attempt counted, no lock.
- **T-CMD** 1 `add` needs `--user` and `--actor`, both active staff; 2 `add` refuses an existing address and a second active identity for one user; 3 `enrol` refuses with an empty or invalid key; 4 `enrol` refuses without an interactive terminal; 5 `enrol` stores only ciphertext and hashes, prints the key and ten codes once; 6 `reset` clears secret and codes, raises the version, ends sessions; 7 `disable` keeps the row and ends sessions; 8 `list` writes no audit row and shows staff users with no enrolled identity and the switch warning; 9 every action but `list` writes one audit row with the actor.
- **T-HQ** 1 `/` without a session goes to `/sign-in/`; 2 `/code/` without a pre cookie goes to `/sign-in/`; 3 `/verify/` needs `email_ok`; 4 `email_ok` at `/` goes to `/verify/`; 5 a verified session at the three sign-in pages goes to `/`; 6 the full path S1 to S5; 7 wrong, expired, used and sixth-try email codes give one generic error; 8 known, unknown, disabled, unenrolled and over-limit addresses get the same status, body, headers and cookie attributes at S1; 9 the request path only calls `run_later`; 10 `run_later` raising changes nothing in the response; 11 empty secrets key: same page, nothing sent, one log line; 12 malformed, empty and over-long addresses get "Enter an email address." and no counter moves; 13 sign-out is POST only, revokes, clears both cookies, writes the audit row; 14 CSRF required on every POST and the 403 carries the hq headers; 15 every response has `no-store` and `noindex`; 16 rendered pages hold no `<script`, no external URL and none of the forbidden words; 17 disable, reset or lock elsewhere ends access on the next request; 18 S5 shows the email, both times in Manila time, the ten newest events with the right labels, and nothing else; 19 expired challenges and old sessions are purged when a new challenge is issued.
- **T-HOST** `HQ_HOST` empty: hq paths do not exist anywhere; set: the hq host serves only hq (no `/admin/`, `/v1/`, `/healthz/`), other hosts serve no hq path; the startup check refuses a clashing or malformed host and hq stays off.
- **T-ADM** 1 switch off: the admin behaves exactly as today (existing test kept); 2 switch on, password only: every admin URL redirects to `/admin/two-step/` with `next`; 3 right code: session key rotated, flag set, lands on `next`; 4 unsafe or foreign `next` goes to the admin index; 5 no linked, active, enrolled identity: S8 with status 403; 6 wrong, replayed and used codes: generic error; 7 fifth failure: S4, flag never set, hq sessions of that identity ended; 8 stale flag after lock, reset then enrol, disable and expiry: deleted, exactly one redirect, no loop; 9 a lock committed between verify and flag creation leaves a refused flag; 10 sign-out route is POST only, clears the flag, logs out, writes the audit row; 11 a non-staff or signed-out visitor gets the normal login; 12 a code used on hq is refused on the Django admin and the reverse.
- **T-BND** 1 no URL, view, form or admin registration can create or change an identity, a recovery code or a secret; 2 `apps/operators` writes to no other app's tables.

## 10. Local review checklist (Coach Ed), after A1 to A4 and the build

Environment: local, `http`, port 8003, this worktree, SQLite. Emailed codes appear in the `runserver` output. Set-up the builder does first and reports: migrate the local file, create two local staff users (one to enrol, one left without an identity for S8), `admin_identity add`. Coach Ed runs `admin_identity enrol` himself in his terminal so the key goes into his own authenticator app. The builder runs the same pages first and reports what was seen.

Check each row at desktop width (1280 or wider) and at phone width (390, and 320 for "no sideways scroll").

| Screen | Link | What to see |
|---|---|---|
| S1 | `http://hq.negosyotest.com:8003/sign-in/` | "Admin sign-in", the Email field, "Send code". An empty or malformed address shows "Enter an email address." A made-up address and the real one both move to S2 |
| S2 | `http://hq.negosyotest.com:8003/code/` | The notice "If this address is allowed, a code was sent. It expires in 10 minutes.", "Code sent to" the typed address, the 6-digit field, "Continue", the link back. A wrong code shows "That code did not work. Check it, or get a new code." |
| S3 | `http://hq.negosyotest.com:8003/verify/` and `/verify/?method=recovery` | "Two-step sign-in", the authenticator field, "Verify", the switch link; then the recovery wording and field. A wrong code shows "That code did not work. Try again." |
| S4 | Five wrong codes at S3, and again at S7 | "Sign-in paused", the 15-minute warning, the email sentence; "Back to sign-in" on hq, "Sign out" on the Django admin. The lockout email is in the `runserver` output |
| S5 | `http://hq.negosyotest.com:8003/` | "Admin console", "Signed in as", the "Verified" badge with both times, "Recent sign-in activity" with at most ten rows, "Sign out" at top right, and nothing else |
| S6 | Press "Sign out" on S5 | Back at S1 with no banner; `/` then returns to S1 |
| S7 | `http://ggwp.negosyotest.com:8003/admin/`, sign in with the password | "Two-step sign-in", "Your password is accepted. ...", both modes, "Verify", "Sign out". The right code opens the admin |
| S8 | Same link, signed in as the staff user with no identity | "Two-step sign-in is not set up", the one sentence, "Sign out", no link into the admin |
| All | Every screen above | Header "NegosyoOnline admin", no logo, no footer; light background; at phone width one column, 16px gutters, long emails wrap, no sideways scroll |

## 11. Build order after approval

Test first at each step. PR A: dependency and settings; models and migration; `crypto`, `totp`; `audit`; `two_step`; the command; runbook; the scope doc amendment. PR B: `background`, hq middleware, URLs, views and templates; the admin site and code views; README, `.env.example`. Before each PR: fetch `origin/main`, update the branch, re-run the tests and checks, list open PRs with their files, and report any overlap to the coordinator first.
