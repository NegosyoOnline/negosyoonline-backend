# Backend Slice 2c (Facebook Sign-In) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status: complete on staging, 2026-11-10. Approved by Coach Ed that day, as written, with the seven decisions below as recommended. PR A (#32) and PR B (#34) are merged and deployed. Task 6 Steps 4 and 5 were done with his approval: the Facebook variables are set on staging, and he signed in once with Facebook, confirmed an email by code (Facebook returned no email for his account, so he typed one), and on a second visit went straight to the dashboard. Task 6 Step 6 is deferred to cutover: he confirmed the Meta app is shared with the legacy sign-in in TWCinc, so its data deletion URL stays on legacy until then. Moving it needs his approval at the time.**

**Goal:** A subscriber can sign up and sign in with Facebook. The first time a Facebook user is seen they confirm an email address with a 6-digit code; after that, Facebook alone signs them in. Meta's data deletion callback removes the Facebook link.

**Architecture:** `apps/accounts` gains one module that talks to Facebook (`facebook.py`), three service functions, and a second router file (`api_facebook.py`) with the three sign-in endpoints and the data deletion callback. The browser-state cookie code written for Google moves into a small shared module (`flow.py`) that both providers use. No new table and no migration: `ExternalIdentity` already allows `facebook`.

**Tech Stack:** Python 3.12, Django 5.2, Django Ninja 1.x, Python's standard library for the two HTTPS calls to Facebook (no new dependency), `uv`, Django's test runner.

**Spec:** `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`, section 4 (the Facebook lines, the matching rule, the data deletion callback, "provider access tokens ... are never stored", "verifying an email by code revokes that account's earlier sessions") and section 8 (`GET auth/facebook/start/`, `GET auth/facebook/callback/`, `POST auth/facebook/confirm-email/`, `POST webhooks/facebook/data-deletion/`).

## Scope of this plan

- **In:** the Facebook redirect flow, confirming the email by code on first sign-in, linking Facebook to a new or existing account, later sign-ins without a code, Meta's data deletion callback and its status URL.
- **Not in:** any other Facebook feature (pages, posting, catalogue, messaging), unlinking a provider from account settings, any frontend work, anything from slice 3 onward.

## How the flow works

1. The site's popup links to `GET /v1/auth/facebook/start/?accept_policies=true` (the parameter only when the policies box is ticked).
2. The API stores a random `state` in a signed, `HttpOnly`, 10-minute cookie and redirects the browser to Facebook.
3. Facebook redirects the browser to `GET /v1/auth/facebook/callback/?code=...&state=...`. The API checks `state` against the cookie and swaps `code` for the person's Facebook user id, name and (maybe) email.
4. **Seen before:** the API signs them in, sets the session cookie and redirects to `<DASHBOARD_URL>/`. Done.
5. **First time:** the API signs nobody in. It puts the Facebook user id, name and email into a second signed, `HttpOnly`, 15-minute cookie (the "pending" cookie) and redirects to `<SITE_URL>/?signin=1&facebook=confirm`. No email address is ever put in a URL.
6. The popup calls `POST /v1/auth/facebook/confirm-email/` with `{}`. The API emails a code to the address Facebook gave and answers `202 {"email": "..."}` so the popup can say where the code went. If Facebook gave no address, the API answers `400` naming the `email` field; the popup asks for one and calls again with `{"email": "..."}`.
7. The popup calls the same endpoint with `{"code": "123456"}`. The API checks the code, links Facebook to the account with that email (or creates the account), sets the session cookie and answers `200` with the account, exactly like the email-code verify endpoint.

## Decisions for Coach Ed

Each has a recommendation. The plan is written to the recommendations; a different answer changes only the step named.

1. **Sessions.** The first Facebook sign-in confirms an email by code, so, as the spec says for any email code, it signs out the earlier sessions of an existing account. Later Facebook sign-ins (no code) leave other sessions alone, the same as Google. (Task 3.)
2. **One endpoint sends and checks the code.** The spec lists only `confirm-email/`. Recommended: without a `code` it sends one; with a `code` it checks it. This avoids a fourth endpoint and keeps email addresses out of URLs. The landing URL `<SITE_URL>/?signin=1&facebook=confirm` is a contract with the frontend; the coordinator session must confirm it before PR B. (Task 4.)
3. **The email cannot be changed when Facebook supplies one.** Per the spec, the subscriber types an email only when Facebook returned none. Either way it must be confirmed by code, even when it matches an existing account (already decided on 2026-11-10).
4. **Data deletion removes the Facebook link only.** The account, its sites and its sessions stay; the person can still sign in by email code. An audit event is recorded. (Task 5.)
5. **The status URL is one small API endpoint not in the spec's table:** `GET /v1/webhooks/facebook/data-deletion/{code}/`, answering `{"status": "completed"}`. The deletion happens during Meta's call, so there is nothing to track and no new table: the confirmation code is a value signed by this API, and the status endpoint only checks that signature. (Task 5.)
6. **Which Meta app, and when its data deletion URL moves.** Recommended: use the approved Meta app; Coach Ed enters its app id and secret in Railway himself. A Meta app has exactly one data deletion URL. If the legacy sign-in in TWCinc uses the same app, that URL stays on legacy until cutover, and Task 6 Step 6 is skipped for now. Coach Ed decides. (2026-11-10: approved. Coach Ed confirmed the app is shared with legacy, so the URL stays on legacy until cutover and Step 6 is deferred.)
7. **No Graph API version is pinned and no dependency is added.** The calls use Facebook's unversioned URLs, which Meta serves with the app's default version. This avoids hard-coding a version that Meta retires. If Meta's dashboard shows a deprecation warning for the app, pin the version it names in `apps/accounts/facebook.py`.

## Rules for whoever executes this

- Read `CLAUDE.md` first. Work on feature branches; `main` is protected; merge through pull requests with CI green.
- This plan adds no migration. If `makemigrations --check --dry-run` reports a change at any point, stop and tell Coach Ed.
- Tell the coordinator session for ClickUp task `z8uq9mach5` before PR B, and get its confirmation of the landing URL in decision 2.
- Three steps change state outside this repository and need Coach Ed's explicit approval at the time: Task 6 Step 4 (Facebook variables on staging), Task 6 Step 5 (one real Facebook sign-in) and Task 6 Step 6 (pointing Meta's data deletion URL at this API).
- Never print secrets. Never touch `production`. Never type a Facebook password: the real sign-in is done by Coach Ed in his own browser.
- All commands run from the repository root with `uv run`. Tests pass `secure=True` to the test client.
- Other sessions merge to `main` while this plan runs, so the total test count moves. Each step states how many tests its own modules run; for the full suite the expected result is `OK` with no failures.
- GitHub sometimes does not merge a green pull request that has auto-merge set; merge it directly in that case.

## File structure

| File | Responsibility |
|---|---|
| `apps/accounts/flow.py` | New. The signed browser cookie that ties a provider flow to one browser, and where a flow ends. Used by Google and Facebook. |
| `apps/accounts/facebook.py` | New. The only code that talks to Facebook, and the check of Meta's signed requests. |
| `apps/accounts/services.py` | Gains `sign_in_with_facebook()`, `send_facebook_email_code()`, `confirm_facebook_email()`, `remove_facebook_identity()`. |
| `apps/accounts/api.py` | The Google endpoints switch to `flow.py`. No behaviour change. |
| `apps/accounts/api_facebook.py` | New. The Facebook endpoints and the data deletion callback, on their own router. |
| `config/api.py`, `config/settings.py`, `.env.example`, `README.md`, `AGENTS.md` | Router mount, settings, example variables, the record. |

Two pull requests. PR A is Tasks 1 to 3: the shared cookie module, the Facebook client and the services, with no new endpoints. PR B is Tasks 4 to 6: the endpoints, which answer 404 until the Facebook variables are set.

---

### Task 1: The shared flow cookie

The Google endpoints build, sign, read and clear their state cookie inline. Facebook needs the same four things twice (state and pending). This task moves them into one module and switches Google to it. Google's behaviour and tests do not change.

**Files:**
- Create: `apps/accounts/flow.py`, `apps/accounts/tests/test_flow.py`
- Modify: `apps/accounts/api.py`

- [ ] **Step 1: Create the branch**

```bash
git fetch origin && git checkout -b feat/facebook-signin-service origin/main
```

- [ ] **Step 2: Write the failing test**

`apps/accounts/tests/test_flow.py`:

```python
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.accounts import flow

NAME = "no_test_flow"
PATH = "/v1/auth/test/"


def request_with(response, name=NAME):
    request = RequestFactory().get(PATH)
    request.COOKIES[name] = response.cookies[NAME].value
    return request


class FlowCookieTests(SimpleTestCase):
    def test_payload_round_trips_through_a_locked_down_cookie(self):
        response = HttpResponse()

        flow.set_flow_cookie(response, NAME, PATH, {"state": "abc", "n": 1}, 600)

        cookie = response.cookies[NAME]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], PATH)
        self.assertEqual(cookie["max-age"], 600)
        self.assertNotIn("abc", cookie.value)
        self.assertEqual(
            flow.read_flow_cookie(request_with(response), NAME, 600), {"state": "abc", "n": 1}
        )

    def test_missing_altered_expired_or_borrowed_cookies_read_as_none(self):
        response = HttpResponse()
        flow.set_flow_cookie(response, NAME, PATH, {"state": "abc"}, 600)
        altered = request_with(response)
        altered.COOKIES[NAME] = altered.COOKIES[NAME][:-2] + "xx"

        self.assertIsNone(flow.read_flow_cookie(RequestFactory().get(PATH), NAME, 600))
        self.assertIsNone(flow.read_flow_cookie(altered, NAME, 600))
        self.assertIsNone(flow.read_flow_cookie(request_with(response), NAME, -1))
        self.assertIsNone(
            flow.read_flow_cookie(request_with(response, "no_other_flow"), "no_other_flow", 600)
        )

    def test_clear_flow_cookie_empties_it(self):
        response = HttpResponse()

        flow.clear_flow_cookie(response, NAME, PATH)

        self.assertEqual(response.cookies[NAME].value, "")
        self.assertEqual(response.cookies[NAME]["path"], PATH)

    def test_same_state_compares_text_safely(self):
        self.assertTrue(flow.same_state("abc", "abc"))
        self.assertFalse(flow.same_state("abc", "abd"))
        self.assertFalse(flow.same_state("abc", ""))
        self.assertFalse(flow.same_state("abc", "ábc"))

    @override_settings(DASHBOARD_URL="https://dash.example.test", SITE_URL="https://site.example.test")
    def test_a_flow_ends_on_the_dashboard_or_in_the_site_popup(self):
        self.assertEqual(flow.finish_redirect()["Location"], "https://dash.example.test/")
        self.assertEqual(
            flow.finish_redirect("some_error")["Location"],
            "https://site.example.test/?signin=1&error=some_error",
        )
```

- [ ] **Step 3: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_flow`
Expected: FAIL with `ImportError: cannot import name 'flow' from 'apps.accounts'`.

- [ ] **Step 4: Write the implementation**

`apps/accounts/flow.py`:

```python
"""What ties a provider sign-in flow to one browser: a signed, short-lived, HttpOnly cookie."""
import hmac

from django.conf import settings
from django.core import signing
from django.http import HttpResponseRedirect


def _salt(name):
    # A cookie signed for one flow is never accepted as another.
    return f"accounts.flow.{name}"


def set_flow_cookie(response, name, path, payload, seconds):
    response.set_cookie(
        name,
        signing.dumps(payload, salt=_salt(name), compress=True),
        max_age=seconds,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path=path,
    )


def read_flow_cookie(request, name, seconds):
    """The payload, or None when the cookie is missing, altered or older than `seconds`."""
    try:
        return signing.loads(request.COOKIES.get(name, ""), salt=_salt(name), max_age=seconds)
    except signing.BadSignature:
        return None


def clear_flow_cookie(response, name, path):
    response.delete_cookie(name, path=path, samesite="Lax")


def same_state(expected, given):
    return hmac.compare_digest(str(expected).encode(), str(given).encode())


def finish_redirect(error=None):
    """Where a provider flow ends: the dashboard, or the site's sign-in popup with an error."""
    if error:
        return HttpResponseRedirect(f"{settings.SITE_URL}/?signin=1&error={error}")
    return HttpResponseRedirect(f"{settings.DASHBOARD_URL}/")
```

In `apps/accounts/api.py`:

1. Delete the lines `import hmac` and `from django.core import signing` (nothing else in the file uses them), and add this import directly below the `apps.accounts.auth` import:

```python
from apps.accounts.flow import (
    clear_flow_cookie,
    finish_redirect,
    read_flow_cookie,
    same_state,
    set_flow_cookie,
)
```

2. Replace everything from the comment line `# Google sign-in. These two are browser navigations: they answer with redirects, not JSON.` to the end of the file with:

```python
# Google sign-in. These two are browser navigations: they answer with redirects, not JSON.
GOOGLE_STATE_COOKIE = "no_google_state"
GOOGLE_STATE_PATH = "/v1/auth/google/"
GOOGLE_STATE_SECONDS = 600


def _require_google():
    if not google.is_configured():
        raise ApiError("not_found", "No such endpoint.", status=404)


def _google_callback_url(request):
    return request.build_absolute_uri("/v1/auth/google/callback/")


def _end_google_flow(error=None):
    response = finish_redirect(error)
    clear_flow_cookie(response, GOOGLE_STATE_COOKIE, GOOGLE_STATE_PATH)
    return response


@router.get("/auth/google/start/", response={302: None})
def google_start(request, accept_policies: bool = False):
    _require_google()
    state = secrets.token_urlsafe(32)
    response = HttpResponseRedirect(google.authorization_url(_google_callback_url(request), state))
    set_flow_cookie(
        response,
        GOOGLE_STATE_COOKIE,
        GOOGLE_STATE_PATH,
        {"state": state, "accept_policies": accept_policies},
        GOOGLE_STATE_SECONDS,
    )
    return response


@router.get("/auth/google/callback/", response={302: None})
def google_callback(request, code: str = "", state: str = "", error: str = ""):
    _require_google()
    started = read_flow_cookie(request, GOOGLE_STATE_COOKIE, GOOGLE_STATE_SECONDS)
    if started is None or not same_state(started["state"], state):
        return _end_google_flow("google_state_invalid")
    if error or not code:
        return _end_google_flow("google_cancelled")
    try:
        profile = google.fetch_profile(code, _google_callback_url(request))
        _account, _session, token, _created = services.sign_in_with_google(
            profile, started["accept_policies"], request
        )
    except google.GoogleSignInFailed:
        return _end_google_flow("google_failed")
    except ApiError as refused:
        return _end_google_flow(refused.code)
    response = _end_google_flow()
    set_session_cookie(response, token)
    return response
```

The cookie's signing salt changes with this step, so a Google sign-in that is in the middle of its ten minutes during the deploy lands on `google_state_invalid` once and works on the next try.

- [ ] **Step 5: Run it and watch it pass, with the Google tests still green**

Run: `uv run python manage.py test apps.accounts.tests.test_flow apps.accounts.tests.test_api_google`
Expected: `Ran 12 tests` and `OK`.

- [ ] **Step 6: Commit**

```bash
git add apps/accounts/flow.py apps/accounts/tests/test_flow.py apps/accounts/api.py
git commit -m "refactor: share the provider flow cookie between sign-in providers"
```

---

### Task 2: The Facebook client

**Files:**
- Create: `apps/accounts/facebook.py`, `apps/accounts/tests/test_facebook.py`
- Modify: `config/settings.py`, `.env.example`

- [ ] **Step 1: Write the failing test**

`apps/accounts/tests/test_facebook.py`:

```python
import base64
import hashlib
import hmac
import json
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings

from apps.accounts import facebook

REDIRECT = "https://api.example.test/v1/auth/facebook/callback/"
SECRET = "app-secret"


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def signed_request(secret=SECRET, **overrides):
    """A request signed the way Meta signs its data deletion callback."""
    data = {"algorithm": "HMAC-SHA256", "user_id": "fb-1", "issued_at": 1}
    data.update(overrides)
    payload = _b64(json.dumps(data).encode())
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{_b64(signature)}.{payload}"


@override_settings(FACEBOOK_APP_ID="app-id", FACEBOOK_APP_SECRET=SECRET)
class FacebookClientTests(SimpleTestCase):
    def fetch(self, *answers):
        with mock.patch("apps.accounts.facebook._get_json", side_effect=list(answers)) as call:
            profile = facebook.fetch_profile("the-code", REDIRECT)
        return profile, call

    def test_is_configured_needs_both_the_id_and_the_secret(self):
        self.assertTrue(facebook.is_configured())
        with override_settings(FACEBOOK_APP_SECRET=""):
            self.assertFalse(facebook.is_configured())

    def test_authorization_url_asks_only_for_profile_and_email(self):
        url = urlsplit(facebook.authorization_url(REDIRECT, "the-state"))
        query = {key: values[0] for key, values in parse_qs(url.query).items()}

        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", facebook.DIALOG_URL)
        self.assertEqual(
            query,
            {
                "client_id": "app-id",
                "redirect_uri": REDIRECT,
                "state": "the-state",
                "response_type": "code",
                "scope": "public_profile,email",
            },
        )
        self.assertNotIn(SECRET, url.query)

    def test_fetch_profile_swaps_the_code_then_reads_the_person(self):
        profile, call = self.fetch(
            {"access_token": "the-token"},
            {"id": "fb-1", "name": "Ana Reyes", "email": "Ana@Example.test"},
        )

        self.assertEqual(
            profile,
            facebook.FacebookProfile(user_id="fb-1", name="Ana Reyes", email="Ana@Example.test"),
        )
        (token_url, token_params), (me_url, me_params) = [item.args for item in call.call_args_list]
        self.assertEqual(token_url, facebook.TOKEN_URL)
        self.assertEqual(
            token_params,
            {
                "client_id": "app-id",
                "client_secret": SECRET,
                "redirect_uri": REDIRECT,
                "code": "the-code",
            },
        )
        self.assertEqual(me_url, facebook.ME_URL)
        self.assertEqual(me_params["fields"], "id,name,email")
        self.assertEqual(
            me_params["appsecret_proof"],
            hmac.new(SECRET.encode(), b"the-token", hashlib.sha256).hexdigest(),
        )

    def test_a_person_who_shares_no_email_has_an_empty_one(self):
        profile, _call = self.fetch({"access_token": "the-token"}, {"id": "fb-1", "name": "Ana"})

        self.assertEqual(profile.email, "")

    def test_a_failed_exchange_is_refused_without_logging_secrets(self):
        failures = [
            [OSError("down")],
            [{"error": {"message": "bad code"}}],
            [{"access_token": "the-token"}, {"name": "No Id"}],
        ]
        for answers in failures:
            with self.assertLogs("apps.accounts.facebook", level="WARNING") as logs:
                with self.assertRaises(facebook.FacebookSignInFailed):
                    self.fetch(*answers)
            output = "".join(logs.output)
            self.assertNotIn("the-code", output)
            self.assertNotIn("the-token", output)

    def test_parse_signed_request_returns_the_facebook_user_id(self):
        self.assertEqual(facebook.parse_signed_request(signed_request()), "fb-1")

    def test_a_request_meta_did_not_sign_is_refused(self):
        good = signed_request()
        bad_requests = [
            signed_request(secret="someone-elses-secret"),
            good[:-3] + "abc",
            signed_request(algorithm="none"),
            signed_request(user_id=""),
            "no-dot-here",
            "",
        ]
        for value in bad_requests:
            with self.assertLogs("apps.accounts.facebook", level="WARNING"):
                with self.assertRaises(facebook.BadSignedRequest):
                    facebook.parse_signed_request(value)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_facebook`
Expected: FAIL with `ImportError: cannot import name 'facebook' from 'apps.accounts'`.

- [ ] **Step 3: Write the implementation**

`apps/accounts/facebook.py`:

```python
"""Facebook sign-in (server-side redirect flow) and Meta's signed requests.

Facebook's tokens are used once and never stored.
"""
import base64
import hashlib
import hmac
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

# Unversioned on purpose: Meta serves these with the app's default Graph API version.
DIALOG_URL = "https://www.facebook.com/dialog/oauth"
TOKEN_URL = "https://graph.facebook.com/oauth/access_token"
ME_URL = "https://graph.facebook.com/me"
TIMEOUT = 10  # seconds


@dataclass(frozen=True)
class FacebookProfile:
    user_id: str
    name: str
    email: str  # "" when the person shared none; never trusted until confirmed by code


class FacebookSignInFailed(Exception):
    """Facebook refused the code, or answered with something we cannot use."""


class BadSignedRequest(Exception):
    """A request that claims to come from Meta but is not signed with our app secret."""


def is_configured():
    return bool(settings.FACEBOOK_APP_ID and settings.FACEBOOK_APP_SECRET)


def authorization_url(redirect_uri, state):
    query = urllib.parse.urlencode(
        {
            "client_id": settings.FACEBOOK_APP_ID,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": "public_profile,email",
        }
    )
    return f"{DIALOG_URL}?{query}"


def _get_json(url, params):
    """The only network call in this module."""
    with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=TIMEOUT) as response:
        return json.load(response)


def _sign(message):
    return hmac.new(settings.FACEBOOK_APP_SECRET.encode(), message.encode(), hashlib.sha256)


def fetch_profile(code, redirect_uri):
    """The Facebook user behind this one-time code. Raises FacebookSignInFailed."""
    try:
        token = _get_json(
            TOKEN_URL,
            {
                "client_id": settings.FACEBOOK_APP_ID,
                "client_secret": settings.FACEBOOK_APP_SECRET,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )["access_token"]
        person = _get_json(
            ME_URL,
            {
                "fields": "id,name,email",
                "access_token": token,
                "appsecret_proof": _sign(token).hexdigest(),
            },
        )
        if not person["id"]:
            raise ValueError("no user id")
        return FacebookProfile(
            user_id=str(person["id"]),
            name=str(person.get("name", ""))[:120],
            email=str(person.get("email", "")),
        )
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        # Only the kind of failure is logged: the request URLs carry the secret and the token.
        logger.warning("Facebook sign-in refused (%s)", type(error).__name__)
        raise FacebookSignInFailed() from None


def _b64decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def parse_signed_request(signed_request):
    """The Facebook user id in a request Meta signed with our app secret. Raises BadSignedRequest."""
    try:
        signature, payload = signed_request.split(".", 1)
        if not hmac.compare_digest(_b64decode(signature), _sign(payload).digest()):
            raise ValueError("signature does not match")
        data = json.loads(_b64decode(payload))
        if str(data["algorithm"]).upper() != "HMAC-SHA256" or not data["user_id"]:
            raise ValueError("unexpected content")
        return str(data["user_id"])
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        logger.warning("Facebook signed request refused (%s)", type(error).__name__)
        raise BadSignedRequest() from None
```

In `config/settings.py`, directly below the `GOOGLE_CLIENT_SECRET = ...` line, add:

```python
# Facebook sign-in. With no app id and secret the Facebook endpoints answer 404.
FACEBOOK_APP_ID = os.environ.get("FACEBOOK_APP_ID", "")
FACEBOOK_APP_SECRET = os.environ.get("FACEBOOK_APP_SECRET", "")
```

In `.env.example`, add:

```
FACEBOOK_APP_ID=
FACEBOOK_APP_SECRET=
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.accounts.tests.test_facebook`
Expected: `Ran 7 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/accounts/facebook.py apps/accounts/tests/test_facebook.py config/settings.py .env.example
git commit -m "feat: add the Facebook sign-in client"
```

---

### Task 3: The services

**Files:**
- Create: `apps/accounts/tests/test_services_facebook.py`
- Modify: `apps/accounts/services.py`

- [ ] **Step 1: Write the failing test**

`apps/accounts/tests/test_services_facebook.py`:

```python
import re

from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts import services
from apps.accounts.facebook import FacebookProfile
from apps.accounts.models import Account, AccountAuditEvent, ExternalIdentity, PolicyAcceptance
from apps.accounts.sessions import find_session, start_session
from apps.common.errors import ApiError

EMAIL = "ana@example.test"
IP = "203.0.113.9"
PROFILE = FacebookProfile(user_id="fb-1", name="Ana Reyes", email=EMAIL)


def last_code():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class FacebookServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.request = RequestFactory().post("/", REMOTE_ADDR=IP)

    def confirm(self, user_id="fb-1", email=EMAIL, accept_policies=True, code=None):
        if code is None:
            services.send_facebook_email_code(user_id, email, IP)
            code = last_code()
        return services.confirm_facebook_email(
            user_id, "Ana Reyes", email, code, accept_policies, self.request
        )

    def events(self):
        return list(AccountAuditEvent.objects.values_list("event", flat=True).order_by("id"))

    def test_a_facebook_user_not_seen_before_is_not_signed_in(self):
        self.assertIsNone(services.sign_in_with_facebook(PROFILE, self.request))
        self.assertFalse(Account.objects.exists())

    def test_the_code_goes_to_the_normalised_email(self):
        sent_to = services.send_facebook_email_code("fb-1", "  Ana@Example.TEST ", IP)

        self.assertEqual(sent_to, EMAIL)
        self.assertEqual(mail.outbox[-1].to, [EMAIL])
        self.assertRegex(last_code(), r"^\d{6}$")
        with self.assertRaises(ApiError) as raised:
            services.send_facebook_email_code("fb-1", "not-an-email", IP)
        self.assertIn("email", raised.exception.fields)

    def test_confirming_creates_the_account_and_later_sign_ins_need_no_code(self):
        account, _session, first_token, created = self.confirm()

        self.assertTrue(created)
        self.assertEqual((account.email, account.name), (EMAIL, "Ana Reyes"))
        self.assertIsNotNone(account.email_verified_at)
        identity = ExternalIdentity.objects.get(account=account)
        self.assertEqual((identity.provider, identity.provider_user_id), ("facebook", "fb-1"))
        self.assertEqual(PolicyAcceptance.objects.get(account=account).ip, IP)
        self.assertEqual(self.events(), ["account_created", "identity_linked", "signed_in"])

        again, _session, token = services.sign_in_with_facebook(PROFILE, self.request)

        self.assertEqual(again, account)
        self.assertIsNotNone(find_session(token))
        self.assertIsNotNone(find_session(first_token))

    def test_new_account_must_accept_policies_and_the_code_survives(self):
        services.send_facebook_email_code("fb-1", EMAIL, IP)
        code = last_code()

        with self.assertRaises(ApiError) as raised:
            self.confirm(accept_policies=False, code=code)

        self.assertEqual(raised.exception.code, "policies_required")
        self.assertFalse(Account.objects.exists())
        _account, _session, _token, created = self.confirm(code=code)
        self.assertTrue(created)

    def test_existing_account_is_linked_and_its_earlier_sessions_end(self):
        existing = Account.objects.create(email=EMAIL, name="Ana")
        _earlier, earlier_token = start_session(existing, self.request)

        account, _session, token, created = self.confirm(accept_policies=False)

        self.assertFalse(created)
        self.assertEqual(account, existing)
        self.assertEqual(account.name, "Ana")
        self.assertIsNotNone(account.email_verified_at)
        self.assertTrue(existing.identities.filter(provider="facebook").exists())
        self.assertIsNone(find_session(earlier_token))
        self.assertIsNotNone(find_session(token))
        self.assertEqual(self.events(), ["identity_linked", "signed_in"])

    def test_a_wrong_code_or_a_plain_sign_in_code_is_refused(self):
        services.send_facebook_email_code("fb-1", EMAIL, IP)
        wrong = "000000" if last_code() != "000000" else "111111"
        services.request_signin_code(EMAIL, IP)
        signin_code = last_code()

        for code in (wrong, signin_code):
            with self.assertRaises(ApiError) as raised:
                self.confirm(code=code)
            self.assertEqual(raised.exception.code, "invalid_code")
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_an_account_linked_to_another_facebook_user_is_refused(self):
        self.confirm()

        with self.assertRaises(ApiError) as raised:
            self.confirm(user_id="fb-2")

        self.assertEqual(
            (raised.exception.code, raised.exception.status), ("facebook_account_mismatch", 409)
        )
        self.assertEqual(ExternalIdentity.objects.count(), 1)

    def test_closed_account_cannot_sign_in_or_be_linked(self):
        account, _session, _token, _ = self.confirm()
        Account.objects.filter(pk=account.pk).update(closed_at=timezone.now())

        with self.assertRaises(ApiError) as known:
            services.sign_in_with_facebook(PROFILE, self.request)
        ExternalIdentity.objects.all().delete()
        cache.clear()  # the first confirm started the one-minute wait for a new code
        with self.assertRaises(ApiError) as linking:
            self.confirm()

        self.assertEqual((known.exception.code, known.exception.status), ("account_closed", 403))
        self.assertEqual(linking.exception.code, "account_closed")
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_removing_the_identity_keeps_the_account_and_records_it(self):
        account, _session, token, _ = self.confirm()

        self.assertTrue(services.remove_facebook_identity("fb-1"))
        self.assertFalse(services.remove_facebook_identity("fb-1"))

        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertTrue(Account.objects.filter(pk=account.pk).exists())
        self.assertIsNotNone(find_session(token))
        event = AccountAuditEvent.objects.get(event="identity_removed")
        self.assertEqual(event.detail, {"provider": "facebook", "reason": "data_deletion_request"})
        self.assertIsNone(services.sign_in_with_facebook(PROFILE, self.request))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_services_facebook`
Expected: FAIL (errors) with `AttributeError: module 'apps.accounts.services' has no attribute 'sign_in_with_facebook'` (or `send_facebook_email_code`).

- [ ] **Step 3: Write the implementation**

In `apps/accounts/services.py`:

1. Directly below the line `GOOGLE = ExternalIdentity.Provider.GOOGLE`, add:

```python
FACEBOOK = ExternalIdentity.Provider.FACEBOOK
```

2. Directly above the line `def update_account(account, *, name):`, add:

```python
def _facebook_purpose(user_id):
    # A code sent for one Facebook user confirms an email for that user only.
    return f"facebook:{user_id}"


@transaction.atomic
def sign_in_with_facebook(profile, request):
    """Sign in a Facebook user we have linked before. Returns (account, session, token).

    Returns None when this Facebook user has never been linked: the caller must then have
    the person confirm an email with confirm_facebook_email().
    """
    identity = (
        ExternalIdentity.objects.select_related("account")
        .filter(provider=FACEBOOK, provider_user_id=profile.user_id)
        .first()
    )
    if identity is None:
        return None
    account = identity.account
    _refuse_closed(account)
    session, token = start_session(account, request)
    record_event(account, "signed_in", method="facebook", session=session.public_id)
    return account, session, token


def send_facebook_email_code(user_id, email, ip):
    """Email the code that confirms `email` for this Facebook user. Returns the address used."""
    email = normalise_email(email)
    code = codes.issue_code(email, ip, _facebook_purpose(user_id))
    send_email(
        to=email,
        category="facebook_email_code",
        subject="Confirm your email for NegosyoOnline",
        body=(
            f"Your confirmation code is {code}. It expires in 10 minutes.\n\n"
            "You are seeing this because someone is signing in to NegosyoOnline with Facebook "
            "using this email. If that was not you, you can ignore this email."
        ),
    )
    return email


@transaction.atomic
def confirm_facebook_email(user_id, name, email, code, accept_policies, request):
    """First Facebook sign-in: check the emailed code, then link Facebook to the account with
    that email, creating the account if there is none.

    Returns (account, session, token, created).
    """
    email = normalise_email(email)
    purpose = _facebook_purpose(user_id)
    if not codes.check_code(email, code, purpose):
        raise _invalid_code()
    account = Account.objects.filter(email=email).first()
    created = account is None
    if created:
        if not accept_policies:
            raise _policies_required()
    else:
        _refuse_closed(account)
        if account.identities.filter(provider=FACEBOOK).exists():
            raise ApiError(
                "facebook_account_mismatch",
                "This email is already linked to a different Facebook account.",
                status=409,
            )
    if ExternalIdentity.objects.filter(provider=FACEBOOK, provider_user_id=user_id).exists():
        raise ApiError(
            "facebook_already_linked", "This Facebook account is already linked.", status=409
        )
    codes.clear_code(email, purpose)
    if created:
        account = _create_account(email, request, method="facebook", name=name[:120])
    else:
        if account.email_verified_at is None:
            account.email_verified_at = timezone.now()
            account.save(update_fields=["email_verified_at"])
        revoke_sessions(account)  # an email was just verified by code
    ExternalIdentity.objects.create(account=account, provider=FACEBOOK, provider_user_id=user_id)
    record_event(account, "identity_linked", provider="facebook")
    session, token = start_session(account, request)
    record_event(account, "signed_in", method="facebook", session=session.public_id)
    return account, session, token, created


@transaction.atomic
def remove_facebook_identity(user_id):
    """Meta's data deletion request: unlink this Facebook user. True when there was a link."""
    identity = (
        ExternalIdentity.objects.select_related("account")
        .filter(provider=FACEBOOK, provider_user_id=user_id)
        .first()
    )
    if identity is None:
        return False
    account = identity.account
    identity.delete()
    record_event(account, "identity_removed", provider="facebook", reason="data_deletion_request")
    return True
```

If Coach Ed answers decision 1 the other way for existing accounts, delete the `revoke_sessions(account)` line in `confirm_facebook_email` and change `assertIsNone(find_session(earlier_token))` in the test to `assertIsNotNone`.

- [ ] **Step 4: Run it and watch it pass, with the other sign-in tests still green**

Run: `uv run python manage.py test apps.accounts.tests.test_services_facebook apps.accounts.tests.test_services apps.accounts.tests.test_services_google`
Expected: `Ran 26 tests` and `OK`.

- [ ] **Step 5: Run every check CI runs**

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
DJANGO_DEBUG=false DJANGO_SECRET_KEY=local-check-only-long-random-value-0123456789abcdef uv run python manage.py check --deploy
```

Expected: `No changes detected`, `OK` (21 more tests than `origin/main`), and only the two deferred HSTS warnings (W005, W021).

- [ ] **Step 6: Commit and open PR A**

```bash
git add apps/accounts/services.py apps/accounts/tests/test_services_facebook.py
git commit -m "feat: add the Facebook sign-in services"
```

Push and open the pull request "feat: Facebook sign-in services and the shared flow cookie (no endpoints)". Say in the description that it adds no migration and no endpoint, and that the Google endpoints were moved onto the shared cookie module with their tests unchanged. Enable auto-merge.

- [ ] **Step 7: Verify the deploy**

Note the newest staging deployment id before the merge, then:

```bash
scripts/wait_railway_deploy.sh <before-deployment-id>
scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <merge-commit-prefix>
curl -s -o /dev/null -w "%{http_code}\n" https://api-staging.negosyonline.com/v1/auth/google/start/
```

Expected: `SUCCESS ...`, `OK:` with the merge commit, then `302` (Google still works after the refactor; `404` instead if the Google variables are not set on staging).

---

### Task 4: The sign-in endpoints

Endpoints: `GET /v1/auth/facebook/start/`, `GET /v1/auth/facebook/callback/`, `POST /v1/auth/facebook/confirm-email/`.

`start` and `callback` are browser navigations and answer with redirects. `confirm-email` is called by the site's popup with `fetch` and answers JSON.

Error codes the public site receives in `?error=` after the callback: `facebook_state_invalid`, `facebook_cancelled`, `facebook_failed`, `account_closed`.

`confirm-email` answers:

| Request body | Answer |
|---|---|
| `{}` and Facebook supplied an email | `202 {"email": "<where the code went>"}` |
| `{}` and Facebook supplied none | `400 invalid_request` with `fields.email` (ask for an email) |
| `{"email": "..."}` and Facebook supplied none | `202 {"email": "..."}` |
| `{"code": "123456"}` (optionally `"accept_policies": true`) | `200 {"account": {...}, "created": true or false}` and the session cookie |
| any, with no pending Facebook sign-in in this browser | `400 facebook_not_started` |

Other errors from `confirm-email`: `invalid_code`, `policies_required`, `facebook_account_mismatch` (409), `facebook_already_linked` (409), `account_closed` (403), `rate_limited` (429), `origin_required` (403).

**Files:**
- Create: `apps/accounts/api_facebook.py`, `apps/accounts/tests/test_api_facebook.py`
- Modify: `config/api.py`

- [ ] **Step 1: Create the branch and write the failing test**

Confirm the coordinator session has confirmed the landing URL in decision 2.

```bash
git fetch origin && git checkout -b feat/facebook-signin-api origin/main
```

`apps/accounts/tests/test_api_facebook.py`:

```python
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

from django.core import mail
from django.test import override_settings

from apps.accounts import facebook
from apps.accounts.facebook import FacebookProfile
from apps.accounts.models import Account, ExternalIdentity
from apps.accounts.tests.base import EMAIL, ApiTestCase

DASH = "https://dash.example.test"
SITE = "https://site.example.test"
CALLBACK = "https://testserver/v1/auth/facebook/callback/"
CONFIRM = "/v1/auth/facebook/confirm-email/"
PROFILE = FacebookProfile(user_id="fb-1", name="Ana Reyes", email=EMAIL)
NO_EMAIL = FacebookProfile(user_id="fb-1", name="Ana Reyes", email="")


@override_settings(
    FACEBOOK_APP_ID="app-id", FACEBOOK_APP_SECRET="app-secret", DASHBOARD_URL=DASH, SITE_URL=SITE
)
class FacebookApiTests(ApiTestCase):
    def start(self, query="?accept_policies=true"):
        """Begin the flow and return the state Facebook would send back."""
        response = self.get(f"/v1/auth/facebook/start/{query}")
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlsplit(response["Location"]).query)["state"][0]

    def callback(self, state, code="the-code", **fetch):
        fetch = fetch or {"return_value": PROFILE}
        query = urlencode({"state": state, "code": code})
        with mock.patch("apps.accounts.api_facebook.facebook.fetch_profile", **fetch) as call:
            response = self.get(f"/v1/auth/facebook/callback/?{query}")
        self.assertEqual(response.status_code, 302)
        return response, call

    def arrive(self, profile=PROFILE, query="?accept_policies=true"):
        """A first-time Facebook user, back from Facebook and waiting to confirm an email."""
        response, _call = self.callback(self.start(query), return_value=profile)
        self.assertEqual(response["Location"], f"{SITE}/?signin=1&facebook=confirm")
        return response

    def test_start_redirects_to_facebook_and_sets_the_state_cookie(self):
        response = self.get("/v1/auth/facebook/start/")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(facebook.DIALOG_URL + "?"))
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(query["redirect_uri"], [CALLBACK])
        cookie = response.cookies["no_facebook_state"]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["path"], "/v1/auth/facebook/")
        self.assertEqual(cookie["max-age"], 600)

    def test_a_linked_facebook_user_is_signed_in_and_sent_to_the_dashboard(self):
        account = Account.objects.create(email=EMAIL)
        ExternalIdentity.objects.create(account=account, provider="facebook", provider_user_id="fb-1")

        response, call = self.callback(self.start())

        call.assert_called_once_with("the-code", CALLBACK)
        self.assertEqual(response["Location"], f"{DASH}/")
        self.assertTrue(response.cookies["no_session"]["httponly"])
        self.assertEqual(response.cookies["no_facebook_state"].value, "")
        self.assertEqual(self.get("/v1/me/").json()["account"]["email"], EMAIL)

    def test_a_first_time_user_is_sent_to_confirm_and_is_not_signed_in(self):
        response = self.arrive()

        self.assertNotIn("no_session", response.cookies)
        self.assertNotIn("example.test/?signin=1&facebook=confirm&", response["Location"])
        pending = response.cookies["no_facebook_pending"]
        self.assertTrue(pending["httponly"])
        self.assertEqual(pending["path"], "/v1/auth/facebook/")
        self.assertEqual(pending["max-age"], 900)
        self.assertEqual(self.get("/v1/me/").status_code, 401)
        self.assertFalse(Account.objects.exists())

    def test_confirming_the_facebook_email_creates_the_account(self):
        self.arrive()

        sent = self.post(CONFIRM, {"email": "ignored@example.test"})

        self.assertEqual(sent.status_code, 202)
        self.assertEqual(sent.json(), {"email": EMAIL})
        self.assertEqual(mail.outbox[-1].to, [EMAIL])

        confirmed = self.post(CONFIRM, {"code": self.last_code()})

        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(confirmed.json()["created"])
        self.assertEqual(confirmed.json()["account"]["email"], EMAIL)
        self.assertTrue(confirmed.cookies["no_session"]["httponly"])
        self.assertEqual(confirmed.cookies["no_facebook_pending"].value, "")
        self.assertEqual(self.get("/v1/me/").json()["account"]["name"], "Ana Reyes")

    def test_a_user_who_shared_no_email_types_one(self):
        self.arrive(NO_EMAIL)

        needs_email = self.post(CONFIRM, {})
        sent = self.post(CONFIRM, {"email": " Ana@Example.TEST "})
        confirmed = self.post(CONFIRM, {"code": self.last_code()})

        self.assertEqual(needs_email.status_code, 400)
        self.assertIn("email", needs_email.json()["error"]["fields"])
        self.assertEqual((sent.status_code, sent.json()), (202, {"email": EMAIL}))
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(Account.objects.filter(email=EMAIL).exists())

    def test_policies_can_be_accepted_when_confirming(self):
        self.arrive(query="")
        self.post(CONFIRM, {})
        code = self.last_code()

        refused = self.post(CONFIRM, {"code": code})
        accepted = self.post(CONFIRM, {"code": code, "accept_policies": True})

        self.assertEqual(refused.json()["error"]["code"], "policies_required")
        self.assertEqual(accepted.status_code, 200)

    def test_confirming_needs_a_pending_sign_in_and_an_allowed_origin(self):
        not_started = self.post(CONFIRM, {})
        self.arrive()
        no_origin = self.post(CONFIRM, {}, HTTP_ORIGIN=None)

        self.assertEqual(not_started.status_code, 400)
        self.assertEqual(not_started.json()["error"]["code"], "facebook_not_started")
        self.assertEqual(no_origin.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_callback_this_browser_did_not_start_is_refused(self):
        response, call = self.callback("made-up-state")
        self.start()
        wrong_state, second_call = self.callback("made-up-state")

        for landed in (response, wrong_state):
            self.assertEqual(landed["Location"], f"{SITE}/?signin=1&error=facebook_state_invalid")
        call.assert_not_called()
        second_call.assert_not_called()

    def test_cancelling_or_a_refused_code_is_sent_back(self):
        cancelled, call = self.callback(self.start(), code="")
        failed, _call = self.callback(self.start(), side_effect=facebook.FacebookSignInFailed())

        self.assertEqual(cancelled["Location"], f"{SITE}/?signin=1&error=facebook_cancelled")
        call.assert_not_called()
        self.assertEqual(failed["Location"], f"{SITE}/?signin=1&error=facebook_failed")
        self.assertEqual(self.get("/v1/me/").status_code, 401)

    @override_settings(FACEBOOK_APP_ID="")
    def test_without_facebook_variables_the_endpoints_are_404(self):
        responses = [
            self.get("/v1/auth/facebook/start/"),
            self.get("/v1/auth/facebook/callback/?state=x&code=y"),
            self.post(CONFIRM, {}),
        ]
        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "not_found")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_api_facebook`
Expected: FAIL: `AttributeError` or `ModuleNotFoundError` for `apps.accounts.api_facebook`, and 404 where 302 is expected.

- [ ] **Step 3: Write the implementation**

`apps/accounts/api_facebook.py`:

```python
"""Facebook sign-in endpoints and Meta's data deletion callback."""
import secrets

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from ninja import Router, Schema

from apps.accounts import facebook, services
from apps.accounts.api import VerifyOut, account_out
from apps.accounts.flow import (
    clear_flow_cookie,
    finish_redirect,
    read_flow_cookie,
    same_state,
    set_flow_cookie,
)
from apps.accounts.sessions import client_ip, set_session_cookie
from apps.common.errors import ApiError
from apps.common.middleware import require_allowed_origin

router = Router(tags=["accounts"])

COOKIE_PATH = "/v1/auth/facebook/"
STATE_COOKIE = "no_facebook_state"
STATE_SECONDS = 600
# Holds a first-time Facebook user until they confirm an email. Signed and HttpOnly.
PENDING_COOKIE = "no_facebook_pending"
PENDING_SECONDS = 900


class FacebookConfirmIn(Schema):
    email: str = ""
    code: str = ""
    accept_policies: bool = False


class CodeSentOut(Schema):
    email: str


def _require_facebook():
    if not facebook.is_configured():
        raise ApiError("not_found", "No such endpoint.", status=404)


def _callback_url(request):
    return request.build_absolute_uri("/v1/auth/facebook/callback/")


def _end_flow(error=None):
    response = finish_redirect(error)
    clear_flow_cookie(response, STATE_COOKIE, COOKIE_PATH)
    return response


def _usable_email(value):
    """Facebook's email in our stored form, or "" when it gave none or a broken one."""
    try:
        return services.normalise_email(value)
    except ApiError:
        return ""


@router.get("/auth/facebook/start/", response={302: None})
def facebook_start(request, accept_policies: bool = False):
    _require_facebook()
    state = secrets.token_urlsafe(32)
    response = HttpResponseRedirect(facebook.authorization_url(_callback_url(request), state))
    set_flow_cookie(
        response,
        STATE_COOKIE,
        COOKIE_PATH,
        {"state": state, "accept_policies": accept_policies},
        STATE_SECONDS,
    )
    return response


@router.get("/auth/facebook/callback/", response={302: None})
def facebook_callback(request, code: str = "", state: str = "", error: str = ""):
    _require_facebook()
    started = read_flow_cookie(request, STATE_COOKIE, STATE_SECONDS)
    if started is None or not same_state(started["state"], state):
        return _end_flow("facebook_state_invalid")
    if error or not code:
        return _end_flow("facebook_cancelled")
    try:
        profile = facebook.fetch_profile(code, _callback_url(request))
        signed_in = services.sign_in_with_facebook(profile, request)
    except facebook.FacebookSignInFailed:
        return _end_flow("facebook_failed")
    except ApiError as refused:
        return _end_flow(refused.code)
    if signed_in is not None:
        _account, _session, token = signed_in
        response = _end_flow()
        set_session_cookie(response, token)
        return response
    # First time: nobody is signed in until an email is confirmed by code.
    email = _usable_email(profile.email)
    response = HttpResponseRedirect(f"{settings.SITE_URL}/?signin=1&facebook=confirm")
    clear_flow_cookie(response, STATE_COOKIE, COOKIE_PATH)
    set_flow_cookie(
        response,
        PENDING_COOKIE,
        COOKIE_PATH,
        {
            "user_id": profile.user_id,
            "name": profile.name,
            "email": email,
            "email_from_facebook": bool(email),
            "accept_policies": started["accept_policies"],
        },
        PENDING_SECONDS,
    )
    return response


@router.post("/auth/facebook/confirm-email/", response={200: VerifyOut, 202: CodeSentOut})
def facebook_confirm_email(request, response: HttpResponse, payload: FacebookConfirmIn):
    require_allowed_origin(request)
    _require_facebook()
    pending = read_flow_cookie(request, PENDING_COOKIE, PENDING_SECONDS)
    if pending is None:
        raise ApiError("facebook_not_started", "Start again with Facebook.")
    if not payload.code:
        # Send the code. The typed email counts only when Facebook supplied none.
        email = pending["email"] if pending["email_from_facebook"] else payload.email
        email = services.send_facebook_email_code(pending["user_id"], email, client_ip(request))
        set_flow_cookie(
            response, PENDING_COOKIE, COOKIE_PATH, {**pending, "email": email}, PENDING_SECONDS
        )
        return 202, {"email": email}
    account, _session, token, created = services.confirm_facebook_email(
        pending["user_id"],
        pending["name"],
        pending["email"],
        payload.code,
        pending["accept_policies"] or payload.accept_policies,
        request,
    )
    set_session_cookie(response, token)
    clear_flow_cookie(response, PENDING_COOKIE, COOKIE_PATH)
    return 200, {"account": account_out(account), "created": created}
```

The pending cookie is signed, not encrypted. It holds only what the person's own Facebook account told us about them, it is `HttpOnly`, and it is sent only to `/v1/auth/facebook/`.

In `config/api.py`, add the import below the `apps.accounts.api` import:

```python
from apps.accounts.api_facebook import router as facebook_router
```

and add this line directly below `api.add_router("", accounts_router)`:

```python
api.add_router("", facebook_router)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.accounts.tests.test_api_facebook`
Expected: `Ran 10 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/accounts/api_facebook.py apps/accounts/tests/test_api_facebook.py config/api.py
git commit -m "feat: add the Facebook sign-in endpoints"
```

---

### Task 5: Meta's data deletion callback

Endpoints: `POST /v1/webhooks/facebook/data-deletion/` (called by Meta's servers, form-encoded, with a `signed_request` field) and `GET /v1/webhooks/facebook/data-deletion/{code}/` (the status URL Meta shows the person).

Meta's servers send no `Origin` header, so the origin rule does not apply here. The proof that the call is real is the signature made with the app secret. Meta expects the answer `{"url": "...", "confirmation_code": "..."}`.

**Files:**
- Create: `apps/accounts/tests/test_api_facebook_deletion.py`
- Modify: `apps/accounts/api_facebook.py`

- [ ] **Step 1: Write the failing test**

`apps/accounts/tests/test_api_facebook_deletion.py`:

```python
from django.test import TestCase, override_settings

from apps.accounts.models import Account, AccountAuditEvent, ExternalIdentity
from apps.accounts.tests.test_facebook import SECRET, signed_request

DELETION = "/v1/webhooks/facebook/data-deletion/"


@override_settings(FACEBOOK_APP_ID="app-id", FACEBOOK_APP_SECRET=SECRET)
class FacebookDataDeletionTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(email="ana@example.test")
        ExternalIdentity.objects.create(
            account=self.account, provider="facebook", provider_user_id="fb-1"
        )

    def send(self, value):
        return self.client.post(DELETION, {"signed_request": value}, secure=True)

    def test_a_signed_request_unlinks_facebook_and_answers_as_meta_expects(self):
        response = self.send(signed_request())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"url", "confirmation_code"})
        self.assertEqual(
            body["url"], f"https://testserver{DELETION}{body['confirmation_code']}/"
        )
        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertTrue(Account.objects.filter(pk=self.account.pk).exists())
        self.assertTrue(AccountAuditEvent.objects.filter(event="identity_removed").exists())

        status = self.client.get(f"{DELETION}{body['confirmation_code']}/", secure=True)

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json(), {"status": "completed"})

    def test_an_unknown_facebook_user_gets_the_same_answer(self):
        response = self.send(signed_request(user_id="fb-unknown"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"url", "confirmation_code"})
        self.assertTrue(ExternalIdentity.objects.exists())

    def test_an_unsigned_or_oversized_request_changes_nothing(self):
        forged = self.send(signed_request(secret="someone-elses-secret"))
        missing = self.client.post(DELETION, {}, secure=True)
        huge = self.send(signed_request() + "A" * 20_000)

        self.assertEqual((forged.status_code, forged.json()["error"]["code"]), (400, "invalid_signature"))
        self.assertEqual(missing.status_code, 400)
        self.assertEqual((huge.status_code, huge.json()["error"]["code"]), (413, "payload_too_large"))
        self.assertTrue(ExternalIdentity.objects.exists())

    def test_a_made_up_confirmation_code_is_404(self):
        response = self.client.get(f"{DELETION}made-up-code/", secure=True)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    @override_settings(FACEBOOK_APP_SECRET="")
    def test_without_facebook_variables_the_callback_is_404(self):
        self.assertEqual(self.send(signed_request()).status_code, 404)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_api_facebook_deletion`
Expected: FAIL: the requests answer 404 because the endpoints do not exist yet (the last test passes).

- [ ] **Step 3: Write the implementation**

In `apps/accounts/api_facebook.py`:

1. Add `from django.core import signing` directly below `from django.conf import settings`.

2. Add at the end of the file:

```python
# Meta's data deletion callback. Meta's servers call it; the signature is the proof.
DELETION_PATH = "/v1/webhooks/facebook/data-deletion/"
DELETION_SALT = "accounts.facebook.data-deletion"
DELETION_MAX_BYTES = 16_384


class DeletionOut(Schema):
    url: str
    confirmation_code: str


class DeletionStatusOut(Schema):
    status: str


@router.post("/webhooks/facebook/data-deletion/", response=DeletionOut)
def facebook_data_deletion(request):
    _require_facebook()
    if len(request.body) > DELETION_MAX_BYTES:
        raise ApiError("payload_too_large", "The request is too large.", status=413)
    try:
        user_id = facebook.parse_signed_request(request.POST.get("signed_request", ""))
    except facebook.BadSignedRequest:
        raise ApiError("invalid_signature", "The request is not signed by Meta.") from None
    # The same answer whether or not we knew this Facebook user, so the call reveals nothing.
    services.remove_facebook_identity(user_id)
    # The unlink is already done, so there is nothing to track: the code is a value we signed,
    # and the status endpoint only has to recognise its own signature.
    code = signing.dumps(secrets.token_hex(8), salt=DELETION_SALT)
    return {"url": request.build_absolute_uri(f"{DELETION_PATH}{code}/"), "confirmation_code": code}


@router.get("/webhooks/facebook/data-deletion/{code}/", response=DeletionStatusOut)
def facebook_data_deletion_status(request, code: str):
    try:
        signing.loads(code, salt=DELETION_SALT)
    except signing.BadSignature:
        raise ApiError("not_found", "No such request.", status=404) from None
    return {"status": "completed"}
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.accounts.tests.test_api_facebook_deletion`
Expected: `Ran 5 tests` and `OK`.

- [ ] **Step 5: Run every check CI runs, then commit**

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
DJANGO_DEBUG=false DJANGO_SECRET_KEY=local-check-only-long-random-value-0123456789abcdef uv run python manage.py check --deploy
```

Expected: `No changes detected`, `OK` (15 more tests than `origin/main`), and only the two deferred HSTS warnings.

```bash
git add apps/accounts/api_facebook.py apps/accounts/tests/test_api_facebook_deletion.py
git commit -m "feat: add Meta's data deletion callback"
```

---

### Task 6: PR B, staging verification, and the record

**Files:**
- Modify: `README.md`, `AGENTS.md`

- [ ] **Step 1: Update the documents**

In `README.md`, replace the `apps/accounts` line of the Layout list with:

```markdown
- `apps/accounts`: subscribers (`Account`), their sessions, email-code, Google and Facebook sign-in, and account settings. Other apps call `apps/accounts/services.py`, never the tables.
```

and add to the Railway variables list, below the Google line:

```markdown
- `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET`: the Meta app for this environment. Without them the Facebook sign-in endpoints and the data deletion callback answer 404.
```

In `AGENTS.md`, replace the line that begins `- Only `apps/accounts/google.py` talks to Google.` with:

```markdown
- Only `apps/accounts/google.py` talks to Google and only `apps/accounts/facebook.py` talks to Facebook. Never store or log a provider code, token or response.
- A Facebook email is never trusted until the subscriber confirms it with an email code.
```

```bash
git add README.md AGENTS.md
git commit -m "docs: record Facebook sign-in"
```

- [x] **Step 2: Open PR B**

Push and open the pull request "feat: Facebook sign-in endpoints and the data deletion callback". Say in the description that it adds no migration and that every new endpoint answers 404 until the Facebook variables are set. Enable auto-merge.

- [x] **Step 3: Verify the deploy on staging**

```bash
scripts/wait_railway_deploy.sh <before-deployment-id>
scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <merge-commit-prefix>
curl -s https://api-staging.negosyonline.com/v1/openapi.json | python3 -c 'import json,sys; print(sorted(p for p in json.load(sys.stdin)["paths"] if "facebook" in p))'
curl -s -o /dev/null -w "%{http_code}\n" https://api-staging.negosyonline.com/v1/auth/facebook/start/
```

Expected: `OK:` with the merge commit; the five paths `/v1/auth/facebook/callback/`, `/v1/auth/facebook/confirm-email/`, `/v1/auth/facebook/start/`, `/v1/webhooks/facebook/data-deletion/` and `/v1/webhooks/facebook/data-deletion/{code}/`; then `404` (no Facebook variables yet).

- [x] **Step 4: APPROVAL. Facebook variables on staging**

Before this step Coach Ed adds `https://api-staging.negosyonline.com/v1/auth/facebook/callback/` to the Meta app's "Valid OAuth Redirect URIs" (Facebook Login settings).

With Coach Ed's approval, `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET` are set on the staging `api` service (it redeploys). The app secret is entered by Coach Ed in Railway, never pasted into a chat or a file. Then:

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://api-staging.negosyonline.com/v1/auth/facebook/start/ | sed 's/state=[^&]*/state=<hidden>/'
curl -s -o /dev/null -w "%{http_code}\n" -X POST -d 'signed_request=not.signed' https://api-staging.negosyonline.com/v1/webhooks/facebook/data-deletion/
```

Expected: `302 https://www.facebook.com/dialog/oauth?client_id=...&redirect_uri=https%3A%2F%2Fapi-staging.negosyonline.com%2Fv1%2Fauth%2Ffacebook%2Fcallback%2F&...&scope=public_profile%2Cemail`, then `400` (an unsigned deletion request is refused). If `redirect_uri` shows `http` or another host, stop.

- [x] **Step 5: APPROVAL. One real Facebook sign-in on staging**

With Coach Ed's approval, Coach Ed (not an agent) opens this in his own browser and continues with a Facebook account he chooses:

```
https://api-staging.negosyonline.com/v1/auth/facebook/start/?accept_policies=true
```

Expected: Facebook's permission screen, then a redirect to `https://www-staging.negosyonline.com/?signin=1&facebook=confirm`.

If the site's popup already handles `facebook=confirm`, he finishes there. If it does not yet, he opens the browser's developer console on that page and runs these two lines, one after the other, reading the code from his inbox in between:

```javascript
await (await fetch("https://api-staging.negosyonline.com/v1/auth/facebook/confirm-email/", {method: "POST", credentials: "include", headers: {"Content-Type": "application/json"}, body: "{}"})).json()
await (await fetch("https://api-staging.negosyonline.com/v1/auth/facebook/confirm-email/", {method: "POST", credentials: "include", headers: {"Content-Type": "application/json"}, body: JSON.stringify({code: "<the 6 digits>"})})).json()
```

Expected: `{email: "..."}` naming where the code went, then `{account: {...}, created: ...}`. He then opens `https://api-staging.negosyonline.com/v1/me/` in the same browser and sees his account. This creates one real account on staging, or links Facebook to the account that already has that email. A second visit to the `start` link then goes straight to the dashboard with no code.

- [ ] **Step 6: APPROVAL. Meta's data deletion URL** (deferred to cutover, 2026-11-10)

Only when Coach Ed has decided decision 6 in favour of it: he sets the Meta app's "Data Deletion Request URL" to `https://api-staging.negosyonline.com/v1/webhooks/facebook/data-deletion/`. Meta's dashboard offers a test send; the expected result there is a confirmation code and a status URL that opens to `{"status": "completed"}`. If the legacy sign-in still uses this Meta app, skip this step and record that it is deferred to cutover.

- [x] **Step 7: Tell the coordinator**

Tell the coordinator session for ClickUp task `z8uq9mach5` that Facebook sign-in is live on staging, with the three sign-in endpoints, the `confirm-email` table and the error codes from Task 4.

---

## Done when

- `uv run python manage.py test` passes locally and in CI, with 36 more tests than before this plan (5 + 7 + 9 in PR A; 10 + 5 in PR B).
- `makemigrations --check --dry-run` reports no changes: this plan adds no migration.
- Staging publishes the five Facebook paths in `/v1/openapi.json`, `start` redirects to Facebook with the staging callback URL, and an unsigned data deletion request is refused with 400.
- Coach Ed has signed in once with Facebook on staging, confirmed his email by code, and seen his account at `/v1/me/`.
- The coordinator session has the contract from Task 6 Step 7.

## Self-review against the spec

- Section 4, "Facebook: server-side OAuth redirect flow requesting only `public_profile` and `email`": Task 2 (`authorization_url`) and Task 4.
- Section 4, "Facebook does not vouch for the email, and may return none ... the email is the one Facebook returned, or one the subscriber enters if Facebook returned none; the subscriber confirms that email with the email code; only then is the identity linked to the existing account with that email, or a new account created": Task 3 (`send_facebook_email_code`, `confirm_facebook_email`) and Task 4 (`confirm-email`, the pending cookie).
- Section 4, "Later sign-ins with the same Facebook identity need no code": Task 3 (`sign_in_with_facebook`) and Task 4 (`callback`).
- Section 4, "matched first by the provider's own user id, then by email. An account can hold one identity per provider": Task 3; the constraints already exist from slice 2b.
- Section 4, "Meta requires a data deletion callback. The API verifies the signed request with the app secret, removes that account's Facebook identity, records an audit event and answers with a confirmation code and status URL": Task 2 (`parse_signed_request`), Task 3 (`remove_facebook_identity`), Task 5.
- Section 4, "Provider access tokens are used only during the callback and are never stored": Task 2 keeps the token in a local variable for two calls and returns only the user id, name and email.
- Section 4, "Every state-changing request must carry an `Origin`": `confirm-email` calls `require_allowed_origin`. The callback is a top-level `GET` protected by the signed state cookie, as for Google. The data deletion callback comes from Meta's servers and is protected by the signature instead.
- Section 4, "Verifying an email by code revokes that account's earlier sessions": Task 3 (`confirm_facebook_email`, for an existing account).
- Section 8, webhooks, "body size cap" and constant-time comparison: Task 5 (16 KB cap) and Task 2 (`hmac.compare_digest`).
- Section 8 endpoints: `auth/facebook/start/`, `auth/facebook/callback/`, `auth/facebook/confirm-email/` (Task 4), `webhooks/facebook/data-deletion/` (Task 5).
- Beyond the spec's endpoint table, and so listed under "Decisions for Coach Ed": the status URL `GET webhooks/facebook/data-deletion/{code}/` and the double duty of `confirm-email`.
