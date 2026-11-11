# HQ Staff Sign-In Routes Implementation Plan (plan 1 of 11, plan only)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the backend the staff sign-in and console routes that the staff app on Vercel will call, switched off unless `HQ_STAFF_ORIGIN` is set, and prove on staging that the HQ cookie works between the two addresses before anything is built on it.

**Architecture:** Six JSON routes under `/staff/v1/` on the HQ host, each a thin wrapper around the functions that check the codes today (`hq_auth`, `two_step`, `audit`). They set the same two cookies the Django pages set. `HqHostMiddleware` answers them itself, before Django's form-token check, and only while a valid staff origin is set; every call must carry that one `Origin`.

**Tech Stack:** Django 5.2, Python, `uv`, Django's test runner. No new dependency, no model, no migration.

---

Date: 2026-11-11. ClickUp task: z8uq9me128 (https://app.clickup.com/t/z8uq9me128), parent z8uq9md33v.
Plan branch: `claude/z8uq9me128-hq-staff-sign-in-plan`, cut from `origin/main` at `1c1eff8`. The build branch will be cut from the latest `origin/main` when this plan is approved.
Status: **approved by Coach Ed on 2026-11-11** ("Approve plan 1", at head `aa61a75`), reviewed by the NPC coordinator. What the approval covers is in 9.3; what it does not cover, each still a separate approval, is in 9.4. Nothing in it is built. The code in it was written from reading the repository and has not been run.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9me128: description and comments, read 2026-11-11 |
| Build order | The coordinator's comment on z8uq9me128, "Build order for the approved design": this is item 1 |
| Design | `docs/superpowers/specs/2026-11-11-sms-inbox-hq-design.md`, sections 20, 21 and 22, on `main` at `1c1eff8` |
| Code | `apps/operators/hq_views.py`, `hq_auth.py`, `two_step.py`, `audit.py`, `middleware.py`, `checks.py`, `hq_urls.py`, `pages.py`, `apps/common/middleware.py`, `apps/common/errors.py`, `apps/accounts/sessions.py`, `config/settings.py`, `config/urls.py`, `apps/health/tests.py`, `apps/operators/tests/base.py` and `test_hq.py`, all at `1c1eff8` |

### 1.1 Rulings, in Coach Ed's words

- **R26**, how HQ staff sign in on Vercel: "Okay, just do all the recommended for the these 4 rows, how it works, Django admin, the security, and the build." The recommended column: the forms are Vercel pages; the backend checks the codes as today and sets today's HQ cookie; no token held by the page; no migration. **The cost he accepted with it:** a compromised Vercel deploy of the staff app can capture both codes as they are typed and sign in as that person, for up to 12 hours each time.
- **R27**: "just drop this django admin... dont show this.. you are confused because of this".
- **R28**: "Switch it off".
- **R3 stands**: two-step sign-in, explicit permission, every read and send audited.
- His approvals of 2026-11-11 ("approved the look", "approved the design") allow plans to be written, each back to him before any code. They approve no build, migration, Railway or Vercel change.

R27 and R28 change nothing in this plan's code: the Django Admin is switched off by plan 11, last. Until then it stays where it is, and a session made through the new routes opens it exactly as a session made through the Django pages does (design 20.6).

## 2. Scope

### 2.1 What is built

| # | Deliverable | Traced to |
|---|---|---|
| 1 | Six routes under `/staff/v1/` on the HQ host: sign-in, code, second step (authenticator or recovery code), sign-out, a session read, the console read | Design 22.3; build order item 1 |
| 2 | The routes check the codes with today's functions and set today's cookies. No token is given to the page | R26; design 20.5 |
| 3 | Everything is off unless `HQ_STAFF_ORIGIN` is set to a usable value. Unset, the HQ host answers exactly as it does today | Design 22.6 step 1; coordinator's brief |
| 4 | Every route refuses a call whose `Origin` is not that one origin, reads included | Design 20.3, 20.5 |
| 5 | A start-up check refuses a bad `HQ_STAFF_ORIGIN`; a running service treats a bad value as unset | Design 20.5, 22.5; the pattern of `HQ_HOST` in `checks.py` |
| 6 | The runbook names the new variable and its log line | `docs/runbooks/admin-two-step.md` |
| 7 | A gated staging step that proves the cookie between the two addresses (section 7) | Design 17 and 22.7 ("not verified"); coordinator's brief |

### 2.2 What is not built here

| Left out | Where it belongs |
|---|---|
| The `apps/hq` staff app, its pages and its Vercel project | Plan 2 (frontend repository) |
| Inbox storage, reply and compose, inbox pages | Plans 3, 4, 5 |
| Contacts and groups, Bulk SMS | Plans 6 to 9 |
| The host names, turning the links, retiring the Django HQ pages | Plan 10 |
| A replacement for each thing only the Django Admin does, and switching the Admin off | Plan 11 |
| The inbox permission (`InboxAccess`, design 7) | Plan 3. Nothing in this plan reaches inbox data |

Not touched: the five Django HQ pages, their templates and their tests; `hq_urls.py`; `hq_auth.py`, `two_step.py`, `audit.py`; any model; any limit; the Subscriber API under `/v1/`; `/healthz/`; the SMS gateway routes.

## 3. What the code shows, and where it differs from the design

Read at `1c1eff8`. Each row matters to this plan.

| # | Finding | What the plan does with it |
|---|---|---|
| 1 | Both cookies carry the `__Host-` prefix off `DEBUG` (`__Host-no_hq`, `__Host-no_hq_pre`), with `Secure`, `HttpOnly`, `SameSite=Strict`, path `/`, no domain (`hq_views._cookie_names`, `_set_cookie`). The design says "host-only" and does not name the prefix | Kept exactly. The prefix means the cookie can never be given a `Domain`: a browser refuses a `__Host-` cookie that has one. So "widen the cookie to the parent domain" is not a fallback unless the cookie is also renamed (section 7.6) |
| 2 | Django's form-token check (`CsrfViewMiddleware`) covers every view on the HQ host. A page on another host cannot hold that token (design 20.5). If the new routes were ordinary views marked exempt, a POST to their paths would change from today's 403 to something else even while the variable is unset | The routes are not in any URL list. `HqHostMiddleware` answers them itself, and only while a staff origin is active. Unset, the request goes down exactly today's path |
| 3 | `HqHostMiddleware` already answers the `/v1/` prefix on the HQ host itself, with the plain "Page not found" | The same shape is used for `/staff/v1/`. One new branch |
| 4 | `hq_views.code` shows `pre.get("typed") or pre["email"]`, so a pre-sign-in cookie without `typed` is accepted | The route's cookie holds `email` and `nonce` only. A person who asked for a code through the route can finish on the Django page, and the other way round |
| 5 | The design gives `GET /staff/v1/me/` only its two 401 answers (22.3) | The plan adds the 200: `{"email", "expires_at"}`. A routine detail, listed in 9.1 |
| 6 | The design's proposed names put the staff app at `hq-staging.negosyonline.com` (22.7). That name is `HQ_HOST` on staging today (relayed in design 22.5, not checked by this session). The two cannot hold the same name at once | The proof cannot use the final names without doing plan 10's rename first. Section 7.2 sets out two arrangements; which one is used is Coach Ed's |
| 7 | `checks.hq_host_problem` refuses an `HQ_HOST` that sits under `SITE_PARENT_HOST` with a name a Subscriber could take. `is_reserved("hq-api-staging")` and `is_reserved("hq-app-staging")` are both false by reading `apps/sites/subdomains.py`; so is `is_reserved("hq-staging")`, which is accepted on staging today | A sibling of `hq-staging` under the same parent meets this rule the same way `hq-staging` does. Not run against staging's real `SITE_PARENT_HOST`, whose value this session has not seen (section 8) |
| 8 | The hourly limit counts the caller's IP from `X-Real-IP` (`apps/accounts/sessions.client_ip`) | Unchanged. The staff page calls the backend straight from the browser (design 21.1), so the IP is the staff member's, as today |
| 9 | `HqHostMiddleware` is second in `MIDDLEWARE` on `origin/main` at `1c1eff8`, after `SecurityMiddleware` only. An answer it gives itself never reaches the eight entries after it: `ApiOriginMiddleware` (which passes on anything outside `/v1/`), `SessionMiddleware`, `CommonMiddleware`, `CsrfViewMiddleware`, `AuthenticationMiddleware`, `HqUserMiddleware`, `MessageMiddleware` and `XFrameOptionsMiddleware` | **A staff answer skips all eight.** `SecurityMiddleware` still runs, so the https redirect, HSTS and `X-Content-Type-Options: nosniff` apply. What each skipped one would have done, and what replaces it: the form token is replaced by the origin rule; `HqUserMiddleware`'s session read is done by the routes themselves with the same function; `X-Frame-Options` is set by `dispatch` on every staff answer (the coordinator's decision); the routes use no Django session, user, message or trailing-slash redirect. Tests in task 2 hold the two headers |

Nothing else in the design's reading of sections 20 and 22 was found wrong: the six functions it names exist with the signatures used below, no model or audit action changes, and no migration is needed.

## 4. Edits to `config/settings.py`, by setting name

For the NPC coordinator, who asked for this list. **One setting is added. No existing setting is edited.**

| Setting | Change |
|---|---|
| `HQ_STAFF_ORIGIN` | **New.** `os.environ.get("HQ_STAFF_ORIGIN", "").strip()`, placed directly under the `HQ_HOST` block. Empty by default |
| `ALLOWED_HOSTS` | Not edited. The staff app's host is never a host the backend answers on. `healthcheck.railway.app` is untouched |
| `CSRF_TRUSTED_ORIGINS` | Not edited. The routes do not use Django's form token |
| `API_ALLOWED_ORIGINS` | Not edited. The staff origin is never an API origin; the start-up check refuses it if it is listed there |
| `MIDDLEWARE` | Not edited. No middleware is added or moved |
| `SECURE_REDIRECT_EXEMPT`, `SECURE_SSL_REDIRECT`, `SECURE_PROXY_SSL_HEADER` | Not edited. `/healthz/` stays as it is |
| `SESSION_COOKIE_*`, `CSRF_COOKIE_*` | Not edited. The HQ cookies are set by `hq_views._set_cookie`, not by these |
| `HQ_HOST`, `ADMIN_SECRETS_KEY` | Not edited |

`/healthz/` is in `config/urls.py` and is not touched. The new code runs only for a request whose host is `HQ_HOST` and whose path starts with `/staff/v1/`. A health check on the API host or on `healthcheck.railway.app` never reaches it. Task 2 adds tests that say so with the variable set.

### 4.1 The exact lines added, and nothing else

The build adds these four lines to `config/settings.py`, directly after the line `    ALLOWED_HOSTS.append(HQ_HOST)`, and changes, moves or removes no other line of the file:

```python
# The one browser origin that may call the staff routes on the hq host (/staff/v1/): the staff
# app on Vercel, for example https://hq.example.com. Empty means those routes do not exist. It
# is never a host this service answers on, so it is not added to ALLOWED_HOSTS.
HQ_STAFF_ORIGIN = os.environ.get("HQ_STAFF_ORIGIN", "").strip()
```

Lines are only added. Nothing is reordered or restructured, so another session's added lines (Santé slice 7, z8uq9me4dy: a new switch, `PAYMENT_CREDENTIALS_ENCRYPTION_KEY`, one `INSTALLED_APPS` entry) merge beside these. Whichever PR is ready first merges first; the other updates from `origin/main` and keeps both sides' lines.

**This plan's build does not touch `config/api.py`, `pyproject.toml` or `uv.lock`.** The routes are not on the API and need no new dependency. This plan-only PR touches none of the shared files.

### 4.2 Two things that stay exactly as they are, each with a check

| What | Where it is | How the build shows it is unchanged |
|---|---|---|
| `SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]` (PR 78) | `config/settings.py` | The existing test `test_only_the_exact_healthz_path_is_exempt` in `apps/health/tests.py`, which stays unedited and passes; the new test `test_health_over_plain_http_is_untouched` in task 2; and the diff check in task 4 step 5 |
| `healthcheck.railway.app` in the allowed hosts | Not in the code. It is in the Railway variable `DJANGO_ALLOWED_HOSTS` (`README.md` line 31, `docs/runbooks/billing.md`). `config/settings.py` only reads that variable into `ALLOWED_HOSTS` | The build edits no line that reads or builds `ALLOWED_HOSTS`, and the new setting is never appended to it: the new test `test_the_staff_origin_changes_no_allowed_host` in task 2. This plan sets or changes no Railway variable other than `HQ_STAFF_ORIGIN`, and never `DJANGO_ALLOWED_HOSTS` (7.3). On staging, step 1 of 7.4 confirms the deployment passed Railway's health check |

The present value of `DJANGO_ALLOWED_HOSTS` on staging was not read by this session. The NPC coordinator checked on 2026-11-11, by name, that it lists `healthcheck.railway.app` on staging `api` (relayed, 7.3).

## 5. The routes

All on the HQ host, under `/staff/v1/`. Request and answer bodies are JSON. Errors use the API's shape: `{"error": {"code", "message"}}`.

| Route | Sends | Answers |
|---|---|---|
| `POST /staff/v1/sign-in/` | `{"email"}` | 204 for every well-formed address, known or not, and the 10-minute cookie. 400 `invalid_email` |
| `POST /staff/v1/sign-in/code/` | `{"code"}` | 204 and the first-stage session cookie. 400 `invalid_code`, with no reason given, also when the 10-minute cookie is missing or stale |
| `POST /staff/v1/sign-in/verify/` | `{"code"}`: an authenticator code or a recovery code | 204 and the verified session cookie. 400 `invalid_code`. 403 `sign_in_paused` with both cookies dropped. 401 `unauthenticated` with no first-stage session |
| `POST /staff/v1/sign-out/` | nothing | 204, the session ended, `admin_sign_out` written, both cookies dropped. 204 also with no session |
| `GET /staff/v1/me/` | nothing | 200 `{"email", "expires_at"}`. 401 `unauthenticated` or `second_step_required` |
| `GET /staff/v1/console/` | nothing | 200 `{"email", "verified_at", "expires_at", "events": [{"action", "surface", "step", "at"}]}`: the person's own last 10 sign-in events. The same two 401s |

Rules that cover every route:

- **Off unless the origin is set.** With `HQ_STAFF_ORIGIN` empty or refused, the paths are not special: a GET answers the HQ host's plain "Page not found" and a POST meets the form-token check, as today.
- **One origin.** A call whose `Origin` header is not exactly `HQ_STAFF_ORIGIN` answers 403 `origin_not_allowed`: reads, writes and preflights alike, and before the path is looked at, so another origin learns nothing about which routes exist. A browser always sends `Origin` on a call from one origin to another, so the staff app is not affected. A call with no `Origin` (an address typed in a browser, `curl` without the header) is refused.
- **The answer names that origin** (`Access-Control-Allow-Origin`, `Access-Control-Allow-Credentials: true`, `Vary: Origin`) only when the call came from it. A preflight answers 204 with the one method of that route and `content-type`.
- **A signed-in person** calling `sign-in/`, `sign-in/code/` or `sign-in/verify/` gets 409 `already_signed_in`, where the Django pages send them to the console.
- **Every answer** carries `Cache-Control: no-store` and `X-Robots-Tag: noindex`, set by `HqHostMiddleware` as today.
- **Limits, the pause, the 12 hours, the audit entries** are whatever `hq_auth` and `two_step` do. The routes add none and remove none.
- **The page never sees a secret.** No answer body holds a token, a code, or anything of the authenticator secret.

## 6. Files

| File | Change | Responsibility |
|---|---|---|
| `config/settings.py` | Add `HQ_STAFF_ORIGIN` | Reads the variable |
| `apps/operators/checks.py` | Add `staff_origin_problem`, `active_staff_origin`, the check `operators.E002` | Says whether the value can be used |
| `apps/operators/staff_views.py` | **New** | The six routes, the origin rule, the cross-origin headers |
| `apps/operators/middleware.py` | One new branch in `HqHostMiddleware` | Hands `/staff/v1/` to `staff_views` while an origin is active |
| `apps/operators/tests/test_staff_routes.py` | **New** | Every test in section 6.1 |
| `docs/runbooks/admin-two-step.md` | One table row, one log line, one paragraph | What the variable means and how to turn the routes off |

`staff_views.py` uses four helpers of `hq_views.py` that begin with an underscore (`_cookie_names`, `_set_cookie`, `_drop_cookies`, `_pre`). They are used as they are, not renamed or moved, so the cookies cannot drift apart and `hq_views.py` has no diff. Plan 10 moves them when it removes the Django pages.

### 6.1 Tests written first

| Test | Proves |
|---|---|
| Off by default | With the variable unset, each staff path answers byte for byte what an unknown path on the HQ host answers, for GET and POST, with and without an `Origin` |
| A bad value is refused | Each bad value gives `operators.E002`, logs `hq-staff-origin-refused:` once, and leaves the routes off |
| A good value passes | No error |
| Origin refusal | No `Origin`, the dashboard's, a preview address, the HQ host's own: 403 `origin_not_allowed` on every route and method, no cross-origin header, no cookie, no audit row, no email |
| Preflight | 204 with the route's method for the staff origin |
| The full path | Email, code, second step, session read, console, sign-out, with the audit rows written |
| Same cookies | Name, `Secure`, `HttpOnly`, `SameSite=Strict`, path `/`, no domain: equal to what the Django pages set |
| Same answer for any address | Known, unknown and over the limit: 204, and mail only for the known one within limits |
| Wrong code, missing cookie | `invalid_code`, and `admin_sign_in_failed` for a wrong one |
| Recovery code | Passes once, refused the second time |
| Five wrong codes | `sign_in_paused`, cookies dropped, `admin_locked` written, every session of that person ended |
| Both ways in make one session | A session made by the routes opens the Django console, and one made by the Django pages answers `me/` |
| Other hosts | `/staff/v1/…` on the API host answers 404 with the variable set |
| The Django pages are unchanged | With the variable set, the Django sign-in still works and still needs its form token |
| Health | With the variable set, `/healthz/` answers 200 on the default host and on `healthcheck.railway.app`, over plain http with the redirect on; `SECURE_REDIRECT_EXEMPT` is `[r"^healthz/$"]`; the staff origin adds no allowed host |
| A failing route | 500 `server_error` as JSON, with the two headers |
| Skipped middleware | Every staff answer, refusals and errors included, carries `X-Frame-Options: DENY` (set by `dispatch`) and `X-Content-Type-Options: nosniff` (from `SecurityMiddleware`) |

---

## Task 1: The setting and its start-up check

**Files:**
- Modify: `config/settings.py` (under the `HQ_HOST` block, after line 91)
- Modify: `apps/operators/checks.py`
- Test: `apps/operators/tests/test_staff_routes.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `apps/operators/tests/test_staff_routes.py`:

```python
"""The staff routes under /staff/v1/ on the hq host: off by default, one origin, today's cookies."""
import json
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from apps.operators import checks
from apps.operators.models import AdminSession, OperatorAuditEntry
from apps.operators.tests.base import EMAIL, HQ, OTHER
from apps.operators.tests.test_hq import PRE, SESSION, HqTestCase

STAFF = "https://hq-app.example.test"
PATHS = (
    ("POST", "/staff/v1/sign-in/"),
    ("POST", "/staff/v1/sign-in/code/"),
    ("POST", "/staff/v1/sign-in/verify/"),
    ("POST", "/staff/v1/sign-out/"),
    ("GET", "/staff/v1/me/"),
    ("GET", "/staff/v1/console/"),
)
BAD_ORIGINS = (
    "hq-app.example.test",                    # no scheme
    "http://hq-app.example.test",             # not https
    "https://hq-app.example.test/",           # a trailing slash
    "https://hq-app.example.test:8443",       # a port
    "https://hq-app.example.test/sign-in",    # a path
    "https://HQ-APP.example.test",            # upper case
    "https://user@hq-app.example.test",       # a user
    "https://*.example.test",                 # a pattern
    "https://hq.example.test",                # the hq host itself
    "https://hq-app.vercel.app",              # another parent: the cookie would not be sent
    "https://app.hq.example.test",            # not a sibling of the hq host
    "https://dash.example.test",              # the dashboard
    "https://www.example.test",               # the public site
    "https://extra.example.test",             # an API origin
)
on_staff = override_settings(HQ_STAFF_ORIGIN=STAFF)


class StaffOriginCheckTests(HqTestCase):
    @on_staff
    def test_a_good_origin_passes_the_check(self):
        self.assertEqual(checks.staff_origin_problem(), "")
        self.assertEqual(checks.active_staff_origin(), STAFF)
        self.assertEqual(checks.check_staff_origin(None), [])

    def test_an_empty_origin_is_off_and_is_not_an_error(self):
        self.assertEqual(checks.active_staff_origin(), "")
        self.assertEqual(checks.check_staff_origin(None), [])

    @override_settings(
        DASHBOARD_URL="https://dash.example.test", SITE_URL="https://www.example.test",
        API_ALLOWED_ORIGINS=["https://dash.example.test", "https://extra.example.test"],
    )
    def test_a_bad_origin_is_refused_and_the_routes_stay_off(self):
        for value in BAD_ORIGINS:
            cache.clear()
            with self.subTest(value), override_settings(HQ_STAFF_ORIGIN=value):
                errors = checks.check_staff_origin(None)
                self.assertEqual([e.id for e in errors], ["operators.E002"])
                with self.assertLogs("apps.operators.checks", "ERROR") as logs:
                    self.assertEqual(checks.active_staff_origin(), "")
                self.assertIn("hq-staff-origin-refused:", logs.output[0])
                # Said once an hour, not on every request.
                with self.assertNoLogs("apps.operators.checks", "ERROR"):
                    self.assertEqual(checks.active_staff_origin(), "")

    @override_settings(HQ_HOST="", HQ_STAFF_ORIGIN=STAFF)
    def test_an_origin_without_an_hq_host_is_refused(self):
        self.assertEqual([e.id for e in checks.check_staff_origin(None)], ["operators.E002"])

    @override_settings(SITE_PARENT_HOST="example.test", HQ_STAFF_ORIGIN="https://tindahan.example.test")
    def test_an_origin_a_subscriber_site_could_take_is_refused(self):
        # hq.example.test itself is reserved, so HQ_HOST passes; "tindahan" is a name anyone can take.
        self.assertEqual([e.id for e in checks.check_staff_origin(None)], ["operators.E002"])

    @override_settings(DEBUG=True, HQ_STAFF_ORIGIN="http://hq-app.example.test:3002")
    def test_local_development_may_use_http_and_a_port(self):
        self.assertEqual(checks.staff_origin_problem(), "")
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run python manage.py test apps.operators.tests.test_staff_routes -v 2`
Expected: errors, `AttributeError: 'Settings' object has no attribute 'HQ_STAFF_ORIGIN'` and `module 'apps.operators.checks' has no attribute 'staff_origin_problem'`.

- [ ] **Step 3: Add the setting**

In `config/settings.py`, directly after the two lines that append `HQ_HOST` to `ALLOWED_HOSTS`:

```python
# The one browser origin that may call the staff routes on the hq host (/staff/v1/): the staff
# app on Vercel, for example https://hq.example.com. Empty means those routes do not exist. It
# is never a host this service answers on, so it is not added to ALLOWED_HOSTS.
HQ_STAFF_ORIGIN = os.environ.get("HQ_STAFF_ORIGIN", "").strip()
```

- [ ] **Step 4: Add the check**

In `apps/operators/checks.py`, change the module's first docstring line to `"""HQ_HOST must be a bare host name that nothing else answers on, and HQ_STAFF_ORIGIN the one origin that may call it.` and add, after `_HOST`:

```python
_ORIGIN = re.compile(rf"^(https?)://({_LABEL}(?:\.{_LABEL})+)(?::(\d{{1,5}}))?$")
```

Add after `active_hq_host`:

```python
def staff_origin_problem():
    """Why HQ_STAFF_ORIGIN cannot be used, or "" when it is empty or fine."""
    origin = settings.HQ_STAFF_ORIGIN
    if not origin:
        return ""
    hq_host = settings.HQ_HOST
    if not hq_host or hq_host_problem():
        return "HQ_STAFF_ORIGIN is set but HQ_HOST is empty or refused."
    match = _ORIGIN.fullmatch(origin)
    if match is None or len(match.group(2)) > 253:
        return (
            "HQ_STAFF_ORIGIN must be one origin: https://, a lower-case host name, and nothing "
            "after it."
        )
    scheme, host, port = match.groups()
    if not settings.DEBUG and (scheme != "https" or port):
        return "HQ_STAFF_ORIGIN must be https with no port."
    if host == hq_host:
        return "HQ_STAFF_ORIGIN is the hq host itself."
    taken = {
        urlsplit(url).hostname
        for url in (settings.DASHBOARD_URL, settings.SITE_URL, *settings.API_ALLOWED_ORIGINS)
    }
    if host in taken:
        return (
            "HQ_STAFF_ORIGIN is also the host of DASHBOARD_URL, SITE_URL or an "
            "API_ALLOWED_ORIGINS entry."
        )
    if host.partition(".")[2] != hq_host.partition(".")[2]:
        return (
            "HQ_STAFF_ORIGIN and HQ_HOST must be names directly under the same parent, so that "
            "the hq cookie is sent with SameSite=Strict."
        )
    parent = settings.SITE_PARENT_HOST
    if parent and host.endswith("." + parent):
        from apps.sites.subdomains import is_reserved

        label = host[: -len(parent) - 1]
        if "." not in label and not is_reserved(label):
            return "HQ_STAFF_ORIGIN sits under SITE_PARENT_HOST with a name a subscriber site could take."
    return ""


def active_staff_origin():
    """The one origin that may call the staff routes, or "" when they are off."""
    origin = settings.HQ_STAFF_ORIGIN
    if not origin:
        return ""
    problem = staff_origin_problem()
    if problem:
        if cache.add(f"hq-staff-origin-refused:{origin}", 1, 3600):
            logger.error("hq-staff-origin-refused: %s The staff routes are off.", problem)
        return ""
    return origin
```

Add at the end of the file:

```python
@register()
def check_staff_origin(app_configs, **kwargs):
    problem = staff_origin_problem()
    if not problem:
        return []
    return [Error(problem, hint="The staff routes stay off until it is fixed.", id="operators.E002")]
```

The "same parent" rule is deliberately stricter than a browser's: it accepts only a sibling of the HQ host (`hq-app.example.test` beside `hq.example.test`). It needs no list of public suffixes, it refuses every `vercel.app` address, and it can be loosened later by a ruling if a real name needs it.

- [ ] **Step 5: Run the tests and see them pass**

Run: `uv run python manage.py test apps.operators.tests.test_staff_routes -v 2`
Expected: 6 tests, OK.

- [ ] **Step 6: Commit**

```bash
git add config/settings.py apps/operators/checks.py apps/operators/tests/test_staff_routes.py
git commit -m "feat: HQ_STAFF_ORIGIN and its start-up check, unset by default [z8uq9me128]"
```

## Task 2: Off by default, and the origin rule

**Files:**
- Create: `apps/operators/staff_views.py`
- Modify: `apps/operators/middleware.py` (`HqHostMiddleware.__call__`)
- Test: `apps/operators/tests/test_staff_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/operators/tests/test_staff_routes.py`:

```python
class StaffTestCase(HqTestCase):
    def call(self, method, path, data=None, origin=STAFF, client=None, host=HQ):
        extra = {"secure": True, "HTTP_HOST": host}
        if origin:
            extra["HTTP_ORIGIN"] = origin
        client = client or self.client
        if method == "GET":
            return client.get(path, **extra)
        if method == "OPTIONS":
            return client.options(path, HTTP_ACCESS_CONTROL_REQUEST_METHOD=data, **extra)
        return client.post(
            path, data=json.dumps(data or {}), content_type="application/json", **extra
        )

    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)

    def staff_email_step(self, email=EMAIL):
        self.assertEqual(self.call("POST", "/staff/v1/sign-in/", {"email": email}).status_code, 204)
        response = self.call("POST", "/staff/v1/sign-in/code/", {"code": self.emailed_code()})
        self.assertEqual(response.status_code, 204)
        return response

    def staff_sign_in(self, email=EMAIL):
        self.staff_email_step(email)
        response = self.call("POST", "/staff/v1/sign-in/verify/", {"code": self.code()})
        self.assertEqual(response.status_code, 204)
        return response


class StaffRoutesOffTests(StaffTestCase):
    def test_unset_every_staff_path_answers_as_an_unknown_path_does_today(self):
        self.enrolled()
        # The second client checks the form token as a browser's request is checked.
        strict = Client(enforce_csrf_checks=True)
        for method, path in PATHS:
            for origin in (None, STAFF):
                with self.subTest(path=path, origin=origin):
                    got = self.call("GET", path, origin=origin)
                    want = self.call("GET", "/no-such-page/", origin=origin)
                    self.assertEqual((got.status_code, got.content), (404, want.content))
                    self.assertEqual(sorted(got.headers.items()), sorted(want.headers.items()))
                    got = self.call("POST", path, {"email": EMAIL}, origin=origin, client=strict)
                    want = self.call(
                        "POST", "/no-such-page/", {"email": EMAIL}, origin=origin, client=strict
                    )
                    self.assertEqual((got.status_code, got.content), (403, want.content))
                    self.assertNotIn("Access-Control-Allow-Origin", got.headers)
                    self.assertNotIn(PRE, got.cookies)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(OperatorAuditEntry.objects.exists())

    @override_settings(HQ_STAFF_ORIGIN="https://hq-app.vercel.app")
    def test_a_refused_value_leaves_the_routes_off(self):
        got = self.call("GET", "/staff/v1/me/", origin="https://hq-app.vercel.app")
        self.assertEqual((got.status_code, got.content), (404, self.get("/no-such-page/").content))

    @on_staff
    @override_settings(HQ_HOST="")
    def test_with_no_hq_host_the_routes_do_not_exist(self):
        self.assertEqual(self.call("GET", "/staff/v1/me/", host=OTHER).status_code, 404)

    @on_staff
    def test_other_hosts_serve_no_staff_route(self):
        for method, path in PATHS:
            response = self.call(method, path, host=OTHER)
            self.assertEqual(response.status_code, 404, path)
            self.assertNotIn("Access-Control-Allow-Origin", response.headers)


@on_staff
@override_settings(DASHBOARD_URL="https://dash.example.test")
class StaffOriginTests(StaffTestCase):
    def test_any_other_origin_is_refused_on_every_route(self):
        self.enrolled()
        others = (
            None, "https://dash.example.test", "https://hq-app-git-x.vercel.app",
            f"https://{HQ}", STAFF + "/", "null",
        )
        for method, path in PATHS:
            for origin in others:
                for verb in (method, "OPTIONS"):
                    with self.subTest(path=path, origin=origin, verb=verb):
                        data = method if verb == "OPTIONS" else {"email": EMAIL}
                        response = self.call(verb, path, data, origin=origin)
                        self.assertError(response, 403, "origin_not_allowed")
                        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
                        self.assertNotIn("Access-Control-Allow-Credentials", response.headers)
                        self.assertEqual(len(response.cookies), 0)
                        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(mail.outbox, [])
        self.assertFalse(OperatorAuditEntry.objects.exists())

    def test_another_origin_cannot_tell_a_route_from_no_route(self):
        there = self.call("GET", "/staff/v1/me/", origin="https://dash.example.test")
        absent = self.call("GET", "/staff/v1/nothing/", origin="https://dash.example.test")
        self.assertEqual((there.status_code, there.content), (absent.status_code, absent.content))

    def test_a_preflight_from_the_staff_origin_is_answered(self):
        for method, path in PATHS:
            response = self.call("OPTIONS", path, method)
            self.assertEqual(response.status_code, 204, path)
            self.assertEqual(response["Access-Control-Allow-Origin"], STAFF)
            self.assertEqual(response["Access-Control-Allow-Credentials"], "true")
            self.assertEqual(response["Access-Control-Allow-Methods"], f"{method}, OPTIONS")
            self.assertEqual(response["Access-Control-Allow-Headers"], "content-type")
            self.assertIn("Origin", response["Vary"])

    def test_an_unknown_path_and_a_wrong_method(self):
        self.assertError(self.call("GET", "/staff/v1/nothing/"), 404, "not_found")
        self.assertError(self.call("GET", "/staff/v1/sign-in/"), 405, "method_not_allowed")
        self.assertError(self.call("POST", "/staff/v1/me/"), 405, "method_not_allowed")
        self.assertError(self.call("GET", "/staff/v1/me"), 404, "not_found")

    def test_every_answer_is_uncached_unindexed_and_names_the_origin(self):
        response = self.call("GET", "/staff/v1/me/")
        self.assertError(response, 401, "unauthenticated")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        self.assertEqual(response["Access-Control-Allow-Origin"], STAFF)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_every_staff_answer_may_not_be_framed_or_sniffed(self):
        # X-Frame-Options is set by dispatch(): XFrameOptionsMiddleware is skipped.
        # X-Content-Type-Options comes from SecurityMiddleware, which still runs.
        answers = [
            self.call("GET", "/staff/v1/me/"),                                  # 401
            self.call("POST", "/staff/v1/sign-in/", {"email": "nobody@example.test"}),  # 204
            self.call("OPTIONS", "/staff/v1/sign-in/", "POST"),                 # preflight
            self.call("GET", "/staff/v1/nothing/"),                             # 404
            self.call("GET", "/staff/v1/me/", origin="https://dash.example.test"),  # 403
            self.call("GET", "/staff/v1/me/", origin=None),                     # 403
        ]
        with mock.patch("apps.operators.staff_views.hq_views.load_session", side_effect=RuntimeError):
            with self.assertLogs("apps.operators.staff_views", "ERROR"):
                answers.append(self.call("GET", "/staff/v1/me/"))               # 500
        for response in answers:
            with self.subTest(status=response.status_code):
                self.assertEqual(response["X-Frame-Options"], "DENY")
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_a_failing_route_answers_a_plain_json_error(self):
        with mock.patch("apps.operators.staff_views.hq_views.load_session", side_effect=RuntimeError):
            with self.assertLogs("apps.operators.staff_views", "ERROR"):
                response = self.call("GET", "/staff/v1/me/")
        self.assertError(response, 500, "server_error")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Access-Control-Allow-Origin"], STAFF)

    def test_the_api_prefix_is_still_not_found_on_the_hq_host(self):
        self.assertEqual(self.call("GET", "/v1/account/").status_code, 404)

    def test_health_is_untouched(self):
        self.assertEqual(self.client.get("/healthz/").status_code, 200)
        with override_settings(ALLOWED_HOSTS=["healthcheck.railway.app", HQ]):
            response = self.client.get("/healthz/", HTTP_HOST="healthcheck.railway.app")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_health_over_plain_http_is_untouched(self):
        from django.conf import settings

        self.assertEqual(settings.SECURE_REDIRECT_EXEMPT, [r"^healthz/$"])
        hosts = ["healthcheck.railway.app", HQ, "testserver"]
        with override_settings(SECURE_SSL_REDIRECT=True, ALLOWED_HOSTS=hosts):
            # Railway's check: plain http, its own host name. Answered, not redirected.
            response = self.client.get("/healthz/", HTTP_HOST="healthcheck.railway.app")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "ok")
            # A staff path over plain http is still redirected, like every other path.
            self.assertEqual(self.client.get("/staff/v1/me/", HTTP_HOST=HQ).status_code, 301)

    def test_the_staff_origin_changes_no_allowed_host(self):
        from pathlib import Path

        from django.conf import settings

        # Read as text: the settings module is never loaded a second time inside the suite.
        source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text()
        code = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
        self.assertEqual(
            [line for line in code if "HQ_STAFF_ORIGIN" in line],
            ['HQ_STAFF_ORIGIN = os.environ.get("HQ_STAFF_ORIGIN", "").strip()'],
        )
        self.assertEqual(
            [line for line in code if "SECURE_REDIRECT_EXEMPT" in line],
            ['SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]'],
        )
        # Under this class's override the staff host is still not a host the service answers on.
        self.assertNotIn("hq-app.example.test", settings.ALLOWED_HOSTS)
```

The settings file is read as text on purpose. Loading `config.settings` again inside the suite would re-read the environment and re-run that module's side effects in CI, so no test does it.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run python manage.py test apps.operators.tests.test_staff_routes -v 2`
Expected: `StaffRoutesOffTests` pass already (nothing is built, which is the point of them). `StaffOriginTests` fail: 404 where 403, 204, 401 and 405 are expected.

- [ ] **Step 3: Write `apps/operators/staff_views.py` with the origin rule and the two reads**

```python
"""The staff routes: what the staff app on Vercel calls on the hq host, under /staff/v1/.

They are in no URL list. HqHostMiddleware hands them here, and only while HQ_STAFF_ORIGIN is
usable, so with it unset the hq host answers as if this file did not exist. An answer from
here passes back through SecurityMiddleware only: no middleware listed after HqHostMiddleware
runs for it, which is why dispatch() sets X-Frame-Options itself. Django's form token
cannot be held by a page on another host; the one allowed Origin, required on every call,
takes its place. The codes are checked by hq_auth and two_step, as on the Django pages, and
the cookies are the same two cookies.
"""
import json
import logging
import secrets

from django.core import signing
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.cache import patch_vary_headers

from apps.accounts.sessions import client_ip
from apps.common.errors import error_body
from apps.operators import audit, background, hq_auth, hq_views, two_step

logger = logging.getLogger(__name__)

PREFIX = "/staff/v1/"
MAX_BODY = 2048
ALLOW_HEADERS = "content-type"
PREFLIGHT_MAX_AGE = "600"


def _error(status, code, message):
    return JsonResponse(error_body(code, message), status=status)


def _no_content():
    return HttpResponse(status=204)


def _gate(request):
    """(session, None) for a session that passed both steps, else (None, the 401 to send)."""
    session = hq_views.load_session(request)
    if session is None:
        return None, _error(401, "unauthenticated", "Sign in to continue.")
    if not hq_views.is_verified(session):
        return None, _error(
            401, "second_step_required", "Enter your authenticator code to continue."
        )
    return session, None


def me(request):
    session, refusal = _gate(request)
    if refusal:
        return refusal
    return JsonResponse(
        {"email": session.identity.email, "expires_at": session.expires_at.isoformat()}
    )


def console(request):
    session, refusal = _gate(request)
    if refusal:
        return refusal
    events = [
        {
            "action": entry.action,
            "surface": entry.detail.get("surface"),
            "step": entry.detail.get("step"),
            "at": entry.at.isoformat(),
        }
        for entry in audit.recent_sign_in_events(session.identity)
    ]
    return JsonResponse({
        "email": session.identity.email,
        "verified_at": session.verified_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "events": events,
    })


ROUTES = {
    "me/": ("GET", me),
    "console/": ("GET", console),
}


def _answer(request, allowed):
    # The origin is looked at before the path: another origin learns nothing about the routes.
    if not allowed:
        return _error(403, "origin_not_allowed", "This origin may not call the staff routes.")
    route = ROUTES.get(request.path[len(PREFIX):])
    if route is None:
        return _error(404, "not_found", "Not found.")
    method, view = route
    if request.method == "OPTIONS":
        response = _no_content()
        response["Access-Control-Allow-Methods"] = f"{method}, OPTIONS"
        response["Access-Control-Allow-Headers"] = ALLOW_HEADERS
        response["Access-Control-Max-Age"] = PREFLIGHT_MAX_AGE
        return response
    if request.method != method:
        return _error(405, "method_not_allowed", "Method not allowed.")
    return view(request)


def dispatch(request, origin):
    """Answer one call under PREFIX. `origin` is checks.active_staff_origin(), known to be set."""
    allowed = request.headers.get("Origin") == origin
    try:
        response = _answer(request, allowed)
    except Exception:
        logger.exception("staff route failed")
        response = _error(500, "server_error", "Something went wrong.")
    if allowed:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Credentials"] = "true"
    patch_vary_headers(response, ["Origin"])
    # XFrameOptionsMiddleware comes after HqHostMiddleware and never sees a staff answer.
    response["X-Frame-Options"] = "DENY"
    return response
```

- [ ] **Step 4: Hand the prefix over in `HqHostMiddleware`**

In `apps/operators/middleware.py`, change the import line to:

```python
from apps.operators import checks, hq_views, staff_views
```

and replace the `if`/`else` inside `HqHostMiddleware.__call__` with:

```python
        staff_origin = (
            checks.active_staff_origin() if request.path.startswith(staff_views.PREFIX) else ""
        )
        if request.path.startswith(API_PREFIX):
            # The API's origin middleware answers on its prefix before any URL is resolved.
            response = hq_views.not_found(request)
        elif staff_origin:
            # Answered here, before the form-token check: the one allowed Origin replaces it.
            response = staff_views.dispatch(request, staff_origin)
        else:
            response = self.get_response(request)
```

The lines that set `Cache-Control` and `X-Robots-Tag` after it are not changed, so staff answers carry them too. With the origin unset or refused, `staff_origin` is `""` and the request takes the last branch, which is today's.

- [ ] **Step 5: Run the tests and see them pass**

Run: `uv run python manage.py test apps.operators.tests.test_staff_routes -v 2`
Expected: OK. In `test_any_other_origin_is_refused_on_every_route` the four POST routes are not in `ROUTES` yet; they still answer 403, because the origin is checked first. In `test_every_staff_answer_may_not_be_framed_or_sniffed` the `sign-in/` call answers 404 until task 3 and 204 after it; the test holds the two headers on whatever status comes back.

- [ ] **Step 6: Run the whole operators suite, to see the Django pages untouched**

Run: `uv run python manage.py test apps.operators apps.health`
Expected: OK, with no test changed.

- [ ] **Step 7: Commit**

```bash
git add apps/operators/staff_views.py apps/operators/middleware.py apps/operators/tests/test_staff_routes.py
git commit -m "feat: staff routes on the hq host, off unless HQ_STAFF_ORIGIN is set, one origin only [z8uq9me128]"
```

## Task 3: Sign-in, code, second step and sign-out

**Files:**
- Modify: `apps/operators/staff_views.py`
- Test: `apps/operators/tests/test_staff_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/operators/tests/test_staff_routes.py`:

```python
def _attributes(morsel):
    return {k: morsel[k] for k in ("path", "domain", "secure", "httponly", "samesite")}


@on_staff
class StaffSignInTests(StaffTestCase):
    def actions(self):
        return list(OperatorAuditEntry.objects.order_by("pk").values_list("action", flat=True))

    def test_the_full_path(self):
        identity = self.enrolled()
        self.assertError(self.call("GET", "/staff/v1/me/"), 401, "unauthenticated")

        response = self.call("POST", "/staff/v1/sign-in/", {"email": f"  {EMAIL.upper()} "})
        self.assertEqual((response.status_code, response.content), (204, b""))
        self.assertEqual(response.cookies[PRE]["max-age"], 600)
        self.assertEqual(len(mail.outbox), 1)

        response = self.call("POST", "/staff/v1/sign-in/code/", {"code": self.emailed_code()})
        self.assertEqual((response.status_code, response.content), (204, b""))
        self.assertEqual(response.cookies[PRE].value, "")
        self.assertEqual(response.cookies[SESSION]["max-age"], 12 * 3600)
        first_token = response.cookies[SESSION].value
        self.assertError(self.call("GET", "/staff/v1/me/"), 401, "second_step_required")
        self.assertError(self.call("GET", "/staff/v1/console/"), 401, "second_step_required")

        response = self.call("POST", "/staff/v1/sign-in/verify/", {"code": self.code()})
        self.assertEqual((response.status_code, response.content), (204, b""))
        self.assertNotEqual(response.cookies[SESSION].value, first_token)
        self.assertLessEqual(response.cookies[SESSION]["max-age"], 12 * 3600)

        session = AdminSession.objects.get()
        body = self.call("GET", "/staff/v1/me/").json()
        self.assertEqual(body, {"email": EMAIL, "expires_at": session.expires_at.isoformat()})

        body = self.call("GET", "/staff/v1/console/").json()
        self.assertEqual(
            sorted(body), ["email", "events", "expires_at", "verified_at"]
        )
        self.assertEqual(body["verified_at"], session.verified_at.isoformat())
        self.assertEqual(
            [(e["action"], e["surface"], e["step"]) for e in body["events"]],
            [("admin_sign_in_ok", "hq", "totp")],
        )
        self.assertEqual(sorted(body["events"][0]), ["action", "at", "step", "surface"])

        response = self.call("POST", "/staff/v1/sign-out/")
        self.assertEqual((response.status_code, response.content), (204, b""))
        self.assertEqual(response.cookies[SESSION].value, "")
        self.assertIsNotNone(AdminSession.objects.get().revoked_at)
        self.assertError(self.call("GET", "/staff/v1/me/"), 401, "unauthenticated")
        self.assertEqual(self.actions(), ["admin_sign_in_ok", "admin_sign_out"])
        self.assertEqual(
            OperatorAuditEntry.objects.filter(target_id=str(identity.pk)).count(), 2
        )

    def test_no_answer_holds_a_token_or_a_code(self):
        self.enrolled()
        self.staff_sign_in()
        token = self.client.cookies[SESSION].value
        for path in ("/staff/v1/me/", "/staff/v1/console/"):
            text = self.call("GET", path).content.decode()
            self.assertNotIn(token, text)
            self.assertNotIn(self.secret, text)

    def test_the_cookies_are_the_ones_the_django_pages_set(self):
        self.enrolled()
        django, staff = Client(), Client()
        self.post("/sign-in/", {"email": EMAIL}, client=django)
        via_page = self.post("/code/", {"code": self.emailed_code()}, client=django)
        self.call("POST", "/staff/v1/sign-in/", {"email": EMAIL}, client=staff)
        via_route = self.call(
            "POST", "/staff/v1/sign-in/code/", {"code": self.emailed_code()}, client=staff
        )
        want = {"path": "/", "domain": "", "secure": True, "httponly": True, "samesite": "Strict"}
        for name in (SESSION, PRE):
            self.assertEqual(_attributes(via_route.cookies[name]), _attributes(via_page.cookies[name]))
        self.assertEqual(_attributes(via_route.cookies[SESSION]), want)

    def test_both_ways_in_make_the_same_session(self):
        self.enrolled()
        self.staff_sign_in()
        self.assertEqual(self.get("/").status_code, 200)  # the Django console opens
        other = Client()
        self.post("/sign-in/", {"email": EMAIL}, client=other)
        self.post("/code/", {"code": self.emailed_code()}, client=other)
        self.post("/verify/", {"code": self.code(1)}, client=other)
        self.assertEqual(self.call("GET", "/staff/v1/me/", client=other).json()["email"], EMAIL)

    def test_a_code_asked_for_through_the_route_can_be_finished_on_the_django_page(self):
        self.enrolled()
        self.call("POST", "/staff/v1/sign-in/", {"email": EMAIL})
        page = self.get("/code/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, EMAIL)

    def test_every_well_formed_address_gets_the_same_answer(self):
        self.enrolled()
        answers = []
        for email in (EMAIL, "nobody@example.test"):
            response = self.call("POST", "/staff/v1/sign-in/", {"email": email}, client=Client())
            answers.append((response.status_code, response.content, sorted(response.cookies)))
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(len(mail.outbox), 1)

    def test_an_over_limit_address_gets_the_same_answer_and_no_mail(self):
        self.enrolled()
        for _ in range(5):
            self.call("POST", "/staff/v1/sign-in/", {"email": EMAIL})
        sent = len(mail.outbox)
        response = self.call("POST", "/staff/v1/sign-in/", {"email": EMAIL})
        self.assertEqual(response.status_code, 204)
        self.assertIn(PRE, response.cookies)
        self.assertEqual(len(mail.outbox), sent)

    def test_a_malformed_address_or_body_is_refused_before_any_counter(self):
        bodies = ({"email": "not-an-address"}, {"email": 5}, {}, [], "x")
        with mock.patch("apps.operators.hq_auth.within_limits") as limits:
            for body in bodies:
                response = self.call("POST", "/staff/v1/sign-in/", body)
                self.assertError(response, 400, "invalid_email")
                self.assertNotIn(PRE, response.cookies)
            raw = self.client.post(
                "/staff/v1/sign-in/", data="{", content_type="application/json",
                secure=True, HTTP_HOST=HQ, HTTP_ORIGIN=STAFF,
            )
            self.assertError(raw, 400, "invalid_email")
            big = self.call("POST", "/staff/v1/sign-in/", {"email": EMAIL, "pad": "x" * 3000})
            self.assertError(big, 400, "invalid_email")
        limits.assert_not_called()

    def test_a_wrong_code_is_refused_and_audited(self):
        self.enrolled()
        self.call("POST", "/staff/v1/sign-in/", {"email": EMAIL})
        wrong = "000000" if self.emailed_code() != "000000" else "111111"
        response = self.call("POST", "/staff/v1/sign-in/code/", {"code": wrong})
        self.assertError(response, 400, "invalid_code")
        self.assertNotIn(SESSION, response.cookies)
        self.assertEqual(self.actions(), ["admin_sign_in_failed"])

    def test_a_code_without_the_ten_minute_cookie_gets_the_same_refusal(self):
        self.enrolled()
        self.call("POST", "/staff/v1/sign-in/", {"email": EMAIL})
        answers = []
        for client in (self.client, Client()):
            response = self.call("POST", "/staff/v1/sign-in/code/", {"code": "12345"}, client=client)
            answers.append((response.status_code, response.content))
        self.assertEqual(answers[0], answers[1])
        self.assertError(
            self.call("POST", "/staff/v1/sign-in/code/", {"code": self.emailed_code()}, client=Client()),
            400, "invalid_code",
        )
        self.assertFalse(AdminSession.objects.exists())

    def test_the_second_step_needs_the_first(self):
        self.enrolled()
        response = self.call("POST", "/staff/v1/sign-in/verify/", {"code": self.code()})
        self.assertError(response, 401, "unauthenticated")

    def test_a_recovery_code_passes_the_second_step_once(self):
        self.enrolled()
        self.staff_email_step()
        recovery = self.recovery_codes[0]
        spaced = f"{recovery[:5]}-{recovery[5:]}".lower()
        response = self.call("POST", "/staff/v1/sign-in/verify/", {"code": spaced})
        self.assertEqual(response.status_code, 204)
        entry = OperatorAuditEntry.objects.get(action="admin_sign_in_ok")
        self.assertEqual(entry.detail, {"surface": "hq", "step": "recovery"})
        again = Client()
        self.staff_email_step_for(again)
        self.assertError(
            self.call("POST", "/staff/v1/sign-in/verify/", {"code": recovery}, client=again),
            400, "invalid_code",
        )

    def staff_email_step_for(self, client):
        self.call("POST", "/staff/v1/sign-in/", {"email": EMAIL}, client=client)
        self.call("POST", "/staff/v1/sign-in/code/", {"code": self.emailed_code()}, client=client)

    def test_a_wrong_second_step_code_is_refused_and_audited(self):
        self.enrolled()
        self.staff_email_step()
        response = self.call("POST", "/staff/v1/sign-in/verify/", {"code": self.wrong_code()})
        self.assertError(response, 400, "invalid_code")
        self.assertEqual(self.actions(), ["admin_sign_in_failed"])
        self.assertError(self.call("GET", "/staff/v1/me/"), 401, "second_step_required")

    def test_five_wrong_codes_pause_and_end_every_session(self):
        identity = self.enrolled()
        elsewhere = Client()
        self.staff_email_step_for(elsewhere)
        self.call("POST", "/staff/v1/sign-in/verify/", {"code": self.code()}, client=elsewhere)
        self.staff_email_step()
        mail.outbox.clear()
        with self.committed():
            for _ in range(4):
                self.assertError(
                    self.call("POST", "/staff/v1/sign-in/verify/", {"code": self.wrong_code()}),
                    400, "invalid_code",
                )
            response = self.call("POST", "/staff/v1/sign-in/verify/", {"code": self.wrong_code()})
        self.assertError(response, 403, "sign_in_paused")
        self.assertEqual(response.cookies[SESSION].value, "")
        self.assertEqual(response.cookies[PRE].value, "")
        self.assertEqual(self.actions()[-1], "admin_locked")
        identity.refresh_from_db()
        self.assertGreater(identity.locked_until, timezone.now() + timedelta(minutes=14))
        self.assertEqual([m.subject for m in mail.outbox], ["NegosyoOnline admin sign-in paused"])
        self.assertError(self.call("GET", "/staff/v1/me/", client=elsewhere), 401, "unauthenticated")

    def test_a_signed_in_person_is_told_so(self):
        self.enrolled()
        self.staff_sign_in()
        sent = len(mail.outbox)
        for path, body in (
            ("/staff/v1/sign-in/", {"email": EMAIL}),
            ("/staff/v1/sign-in/code/", {"code": "123456"}),
            ("/staff/v1/sign-in/verify/", {"code": self.code(1)}),
        ):
            self.assertError(self.call("POST", path, body), 409, "already_signed_in")
        self.assertEqual(len(mail.outbox), sent)

    def test_sign_out_without_a_session_answers_the_same(self):
        response = self.call("POST", "/staff/v1/sign-out/")
        self.assertEqual((response.status_code, response.content), (204, b""))
        self.assertFalse(OperatorAuditEntry.objects.exists())

    def test_an_ended_or_expired_session_reads_nothing(self):
        self.enrolled()
        self.staff_sign_in()
        AdminSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertError(self.call("GET", "/staff/v1/console/"), 401, "unauthenticated")

    def test_the_django_pages_still_work_and_still_need_their_form_token(self):
        self.enrolled()
        self.sign_in()  # HqTestCase: the three Django pages, in order
        self.assertEqual(self.get("/").status_code, 200)
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(self.post("/sign-in/", {"email": EMAIL}, client=strict).status_code, 403)
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run python manage.py test apps.operators.tests.test_staff_routes.StaffSignInTests -v 2`
Expected: failures, 404 `not_found` where 204 is expected, on every test that calls a POST route.

- [ ] **Step 3: Add the four routes to `apps/operators/staff_views.py`**

Add after `_no_content`:

```python
def _body(request):
    """The JSON object sent, or {} when there is none or it is not one."""
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_BODY:
            return {}
        data = json.loads(request.body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _text(request, key):
    value = _body(request).get(key)
    return value if isinstance(value, str) else ""


def _already_signed_in(request):
    """The 409 for a session that passed both steps, else None."""
    if hq_views.is_verified(hq_views.load_session(request)):
        return _error(409, "already_signed_in", "You are already signed in.")
    return None
```

Add after `console`:

```python
def sign_in(request):
    refusal = _already_signed_in(request)
    if refusal:
        return refusal
    email = hq_auth.normalize_email(_text(request, "email"))
    if email is None:
        return _error(400, "invalid_email", "Enter a valid email address.")
    # From here every address gets the same answer. The lookup and the email happen later.
    nonce = secrets.token_urlsafe(16)
    if hq_auth.within_limits(email, client_ip(request)):
        try:
            background.run_later(hq_auth.issue_email_code, email, nonce)
        except Exception:
            logger.exception("hq sign-in code could not be handed over")
    response = _no_content()
    value = signing.dumps({"email": email, "nonce": nonce}, salt=hq_views.PRE_SALT)
    hq_views._set_cookie(response, hq_views._cookie_names()[1], value, hq_views.PRE_SECONDS)
    return response


def code(request):
    refusal = _already_signed_in(request)
    if refusal:
        return refusal
    pre = hq_views._pre(request)
    typed = "".join(_text(request, "code").split())
    token = None
    if pre is not None and len(typed) == 6 and typed.isdigit():
        token = hq_auth.check_email_code(pre["email"], pre["nonce"], typed)
    if token is None:
        # One answer for a wrong code, a used one, a stale one and a browser that never asked.
        return _error(400, "invalid_code", "That code did not work.")
    session_name, pre_name = hq_views._cookie_names()
    response = hq_views._drop_cookies(_no_content(), pre_name)
    hq_views._set_cookie(response, session_name, token, hq_auth.SESSION_HOURS * 3600)
    return response


def verify(request):
    session = hq_views.load_session(request)
    if session is None:
        return _error(401, "unauthenticated", "Sign in to continue.")
    if hq_views.is_verified(session):
        return _error(409, "already_signed_in", "You are already signed in.")
    result = two_step.verify_second_step(
        session.identity_id, _text(request, "code"), surface=two_step.SURFACE_HQ
    )
    if result.locked:
        paused = _error(
            403, "sign_in_paused", "Too many wrong codes. Sign-in is paused for 15 minutes."
        )
        return hq_views._drop_cookies(paused, *hq_views._cookie_names())
    if not result.ok:
        return _error(400, "invalid_code", "That code did not work.")
    token = hq_auth.mark_verified(session, result.credential_version)
    response = _no_content()
    remaining = int((session.expires_at - timezone.now()).total_seconds())
    hq_views._set_cookie(response, hq_views._cookie_names()[0], token, max(remaining, 0))
    return response


def sign_out(request):
    session = hq_views.load_session(request)
    if session is not None:
        hq_auth.sign_out(session)
    return hq_views._drop_cookies(_no_content(), *hq_views._cookie_names())
```

Replace `ROUTES` with:

```python
ROUTES = {
    "sign-in/": ("POST", sign_in),
    "sign-in/code/": ("POST", code),
    "sign-in/verify/": ("POST", verify),
    "sign-out/": ("POST", sign_out),
    "me/": ("GET", me),
    "console/": ("GET", console),
}
```

Each function body is the matching Django view of `hq_views.py` with its page answers replaced by JSON. The calls into `hq_auth` and `two_step`, their order and their arguments are the same. The surface written to the audit record stays `hq`, as design 22.5 says.

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run python manage.py test apps.operators.tests.test_staff_routes -v 2`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add apps/operators/staff_views.py apps/operators/tests/test_staff_routes.py
git commit -m "feat: staff sign-in, code, second step and sign-out routes on today's functions and cookies [z8uq9me128]"
```

## Task 4: The runbook, and the full checks

**Files:**
- Modify: `docs/runbooks/admin-two-step.md`

- [ ] **Step 1: Add the variable to the runbook's table**

Under the `HQ_HOST` row of "Variables":

```markdown
| `HQ_STAFF_ORIGIN` | The one browser origin that may call the staff routes on the hq host (`/staff/v1/`): the staff app's address, written as `https://` and a host name with nothing after it. Empty: the routes do not exist and hq answers as it did before them. It must be a name directly beside `HQ_HOST` under the same parent, and must not be the dashboard, the public site, an API origin or a preview address. |
```

- [ ] **Step 2: Add the log line**

Under the `hq-host-refused:` line of the log lines list:

```markdown
- `hq-staff-origin-refused:` `HQ_STAFF_ORIGIN` is malformed, clashes with another address, or is not beside `HQ_HOST`. The staff routes stay off until it is fixed; the hq pages are not affected.
```

- [ ] **Step 3: Add how to turn the routes off**

A new section after "Release order":

```markdown
## The staff routes

The staff app's pages call `/staff/v1/` on the hq host. Those routes check the same codes with the same limits as the hq pages and set the same cookie. They answer one origin only, `HQ_STAFF_ORIGIN`.

To stop every staff call at once, for example if the staff app's deploy is in doubt: delete `HQ_STAFF_ORIGIN` and redeploy. The hq pages keep working. To also end every live session of a person, run `admin_identity reset` for them.

Setting or deleting `HQ_STAFF_ORIGIN` on staging or production needs Coach Ed's approval each time.

On production it also waits for the NPC wording amendment: from the day it is set, Vercel serves the page where both admin codes are typed, and the NPC drafts describe one Django sign-in surface. Staging is not affected.

The audit record does not say which way a sign-in came in. A sign-in through the staff app's page and one through the hq pages are both written with the surface `hq`.
```

- [ ] **Step 4: Run every check the repository's CI runs**

```bash
uv run python manage.py makemigrations --check --dry-run
```
Expected: `No changes detected`.

```bash
uv run python manage.py check
```
Expected: `System check identified no issues (0 silenced).`

```bash
uv run python manage.py test
```
Expected: OK, with no existing test changed.

```bash
DJANGO_DEBUG=false DJANGO_SECRET_KEY=local-check-only uv run python manage.py check --deploy
```
Expected: the same warnings as on `main` and no new one. Compare with the same command run on `origin/main` first.

- [ ] **Step 5: Show that the shared files changed only as section 4 says**

```bash
git fetch origin main && git diff origin/main...HEAD --stat -- config/settings.py config/api.py pyproject.toml uv.lock
```
Expected: one line, `config/settings.py | 4 ++++`. No line for `config/api.py`, `pyproject.toml` or `uv.lock`.

```bash
git diff origin/main...HEAD -- config/settings.py | grep '^[-+]' | grep -v '^+++\|^---'
```
Expected: exactly the four `+` lines of 4.1 and no `-` line.

```bash
grep -n 'SECURE_REDIRECT_EXEMPT' config/settings.py
```
Expected: one line, `SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]`, as on `main`.

Report the three outputs to the coordinator with the build PR, for the NPC coordinator's review.

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/admin-two-step.md
git commit -m "docs: the admin two-step runbook names HQ_STAFF_ORIGIN and how to turn the staff routes off [z8uq9me128]"
```

## Task 5: Check locally, by hand

Local first (builder brief, section 7). `DEBUG` is on locally, so the cookies are named `no_hq` and `no_hq_pre` and are not `Secure`. **This local check proves the routes, the origin rule and the off switch. It cannot prove the browser's cookie behaviour between two https addresses; only section 7 can.**

It needs a local admin identity that is enrolled (`docs/runbooks/admin-two-step.md`, "Commands"). The emailed code is printed in the server's log, because no `EMAIL_HOST` is set locally.

- [ ] **Step 1: Run the server with the origin unset, and see today's answers**

```bash
HQ_HOST=ggwp.negosyotest.com uv run python manage.py runserver 8000
```

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://ggwp.negosyotest.com:8000/staff/v1/me/
```
Expected: `404`, the plain "Page not found". Environment: local, http, port 8000.

- [ ] **Step 2: Run it with the origin set, and walk the path with curl**

```bash
HQ_HOST=ggwp.negosyotest.com HQ_STAFF_ORIGIN=http://hq.negosyotest.com:3002 uv run python manage.py runserver 8000
```

`hq.negosyotest.com` need not resolve for this: `curl` only sends it as a header. In another terminal, with `-c` and `-b` keeping the cookies in a file under the scratch folder:

```bash
curl -s -i -c jar -b jar -H 'Origin: http://hq.negosyotest.com:3002' -H 'content-type: application/json' -d '{"email":"THE-LOCAL-ADMIN-ADDRESS"}' http://ggwp.negosyotest.com:8000/staff/v1/sign-in/
```
Expected: `204`, `Set-Cookie: no_hq_pre=…; HttpOnly; Max-Age=600; Path=/; SameSite=Strict`, `Access-Control-Allow-Origin: http://hq.negosyotest.com:3002`, `Access-Control-Allow-Credentials: true`, `Cache-Control: no-store`.

Then the same command against `/staff/v1/sign-in/code/` with `{"code":"…"}` from the server's log, against `/staff/v1/sign-in/verify/` with the authenticator code, a GET of `/staff/v1/console/`, and a POST of `/staff/v1/sign-out/`. Expected: 204, 204, 200 with the four fields, 204.

- [ ] **Step 3: See the refusals**

The same `sign-in/` command without the `Origin` header, and with `-H 'Origin: http://dash.negosyotest.com:3001'`.
Expected: `403` and `origin_not_allowed` both times, no `Set-Cookie`, no `Access-Control-Allow-Origin`.

- [ ] **Step 4: See the Django pages unchanged**

Open `http://ggwp.negosyotest.com:8000/sign-in/` in a browser and sign in with both steps. Expected: the console, as before.

- [ ] **Step 5: Report** to the coordinator each command with its actual answer, and what was not checked.

---

## 7. The staging proof, a gated step of its own

**Nothing in this section is approved by approving this plan. Each row of 7.3 is Coach Ed's approval, asked for one at a time, through the coordinator. The coordinator carries out what he approves; the builder does not change Railway, Vercel or DNS.**

### 7.1 What has to be proven, and why it is not known

The design chose today's cookie on the reasoning that a browser sends a `SameSite=Strict` cookie when the calling page and the called address are under the same registered domain (design 20.4, 20.5). That was reasoned from how browsers are documented to behave. It was never tested for this project, and the design says so (17, 22.7). Until it is, every later plan stands on an assumption.

To prove, in a real browser, on staging, over https:

| # | Question | Expected, to be shown true or false |
|---|---|---|
| 1 | Does the browser **store** the `__Host-no_hq_pre` and `__Host-no_hq` cookies when they arrive in the answer to a call made from the staff page to the HQ host (`credentials: "include"`)? | Yes |
| 2 | Does it **send** them on the next call from the staff page? | Yes: `sign-in/code/` and `sign-in/verify/` succeed, `me/` answers 200 |
| 3 | Are the cookies stored with `Secure`, `HttpOnly`, `SameSite=Strict`, path `/` and no domain, under the HQ host only? | Yes, as read in the browser's storage panel |
| 4 | Can script on the staff page read them? | No: `document.cookie` on the staff page does not show them |
| 5 | Does the preflight pass, and does the browser let the page read the JSON answers? | Yes |
| 6 | Is a call from any other origin refused, with nothing stored? | Yes: the dashboard's origin gets 403 and no cookie |
| 7 | Does a page on an unrelated domain (a `vercel.app` preview address) get anywhere? | No: refused by the origin rule; and the browser sends no HQ cookie with it |
| 8 | Is a session made this way the same session the Django pages make, and is each step in the audit record? | Yes: the Django console at the HQ host opens with it; `admin_sign_in_ok` and `admin_sign_out` are written |
| 9 | Does the same hold in Chrome, Safari and Firefox on a computer? | Unknown until run |
| 10 | Does the same hold in Safari on an iPhone and Chrome on Android? | Unknown until run. These browsers have no console to type into, so this row waits for plan 2's sign-in page (7.5) |

### 7.2 Two arrangements of the names. Which one is his

The proof needs a page served over https from a name that is **not** the HQ host and **is** beside it under `negosyonline.com`. The design's final names (22.7, a proposal) put the staff app at `hq-staging.negosyonline.com`, which is the HQ host today (finding 6 of section 3).

| | **A. A temporary name for the staff page (recommended for this plan)** | B. The final names now |
|---|---|---|
| The HQ host on Railway | Stays `hq-staging.negosyonline.com` | Becomes `hq-api-staging.negosyonline.com` |
| The staff page on Vercel | `hq-app-staging.negosyonline.com`, a name used only until plan 10 | `hq-staging.negosyonline.com` |
| The Django HQ pages | Stay at the address staff know | Move to `hq-api-staging.negosyonline.com`; the old address shows the proof page |
| What it changes on Railway | One new variable | One new variable, one changed variable (`HQ_HOST`), one new custom domain, one removed |
| What it proves | Everything in 7.1. The cookie is host-only on the backend's name; which sibling name the page has makes no difference to a browser | The same, and also that `hq-api-staging` passes the `HQ_HOST` start-up check on staging |
| Going back | Delete the variable | Delete the variable, set `HQ_HOST` back, move the domains back |
| Fits this plan's boundary | Yes: the Django pages keep working unchanged | No: it is plan 10's rename, done early |

**Recommended: A.** It is the smallest change that answers the question, it leaves the way staff sign in today untouched, and it is undone by deleting one variable. B is plan 10's work. Both keep the staff routes on the HQ host's Railway address, apart from the Subscriber API host `api-staging.negosyonline.com`, which is never given a staff route.

### 7.3 What would be set on staging, each one Coach Ed's approval

Under arrangement A. Example values; the real ones are his.

| # | Where | What | Example value | Approval |
|---|---|---|---|---|
| 1 | GitHub | The build PR of this plan merged to `main` and deployed to staging, with the variable still unset | | Coach Ed: the build PR. The coordinator merges on green CI |
| 2 | DNS for `negosyonline.com` | A record for the staff page's name, pointing at Vercel | `hq-app-staging` CNAME to the value Vercel gives | Coach Ed |
| 3 | Vercel | A project, or a domain on plan 2's project, that serves a page at that name over https. For the proof a blank page is enough. **No environment variable, no secret, no integration** (design 21.2) | `https://hq-app-staging.negosyonline.com` | Coach Ed |
| 4 | Railway, staging, the service that answers `HQ_HOST` | New variable `HQ_STAFF_ORIGIN` | `https://hq-app-staging.negosyonline.com` | Coach Ed |

Not set and not changed: `HQ_HOST`, `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `API_ALLOWED_ORIGINS`, `ADMIN_SECRETS_KEY`, `DASHBOARD_URL`, `SITE_URL`, `SITE_PARENT_HOST`. No migration. Nothing on production, which does not exist yet.

Under arrangement B the rows would instead be: `HQ_HOST=hq-api-staging.negosyonline.com`; `HQ_STAFF_ORIGIN=https://hq-staging.negosyonline.com`; the Railway custom domain `hq-api-staging.negosyonline.com` added to the service and `hq-staging.negosyonline.com` removed from it; DNS for both names; the Vercel domain `hq-staging.negosyonline.com`. Each is his approval.

Facts from the NPC coordinator's work of 2026-11-11, relayed by the coordinator and not checked by this session: the staging `api` service has Healthcheck Path `/healthz/` with a timeout of 120, so a deployment that breaks `/healthz/` does not go live; and `DJANGO_ALLOWED_HOSTS` on staging `api` lists `healthcheck.railway.app`, checked by name.

Still unread by anyone for this plan: the value of `HQ_HOST` on staging, and which custom domains that service holds. The coordinator confirms both before row 4.

### 7.4 The steps

Done by a person who holds an enrolled staging admin identity, with the coordinator. No code or secret is pasted into chat, a ticket or a commit.

- [ ] **Step 1: Before the variable is set**, with the build deployed: `https://hq-staging.negosyonline.com/staff/v1/me/` answers the plain "Page not found", and `https://api-staging.negosyonline.com/healthz/` answers 200 with the deployed commit. The Django sign-in works. The deployment itself succeeded, which on staging means Railway's health check (plain http, host `healthcheck.railway.app`, path `/healthz/`) passed on this build; the coordinator confirms by name only that `DJANGO_ALLOWED_HOSTS` still lists `healthcheck.railway.app`.
- [ ] **Step 2: After rows 2 to 4**, read the service log for `hq-staff-origin-refused:`. If it is there, the value was refused and the routes are off: stop and report the line.
- [ ] **Step 3: With curl**, no browser yet:

```bash
curl -s -i -X OPTIONS -H 'Origin: https://hq-app-staging.negosyonline.com' -H 'Access-Control-Request-Method: POST' https://hq-staging.negosyonline.com/staff/v1/sign-in/
```
Expected: `204`, `Access-Control-Allow-Origin: https://hq-app-staging.negosyonline.com`, `Access-Control-Allow-Credentials: true`.

```bash
curl -s -i -H 'Origin: https://dash-staging.negosyonline.com' https://hq-staging.negosyonline.com/staff/v1/me/
```
Expected: `403`, `origin_not_allowed`, no `Access-Control-Allow-Origin`.

- [ ] **Step 4: In Chrome on a computer**, open `https://hq-app-staging.negosyonline.com`, open the developer tools' console, and run, one at a time, with the real address and codes typed in by the person:

```js
const hq = "https://hq-staging.negosyonline.com/staff/v1/";
const call = (path, body) => fetch(hq + path, {
  method: body ? "POST" : "GET", credentials: "include",
  headers: body ? {"content-type": "application/json"} : {},
  body: body ? JSON.stringify(body) : undefined,
}).then(async r => [r.status, await r.text()]);

await call("sign-in/", {email: "…"});          // expect [204, ""]
await call("sign-in/code/", {code: "…"});      // expect [204, ""]
await call("me/");                             // expect [401, …second_step_required…]
await call("sign-in/verify/", {code: "…"});    // expect [204, ""]
await call("console/");                        // expect [200, …]
document.cookie;                               // expect no no_hq in it
```

Read the Network panel for each call (the `Set-Cookie` on the answer, the `Cookie` on the next request) and the Application panel's cookies for `hq-staging.negosyonline.com` (rows 1 to 4 of 7.1). Then open `https://hq-staging.negosyonline.com/` in a new tab: the Django console opens without signing in again (row 8). Then `await call("sign-out/", {})`, expect `[204, ""]`, and `await call("me/")`, expect 401 `unauthenticated`.

- [ ] **Step 5: The other origin, in the same browser.** On `https://dash-staging.negosyonline.com`, run the same `call("me/")`. Expected: the browser reports the call as blocked; the Network panel shows 403 (row 6). If a `vercel.app` preview address of any project is at hand, the same there (row 7).
- [ ] **Step 6: Repeat step 4** in Safari and in Firefox on a computer (row 9).
- [ ] **Step 7: Read the audit record** for that identity, with the terminal command the runbook gives or in the Admin's list: one `admin_sign_in_ok` and one `admin_sign_out` per browser, surface `hq`.
- [ ] **Step 8: Report** to the coordinator, per browser and per row of 7.1: what was seen. The report says which rows were not run.

### 7.5 What waits on the proof

- Plan 2 may be written while this runs. **No page of plan 2 beyond the sign-in pages is built, and none of plans 3 to 9 is built, until rows 1 to 9 of 7.1 are shown true.**
- Row 10 (phones) is run on plan 2's sign-in page as its first staging check, before plan 2 builds anything behind the sign-in.
- If arrangement A is used, plan 10 repeats rows 1 to 3 on the final names.

### 7.6 If it fails

If any of rows 1 to 8 is false in any browser of row 9, or later of row 10:

1. **Stop.** Delete `HQ_STAFF_ORIGIN` on staging. The routes are gone and the Django pages are the way in, as before. Nothing else has been built on the cookie.
2. **Report** to the coordinator exactly which row failed, in which browser and version, with the request and answer headers as the browser showed them.
3. **Decide nothing.** What to do instead is a security decision of the kind R26 was, and it is Coach Ed's. The choices that exist, with what the design already said of each, for him and not chosen here:

| Choice | What the design says of it |
|---|---|
| Make the cookie `SameSite=None` | Weaker. "This design asks that it never is" (20.4) |
| Give the cookie a `Domain` of the parent, so every name under it receives it | Not possible while the cookie is named `__Host-…` (finding 1). It would mean renaming the cookie and sending the staff session to the dashboard's and the public site's hosts too. Not in the design |
| A token the page holds (way 2) | Considered and not chosen: script can read it, and ending access at once needs a revocation list (20.4) |
| Send the staff page's calls through Vercel so the browser sees one host | Forbidden by section 21: every code, number and text would pass through Vercel (20.4) |
| Keep the sign-in pages on Django | What R24 moved away from. It would reopen R24 |

A failure limited to one browser is still a failure to report; whether that browser matters is also his.

## 8. Verification, and what stays unverified

| Where | What is verified | By |
|---|---|---|
| Tests | Every row of 6.1 | Tasks 1 to 3, `uv run python manage.py test` |
| CI's checks | No migration, `check`, `check --deploy` | Task 4 |
| Local, http | The routes, the origin rule, the off switch, the Django pages unchanged | Task 5 |
| Staging, https | The cookie between the two addresses | Section 7, gated |

Unverified after this plan is built, said plainly:

- **The browser's cookie behaviour**, until section 7 is run. Tests use Django's test client, which is not a browser and applies no `SameSite` or `__Host-` rule.
- **Phones** (row 10 of 7.1), until plan 2's sign-in page exists.
- **Staging's real values.** This session has not seen `HQ_HOST`, `SITE_PARENT_HOST`, `DASHBOARD_URL`, `SITE_URL` or `API_ALLOWED_ORIGINS` on staging. Whether a proposed name passes the start-up check there is known only when the service log is read in step 2 of 7.4. Whether `hq-api-staging` passes the `HQ_HOST` check was reasoned from `is_reserved`, not run.
- **The value of `HQ_HOST` on staging and which custom domains its service holds**, and whether the deploy runs `manage.py check`. A running service does not stop on a bad value: it logs one line and leaves the routes off.
- **The code in this plan** has not been run. The builder runs each test before and after each change and reports the real output; where a test in this plan is wrong, the builder says so and corrects the test to the behaviour of section 5, not the other way round.
- **Section 21's rules on the Vercel project** (who can deploy, previews, no third-party script). They are plan 2's to check against the real project.
- **The audit record cannot tell the two ways in apart.** A sign-in made through the Vercel page and one made through the Django pages are both written with the surface `hq` (design 22.5), by the same functions. So if a Vercel deploy were ever compromised, which is the cost R26 accepted, the sign-ins made with captured codes would look like ordinary ones in the record. Marking the way in would mean a change to `two_step` and `hq_auth`, which this plan leaves alone; it is listed in 9.4 as a separate decision.
- **The cost R26 accepted** is not reduced by anything here. From the day a staff origin is set on an environment, whoever controls the page at that origin can capture both codes as typed.

## 9. For Coach Ed

### 9.1 Routine details decided in this plan, his to overrule

1. The routes live under `/staff/v1/` on the HQ host with the names of design 22.3.
2. `GET /staff/v1/me/` answers 200 with `email` and `expires_at`; the design named only its 401s.
3. A signed-in person who calls a sign-in route gets 409 `already_signed_in`.
4. A code sent without the 10-minute cookie gets the same `invalid_code` as a wrong code, and the page offers "start again".
5. `sign_in_paused` is a 403. An unknown staff path is a JSON 404; a wrong method a JSON 405.
6. Reads are held to the origin rule as well as writes, so a route answers nothing to an address typed into a browser.
7. The staff origin must be a sibling name of the HQ host. Stricter than a browser needs; simple to check; refuses every preview address.
8. Locally, with `DEBUG` on, the origin may be `http` with a port.
9. The routes are answered inside `HqHostMiddleware`, not through the URL list, so that the variable being unset leaves today's behaviour exactly as it is. **Its cost, plainly:** a staff answer skips every middleware after the second: `ApiOriginMiddleware` (which passes on anything outside `/v1/`), `SessionMiddleware`, `CommonMiddleware`, `CsrfViewMiddleware`, `AuthenticationMiddleware`, `HqUserMiddleware`, `MessageMiddleware` and `XFrameOptionsMiddleware` (finding 9 of section 3). `SecurityMiddleware` still applies.
10. The coordinator's decision on the choice that offered: `dispatch` sets `X-Frame-Options: DENY` on every staff answer, since `XFrameOptionsMiddleware` is skipped.

### 9.2 One thing he should know before approving

The audit record will not show whether a person signed in through the Vercel page or through the Django pages: both are recorded as `hq`. If the staff app's deploy were ever compromised, sign-ins made with captured codes would look like ordinary sign-ins. Recording the way in is possible, is not built by this plan, and is his to ask for (9.4).

### 9.3 What approving this plan approves

- Building tasks 1 to 5 on a build branch: one new setting, one new file of routes, one new branch in `HqHostMiddleware`, one new check, one new test file, the runbook lines.
- A build PR, reviewed by the NPC coordinator before merge, merged by the coordinator on green CI after the overlap check.
- That merge reaching staging by the usual deploy, **with `HQ_STAFF_ORIGIN` unset**, which changes nothing a person or a browser can see.

### 9.4 What stays a separate approval, each asked on its own

- Setting `HQ_STAFF_ORIGIN` on staging (7.3 row 4), and every later change of it.
- Setting `HQ_STAFF_ORIGIN` on production. Besides his approval, **it waits for the NPC wording amendment**, which is the NPC coordinator's later plan, on hold until plans 10 and 11 have dates. The reason: from the day it is set, Vercel serves the page where both admin codes are typed, and the NPC drafts describe one Django sign-in surface. Staging is not affected by this.
- Whether the audit record should mark which way a sign-in came in (section 8 and 9.2). Not built here: it would change `two_step` and `hq_auth`, which this plan leaves alone.
- The DNS record and the Vercel project or domain for the proof page (7.3 rows 2 and 3).
- Which arrangement of names the proof uses (7.2), and the final host names (design 22.7), which plan 10 asks.
- What to do if the proof fails (7.6).
- Anything on production.
- Plans 2 to 11, each its own plan and approval.

## 10. Rollback

- **The routes:** delete `HQ_STAFF_ORIGIN` and redeploy. They stop existing; the Django pages are untouched and remain the way in. No data is left behind: the routes write only what the Django pages write (session rows, code rows, audit rows), in the same tables.
- **A session made through the routes** is an ordinary HQ session. It ends at sign-out, after 12 hours, or at `admin_identity reset`.
- **The build itself:** revert the build PR. With the variable unset there is nothing to migrate back and nothing to clean up.

## 11. Shared files and other sessions

- `config/settings.py`: one added setting, four added lines, nothing reordered (4.1). Announced to the NPC coordinator before merge, as design 22.5 asks. The NPC coordinator has no open PR and no objection. Santé slice 7 (z8uq9me4dy) also adds lines to this file: first ready merges first, the other updates from `origin/main` and keeps both sides' lines.
- `config/api.py`, `pyproject.toml`, `uv.lock`: not touched by this plan's build.
- `apps/operators/middleware.py` and `checks.py` belong to NPC Build 6 and Build 9 (z8uq9mdwdx, z8uq9mdxvt). The NPC coordinator reviews the build PR before it merges.
- `docs/runbooks/admin-two-step.md` is that task's runbook: three additions, nothing rewritten.
- Before the build PR is opened, and again before it merges: fetch `origin/main`, bring the branch up to date, re-run the tests, list the open PRs with their files, and tell the coordinator of any that touch these five files.

## 12. Self-review of this plan

- **Scope against the brief:** routes (tasks 2, 3), off unless set (task 2, tests in 6.1), origin on every route (task 2), start-up check (task 1), staging proof as its own gated step with values and approvals (section 7), failure and fallback as his decision (7.6), later plans named (2.2), rulings quoted (1.1), `/healthz/` and `healthcheck.railway.app` (section 4, task 2's `test_health_is_untouched`), settings by name (section 4), files, tests first, local then staging, unverified, rollback, approvals (sections 6, 8, 9, 10).
- **Names used across tasks:** `staff_origin_problem`, `active_staff_origin`, `check_staff_origin` (task 1; used in task 2). `staff_views.PREFIX`, `dispatch(request, origin)`, `ROUTES` (task 2; extended in task 3). Test helpers `call`, `assertError`, `staff_email_step`, `staff_email_step_for`, `staff_sign_in` are defined before they are used.
- **Not run:** see section 8.
