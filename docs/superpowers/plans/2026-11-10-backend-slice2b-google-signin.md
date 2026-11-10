# Backend Slice 2b (Google Sign-In) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status: approved by Coach Ed on 2026-11-10, as written, with the six decisions below as recommended. The three staging steps marked APPROVAL still need his approval at the time.**

**Goal:** A subscriber can sign up and sign in with their Google account: the dashboard sends the browser to the API, the API sends it to Google, and Google sends it back to the API, which signs the account in and returns the browser to the dashboard.

**Architecture:** `apps/accounts` gains one table, `ExternalIdentity` (shared later with Facebook), one module that talks to Google (`google.py`), one service function (`sign_in_with_google`) and two `GET` endpoints. The flow is the server-side OAuth redirect flow; the `state` value is bound to the browser with a short-lived signed cookie. Google's tokens are used once inside the callback and never stored.

**Tech Stack:** Python 3.12, Django 5.2, Django Ninja 1.x, Python's standard library for the one HTTPS call to Google (no new dependency), `uv`, Django's test runner.

**Spec:** `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`, section 4 (the Google line, the matching rule, "provider access tokens ... are never stored"), section 6 (`ExternalIdentity`) and section 8 (`GET auth/google/start/`, `GET auth/google/callback/`).

## Scope of this plan

- **In:** `ExternalIdentity`, the Google redirect flow, signing in or creating an account from it, linking Google to an existing account with the same email.
- **Not in:** Facebook and Meta's data deletion callback (2c), unlinking Google from an account, showing linked providers in account settings, any frontend work, anything from slice 3 onward.

## Decisions (approved by Coach Ed on 2026-11-10)

Coach Ed approved each recommendation below as written. The landing paths in decision 3 must still be confirmed with the coordinator session before PR B.

1. **Other sessions stay signed in.** The spec says an email-code sign-in revokes the account's earlier sessions. It does not say so for Google. Recommended: a Google sign-in leaves other sessions alone, so a subscriber can use a phone and a laptop. (Changing this is one line in Task 3.)
2. **Accepting the policies.** A new account must accept the terms and privacy policy. Recommended: the dashboard's "Continue with Google" link carries `?accept_policies=true` when the box is ticked. If Google returns a person with no account and the box was not ticked, no account is created and the browser returns to the dashboard with `error=policies_required`.
3. **Where the browser lands.** Recommended: success goes to `<DASHBOARD_URL>/`; failure goes to `<DASHBOARD_URL>/sign-in?error=<code>`. `DASHBOARD_URL` is a new Railway variable (staging: `https://dash-staging.negosyonline.com`). The two paths are a contract with the frontend, so the coordinator session must confirm them before PR B.
4. **Linking by email.** Per the spec: when Google says the email is verified and an account with that email exists, Google is linked to it and the person is signed in. If that account is already linked to a different Google user, the sign-in is refused (`google_account_mismatch`). A Google email that Google has not verified is refused (`google_email_unverified`).
5. **No new dependency.** The token exchange is one HTTPS `POST`, done with the standard library. The ID token is read without checking its signature, which Google's OpenID Connect guide allows when the token comes straight from Google's token endpoint over TLS; issuer, audience and expiry are still checked.
6. **The Google OAuth client is created by Coach Ed** in Google Cloud (type "Web application", scopes `openid email profile`). The client secret is entered by him in Railway and never pasted into a chat or a file.

## Rules for whoever executes this

- Read `CLAUDE.md` first. Work on feature branches; `main` is protected; merge through pull requests with CI green.
- This session owns migrations in `apps/accounts`. **Before opening PR A, tell the coordinator session for ClickUp task `z8uq9mach5`** that `accounts.0002_externalidentity` is coming, and wait for its acknowledgement that no other open branch adds an `apps/accounts` migration.
- Three steps change staging state and need Coach Ed's explicit approval at the time: Task 4 Step 6 (apply the migration), Task 6 Step 4 (Google variables) and Task 6 Step 5 (one real Google sign-in). Do not run them on standing permission.
- Generating the migration file in Task 1 is part of this plan; Coach Ed approves it by approving the plan.
- Never print secrets. Never touch `production`. Never type a Google password: the real sign-in in Task 6 is done by Coach Ed in his own browser.
- All commands run from the repository root with `uv run`. Tests pass `secure=True` to the test client.
- `railway ssh` re-parses the remote command: wrap the whole remote command in single quotes.
- GitHub refuses auto-merge on a pull request that is already fully green; merge it directly in that case.

## File structure

| File | Responsibility |
|---|---|
| `apps/accounts/models.py` | Gains `ExternalIdentity`: which provider user is linked to which account. |
| `apps/accounts/google.py` | The only code that talks to Google: builds the sign-in URL, exchanges the code, returns a `GoogleProfile`. |
| `apps/accounts/services.py` | Gains `sign_in_with_google()`; account creation moves into one shared helper. |
| `apps/accounts/api.py` | Gains `GET /v1/auth/google/start/` and `GET /v1/auth/google/callback/`. |
| `apps/accounts/admin.py` | Shows linked identities, read-only. |
| `config/settings.py`, `.env.example`, `README.md`, `AGENTS.md`, `docs/runbooks/migrations.md` | Settings, example variables, the record. |

Two pull requests. PR A is Tasks 1 to 4: the table, the Google client and the service, no endpoints, so it is safe to deploy before its migration is applied. PR B is Tasks 5 and 6: the endpoints, which need the table on staging first.

How the flow works, for the reader who has not built one:

1. The dashboard links to `GET /v1/auth/google/start/?accept_policies=true`.
2. The API makes a random `state`, stores it in a signed, `HttpOnly` cookie that lasts 10 minutes and is sent only to `/v1/auth/google/`, and redirects the browser to Google.
3. Google redirects the browser to `GET /v1/auth/google/callback/?code=...&state=...`.
4. The API checks that `state` equals the one in the cookie (this proves the same browser started the flow), swaps `code` for the person's Google profile, signs them in, sets the session cookie and redirects to the dashboard.

---

### Task 1: The `ExternalIdentity` table

**Files:**
- Modify: `apps/accounts/models.py`, `apps/accounts/tests/test_models.py`
- Create: `apps/accounts/migrations/0002_externalidentity.py` (generated)

- [ ] **Step 1: Create the branch**

```bash
git fetch origin && git checkout -b feat/google-identity origin/main
```

- [ ] **Step 2: Write the failing test**

In `apps/accounts/tests/test_models.py`, change the models import to:

```python
from apps.accounts.models import Account, ExternalIdentity, Session
```

and add this class at the end of the file:

```python
class ExternalIdentityModelTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(email="ana@example.test")
        ExternalIdentity.objects.create(
            account=self.account, provider="google", provider_user_id="g-1"
        )

    def test_a_provider_user_links_to_one_account_only(self):
        other = Account.objects.create(email="ben@example.test")

        with self.assertRaises(IntegrityError), transaction.atomic():
            ExternalIdentity.objects.create(
                account=other, provider="google", provider_user_id="g-1"
            )

    def test_an_account_holds_one_identity_per_provider(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExternalIdentity.objects.create(
                account=self.account, provider="google", provider_user_id="g-2"
            )

        ExternalIdentity.objects.create(
            account=self.account, provider="facebook", provider_user_id="f-1"
        )
        self.assertEqual(self.account.identities.count(), 2)
```

- [ ] **Step 3: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_models`
Expected: FAIL with `ImportError: cannot import name 'ExternalIdentity'`.

- [ ] **Step 4: Write the implementation**

Add this class at the end of `apps/accounts/models.py`:

```python
class ExternalIdentity(models.Model):
    """A Google or Facebook user linked to an account. No provider token is ever stored."""

    class Provider(models.TextChoices):
        GOOGLE = "google"
        FACEBOOK = "facebook"

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="identities")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    provider_user_id = models.CharField(max_length=255)
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_user_id"], name="unique_provider_user"
            ),
            models.UniqueConstraint(
                fields=["account", "provider"], name="one_identity_per_provider"
            ),
        ]

    def __str__(self):
        return f"{self.provider} for {self.account}"
```

`facebook` is listed now so slice 2c needs no migration for it.

- [ ] **Step 5: Generate the migration, run the test and watch it pass**

```bash
uv run python manage.py makemigrations accounts
uv run python manage.py test apps.accounts.tests.test_models
```

Expected: `apps/accounts/migrations/0002_externalidentity.py` with `Create model ExternalIdentity` (and its two constraints), then `Ran 5 tests` and `OK`. If the migration contains anything other than creating `ExternalIdentity`, stop and tell Coach Ed.

- [ ] **Step 6: Commit**

```bash
git add apps/accounts/models.py apps/accounts/migrations/0002_externalidentity.py apps/accounts/tests/test_models.py
git commit -m "feat: add the external identity table"
```

---

### Task 2: The Google client

**Files:**
- Create: `apps/accounts/google.py`, `apps/accounts/tests/test_google.py`
- Modify: `config/settings.py`, `.env.example`

- [ ] **Step 1: Write the failing test**

`apps/accounts/tests/test_google.py`:

```python
import base64
import json
import time
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings

from apps.accounts import google

REDIRECT = "https://api.example.test/v1/auth/google/callback/"


def id_token(**overrides):
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "client-id",
        "exp": time.time() + 3600,
        "sub": "g-123",
        "email": "Ana@Example.test",
        "email_verified": True,
        "name": "Ana Reyes",
    }
    claims.update(overrides)
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


@override_settings(GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="client-secret")
class GoogleClientTests(SimpleTestCase):
    def fetch(self, **exchange):
        with mock.patch("apps.accounts.google._token_response", **exchange) as call:
            profile = google.fetch_profile("the-code", REDIRECT)
        call.assert_called_once_with("the-code", REDIRECT)
        return profile

    def test_is_configured_needs_both_the_id_and_the_secret(self):
        self.assertTrue(google.is_configured())
        with override_settings(GOOGLE_CLIENT_SECRET=""):
            self.assertFalse(google.is_configured())

    def test_authorization_url_asks_google_for_a_code(self):
        url = urlsplit(google.authorization_url(REDIRECT, "the-state"))
        query = {key: values[0] for key, values in parse_qs(url.query).items()}

        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", google.AUTH_URL)
        self.assertEqual(
            query,
            {
                "client_id": "client-id",
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "scope": "openid email profile",
                "state": "the-state",
                "prompt": "select_account",
            },
        )
        self.assertNotIn("client-secret", url.query)

    def test_fetch_profile_reads_the_person_from_the_id_token(self):
        profile = self.fetch(return_value={"id_token": id_token(), "access_token": "unused"})

        self.assertEqual(
            profile,
            google.GoogleProfile(
                user_id="g-123", email="Ana@Example.test", email_verified=True, name="Ana Reyes"
            ),
        )

    def test_a_token_not_meant_for_us_is_refused(self):
        bad_tokens = [
            id_token(aud="someone-else"),
            id_token(iss="https://evil.example.test"),
            id_token(exp=time.time() - 1),
            id_token(sub=""),
            "not-a-token",
        ]
        for token in bad_tokens:
            with self.assertLogs("apps.accounts.google", level="WARNING"):
                with self.assertRaises(google.GoogleSignInFailed):
                    self.fetch(return_value={"id_token": token})

    def test_a_failed_exchange_is_refused(self):
        for exchange in ({"side_effect": OSError("down")}, {"return_value": {"error": "bad"}}):
            with self.assertLogs("apps.accounts.google", level="WARNING") as logs:
                with self.assertRaises(google.GoogleSignInFailed):
                    self.fetch(**exchange)
            self.assertNotIn("the-code", "".join(logs.output))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_google`
Expected: FAIL with `ImportError: cannot import name 'google' from 'apps.accounts'`.

- [ ] **Step 3: Write the implementation**

`apps/accounts/google.py`:

```python
"""Google sign-in, server-side redirect flow. Google's tokens are used once and never stored."""
import base64
import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
TIMEOUT = 10  # seconds


@dataclass(frozen=True)
class GoogleProfile:
    user_id: str
    email: str
    email_verified: bool
    name: str


class GoogleSignInFailed(Exception):
    """Google refused the code, or answered with something we cannot trust."""


def is_configured():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def authorization_url(redirect_uri, state):
    query = urllib.parse.urlencode(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
    )
    return f"{AUTH_URL}?{query}"


def _token_response(code, redirect_uri):
    """Swap the one-time code for Google's answer. The only network call in this module."""
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def _claims(id_token):
    # The signature is not checked: the token came straight from Google's token endpoint over
    # TLS, authenticated with our client secret. Issuer, audience and expiry are checked below.
    payload = id_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def fetch_profile(code, redirect_uri):
    """The Google user behind this one-time code. Raises GoogleSignInFailed."""
    try:
        claims = _claims(_token_response(code, redirect_uri)["id_token"])
        if claims["iss"] not in ISSUERS or claims["aud"] != settings.GOOGLE_CLIENT_ID:
            raise ValueError("not issued by Google for this client")
        if float(claims["exp"]) <= time.time():
            raise ValueError("expired")
        if not claims["sub"]:
            raise ValueError("no subject")
        return GoogleProfile(
            user_id=str(claims["sub"]),
            email=str(claims.get("email", "")),
            email_verified=claims.get("email_verified") is True,
            name=str(claims.get("name", ""))[:120],
        )
    except (OSError, KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
        # Only the kind of failure is logged: never the code, a token or Google's answer.
        logger.warning("Google sign-in refused (%s)", type(error).__name__)
        raise GoogleSignInFailed() from None
```

In `config/settings.py`, directly below the `POLICIES = ["terms", "privacy"]` line, add:

```python
# Google sign-in. With no client id and secret the Google endpoints answer 404.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
```

In `.env.example`, add:

```
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.accounts.tests.test_google`
Expected: `Ran 5 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/accounts/google.py apps/accounts/tests/test_google.py config/settings.py .env.example
git commit -m "feat: add the Google sign-in client"
```

---

### Task 3: The sign-in service

**Files:**
- Create: `apps/accounts/tests/test_services_google.py`
- Modify: `apps/accounts/services.py`

- [ ] **Step 1: Write the failing test**

`apps/accounts/tests/test_services_google.py`:

```python
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts import services
from apps.accounts.google import GoogleProfile
from apps.accounts.models import Account, AccountAuditEvent, ExternalIdentity, PolicyAcceptance
from apps.accounts.sessions import find_session, start_session
from apps.common.errors import ApiError

EMAIL = "ana@example.test"
IP = "203.0.113.9"
PROFILE = GoogleProfile(
    user_id="g-123", email="Ana@Example.test", email_verified=True, name="Ana Reyes"
)


class GoogleSignInServiceTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/", REMOTE_ADDR=IP)

    def sign_in(self, profile=PROFILE, accept_policies=True):
        return services.sign_in_with_google(profile, accept_policies, self.request)

    def events(self):
        return list(AccountAuditEvent.objects.values_list("event", flat=True).order_by("id"))

    def test_first_sign_in_creates_the_account_and_links_google(self):
        account, session, token, created = self.sign_in()

        self.assertTrue(created)
        self.assertEqual((account.email, account.name), (EMAIL, "Ana Reyes"))
        self.assertIsNotNone(account.email_verified_at)
        self.assertEqual(find_session(token), session)
        identity = ExternalIdentity.objects.get(account=account)
        self.assertEqual((identity.provider, identity.provider_user_id), ("google", "g-123"))
        acceptance = PolicyAcceptance.objects.get(account=account)
        self.assertEqual((acceptance.policy_version, acceptance.ip), ("2026-11-10", IP))
        self.assertEqual(self.events(), ["account_created", "identity_linked", "signed_in"])

    def test_new_account_must_accept_policies(self):
        with self.assertRaises(ApiError) as raised:
            self.sign_in(accept_policies=False)

        self.assertEqual(raised.exception.code, "policies_required")
        self.assertFalse(Account.objects.exists())
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_known_google_user_signs_in_whatever_email_google_now_reports(self):
        first, _session, first_token, _ = self.sign_in()
        moved = GoogleProfile(
            user_id="g-123", email="other@example.test", email_verified=False, name="A"
        )

        account, _session, token, created = self.sign_in(moved, accept_policies=False)

        self.assertFalse(created)
        self.assertEqual(account, first)
        self.assertEqual(account.email, EMAIL)
        self.assertEqual(Account.objects.count(), 1)
        self.assertIsNotNone(find_session(token))
        self.assertIsNotNone(find_session(first_token))

    def test_existing_account_with_that_email_is_linked_and_keeps_its_sessions(self):
        existing = Account.objects.create(email=EMAIL, name="Ana")
        _earlier, earlier_token = start_session(existing, self.request)

        account, _session, _token, created = self.sign_in(accept_policies=False)

        self.assertFalse(created)
        self.assertEqual(account, existing)
        self.assertEqual(account.name, "Ana")
        self.assertIsNotNone(account.email_verified_at)
        self.assertTrue(existing.identities.filter(provider="google").exists())
        self.assertIsNotNone(find_session(earlier_token))
        self.assertEqual(self.events(), ["identity_linked", "signed_in"])

    def test_an_email_google_has_not_verified_is_refused(self):
        unverified = GoogleProfile(
            user_id="g-123", email=EMAIL, email_verified=False, name="Ana"
        )

        with self.assertRaises(ApiError) as raised:
            self.sign_in(unverified)

        self.assertEqual(raised.exception.code, "google_email_unverified")
        self.assertFalse(Account.objects.exists())

    def test_an_account_linked_to_another_google_user_is_refused(self):
        self.sign_in()
        impostor = GoogleProfile(user_id="g-999", email=EMAIL, email_verified=True, name="X")

        with self.assertRaises(ApiError) as raised:
            self.sign_in(impostor)

        self.assertEqual(
            (raised.exception.code, raised.exception.status), ("google_account_mismatch", 409)
        )
        self.assertEqual(ExternalIdentity.objects.count(), 1)

    def test_closed_account_cannot_sign_in_by_identity_or_by_email(self):
        account, _session, _token, _ = self.sign_in()
        Account.objects.filter(pk=account.pk).update(closed_at=timezone.now())

        with self.assertRaises(ApiError) as by_identity:
            self.sign_in()

        ExternalIdentity.objects.all().delete()
        with self.assertRaises(ApiError) as by_email:
            self.sign_in()

        self.assertEqual((by_identity.exception.code, by_identity.exception.status), ("account_closed", 403))
        self.assertEqual(by_email.exception.code, "account_closed")
        self.assertFalse(ExternalIdentity.objects.exists())
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_services_google`
Expected: FAIL (errors) with `AttributeError: module 'apps.accounts.services' has no attribute 'sign_in_with_google'`.

- [ ] **Step 3: Write the implementation**

In `apps/accounts/services.py`:

1. Change the models import to:

```python
from apps.accounts.models import Account, AccountAuditEvent, ExternalIdentity, PolicyAcceptance
```

2. Directly below the line `SIGNIN = "signin"`, add:

```python
GOOGLE = ExternalIdentity.Provider.GOOGLE
```

3. Directly below the `_change_purpose` function, add these three helpers. They hold what the email-code and Google sign-ins share:

```python
def _refuse_closed(account):
    if account.closed_at is not None:
        raise ApiError("account_closed", "This account is closed.", status=403)


def _policies_required():
    return ApiError(
        "policies_required", "Accept the terms and privacy policy to create an account."
    )


def _create_account(email, request, *, method, name=""):
    """A new account whose email is already proven, with its policy acceptance."""
    account = Account.objects.create(email=email, name=name, email_verified_at=timezone.now())
    PolicyAcceptance.objects.create(
        account=account,
        policy_version=settings.POLICY_VERSION,
        policies=list(settings.POLICIES),
        ip=client_ip(request) or None,
    )
    record_event(account, "account_created", method=method)
    return account
```

4. Replace the whole `verify_signin_code` function with this one. Its behaviour does not change; it now uses the helpers:

```python
@transaction.atomic
def verify_signin_code(email, code, accept_policies, request):
    """Sign in, creating the account on first use. Returns (account, session, token, created)."""
    email = normalise_email(email)
    if not codes.check_code(email, code, SIGNIN):
        raise _invalid_code()
    account = Account.objects.filter(email=email).first()
    created = account is None
    if created:
        if not accept_policies:
            raise _policies_required()
    else:
        _refuse_closed(account)
    codes.clear_code(email, SIGNIN)
    if created:
        account = _create_account(email, request, method="email_code")
    else:
        if account.email_verified_at is None:
            account.email_verified_at = timezone.now()
            account.save(update_fields=["email_verified_at"])
        revoke_sessions(account)
    session, token = start_session(account, request)
    record_event(account, "signed_in", method="email_code", session=session.public_id)
    return account, session, token, created
```

5. Add this function directly below `verify_signin_code`:

```python
@transaction.atomic
def sign_in_with_google(profile, accept_policies, request):
    """Sign in with a profile from apps.accounts.google. Returns (account, session, token, created).

    The Google user id is matched first, then the email. Other sessions are left signed in.
    """
    created = False
    identity = (
        ExternalIdentity.objects.select_related("account")
        .filter(provider=GOOGLE, provider_user_id=profile.user_id)
        .first()
    )
    if identity is not None:
        account = identity.account
        _refuse_closed(account)
    else:
        if not profile.email_verified:
            raise ApiError("google_email_unverified", "Google has not verified that email address.")
        email = normalise_email(profile.email)
        account = Account.objects.filter(email=email).first()
        if account is None:
            if not accept_policies:
                raise _policies_required()
            account = _create_account(email, request, method="google", name=profile.name)
            created = True
        else:
            _refuse_closed(account)
            if account.identities.filter(provider=GOOGLE).exists():
                raise ApiError(
                    "google_account_mismatch",
                    "This email is already linked to a different Google account.",
                    status=409,
                )
            if account.email_verified_at is None:
                account.email_verified_at = timezone.now()
                account.save(update_fields=["email_verified_at"])
        ExternalIdentity.objects.create(
            account=account, provider=GOOGLE, provider_user_id=profile.user_id
        )
        record_event(account, "identity_linked", provider="google")
    session, token = start_session(account, request)
    record_event(account, "signed_in", method="google", session=session.public_id)
    return account, session, token, created
```

If Coach Ed answers decision 1 the other way, add `revoke_sessions(account)` on the line above `session, token = start_session(account, request)` and change the two "keeps its sessions" assertions in the tests to `assertIsNone`.

- [ ] **Step 4: Run it and watch it pass, with the email-code tests still green**

Run: `uv run python manage.py test apps.accounts.tests.test_services_google apps.accounts.tests.test_services apps.accounts.tests.test_api_auth`
Expected: `Ran 28 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/accounts/services.py apps/accounts/tests/test_services_google.py
git commit -m "feat: add the Google sign-in service"
```

---

### Task 4: Admin page, PR A, and the staging migration

**Files:**
- Modify: `apps/accounts/admin.py`

- [ ] **Step 1: Show linked identities in the admin**

In `apps/accounts/admin.py`, change the models import to:

```python
from apps.accounts.models import (
    Account,
    AccountAuditEvent,
    ExternalIdentity,
    PolicyAcceptance,
    Session,
)
```

and add at the end of the file:

```python
@admin.register(ExternalIdentity)
class ExternalIdentityAdmin(ReadOnlyAdmin):
    list_display = ("account", "provider", "linked_at")
    list_filter = ("provider",)
```

- [ ] **Step 2: Run every check CI runs**

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
DJANGO_DEBUG=false DJANGO_SECRET_KEY=local-check-only-long-random-value-0123456789abcdef uv run python manage.py check --deploy
```

Expected: `No changes detected`, `OK` (89 tests), and only the two deferred HSTS warnings (W005, W021).

- [ ] **Step 3: Commit**

```bash
git add apps/accounts/admin.py
git commit -m "feat: show linked identities in the admin"
```

- [ ] **Step 4: Tell the coordinator, then open PR A**

First tell the coordinator session for ClickUp task `z8uq9mach5`: "`accounts.0002_externalidentity` is about to be opened as a pull request from `feat/google-identity`. Do not add another `apps/accounts` migration until it is merged." Wait for its acknowledgement.

Then push and open the pull request "feat: external identities and the Google sign-in service (no endpoints)". Say in the description that it adds one migration file (`accounts.0002_externalidentity`) that is not applied by the deploy, and that nothing in the running service reads the new table yet. Enable auto-merge.

- [ ] **Step 5: Verify the deploy**

Note the newest staging deployment id before the merge, then:

```bash
scripts/wait_railway_deploy.sh <before-deployment-id>
scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <merge-commit-prefix>
```

Expected: `SUCCESS ...` and `OK:` with the merge commit.

- [ ] **Step 6: APPROVAL. Apply the migration on staging**

Follow `docs/runbooks/migrations.md`: show the plan, read it, apply, confirm nothing is pending.

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- 'uv run python manage.py migrate --plan'
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- 'uv run python manage.py migrate --noinput'
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- 'uv run python manage.py migrate --plan'
```

Expected: the first plan lists exactly `accounts.0002_externalidentity`. If it lists anything else, stop and tell Coach Ed. The last command prints `No planned migration operations.`

---

### Task 5: The endpoints

Endpoints: `GET /v1/auth/google/start/`, `GET /v1/auth/google/callback/`. Both are browser navigations, not `fetch` calls, so they answer with redirects, not JSON. The one exception: when Google is not configured they answer the API's JSON 404.

The callback is a `GET` that signs someone in. It is safe from forged requests because it does nothing unless `state` matches the signed cookie that only `start` sets.

Error codes the dashboard receives in `?error=`: `google_state_invalid` (flow not started here, or older than 10 minutes), `google_cancelled` (the person backed out at Google), `google_failed` (Google refused the code), plus the service's `policies_required`, `google_email_unverified`, `google_account_mismatch`, `account_closed`.

**Files:**
- Create: `apps/accounts/tests/test_api_google.py`
- Modify: `apps/accounts/api.py`, `config/settings.py`, `.env.example`

- [ ] **Step 1: Create the branch and write the failing test**

Confirm Task 4 Step 6 has been done, and that the coordinator session has confirmed the two landing paths in decision 3.

```bash
git fetch origin && git checkout -b feat/google-signin-api origin/main
```

`apps/accounts/tests/test_api_google.py`:

```python
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

from django.test import override_settings

from apps.accounts import google
from apps.accounts.google import GoogleProfile
from apps.accounts.models import Account
from apps.accounts.tests.base import EMAIL, ApiTestCase

DASH = "https://dash.example.test"
CALLBACK = "https://testserver/v1/auth/google/callback/"
PROFILE = GoogleProfile(user_id="g-123", email=EMAIL, email_verified=True, name="Ana Reyes")


@override_settings(
    GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="client-secret", DASHBOARD_URL=DASH
)
class GoogleApiTests(ApiTestCase):
    def start(self, query=""):
        """Begin the flow and return the state Google would send back."""
        response = self.get(f"/v1/auth/google/start/{query}")
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlsplit(response["Location"]).query)["state"][0]

    def callback(self, state, code="the-code", **fetch):
        fetch = fetch or {"return_value": PROFILE}
        query = urlencode({"state": state, "code": code})
        with mock.patch("apps.accounts.api.google.fetch_profile", **fetch) as call:
            response = self.get(f"/v1/auth/google/callback/?{query}")
        self.assertEqual(response.status_code, 302)
        return response, call

    def test_start_redirects_to_google_and_sets_the_state_cookie(self):
        response = self.get("/v1/auth/google/start/")

        self.assertEqual(response.status_code, 302)
        location = urlsplit(response["Location"])
        self.assertTrue(response["Location"].startswith(google.AUTH_URL + "?"))
        self.assertEqual(parse_qs(location.query)["redirect_uri"], [CALLBACK])
        cookie = response.cookies["no_google_state"]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/v1/auth/google/")
        self.assertEqual(cookie["max-age"], 600)
        self.assertNotIn(parse_qs(location.query)["state"][0], response.cookies.output())

    def test_callback_signs_in_and_returns_to_the_dashboard(self):
        state = self.start("?accept_policies=true")

        response, call = self.callback(state)

        call.assert_called_once_with("the-code", CALLBACK)
        self.assertEqual(response["Location"], f"{DASH}/")
        self.assertTrue(response.cookies["no_session"]["httponly"])
        self.assertEqual(response.cookies["no_google_state"].value, "")
        self.assertEqual(self.get("/v1/me/").json()["account"]["email"], EMAIL)

    def test_new_account_without_accepting_policies_is_sent_back(self):
        state = self.start()

        response, _call = self.callback(state)

        self.assertEqual(response["Location"], f"{DASH}/sign-in?error=policies_required")
        self.assertNotIn("no_session", response.cookies)
        self.assertFalse(Account.objects.exists())

    def test_a_callback_this_browser_did_not_start_is_refused(self):
        without_cookie, first_call = self.callback("made-up-state")
        self.start("?accept_policies=true")
        wrong_state, second_call = self.callback("made-up-state")

        for response in (without_cookie, wrong_state):
            self.assertEqual(response["Location"], f"{DASH}/sign-in?error=google_state_invalid")
        first_call.assert_not_called()
        second_call.assert_not_called()
        self.assertEqual(self.get("/v1/me/").status_code, 401)

    def test_cancelling_at_google_is_sent_back(self):
        state = self.start("?accept_policies=true")

        response, call = self.callback(state, code="")

        self.assertEqual(response["Location"], f"{DASH}/sign-in?error=google_cancelled")
        call.assert_not_called()

    def test_a_code_google_refuses_is_sent_back(self):
        state = self.start("?accept_policies=true")

        response, _call = self.callback(state, side_effect=google.GoogleSignInFailed())

        self.assertEqual(response["Location"], f"{DASH}/sign-in?error=google_failed")
        self.assertEqual(self.get("/v1/me/").status_code, 401)

    @override_settings(GOOGLE_CLIENT_ID="")
    def test_without_google_variables_both_endpoints_are_404(self):
        for path in ("/v1/auth/google/start/", "/v1/auth/google/callback/?state=x&code=y"):
            response = self.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "not_found")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_api_google`
Expected: FAIL: `AttributeError` on `apps.accounts.api.google` in the callback tests and 404 instead of 302 in the others.

- [ ] **Step 3: Write the implementation**

In `config/settings.py`, directly below the `GOOGLE_CLIENT_SECRET = ...` line, add:

```python
# Where the browser lands after a Google sign-in. No trailing slash.
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:3001").rstrip("/")
```

In `.env.example`, add:

```
DASHBOARD_URL=http://localhost:3001
```

In `apps/accounts/api.py`:

1. Replace the imports at the top of the file (everything above `router = Router(tags=["accounts"])`) with:

```python
"""Sign-in and account endpoints. Shapes here are the contract the frontend builds against."""
import hmac
import secrets
from datetime import datetime

from django.conf import settings
from django.core import signing
from django.http import HttpResponse, HttpResponseRedirect
from ninja import Field, Router, Schema

from apps.accounts import google, services
from apps.accounts.auth import SessionCookieAuth
from apps.accounts.models import Session
from apps.accounts.sessions import (
    clear_session_cookie,
    client_ip,
    revoke_session,
    revoke_sessions,
    set_session_cookie,
)
from apps.common.errors import ApiError
from apps.common.middleware import require_allowed_origin
```

2. Add at the end of the file:

```python
# Google sign-in. These two are browser navigations: they answer with redirects, not JSON.
GOOGLE_STATE_COOKIE = "no_google_state"
GOOGLE_STATE_PATH = "/v1/auth/google/"
GOOGLE_STATE_SALT = "accounts.google.state"
GOOGLE_STATE_SECONDS = 600


def _require_google():
    if not google.is_configured():
        raise ApiError("not_found", "No such endpoint.", status=404)


def _google_callback_url(request):
    return request.build_absolute_uri("/v1/auth/google/callback/")


def _to_dashboard(error=None):
    """Send the browser back to the dashboard and end the Google flow."""
    path = f"/sign-in?error={error}" if error else "/"
    response = HttpResponseRedirect(settings.DASHBOARD_URL + path)
    response.delete_cookie(GOOGLE_STATE_COOKIE, path=GOOGLE_STATE_PATH, samesite="Lax")
    return response


@router.get("/auth/google/start/", response={302: None})
def google_start(request, accept_policies: bool = False):
    _require_google()
    state = secrets.token_urlsafe(32)
    response = HttpResponseRedirect(google.authorization_url(_google_callback_url(request), state))
    # The cookie ties the flow to this browser. It is signed and expires with the flow.
    response.set_cookie(
        GOOGLE_STATE_COOKIE,
        signing.dumps(
            {"state": state, "accept_policies": accept_policies},
            salt=GOOGLE_STATE_SALT,
            compress=True,
        ),
        max_age=GOOGLE_STATE_SECONDS,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path=GOOGLE_STATE_PATH,
    )
    return response


@router.get("/auth/google/callback/", response={302: None})
def google_callback(request, code: str = "", state: str = "", error: str = ""):
    _require_google()
    try:
        started = signing.loads(
            request.COOKIES.get(GOOGLE_STATE_COOKIE, ""),
            salt=GOOGLE_STATE_SALT,
            max_age=GOOGLE_STATE_SECONDS,
        )
    except signing.BadSignature:  # missing, altered or older than ten minutes
        return _to_dashboard("google_state_invalid")
    if not hmac.compare_digest(started["state"].encode(), state.encode()):
        return _to_dashboard("google_state_invalid")
    if error or not code:
        return _to_dashboard("google_cancelled")
    try:
        profile = google.fetch_profile(code, _google_callback_url(request))
        _account, _session, token, _created = services.sign_in_with_google(
            profile, started["accept_policies"], request
        )
    except google.GoogleSignInFailed:
        return _to_dashboard("google_failed")
    except ApiError as refused:
        return _to_dashboard(refused.code)
    response = _to_dashboard()
    set_session_cookie(response, token)
    return response
```

The state itself is not visible in the cookie: `compress=True` plus signing makes the cookie value an opaque string, and the test checks the raw state does not appear in it.

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.accounts.tests.test_api_google`
Expected: `Ran 7 tests` and `OK`.

- [ ] **Step 5: Run every check CI runs, then commit**

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
DJANGO_DEBUG=false DJANGO_SECRET_KEY=local-check-only-long-random-value-0123456789abcdef uv run python manage.py check --deploy
```

Expected: `No changes detected`, `OK` (96 tests), and only the two deferred HSTS warnings.

```bash
git add apps/accounts/api.py apps/accounts/tests/test_api_google.py config/settings.py .env.example
git commit -m "feat: add the Google sign-in endpoints"
```

---

### Task 6: PR B, staging verification, and the record

**Files:**
- Modify: `README.md`, `AGENTS.md`, `docs/runbooks/migrations.md`

- [ ] **Step 1: Update the documents**

In `README.md`, replace the `apps/accounts` line of the Layout list with:

```markdown
- `apps/accounts`: subscribers (`Account`), their sessions, email-code and Google sign-in, and account settings. Other apps call `apps/accounts/services.py`, never the tables.
```

and add to the Railway variables list:

```markdown
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`: the Google OAuth client for this environment. Without them the Google sign-in endpoints answer 404.
- `DASHBOARD_URL`: where the browser lands after a Google sign-in, with no trailing slash.
```

Add to the Safety list in `AGENTS.md`:

```markdown
- Only `apps/accounts/google.py` talks to Google. Never store or log a provider code, token or response.
```

Append to the History list in `docs/runbooks/migrations.md` (use the date Task 4 Step 6 was run):

```markdown
- <date of the run>: `accounts.0002_externalidentity` was applied to `staging`. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
```

```bash
git add README.md AGENTS.md docs/runbooks/migrations.md
git commit -m "docs: record Google sign-in and the staging migration"
```

- [ ] **Step 2: Open PR B**

Push and open the pull request "feat: Google sign-in endpoints". Say in the description that the endpoints answer 404 until the Google variables are set. Enable auto-merge.

- [ ] **Step 3: Verify the deploy on staging**

```bash
scripts/wait_railway_deploy.sh <before-deployment-id>
scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <merge-commit-prefix>
curl -s https://api-staging.negosyonline.com/v1/openapi.json | python3 -c 'import json,sys; print(sorted(p for p in json.load(sys.stdin)["paths"] if "google" in p))'
curl -s -o /dev/null -w "%{http_code}\n" https://api-staging.negosyonline.com/v1/auth/google/start/
```

Expected: `OK:` with the merge commit; `['/v1/auth/google/callback/', '/v1/auth/google/start/']`; then `404` (no Google variables yet).

- [ ] **Step 4: APPROVAL. Google variables on staging**

Before this step Coach Ed creates the OAuth client in Google Cloud: type "Web application", authorised redirect URI exactly `https://api-staging.negosyonline.com/v1/auth/google/callback/`, scopes `openid`, `email`, `profile`. While the consent screen is in "Testing", only the test users he lists can sign in.

With Coach Ed's approval, these variables are set on the staging `api` service (it redeploys): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `DASHBOARD_URL=https://dash-staging.negosyonline.com`. The client secret is entered by Coach Ed in Railway, never pasted into a chat or a file. Then:

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://api-staging.negosyonline.com/v1/auth/google/start/ | sed 's/state=[^&]*/state=<hidden>/'
```

Expected: `302 https://accounts.google.com/o/oauth2/v2/auth?client_id=...&redirect_uri=https%3A%2F%2Fapi-staging.negosyonline.com%2Fv1%2Fauth%2Fgoogle%2Fcallback%2F&...`. If `redirect_uri` shows `http` or another host, stop: the callback would not match the one registered at Google.

- [ ] **Step 5: APPROVAL. One real Google sign-in on staging**

With Coach Ed's approval, Coach Ed (not an agent) opens this in his own browser and signs in with a Google account he chooses:

```
https://api-staging.negosyonline.com/v1/auth/google/start/?accept_policies=true
```

Expected: Google's account chooser, then a redirect to `https://dash-staging.negosyonline.com/`. Whatever that page shows, he then opens `https://api-staging.negosyonline.com/v1/me/` in the same browser and sees his account as JSON. This creates one real account on staging, or links Google to the account that already has that email.

- [ ] **Step 6: Tell the coordinator**

Tell the coordinator session for ClickUp task `z8uq9mach5` that Google sign-in is live on staging, with the two endpoints, the `accept_policies` parameter, the two landing paths and the list of `error` codes from Task 5.

---

## Done when

- `uv run python manage.py test` passes locally and in CI (96 tests).
- Staging has `accounts.0002_externalidentity` applied and `migrate --plan` reports nothing pending.
- Staging publishes `/v1/auth/google/start/` and `/v1/auth/google/callback/` in `/v1/openapi.json`, and `start` redirects to Google with the staging callback URL.
- Coach Ed has signed in once with Google on staging and seen his account at `/v1/me/`.
- The coordinator session has the contract from Task 6 Step 6.

## Self-review against the spec

- Section 4, "Google: server-side OAuth redirect flow. The callback signs the account in and redirects to the dashboard": Tasks 2 and 5.
- Section 4, "Google vouches for the email": Task 3 creates or links an account only when Google reports the email as verified, and marks the account's email verified.
- Section 4, "matched first by the provider's own user id, then by email. An account can hold one identity per provider": Task 3 (`sign_in_with_google`) and the two constraints in Task 1.
- Section 4, "Provider access tokens are used only during the callback and are never stored": Task 2 keeps nothing from Google's answer except the four profile values; no table has a token column.
- Section 4, session cookie rules: the callback uses the existing `set_session_cookie`, unchanged.
- Section 4, "Every state-changing request must carry an `Origin`": the callback is a top-level `GET` from Google and cannot carry one. The signed state cookie does that job here; this is the one deliberate exception and is described in Task 5.
- Section 6, `ExternalIdentity`: account, provider (`google`, `facebook`), provider user id (unique per provider), linked_at, one row per account per provider: Task 1.
- Section 8, `GET auth/google/start/`, `GET auth/google/callback/`: Task 5.
- Not in this plan by design: Facebook, `auth/facebook/*` and the data deletion webhook (2c).
- Not decided by the spec and so listed under "Decisions for Coach Ed": whether Google sign-in revokes other sessions, how policies are accepted, and the dashboard landing paths.
