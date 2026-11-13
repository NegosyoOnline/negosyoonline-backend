# Billing HQ-RA: receiving accounts set in HQ Finance (plan)

ClickUp z8uq9metz0 (parent z8uq9m8h46, BILLING). Plan only: no code, no migration file, no PR. Written 2026-11-13 from `origin/main` d229a28 (backend) and `origin/main` a51b797 (frontend, read only).

Source: Coach Ed, Manager session, 2026-11-13, on setting `BILLING_TRANSFER_ACCOUNTS` in Railway: "this must be in hq dashboard... there we can set all our billiing related." The Manager asked for the smallest change.

## 1. What the code does today

| Fact | Where |
|---|---|
| The accounts are one setting, a JSON string, read once at import | `config/settings.py:159-161` |
| It is parsed on every call: a list of objects with exactly `kind` (`bank` or `ewallet`), `provider_name`, `account_name`, `account_number`, each a non-blank string; anything else logs an error and counts as none | `apps/billing/plan.py:87-88` (constants), `plan.py:119-145` (`transfer_accounts()`) |
| `collection_live()` = `BILLING_ENABLED` and `BILLING_BANK_TRANSFER_ENABLED` and `transfer_accounts()` and `proofs.available()`, in that order, so a switch that is off short-circuits before anything else is read | `plan.py:148-159` |
| The check `billing.E001` calls `plan.transfer_accounts()`; `billing.E002` is the missing proof store. It is registered with `@register()` (a normal check) so it runs on `manage.py check`, `migrate` and `runserver` | `apps/billing/proofs.py:52-72`, imported at `apps/billing/apps.py:9` |
| The only place the accounts reach a Subscriber: `payment_options()` puts `plan.transfer_accounts()` in the answer; `GET /v1/sites/{site_id}/billing/payment/` returns it as `transfer_accounts` (schema `TransferAccountOut`: the four fields) after the 404 gate on `collection_live()` | `apps/billing/services.py:1365`, `apps/billing/api.py:289-293`, `:313`, `:353-357` |
| No email, statement, invoice or export carries an account (`grep` of `provider_name`, `account_name`, `account_number` outside `apps/payments`, `apps/shop`, `apps/pack` finds only `plan.py`, `api.py`, `services.py` for billing). The B2a-E emails do not name the account; they link to `{DASHBOARD_URL}/settings/billing/` | `apps/billing/notices.py` (no match) |
| `BankTransferPayment` stores no account: no field says which account the Subscriber was shown or paid | `apps/billing/models.py:147-212` |
| `collection_live()` is also the gate for: the sweep and its due-lists (`services.py:200`, `:437`, `:461`, `:1586`), the 404 of every owner and Finance payment route (`services.py:783`, `api.py:355`, `staff.py:59`), the billing emails (`notices.py:421`), and `methods.active_at` (`methods.py:20`) | as listed |
| The existing Finance routes are all closed with 404 while `collection_live()` is false (`staff.py:54-66`) | `apps/billing/staff.py:54` |

Two consequences drive the design:

1. **Chicken and egg.** The Finance routes answer 404 until an account exists. The new account routes therefore cannot use that gate, or Coach Ed could never enter the first account.
2. **Deploy before migrate.** The `api` service does not run migrations (CLAUDE.md, deployment). After the backend deploys, the new table does not exist until the Supervisor runs the migration. A read of the table in that window would fail. The order of the checks in `collection_live()` (switches, then store, then the table last) makes this safe as long as `BILLING_BANK_TRANSFER_ENABLED` is not yet on, which it is not (owner-emails plan z8uq9mbbkj, section on release order).

## 2. Model

One new model, `ReceivingAccount`, in **`apps/billing/models.py`**. Billing owns payments and the accounts exist only to be paid into; a new app would add a migration chain, an app config and a test package for one table. It is NegosyoOnline's own account, so it must not reuse `apps.payments.PaymentAccount` (that is a Subscriber's store account, with personal-data retention rules at `apps/payments/retention.py:13`).

| Field | Type | Rule |
|---|---|---|
| `public_id` | `CharField(40, unique)`, `new_public_id("rca")` | The staff routes name a row by it. Never sent to a Subscriber. A later ledger `BankAccount` (FAS-03 note, `docs/superpowers/specs/2026-11-11-fas-03-ledger-data-model-and-access.md:263`) can point at it. |
| `kind` | `CharField(10)`, choices `bank`, `ewallet` | Same two values and spelling as `plan.ACCOUNT_KINDS`, so the Dash payload does not change. |
| `provider_name` | `CharField(60)` | Same limit as `apps/payments/models.py:35`. |
| `account_name` | `CharField(120)` | Same limit as `apps/payments/models.py:37`. |
| `account_number` | `CharField(40)` | Same limit as `apps/payments/models.py:38`. The service accepts digits, spaces and dashes, 6 to 40 characters. |
| `active` | `BooleanField(default=True)` | Only active rows reach a Subscriber or count for `collection_live()`. |
| `sort_order` | `PositiveSmallIntegerField(default=0)` | Rows are shown by `sort_order`, then `pk`. |
| `created_at`, `updated_at` | `DateTimeField(auto_now_add)`, `DateTimeField(auto_now)` | |
| `created_by`, `updated_by` | `ForeignKey(settings.AUTH_USER_MODEL, PROTECT, related_name="+")` | Who last changed it. Every change is also in the operator audit (section 4). |

Constraints: `CheckConstraint` for `kind` in the two values; `CheckConstraint` that the three text fields are not blank; `UniqueConstraint(provider_name, account_number)` so one account is never listed twice. `delete()` raises `RuntimeError("A receiving account is kept and cannot be deleted.")`, as `BankTransferPayment.delete` does (`models.py:238-239`). There is no hard delete: an account stays for the audit trail and for reconciling an old proof.

**Migration: exactly one, `apps/billing/migrations/0009_receiving_account.py`**, a `CreateModel` with its constraints and no data step. It depends on `0008_transfer_sender_details`. No row is created by any migration, seed or fixture: Coach Ed types the accounts in HQ (the "none is in code" rule, runbook `billing.md:32`). It is additive, so the previous release still runs against the migrated schema (`docs/runbooks/migrations.md`, Notes).

**Personal or secret rules.** None of the Subscriber retention rules applies: it is not Subscriber data and no rule in `docs/runbooks/retention.md` touches billing records (line 49). It is not a secret (it is what a Subscriber is told to pay into), so it is stored as plain text, like the issuer profile, and no rule blanks it. Two exposures to state plainly:

- The full account number and account name are shown to **any signed-in Subscriber** on Dash Billing while bank transfer is live, and to staff in HQ. That is the exposure the B2a plan already approved ("shown only to a signed-in Subscriber on the Billing page", `2026-11-11-backend-billing-b2a-bank-transfer-launch-promo.md:336`). It is not in any public route.
- If the receiving account is in an individual's name (Coach Ed's own), `account_name` is that person's name and is shown to Subscribers. That is his account and his call; no code change, but it is worth his knowing (owner table, D4).

The audit rows (section 4) hold the account values. Audit entries of these actions are declared `KEPT` and are never blanked; the values are business data, not personal data of a third party.

## 3. Reads

Changes inside `apps/billing/plan.py`:

- `transfer_accounts()` returns the **active** rows, ordered `sort_order, pk`, as the same list of four-key dicts (`kind`, `provider_name`, `account_name`, `account_number`). The Dash payload and `TransferAccountOut` do not change. The JSON parsing, `_bad_accounts()`, `ACCOUNT_FIELDS` (kept only if the staff validation reuses it) and the `json` import go.
- `collection_live()` keeps its order: `BILLING_ENABLED`, `BILLING_BANK_TRANSFER_ENABLED`, `proofs.available()`, then `transfer_accounts()` last. Switches off means no query, as today (the existing "switched off reads no new table" tests keep their meaning). With the switches on and no active row it is false: bank transfer is not offered, the Finance payment routes answer 404, the emails stop, exactly as with an empty variable. Fail-closed is unchanged. A cheaper `ReceivingAccount.objects.filter(active=True).exists()` is used inside `collection_live()`; the dict list is built only for the payload.
- `apps/billing/services.py:1508` calls `collection_live()` inside a loop over subscriptions. Hoist it above the loop (one line) so the table is read once per cancel batch. The other callers call it once per request or once per task run, except `_dates_after` (`:437`), which runs per subscription inside the sweep: one tiny indexed `exists()` each is acceptable and is not cached.
- **No cache.** A cached answer could keep sending money to an account staff just deactivated. One extra query on a table of a few rows is the right price.

**Env var: dropped, not kept as a fallback.** A fallback is not trivial or safe: if staff deactivate every row, a fallback would bring the variable's accounts back, which breaks "with no active account, bank transfer stays off" (the acceptance line) and gives two places to look when money goes to the wrong account. So `BILLING_TRANSFER_ACCOUNTS` is removed from `config/settings.py:159-161`, from `plan.py`, from `proofs.py` and from the tests, and from `docs/runbooks/billing.md` (lines 23, 32, 37 and the "Order on staging" paragraph). A stale variable left in Railway is ignored. Coach Ed has not set it yet (owner-emails plan z8uq9mbbkj: not set on staging), so nothing is lost; the Manager should tell him not to set it and, only if it was set, delete it (a Railway variable change, his approval).

**The system check.** `billing.E001` cannot query the database in a normal check: checks run at `migrate` time, before the table exists, and a database read at import breaks `makemigrations --check` in CI. Plan:

1. `proofs.check_bank_transfer` keeps only `billing.E002` (no proof store): it reads settings only, so it stays a normal check.
2. `billing.E001` moves to a new check `check_receiving_account(app_configs, databases=None, **kwargs)` in `apps/billing/proofs.py`, registered with `@register(Tags.database)`. Django (5.2 here) runs database-tagged checks only on `manage.py check --database default`, never on `migrate`, `runserver` or the CI `check` and `check --deploy` steps (`.github/workflows/ci.yml:98-101`). It returns `billing.E001` ("bank transfer is on but no active receiving account is saved") when `BILLING_BANK_TRANSFER_ENABLED` is on and no active row exists. The builder confirms against the Django 5.2 behaviour before relying on it.
3. The everyday readiness signal is not a check but the HQ page itself: the list route (section 4) returns a `bank_transfer` block, and the page says in words why Subscribers cannot pay yet (switch off, no active account, no proof store). `/healthz/` is untouched (CLAUDE.md baseline).
4. The runbook gets one line: after changing accounts or the switch, run `manage.py check --database default` on `api` if in doubt.

**Proofs against an account later changed or deactivated.** A proof does not reference an account (section 1), so deactivating or editing a row never orphans, hides or changes a proof, a payment, a cycle or a statement. The proof does not snapshot the account either, and this plan does not add that: it would change `BankTransferPayment` and the submit API, which is payment behaviour outside this task, and which FAS-03 already owns ("which receiving account got a given transfer", `fas-03-ledger-data-model-and-access.md:371`). What staff can still answer after an edit is "what was the Subscriber shown at 14:05?": the audit entry of every add, edit and activate/deactivate keeps the before and after values and the time. Owner table D3 asks him to confirm this stays out of scope.

## 4. Staff routes

In `apps/billing/staff.py`, added to `ROUTES` (line 287) and so to `/staff/v1/` through the existing `billing_staff.ROUTES` registration (`apps/operators/staff_views.py:201-203`). Nothing in `staff_views.py` changes. Every route is a POST with a small JSON body, as the others; the origin, method and the two-step session are already checked in `staff_views` (`_for_staff`, `_gate`), so a route cannot be reached without a verified staff session. Business logic goes in a new `apps/billing/accounts.py` (so `staff.py` keeps importing nothing from `apps.operators`, which `test_the_finance_module_imports_nothing_from_the_staff_routes` enforces; `services.py` already imports `audit`).

| Path | Body | Answer |
|---|---|---|
| `finance/accounts/list/` | `{}` | `{ "items": [account...], "bank_transfer": {"switch_on": bool, "store_available": bool, "active_accounts": int, "live": bool} }` |
| `finance/accounts/add/` | `kind`, `provider_name`, `account_name`, `account_number`, optional `sort_order`, optional `active` (default true) | the new account (200 with the row) |
| `finance/accounts/edit/` | `account` (public id) and any of the four text fields and `sort_order` | the row |
| `finance/accounts/active/` | `account`, `active` (bool) | the row |

An `account` in an answer: `id`, `kind`, `provider_name`, `account_name`, `account_number` (full, staff only), `active`, `sort_order`, `created_at`, `updated_at`, `updated_by`.

- **Gate.** A new `_accounts_route` wrapper (like `_route` and `_usage_route`, `staff.py:54-82`) answers 404 `not_found` only while `settings.BILLING_ENABLED` is off, and reads no billing table then. It does **not** use `collection_live()` (chicken and egg). It turns a service refusal into the one error shape, as `_route` does. It assumes `BILLING_ENABLED` is on in staging; the plan cannot read Railway variables, so the Supervisor confirms that before the visual test.
- **Validation (in `accounts.py`, field errors in the `invalid_request` shape of `statements/services.py:27-45`).** `kind` is `bank` or `ewallet`. Text is a string, stripped, not blank, within the limit. `account_number` matches `^[0-9 -]{6,40}$`. `sort_order` is a whole number 0 to 999. Unknown keys are ignored. A duplicate (`provider_name`, `account_number`) answers 409 `account_exists` ("Edit or reactivate that account instead"). A body over `MAX_BODY` is the same empty-object case as the other routes.
- **Writes.** Each write is in `transaction.atomic()`, locks the rows (the table is a handful of rows, so lock them all in `pk` order as `_lock_issuers` does, `statements/services.py:77-81`) and writes its audit entry in the same transaction, so an account cannot change without its audit row.
- **Last active account (choice C7, owner table D2).** Deactivating the last active account while `BILLING_BANK_TRANSFER_ENABLED` is on is refused with 409 `last_active_account`: "Add the new account first, then switch this one off." Reason: with no active account `collection_live()` is false and every Finance payment route returns 404, so proofs waiting for review could not be confirmed or rejected. To stop taking payments, the Railway switch is the tool. With the switch off the deactivation is allowed.
- **Audit.** Four new actions declared `KEPT` in `apps/operators/audit.py:12-` beside `billing_issuer_set` (line 53): `billing_receiving_account_added`, `billing_receiving_account_edited`, `billing_receiving_account_activated`, `billing_receiving_account_deactivated`. Each is a literal string in a `audit.record(...)` call (the AST test in `apps/operators/tests/test_audit.py` requires that). `target_type="receiving_account"`, `target_id=public_id`, and `detail` holds `{"before": {...}, "after": {...}}` of the four text fields, `sort_order` and `active` (add: only `after`). Edit that changes nothing writes no row and returns the row.
- **No delete route.** There is no hard delete anywhere, in the model, the service or the routes.

## 5. HQ frontend page (`/Users/edgeronilla/Dev/negosyoonline/negosyoonline-frontend`, read only)

The HQ app is static HTML and plain scripts in `apps/hq/public` (`apps/hq/next.config.ts` routes and headers; `AGENTS.md` only warns about Next.js). The closest page is `finance-issuer.html` + `assets/js/finance-issuer.js` (issuer card, book list with an Active switch per row, add form, closed and down views, `NOHq.call`, `NOFin` helpers from `finance-common.js`), tested by `tests/finance-issuer.test.mjs` with the `tests/page.mjs` harness.

New or changed files (frontend PR):

1. `apps/hq/public/finance-accounts.html`: same chrome as the other Finance pages, `<title>Receiving accounts | NegosyoOnline HQ</title>`, sub-nav with the new "Accounts" link marked current, `h1.sr-only` "Finance, receiving accounts". Views: `boot`, `v-main`, `v-closed` ("Receiving accounts are not switched on."), `v-down` (Try again). In `v-main`: a **status banner** from `bank_transfer` ("Subscribers can pay by bank transfer" or the reason they cannot), a **list** (`ol.list` of `li.batch`: kind and provider as title, account name, full account number, an Active switch `role="switch"` and an Edit button), an **Add an account** form (kind select, provider, account name, account number, order), and an edit form in a dialog or inline, as the issuer page does for its forms. Copy near the edit form: changing a number takes effect for every Subscriber at once; to replace an account, add the new one, then switch the old one off.
2. `apps/hq/public/assets/js/finance-accounts.js`: calls the four routes through `NOHq.call("POST", "finance/accounts/<name>/", body)`. The page decides nothing: it shows the backend's refusals (`invalid_request` field errors, `account_exists`, `last_active_account`) in words, follows 404 to `v-closed`, other failures to `v-down`, and `F.left(res)` for a lost session, as `finance-issuer.js` does. Nothing is stored in the browser and nothing is put in an address.
3. `apps/hq/next.config.ts`: add `{ source: "/finance/accounts/", destination: "/finance-accounts.html" }` to the rewrites and `{ source: "/finance-accounts.html", destination: "/finance/accounts/" }` to the redirects. `menu.js` needs no change: the Finance entry is `within: true` and covers every `/finance/` address.
4. The sub-nav line (`<nav class="set-tabs fin-sub">`) of the five existing Finance pages (`finance.html`, `finance-usage.html`, `finance-issuer.html`, `finance-invoices.html`, `finance-reconcile.html`) gains `<a href="/finance/accounts/">Accounts</a>` (the nav is asserted by `tests/rules.test.mjs:89`; that test is updated). Page title and the menu label are "Receiving accounts"; the tab is the short "Accounts" so the tab row still fits on a phone.
5. Cache-busting `?v=` strings follow the other pages.

**Design rules (Coach Ed, focus.test.mjs).** Focus border light blue as on dash (inputs use the existing `.input:focus` rule, no new colour, no gold); card titles and headings are never focusable: no `tabindex` on any heading, no script gives a heading focus. The page adds no CSS colour. The Add and Edit controls use the existing `no-btn` classes. Both new files are added to the list in `tests/focus.test.mjs` ("the Finance usage and issuer pages follow the focus rule").

**Frontend tests** (`apps/hq/tests/finance-accounts.test.mjs`, same harness and fixture style as `finance-issuer.test.mjs`, only made-up sample values): asks who is signed in and then lists; renders the status banner for each of the three reasons; empty list text; Add sends exactly the typed fields and repaints, with field errors shown for `invalid_request`, `account_exists` text; Edit sends only the account id and changed fields; the Active switch sends `{account, active}`, disables while busy, repaints from the answer, shows the `last_active_account` message and reverts; 404 shows `v-closed`; 500 or offline shows `v-down` and Try again retries; a 401 goes through `F.left`; no call carries a value in the URL (every call is a POST with the path only); no write to `localStorage` or `sessionStorage`; headings are not focusable; the five old pages and the new one all list the Accounts tab; `next.config.ts` has the rewrite and the redirect.

## 6. PR count and release order

Two PRs, one per repository, each one inside its repository's release round (builders open none; the Release Builders combine ready branches, `.claude/briefs/builder.md` "Ready branches, no PR").

1. **Backend branch** `feat/z8uq9metz0-receiving-accounts` (model, migration `0009`, reads, check, staff routes, audit actions, tests, runbook). Ready, then reviewed by the Supervisor, then in the next backend release round. After the merge and the Railway deploy of `api`: the Supervisor runs `migrate --plan`, then `migrate --noinput` on staging under standing approval 3 (`CLAUDE.md`; the plan above specifies the migration), then verifies `migrate --plan` shows nothing pending and the health URL answers (`docs/runbooks/migrations.md` steps 1-5). Production is not covered and is not touched.
2. **Frontend branch** `feat/z8uq9metz0-receiving-accounts-page`, merged and deployed **after** the backend is merged and migrated on staging. Before that, the new page's calls would hit routes that do not exist (404 shows the closed view, harmless but wrong). The two can be built in parallel because the routes and bodies are fixed in section 4.
3. Coach Ed signs in to HQ staging, opens Finance, Accounts, and enters the account. The Manager (or Coach Ed) then turns `BILLING_BANK_TRANSFER_ENABLED` on for staging `api` and `worker` (the switch is not a code change; its own approval, per `billing.md:21` and the Manager's staging-switch standing approval 512065 where it applies). The proof-store token `PRIVATE_BLOB_READ_WRITE_TOKEN` is a separate item already on Coach Ed (ruling 512065). Once the switch is on, `billing.notices` and the sweep begin; run `billing_sweep --dry-run` first (`billing.md`, last paragraph).

Do not turn `BILLING_BANK_TRANSFER_ENABLED` on before migration `0009` is applied (the first read of the table would fail).

## 7. Tests

Backend (`apps/billing/tests`, plus the files that use `transfers_on`):

- **Helper.** `base.transfers_on()` (`tests/base.py:109-124`) stops overriding `BILLING_TRANSFER_ACCOUNTS` and creates one active `ReceivingAccount` with the same made-up values (`TEST_ACCOUNTS`, `base.py:48-51`) and removes it on exit; `TEST_ACCOUNTS` becomes a dict list. About 17 test files use the helper (`owner_base.py`, `test_staff_finance.py`, `test_transfer*.py`, `test_annual.py`, `test_notices.py`, `test_concurrency.py`, `test_usage_collection.py`, invoices, statements, accounts export) and need no further change because they go through it; the `SimpleTestCase` users must become `TestCase` (`test_proof_store.AvailableTests`).
- **Model and migration.** Blank text and a bad `kind` are refused by the database constraints; the duplicate pair is refused; `delete()` raises; fresh PostgreSQL applies `0009` and `makemigrations --check` is clean (CI).
- **Reads.** Replace `test_transfer.py:534-550` (the JSON cases) with: no row, an inactive row and only inactive rows give `transfer_accounts() == []` and `collection_live()` false; one active row gives true; order is `sort_order, pk`; deactivating the only row turns `collection_live()` false at once (no cache); switches off run **zero** queries on the accounts table (`CaptureQueriesContext`); `test_proof_store.test_collection_is_live_only_with_a_store` uses a row.
- **Dash payload.** `test_transfer_api.py:102` unchanged in shape: exactly the four keys, only active rows, no `id`, no `sort_order`, no `active`.
- **Check.** `test_transfer.py:581-587` becomes: `check_bank_transfer` returns only E002; `check_receiving_account` returns `billing.E001` with the switch on and no active row, nothing with a row, nothing with the switch off; it is not run by the plain `call_command("check")` (so `migrate` never queries) and does run with `--database default`.
- **Staff routes** (`test_staff_receiving_accounts.py`, in the style of `test_staff_finance.py`): the route set and `staff_views.ROUTES` count test are updated (`test_staff_finance.py:70-90`); each route is POST only; unsigned, one-step and wrong-origin calls are refused exactly as the others; list, add, edit, deactivate, reactivate happy paths; every field validation; duplicate 409; unknown `account` 404; edit with no change writes no audit row; each write writes exactly one audit entry with the right action, `target_id`, before and after; an audit write failure rolls the change back; `last_active_account` with the switch on and allowed with it off; two concurrent deactivations of two active rows leave one (PostgreSQL, like `test_concurrency.py`); closed with 404 when `BILLING_ENABLED` is off and no table is read; **open when `collection_live()` is false** (the chicken-and-egg case); the list's `bank_transfer` block for each of its reasons.
- **Audit.** The four actions are declared `KEPT` (`test_audit.py` literal test passes), and a `personal=` value on them is refused.
- **Isolation.** (a) The Subscriber billing route returns only active rows and none of the staff fields; (b) every `/staff/v1/finance/accounts/*` path refuses an anonymous client, a signed-in Subscriber session (their cookie is not a staff session), a staff session that has not passed the second step, and any other origin; (c) no route under `/v1/` or the public host reads or writes the table: a test walks the Ninja routes and asserts none is a write method that touches `ReceivingAccount` (an AST check that only `plan.py`, `accounts.py`, `staff.py` and `models.py` import the model), and the existing two-owner snapshot (`owner_base.row_snapshot`, `owner_base.py:157-163`) shows no Subscriber call changes any row of the new table; (d) Subscriber A cannot see anything Subscriber B-specific because the table has no owner column and the answer is identical for both.
- **Docs test.** None; `billing.md` and `migrations.md` are edited by hand and reviewed.

Frontend: as listed in section 5.

Local checks before "ready": backend full suite on SQLite and on throwaway PostgreSQL with Redis, `manage.py check`, `check --deploy`, `makemigrations --check`, migrations on a fresh PostgreSQL (release brief); frontend lint, tests and build for the `hq` app with the CI's Node and pnpm. The Supervisor's local visual test: backend on `http://ggwp.negosyotest.com:8000` as in the B2a PR 4 packet, HQ at `http://hq.negosyotest.com:3003`, with `DEBUG`, `BILLING_ENABLED=1`, `BILLING_BANK_TRANSFER_ENABLED=1` and `BILLING_PROOF_DIR` set; add an account in HQ and see it on Dash Billing; deactivate it and see bank transfer disappear.

## 8. Schedule (target merge about 2026-11-21; trials end 2026-11-24 sst and 2026-11-25 Live4More)

| Date | Step |
|---|---|
| Fri 11-13 / Sat 11-14 | Plan to the Manager; approval; D1 to D4 answered |
| Mon 11-16 | Backend builder starts: model, migration, reads, check, helper change; frontend builder starts in parallel from section 4's contract |
| Tue 11-17 | Backend staff routes, audit, tests, runbook; frontend page and tests |
| Wed 11-18 | Both branches ready with local checks green; Supervisor review and local visual test |
| Thu 11-19 | Both ready branches reported to the Manager; backend release round |
| Fri 11-20 | Backend release PR merged, Railway deploys; Supervisor migrates staging and verifies; frontend release round merged after it |
| Sat 11-21 | Target merge met. Coach Ed enters the account in HQ; the switch goes on for staging |
| 11-22 to 11-23 | Buffer for fixes; a real test transfer with a clearly named test account; emails checked. Staging holds live data (ruling 509745) |

The buffer before 11-24 is two days, which is thin. If either branch slips past 11-22 the old variable still works on `main` (this change is not merged), so a trial is never blocked by this task: Coach Ed could set `BILLING_TRANSFER_ACCOUNTS` himself as his earlier ruling allowed. He should only be told that if the schedule slips.

## 9. Effort (Sonnet 5.5 builder, medium)

| PR | Work | Estimate |
|---|---|---|
| Backend | Model, migration, constraints 0.5 d; reads, check, settings and docs removal 0.5 d; `accounts.py`, routes, audit 1 d; moving the existing tests to the helper and writing the new ones 1.25 d; runbooks 0.25 d; local checks on SQLite and PostgreSQL 0.5 d | about 4 builder-days. One backend PR is about 20 to 25 CI minutes inside a shared release round. |
| Frontend | Page and script 1 d; tests 0.75 d; nav links, rewrites, focus test 0.25 d; local checks and visual test 0.25 d | about 2.25 builder-days. About 2 to 3 CI minutes. |

The two run in parallel, so the elapsed time is about 3 working days to ready.

## 10. Choices (reversible, mine)

| # | Choice | Why / source |
|---|---|---|
| C1 | Model `ReceivingAccount` in `apps/billing`, one migration `0009_receiving_account`, public id `rca_` | Smallest; billing owns it; leaves FAS free to point at it |
| C2 | Env var dropped, not kept as a fallback | A fallback breaks fail-closed (section 3); nothing is set yet |
| C3 | No cache of the answer | A stale answer could send money to a deactivated account |
| C4 | `billing.E001` becomes a database-tagged check; E002 stays normal; the HQ page shows the live reason | No query at import or migrate time |
| C5 | Routes: list, add, edit, active (one route for deactivate and reactivate); no hard delete (`delete()` raises) | The task; matches `invoices/books/active` |
| C6 | The account routes are gated on `BILLING_ENABLED`, not `collection_live()` | Otherwise the first account cannot be entered |
| C7 | Refuse deactivating the last active account while the transfer switch is on | Keeps proofs reviewable; protective only (but see D2) |
| C8 | Audit detail holds before and after values; four `KEPT` actions | The only record of what a Subscriber was shown |
| C9 | Field limits 60/120/40, number digits/space/dash 6 to 40, unique (`provider_name`, `account_number`) | Same limits as `apps/payments`; no duplicate rows |
| C10 | `kind` value stays `ewallet`; Dash payload unchanged | No frontend dash change |
| C11 | Hoist `collection_live()` out of the loop at `services.py:1508` | One query not N |
| C12 | No `account` on `BankTransferPayment`; no snapshot | Payment behaviour is out of scope; FAS-03 owns it (see D3) |
| C13 | Page title "Receiving accounts", tab "Accounts"; edit and add as forms on the issuer page's pattern | Follows the existing Finance pages; no new design |
| C14 | Backend and frontend are built in parallel; frontend merges after the backend is migrated | Order in section 6 |

## 11. Owner decisions

| # | Decision | Recommendation | Risk | Approving words |
|---|---|---|---|---|
| D1 | May staff **edit** an account number or name that Subscribers can already see? | Yes, with a before and after audit entry and a page warning ("to replace an account, add the new one, then switch the old one off") | A Subscriber who read the old number may still send to it; the money goes to the old account, so he must keep it open until proofs stop arriving | "Approved as recommended: editing allowed, audited." Alternative if he prefers: no edit of the number at all, add and deactivate only |
| D2 | May staff deactivate the **last** active account while bank transfer is on? | No: refused with the message; to stop payments use the Railway switch | If allowed, every Finance payment route answers 404 and a proof waiting for review cannot be confirmed or rejected | "Approved as recommended: refuse the last one." |
| D3 | Should a proof record which account the Subscriber paid to? | Not in this task; leave to FAS-03 (ledger) | Staff match a payment to an account by the bank statement, not by the proof; with two active accounts this is manual until FAS-03 | "Approved: not in this task." |
| D4 | If the receiving account is in his own name, the name is shown to every signed-in Subscriber (the number already was, B2a) | Note only; no code change | A personal name shown; his account, his choice | "Noted." (or he names a business account) |

## 12. What I cannot verify from here

- That `BILLING_ENABLED` is on in staging (the Railway variables were not read).
- That the proof-store token is set on staging `api` and `worker` (a separate item, ruling 512065).
- The Django 5.2 behaviour of database-tagged checks was taken from memory of the documented behaviour (added in Django 3.1), not run; the builder runs it first.
- The frontend was read from `origin/main` after a fetch, not run.

## 13. Out of scope

Anything in production; a new payment method or gateway; per-order billing; showing the account in any email; a staff-users page (one staff account, ruling 509990); a ledger `BankAccount`; any change to `/healthz/`; any new Django admin screen.
