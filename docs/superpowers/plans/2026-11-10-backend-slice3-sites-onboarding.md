# Backend Slice 3 (Sites, Subdomains and Onboarding State) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** draft for Coach Ed's approval. Nothing in it is built. The "Decisions needed" section must be answered first.

**ClickUp:** `z8uq9mach6` "Phase 1: Foundation — Account, Site, Onboarding State & Authorization" (https://app.clickup.com/t/z8uq9mach6), child of `z8uq9mach5`. This is a port: the behaviour was built and accepted in TWCinc, and is rewritten here in this repository's design.

**Goal:** A signed-in subscriber can check a web address, create their first Santé site, read and edit that site and its business profile, and read a server-derived onboarding state. No account can read or change another account's site.

**Architecture:** A new `apps/sites` app owns `Site`, `Subdomain`, `SiteBusinessProfile` and `Onboarding`, with small modules for the subdomain rules, the template registry, onboarding derivation and the site services. Its Ninja router is mounted on the existing `/v1/` API and uses `auth=signed_in` from `apps/accounts/api.py`. It reads the account only from `request.account` and writes the account audit trail through `apps.accounts.services.record_event`. It adds no migration and no model change in `apps/accounts`.

**Tech Stack:** Python 3.12, Django 5.2, Django Ninja 1.x, Django's cache, `uv`, Django's test runner.

**Spec:** `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`, sections 5, 6 (the `sites` tables), 7 (first paragraph only), 8 (the Sites row) and 9 item 3.

## Scope of this plan

In this plan:

- `Site`, the `Subdomain` ledger, `SiteBusinessProfile`, and the per-account `Onboarding` record.
- `POST sites/subdomain-check/`, `GET POST sites/`, `GET PATCH sites/{site_id}/`, `GET PATCH sites/{site_id}/business-profile/`.
- The onboarding state read (Decision 2 settles its path).
- Cursor pagination, first used by `GET sites/`.
- Read-only admin pages for operators.

Not in this plan, with the reason:

| Left out | Why |
|---|---|
| `Subscription` and any trial record | Billing is slice 4 and has no owner yet. See Decision 1. |
| `GET public/sites/by-host/` | Spec section 7 makes storefront availability depend on the subscription state, which does not exist yet. It ships with or after slice 4. |
| Subscriber rename of a subdomain, site deletion | Legacy never let a subscriber do either (`negosyoonline_platform/sites.py:196-243`, rename is operator-only in `admin.py:105-120`). |
| Operator rename, release and reserve actions, `OperatorAuditEntry` | The ledger supports them (states and `released_at` exist). The actions need the operator audit table, which no slice has built. Proposed as a follow-up. |
| BIR seal image upload | Needs private file storage, which the scope spec does not cover. |
| Santé site settings (sponsor ID), catalogue, members, payouts | Santé domain. The task forbids it. |
| Launch gate, platform switches, per-account gate exceptions, interim site states, the Santé selection approval table | Spec section 6: not rebuilt from legacy. |
| The derived "Santé Site Owner" label | Phase 6 (`z8uq9machh`), still open. `Site.template` is already the only capability source. |
| `GET /v1/dashboard/` | Its legacy payload is trial, referral and sales figures: billing, referrals and commerce. See Decision 3. |

## Rules carried over from legacy, with sources

Legacy paths are under `~/Dev/twcinc/app/`, read at commit `ac483675c`. Rules only; no code is copied.

| # | Rule | Legacy source | In this plan |
|---|---|---|---|
| L1 | A subdomain is trimmed and lowercased before any check. | `negosyoonline_platform/subdomains.py:18-19` | Same. |
| L2 | Format: 3 to 30 characters, lowercase letters, digits and hyphens, starts and ends with a letter or digit, no double hyphen. | `subdomains.py:9, 22-23`; `models.py:19` | Same. The spec states the 30 maximum; the 3 minimum and the double-hyphen rule come from legacy. |
| L3 | Infrastructure labels are never assignable: `www, dash, hq, api, admin, app, mail, static, notify, billing, login, support, help, status, ggwp, webhook`. | `reserved.py:8-11` | Same list. |
| L4 | Brand names are reserved for self-serve: `sante, santebarley, santebarleyph, official, negosyoonline`. | `reserved.py:12` | Same list. |
| L5 | A name is compared both as typed and with hyphens and digits removed. Reserved also when the squashed form contains `official`, or contains `sante` together with `ph` or `corporate`. | `reserved.py:15-30` | Same. |
| L6 | The reserved check runs before the format check, so `hq` answers reserved, not invalid. | `subdomains.py:26-41` | Same order: reserved, invalid, taken, available. |
| L7 | A name held by a soft-deleted site, or retired and not released, is taken. | `subdomains.py:37-39`; `models.py:135-142` | One `Subdomain` ledger row per name, as the spec defines. |
| L8 | The check answers 200 with a status and a message; over the limit it answers 429. Limit: 60 checks per account per 10 minutes. | `api.py:121-135` | Same. Legacy's fifth status, `refused` (collision with a TWC host), has no meaning here and is dropped. |
| L9 | Messages: "Use 3 to 30 lowercase letters, digits or single hyphens.", "That name is reserved. Please choose another.", "That name is already taken. Please choose another." | `subdomains.py:10-15` | Same wording. |
| L10 | Site creation accepts only `template`, `subdomain` and `display_name`. Any other key is refused and nothing is created. | `api.py:95-98` | Same; refused with 400 `invalid_request`. |
| L11 | Creation for one account is serialised by a row lock, and a repeat request answers 409 and creates nothing. There is no idempotency key. | `sites.py:73-110`; `tests/test_setup_races.py` | Same, locking the account's `Onboarding` row. |
| L12 | A removed first site blocks creating another: 409 `first_site_removed`. | `sites.py:73-110` | Same. |
| L13 | Site creation: at most 10 attempts per account per day. | `api.py:115` | Same. |
| L14 | `display_name` is trimmed, 1 to 80 characters. | `sites.py:154-190` | Same. Required on create here (legacy defaulted it to the subdomain; the owner ruling requires a first Site name). |
| L15 | Template and subdomain cannot be changed by the subscriber after creation. | `tests/test_template_capability.py` (PatchCannotChangeTemplateTests) | Same. |
| L16 | Only Santé is offered; "Basic Site" is never offered and is no fallback. A request for another template answers 400 `invalid_template`. | `templates_registry.py:64-73`; `sites.py:97-101` | Same, and stricter: no other template exists in the model's choices. |
| L17 | Contact email: trimmed, lowercased, valid, may be blank. Contact mobile: Philippine mobile, stored as `+639XXXXXXXXX`, may be blank. | `sites.py:154-190`; `negosyoonline/services.py:69-80` | Same. |
| L18 | Social links: https only, host `instagram.com` or `www.instagram.com`, `tiktok.com` or `www.tiktok.com`, 200 characters at most. Accent colour: `#RRGGBB`, stored uppercase. | `sites.py:151-190` | Same. |
| L19 | Business name: 160 characters at most. | `sites.py:154-190` | Same. |
| L20 | Business profile fields and limits: registered name 120, TIN 9 or 14 digits stored hyphenated, DTI number 4 to 15 digits, BIR serial `^\d{3}[A-Z]{2}\d{14}$`, address 255, complaints contact name 120, privacy contact email, six shipping estimates of 60 with the legacy defaults, a confirmed flag. All optional. | `negosyoonline_orders/business_profile.py:20-84`; `negosyoonline_platform/models.py:312-335` | Same. |
| L21 | Onboarding states: `not_started`, `in_progress`, `minimum_complete`, `blocked`. | `models.py:370` | Same. |
| L22 | Onboarding is evaluated in a fixed order, first match wins; the read never writes or locks. | `setup.py:68-91`; `tests/test_subscriber_setup.py` | Same. |
| L23 | `missing` uses the keys `business_name`, `site_name`, `public_contact`. Public contact needs both the email and the mobile. | `setup.py:19, 68-91` | Same. |
| L24 | `started_at` and `minimum_completed_at` are stamped once by a write and never cleared, even if a field is later emptied. | `setup.py:94-120` | Same. |
| L25 | Another account's site answers 404, identical to a missing or deleted one. | `access.py:6-8`; `tests/test_setup_isolation.py`, `tests/test_isolation.py` | Same. |
| L26 | The account is always the signed-in one; no account or site selector in a body or query is trusted. | `api.py:30-34` | Same. |

Deliberate differences from legacy, each with its source:

- **Site creation is not refused after the trial ends.** Legacy answers 409 `subscription_inactive`. Spec section 7 says a first site created after day 14 gets a subscription in `cancelled_no_payment_method` and goes live once a card is added.
- **Trial and subscription are not created at signup.** Spec section 7: signup creates the Account only.
- **One `Site` table.** Spec section 6: it replaces legacy's billing Site and platform Site, so the "link" between them and its `business_identity_unavailable` block disappear.
- **No `site_billing_only` block.** It depended on interim site states, which are not rebuilt.

## The contract

All paths are under `/v1/`. Every endpoint uses `auth=signed_in`: no cookie answers 401 `unauthenticated`, and a write without an allowed `Origin` answers 403. Errors use the one shape from `apps/common/errors.py`, with `fields` values as lists of messages.

### Shapes

`Site`:

```json
{
  "id": "site_…",
  "template": "sante",
  "template_label": "Santé",
  "display_name": "Ana's Store",
  "subdomain": "anas-store",
  "host": "anas-store.negosyotest.com",
  "business_name": "",
  "contact_email": "",
  "contact_mobile": "",
  "accent_color": "",
  "instagram_url": "",
  "tiktok_url": "",
  "created_at": "2026-11-10T08:00:00+08:00"
}
```

`host` is `null` when `SITE_PARENT_HOST` is not set. `business_name` is read-only here; it is written through the business profile.

`BusinessProfile`: `business_name`, `registered_name`, `tin`, `dti_business_number`, `bir_rsn`, `business_address`, `complaints_contact_name`, `privacy_contact_email`, `shipping_processing_time`, `shipping_metro_manila_estimate`, `shipping_luzon_estimate`, `shipping_visayas_estimate`, `shipping_mindanao_estimate`, `shipping_other_areas_estimate` (strings), `shipping_estimates_confirmed` (boolean), `updated_at`.

### Endpoints

| Endpoint | Request | Success | Errors |
|---|---|---|---|
| `POST sites/subdomain-check/` | `{"subdomain": "…"}` | 200 `{"subdomain": "<normalised>", "status": "available" \| "taken" \| "reserved" \| "invalid", "message": "…"}`. `message` is empty when available. | 429 `rate_limited` after 60 checks in 10 minutes. |
| `GET sites/` | Query: `limit` (1 to 100, default 20), `cursor`. | 200 `{"items": [Site…], "next_cursor": "site_…" \| null}`. Only the caller's sites that are not deleted, oldest first. | 400 `invalid_cursor`. |
| `POST sites/` | `{"template": "sante", "subdomain": "…", "display_name": "…"}`. All three required; no other key. | 201 `Site`. | 400 `invalid_request` (missing or extra key; `fields.display_name` "Use 1 to 80 characters."). 400 `invalid_template` with `fields.template`. 400 `subdomain_invalid`, 400 `subdomain_reserved`, 409 `subdomain_taken`, each with `fields.subdomain`. 409 `site_limit_reached`. 409 `first_site_removed`. 403 `email_not_verified`. 429 `rate_limited` after 10 attempts in a day. |
| `GET sites/{site_id}/` | none | 200 `Site`. | 404 `not_found`. |
| `PATCH sites/{site_id}/` | Any of `display_name`, `contact_email`, `contact_mobile`, `accent_color`, `instagram_url`, `tiktok_url`. | 200 `Site`. | 400 `invalid_request` with every failing field in `fields` (also for `template`, `subdomain` or any unknown key). 404 `not_found`. |
| `GET sites/{site_id}/business-profile/` | none | 200 `BusinessProfile`. | 404 `not_found`. |
| `PATCH sites/{site_id}/business-profile/` | Any `BusinessProfile` field except `updated_at`. A blank string clears a field. | 200 `BusinessProfile`. | 400 `invalid_request` with `fields`. 404 `not_found`. |
| `GET account/setup/` (Decision 2) | none; any query is ignored | 200 `Setup`, below. | none beyond 401. |

`Setup`:

```json
{
  "state": "not_started",
  "blocked_reason": null,
  "missing": ["business_name", "site_name", "public_contact"],
  "first_site": null,
  "can_create_site": true,
  "templates": [{"id": "sante", "label": "Santé"}],
  "site_host_suffix": "negosyotest.com",
  "started_at": null,
  "minimum_completed_at": null
}
```

`first_site` is `null` or `{"id": "site_…", "subdomain": "…", "display_name": "…", "deleted": false}`.

### Onboarding state: values and derivation

The state is never stored and never accepted from a client. It is computed on every read from persisted fields. The rules run in this order and the first match wins:

| # | Condition (persisted data) | `state` | `blocked_reason` | `missing` |
|---|---|---|---|---|
| 1 | `Account.email_verified_at` is null | `blocked` | `owner_email_unverified` | all three |
| 2 | The account's `Onboarding.first_site` is empty | `not_started` | null | all three |
| 3 | The first site has `deleted_at` set | `blocked` | `first_site_removed` | `[]` |
| 4 | Anything in `missing` | `in_progress` | null | the missing keys |
| 5 | Otherwise | `minimum_complete` | null | `[]` |

`missing` for rules 4 and 5:

- `business_name`: the first site's `SiteBusinessProfile.business_name` is blank.
- `site_name`: `Site.display_name` is blank. The subdomain cannot be missing: a Site always has one active `Subdomain`.
- `public_contact`: `Site.contact_email` or `Site.contact_mobile` is blank. Both are required.

What is persisted, in `Onboarding` (one row per account): `first_site`, `started_at` (set once, when the first site is created) and `minimum_completed_at` (set once, by the first write that leaves the state at `minimum_complete`). The read sets nothing. `can_create_site` is true only for `not_started`.

The four states the task requires map as: not started, in progress, minimum complete, and blocked or needs verification (`blocked` with `owner_email_unverified`).

### Ownership check for every route

There is one account principal in the MVP: the signed-in Account, which is the owner. So every endpoint here is account-owner-only by construction; there are no team members to refuse.

| Route | Check |
|---|---|
| `POST sites/subdomain-check/` | Signed in. Reads only the name ledger; returns no site or account data. |
| `GET sites/` | Query is filtered by `account = request.account` and `deleted_at IS NULL`. The cursor must name one of the caller's own sites. |
| `POST sites/` | The site is created for `request.account`. The body cannot name an account. |
| `GET PATCH sites/{site_id}/` | `site_for(request.account, site_id)`. |
| `GET PATCH sites/{site_id}/business-profile/` | `site_for(request.account, site_id)`, then that site's profile. |
| `GET account/setup/` | Reads only `request.account` and its `Onboarding` row. |

`site_for(account, site_id)` is the only way a view obtains a Site: it filters on `public_id`, `account` and `deleted_at IS NULL` in one query and raises 404 `not_found` "Not found." The answer is the same for a missing id, a deleted site and another account's site. A test walks every route under `sites/{site_id}/` and fails if a new one is added without the isolation check.

### Santé only

- `apps/sites/templates.py` holds the registry: one entry, `sante`, label "Santé". `Site.template` choices come from it, so the database cannot hold another template.
- `POST sites/` compares `template` against the registry. `standard`, `basic`, an empty string or any other value answers 400 `invalid_template` with `fields.template` "Choose a site type." and creates nothing. There is no default and no fallback: omitting `template` is 400 `invalid_request`.
- `PATCH sites/{site_id}/` refuses `template`.
- The template is the capability source. No account field or label grants anything.

### Subdomain rules and the ledger

- Rules L1 to L6 above, in `apps/sites/subdomains.py`.
- `Subdomain` is the single source of truth. A name is taken when a row exists with `released_at` empty, whatever its state (`active`, `retired`, `operator_reserved`) and whether or not its site is deleted. `name` is unique in the database, which is also the backstop when two accounts ask for one name at the same moment: the loser gets 409 `subdomain_taken`.
- A site has exactly one `active` row, enforced by a conditional unique constraint.
- Nothing in this slice retires or releases a name. The columns and states exist so the operator actions can be added without a schema change.

### Cursor pagination

`apps/common/pagination.py` has one function, `page(queryset, *, limit, cursor, prefix)`. Rows are ordered by primary key (creation order). The cursor is the public id of the last item of the previous page, so no sequence number leaves the API. The function looks the cursor up inside the same filtered queryset: an unknown id, or one belonging to another account, answers 400 `invalid_cursor`. It fetches `limit + 1` rows to decide `next_cursor`.

## What the dashboard must change

The API follows the approved spec; the copied dashboard adapts. This list is for the frontend builder. Line references are to `apps/dash/public/assets/js/` in the frontend repository.

| Today in the dashboard | Change to |
|---|---|
| `Authorization: Bearer` from `localStorage` (`app.js:22-35`) | Session cookie: every call uses `credentials: "include"`; no token is stored. |
| Errors read as top-level `code`, `message`, `fields` with string values | `error.code`, `error.message`, `error.fields` with a list of messages per field. |
| Numeric site ids; `?site=` must match digits (`business.js:99-100` and others) | Opaque ids such as `site_…`. |
| `GET /sites/` returns `{sites: […]}` | `{items: […], next_cursor}`. |
| `GET /platform/eligibility/` (`dashboard.js:857`) | Removed. `can_create_site`, `templates` and `site_host_suffix` are in `GET /account/setup/`. |
| `POST /sites/subdomain-check/`: a non-available answer is read from `message` | Unchanged in substance: 200 with `status` and `message`. |
| `POST /sites/` codes `add_site_unavailable`, `subscription_inactive`, `business_identity_unavailable` (`dashboard.js:568-579`) | `site_limit_reached`; the other two no longer exist. Subdomain errors are `subdomain_invalid`, `subdomain_reserved`, `subdomain_taken`. The 201 `id` is a string. |
| `PATCH /sites/{id}/settings/` with `business_name`, `contact_email`, `contact_mobile` (`dashboard.js:1193`) | Two calls: `PATCH /sites/{id}/` for the contacts, `PATCH /sites/{id}/business-profile/` for `business_name` (Decision 4). |
| `GET /sites/{id}/` reads `settings.contact_email` and the like | The same keys at the top level of `Site`. |
| Setup `blocked_reason` values `business_identity_unavailable`, `site_billing_only` | Gone. New value: `owner_email_unverified`. `first_site_removed` stays. |
| A 404 from `GET /account/setup/` meaning "gate closed" | Never happens; there is no gate. |
| `GET /dashboard/` for `trial` and `referral`; `/me/` keys `site_name`, `trial_status`, `first_name`, `last_name` | Not provided by this slice (Decision 3). The workspace label comes from `first_site.display_name`; `/me/` returns `account.name`. |

## Rules for whoever executes this

- Read `CLAUDE.md`, `.claude/commands/clickup-builder.md` and `.claude/briefs/builder.md` first. Report to the coordinator session, not to Coach Ed.
- Branch names and both PR descriptions carry the task id `z8uq9mach6`.
- **No file under `apps/accounts/` is created or edited**, migrations above all. If a step seems to need a new Account field, stop that step and ask the coordinator.
- Two actions need Coach Ed's explicit approval at the time: Task 6 Step 5 (apply `sites.0001_initial` on staging) and Task 9 Step 3 (set `SITE_PARENT_HOST` on staging). Generating the migration file in Task 2 is approved by approving this plan.
- No seed data. Never touch `production`. Never print secrets.
- All commands run from the repository root with `uv run`. Tests use Django's runner and pass `secure=True`.
- `railway ssh` re-parses the remote command: wrap the whole remote command in single quotes.

## File structure

| File | Responsibility |
|---|---|
| `apps/common/pagination.py` | `page()`: cursor pagination by public id. |
| `apps/common/throttle.py` | `hit(scope, key, limit, window)`: a fixed-window counter in the cache; raises 429 `rate_limited`. |
| `apps/common/phones.py` | `normalise_ph_mobile()`: returns `+639XXXXXXXXX` or raises `ValueError`. |
| `apps/sites/models.py` | `Site`, `Subdomain`, `SiteBusinessProfile`, `Onboarding`. |
| `apps/sites/templates.py` | The template registry: Santé only. |
| `apps/sites/subdomains.py` | Normalise, the reserved lists, `check()`. |
| `apps/sites/onboarding.py` | `evaluate()` (pure read) and `stamp()` (called by writes). |
| `apps/sites/services.py` | `site_for()`, `create_first_site()`, `update_site()`, `update_business_profile()`. The functions other apps may call. |
| `apps/sites/api.py` | The router: shapes and endpoints. |
| `apps/sites/admin.py` | Read-only admin pages. |
| `apps/sites/tests/` | One file per module, plus isolation and concurrency. |
| `config/settings.py`, `config/api.py`, `.env.example` | `apps.sites` in `INSTALLED_APPS`, `SITE_PARENT_HOST`, `SITES_PER_ACCOUNT_LIMIT = 1`, the router mount. |

Two pull requests, as in slice 2a. PR A is Tasks 1 to 6: tables, rules and services with no endpoint, safe to deploy before its migration is applied. PR B is Tasks 7 to 9: the endpoints, which need the tables on staging first.

---

### Task 1: Shared helpers (pagination, throttle, mobile numbers)

**Files:**
- Create: `apps/common/pagination.py`, `apps/common/throttle.py`, `apps/common/phones.py`, `apps/common/tests/test_pagination.py`, `apps/common/tests/test_throttle.py`, `apps/common/tests/test_phones.py`

- [ ] **Step 1: Create the branch**

```bash
git fetch origin && git checkout -b feat/z8uq9mach6-sites-models origin/main
```

- [ ] **Step 2: Write the failing tests**

`test_phones.py`: `09171234567`, `9171234567`, `+639171234567`, `639171234567` and `0917 123-4567` all give `+639171234567`; `0917123456`, `+6391712345678`, `08171234567`, `abc` and `""` raise `ValueError`.

`test_throttle.py`: the call at the limit passes; the next raises `ApiError` with code `rate_limited` and status 429; a different key is counted separately.

`test_pagination.py` is written in Task 2, once a model with a public id exists in `apps/sites` (it pages `Site` rows).

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run python manage.py test apps.common`
Expected: FAIL with `ModuleNotFoundError` for `apps.common.phones`.

- [ ] **Step 4: Write the implementation**

`apps/common/phones.py`:

```python
"""Philippine mobile numbers, stored as +639XXXXXXXXX."""
import re

_STRIP = re.compile(r"[\s\-().]")
_FULL = re.compile(r"^639\d{9}$")


def normalise_ph_mobile(raw):
    digits = _STRIP.sub("", raw or "").removeprefix("+")
    if re.fullmatch(r"09\d{9}", digits):
        digits = "63" + digits[1:]
    elif re.fullmatch(r"9\d{9}", digits):
        digits = "63" + digits
    if not _FULL.match(digits):
        raise ValueError("Enter a valid Philippine mobile number.")
    return "+" + digits
```

`apps/common/throttle.py`:

```python
"""A fixed-window counter in the cache. One place for every per-account limit."""
from django.core.cache import cache

from apps.common.errors import ApiError


def hit(scope, key, *, limit, window):
    cache_key = f"throttle:{scope}:{key}"
    cache.add(cache_key, 0, window)
    try:
        count = cache.incr(cache_key)
    except ValueError:  # the key expired between add and incr
        cache.set(cache_key, 1, window)
        count = 1
    if count > limit:
        raise ApiError("rate_limited", "Too many attempts. Please wait and try again.", status=429)
```

`apps/common/pagination.py`:

```python
"""Cursor pagination. The cursor is the public id of the last item already returned."""
from apps.common.errors import ApiError
from apps.common.ids import is_public_id

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def page(queryset, *, limit, cursor, prefix):
    """Return (items, next_cursor). `queryset` must already be filtered to the caller's rows."""
    limit = max(1, min(limit or DEFAULT_LIMIT, MAX_LIMIT))
    rows = queryset.order_by("pk")
    if cursor:
        last = rows.filter(public_id=cursor).first() if is_public_id(cursor, prefix) else None
        if last is None:
            raise ApiError("invalid_cursor", "That page marker is not valid.")
        rows = rows.filter(pk__gt=last.pk)
    items = list(rows[: limit + 1])
    more = len(items) > limit
    items = items[:limit]
    return items, (items[-1].public_id if more else None)
```

- [ ] **Step 5: Run them and watch them pass, then commit**

```bash
uv run python manage.py test apps.common
git add apps/common && git commit -m "feat: add pagination, throttle and mobile number helpers"
```

---

### Task 2: Site models and the migration

**Files:**
- Create: `apps/sites/__init__.py`, `apps/sites/apps.py`, `apps/sites/templates.py`, `apps/sites/models.py`, `apps/sites/migrations/__init__.py`, `apps/sites/tests/__init__.py`, `apps/sites/tests/test_models.py`, `apps/common/tests/test_pagination.py`
- Modify: `config/settings.py` (add `'apps.sites',` above `'apps.notifications',`; add `SITE_PARENT_HOST` and `SITES_PER_ACCOUNT_LIMIT`), `.env.example` (add `SITE_PARENT_HOST=`)

- [ ] **Step 1: Write the failing tests**

`test_models.py`:
- `test_site_gets_a_public_id` (`site_` prefix).
- `test_template_choices_are_sante_only`.
- `test_subdomain_name_is_unique`.
- `test_subdomain_must_be_lowercase_in_the_database`.
- `test_a_site_has_at_most_one_active_subdomain`.
- `test_a_retired_row_does_not_block_a_new_active_row_for_the_same_site`.
- `test_business_profile_carries_the_suggested_shipping_estimates`.
- `test_one_onboarding_row_per_account`.

`test_pagination.py`: three sites and `limit=2` give two items and a cursor; the cursor gives the third and `None`; another account's site id as cursor raises `invalid_cursor`; a malformed cursor raises `invalid_cursor`; `limit=0` and `limit=1000` are clamped.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run python manage.py test apps.sites apps.common.tests.test_pagination`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.sites.models'`.

- [ ] **Step 3: Write the implementation**

`apps/sites/templates.py`:

```python
"""Site templates. A template is the capability source of a site. The MVP has one."""
SANTE = "sante"
TEMPLATES = {SANTE: "Santé"}
CHOICES = list(TEMPLATES.items())


def offered():
    """The templates a subscriber may choose when creating a site."""
    return [{"id": key, "label": label} for key, label in TEMPLATES.items()]
```

`apps/sites/models.py`:

```python
"""Sites and their web addresses. Every site-owned row in later slices points at Site."""
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower

from apps.common.ids import new_public_id
from apps.sites import templates


def new_site_id():
    return new_public_id("site")


class Site(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_site_id, editable=False)
    account = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="sites")
    template = models.CharField(max_length=30, choices=templates.CHOICES)
    display_name = models.CharField(max_length=80)
    contact_email = models.EmailField(blank=True)
    contact_mobile = models.CharField(max_length=13, blank=True)  # +639XXXXXXXXX
    accent_color = models.CharField(max_length=7, blank=True)  # #RRGGBB
    instagram_url = models.URLField(max_length=200, blank=True)
    tiktok_url = models.URLField(max_length=200, blank=True)
    operator_hold = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.display_name


class Subdomain(models.Model):
    """The ledger of every name ever issued. A name is taken until an operator releases it."""

    class State(models.TextChoices):
        ACTIVE = "active"
        RETIRED = "retired"
        OPERATOR_RESERVED = "operator_reserved"

    name = models.CharField(max_length=30, unique=True)
    site = models.ForeignKey(
        Site, null=True, blank=True, on_delete=models.PROTECT, related_name="subdomains"
    )
    state = models.CharField(max_length=20, choices=State.choices, default=State.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(name=Lower(F("name"))), name="sites_subdomain_lowercase"),
            models.UniqueConstraint(
                fields=["site"], condition=Q(state="active"), name="sites_one_active_subdomain"
            ),
        ]

    def __str__(self):
        return self.name


class SiteBusinessProfile(models.Model):
    site = models.OneToOneField(Site, on_delete=models.PROTECT, related_name="business_profile")
    business_name = models.CharField(max_length=160, blank=True)
    registered_name = models.CharField(max_length=120, blank=True)
    tin = models.CharField(max_length=17, blank=True)
    dti_business_number = models.CharField(max_length=15, blank=True)
    bir_rsn = models.CharField(max_length=19, blank=True)
    business_address = models.CharField(max_length=255, blank=True)
    complaints_contact_name = models.CharField(max_length=120, blank=True)
    privacy_contact_email = models.EmailField(blank=True)
    shipping_processing_time = models.CharField(max_length=60, blank=True, default="1-2 business days")
    shipping_metro_manila_estimate = models.CharField(max_length=60, blank=True, default="1-2 business days")
    shipping_luzon_estimate = models.CharField(max_length=60, blank=True, default="2-5 business days")
    shipping_visayas_estimate = models.CharField(max_length=60, blank=True, default="5-7 business days")
    shipping_mindanao_estimate = models.CharField(max_length=60, blank=True, default="5-7 business days")
    shipping_other_areas_estimate = models.CharField(max_length=60, blank=True)
    shipping_estimates_confirmed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class Onboarding(models.Model):
    """What onboarding persists per account. The state itself is derived; see onboarding.py."""

    account = models.OneToOneField("accounts.Account", on_delete=models.PROTECT, related_name="onboarding")
    first_site = models.OneToOneField(
        Site, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    started_at = models.DateTimeField(null=True, blank=True)
    minimum_completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
```

The foreign keys to `accounts.Account` live in the `sites` migration and depend on `accounts.0001_initial`. They change nothing in `apps/accounts`.

In `config/settings.py`, below the `API_ALLOWED_ORIGINS` block:

```python
# The host under which subscriber sites live, for example negosyotest.com. Empty hides the host.
SITE_PARENT_HOST = os.environ.get("SITE_PARENT_HOST", "").strip().lower().strip(".")
SITES_PER_ACCOUNT_LIMIT = 1  # MVP: onboarding creates the first site only. The schema allows many.
```

- [ ] **Step 4: Generate the migration, run the tests and watch them pass**

```bash
uv run python manage.py makemigrations sites
uv run python manage.py test apps.sites apps.common
```

Expected: `apps/sites/migrations/0001_initial.py` creating `Site`, `Subdomain`, `SiteBusinessProfile` and `Onboarding`, with `('accounts', '0001_initial')` as its only cross-app dependency. Confirm `git status` shows nothing under `apps/accounts/`.

- [ ] **Step 5: Commit**

```bash
git add apps/sites apps/common config/settings.py .env.example
git commit -m "feat: add the sites app with Site, Subdomain, business profile and onboarding record"
```

---

### Task 3: Subdomain rules

**Files:**
- Create: `apps/sites/subdomains.py`, `apps/sites/tests/test_subdomains.py`

- [ ] **Step 1: Write the failing tests**

- `test_normalise_trims_and_lowercases`.
- `test_format`: accepts `abc`, `a-b`, `shop123`, a 30-character name; refuses `ab`, a 31-character name, `-abc`, `abc-`, `a--b`, `a_b`, `a.b`, `añb`, an empty string.
- `test_every_infrastructure_label_is_reserved` and `test_every_brand_name_is_reserved`: loop the two lists.
- `test_combined_forms_are_reserved`: `sante-ph`, `sante2ph`, `santecorporate`, `my-official-store`, `w-w-w`, `api2`.
- `test_ordinary_names_are_not_reserved`: `anas-store`, `mysantestore`, `barley-shop`.
- `test_reserved_is_answered_before_invalid`: `hq` is `reserved`.
- `test_an_active_name_is_taken`, `test_a_deleted_sites_name_is_taken`, `test_a_retired_name_is_taken`, `test_an_operator_reserved_name_is_taken`, `test_a_released_name_is_available`.
- `test_each_status_has_its_message`.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run python manage.py test apps.sites.tests.test_subdomains`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

```python
"""Subdomain rules. A name is never reissued: the Subdomain ledger decides what is taken."""
import re

from apps.sites.models import Subdomain

MIN_LENGTH, MAX_LENGTH = 3, 30
FORMAT = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")

INFRA_LABELS = frozenset({
    "www", "dash", "hq", "api", "admin", "app", "mail", "static", "notify", "billing",
    "login", "support", "help", "status", "ggwp", "webhook",
})
BRAND_NAMES = frozenset({"sante", "santebarley", "santebarleyph", "official", "negosyoonline"})

AVAILABLE, TAKEN, RESERVED, INVALID = "available", "taken", "reserved", "invalid"
MESSAGES = {
    AVAILABLE: "",
    INVALID: "Use 3 to 30 lowercase letters, digits or single hyphens.",
    RESERVED: "That name is reserved. Please choose another.",
    TAKEN: "That name is already taken. Please choose another.",
}


def normalise(raw):
    return (raw or "").strip().lower()


def _squash(name):
    return re.sub(r"[-0-9]", "", name)


def is_reserved(name):
    squashed = _squash(name)
    if name in INFRA_LABELS or squashed in INFRA_LABELS:
        return True
    if name in BRAND_NAMES or squashed in BRAND_NAMES or "official" in squashed:
        return True
    if "sante" in squashed:
        rest = squashed.replace("sante", "")
        return "ph" in rest or "corporate" in squashed
    return False


def is_valid_format(name):
    return (
        MIN_LENGTH <= len(name) <= MAX_LENGTH
        and FORMAT.fullmatch(name) is not None
        and "--" not in name
    )


def check(raw):
    """Return (name, status) for a requested subdomain. Reads the ledger; changes nothing."""
    name = normalise(raw)
    if is_reserved(name):
        return name, RESERVED
    if not is_valid_format(name):
        return name, INVALID
    if Subdomain.objects.filter(name=name, released_at__isnull=True).exists():
        return name, TAKEN
    return name, AVAILABLE
```

A released name is reused by updating its ledger row; that path belongs to the operator follow-up and is not written in this slice. Until then `create_first_site` treats a released row as an operator matter and answers `subdomain_taken` if the unique constraint fires.

- [ ] **Step 4: Run them and watch them pass, then commit**

```bash
uv run python manage.py test apps.sites.tests.test_subdomains
git add apps/sites && git commit -m "feat: add subdomain rules and the never-reissued check"
```

---

### Task 4: Onboarding derivation

**Files:**
- Create: `apps/sites/onboarding.py`, `apps/sites/tests/test_onboarding.py`

- [ ] **Step 1: Write the failing tests**

- `test_a_fresh_account_is_not_started_with_all_three_missing`.
- `test_an_unverified_owner_is_blocked_before_any_other_rule` (also with a complete site).
- `test_a_removed_first_site_is_blocked`.
- `test_a_site_without_business_name_or_contacts_is_in_progress` (missing lists exactly those keys).
- `test_public_contact_needs_both_email_and_mobile`.
- `test_all_required_fields_give_minimum_complete`.
- `test_evaluate_never_writes` (`assertNumQueries` shows only SELECTs; no `Onboarding` row exists after any number of calls).
- `test_evaluate_ignores_another_accounts_site`.
- `test_stamp_sets_started_at_once` and `test_stamp_sets_minimum_completed_at_once`.
- `test_clearing_a_contact_returns_to_in_progress_and_keeps_the_stamp`.
- `test_a_second_later_site_is_never_the_first_site`.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run python manage.py test apps.sites.tests.test_onboarding`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

```python
"""Onboarding state. Derived from persisted fields on every read; never taken from a client."""
from dataclasses import dataclass, field

from django.utils import timezone

from apps.sites.models import Onboarding

NOT_STARTED, IN_PROGRESS, MINIMUM_COMPLETE, BLOCKED = (
    "not_started", "in_progress", "minimum_complete", "blocked",
)
OWNER_EMAIL_UNVERIFIED, FIRST_SITE_REMOVED = "owner_email_unverified", "first_site_removed"
MISSING_ALL = ["business_name", "site_name", "public_contact"]


@dataclass
class Setup:
    state: str
    blocked_reason: str | None = None
    missing: list = field(default_factory=list)
    first_site: object = None
    started_at: object = None
    minimum_completed_at: object = None


def evaluate(account):
    """Read-only. The first matching rule wins; see the plan for the order."""
    record = (
        Onboarding.objects.filter(account=account)
        .select_related("first_site__business_profile")
        .first()
    )
    site = record.first_site if record else None
    stamps = {
        "first_site": site,
        "started_at": record.started_at if record else None,
        "minimum_completed_at": record.minimum_completed_at if record else None,
    }
    if account.email_verified_at is None:
        return Setup(BLOCKED, OWNER_EMAIL_UNVERIFIED, list(MISSING_ALL), **stamps)
    if site is None:
        return Setup(NOT_STARTED, None, list(MISSING_ALL), **stamps)
    if site.deleted_at is not None:
        return Setup(BLOCKED, FIRST_SITE_REMOVED, [], **stamps)
    missing = []
    if not site.business_profile.business_name:
        missing.append("business_name")
    if not site.display_name:
        missing.append("site_name")
    if not (site.contact_email and site.contact_mobile):
        missing.append("public_contact")
    return Setup(IN_PROGRESS if missing else MINIMUM_COMPLETE, None, missing, **stamps)


def stamp(account):
    """Called by every write, inside its transaction. Sets each stamp once and never clears it."""
    record = Onboarding.objects.select_for_update().get(account=account)
    now = timezone.now()
    changed = []
    if record.first_site_id and record.started_at is None:
        record.started_at = now
        changed.append("started_at")
    if record.minimum_completed_at is None and evaluate(account).state == MINIMUM_COMPLETE:
        record.minimum_completed_at = now
        changed.append("minimum_completed_at")
    if changed:
        record.save(update_fields=[*changed, "updated_at"])
```

- [ ] **Step 4: Run them and watch them pass, then commit**

```bash
uv run python manage.py test apps.sites.tests.test_onboarding
git add apps/sites && git commit -m "feat: derive the onboarding state from persisted fields"
```

---

### Task 5: Site services

**Files:**
- Create: `apps/sites/services.py`, `apps/sites/tests/test_services.py`, `apps/sites/tests/test_concurrency.py`

- [ ] **Step 1: Write the failing tests**

`test_services.py`:
- `test_create_makes_the_site_its_subdomain_its_profile_and_the_record_in_one_call`.
- `test_create_records_an_audit_event`.
- `test_any_template_other_than_sante_is_refused_and_nothing_is_created`: loops `standard`, `basic`, `Sante`, `""`, `shop`.
- `test_reserved_invalid_and_taken_answer_their_codes_and_create_nothing`.
- `test_display_name_is_trimmed_and_must_be_1_to_80_characters`.
- `test_a_second_create_is_409_and_creates_nothing` (the resumed case: same account, any subdomain).
- `test_a_removed_first_site_refuses_creation`.
- `test_an_unverified_account_cannot_create`.
- `test_a_failure_after_the_site_row_rolls_everything_back` (patch `Subdomain.objects.create` to raise; no `Site` remains).
- `test_two_accounts_cannot_take_one_subdomain`.
- `test_site_for_answers_the_same_404_for_missing_foreign_and_deleted`.
- `test_update_site_normalises_contacts_colour_and_links`.
- `test_update_site_reports_every_invalid_field_in_one_error_and_saves_nothing`.
- `test_social_links_must_be_https_on_their_own_host`.
- `test_update_business_profile_cleans_tin_dti_and_serial`.
- `test_a_blank_value_clears_a_profile_field`.
- `test_business_name_is_160_at_most`.
- `test_completing_the_last_required_field_stamps_minimum_completed_at`.

`test_concurrency.py` (a `TransactionTestCase`; the threaded cases are skipped unless the database is PostgreSQL, so they run in CI's "PostgreSQL and Redis" job, because SQLite's in-memory test database cannot be shared across threads):
- `test_two_simultaneous_creates_for_one_account_make_one_site`: two threads, a barrier, different subdomains. Exactly one `Site`, one `Subdomain`, one `SiteBusinessProfile`; one call returns the site and the other raises `site_limit_reached`.
- `test_two_accounts_racing_for_one_subdomain_make_one_site`: one succeeds, one raises `subdomain_taken`, and the loser's account has no `Site`.
- `test_simultaneous_updates_leave_one_consistent_row_and_one_stamp`.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run python manage.py test apps.sites.tests.test_services apps.sites.tests.test_concurrency`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

The creation path, which carries the rules that matter most:

```python
def site_for(account, site_id):
    """The only way to reach a Site from a request. Missing, deleted and foreign all answer 404."""
    site = Site.objects.filter(public_id=site_id, account=account, deleted_at__isnull=True).first()
    if site is None:
        raise ApiError("not_found", "Not found.", status=404)
    return site


def _locked_record(account):
    """The account's Onboarding row, locked. Serialises every site write for one account."""
    Onboarding.objects.get_or_create(account=account)
    return Onboarding.objects.select_for_update().get(account=account)


@transaction.atomic
def create_first_site(account, *, template, subdomain, display_name):
    if account.email_verified_at is None:
        raise ApiError("email_not_verified", "Confirm your email to continue.", status=403)
    record = _locked_record(account)
    if record.first_site_id is not None:
        if record.first_site.deleted_at is not None:
            raise ApiError(
                "first_site_removed", "This site was removed. Please contact support.", status=409
            )
    if Site.objects.filter(account=account).count() >= settings.SITES_PER_ACCOUNT_LIMIT:
        raise ApiError("site_limit_reached", "You can't add another site yet.", status=409)
    if template not in templates.TEMPLATES:
        raise ApiError(
            "invalid_template", "Choose a site type.", fields={"template": ["Choose a site type."]}
        )
    display_name = (display_name or "").strip()
    if not 1 <= len(display_name) <= 80:
        raise ApiError(
            "invalid_request",
            "Some values are missing or invalid.",
            fields={"display_name": ["Use 1 to 80 characters."]},
        )
    name, status = subdomains.check(subdomain)
    if status != subdomains.AVAILABLE:
        raise _subdomain_error(status)
    site = Site.objects.create(account=account, template=template, display_name=display_name)
    try:
        with transaction.atomic():
            Subdomain.objects.create(name=name, site=site)
    except IntegrityError:
        raise _subdomain_error(subdomains.TAKEN) from None
    SiteBusinessProfile.objects.create(site=site)
    record.first_site = site
    record.save(update_fields=["first_site", "updated_at"])
    onboarding.stamp(account)
    record_event(account, "site_created", site=site.public_id, subdomain=name, template=template)
    return site
```

`_subdomain_error(status)` maps `invalid` and `reserved` to 400 and `taken` to 409, with code `subdomain_<status>` and the message in `fields.subdomain`. Because the `ApiError` propagates out of `transaction.atomic`, the `Site` row is rolled back with it.

`update_site(account, site, data)` and `update_business_profile(account, site, data)` follow the same pattern: lock the account's record, lock the row, clean every field present in `data` collecting all errors into one `ApiError("invalid_request", …, fields=…)`, save only when there is no error, call `onboarding.stamp(account)`, and `record_event(account, "site_updated" | "business_profile_updated", site=…, fields=[names])`. Audit detail holds field names only, never values. Field rules are L14 and L17 to L20.

- [ ] **Step 4: Run them and watch them pass, then commit**

```bash
uv run python manage.py test apps.sites
git add apps/sites && git commit -m "feat: add first-site creation and site settings services"
```

---

### Task 6: Admin pages, PR A, and the staging migration

**Files:**
- Create: `apps/sites/admin.py`

- [ ] **Step 1: Register the models in the admin, read-only**

`Site` (list: display name, account, template, operator hold, created, deleted; search by display name, account email and subdomain name), `Subdomain` (list: name, site, state, retired, released), `SiteBusinessProfile` and `Onboarding`. No add, change or delete permission in this slice: the operator actions need the operator audit table.

- [ ] **Step 2: Run every check CI runs**

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
DJANGO_DEBUG=false DJANGO_SECRET_KEY=local-check-only-long-random-value-0123456789abcdef uv run python manage.py check --deploy
```

Expected: `No changes detected`, `OK`, and only the two deferred HSTS warnings (W005, W021).

- [ ] **Step 3: Commit and open PR A**

"feat: sites, subdomain ledger and onboarding record (no endpoints) [z8uq9mach6]". The description names the task and its URL, says it adds one migration file (`sites.0001_initial`) that the deploy does not apply, and that nothing in the running service reads the new tables yet.

- [ ] **Step 4: Verify the deploy**

```bash
scripts/wait_railway_deploy.sh <before-deployment-id>
scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <merge-commit-prefix>
```

- [ ] **Step 5: APPROVAL. Apply the migration on staging**

Needs Coach Ed's explicit approval at the time. Follow `docs/runbooks/migrations.md`:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- 'uv run python manage.py migrate --plan'
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- 'uv run python manage.py migrate --noinput'
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- 'uv run python manage.py migrate --plan'
```

Expected: the first plan lists exactly `sites.0001_initial`. If it lists anything else, including an `accounts` migration from the other session, stop and tell the coordinator. The last command prints `No planned migration operations.` Record the run in PR A and in the runbook's History.

---

### Task 7: Site endpoints

**Files:**
- Create: `apps/sites/api.py`, `apps/sites/tests/base.py`, `apps/sites/tests/test_api_sites.py`
- Modify: `config/api.py` (add `api.add_router("", sites_router)`)

- [ ] **Step 1: Create the branch and write the failing tests**

```bash
git fetch origin && git checkout -b feat/z8uq9mach6-sites-endpoints origin/main
```

`test_api_sites.py`, using the `ApiTestCase` from `apps/accounts/tests/base.py`:
- Signed out: every endpoint answers 401.
- Origin: every write without an allowed `Origin` answers 403.
- `test_subdomain_check_statuses_and_messages`; `test_subdomain_check_returns_the_normalised_name`; `test_subdomain_check_is_limited_to_60_in_ten_minutes`.
- `test_create_answers_201_with_the_site_shape` (exact key set; `id` starts `site_`; no primary key anywhere in the body).
- `test_create_refuses_basic_and_every_other_template` (the legacy gap: a direct call asking for `standard` gets 400 `invalid_template`).
- `test_create_refuses_a_missing_template` and `test_create_refuses_unknown_keys` (`account`, `operator_hold`, `business_name`, `id`); nothing is created.
- `test_create_subdomain_errors` (400, 400, 409 with `fields.subdomain`).
- `test_a_repeated_create_is_409_site_limit_reached_and_the_list_still_has_one_site`.
- `test_create_is_limited_to_10_a_day`.
- `test_list_shows_only_my_sites_oldest_first`; `test_list_paginates_with_a_cursor`; `test_a_cursor_from_another_account_is_400`; `test_list_hides_a_deleted_site`.
- `test_detail_and_patch`; `test_patch_refuses_template_subdomain_and_unknown_keys`; `test_patch_reports_all_invalid_fields_together`.
- `test_business_profile_get_and_patch`; `test_site_business_name_follows_the_profile`.
- `test_host_is_null_without_a_parent_host_and_built_from_it_otherwise`.
- `test_openapi_lists_the_site_paths`.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run python manage.py test apps.sites.tests.test_api_sites`
Expected: FAIL with 404 answers.

- [ ] **Step 3: Write the implementation**

Request shapes set `model_config = {"extra": "forbid"}` so an unknown key becomes the API's 400 `invalid_request`. `SitePatch` and `BusinessProfilePatch` have every field optional and the view passes `payload.dict(exclude_unset=True)` to the service. The list view calls `page(Site.objects.filter(account=request.account, deleted_at__isnull=True), limit=limit, cursor=cursor, prefix="site")`. Views hold no rule: they call `services` and shape the answer.

- [ ] **Step 4: Run them and watch them pass, then commit**

```bash
uv run python manage.py test apps.sites
git add apps/sites config/api.py && git commit -m "feat: add the site endpoints"
```

---

### Task 8: Onboarding endpoint and tenant isolation

**Files:**
- Modify: `apps/sites/api.py`
- Create: `apps/sites/tests/test_api_setup.py`, `apps/sites/tests/test_isolation.py`

- [ ] **Step 1: Write the failing tests**

`test_api_setup.py`:
- `test_the_shape_for_a_fresh_account` (exact key set).
- `test_create_then_complete_in_order`: `not_started`, then `in_progress` with `first_site`, then `minimum_complete` after the contacts and the business name are saved; both stamps set once.
- `test_a_new_sign_in_on_another_client_returns_the_same_setup` (the resumed case across devices).
- `test_a_client_cannot_mark_onboarding_complete`: `state`, `minimum_completed_at`, `missing` or `onboarding_complete` in any write body is refused and changes nothing; `POST`, `PATCH`, `PUT` and `DELETE` on the setup path answer 404 or 405 and change nothing.
- `test_the_get_ignores_any_account_or_site_selector` (`?account=`, `?site=` of another account).
- `test_can_create_site_is_true_only_when_not_started`.
- `test_templates_lists_sante_only`.
- `test_a_removed_first_site_is_blocked`.
- `test_the_get_writes_nothing`.

`test_isolation.py` (two signed-in clients, A and B, each with a site):
- `test_another_accounts_site_is_404_on_every_site_route_and_method`: built by walking the router's registered paths that contain `{site_id}`, so a route added later is covered or the test fails. The body equals the body for a made-up id and for a deleted site.
- `test_a_patch_by_another_account_changes_nothing` (site and business profile rows compared before and after).
- `test_the_list_never_contains_another_accounts_site`.
- `test_setup_never_returns_another_accounts_first_site`.
- `test_a_site_id_in_the_body_or_query_is_never_authorization`.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run python manage.py test apps.sites.tests.test_api_setup apps.sites.tests.test_isolation`

- [ ] **Step 3: Write the implementation**

The view calls `onboarding.evaluate(request.account)` and adds `can_create_site`, `templates.offered()` and `settings.SITE_PARENT_HOST`.

- [ ] **Step 4: Run every check CI runs, then commit**

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
DJANGO_DEBUG=false DJANGO_SECRET_KEY=local-check-only-long-random-value-0123456789abcdef uv run python manage.py check --deploy
git add apps/sites && git commit -m "feat: add the onboarding state endpoint and isolation tests"
```

---

### Task 9: PR B, staging verification, and the record

- [ ] **Step 1: Update the documents**

`docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`: add an amendment in section 12 for whatever Coach Ed decides under "Decisions needed" (the setup endpoint in section 8, the extra business profile fields and `Onboarding` in section 6, the trial-start rule in section 7). `README.md` or `.env.example`: `SITE_PARENT_HOST`.

- [ ] **Step 2: Open PR B**

"feat: site, subdomain and onboarding endpoints [z8uq9mach6]". The description names the task and its URL, lists each acceptance criterion with the test that proves it, and states what was verified where.

- [ ] **Step 3: APPROVAL. `SITE_PARENT_HOST` on staging**

A Railway variable change. Needs Coach Ed's explicit approval. The value is `storefronts-staging.negosyonline.com` (Decision 6). Until it is set, `host` is `null` and `site_host_suffix` is empty; everything else works.

- [ ] **Step 4: Verify on staging**

After the deploy: `/healthz/` shows the merge commit; `/v1/openapi.json` lists the site paths; an unauthenticated `GET /v1/sites/` answers 401. A signed-in walk-through on staging creates a real Site row and a subdomain that can never be reissued, so it is done only if Coach Ed asks for it, with the name he chooses.

## Done when

- Every acceptance line of `z8uq9mach6` has a passing test: create and resume only one's own first site; completion derived from persisted fields; cross-account reads and writes refused with 404; concurrent and resumed creation leaves one Site; tenant isolation on every site route.
- A request for any template other than Santé is refused by the API.
- CI is green on both PRs, including the PostgreSQL job that runs the threaded tests.
- `sites.0001_initial` is applied on staging with Coach Ed's approval and recorded in the runbook.
- No file under `apps/accounts/` changed.

## Decisions needed

Only what the task, the spec and the rulings do not settle. Each has a recommendation.

Status on 2026-11-10: decisions 2, 4, 5, 6 and 7 are settled by the coordinator, as noted under each. Decisions 1 and 3 are with Coach Ed, because they change the approved spec or an accepted screen.

1. **Where the trial start lands before billing exists.** *Open: with Coach Ed.* Spec section 7 says creating the first site also creates its `Subscription`, with trial dates copied from `Account.created_at`. Billing has no owner and no tables.
   - **Recommended: record nothing in slice 3.** The trial dates are fully determined by `Account.created_at` (14 days), which already exists. Slice 4 adds `Subscription`, a call inside `create_first_site`'s transaction, and a one-time backfill for sites created before it, computed from the same dates. No information is lost, and billing's table is designed by whoever owns billing.
   - Alternative: create a minimal `apps/billing` with `Subscription` now. It commits the billing schema before the billing values in spec section 7 are confirmed, and puts unowned code in this slice.
   - Consequence of the recommendation: until slice 4, the API cannot say "trial active" or "trial ended", and site creation is not limited by the trial (which matches spec section 7).

2. **The onboarding state endpoint.** Spec section 8 lists no endpoint for it, and the dashboard needs one.
   - **Recommended: a new `GET /v1/account/setup/`**, served by the sites app, with the `Setup` shape above; it also replaces legacy's `GET /platform/eligibility/`. It keeps the path the dashboard already calls.
   - Alternative: fold it into `GET /v1/me/`. That makes the accounts app, owned by the other session, read site tables on every page load.
   - Needs a one-line amendment to spec section 8.
   - **Settled by the coordinator, 2026-11-10: yes.** `GET /v1/account/setup/` is served by the sites app, with the spec section 8 amendment in Task 9 Step 1. Source: task `z8uq9mach6` requires a persisted, resumable onboarding state; the dashboard already calls this path; `/me/` belongs to the staging proof session.

3. **`GET /v1/dashboard/`.** *Open: with Coach Ed.* Its legacy payload is `trial`, `referral` and `kpis`: billing, referrals and commerce figures, none of which exist here.
   - **Recommended: do not build it in slice 3.** The Overview decides its mode from `GET /account/setup/` and takes the site label from it. The trial strip returns with slice 4 (`GET sites/{site_id}/billing/`), the referral link with slice 7, sales figures with commerce.
   - Consequence: until slice 4 the Overview shows no trial strip.

4. **Where the business name is written.** The spec's data model puts it in `SiteBusinessProfile`. The dashboard's Step 3 saves business name and both contacts in one call.
   - **Recommended: keep it in `SiteBusinessProfile`**, written only through `PATCH sites/{site_id}/business-profile/`, and returned read-only on `Site`. Step 3 makes two calls (it already makes two on the Business Settings page). One writer per field, as the spec's model says.
   - Alternative: let `PATCH sites/{site_id}/` also accept `business_name`. One call for Step 3, but two endpoints write one field.
   - **Settled by the coordinator, 2026-11-10: the recommendation.** `business_name` is written only through `PATCH sites/{site_id}/business-profile/` and is read-only on `Site`, as the spec's data model says. The frontend builder makes Step 3 two calls.

5. **Fields the dashboard needs that the spec's data model lacks.** The spec lists `SiteBusinessProfile` as "business name, TIN, DTI number, address, shipping estimates" and `Site` social links without naming them. The copied Business Settings page (`business.js`) also reads and writes `registered_name`, `bir_rsn`, `complaints_contact_name`, `privacy_contact_email` and `shipping_estimates_confirmed`.
   - **Recommended: carry all of them now**, as in the model above, with `instagram_url` and `tiktok_url` as the two social links. They are in one new table, so adding them later would cost a second migration and approval.
   - Not carried either way: the BIR seal image (needs private storage).
   - Also new and not in the spec: the `Onboarding` table (first site and two timestamps per account). It is what makes resumed and concurrent creation safe without touching `apps/accounts`.
   - **Settled by the coordinator, 2026-11-10: carry them.** The extra business profile fields, `instagram_url` and `tiktok_url`, and the `Onboarding` table are in. Reason: the Business Settings screen is accepted work that already uses them, and one migration is better than two. They remain part of the migration Coach Ed approves with this plan. The BIR seal image stays out.

6. **The staging value of `SITE_PARENT_HOST`.** The builder brief gives `negosyotest.com` for local testing. No document names the parent host for subscriber sites on staging or production.
   - **Recommended: Coach Ed names it when Task 9 Step 3 comes up.** Nothing in the code depends on the value.
   - **Settled by the coordinator, 2026-11-10: the staging value is `storefronts-staging.negosyonline.com`.** Source: the `negosyoonline-frontend` README, "Storefronts staging domains" (the `*.storefronts-staging.negosyonline.com` wildcard has a valid certificate). Setting the Railway variable still needs Coach Ed's approval at Task 9 Step 3. Local stays `negosyotest.com`. Production is not set.

7. **Operator actions on the ledger** (rename a site's subdomain, release a retired name, assign a brand-reserved name). Spec section 5 requires an audit entry for a release, and `OperatorAuditEntry` has not been built.
   - **Recommended: a separate small task after this slice.** This plan leaves the admin read-only, so no name can be released or reissued by accident.
   - **Settled by the coordinator, 2026-11-10: out of this plan.** The admin stays read-only. The coordinator raises the follow-up task with Coach Ed.

## Self-review against the spec

- Section 5: site-owned rows carry `site`; every `sites/{site_id}/` route goes through `site_for` and answers 404; public ids only; subdomain rules and reserved names carried; never reissued through the ledger; four availability answers; soft delete only (no delete path exists). The `Host` header rule belongs to the by-host lookup, which is deferred.
- Section 6: `Site` and `Subdomain` as listed. `SiteBusinessProfile` has more fields than listed (Decision 5). `Onboarding` is additional (Decision 5). Nothing from the "not rebuilt" list is present.
- Section 7: Site, Subdomain and profile are created in one transaction. The Subscription is not (Decision 1).
- Section 8: the Sites row is implemented in full; one extra endpoint is proposed (Decision 2); list pagination uses a cursor; errors use the one shape.
- Section 9 item 3: "trial subscription start" and "public host lookup" are deferred, with reasons, in "Scope of this plan".
- Task `z8uq9mach6`: many sites per account are preserved in the schema, the queries and the URLs (`SITES_PER_ACCOUNT_LIMIT` is a setting, and no query assumes one site); onboarding state is explicit, resumable and derived; ownership checks are listed per route; the template is the capability source.
