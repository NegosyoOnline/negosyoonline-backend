# NPC Build 9: hq is the only admin door (plan)

Date: 2026-11-11. ClickUp task: z8uq9mdxvt (https://app.clickup.com/t/z8uq9mdxvt), parent z8uq9mbreu.
Plan branch: `docs/z8uq9mdxvt-hq-only-admin-door`, cut from `origin/main` at `e85e727`. The build branch will be `feat/z8uq9mdxvt-hq-only-admin-door`, cut from the latest `origin/main` when the plan is approved.
Status: **plan only. Nothing here is approved.** No code, migration, dependency, variable or ClickUp change exists. Section 3 lists what Coach Ed must decide.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9mdxvt, description (no comments, attachments or checklists as of 2026-11-11) |
| Ruling | Coach Ed, 2026-11-11: "lets remove the django admin login.. all will now be in hq dashboard" and "it means that all admin works for negosyoonline will now be in hq" |
| Reading | The coordinator's reading, stated to Coach Ed: the existing admin lists are kept and open from hq behind the hq sign-in; new admin screens are built in hq |
| Build 6 | `docs/superpowers/plans/2026-11-10-backend-npc-build6-admin-two-step-hq.md`; PRs 40 and 41 on `main` at `e85e727` |
| Scope doc | `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md` (lines 32, 193, 197 describe the Django admin's own sign-in) |
| Code | `apps/operators`, `apps/common/admin.py`, `apps/accounts/admin.py`, `apps/sites/admin.py`, `apps/notifications/admin.py`, `config/settings.py`, `config/urls.py`, `railway.toml` at `e85e727` |

## 2. Scope, traced

### 2.1 Deliverables

| # | Task item | Built as (section 4) |
|---|---|---|
| 1 | On the API host, `/admin/` and its login answer 404 | The admin leaves `config/urls.py`. Nothing under `/admin/` is routed on any host except hq |
| 2 | On the hq host, the lists open only for an hq session that passed both steps | The admin is mounted in `apps/operators/hq_urls.py`. The admin's user comes only from a verified hq session |
| 3 | The hq signed-in page (S5) links to the lists | One new card on `hq/home.html` (text in 3.3) |
| 4 | The username-and-password login, S7, S8 and `ADMIN_TWO_STEP_REQUIRED` are removed | `admin_views.py`, `hq/two_step.html`, `hq/not_set_up.html`, the session flag, the setting and the sign-out signal are deleted |
| 5 | Sign-ins, failures, lockouts and sign-outs stay in the audit record | Unchanged hq code path (`hq_auth`, `two_step.verify_second_step`). "Sign out" on the lists calls the same hq sign-out |
| 6 | The runbook is updated, with the staging shell commands | `docs/runbooks/admin-two-step.md` rewritten where it mentions the Django admin sign-in or the switch |

### 2.2 Boundaries

Not built (Task "Not in this task"): rebuilding the lists in the hq look, the health page, hq settings screens, provider-console two-step. Also not touched: which models are listed, their columns, permissions per model, the `admin_identity` actions, the hq sign-in pages S1 to S4 and S6, any API endpoint.

## 3. Items for Coach Ed

Each item is separate. The recommended answer is what the rest of the plan assumes.

### 3.1 How an hq session authorises the admin lists

- **Which user the admin sees.** The staff user linked to the admin identity that signed in on hq (`AdminSession.identity.user`). A new small middleware sets `request.user` on the hq host from the hq cookie on every request: that user when the session is verified, nobody otherwise. The Django password session is not read on the hq host at all, so a `sessionid` cookie can never open a list.
- **Permissions.** Unchanged. The lists check the same staff user's Django permissions as today (`is_staff`, `is_superuser`, per-model permissions). The read-only lists stay read-only.
- **The 12-hour session.** The hq session row is read again on every list request (`hq_auth.load_session`). When it is past its 12 hours, revoked, or made under an older credential version, the request is sent to the hq sign-in page. The 12 hours are fixed from the emailed-code step and do not extend with use, as on S5 today.
- **Lockout.** Five wrong second-step codes pause the identity for 15 minutes and end its live hq sessions (unchanged). Because the lists use that same session, they close on the next request. `reset`, `disable` and `enrol` from the terminal close them the same way. A user who stops being active staff loses access on the next request.
- **Sign-in and sign-out from the lists.** Opening a list without a verified session goes to `/`, which already sends the browser to `/sign-in/` or `/verify/`. After signing in the admin lands on S5 and opens the lists from there (the page first asked for is not remembered; smallest build). "Sign out" on a list ends the hq session, writes the `admin_sign_out` audit row and returns to `/sign-in/`.
- **Password pages.** The admin's "change password" pages answer 404 on hq. Django passwords stay in the database but no page accepts one any more.
- **Cookies on the hq host.** The hq session cookie is unchanged: `__Host-no_hq`, HttpOnly, Secure, SameSite=Strict, host-only. Because it is Strict, a link to a list opened from another site or an email shows the sign-in page first; opening it from S5 works. The lists also use Django's own `csrftoken` cookie (the hq pages already set it) and may set a `messages` or `sessionid` cookie to show "saved" notices; neither carries a sign-in.
- **CSRF.** Every form on the lists keeps Django's CSRF check, the same check the hq forms pass today on staging. No new trusted origin is needed.
- **Headers.** Every answer on the hq host, lists included, keeps `Cache-Control: no-store` and `X-Robots-Tag: noindex`.

### 3.2 Migration, dependency, variables

- **Migration: none.** No model changes. CI's `makemigrations --check` proves it.
- **Dependency: none** with the recommended answer to 3.5.
- **Variables: none needed for the release.** `ADMIN_TWO_STEP_REQUIRED` stops being read by the code. On staging it stays at `1` through the release and the checks, because a rollback to the Build 6 build would otherwise open the old admin with a password alone. After Coach Ed accepts staging, deleting the variable is a separate approval (Railway redeploys on a variable change; behaviour does not change).

### 3.3 Visible change to S5 (needs visual approval)

A new card between the "Admin console" card and "Recent sign-in activity":

- Heading: **Admin lists**
- Sentence: **Subscribers, sites and sent emails.**
- Link: **Open admin lists** (goes to `/admin/`)

One sentence changes in the activity card, because there is no second place to sign in any more:

- Now: "Your last 10 sign-in events on the admin console and the operator admin."
- Proposed: **Your last 10 sign-in events.**

Older rows made on the Django admin keep their "Operator admin" label so the history stays true.

### 3.4 Visible change to the lists' header (needs visual approval)

The lists keep Django's look. Only the words that would now be wrong change:

| Place | Now | Proposed |
|---|---|---|
| Header title and browser tab | Django administration / Django site admin | **NegosyoOnline admin** |
| First page heading | Site administration | **Admin lists** |
| Top-right link "View site" | goes to `/` | **Admin console** (goes to S5) |
| Top-right link "Change password" | shown | removed |
| Top-right button "Log out" | Django log-out | **Sign out** (ends the hq session) |

### 3.5 The lists' styling files: a finding and a decision

Finding: on staging today the Django admin has no styling. `https://api-staging.negosyonline.com/static/admin/css/base.css` answers 404 (checked 2026-11-11): nothing in this repository serves static files outside local development. The lists work but look like bare HTML, and the row checkboxes and filters lose their script.

Options:

| | What | Cost |
|---|---|---|
| A (recommended) | Django serves the admin's own CSS and script on the hq host under `/static/admin/`, only to a verified hq session, from the folder inside the installed Django package | No dependency, no build step, no variable. Files are re-sent on each page (`no-store`), which is fine for a handful of admins |
| B | Add the `whitenoise` package and a `collectstatic` build step | A new dependency, a `railway.toml` build change, and a public `/static/` path. Both need separate approval |
| C | Leave the lists unstyled | Nothing to build; hard to use on a phone |

Note for the record: Build 6's rule "no script and no external asset" was written for the hq pages. The lists keep Django's own script, served from the same host; nothing is loaded from another site. The hq pages S1 to S6 stay script-free.

### 3.6 Where the lists live on the hq host

Recommended: `/admin/` on the hq host (smallest change; every list keeps its path). It is not reachable on any other host.

### 3.7 Release order for staging

The rule: the lists are never open without the second step, and Coach Ed is never locked out.

1. **Before the merge.** Coach Ed signs in to hq on staging once more and confirms his recovery codes are at hand. `admin_identity list` on staging shows his identity enrolled and not locked. No variable is touched: `ADMIN_TWO_STEP_REQUIRED` stays `1`.
2. **Merge after CI is green.** One build carries both halves: the API host stops answering `/admin/` and the hq host starts serving the lists behind the hq session. There is no in-between state, no migration and no variable step.
3. **Check staging (builder, no sign-in needed).** `/healthz/` shows the new commit. `https://api-staging.negosyonline.com/admin/`, `/admin/login/` and `/admin/two-step/` answer 404. `https://hq-staging.negosyonline.com/admin/` without a session lands on the sign-in page. `/static/admin/css/base.css` on hq answers 404 without a session.
4. **Coach Ed's check on staging**, desktop and phone width, after the local check has passed (section 7).
5. **Going back.** Redeploy the previous build in Railway. Because the variable is still `1`, the old admin returns with its two-step sign-in, never with the password alone.
6. **After acceptance, with a separate approval:** delete `ADMIN_TWO_STEP_REQUIRED` on staging.

What changes for emergencies: the old fallback "unset the switch and use the password" no longer exists. If hq sign-in cannot work (for example sign-in email is not being delivered), the ways in are the terminal (`admin_identity reset` and `enrol`, the Django shell) and step 5. The runbook will say so.

### 3.8 Scope document

The scope doc needs three sentences corrected and one dated entry added. Open PR 42 (another coordinator's builder) edits the same file. Recommended: the coordinator agrees the order with that session before this build's PR opens; this builder then merges `main` and keeps both changes.

## 4. Design

### 4.1 Files

| File | Change |
|---|---|
| `config/urls.py` | Remove the admin mount, the two `admin/two-step/` routes and their imports |
| `config/settings.py` | Remove `ADMIN_TWO_STEP_REQUIRED`. Add `apps.operators.middleware.HqUserMiddleware` after `AuthenticationMiddleware`. Move `apps.operators` above the admin entry in `INSTALLED_APPS` so its `admin/base_site.html` is the one used |
| `apps/operators/hq_urls.py` | Add `static/admin/<path>` (3.5 option A) and `path("admin/", admin.site.urls)` before the catch-all |
| `apps/operators/middleware.py` | New `HqUserMiddleware`: on a request routed to hq, `request.hq_session` is the verified session or `None`, and `request.user` is its identity's user or `AnonymousUser`. On other hosts it does nothing |
| `apps/operators/admin_site.py` | Rewritten as `HqAdminSite`: `has_permission` needs `request.hq_session` and an active staff user; `login` redirects to `/`; `logout` (POST) calls the hq sign-out; `password_change` and `password_change_done` raise 404; titles from 3.4; `site_url = "/"`. The flag helpers and the sign-out signal are deleted |
| `apps/operators/admin_config.py` | Points at `HqAdminSite` |
| `apps/operators/apps.py` | Drop the `user_logged_out` connection |
| `apps/operators/hq_views.py` | `_session` reads `request.hq_session` when present; new `admin_static` view (verified session or 404, then Django's `static.serve` on the admin package's static folder); the hint sentence from 3.3 |
| `apps/operators/admin_views.py`, `templates/hq/two_step.html`, `templates/hq/not_set_up.html` | Deleted |
| `templates/hq/paused.html` | Keeps only the hq branch ("Back to sign-in") |
| `templates/hq/home.html` | The card from 3.3 |
| `templates/admin/base_site.html` (new, in `apps/operators`) | The header words from 3.4 |
| `apps/operators/two_step.py` | Docstrings only. `SURFACE_DJANGO_ADMIN` stays as the label key for old audit rows |
| `apps/operators/management/commands/admin_identity.py` | `list` no longer prints the switch or its warning; the uncovered-staff line reads "they cannot open the admin lists" |
| `docs/runbooks/admin-two-step.md`, `README.md`, scope doc | Section 6 |

### 4.2 Why the lists cannot open another way

- Routing: `/admin/` exists only in the hq URL set, which `HqHostMiddleware` selects only for `HQ_HOST`. With `HQ_HOST` empty the lists do not exist anywhere.
- Identity: on hq, `request.user` is set by `HqUserMiddleware` after Django's own authentication middleware, so a Django password session is overwritten, not combined.
- Second check: `HqAdminSite.has_permission` also requires `request.hq_session`, so a mistake in one layer does not open the lists.
- Nothing calls Django's `login()` any more, so no new password session is ever made.

## 5. Tests

`uv run python manage.py test`, on both CI jobs. `test_admin_two_step.py` is deleted with the code it tested; its still-relevant cases move to the new file.

| Task "Accepted when" | Tests (new file `apps/operators/tests/test_hq_admin.py` unless named) |
|---|---|
| No admin page is reachable without the hq emailed code and the authenticator code | 1 no cookie: `/admin/`, a list, an add page and `/admin/login/` on hq all end at `/sign-in/`, never 200. 2 emailed code only (`email_ok`): the same paths end at `/verify/`. 3 a Django password session (`force_login`) with no hq cookie: same as 1. 4 full hq sign-in: `/admin/` and a list answer 200 as the identity's user. 5 expired session, sign-out, lock, `reset`, `disable`, user no longer staff: the next list request ends at `/sign-in/`. 6 a staff user without list permissions sees no lists (permissions unchanged). 7 `/admin/password_change/` is 404. 8 `/static/admin/css/base.css` is 404 without a verified session and 200 with one. 9 every list answer carries `no-store` and `noindex`. 10 a list POST without the CSRF token is refused |
| No admin page answers on the API host | 11 on the API host and on any other host, with and without a signed-in staff user: `/admin/`, `/admin/login/`, `/admin/logout/`, `/admin/two-step/`, `/admin/two-step/sign-out/`, a list path and `/static/admin/css/base.css` answer 404. 12 with `HQ_HOST` empty the same paths are 404 on every host. `test_user.py`'s "superuser can open the admin" becomes case 3 and 11 |
| The masked contact lists from Build 3 still mask | `apps/accounts/tests/test_admin.py` and `apps/sites/tests/test_admin.py` keep every assertion and are moved onto the hq host with a real hq sign-in (a shared helper in `apps/operators/tests/base.py`). The sent-emails list has no masking test today; one is added in `test_hq_admin.py` |
| Audit record kept (deliverable 5) | 13 "Sign out" on a list revokes the hq session, clears the cookie and writes `admin_sign_out` with surface `hq`. Existing `test_hq.py` and `test_two_step.py` cases for sign-in, failure and lock rows stay as they are |
| S5 link and words (deliverable 3) | `test_hq.py` home test: the new card, the link to `/admin/`, the new hint sentence; an old `django_admin` row still shows "Operator admin" |
| The switch is gone (deliverable 4) | 14 `settings` has no `ADMIN_TWO_STEP_REQUIRED`; `test_command.py` list output no longer mentions it; `test_boundary.py` unchanged and passing |
| Tests pass in CI | Both existing CI jobs, plus `manage.py check --deploy` |
| Coach Ed passes the pages | Section 7 |

## 6. Documents

- `docs/runbooks/admin-two-step.md`: the variable table loses `ADMIN_TWO_STEP_REQUIRED`; "Release order" becomes section 3.7; "Lost phone" loses the unset-the-switch line and gains the terminal and redeploy routes; the lockout sentence names hq only. New section "Commands on staging": the interactive staging shell has no `uv` and no `python` on its path, so `uv run python manage.py ...` and `python manage.py ...` fail there; use `/app/.venv/bin/python manage.py admin_identity ...`.
- `README.md`: the `apps/operators` and hq lines and the variables line.
- Scope doc: lines 32, 193 and 197 corrected and a 2026-11-11 entry added (see 3.8).
- The Build 6 plan is history and is not edited.

## 7. Local review packet (after the build, before staging)

Environment: local, `http`, port 8013, this worktree, SQLite. Emailed codes appear in the `runserver` output. `HQ_HOST=hq.negosyotest.com`; this name must resolve to `127.0.0.1` on Coach Ed's computer, as it did for Build 6.

| Check | Where | Expect |
|---|---|---|
| API host has no admin | `http://api.negosyotest.com:8013/admin/` and `/admin/login/` | Page not found |
| Lists closed without sign-in | `http://hq.negosyotest.com:8013/admin/` in a fresh window | The hq sign-in page |
| S5 | Sign in with both steps | The new "Admin lists" card and the shorter activity sentence |
| Lists | "Open admin lists" | Styled lists, header "NegosyoOnline admin", "Admin console" and "Sign out" at top right, no "Change password"; emails masked |
| Sign out | "Sign out" on a list | Back at sign-in; the list link then shows sign-in again |
| Phone width | S5 and one list at 390 px | Readable, no sideways scroll on S5 |

## 8. Order of work after approval

1. Cut `feat/z8uq9mdxvt-hq-only-admin-door` from the latest `origin/main`; set ClickUp to IN PROGRESS.
2. Tests first for section 5, then the code in 4.1, in small commits.
3. Local checks, then the review packet in section 7 to the coordinator; ClickUp FOR CHECKING.
4. After Coach Ed's local pass: overlap check on open PRs, merge `main`, re-run tests, open the PR.
5. Staging follows 3.7, each step with its own approval.
