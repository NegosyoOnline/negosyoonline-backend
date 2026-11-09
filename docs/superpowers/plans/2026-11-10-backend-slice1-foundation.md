# Backend Slice 1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the backend the shared base every later slice builds on: a `/v1/` API with a published OpenAPI description, one error shape, opaque public ids, an origin allowlist, and a custom operator user model.

**Architecture:** A new `apps/common` package holds code with no business meaning (ids, error shape, origin middleware). `config/api.py` owns the single Django Ninja API mounted at `/v1/`. `apps/operators` holds the custom staff `User`. No subscriber-facing models are created in this slice.

**Tech Stack:** Python 3.12, Django 5.2, Django Ninja 1.x, `uv`, Django's test runner (`manage.py test`), Railway staging.

**Spec:** `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`, sections 3, 4 (operator user and origin rules only), 5 (public ids only), 8 (error shape only) and 9 item 1.

## Rules for whoever executes this

- Read `CLAUDE.md` first. Work on feature branches; `main` is protected; merge through pull requests with CI green.
- Two steps change staging state and need Coach Ed's explicit approval at the time: Task 5 Step 7 (a Railway variable) and Task 6 Steps 6 to 9 (unapplying and re-applying migrations on staging). Do not run them on standing permission.
- Never print secrets. Never touch `production`.
- All commands run from the repository root with `uv run`.
- Tests use Django's runner. Existing tests pass `secure=True` to the test client; do the same.

## File structure

| File | Responsibility |
|---|---|
| `apps/common/__init__.py`, `apps/common/apps.py` | Package and app config. No models. |
| `apps/common/ids.py` | Create and validate opaque public ids. |
| `apps/common/errors.py` | `ApiError`, `error_body()`, and `register_error_handlers(api)`. |
| `apps/common/middleware.py` | `ApiOriginMiddleware`: CORS for allowed origins, refusal of unsafe requests from other origins. |
| `apps/common/tests/` | One test module per file above. |
| `config/api.py` | The one `NinjaAPI` instance and the JSON 404 for unknown `/v1/` paths. |
| `config/urls.py` | Mounts the API. |
| `config/settings.py` | `API_ALLOWED_ORIGINS`, middleware, installed apps, `AUTH_USER_MODEL`. |
| `apps/operators/` | Custom staff `User`, its admin registration, its first migration, its tests. |

Two pull requests: PR A is Tasks 1 to 5 (no database change). PR B is Task 6 (user model and the staging database reset). Task 7 is documentation in PR B.

---

### Task 1: Add Django Ninja

**Files:**
- Modify: `pyproject.toml`, `uv.lock`

- [ ] **Step 1: Create the branch**

```bash
git fetch origin && git checkout -b feat/foundation-api origin/main
```

- [ ] **Step 2: Add the dependency**

```bash
uv add "django-ninja>=1.3,<2"
```

Expected: `pyproject.toml` gains the `django-ninja` line and `uv.lock` changes.

- [ ] **Step 3: Confirm nothing broke**

Run: `uv run python manage.py check && uv run python manage.py test`
Expected: `System check identified no issues` and `OK` (3 tests).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add django-ninja"
```

---

### Task 2: Public ids

**Files:**
- Create: `apps/common/__init__.py` (empty), `apps/common/apps.py`, `apps/common/ids.py`
- Create: `apps/common/tests/__init__.py` (empty), `apps/common/tests/test_ids.py`
- Modify: `config/settings.py` (`INSTALLED_APPS`)

- [ ] **Step 1: Write the failing test**

`apps/common/tests/test_ids.py`:

```python
from django.test import SimpleTestCase

from apps.common.ids import is_public_id, new_public_id


class PublicIdTests(SimpleTestCase):
    def test_new_id_has_prefix_and_twenty_random_characters(self):
        value = new_public_id("site")

        self.assertTrue(value.startswith("site_"))
        self.assertEqual(len(value), len("site_") + 20)

    def test_new_ids_are_different_each_time(self):
        self.assertNotEqual(new_public_id("acc"), new_public_id("acc"))

    def test_is_public_id_accepts_its_own_output(self):
        self.assertTrue(is_public_id(new_public_id("acc"), "acc"))

    def test_is_public_id_rejects_wrong_prefix_and_bad_shapes(self):
        value = new_public_id("acc")

        self.assertFalse(is_public_id(value, "site"))
        self.assertFalse(is_public_id("acc_short", "acc"))
        self.assertFalse(is_public_id(value.upper(), "acc"))
        self.assertFalse(is_public_id("42", "acc"))

    def test_prefix_must_be_lowercase_letters(self):
        with self.assertRaises(ValueError):
            new_public_id("Site_1")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.common.tests.test_ids`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.common.ids'`.

- [ ] **Step 3: Write the implementation**

`apps/common/apps.py`:

```python
from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
```

`apps/common/ids.py`:

```python
"""Opaque public ids such as ``site_3k9x...``. Database primary keys never leave the API."""
import re
import secrets

# Crockford base32, lowercase: no i, l, o or u, so ids are hard to misread.
ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
RANDOM_LENGTH = 20  # 20 characters of 5 bits each: 100 bits
_PREFIX = re.compile(r"^[a-z]{2,8}$")


def new_public_id(prefix: str) -> str:
    if not _PREFIX.match(prefix):
        raise ValueError("prefix must be 2 to 8 lowercase letters")
    return prefix + "_" + "".join(secrets.choice(ALPHABET) for _ in range(RANDOM_LENGTH))


def is_public_id(value: str, prefix: str) -> bool:
    pattern = rf"^{re.escape(prefix)}_[{ALPHABET}]{{{RANDOM_LENGTH}}}$"
    return isinstance(value, str) and re.match(pattern, value) is not None
```

In `config/settings.py`, add `'apps.common',` to `INSTALLED_APPS` directly above `'apps.health',`.

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.common.tests.test_ids`
Expected: `Ran 5 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/common config/settings.py
git commit -m "feat: add opaque public ids"
```

---

### Task 3: One error shape

Every API error is `{"error": {"code": "...", "message": "...", "fields": {...}}}`. `fields` is present only for validation errors.

**Files:**
- Create: `apps/common/errors.py`, `apps/common/tests/test_errors.py`

- [ ] **Step 1: Write the failing test**

`apps/common/tests/test_errors.py`:

```python
from django.http import Http404
from django.test import SimpleTestCase
from ninja import NinjaAPI
from ninja.errors import HttpError
from ninja.testing import TestClient

from apps.common.errors import ApiError, error_body, register_error_handlers

# One module-level API with its own namespace: Ninja refuses two APIs that share one.
api = NinjaAPI(urls_namespace="test-errors")
register_error_handlers(api)


@api.get("/api-error")
def raise_api_error(request):
    raise ApiError("subdomain_taken", "That subdomain is taken.", status=409)


@api.get("/needs-int")
def needs_int(request, limit: int):
    return {"limit": limit}


@api.get("/missing")
def missing(request):
    raise Http404()


@api.get("/too-many")
def too_many(request):
    raise HttpError(429, "Slow down.")


@api.get("/crash")
def crash(request):
    raise RuntimeError("secret internal detail")


class ErrorShapeTests(SimpleTestCase):
    def setUp(self):
        self.client = TestClient(api)

    def test_error_body_omits_fields_when_there_are_none(self):
        self.assertEqual(
            error_body("x", "y"), {"error": {"code": "x", "message": "y"}}
        )

    def test_api_error_uses_its_code_message_and_status(self):
        response = self.client.get("/api-error")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"error": {"code": "subdomain_taken", "message": "That subdomain is taken."}},
        )

    def test_validation_error_is_400_and_names_the_field(self):
        response = self.client.get("/needs-int?limit=abc")

        self.assertEqual(response.status_code, 400)
        body = response.json()["error"]
        self.assertEqual(body["code"], "invalid_request")
        self.assertIn("limit", body["fields"])

    def test_http_404_uses_not_found(self):
        response = self.client.get("/missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_http_error_maps_status_to_code(self):
        response = self.client.get("/too-many")

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json(),
            {"error": {"code": "rate_limited", "message": "Slow down."}},
        )

    def test_unexpected_exception_is_500_without_internal_detail(self):
        with self.assertLogs("apps.common.errors", level="ERROR"):
            response = self.client.get("/crash")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "server_error")
        self.assertNotIn("secret internal detail", response.content.decode())
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.common.tests.test_errors`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.common.errors'`.

- [ ] **Step 3: Write the implementation**

`apps/common/errors.py`:

```python
"""The single error shape of the API and the handlers that produce it."""
import logging

from django.http import Http404
from ninja.errors import AuthenticationError, HttpError, ValidationError

logger = logging.getLogger(__name__)

STATUS_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    429: "rate_limited",
}


class ApiError(Exception):
    """Raise from a view or service to answer with a specific code and status."""

    def __init__(self, code, message, *, status=400, fields=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.fields = fields


def error_body(code, message, fields=None):
    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return {"error": error}


def register_error_handlers(api):
    @api.exception_handler(ApiError)
    def on_api_error(request, exc):
        return api.create_response(
            request, error_body(exc.code, exc.message, exc.fields), status=exc.status
        )

    @api.exception_handler(ValidationError)
    def on_validation_error(request, exc):
        fields = {}
        for item in exc.errors:
            location = item.get("loc") or ("request",)
            fields.setdefault(str(location[-1]), []).append(item.get("msg", "Invalid value."))
        return api.create_response(
            request,
            error_body("invalid_request", "Some values are missing or invalid.", fields),
            status=400,
        )

    @api.exception_handler(AuthenticationError)
    def on_authentication_error(request, exc):
        return api.create_response(
            request, error_body("unauthenticated", "Sign in to continue."), status=401
        )

    @api.exception_handler(HttpError)
    def on_http_error(request, exc):
        code = STATUS_CODES.get(exc.status_code, "error")
        return api.create_response(
            request, error_body(code, str(exc)), status=exc.status_code
        )

    @api.exception_handler(Http404)
    def on_not_found(request, exc):
        return api.create_response(
            request, error_body("not_found", "Not found."), status=404
        )

    @api.exception_handler(Exception)
    def on_unexpected(request, exc):
        logger.exception("Unhandled API error")
        return api.create_response(
            request, error_body("server_error", "Something went wrong."), status=500
        )
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.common.tests.test_errors`
Expected: `Ran 6 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/common/errors.py apps/common/tests/test_errors.py
git commit -m "feat: add the API error shape and handlers"
```

---

### Task 4: Mount the API at /v1/

**Files:**
- Create: `config/api.py`, `apps/common/tests/test_api_mount.py`
- Modify: `config/urls.py`

- [ ] **Step 1: Write the failing test**

`apps/common/tests/test_api_mount.py`:

```python
from django.test import TestCase


class ApiMountTests(TestCase):
    def test_openapi_description_is_published(self):
        response = self.client.get("/v1/openapi.json", secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["info"]["title"], "NegosyoOnline API")

    def test_unknown_v1_path_answers_with_the_error_shape(self):
        response = self.client.get("/v1/no-such-endpoint/", secure=True)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_healthz_still_works(self):
        response = self.client.get("/healthz/", secure=True)

        self.assertEqual(response.status_code, 200)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.common.tests.test_api_mount`
Expected: the first two tests FAIL with status 404 pages that are not JSON; the third passes.

- [ ] **Step 3: Write the implementation**

`config/api.py`:

```python
"""The one API of the backend. Routers from each app are added here in later slices."""
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from ninja import NinjaAPI

from apps.common.errors import error_body, register_error_handlers

api = NinjaAPI(
    title="NegosyoOnline API",
    version="1.0.0",
    urls_namespace="v1",
    openapi_url="/openapi.json",
    docs_url="/docs" if settings.DEBUG else None,
)
register_error_handlers(api)


@csrf_exempt  # so a write to an unknown path gets this JSON 404, not Django's HTML CSRF page
def api_not_found(request, *args, **kwargs):
    """Unknown /v1/ paths answer in the API's error shape, not Django's HTML 404."""
    return JsonResponse(error_body("not_found", "No such endpoint."), status=404)
```

In `config/urls.py`, replace the imports and `urlpatterns` (keep the `healthz` view as it is):

```python
from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, re_path

from config.api import api, api_not_found


def healthz(_request):
    return JsonResponse({"status": "ok", "commit": settings.RELEASE_COMMIT})

urlpatterns = [
    path('admin/', admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("v1/", api.urls),
    re_path(r"^v1/", api_not_found),
]
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.common.tests.test_api_mount`
Expected: `Ran 3 tests` and `OK`.

- [ ] **Step 5: Run everything and commit**

Run: `uv run python manage.py test`
Expected: `OK` (17 tests).

```bash
git add config/api.py config/urls.py apps/common/tests/test_api_mount.py
git commit -m "feat: mount the v1 API and publish its OpenAPI description"
```

---

### Task 5: Origin allowlist

Rule from the spec: browsers may call the API with credentials only from the configured site and dashboard origins, and a state-changing request from any other origin is refused. Requests with no `Origin` header (webhooks, server-to-server, curl) pass this middleware; endpoints that rely on the sign-in cookie will additionally require an `Origin` in slice 2.

**Files:**
- Create: `apps/common/middleware.py`, `apps/common/tests/test_middleware.py`
- Modify: `config/settings.py`, `.env.example`

- [ ] **Step 1: Write the failing test**

`apps/common/tests/test_middleware.py`:

```python
from django.test import TestCase, override_settings

DASH = "https://dash.example.test"
STOREFRONT = "https://shop1.example.test"


@override_settings(API_ALLOWED_ORIGINS=[DASH])
class ApiOriginMiddlewareTests(TestCase):
    def test_allowed_origin_gets_cors_headers_with_credentials(self):
        response = self.client.get("/v1/openapi.json", secure=True, HTTP_ORIGIN=DASH)

        self.assertEqual(response["Access-Control-Allow-Origin"], DASH)
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")
        self.assertIn("Origin", response["Vary"])

    def test_other_origin_gets_no_cors_headers_on_reads(self):
        response = self.client.get("/v1/openapi.json", secure=True, HTTP_ORIGIN=STOREFRONT)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_other_origin_is_refused_on_writes(self):
        response = self.client.post("/v1/anything/", secure=True, HTTP_ORIGIN=STOREFRONT)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "origin_not_allowed")

    def test_write_without_origin_passes_the_middleware(self):
        response = self.client.post("/v1/anything/", secure=True)

        self.assertEqual(response.status_code, 404)

    def test_preflight_from_allowed_origin(self):
        response = self.client.options(
            "/v1/anything/",
            secure=True,
            HTTP_ORIGIN=DASH,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], DASH)
        self.assertIn("POST", response["Access-Control-Allow-Methods"])

    def test_preflight_from_other_origin_has_no_cors_headers(self):
        response = self.client.options(
            "/v1/anything/",
            secure=True,
            HTTP_ORIGIN=STOREFRONT,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        self.assertEqual(response.status_code, 204)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_paths_outside_v1_are_untouched(self):
        response = self.client.get("/healthz/", secure=True, HTTP_ORIGIN=DASH)

        self.assertNotIn("Access-Control-Allow-Origin", response)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.common.tests.test_middleware`
Expected: FAIL (no `Access-Control-Allow-Origin` header; the write test gets 404 instead of 403).

- [ ] **Step 3: Write the implementation**

`apps/common/middleware.py`:

```python
"""CORS for the allowed browser origins, and refusal of writes from any other origin."""
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.cache import patch_vary_headers

from apps.common.errors import error_body

API_PREFIX = "/v1/"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
ALLOW_HEADERS = "content-type"
PREFLIGHT_MAX_AGE = "600"


class ApiOriginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(API_PREFIX):
            return self.get_response(request)

        origin = request.headers.get("Origin")
        allowed = bool(origin) and origin in settings.API_ALLOWED_ORIGINS

        if origin and not allowed and request.method not in SAFE_METHODS:
            return JsonResponse(
                error_body("origin_not_allowed", "This origin may not call the API."),
                status=403,
            )

        is_preflight = (
            request.method == "OPTIONS"
            and origin
            and request.headers.get("Access-Control-Request-Method")
        )
        if is_preflight:
            response = HttpResponse(status=204)
            if allowed:
                response["Access-Control-Allow-Methods"] = ALLOW_METHODS
                response["Access-Control-Allow-Headers"] = ALLOW_HEADERS
                response["Access-Control-Max-Age"] = PREFLIGHT_MAX_AGE
        else:
            response = self.get_response(request)

        if allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Credentials"] = "true"
        patch_vary_headers(response, ["Origin"])
        return response
```

In `config/settings.py`:

1. Add `'apps.common.middleware.ApiOriginMiddleware',` to `MIDDLEWARE` directly after `'django.middleware.security.SecurityMiddleware',`.
2. Directly below the `CSRF_TRUSTED_ORIGINS = [...]` block, add:

```python
# Browser origins that may call the API with credentials (the site and the dashboard).
# Subscriber storefront origins must never be listed here.
API_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "API_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:3001"
    ).split(",")
    if origin.strip()
]
```

In `.env.example`, add the line:

```
API_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run python manage.py test apps.common.tests.test_middleware`
Expected: `Ran 7 tests` and `OK`.

- [ ] **Step 5: Run every check CI runs, then commit**

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
DJANGO_DEBUG=false DJANGO_SECRET_KEY=local-check-only-long-random-value-0123456789abcdef uv run python manage.py check --deploy
```

Expected: `No changes detected`, `OK` (24 tests), and no errors from `check --deploy`.

```bash
git add apps/common/middleware.py apps/common/tests/test_middleware.py config/settings.py .env.example
git commit -m "feat: add the API origin allowlist"
```

- [ ] **Step 6: Open PR A and deploy to staging**

Push, open the pull request "feat: v1 API foundation (ids, errors, mount, origins)", enable auto-merge, and wait for the staging deploy with `scripts/wait_railway_deploy.sh <before-deployment-id>`. Then verify:

```bash
scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <merge-commit-prefix>
curl -s https://api-staging.negosyonline.com/v1/openapi.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["title"])'
curl -s -o /dev/null -w "%{http_code}\n" https://api-staging.negosyonline.com/v1/nope/
```

Expected: `OK:` with the merge commit, `NegosyoOnline API`, and `404`.

- [ ] **Step 7: APPROVAL. Set the staging origin variable**

Ask Coach Ed to add this variable to the staging `api` service in Railway (it redeploys the service):

```
API_ALLOWED_ORIGINS=https://www-staging.negosyonline.com,https://dash-staging.negosyonline.com
```

Then verify from a terminal:

```bash
curl -s -D - -o /dev/null -H "Origin: https://dash-staging.negosyonline.com" https://api-staging.negosyonline.com/v1/openapi.json | grep -i "access-control-allow-origin"
curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Origin: https://demo1.storefronts-staging.negosyonline.com" https://api-staging.negosyonline.com/v1/anything/
```

Expected: the header line with the dashboard origin, and `403`.

---

### Task 6: Custom operator user model

Django must be told about a custom user model before the first migration. Staging already has the 18 built-in migrations applied with Django's default user (see `docs/runbooks/migrations.md`, History). Switching the user model on a database in that state fails with `InconsistentMigrationHistory`, so the built-in migrations are unapplied on staging first, while the old code is still running, and re-applied after the new code is deployed. Staging holds no product data.

**Files:**
- Create: `apps/operators/__init__.py` (empty), `apps/operators/apps.py`, `apps/operators/models.py`, `apps/operators/admin.py`, `apps/operators/tests.py`, `apps/operators/migrations/__init__.py` (empty), `apps/operators/migrations/0001_initial.py` (generated)
- Modify: `config/settings.py`

- [ ] **Step 1: Create the branch and write the failing test**

```bash
git fetch origin && git checkout -b feat/operators-user origin/main
```

`apps/operators/tests.py`:

```python
from django.contrib.auth import get_user_model
from django.test import TestCase


class OperatorUserTests(TestCase):
    def test_user_model_is_the_operators_user(self):
        self.assertEqual(get_user_model()._meta.label, "operators.User")

    def test_superuser_can_be_created_and_can_open_the_admin(self):
        get_user_model().objects.create_superuser(
            username="operator", email="operator@example.test", password="a-long-test-only-passphrase"
        )
        self.client.login(username="operator", password="a-long-test-only-passphrase")

        response = self.client.get("/admin/", secure=True)

        self.assertEqual(response.status_code, 200)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run python manage.py test apps.operators`
Expected: FAIL: `'auth.User' != 'operators.User'`.

- [ ] **Step 3: Write the implementation**

`apps/operators/apps.py`:

```python
from django.apps import AppConfig


class OperatorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.operators"
```

`apps/operators/models.py`:

```python
"""Operators are Django staff users. Subscribers are Accounts (slice 2), never Users."""
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    pass
```

`apps/operators/admin.py`:

```python
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.operators.models import User

admin.site.register(User, UserAdmin)
```

In `config/settings.py`:

1. Add `'apps.operators',` to `INSTALLED_APPS` directly above `'apps.health',`.
2. Directly below the `INSTALLED_APPS` list, add:

```python
AUTH_USER_MODEL = "operators.User"
```

- [ ] **Step 4: Generate the migration**

Run: `uv run python manage.py makemigrations operators`
Expected: `apps/operators/migrations/0001_initial.py` is created with `Create model User`.

- [ ] **Step 5: Run the tests and the CI checks, then commit**

```bash
uv run python manage.py test
uv run python manage.py makemigrations --check --dry-run
```

Expected: `OK` (26 tests) and `No changes detected`.

```bash
git add apps/operators config/settings.py
git commit -m "feat: add the custom operator user model"
```

Push and open PR B "feat: custom operator user model". **Do not enable auto-merge yet.** Steps 6 and 7 must run before it merges.

- [ ] **Step 6: APPROVAL. Confirm staging has no users to lose**

With Coach Ed's approval for this run, and while the old code is still deployed:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py shell -c "from django.contrib.auth.models import User; print(User.objects.count())"
```

Expected: `0`. If it prints anything else, stop and tell Coach Ed: those users would be deleted by the next step and must be recreated afterwards.

- [ ] **Step 7: APPROVAL. Unapply the built-in migrations on staging**

Still on the old code. Each command drops only empty built-in tables:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py migrate admin zero --noinput
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py migrate sessions zero --noinput
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py migrate auth zero --noinput
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py migrate contenttypes zero --noinput
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py showmigrations
```

Expected: every migration in the last output shows `[ ]`. `/healthz/` still returns ok; it does not use the database.

- [ ] **Step 8: Merge PR B and wait for the deploy**

Enable auto-merge on PR B, wait with `scripts/wait_railway_deploy.sh <before-deployment-id>`, and verify `/healthz/` reports the merge commit.

- [ ] **Step 9: APPROVAL. Apply the migrations on staging**

Follow `docs/runbooks/migrations.md`: show the plan, read it, apply, confirm nothing is pending.

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py migrate --plan
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py migrate --noinput
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py migrate --plan
```

Expected: the first plan lists the built-in migrations plus `operators.0001_initial`, with `operators.0001_initial` before `admin.0001_initial`. The last command prints `No planned migration operations.`

---

### Task 7: Record it

**Files:**
- Modify: `docs/runbooks/migrations.md` (History), `README.md`, `AGENTS.md`

- [ ] **Step 1: Update the documents** (in PR B's branch before it merges, or a follow-up docs pull request)

Append to the History list in `docs/runbooks/migrations.md`:

```markdown
- <date of the run>: the built-in migrations were unapplied on `staging` and re-applied together with `operators.0001_initial`, because the custom user model (`operators.User`) must exist before `auth` and `admin` are migrated. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
```

Add to `README.md`, below the "Local setup" section:

```markdown
## Layout

- `config/api.py`: the single API, served under `/v1/`. Its OpenAPI description is at `/v1/openapi.json` and is the contract the frontend builds against.
- `apps/common`: shared code with no business meaning (public ids, the error shape, the origin allowlist).
- `apps/operators`: the staff user model used by the Django admin. Subscribers are not Django users.
- `API_ALLOWED_ORIGINS`: the site and dashboard origins allowed to call the API from a browser. Never list a storefront origin.
```

Add to the Safety list in `AGENTS.md`:

```markdown
- API errors use one shape, produced by `apps/common/errors.py`. Raise `ApiError`; do not hand-build error responses.
- Never expose a database primary key through the API. Use `apps.common.ids.new_public_id`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/migrations.md README.md AGENTS.md
git commit -m "docs: record the foundation layout and the staging re-migration"
```

---

## Done when

- `uv run python manage.py test` passes locally and in CI (26 tests).
- Staging serves `https://api-staging.negosyonline.com/v1/openapi.json`, answers unknown `/v1/` paths with the JSON error shape, sends CORS headers only to the two staging origins, and refuses writes from storefront origins.
- Staging's database is migrated with `operators.User` as the user model and `migrate --plan` reports nothing pending.
- The frontend session has the OpenAPI URL.

## Self-review against the spec

- Section 3 (one API under `/v1/`, OpenAPI published): Task 4.
- Section 4 (custom `operators.User` before the first migration; origin allowlist; storefront origins never allowed): Tasks 5 and 6. The sign-in cookie and sessions are slice 2.
- Section 5 (opaque public ids): Task 2. The fields that store them arrive with the first models in slice 2.
- Section 8 (one error shape, normal HTTP statuses): Tasks 3 and 4. Cursor pagination is added with the first list endpoint in slice 3.
- Section 9 item 1 (test tooling): the existing Django runner is kept; each new module has its own test module. No new test framework is added.
- Not in this slice by design: any subscriber model, any endpoint from the section 8 table, the task queue.
