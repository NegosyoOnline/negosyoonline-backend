# Backend Slice 2a (Accounts and Email-Code Sign-In) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A subscriber can sign up and sign in with a 6-digit email code, stay signed in through a secure cookie, see and edit their account, change their email, and manage their sessions.

**Architecture:** A new `apps/accounts` app owns `Account`, `Session`, `PolicyAcceptance` and `AccountAuditEvent`, with service modules for codes, sessions and account actions; its Ninja router is mounted on the existing `/v1/` API. A new `apps/notifications` app owns `EmailMessage` and the one `send_email()` function every other app uses. Codes live in the cache; only the SHA-256 of a session token is stored.

**Tech Stack:** Python 3.12, Django 5.2, Django Ninja 1.x, Django's cache (Redis on Railway, local memory in development), Django's email backends, `uv`, Django's test runner.

**Spec:** `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`, sections 4 (everything except Google and Facebook), 6 (the `accounts` tables named above and `EmailMessage`), 8 (the Sign-in and Account rows, except Google, Facebook, mobile and referrals) and 9 item 2.

## Scope of this plan

Slice 2 is split into three plans so each one ships working software:

- **2a (this plan):** accounts, email-code sign-in, sessions, account settings.
- **2b:** Google sign-in. Adds `ExternalIdentity`.
- **2c:** Facebook sign-in and Meta's data deletion callback.

Not in this plan: `ExternalIdentity`, `AccountMobile`, `MobileOtpChallenge` (slice 8), `account/referrals/` (slice 7), sites (slice 3).

## Decisions (approved by Coach Ed on 2026-11-10)

Coach Ed approved this plan as written, with these decisions:

1. **Email provider: Resend.** It is sent through Resend's SMTP service, so no code in this plan changes. The staging variables for Task 9 Step 4 are `EMAIL_HOST=smtp.resend.com`, `EMAIL_PORT=587`, `EMAIL_HOST_USER=resend`, `EMAIL_HOST_PASSWORD=<a Resend API key>` and `DEFAULT_FROM_EMAIL` on a domain verified in Resend. TWCinc already uses Resend, but its key and configuration are not copied: NegosyoOnline gets its own API key, and Coach Ed enters it in Railway himself. Until the variables are set, sign-in emails are printed to the Railway log.
2. **A new email-code sign-in signs out the account's other sessions.** Confirmed: this follows the spec.
3. **Policy label is temporary.** `POLICY_VERSION = "2026-11-10"` and `POLICIES = ["terms", "privacy"]` are placeholders. All dates are provisional until launch; the final policy version is set on the launch date by changing the settings, with no migration.

## Rules for whoever executes this

- Read `CLAUDE.md` first. Work on feature branches; `main` is protected; merge through pull requests with CI green.
- Three steps change staging state and need Coach Ed's explicit approval at the time: Task 6 Step 6 (apply migrations), Task 9 Step 4 (the Resend email variables) and Task 9 Step 5 (creating a test account on staging). Do not run them on standing permission.
- Generating the migration files in Task 6 Step 2 is part of this plan; Coach Ed approves it by approving the plan.
- Never print secrets. Never touch `production`.
- All commands run from the repository root with `uv run`.
- Tests use Django's runner and pass `secure=True` to the test client.
- `railway ssh` re-parses the remote command: wrap the whole remote command in single quotes.
- GitHub refuses auto-merge on a pull request that is already fully green; merge it directly in that case.

## File structure

| File | Responsibility |
|---|---|
| `apps/notifications/models.py` | `EmailMessage`: who was emailed, which category, what happened. No body is stored. |
| `apps/notifications/services.py` | `send_email()`: the only way any app sends email. |
| `apps/accounts/models.py` | `Account`, `Session`, `PolicyAcceptance`, `AccountAuditEvent`. |
| `apps/accounts/codes.py` | Issue, check and clear 6-digit codes in the cache, with rate limits. |
| `apps/accounts/sessions.py` | Start, find and revoke sessions; set and clear the cookie; mask IPs; label devices. |
| `apps/accounts/services.py` | Sign-in, email change and profile update. The functions other apps may call. |
| `apps/accounts/auth.py` | `SessionCookieAuth`: the Ninja authentication class for signed-in endpoints. |
| `apps/accounts/api.py` | The router: request and response shapes and the endpoints. |
| `apps/accounts/admin.py`, `apps/notifications/admin.py` | Read-mostly admin pages for operators. |
| `apps/common/middleware.py` | Gains `require_allowed_origin()`. |
| `config/settings.py`, `config/api.py`, `.env.example` | Settings, router mount, example variables. |

Two pull requests. PR A is Tasks 1 to 6: tables and services, no endpoints, so it is safe to deploy before its migrations are applied. PR B is Tasks 7 to 9: the endpoints, which need the tables to exist on staging first.

---

### Task 1: Email sending (notifications app)

**Files:**
- Create: `apps/notifications/__init__.py` (empty), `apps/notifications/apps.py`, `apps/notifications/models.py`, `apps/notifications/services.py`, `apps/notifications/tests.py`, `apps/notifications/migrations/__init__.py` (empty)
- Modify: `config/settings.py`, `.env.example`

- [ ] **Step 1: Create the branch**

```bash
git fetch origin && git checkout -b feat/accounts-models origin/main
mkdir -p apps/notifications/migrations
touch apps/notifications/__init__.py apps/notifications/migrations/__init__.py
```

- [ ] **Step 2: Write the failing test**

`apps/notifications/tests.py`:

```python
from unittest import mock

from django.core import mail
from django.test import TestCase

from apps.notifications.models import EmailMessage
from apps.notifications.services import EmailNotSent, send_email


class SendEmailTests(TestCase):
    def test_sends_the_email_and_records_it_without_the_body(self):
        message = send_email(
            to="ana@example.test", category="signin_code", subject="Your code", body="123456"
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ana@example.test"])
        self.assertEqual(mail.outbox[0].body, "123456")
        self.assertEqual(message.status, EmailMessage.Status.SENT)
        self.assertIsNotNone(message.sent_at)
        self.assertNotIn("body", [field.name for field in EmailMessage._meta.get_fields()])

    def test_a_failed_send_is_recorded_and_raised(self):
        with mock.patch("apps.notifications.services.send_mail", side_effect=OSError("smtp down")):
            with self.assertLogs("apps.notifications.services", level="ERROR"):
                with self.assertRaises(EmailNotSent) as raised:
                    send_email(
                        to="ana@example.test", category="signin_code", subject="s", body="b"
                    )

        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(EmailMessage.objects.get().status, EmailMessage.Status.FAILED)
```

- [ ] **Step 3: Run it and watch it fail**

Run: `uv run python manage.py test apps.notifications`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.notifications.models'`.

- [ ] **Step 4: Write the implementation**

`apps/notifications/apps.py`:

```python
from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
```

`apps/notifications/models.py`:

```python
"""A record of each message sent. Bodies are never stored: they can hold sign-in codes."""
from django.db import models


class EmailMessage(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        SENT = "sent"
        FAILED = "failed"

    recipient = models.EmailField()
    category = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider_reference = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.category} to {self.recipient} ({self.status})"
```

`apps/notifications/services.py`:

```python
"""The only way any app sends email."""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.common.errors import ApiError
from apps.notifications.models import EmailMessage

logger = logging.getLogger(__name__)


class EmailNotSent(ApiError):
    def __init__(self):
        super().__init__(
            "email_not_sent", "We could not send the email. Try again shortly.", status=503
        )


def send_email(*, to, category, subject, body):
    message = EmailMessage.objects.create(recipient=to, category=category)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to])
    except Exception:
        logger.exception("Email send failed (category=%s)", category)
        message.status = EmailMessage.Status.FAILED
        message.save(update_fields=["status"])
        raise EmailNotSent() from None
    message.status = EmailMessage.Status.SENT
    message.sent_at = timezone.now()
    message.save(update_fields=["status", "sent_at"])
    return message
```

In `config/settings.py`:

1. Add `'apps.notifications',` to `INSTALLED_APPS` directly above `'apps.health',`.
2. Directly below the `API_ALLOWED_ORIGINS = [...]` block, add:

```python
# Email. With no EMAIL_HOST the message is printed to the service log instead of sent.
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend"
)
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", "NegosyoOnline <no-reply@negosyoonline.com>"
)
```

In `.env.example`, add:

```
EMAIL_HOST=
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL=NegosyoOnline <no-reply@negosyoonline.com>
```

- [ ] **Step 5: Generate the migration, run the test and watch it pass**

```bash
uv run python manage.py makemigrations notifications
uv run python manage.py test apps.notifications
```

Expected: `apps/notifications/migrations/0001_initial.py` with `Create model EmailMessage`, then `Ran 2 tests` and `OK`.

- [ ] **Step 6: Commit**

```bash
git add apps/notifications config/settings.py .env.example
git commit -m "feat: add the notifications app and send_email"
```

---

### Task 2: Account models

**Files:**
- Create: `apps/accounts/__init__.py` (empty), `apps/accounts/apps.py`, `apps/accounts/models.py`, `apps/accounts/migrations/__init__.py` (empty), `apps/accounts/tests/__init__.py` (empty), `apps/accounts/tests/test_models.py`
- Modify: `config/settings.py`

- [ ] **Step 1: Write the failing test**

```bash
mkdir -p apps/accounts/migrations apps/accounts/tests
touch apps/accounts/__init__.py apps/accounts/migrations/__init__.py apps/accounts/tests/__init__.py
```

`apps/accounts/tests/test_models.py`:

```python
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, Session
from apps.common.ids import is_public_id


class AccountModelTests(TestCase):
    def test_account_gets_a_public_id_and_a_referral_code(self):
        account = Account.objects.create(email="ana@example.test")

        self.assertTrue(is_public_id(account.public_id, "acc"))
        self.assertEqual(len(account.referral_code), 8)
        self.assertIsNone(account.email_verified_at)
        self.assertIsNone(account.closed_at)

    def test_email_is_unique(self):
        Account.objects.create(email="ana@example.test")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.create(email="ana@example.test")

    def test_session_gets_a_public_id(self):
        account = Account.objects.create(email="ana@example.test")
        now = timezone.now()

        session = Session.objects.create(
            account=account, token_hash="0" * 64, expires_at=now, last_used_at=now
        )

        self.assertTrue(is_public_id(session.public_id, "ses"))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_models`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.accounts.models'`.

- [ ] **Step 3: Write the implementation**

`apps/accounts/apps.py`:

```python
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
```

`apps/accounts/models.py`:

```python
"""Subscribers. An Account is not a Django user; operators are (apps.operators)."""
import secrets

from django.db import models

from apps.common.ids import ALPHABET, new_public_id


def new_account_id():
    return new_public_id("acc")


def new_session_id():
    return new_public_id("ses")


def new_referral_code():
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


class Account(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_account_id, editable=False)
    email = models.EmailField(unique=True)  # always stored lowercase; see services.normalise_email
    name = models.CharField(max_length=120, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    referral_code = models.CharField(
        max_length=12, unique=True, default=new_referral_code, editable=False
    )
    referred_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="referrals"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.email


class Session(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_session_id, editable=False)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="sessions")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField()
    device_label = models.CharField(max_length=80, blank=True)
    masked_ip = models.CharField(max_length=45, blank=True)

    def __str__(self):
        return f"{self.account} on {self.device_label or 'unknown device'}"


class PolicyAcceptance(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="policy_acceptances")
    policy_version = models.CharField(max_length=20)
    policies = models.JSONField()
    ip = models.GenericIPAddressField(null=True, blank=True)
    accepted_at = models.DateTimeField(auto_now_add=True)


class AccountAuditEvent(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="audit_events")
    event = models.CharField(max_length=40)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
```

In `config/settings.py`:

1. Add `'apps.accounts',` to `INSTALLED_APPS` directly above `'apps.notifications',`.
2. Directly below the `DEFAULT_FROM_EMAIL = ...` statement, add:

```python
# Subscriber sign-in. The cookie is host-only on the API host and scoped to /v1/.
ACCOUNT_SESSION_COOKIE = "no_session"
POLICY_VERSION = "2026-11-10"
POLICIES = ["terms", "privacy"]
```

- [ ] **Step 4: Generate the migration, run the test and watch it pass**

```bash
uv run python manage.py makemigrations accounts
uv run python manage.py test apps.accounts.tests.test_models
```

Expected: `apps/accounts/migrations/0001_initial.py` creating `Account`, `AccountAuditEvent`, `PolicyAcceptance` and `Session`, then `Ran 3 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/accounts config/settings.py
git commit -m "feat: add the account, session, policy and audit models"
```

---

### Task 3: Email codes

A code is 6 digits, lasts 10 minutes, and dies after 5 wrong guesses. One code per email per minute, 5 per email per hour, 20 per IP per hour. Only an HMAC of the code is kept in the cache.

**Files:**
- Create: `apps/accounts/codes.py`, `apps/accounts/tests/test_codes.py`

- [ ] **Step 1: Write the failing test**

`apps/accounts/tests/test_codes.py`:

```python
from django.core.cache import cache
from django.test import SimpleTestCase

from apps.accounts import codes
from apps.common.errors import ApiError

EMAIL = "ana@example.test"
IP = "203.0.113.9"


def wrong(code):
    return "000000" if code != "000000" else "111111"


class EmailCodeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_issued_code_is_six_digits_and_checks_true(self):
        code = codes.issue_code(EMAIL, IP)

        self.assertRegex(code, r"^\d{6}$")
        self.assertTrue(codes.check_code(EMAIL, code))

    def test_wrong_code_and_unknown_email_check_false(self):
        code = codes.issue_code(EMAIL, IP)

        self.assertFalse(codes.check_code(EMAIL, wrong(code)))
        self.assertFalse(codes.check_code("other@example.test", code))

    def test_code_is_dead_after_five_wrong_guesses(self):
        code = codes.issue_code(EMAIL, IP)

        for _ in range(codes.MAX_ATTEMPTS):
            self.assertFalse(codes.check_code(EMAIL, wrong(code)))

        self.assertFalse(codes.check_code(EMAIL, code))

    def test_cleared_code_no_longer_checks(self):
        code = codes.issue_code(EMAIL, IP)

        codes.clear_code(EMAIL)

        self.assertFalse(codes.check_code(EMAIL, code))

    def test_second_code_within_a_minute_is_refused(self):
        codes.issue_code(EMAIL, IP)

        with self.assertRaises(ApiError) as raised:
            codes.issue_code(EMAIL, IP)

        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.code, "rate_limited")

    def test_sixth_code_for_one_email_in_an_hour_is_refused(self):
        for _ in range(codes.PER_EMAIL_PER_HOUR):
            codes.issue_code(EMAIL, IP)
            cache.delete(codes._key("cooldown", "signin", EMAIL))

        with self.assertRaises(ApiError) as raised:
            codes.issue_code(EMAIL, IP)

        self.assertEqual(raised.exception.status, 429)

    def test_a_code_for_one_purpose_does_not_work_for_another(self):
        code = codes.issue_code(EMAIL, IP, purpose="change:1")

        self.assertFalse(codes.check_code(EMAIL, code))
        self.assertTrue(codes.check_code(EMAIL, code, purpose="change:1"))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_codes`
Expected: FAIL with `ImportError: cannot import name 'codes' from 'apps.accounts'`.

- [ ] **Step 3: Write the implementation**

`apps/accounts/codes.py`:

```python
"""Six-digit email codes, held in the cache with an expiry and an attempt limit."""
import hashlib
import hmac
import secrets

from django.conf import settings
from django.core.cache import cache

from apps.common.errors import ApiError

CODE_TTL = 600  # seconds
MAX_ATTEMPTS = 5
RESEND_SECONDS = 60
PER_EMAIL_PER_HOUR = 5
PER_IP_PER_HOUR = 20
HOUR = 3600


def _key(kind, purpose, value):
    digest = hashlib.sha256(f"{purpose}|{value}".encode()).hexdigest()[:32]
    return f"emailcode:{kind}:{digest}"


def _code_hash(code):
    return hmac.new(settings.SECRET_KEY.encode(), str(code).encode(), hashlib.sha256).hexdigest()


def _count(key, window):
    """Add one to a counter that expires `window` seconds after its first hit."""
    cache.add(key, 0, window)
    try:
        return cache.incr(key)
    except ValueError:  # the key expired between add and incr
        cache.set(key, 1, window)
        return 1


def _rate_limited(message):
    return ApiError("rate_limited", message, status=429)


def issue_code(email, ip, purpose="signin"):
    """Return a new code for this email, replacing any earlier one. Raises ApiError 429."""
    if not cache.add(_key("cooldown", purpose, email), 1, RESEND_SECONDS):
        raise _rate_limited("Wait a minute before asking for another code.")
    if _count(_key("email-hour", purpose, email), HOUR) > PER_EMAIL_PER_HOUR:
        raise _rate_limited("Too many codes requested. Try again later.")
    if _count(_key("ip-hour", "any", ip), HOUR) > PER_IP_PER_HOUR:
        raise _rate_limited("Too many codes requested. Try again later.")
    code = f"{secrets.randbelow(10**6):06d}"
    cache.set(_key("code", purpose, email), _code_hash(code), CODE_TTL)
    cache.delete(_key("attempts", purpose, email))
    return code


def check_code(email, code, purpose="signin"):
    """True when the code is right. Does not consume it; call clear_code() once it is used."""
    stored = cache.get(_key("code", purpose, email))
    if stored is None:
        return False
    if _count(_key("attempts", purpose, email), CODE_TTL) > MAX_ATTEMPTS:
        clear_code(email, purpose)
        return False
    return hmac.compare_digest(stored, _code_hash(code))


def clear_code(email, purpose="signin"):
    cache.delete_many([_key("code", purpose, email), _key("attempts", purpose, email)])
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.accounts.tests.test_codes`
Expected: `Ran 7 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/accounts/codes.py apps/accounts/tests/test_codes.py
git commit -m "feat: add rate-limited email codes"
```

---

### Task 4: Sessions

**Files:**
- Create: `apps/accounts/sessions.py`, `apps/accounts/tests/test_sessions.py`

- [ ] **Step 1: Write the failing test**

`apps/accounts/tests/test_sessions.py`:

```python
import hashlib
from datetime import timedelta

from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts import sessions
from apps.accounts.models import Account, Session

CHROME_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class SessionTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(email="ana@example.test")
        self.request = RequestFactory().post(
            "/", HTTP_USER_AGENT=CHROME_MAC, REMOTE_ADDR="203.0.113.9"
        )

    def test_start_session_stores_only_the_hash_of_the_token(self):
        session, token = sessions.start_session(self.account, self.request)

        self.assertEqual(session.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, [str(value) for value in vars(session).values()])
        self.assertEqual(session.device_label, "Chrome on Mac")
        self.assertEqual(session.masked_ip, "203.0.113.x")
        self.assertAlmostEqual(
            session.expires_at, timezone.now() + timedelta(days=30), delta=timedelta(minutes=1)
        )

    def test_find_session_returns_it_for_the_token_only(self):
        session, token = sessions.start_session(self.account, self.request)

        self.assertEqual(sessions.find_session(token), session)
        self.assertIsNone(sessions.find_session("not-the-token"))
        self.assertIsNone(sessions.find_session(None))

    def test_expired_revoked_and_closed_sessions_are_not_found(self):
        expired, expired_token = sessions.start_session(self.account, self.request)
        Session.objects.filter(pk=expired.pk).update(expires_at=timezone.now())
        revoked, revoked_token = sessions.start_session(self.account, self.request)
        sessions.revoke_session(revoked)

        self.assertIsNone(sessions.find_session(expired_token))
        self.assertIsNone(sessions.find_session(revoked_token))

        _live, live_token = sessions.start_session(self.account, self.request)
        Account.objects.filter(pk=self.account.pk).update(closed_at=timezone.now())

        self.assertIsNone(sessions.find_session(live_token))

    def test_revoke_sessions_can_keep_one(self):
        keep, _ = sessions.start_session(self.account, self.request)
        sessions.start_session(self.account, self.request)
        sessions.start_session(self.account, self.request)

        revoked = sessions.revoke_sessions(self.account, keep=keep)

        self.assertEqual(revoked, 2)
        self.assertEqual(
            list(Session.objects.filter(revoked_at__isnull=True)), [keep]
        )

    def test_cookie_is_httponly_secure_lax_and_scoped_to_v1(self):
        response = HttpResponse()

        sessions.set_session_cookie(response, "token-value")

        cookie = response.cookies["no_session"]
        self.assertEqual(cookie.value, "token-value")
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/v1/")
        self.assertEqual(cookie["domain"], "")
        self.assertEqual(cookie["max-age"], 30 * 24 * 3600)

    def test_ip_masking_and_client_ip(self):
        self.assertEqual(sessions.mask_ip("2001:db8:abcd:12::1"), "2001:0db8:abcd:x")
        self.assertEqual(sessions.mask_ip("garbage"), "")
        proxied = RequestFactory().get("/", HTTP_X_REAL_IP="198.51.100.7", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(sessions.client_ip(proxied), "198.51.100.7")
        forged = RequestFactory().get("/", HTTP_X_REAL_IP="not-an-ip", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(sessions.client_ip(forged), "")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_sessions`
Expected: FAIL with `ImportError: cannot import name 'sessions' from 'apps.accounts'`.

- [ ] **Step 3: Write the implementation**

`apps/accounts/sessions.py`:

```python
"""Subscriber sessions: an opaque cookie token whose SHA-256 is the only thing stored."""
import hashlib
import ipaddress
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import Session

SESSION_LIFETIME = timedelta(days=30)
TOUCH_INTERVAL = timedelta(minutes=10)  # how stale last_used_at may get before it is rewritten
COOKIE_PATH = "/v1/"

# First match wins, so the more specific tokens come first.
BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("CriOS/", "Chrome"),
    ("FxiOS/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Firefox/", "Firefox"),
    ("Safari/", "Safari"),
)
SYSTEMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("Mac OS X", "Mac"),
    ("Linux", "Linux"),
)


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request):
    """The caller's IP as Railway's proxy reports it, or "" when it is not a valid address."""
    value = (request.headers.get("X-Real-IP") or request.META.get("REMOTE_ADDR") or "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def mask_ip(ip):
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if parsed.version == 4:
        return ".".join(str(parsed).split(".")[:3]) + ".x"
    return ":".join(parsed.exploded.split(":")[:3]) + ":x"


def device_label(user_agent):
    user_agent = user_agent or ""
    browser = next((name for token, name in BROWSERS if token in user_agent), "Browser")
    system = next((name for token, name in SYSTEMS if token in user_agent), "unknown device")
    return f"{browser} on {system}"


def start_session(account, request):
    """Create a session and return (session, token). The token is shown once and never stored."""
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    session = Session.objects.create(
        account=account,
        token_hash=_token_hash(token),
        expires_at=now + SESSION_LIFETIME,
        last_used_at=now,
        device_label=device_label(request.headers.get("User-Agent")),
        masked_ip=mask_ip(client_ip(request)),
    )
    return session, token


def find_session(token):
    """The live session for this token, or None. Refreshes last_used_at when it is stale."""
    if not token:
        return None
    now = timezone.now()
    session = (
        Session.objects.select_related("account")
        .filter(
            token_hash=_token_hash(token),
            revoked_at__isnull=True,
            expires_at__gt=now,
            account__closed_at__isnull=True,
        )
        .first()
    )
    if session is not None and now - session.last_used_at > TOUCH_INTERVAL:
        session.last_used_at = now
        session.save(update_fields=["last_used_at"])
    return session


def revoke_session(session):
    Session.objects.filter(pk=session.pk, revoked_at__isnull=True).update(revoked_at=timezone.now())


def revoke_sessions(account, keep=None):
    """Revoke the account's live sessions, except `keep`. Returns how many were revoked."""
    live = Session.objects.filter(account=account, revoked_at__isnull=True)
    if keep is not None:
        live = live.exclude(pk=keep.pk)
    return live.update(revoked_at=timezone.now())


def set_session_cookie(response, token):
    # No domain: the cookie is host-only, so it is never sent to storefront hosts.
    response.set_cookie(
        settings.ACCOUNT_SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path=COOKIE_PATH,
    )


def clear_session_cookie(response):
    response.delete_cookie(settings.ACCOUNT_SESSION_COOKIE, path=COOKIE_PATH, samesite="Lax")
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.accounts.tests.test_sessions`
Expected: `Ran 6 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/accounts/sessions.py apps/accounts/tests/test_sessions.py
git commit -m "feat: add subscriber sessions"
```

---

### Task 5: Account services

**Files:**
- Create: `apps/accounts/services.py`, `apps/accounts/tests/test_services.py`

- [ ] **Step 1: Write the failing test**

`apps/accounts/tests/test_services.py`:

```python
import re

from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Account, AccountAuditEvent, PolicyAcceptance, Session
from apps.accounts.sessions import find_session, start_session
from apps.common.errors import ApiError

EMAIL = "ana@example.test"
IP = "203.0.113.9"


def last_code():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class AccountServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.request = RequestFactory().post("/", REMOTE_ADDR=IP)

    def sign_in(self, email=EMAIL, accept_policies=True):
        cache.clear()
        services.request_signin_code(email, IP)
        return services.verify_signin_code(email, last_code(), accept_policies, self.request)

    def test_request_sends_a_code_and_creates_no_account(self):
        services.request_signin_code("  Ana@Example.TEST ", IP)

        self.assertEqual(mail.outbox[-1].to, [EMAIL])
        self.assertRegex(last_code(), r"^\d{6}$")
        self.assertFalse(Account.objects.exists())

    def test_invalid_email_names_the_field(self):
        with self.assertRaises(ApiError) as raised:
            services.request_signin_code("not-an-email", IP)

        self.assertEqual(raised.exception.code, "invalid_request")
        self.assertIn("email", raised.exception.fields)

    def test_first_verify_creates_the_account_and_records_policy_acceptance(self):
        account, session, token, created = self.sign_in()

        self.assertTrue(created)
        self.assertEqual(account.email, EMAIL)
        self.assertIsNotNone(account.email_verified_at)
        self.assertEqual(find_session(token), session)
        acceptance = PolicyAcceptance.objects.get(account=account)
        self.assertEqual(acceptance.policy_version, "2026-11-10")
        self.assertEqual(acceptance.policies, ["terms", "privacy"])
        self.assertEqual(acceptance.ip, IP)
        self.assertEqual(
            list(AccountAuditEvent.objects.values_list("event", flat=True).order_by("id")),
            ["account_created", "signed_in"],
        )

    def test_later_verify_signs_in_and_revokes_earlier_sessions(self):
        _account, _first, first_token, _ = self.sign_in()

        account, _second, second_token, created = self.sign_in()

        self.assertFalse(created)
        self.assertEqual(Account.objects.count(), 1)
        self.assertIsNone(find_session(first_token))
        self.assertEqual(find_session(second_token).account, account)

    def test_new_account_must_accept_policies_and_the_code_survives(self):
        services.request_signin_code(EMAIL, IP)
        code = last_code()

        with self.assertRaises(ApiError) as raised:
            services.verify_signin_code(EMAIL, code, False, self.request)

        self.assertEqual(raised.exception.code, "policies_required")
        self.assertFalse(Account.objects.exists())
        _account, _session, _token, created = services.verify_signin_code(
            EMAIL, code, True, self.request
        )
        self.assertTrue(created)

    def test_wrong_code_is_refused_and_a_used_code_cannot_be_reused(self):
        services.request_signin_code(EMAIL, IP)
        code = last_code()
        wrong = "000000" if code != "000000" else "111111"

        with self.assertRaises(ApiError) as raised:
            services.verify_signin_code(EMAIL, wrong, True, self.request)
        self.assertEqual(raised.exception.code, "invalid_code")

        services.verify_signin_code(EMAIL, code, True, self.request)
        with self.assertRaises(ApiError):
            services.verify_signin_code(EMAIL, code, True, self.request)

    def test_closed_account_cannot_sign_in(self):
        Account.objects.create(email=EMAIL, closed_at=timezone.now())

        with self.assertRaises(ApiError) as raised:
            self.sign_in()

        self.assertEqual(raised.exception.code, "account_closed")
        self.assertEqual(raised.exception.status, 403)

    def test_email_change_needs_the_code_sent_to_the_new_address(self):
        account, current, current_token, _ = self.sign_in()
        _other, other_token = start_session(account, self.request)
        cache.clear()

        services.start_email_change(account, "New@Example.test", IP)

        self.assertEqual(mail.outbox[-1].to, ["new@example.test"])
        services.confirm_email_change(account, "new@example.test", last_code(), current)
        account.refresh_from_db()
        self.assertEqual(account.email, "new@example.test")
        self.assertIsNotNone(find_session(current_token))
        self.assertIsNone(find_session(other_token))
        event = AccountAuditEvent.objects.get(event="email_changed")
        self.assertEqual(event.detail, {"old": EMAIL, "new": "new@example.test"})

    def test_email_change_to_a_taken_or_unchanged_email_is_refused(self):
        account, _session, _token, _ = self.sign_in()
        Account.objects.create(email="taken@example.test")
        cache.clear()

        with self.assertRaises(ApiError) as taken:
            services.start_email_change(account, "taken@example.test", IP)
        with self.assertRaises(ApiError) as unchanged:
            services.start_email_change(account, EMAIL, IP)

        self.assertEqual((taken.exception.code, taken.exception.status), ("email_taken", 409))
        self.assertEqual(unchanged.exception.code, "email_unchanged")

    def test_update_account_trims_the_name_and_records_it(self):
        account, _session, _token, _ = self.sign_in()

        services.update_account(account, name="  Ana Reyes ")

        account.refresh_from_db()
        self.assertEqual(account.name, "Ana Reyes")
        self.assertTrue(AccountAuditEvent.objects.filter(event="profile_updated").exists())
        self.assertEqual(Session.objects.filter(account=account).count(), 1)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_services`
Expected: FAIL with `ImportError: cannot import name 'services' from 'apps.accounts'`.

- [ ] **Step 3: Write the implementation**

`apps/accounts/services.py`:

```python
"""Account sign-in and settings. Other apps call these functions, never the tables."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.accounts import codes
from apps.accounts.models import Account, AccountAuditEvent, PolicyAcceptance
from apps.accounts.sessions import client_ip, revoke_sessions, start_session
from apps.common.errors import ApiError
from apps.notifications.services import send_email

SIGNIN = "signin"


def normalise_email(email):
    value = (email or "").strip().lower()
    try:
        validate_email(value)
    except ValidationError:
        raise ApiError(
            "invalid_request",
            "Some values are missing or invalid.",
            fields={"email": ["Enter a valid email address."]},
        ) from None
    return value


def record_event(account, event, **detail):
    return AccountAuditEvent.objects.create(account=account, event=event, detail=detail)


def _invalid_code():
    return ApiError("invalid_code", "That code is wrong or has expired.")


def _email_taken():
    return ApiError("email_taken", "That email is already used by another account.", status=409)


def _change_purpose(account):
    return f"change:{account.pk}"


def request_signin_code(email, ip):
    """Email a sign-in code. Behaves the same whether or not the email has an account."""
    email = normalise_email(email)
    code = codes.issue_code(email, ip, SIGNIN)
    send_email(
        to=email,
        category="signin_code",
        subject="Your NegosyoOnline sign-in code",
        body=(
            f"Your sign-in code is {code}. It expires in 10 minutes.\n\n"
            "If you did not ask for this code, you can ignore this email."
        ),
    )


@transaction.atomic
def verify_signin_code(email, code, accept_policies, request):
    """Sign in, creating the account on first use. Returns (account, session, token, created)."""
    email = normalise_email(email)
    if not codes.check_code(email, code, SIGNIN):
        raise _invalid_code()
    account = Account.objects.filter(email=email).first()
    if account is not None and account.closed_at is not None:
        raise ApiError("account_closed", "This account is closed.", status=403)
    created = account is None
    if created and not accept_policies:
        raise ApiError(
            "policies_required", "Accept the terms and privacy policy to create an account."
        )
    codes.clear_code(email, SIGNIN)
    now = timezone.now()
    if created:
        account = Account.objects.create(email=email, email_verified_at=now)
        PolicyAcceptance.objects.create(
            account=account,
            policy_version=settings.POLICY_VERSION,
            policies=list(settings.POLICIES),
            ip=client_ip(request) or None,
        )
        record_event(account, "account_created", method="email_code")
    else:
        if account.email_verified_at is None:
            account.email_verified_at = now
            account.save(update_fields=["email_verified_at"])
        revoke_sessions(account)
    session, token = start_session(account, request)
    record_event(account, "signed_in", method="email_code", session=session.public_id)
    return account, session, token, created


def update_account(account, *, name):
    account.name = name.strip()
    account.save(update_fields=["name"])
    record_event(account, "profile_updated")
    return account


def start_email_change(account, new_email, ip):
    """Email a code to the new address. The email changes only when that code is confirmed."""
    new_email = normalise_email(new_email)
    if new_email == account.email:
        raise ApiError("email_unchanged", "That is already your email.")
    if Account.objects.filter(email=new_email).exists():
        raise _email_taken()
    code = codes.issue_code(new_email, ip, _change_purpose(account))
    send_email(
        to=new_email,
        category="email_change_code",
        subject="Confirm your new NegosyoOnline email",
        body=(
            f"Your confirmation code is {code}. It expires in 10 minutes.\n\n"
            "If you did not ask to use this email for NegosyoOnline, you can ignore this email."
        ),
    )


@transaction.atomic
def confirm_email_change(account, new_email, code, current_session):
    new_email = normalise_email(new_email)
    purpose = _change_purpose(account)
    if not codes.check_code(new_email, code, purpose):
        raise _invalid_code()
    if Account.objects.filter(email=new_email).exclude(pk=account.pk).exists():
        raise _email_taken()
    codes.clear_code(new_email, purpose)
    old_email = account.email
    account.email = new_email
    account.email_verified_at = timezone.now()
    account.save(update_fields=["email", "email_verified_at"])
    revoke_sessions(account, keep=current_session)
    record_event(account, "email_changed", old=old_email, new=new_email)
    return account
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.accounts.tests.test_services`
Expected: `Ran 10 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/accounts/services.py apps/accounts/tests/test_services.py
git commit -m "feat: add account sign-in and settings services"
```

---

### Task 6: Admin pages, PR A, and the staging migration

**Files:**
- Create: `apps/accounts/admin.py`, `apps/notifications/admin.py`

- [ ] **Step 1: Register the models in the admin**

`apps/accounts/admin.py`:

```python
from django.contrib import admin

from apps.accounts.models import Account, AccountAuditEvent, PolicyAcceptance, Session


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("email", "public_id", "name", "email_verified_at", "created_at", "closed_at")
    search_fields = ("email", "public_id", "name")
    readonly_fields = ("public_id", "referral_code", "created_at")


@admin.register(Session)
class SessionAdmin(ReadOnlyAdmin):
    list_display = ("account", "device_label", "masked_ip", "last_used_at", "expires_at", "revoked_at")
    exclude = ("token_hash",)


@admin.register(PolicyAcceptance)
class PolicyAcceptanceAdmin(ReadOnlyAdmin):
    list_display = ("account", "policy_version", "accepted_at")


@admin.register(AccountAuditEvent)
class AccountAuditEventAdmin(ReadOnlyAdmin):
    list_display = ("account", "event", "created_at")
    list_filter = ("event",)
```

`apps/notifications/admin.py`:

```python
from django.contrib import admin

from apps.notifications.models import EmailMessage


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ("recipient", "category", "status", "created_at", "sent_at")
    list_filter = ("category", "status")
    search_fields = ("recipient",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
```

- [ ] **Step 2: Run every check CI runs**

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
DJANGO_DEBUG=false DJANGO_SECRET_KEY=local-check-only-long-random-value-0123456789abcdef uv run python manage.py check --deploy
```

Expected: `No changes detected`, `OK` (54 tests), and only the two deferred HSTS warnings (W005, W021) from `check --deploy`.

- [ ] **Step 3: Commit**

```bash
git add apps/accounts/admin.py apps/notifications/admin.py
git commit -m "feat: show accounts and email records in the admin"
```

- [ ] **Step 4: Open PR A**

Push and open the pull request "feat: accounts, sessions and email codes (no endpoints)". Say in the description that it adds two migration files (`accounts.0001_initial`, `notifications.0001_initial`) that are not applied by the deploy, and that nothing in the running service reads the new tables yet. Enable auto-merge.

- [ ] **Step 5: Verify the deploy**

Note the newest staging deployment id before the merge, then:

```bash
scripts/wait_railway_deploy.sh <before-deployment-id>
scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <merge-commit-prefix>
```

Expected: `SUCCESS ...` and `OK:` with the merge commit.

- [ ] **Step 6: APPROVAL. Apply the migrations on staging**

Follow `docs/runbooks/migrations.md`: show the plan, read it, apply, confirm nothing is pending.

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- 'uv run python manage.py migrate --plan'
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- 'uv run python manage.py migrate --noinput'
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- 'uv run python manage.py migrate --plan'
```

Expected: the first plan lists exactly `accounts.0001_initial` and `notifications.0001_initial`. If it lists anything else, stop and tell Coach Ed. The last command prints `No planned migration operations.`

---

### Task 7: Sign-in endpoints

Endpoints: `POST /v1/auth/email-code/request/`, `POST /v1/auth/email-code/verify/`, `POST /v1/auth/logout/`, `GET /v1/me/`.

Every state-changing request to these endpoints must carry an `Origin` from `API_ALLOWED_ORIGINS`. The middleware from slice 1 already refuses a wrong `Origin`; `require_allowed_origin()` also refuses a missing one.

**Files:**
- Create: `apps/accounts/auth.py`, `apps/accounts/api.py`, `apps/accounts/tests/base.py`, `apps/accounts/tests/test_api_auth.py`, `apps/common/tests/test_origin.py`
- Modify: `apps/common/middleware.py`, `config/api.py`

- [ ] **Step 1: Create the branch and write the failing tests**

```bash
git fetch origin && git checkout -b feat/accounts-api origin/main
```

`apps/common/tests/test_origin.py`:

```python
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.common.errors import ApiError
from apps.common.middleware import require_allowed_origin

DASH = "https://dash.example.test"


@override_settings(API_ALLOWED_ORIGINS=[DASH])
class RequireAllowedOriginTests(SimpleTestCase):
    def test_reads_pass_without_an_origin(self):
        require_allowed_origin(RequestFactory().get("/v1/me/"))

    def test_write_from_an_allowed_origin_passes(self):
        require_allowed_origin(RequestFactory().post("/v1/auth/logout/", HTTP_ORIGIN=DASH))

    def test_write_without_or_with_another_origin_is_refused(self):
        for extra in ({}, {"HTTP_ORIGIN": "https://shop1.example.test"}):
            with self.assertRaises(ApiError) as raised:
                require_allowed_origin(RequestFactory().post("/v1/auth/logout/", **extra))
            self.assertEqual(raised.exception.code, "origin_required")
            self.assertEqual(raised.exception.status, 403)
```

`apps/accounts/tests/base.py`:

```python
import json
import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

ORIGIN = "https://dash.example.test"
EMAIL = "ana@example.test"


@override_settings(API_ALLOWED_ORIGINS=[ORIGIN])
class ApiTestCase(TestCase):
    def setUp(self):
        cache.clear()

    def send(self, method, path, data=None, client=None, **extra):
        extra.setdefault("HTTP_ORIGIN", ORIGIN)
        if extra["HTTP_ORIGIN"] is None:
            del extra["HTTP_ORIGIN"]
        call = getattr(client or self.client, method)
        return call(
            path, data=json.dumps(data or {}), content_type="application/json", secure=True, **extra
        )

    def post(self, path, data=None, **extra):
        return self.send("post", path, data, **extra)

    def get(self, path, client=None):
        return (client or self.client).get(path, secure=True)

    def last_code(self):
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def sign_in(self, email=EMAIL, client=None):
        cache.clear()
        self.post("/v1/auth/email-code/request/", {"email": email}, client=client)
        return self.post(
            "/v1/auth/email-code/verify/",
            {"email": email, "code": self.last_code(), "accept_policies": True},
            client=client,
        )
```

`apps/accounts/tests/test_api_auth.py`:

```python
from django.core import mail
from django.test import Client

from apps.accounts.models import Account, PolicyAcceptance
from apps.accounts.tests.base import EMAIL, ApiTestCase


class SignInApiTests(ApiTestCase):
    def test_request_sends_a_code_and_answers_204(self):
        response = self.post("/v1/auth/email-code/request/", {"email": EMAIL})

        self.assertEqual(response.status_code, 204)
        self.assertEqual(mail.outbox[-1].to, [EMAIL])
        self.assertFalse(Account.objects.exists())

    def test_invalid_email_answers_400_with_the_field(self):
        response = self.post("/v1/auth/email-code/request/", {"email": "nope"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["error"]["fields"])

    def test_verify_creates_the_account_and_sets_the_cookie(self):
        response = self.sign_in()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["created"])
        self.assertEqual(body["account"]["email"], EMAIL)
        self.assertTrue(body["account"]["id"].startswith("acc_"))
        self.assertNotIn("referral_code", body["account"])
        cookie = response.cookies["no_session"]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertTrue(PolicyAcceptance.objects.filter(account__email=EMAIL).exists())

    def test_me_returns_the_signed_in_account(self):
        self.sign_in()

        response = self.get("/v1/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account"]["email"], EMAIL)

    def test_me_without_a_session_is_401(self):
        response = self.get("/v1/me/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthenticated")

    def test_new_account_without_policies_is_refused_then_succeeds_with_the_same_code(self):
        self.post("/v1/auth/email-code/request/", {"email": EMAIL})
        payload = {"email": EMAIL, "code": self.last_code()}

        refused = self.post("/v1/auth/email-code/verify/", payload)
        accepted = self.post("/v1/auth/email-code/verify/", {**payload, "accept_policies": True})

        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["error"]["code"], "policies_required")
        self.assertNotIn("no_session", refused.cookies)
        self.assertEqual(accepted.status_code, 200)

    def test_wrong_code_is_400(self):
        self.post("/v1/auth/email-code/request/", {"email": EMAIL})
        wrong = "000000" if self.last_code() != "000000" else "111111"

        response = self.post(
            "/v1/auth/email-code/verify/", {"email": EMAIL, "code": wrong, "accept_policies": True}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_code")

    def test_second_code_within_a_minute_is_429(self):
        self.post("/v1/auth/email-code/request/", {"email": EMAIL})

        response = self.post("/v1/auth/email-code/request/", {"email": EMAIL})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "rate_limited")

    def test_signing_in_again_signs_out_the_earlier_session(self):
        self.sign_in()
        other_device = Client()

        self.sign_in(client=other_device)

        self.assertEqual(self.get("/v1/me/").status_code, 401)
        self.assertEqual(self.get("/v1/me/", client=other_device).status_code, 200)

    def test_logout_revokes_the_session_and_clears_the_cookie(self):
        self.sign_in()

        response = self.post("/v1/auth/logout/")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.cookies["no_session"].value, "")
        self.assertEqual(self.get("/v1/me/").status_code, 401)

    def test_writes_without_an_origin_are_403(self):
        request_code = self.post(
            "/v1/auth/email-code/request/", {"email": EMAIL}, HTTP_ORIGIN=None
        )
        self.sign_in()
        logout = self.post("/v1/auth/logout/", HTTP_ORIGIN=None)

        self.assertEqual(request_code.status_code, 403)
        self.assertEqual(request_code.json()["error"]["code"], "origin_required")
        self.assertEqual(logout.status_code, 403)
        self.assertEqual(self.get("/v1/me/").status_code, 200)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run python manage.py test apps.common.tests.test_origin apps.accounts.tests.test_api_auth`
Expected: `test_origin` FAILS with `ImportError: cannot import name 'require_allowed_origin'`; the API tests FAIL with 404 responses.

- [ ] **Step 3: Write the implementation**

In `apps/common/middleware.py`, change the errors import to:

```python
from apps.common.errors import ApiError, error_body
```

and add this function directly above `class ApiOriginMiddleware:`:

```python
def require_allowed_origin(request):
    """Sign-in and cookie-backed endpoints call this: a write must name an allowed Origin."""
    if request.method in SAFE_METHODS:
        return
    if request.headers.get("Origin") not in settings.API_ALLOWED_ORIGINS:
        raise ApiError(
            "origin_required", "This request must come from the site or the dashboard.", status=403
        )
```

`apps/accounts/auth.py`:

```python
"""Authentication for signed-in subscriber endpoints: the session cookie."""
from django.conf import settings
from ninja.security import APIKeyCookie

from apps.accounts.sessions import find_session
from apps.common.middleware import require_allowed_origin


class SessionCookieAuth(APIKeyCookie):
    param_name = settings.ACCOUNT_SESSION_COOKIE

    def __init__(self):
        # Django's CSRF token is not used: the Origin allowlist plus SameSite=Lax replace it.
        super().__init__(csrf=False)

    def authenticate(self, request, key):
        require_allowed_origin(request)
        session = find_session(key)
        if session is None:
            return None
        request.account = session.account
        request.account_session = session
        return session.account
```

`apps/accounts/api.py`:

```python
"""Sign-in and account endpoints. Shapes here are the contract the frontend builds against."""
from datetime import datetime

from django.http import HttpResponse
from ninja import Router, Schema

from apps.accounts import services
from apps.accounts.auth import SessionCookieAuth
from apps.accounts.sessions import (
    clear_session_cookie,
    client_ip,
    revoke_session,
    set_session_cookie,
)
from apps.common.middleware import require_allowed_origin

router = Router(tags=["accounts"])
signed_in = SessionCookieAuth()


class EmailIn(Schema):
    email: str


class VerifyIn(Schema):
    email: str
    code: str
    accept_policies: bool = False


class AccountOut(Schema):
    id: str
    email: str
    name: str
    email_verified: bool
    created_at: datetime


class VerifyOut(Schema):
    account: AccountOut
    created: bool


class MeOut(Schema):
    account: AccountOut


def account_out(account):
    return {
        "id": account.public_id,
        "email": account.email,
        "name": account.name,
        "email_verified": account.email_verified_at is not None,
        "created_at": account.created_at,
    }


@router.post("/auth/email-code/request/", response={204: None})
def email_code_request(request, payload: EmailIn):
    require_allowed_origin(request)
    services.request_signin_code(payload.email, client_ip(request))
    return 204, None


@router.post("/auth/email-code/verify/", response=VerifyOut)
def email_code_verify(request, response: HttpResponse, payload: VerifyIn):
    require_allowed_origin(request)
    account, _session, token, created = services.verify_signin_code(
        payload.email, payload.code, payload.accept_policies, request
    )
    set_session_cookie(response, token)
    return {"account": account_out(account), "created": created}


@router.post("/auth/logout/", response={204: None}, auth=signed_in)
def logout(request, response: HttpResponse):
    revoke_session(request.account_session)
    clear_session_cookie(response)
    return 204, None


@router.get("/me/", response=MeOut, auth=signed_in)
def me(request):
    return {"account": account_out(request.account)}
```

In `config/api.py`, add the import below the `apps.common.errors` import:

```python
from apps.accounts.api import router as accounts_router
```

and add this line directly below `register_error_handlers(api)`:

```python
api.add_router("", accounts_router)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run python manage.py test apps.common.tests.test_origin apps.accounts.tests.test_api_auth`
Expected: `Ran 14 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/common/middleware.py apps/common/tests/test_origin.py apps/accounts/auth.py apps/accounts/api.py apps/accounts/tests/base.py apps/accounts/tests/test_api_auth.py config/api.py
git commit -m "feat: add email-code sign-in, logout and me endpoints"
```

---

### Task 8: Account endpoints

Endpoints: `GET PATCH /v1/account/`, `POST /v1/account/email/start/`, `POST /v1/account/email/confirm/`, `GET /v1/account/sessions/`, `POST /v1/account/sessions/sign-out-others/`.

**Files:**
- Create: `apps/accounts/tests/test_api_account.py`
- Modify: `apps/accounts/api.py`

- [ ] **Step 1: Write the failing test**

`apps/accounts/tests/test_api_account.py`:

```python
from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory

from apps.accounts.models import Account
from apps.accounts.sessions import start_session
from apps.accounts.tests.base import EMAIL, ApiTestCase


class AccountApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in()
        self.account = Account.objects.get(email=EMAIL)

    def test_every_account_endpoint_needs_a_session(self):
        self.client.cookies.clear()

        self.assertEqual(self.get("/v1/account/").status_code, 401)
        self.assertEqual(self.get("/v1/account/sessions/").status_code, 401)
        self.assertEqual(self.send("patch", "/v1/account/", {"name": "x"}).status_code, 401)
        self.assertEqual(self.post("/v1/account/email/start/", {"email": "n@example.test"}).status_code, 401)
        self.assertEqual(self.post("/v1/account/sessions/sign-out-others/").status_code, 401)

    def test_get_and_patch_account(self):
        patched = self.send("patch", "/v1/account/", {"name": "  Ana Reyes "})
        fetched = self.get("/v1/account/")

        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["name"], "Ana Reyes")
        self.assertEqual(fetched.json()["name"], "Ana Reyes")
        self.assertEqual(fetched.json()["email"], EMAIL)

    def test_name_longer_than_120_characters_is_400(self):
        response = self.send("patch", "/v1/account/", {"name": "x" * 121})

        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json()["error"]["fields"])

    def test_email_change_start_then_confirm(self):
        cache.clear()

        started = self.post("/v1/account/email/start/", {"email": "new@example.test"})
        self.assertEqual(started.status_code, 204)
        self.assertEqual(mail.outbox[-1].to, ["new@example.test"])

        confirmed = self.post(
            "/v1/account/email/confirm/", {"email": "new@example.test", "code": self.last_code()}
        )

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["email"], "new@example.test")
        self.assertEqual(self.get("/v1/me/").json()["account"]["email"], "new@example.test")

    def test_email_change_to_a_taken_email_is_409(self):
        Account.objects.create(email="taken@example.test")
        cache.clear()

        response = self.post("/v1/account/email/start/", {"email": "taken@example.test"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "email_taken")

    def test_sessions_list_shows_only_my_live_sessions_and_marks_the_current_one(self):
        factory = RequestFactory()
        start_session(self.account, factory.post("/", HTTP_USER_AGENT="Firefox/130 Windows"))
        stranger = Account.objects.create(email="stranger@example.test")
        start_session(stranger, factory.post("/"))

        body = self.get("/v1/account/sessions/").json()

        self.assertEqual(len(body["items"]), 2)
        self.assertIsNone(body["next_cursor"])
        self.assertEqual([item["current"] for item in body["items"]].count(True), 1)
        self.assertIn("Firefox on Windows", [item["device_label"] for item in body["items"]])
        self.assertTrue(all(item["id"].startswith("ses_") for item in body["items"]))
        self.assertNotIn("token_hash", body["items"][0])

    def test_sign_out_others_keeps_the_current_session(self):
        start_session(self.account, RequestFactory().post("/"))
        start_session(self.account, RequestFactory().post("/"))

        response = self.post("/v1/account/sessions/sign-out-others/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"revoked": 2})
        self.assertEqual(len(self.get("/v1/account/sessions/").json()["items"]), 1)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.accounts.tests.test_api_account`
Expected: FAIL: the requests answer 404 because the endpoints do not exist yet.

- [ ] **Step 3: Write the implementation**

In `apps/accounts/api.py`:

1. Change the Ninja import to `from ninja import Field, Router, Schema`.
2. Add `from apps.accounts.models import Session` below the `apps.accounts.auth` import, and add `revoke_sessions,` to the `apps.accounts.sessions` import list (between `revoke_session,` and `set_session_cookie,`).
3. Add these schemas directly below `class MeOut`:

```python
class AccountPatch(Schema):
    name: str = Field(max_length=120)


class EmailConfirmIn(Schema):
    email: str
    code: str


class SessionOut(Schema):
    id: str
    device_label: str
    masked_ip: str
    created_at: datetime
    last_used_at: datetime
    current: bool


class SessionListOut(Schema):
    items: list[SessionOut]
    next_cursor: str | None = None


class RevokedOut(Schema):
    revoked: int
```

4. Add these endpoints at the end of the file:

```python
@router.get("/account/", response=AccountOut, auth=signed_in)
def account_detail(request):
    return account_out(request.account)


@router.patch("/account/", response=AccountOut, auth=signed_in)
def account_update(request, payload: AccountPatch):
    return account_out(services.update_account(request.account, name=payload.name))


@router.post("/account/email/start/", response={204: None}, auth=signed_in)
def email_change_start(request, payload: EmailIn):
    services.start_email_change(request.account, payload.email, client_ip(request))
    return 204, None


@router.post("/account/email/confirm/", response=AccountOut, auth=signed_in)
def email_change_confirm(request, payload: EmailConfirmIn):
    account = services.confirm_email_change(
        request.account, payload.email, payload.code, request.account_session
    )
    return account_out(account)


@router.get("/account/sessions/", response=SessionListOut, auth=signed_in)
def session_list(request):
    live = Session.objects.filter(
        account=request.account, revoked_at__isnull=True
    ).order_by("-last_used_at", "-id")
    return {
        "items": [
            {
                "id": session.public_id,
                "device_label": session.device_label,
                "masked_ip": session.masked_ip,
                "created_at": session.created_at,
                "last_used_at": session.last_used_at,
                "current": session.pk == request.account_session.pk,
            }
            for session in live
        ],
        "next_cursor": None,
    }


@router.post("/account/sessions/sign-out-others/", response=RevokedOut, auth=signed_in)
def sign_out_others(request):
    return {"revoked": revoke_sessions(request.account, keep=request.account_session)}
```

A subscriber has few sessions, so the list is returned whole; `next_cursor` is always `null` and exists so the shape matches the cursor lists that arrive in slice 3.

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.accounts.tests.test_api_account`
Expected: `Ran 7 tests` and `OK`.

- [ ] **Step 5: Run every check CI runs, then commit**

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
DJANGO_DEBUG=false DJANGO_SECRET_KEY=local-check-only-long-random-value-0123456789abcdef uv run python manage.py check --deploy
```

Expected: `No changes detected`, `OK` (75 tests), and only the two deferred HSTS warnings.

```bash
git add apps/accounts/api.py apps/accounts/tests/test_api_account.py
git commit -m "feat: add account, email change and session endpoints"
```

---

### Task 9: PR B, staging verification, and the record

**Files:**
- Modify: `README.md`, `AGENTS.md`, `docs/runbooks/migrations.md`

- [ ] **Step 1: Update the documents**

Add to the Layout list in `README.md`:

```markdown
- `apps/accounts`: subscribers (`Account`), their sessions, email-code sign-in and account settings. Other apps call `apps/accounts/services.py`, never the tables.
- `apps/notifications`: `send_email()` is the only way to send email. With no `EMAIL_HOST` set, email is printed to the service log.
```

Add to the Safety list in `AGENTS.md`:

```markdown
- Signed-in endpoints use `auth=signed_in` from `apps/accounts/api.py` (the `SessionCookieAuth` class). Never read the session cookie by hand.
- Unauthenticated endpoints that change state call `require_allowed_origin(request)` first.
- Never store or log a sign-in code, a session token, or an email body.
```

Append to the History list in `docs/runbooks/migrations.md` (use the date Task 6 Step 6 was run):

```markdown
- <date of the run>: `accounts.0001_initial` and `notifications.0001_initial` were applied to `staging`. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
```

```bash
git add README.md AGENTS.md docs/runbooks/migrations.md
git commit -m "docs: record the accounts layout and the staging migration"
```

- [ ] **Step 2: Open PR B**

Confirm Task 6 Step 6 has been done: the endpoints fail on staging without the tables. Push and open the pull request "feat: email-code sign-in and account endpoints". Enable auto-merge.

- [ ] **Step 3: Verify the deploy on staging**

```bash
scripts/wait_railway_deploy.sh <before-deployment-id>
scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <merge-commit-prefix>
curl -s https://api-staging.negosyonline.com/v1/openapi.json | python3 -c 'import json,sys; print(sorted(json.load(sys.stdin)["paths"]))'
curl -s -o /dev/null -w "%{http_code}\n" https://api-staging.negosyonline.com/v1/me/
curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Content-Type: application/json" -d '{"email":"nobody@example.com"}' https://api-staging.negosyonline.com/v1/auth/email-code/request/
```

Expected: `OK:` with the merge commit; the path list contains `/v1/me/`, `/v1/account/`, `/v1/account/email/confirm/`, `/v1/account/email/start/`, `/v1/account/sessions/`, `/v1/account/sessions/sign-out-others/`, `/v1/auth/email-code/request/`, `/v1/auth/email-code/verify/` and `/v1/auth/logout/`; then `401`; then `403` (no `Origin`, so no email is sent).

- [ ] **Step 4: APPROVAL. Resend email variables on staging**

The provider is Resend (see Decisions). Before this step, the sending domain must be verified in Resend and a Resend API key created for NegosyoOnline. With Coach Ed's approval, these variables are set on the staging `api` service (it redeploys): `EMAIL_HOST=smtp.resend.com`, `EMAIL_PORT=587`, `EMAIL_HOST_USER=resend`, `EMAIL_HOST_PASSWORD` (the API key) and `DEFAULT_FROM_EMAIL`. The API key is entered by Coach Ed in Railway, never pasted into a chat or a file. If the domain or key is not ready, skip this step: sign-in emails are printed to the Railway log.

- [ ] **Step 5: APPROVAL. One real sign-in on staging**

With Coach Ed's approval, and using an email address he names, request a code from the dashboard origin, read it from his inbox (or from the Railway log if Step 4 was skipped), and verify it:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Origin: https://dash-staging.negosyonline.com" -H "Content-Type: application/json" -d '{"email":"<address Coach Ed names>"}' https://api-staging.negosyonline.com/v1/auth/email-code/request/
curl -s -c cookies.txt -X POST -H "Origin: https://dash-staging.negosyonline.com" -H "Content-Type: application/json" -d '{"email":"<same address>","code":"<the code>","accept_policies":true}' https://api-staging.negosyonline.com/v1/auth/email-code/verify/
curl -s -b cookies.txt https://api-staging.negosyonline.com/v1/me/
rm cookies.txt
```

Expected: `204`; a JSON body with `"created": true`; then the same account from `/v1/me/`. This creates one real account on staging.

---

## Done when

- `uv run python manage.py test` passes locally and in CI (75 tests).
- Staging has `accounts.0001_initial` and `notifications.0001_initial` applied and `migrate --plan` reports nothing pending.
- Staging publishes the nine new paths in `/v1/openapi.json`, answers `401` on `/v1/me/` without a session, and `403` on a write without an `Origin`.
- The frontend session has been told the sign-in contract is live at `https://api-staging.negosyonline.com/v1/openapi.json`.

## Self-review against the spec

- Section 4, "A subscriber is an `Account`: unique email, no password. It is not a Django user": Task 2.
- Section 4, email code (6 digits, cache, expiry, attempt limit, rate limits per email and per IP): Task 3.
- Section 4, session (opaque token, SHA-256 only, 30 days, revocation, device label, masked IP; cookie `HttpOnly`, `Secure`, `SameSite=Lax`, host-only): Task 4. The cookie is also scoped to `/v1/`.
- Section 4, "Every state-changing request must carry an `Origin` in that same allowlist": Task 7 (`require_allowed_origin`, called by the sign-in endpoints and by `SessionCookieAuth`).
- Section 4, "Verifying an email by code revokes that account's earlier sessions": Task 5 (`verify_signin_code`) and, for an email change, `confirm_email_change`, which keeps only the session that made the change.
- Section 6: `Account`, `Session`, `PolicyAcceptance`, `AccountAuditEvent` (Task 2) and `EmailMessage` (Task 1). `SmsMessage` arrives with slice 8.
- Section 8: `auth/email-code/request/`, `auth/email-code/verify/`, `auth/logout/`, `me/` (Task 7); `account/`, `account/email/start/`, `account/email/confirm/`, `account/sessions/`, `account/sessions/sign-out-others/` (Task 8). All errors use the one shape through `ApiError`.
- Section 3, "An app talks to another app only through that app's service functions": accounts sends email only through `apps.notifications.services.send_email`.
- Not in this plan by design: Google and `ExternalIdentity` (2b), Facebook (2c), mobile OTP (slice 8), `account/referrals/` (slice 7). `Account.referral_code` and `Account.referred_by` exist now so slice 7 needs no change to the account table; nothing reads them yet.
- Section 3 says email sending runs in a worker. The worker arrives in slice 4; until then `send_email()` sends during the request with a 10-second timeout. Moving it to the worker later changes only `send_email()`.
