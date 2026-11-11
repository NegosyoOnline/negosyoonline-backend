# FAS-01: inspection of the money in the new NegosyoOnline backend and frontend

ClickUp: [z8uq9md5dc](https://app.clickup.com/t/z8uq9md5dc), folder "Financial Accounting System". Date: 2026-11-11. Read-only: no code, test, migration, setting, switch, seed or deploy was changed or run, and no staging or production data was read.

**Ruling that sets the scope** (Coach Ed, 2026-11-11, recorded on FAS-00 and FAS-01): the Financial Accounting System is built in the new codebase, `negosyoonline-backend` and `negosyoonline-frontend`. The task texts that name `twcinc-app`, `app/negosyoonline/`, `PaymentAttempt`, `BillingStatement`, `PaymentEvent` or Xendit webhooks describe the legacy code and are reference for behaviour only. Nothing here was copied from TWCinc, and none of its code, ledgers or data is a source.

## 0. How to read this report

- **[I]** inspected: I read the file at the path and line given. Lines are for backend `origin/main` at `1af6d25` (2026-11-11) unless a path starts with `frontend:`.
- **[U]** unknown: not inspected, not inspectable without reading staging or production, or not decided anywhere I could read.
- **[P]** proposed: my reading of what a design would need. It is not a decision.
- Frontend paths are in the worktree `negosyoonline-frontend` at `2b84de4` ([I]). The frontend `origin/main` is at `27d42b9` (PR 45, the HQ Inbox pages). That commit was not read; it is a different feature and I do not expect it to change what section 7 says, but that is [U].
- Money in this backend is whole centavos in integer columns. Where a FAS task says "Postgres decimal", section 6.4 records the difference and does not resolve it.

## 1. What matters most (read this first)

1. **The coordinator's reading is right, with three corrections.** A confirmed `BankTransferPayment` that settles one `BillingCycle` is the replacement for the legacy `PaymentAttempt.succeeded`. [I] `apps/billing/services.py:876` (`confirm_transfer`) is the only non-test code that makes a paid cycle: the four payment functions (`first_payment_succeeded` 595, `renewed` 631, `recovered` 672, `reactivated` 690) are called from nowhere else (`_settle_transfer`, 848–873). The corrections:
   - **The amount is not on the cycle.** `BillingCycle` has no amount, plan or currency (`models.py:87-123`). The amount, plan, currency and the amount received are on `BankTransferPayment` (`models.py:159-161`, `177`), reached from the cycle through the reverse one-to-one `cycle.transfer` (`models.py:180-182`). A ledger must read the payment, never recompute a price from `plan.py` (that file holds today's prices only: `plan.py:14,35-39`).
   - **`BillingCycle.paid_at` is the operator's confirmation time, not the day the Subscriber transferred.** `staff.py:160-167` passes `now=_now()` into `confirm_transfer`, which passes it as `paid_at` (`services.py:900`, `_settle_transfer(subscription, row, now)`). The Subscriber's own date is `BankTransferPayment.transferred_on` (a date, `models.py:163`). Cash date, confirmation date and service-period dates are three different things; section 3 lists them.
   - **This stops being "one payment, one cycle" once Billing B4 lands.** PR 108 plans a confirmed `usage_only` payment with **no** cycle and replaces the constraint `billing_transfer_confirmed_in_full` (PR 108 plan section 7). PR 106 and PR 107 also add columns and a hook (section 8). So the ledger's source must be "a confirmed payment", with the cycle as an optional link, not "a cycle".
2. **No money can be collected for real today, anywhere but a developer's machine.** [I] `plan.collection_live()` (`plan.py:95-106`) needs `proofs.available()`, which is `settings.DEBUG and settings.BILLING_PROOF_DIR` (`proofs.py:56-58`, `settings.py:123-125`). The only proof store is a local folder; the comment at `settings.py:123-124` says the approved private store "is not built yet". With `DEBUG` off, `collection_live()` is false and every Finance route answers 404 (`staff.py:_route`). So whether any real payment exists is [U] and probably "none": a ledger has nothing real to post until the proof store is wired (Billing B2a follow-up, PR 107 question Q1).
3. **There is no finance-only or owner-only role.** [I] The staff routes admit any `User` with `is_staff=True`, an enrolled active `AdminIdentity` and a session that passed both steps (`two_step.py:43-53`, `staff_views.py:66-80,186-194`). Nothing in `apps/billing/staff.py` checks a group, permission or flag; the 5 Finance routes are open to every operator. Section 5.
4. **No ledger-grade table exists.** No chart of accounts, journal, period, expense, bank record, receivable, refund, credit note, invoice or statement is in `main` (section 4). Statements and the manual BIR invoice register are planned by PR 107 and belong to Billing B5, not here.
5. **Nothing in the code books a merchant's sales or funds.** [I] The only money tables are `apps/billing` (what a Subscriber pays NegosyoOnline) and `apps/catalog` (`srp_centavos`, product prices, no sale). `apps/payments` holds a Subscriber's own receiving accounts and an on/off switch, with no amount. No order, sale, wallet, payout or commission table exists (grep of every `models.py` for `centavo|amount|price|decimal|money|currency|refund|credit|invoice|receipt|vat|tax|expense|commission|payout|wallet` returned only `apps/billing`, `apps/catalog` and the account-name fields of `apps/payments`). The acceptance line "merchant sales and funds are not booked as NegosyoOnline operating revenue" holds today because nothing records them; the day an order table is built (outside FAS), it must stay out of the ledger's sources. Scope question for Coach Ed in section 11.
6. **The idempotency and transaction pattern a ledger should follow already exists and is tested.** `SubscriptionTransition` and `BankTransferEvent` are append-only with a unique `idempotency_key`; `_write` runs a change under the subscription's row lock in `transaction.atomic()` and returns the earlier row on a replay (`services.py:310-332`). Section 3.

## 2. Entity and data-flow map of money

### 2.1 Tables that hold or point at money

| Table (model) | File:lines | Money it holds | Notes |
|---|---|---|---|
| `billing_subscription` (`Subscription`) | `apps/billing/models.py:13-57` | none | One per site (`site` one-to-one, `PROTECT`). `billing_exempt` (44) means "never charged". |
| `billing_subscriptiontransition` (`SubscriptionTransition`) | `models.py:60-84` | none directly; `detail` JSON holds `cycle` and `reference`, and for a payment also `plan` and `amount_centavos` (`services.py:591-592,856-860`) | Append-only by convention; `idempotency_key` unique (78); `effective_at` (79) is when the rule says it took effect; `recorded_at` (80) is when it was written. |
| `billing_billingcycle` (`BillingCycle`) | `models.py:87-123` | **none**. `settlement` (103), `paid_at` (104), `payment_reference` (105) | `kind` trial or paid; `settlement` not_billable, pending, paid, lapsed (92-96). Unique (subscription, sequence) 109; one pending per subscription 115-119. Mutable: `recovered()` rewrites `settlement`, `paid_at`, `payment_reference` and possibly `end_at` of a pending cycle (`services.py:676-680`). |
| `billing_banktransferpayment` (`BankTransferPayment`) | `models.py:130-212` | `amount_centavos` (160, unsigned 32-bit int), `currency` (161), `amount_received_centavos` (177), `plan_code` (159) | The only record of a sale's amount. Frozen at submission (`services.py:828-841`). `reference` unique (158), equal to `public_id` unless set (203-206). Constraint 193-200: a confirmed row has a cycle, a decider, and received = due. `delete()` raises (208). Statuses submitted/confirmed/rejected/replaced (137-141). |
| `billing_banktransferevent` (`BankTransferEvent`) | `models.py:215-232` | none; `detail` JSON | Append-only; `idempotency_key` unique (227) with the pattern `bt_<code>:<payment pk>` (`services.py:744`); `recorded_at` auto (228). |
| `billing_billingnotice` (`BillingNotice`) | `models.py:235-247` | none | "Nothing writes this before the emails are built." |
| `operators_operatorauditentry` (`OperatorAuditEntry`) | `apps/operators/models.py:81-115` | none; `detail` JSON holds `payment`, `plan`, `amount_centavos`, `cycle` for a confirmation (`services.py:912-919`) | Append-only (save/delete raise). `billing_payment_confirmed`, `billing_payment_rejected`, `billing_proof_viewed`, `billing_exempt_set` are declared (`audit.py:44-47`). |
| `payments_sitepaymentaccount` (`SitePaymentAccount`) | `apps/payments/models.py:24-69` | none | A **Subscriber's** bank or e-wallet account for **their** buyers. Not NegosyoOnline's. Removed by `removed_at`, never deleted. |
| `payments_sitepaymentsettings` (`SitePaymentSettings`) | `payments/models.py:72-87` | none | Per-site on/off switch. |
| `catalog_*` (`Product`, revisions) | `apps/catalog/models.py:34,56,131` | `srp_centavos` (`PositiveBigIntegerField`) | A central price list for the Santé reference storefront. Not a sale. |

**NegosyoOnline's own receiving accounts are not in a table.** They are the environment variable `BILLING_TRANSFER_ACCOUNTS`, a JSON list of `kind`, `provider_name`, `account_name`, `account_number` (`settings.py:122`, parsed at `plan.py:66-87`). So there is no bank-account record a ledger or a bank reconciliation could point at, and the payment row does not record which account the money went to [I] (no such field in `models.py:130-212`). Which receiving account got a given transfer is [U] today.

**Where the 2,499 and 4,999 come from.** `plan.py:14,35-39` (`MONTHLY` 249,900 centavos for 1 month; `LAUNCH_PROMO` 499,900 for 6 months, a switch at `settings.py:119`). `BillingCycle` does not store them; PR 106 adds a snapshot to the cycle (section 8).

### 2.2 Data flow (what happens, in order)

```
Subscriber (owner) --POST /sites/{id}/billing/payments/--> submit_transfer           [services.py:771]
   freezes plan_code, amount_centavos, currency; stores the proof file (proofs.py);
   writes BankTransferPayment(submitted) + BankTransferEvent("submitted").
   A newer proof marks the older "replaced" (event "replaced"). No cycle, no money posted.
        |
Operator (HQ staff session, two steps) --POST /staff/v1/finance/payments/confirm/--> staff.confirm   [staff.py:160]
        |   amount_received_centavos typed by the operator (centavos, int)
        v
confirm_transfer(payment, operator, amount_received_centavos, now)                  [services.py:876]
   _write(...)  => transaction.atomic + Subscription row lock + catch_up(now)       [services.py:310-332]
     row lock on BankTransferPayment; already confirmed => returned unchanged (idempotent)
     amount must equal amount_centavos exactly, else 409 amount_mismatch
     _settle_transfer(...)  [848] => one of first_payment_succeeded / renewed /
          recovered / reactivated  (each under its own key "paid:<reference>")
          => BillingCycle(paid, paid_at=now, payment_reference=reference)
          => SubscriptionTransition(code, actor=payment, detail={cycle, reference, plan, amount_centavos})
     payment.status=confirmed, decided_at/by, amount_received_centavos, cycle
     BankTransferEvent("confirmed", actor=operator, detail={cycle})
     OperatorAuditEntry("billing_payment_confirmed", detail={payment, plan, amount_centavos, cycle})
   all in one transaction, or none.
```

Reject: `reject_transfer` (`services.py:925-959`) writes status `rejected`, the reason, an event and an audit entry; it settles nothing and takes no `catch_up`. Time-driven transitions (trial end, lapse, suspension, cancellation end) are written by `sweep()` (`services.py:1218-1254`, task `billing.sweep` every 300 s, `tasks.py:9-13`) and move no money.

## 3. Events a ledger could post from

"Source" below is the row a posting would point at. "Key today" is an idempotency key that already exists in the schema; a ledger would still need its own unique key per journal (a proposal, section 3.2).

### 3.1 Table

| # | Business event | Source row | Existing unique key | Transaction boundary today | Amounts frozen at that point | Timestamps available |
|---|---|---|---|---|---|---|
| E1 | Cash received for a subscription cycle (the "payment succeeded") | `BankTransferPayment` with `status=confirmed` and its `cycle` | `BankTransferPayment.reference` (unique, `models.py:158`); `SubscriptionTransition.idempotency_key = "paid:<reference>"` (`services.py:581-584`); `BankTransferEvent` key `bt_confirmed:<pk>` (`services.py:744`) | One `transaction.atomic()` from `_write` (`services.py:318`) holding the subscription row lock and the payment row lock; the cycle, the transition, the payment update, the event and the audit entry commit together (`services.py:887-922`). A ledger posting added inside `change()` would commit with them or not at all. [P] | `amount_centavos` and `plan_code` frozen at submission (`services.py:828-841`); `amount_received_centavos` set at confirmation and equal to the amount due by constraint (`models.py:193-200`); `currency` default PHP. **No tax, discount, fee or net amount is stored.** | `submitted_at` (when the proof was sent), `transferred_on` (Subscriber's claimed date), `decided_at` (= confirmation time), `BillingCycle.paid_at` (= the same instant), `cycle.start_at`/`end_at` (the service period), `SubscriptionTransition.effective_at` and `recorded_at`, `BankTransferEvent.recorded_at`, `OperatorAuditEntry.at` |
| E2 | Proof rejected | `BankTransferPayment` `status=rejected` | event key `bt_rejected:<pk>` | `reject_transfer`, its own atomic block (`services.py:941`) | `amount_centavos` only | `submitted_at`, `decided_at` |
| E3 | Proof replaced | `BankTransferPayment` `status=replaced` | event key `bt_replaced:<pk>` | inside `submit_transfer` (`services.py:819-821`) | `amount_centavos` | `submitted_at`, `decided_at` |
| E4 | Amount mismatch | no row: `confirm_transfer` raises 409 and writes nothing (`services.py:893-899`) | n/a | n/a | n/a | n/a |
| E5 | Cycle lapsed unpaid | `BillingCycle` `settlement=lapsed` (`services.py:448-449`) | none | inside `reactivated` / sweep | none | `start_at`, `end_at` |
| E6 | Free trial | `BillingCycle` sequence 0, `kind=trial`, `settlement=not_billable` (`models.py:87-96`) | none | at `start_subscription` (`services.py:452-486`) | none | `trial_start_at`, `trial_end_at` |
| E7 | Billing exemption (a test site) | `Subscription.billing_exempt` + audit entry `billing_exempt_set` (`services.py:1117-1138`) | none | own transaction | none | audit `at` |

Only **E1** is money in. E2–E7 move no money and need no journal; E2/E3 matter only to a bank-reconciliation view of "proofs not yet matched".

### 3.2 What a posting from E1 would have to take care of [P]

- **Idempotency.** The payment `reference`/`public_id` is already one-to-one with a confirmation, so it can be the journal's source key. A replay of a confirmed payment returns the row unchanged (`services.py:889-890`), so a ledger posting placed in the same `change()` would not run twice for a confirmed payment.
- **Atomicity.** Posting in the same transaction means a failed posting rolls the confirmation back. That is safe for the books and visible to the operator as a failed confirm. Posting afterwards (Celery) means the books can lag or silently skip, which the FAS-01 text calls unacceptable ("invisible failure"). Which one is wanted is a design choice for FAS-03/FAS-12, not decided here.
- **Competing hook.** PR 107 plans a call to `invoices.services.open_item(cycle, payment)` inside `confirm_transfer`, after the cycle exists and before the audit entry, behind its own switch (PR 107 plan section 6.1). A ledger hook would sit in the same function. The coordinator should sequence the two so they do not both rewrite `confirm_transfer`.
- **Service period vs cash date.** For accrual statements the service period is `cycle.start_at`–`end_at` (3:00 PM Manila on the anchor day, `clock.py:6-33`, `models.py:101-102`). A 6-month Launch Promo is one payment and one 6-month cycle (`services.py:859-860`). Whether to defer revenue over the term, and from which date, is an accounting policy question (section 11), not mine to settle.
- **Early payment.** `early=True` lets a payment before the trial ends or before a due date open a cycle that still starts at the trial end / due date (`services.py:598-599,631-632`), so cash can precede service by days.
- **Time zone.** `TIME_ZONE = 'Asia/Manila'`, `USE_TZ = True` (`settings.py:282,286`): timestamps are stored in UTC and `transferred_on`, `clock.anchor_day` use Manila days. A calendar-year ledger must use Manila dates.
- **Test data.** There is no `is_test` flag on `BankTransferPayment` or `Subscription` [I]. PR 107 plans one on its own tables. A site marked `billing_exempt` never gets a payment (`services.py:363-366`), but a staging payment is indistinguishable from a real one except by environment. A ledger must never run against staging as if it were production books (FAS "never auto-write to production books during experiments").

## 4. What does not exist today

Each line was searched for by name across `apps/*/models.py` and `services.py` [I]; absence is stated as "no model, no function".

| Thing | Status |
|---|---|
| Refunds, credit notes, chargebacks, reversals of a confirmed payment | None. A confirmed payment cannot be undone: `reject_transfer` refuses a confirmed row (`services.py:947-948`), and `BankTransferPayment.delete()` raises (`models.py:208`). `services.cancel` ends service and says "no refund" (PR 106 plan section 5). |
| Receivables, invoices to be paid later, aging | None. `outstanding_centavos` is hard-coded 0 (`services.py:302`, comment: "nothing can be owed before payment collection exists"). PR 108 makes it the total of open usage lines. Every payment is prepaid. |
| Gateway settlement, payouts, fees, bank-fee deductions | None. No gateway client exists; launch payment is bank transfer with proof, and Xendit is disapproved/appealed (memory note). The statement "no gateway is built" is [I] by the absence of any gateway code in `apps/`. |
| Annual plan | Not in `main`: `plan.py:35-39` has `MONTHLY` and `LAUNCH_PROMO` only. PR 106 plans `annual` at 2,499,000 centavos. |
| Usage, overage, storage meter | None. PR 108 plans `UsageEntry`, `ChargeLine`, `UsageEvent`. |
| Statements, invoice numbers, PDFs, BIR invoice register, issuer profile | None. PR 107 plans them (`apps/statements`, `apps/invoices`). Not part of the accounting build. |
| Expenses, vendors, bills, assets, loans, payroll, owner draws | None. |
| Bank accounts, bank statements, bank reconciliation | None. The receiving accounts are an environment variable, not rows (section 2.1). |
| Chart of accounts, journals, fiscal periods, period close | None. |
| Tax tables (percentage tax, 8% election, VAT) | None. `tax`, `vat` in model grep: no match in `apps/`. The 8% election is stated only in the ClickUp text. |
| Commissions, referral credits, eCash, wallets | None in this backend (the legacy ones are TWC concepts). PR 107 mentions `referral_credit` as a future line kind. |
| Merchant (Subscriber) orders and sales | None. No storefront order model exists; `apps/storefront` has no `models.py` ([I], directory listing). |
| Audit of accounting changes | The generic `OperatorAuditEntry` exists (section 5); no accounting-specific change history. |
| A finance-only / owner-only permission | None (section 5). |
| Export (CSV, XLSX, PDF), date picker, table, chart, upload components in the staff app | None (section 7). |

## 5. Access boundaries

### 5.1 What exists [I]

- **One user class.** `apps/operators/models.py:11` defines `class User(AbstractUser): pass`; operators are Django staff users, Subscribers are `accounts.Account` and never users (module docstring). So Django's per-user `is_superuser`, `groups` and `user_permissions` exist on the table (`operators/migrations/0001_initial.py:24-33`) but **no code in `apps/billing`, `apps/inbox` or `apps/operators` reads them** (grep for `has_perm|permission` in those folders returned only `admin_site.py:18` and tests).
- **Two-step HQ session.** Email code, then authenticator or recovery code, then an `AdminSession` row with `stage=verified` (`apps/operators/models.py:64`, `hq_views.py:70-71` `is_verified`, `staff_views.py:128,146`). 12-hour sessions (`hq_auth.py:26`). The Django admin is reachable only through this session (`admin_site.py:18-24`).
- **Eligibility** (`two_step.py:43-53`): `identity.is_active`, `enrolled_at` set, a TOTP secret, not locked, `identity.user.is_active` and `identity.user.is_staff`. Nothing narrower.
- **Route registration.** `staff_views.ROUTES` (`staff_views.py:176-183`) holds the sign-in routes; other apps are added with `ROUTES.update({path: ("POST", _for_staff(view)) ...})` for billing (197-199), inbox (200-202) and contacts (203-205). `_for_staff` (`186-194`) calls `_gate` and then `view(request, session.identity.user)`. So **every staff route of every app has the same gate**, and a new Finance-accounting module would register the same way. `dispatch` (227-) checks the single allowed `Origin` (`HQ_STAFF_ORIGIN`) first and sets `X-Frame-Options: DENY`; with `HQ_STAFF_ORIGIN` unset the whole staff surface does not exist (module docstring, `checks.py:60`).
- **Audit.** `audit.record(operator, action, ...)` refuses an action not declared in `ACTIONS` (`audit.py:10-47,52-60`) and refuses personal values on a KEPT action. Billing actions are declared (44-47). A new accounting action needs an entry in this dict: PRs 103 and 104 touch this file, so I propose none here (task rule).
- **Subscriber side.** Billing endpoints are `signed_in` and reach a site through `sites.services.site_for`, so another account's site answers 404 before any billing row is read (`apps/billing/api.py:1-12,28`). A Subscriber can never reach the staff routes (different host, different session cookie, no shared auth).
- **Tests that exist for this boundary** (`apps/billing/tests/test_staff_finance.py`): origin refusal (92), no session or pre-second-step session refused (100), POST only (113), staff headers (122), switch off = 404 and no billing table read (133,152), routes absent without `HQ_STAFF_ORIGIN` (158), audit of each proof read (310). There is **no** test that a staff user is refused for lack of a finance role, because there is no such role.

### 5.2 What does not exist

- No finance-only, owner-only or per-module permission; every operator with a staff row sees every route, including the five Finance payment routes (`staff.py:179-185`) that read Subscribers' emails (`staff.py:detail`) and proofs.
- No flag on `AdminIdentity` or `User` that could mark an owner. [I] `AdminIdentity` fields: `user`, `email`, `credential_version`, `totp_*`, `failed_attempts`, `locked_until`, `is_active`, `enrolled_at`, timestamps (`apps/operators/models.py:15-42`).
- How many staff identities exist in staging or production is [U] (not read).

### 5.3 What an owner-only rule would have to attach to [P]

The rule has to be enforced in Django, never only in the frontend (the FAS-01 text and `CLAUDE.md`: "Django owns identity... authorization"). The candidate attachment points, from most to least central:
1. **The staff gate** (`_for_staff`, `staff_views.py:186-194`), as a second parameter naming a required capability, so a new module cannot register without declaring one.
2. **A property of `AdminIdentity` or `User`** (a Django group/permission, which the table already carries, or a new column). A new column is a migration and needs the usual approval.
3. **Each handler** in the new accounting module, as a first line. Easiest to forget, so the weakest.
Whatever is chosen also needs: a test that a second staff user without it is refused on every accounting route (the pattern of `test_staff_finance.py`), an audit entry for every read of a book (the proof read is already audited, `billing_proof_viewed`), and a rule for the Django admin lists, where a registered model is visible to any staff user whose `has_view_permission` is true (`apps/payments/admin.py`). Which owners exist and whether a CPA gets read-only access are questions for Coach Ed (section 11). No decision is made here.

## 6. Shared pieces a ledger would reuse

### 6.1 `apps/common` [I]

| Piece | File | Use |
|---|---|---|
| Public ids with a prefix (`new_public_id("btp")`: prefix + 20 Crockford characters) | `common/ids.py:11-19` | Journal/entry ids that never expose a primary key. |
| `ApiError` and the single error shape | `common/errors.py:19-37` | Staff routes answer refusals with `error_body` (`staff.py:_route`). |
| Cursor pagination on `public_id`, `order_by("pk")`, max 100 | `common/pagination.py:10-21` | Lists. Orders by primary key only; a date-ordered ledger list would need a new function. |
| Private file store: Vercel Blob in a shared environment, local folder with `DEBUG`, no store otherwise; key has no URL | `common/private_files.py:1-30,197-230` | Receipts/attachments for expenses and bank files. **Billing proofs do not use it** (they use `billing/proofs.py`'s own `LocalProofStore`), so the unbuilt "approved private store" for proofs and this store are two things today [I]. |
| Retention registry | `common/retention.py` | A rule that must never touch books; `docs/runbooks/retention.md:31` states "No rule touches a billing or tax record". |
| Throttle, masking, phone helpers, middleware | `common/throttle.py`, `masking.py`, ... | Not accounting-specific. |

### 6.2 Celery worker and beat

[I] One Celery app (`config/celery.py`), broker = the Redis of `REDIS_URL` on its own database number; without Redis a task runs in-process, `CELERY_TASK_ALWAYS_EAGER` (`settings.py:235-246`). One scheduled task: `billing-sweep` every 300 s (`settings.py:247-256`), registered by a fixed list. `worker` (`--concurrency=2`) and `beat` (1 instance) run as Railway services on **staging only**; production has no services (`docs/runbooks/billing.md:130-150`). A scheduled accounting job (month-end checks, reconciliation) would add an entry to that list (a change to `config/settings.py`, which PRs 103/104 also edit) and is a Railway setting change that needs Coach Ed.

### 6.3 The on/off switch pattern

[I] Every feature is an environment variable read once in `config/settings.py` as `os.environ.get("X","").strip() == "1"`: `BILLING_ENABLED` (112), `BILLING_BANK_TRANSFER_ENABLED` (116), `BILLING_LAUNCH_PROMO_ENABLED` (119), `RETENTION_ENABLED` (109), `SMS_*` (98-106), `SANTE_*` (128,132), `PAYMENT_METHODS_ENABLED` (136), `STOREFRONT_ENABLED` (147). Off means: routes answer 404, tables are not read or written, and code that ships before its migration is safe (`payments/admin.py` docstring). A ledger switch would follow this exactly. Adding it edits `config/settings.py`.

### 6.4 Money type: centavos versus decimal (recorded, not resolved)

- The backend stores **integer centavos** in every money column: `BankTransferPayment.amount_centavos` and `amount_received_centavos` are `PositiveIntegerField` (`models.py:160,177`; maximum 2,147,483,647 centavos, about PHP 21.5 million, per payment), and the catalog uses `PositiveBigIntegerField` (`catalog/models.py:56`; its module docstring: "Money is whole centavos"). The only `DecimalField`s are weights, dimensions and a 4-decimal earnings rate (`catalog/models.py:48-57,125-132`).
- The FAS-01 text (and others) say "Postgres decimal amounts". The two are not the same schema choice: centavos make rounding explicit at the edge; decimals allow sub-centavo values (for example a daily 6-month revenue allocation, or a 8% tax computed on a percentage). A ledger in centavos needs a stated rounding rule for allocations; a ledger in decimals needs a conversion at every read from billing. Which one the ledger uses is a design decision for FAS-03/FAS-04, and Coach Ed should be told the existing convention before it is decided. The `PositiveIntegerField` width is also too narrow for aggregate balances (a ledger balance can exceed it), so any ledger amount would use a wider type.
- Database: `DATABASE_URL`, PostgreSQL on Railway with TLS enforced, SQLite locally (`settings.py:213-222`, `common/database.py`). Tests and CI run on SQLite by default (`CLAUDE.md`). So **database-level protections for immutability (triggers) are Postgres-only and untested in CI**, which PR 107 already notes as its own question Q8.

## 7. The frontend (`negosyoonline-frontend`, `apps/hq`)

Read at `2b84de4`. [I] unless noted.

- **What it is.** Static HTML, CSS and JavaScript in `apps/hq/public/`; Next.js only serves them and sets headers (`apps/hq/README.md:1-4`). Pages: `index.html` (console), `auth.html` (sign-in, code, verify), `finance.html`, `not-found.html`. Scripts: `hq.js` (72 lines, the client), `shell.js`, `menu.js`, `theme.js`, `console.js`, `auth.js`, `finance.js` (500 lines). One stylesheet, `hq.css` (442 lines). No framework, no bundler step for the pages, no npm dependency in the page code (`apps/hq/package.json` lists `next`, `react`, `react-dom` for the server shell only).
- **API client.** `public/assets/js/hq.js:8-31` `call(method, path, body)` does `fetch(cfg.staffApi + "/" + path, {credentials:"include"})` straight to the hq host, resolves `{ok, status, code, data}`, and has `file(path, body)` for a private file returned as a Blob (33-44). It decides nothing about access: "it follows the answer". `cfg.staffApi` comes from `src/app/assets/js/config.js/route.ts` and `HQ_STAFF_API_BASE` (README table). Staging default `https://hq-staging.negosyonline.com/staff/v1`.
- **Finance page.** `finance.html` + `finance.js` are the bank-transfer proof review: the queue (`finance/payments/list/`, `finance.js:116`), one payment (`detail/`, 209), the proof (`proof/`, 155), and confirm/reject (`finance/payments/<path>`, 348). The confirm dialog takes the amount received as whole pesos with a fixed `.00` suffix (`finance.html:112`).
- **Rules the app holds** (`apps/hq/README.md`, enforced by `tests/rules.test.mjs`): no call through Vercel, nothing from another host, no inline script or style attribute, nothing kept in the browser but the theme, no number, name or code in an address or query string, no secret in a variable, no page names the Django Admin. The CSP is fixed in `next.config.ts`; `img-src 'self' blob:` was the only widening, with a frame/object/other word needing its own approval (README, "A proof of payment has no address").
- **Not present.** No table component, date or period picker, chart, export or download control, file-upload control (the proof is shown, never uploaded from HQ), pagination control beyond the queue's cursor, or number formatter shared across pages (`finance.js` formats its own amounts). A Finance accounting UI would build all of those by hand in the same style, and any export or chart library would need a CSP decision and Coach Ed's approval under the README rules.
- **Other frontends.** `apps/dash` is the Subscriber dashboard; `apps/site`, `apps/storefronts` are public. Platform finances must never appear in them (FAS contract; `AGENTS.md`: "Do not expose tenant data, billing data...").
- **Tests.** `apps/hq/tests/*.test.mjs` (client, finance, rules, focus, theme...) run with Node against the served files [I by file list; the commands were not run].
- Anything the owner's dashboard shows of billing (the Subscriber's own page) is a different surface and not a source for the platform books.

## 8. Open plans by other sessions: read, not duplicated

All three are open pull requests whose only change is one plan document; none is built, so they describe intended code [I: files read from the PR diffs, not from `main`].

| PR | Plan | What it adds that a ledger must read or wait for | Conflicts to sequence |
|---|---|---|---|
| 106 | Billing B3: Annual Plan, versioned plans, Launch Promo consent record (`docs/superpowers/plans/2026-11-11-backend-billing-b3-annual-and-promo-plans.md`) | Every paid `BillingCycle` gets a snapshot (`plan_code`, `plan_version`, `price_centavos`, `term_months`); `BankTransferPayment` gains `plan_version`, `term_months`; a consent table; an annual plan at 2,499,000 centavos. States itself that today "`BillingCycle` holds no plan, price or term". | Same `models.py` and `services.py`; a ledger designed now should read the payment and, once B3 lands, the cycle snapshot. |
| 107 | Billing B5: statements and the manual BIR invoice register (`...-b5-statements-and-bir-register.md`) | New apps `statements` and `invoices`; `Statement`, `StatementLine`, `ManualInvoiceItem` (one per paid cycle, with `is_test`, `sale_date`, `tax_year`); a hook `open_item(cycle, payment)` inside `confirm_transfer`; staff routes `finance/invoices/*`, `finance/statements/*`. **The manual BIR invoice register belongs here, not to the accounting build.** | Both want to add a call inside `confirm_transfer`; the Finance menu in HQ will hold both. |
| 108 | Billing B4: usage ledger and overage (`...-b4-usage-ledger-and-overage.md`) | `UsageEntry`, `ChargeLine`, `UsageEvent`; `BankTransferPayment.usage_centavos`, `usage_line_max_id`; a `usage_only` payment with no cycle; replaces constraint `billing_transfer_confirmed_in_full`; overage lines can be written off. | Changes what "a confirmed payment" means (price + overage; no cycle for usage-only) and creates a write-off outcome a ledger must account for. |

PRs 103 and 104 touch `apps/operators/audit.py` and `config/settings.py` (per the brief). This work touches neither and proposes no change to them. A future accounting build will need to: declare its own audit actions in `audit.py`, add a switch (and perhaps a beat entry) in `settings.py`. Both files are contended, so that belongs in the coordinator's sequencing, after 103 and 104 merge.

## 9. Reuse versus new: a first matrix [P]

| Need | Existing piece | Verdict |
|---|---|---|
| Single idempotent writer with a row lock | `_write` + `SubscriptionTransition` (`services.py:310-352`) | Reuse the **pattern**; a ledger needs its own writer and table, since a journal is not a subscription transition. |
| Append-only event row with a unique key | `BankTransferEvent`, `SubscriptionTransition` | Reuse the pattern; the guarantee today is code convention only (no database trigger) [I]. |
| Immutable rows | `delete()` raising (`models.py:208`), `OperatorAuditEntry.save()` raising on update (`operators/models.py`) | Pattern only; Django-level, bypassable by `QuerySet.update` and raw SQL (e.g. `_lapse_pending` uses `.update`, `services.py:449`). A posted journal needs a stronger guard (PR 107 Q8 asks the same for statements). |
| Staff route registration, two-step gate, origin check | `staff_views.ROUTES`, `_for_staff` | Reuse unchanged; add the owner-only capability (section 5.3). |
| Audit log | `audit.record` | Reuse; new actions need declaration in the contended file. |
| Private file store | `common/private_files.py` | Reuse for expense receipts and bank files; the unbuilt billing proof store is a separate gap. |
| Celery beat | `config/celery.py`, `CELERY_BEAT_SCHEDULE` | Reuse for scheduled checks; Railway change needs approval. |
| Cursor pagination | `common/pagination.py` | Extend (it orders by `pk` only). |
| Chart of accounts, journals, periods, reports | none | New. |
| HQ tables, pickers, charts, export | none | New, within the static-page rules. |
| Legacy ledgers (twcinc-app, twcako) | out of scope per the ruling | Not a source of code or data. Their design ideas named in the FAS text (single writer with idempotency and replay comparison; draft, post, reverse; period close) may be read as ideas only. |

## 10. FAS-02 to FAS-32 against this codebase

Source of the task text: a read-only subagent summarised each description for me (descriptions only, no comments). **FAS-12 was read in full; FAS-32 (about 90 KB) was read through its headers, opening lines, sequencing and acceptance sections only [U for the rest].** No task has a ClickUp dependency field set; "depends on" comes from `FAS-xx` mentions in the text plus the coordinator's build order (FAS-00 comment). FAS-02 to FAS-19 share the boilerplate "Postgres decimal, internal-owner-only, immutable journals, inspect the repo first"; the decimal line conflicts with section 6.4 and the owner-only line has nothing to attach to yet (section 5). "Holds" and "outdated" are judged against sections 2–8.

| Task | What still holds here | What is outdated | Depends on |
|---|---|---|---|
| FAS-02 policy matrix, opening-balance design | CPA policy checkpoints; opening-balance import with dry run, rollback, duplicate guard | Names Xendit balances as an inventory item (no gateway exists, section 4); opening position unknown (U7) | None stated; Coach Ed/CPA answers (11.2 Q1, Q4, Q9) |
| FAS-03 hierarchy, data model, permissions, deployment | Chart baseline, model list, permission matrix, ERD, approved before code | Cites `twcinc-app` `post_entry`/`LedgerAccount`, the Next.js token proxy and Postgres decimal; the permission matrix has no role to attach to (5.2) | FAS-01 (this report), FAS-32 |
| FAS-32 COA v1 and payment-to-journal matrix | Idea of an event-to-journal matrix for owner/CPA sign-off | Written on `PaymentAttempt`, `PaymentEvent`, `BillingStatement`, `BillingCycle`, Xendit; here the event is E1 (3.1) and the cycle holds no amount | Hub the others cite; needs 11.2 Q1, Q3, Q4 |
| FAS-04 posting engine | Draft/review/post, idempotent source mapping, reversal plus replacement, concurrency tests; the `_write` pattern (2.2, 9) | `PaymentAttempt` as canonical event, `BillingStatement` as evidence, `PaymentEvent` webhook inbox, `post_entry` reference: all legacy; there is no webhook | FAS-01, FAS-32, FAS-03 |
| FAS-05 GL browsing, accounts, trial balance | Account CRUD, drilldown, trial balance from posted entries only | `LedgerAccount`, eCash mentions; HQ has no table or pagination for dates (7) | FAS-03, FAS-04 |
| FAS-06 periods, adjustments, close, audit trail | Open/soft-close/locked, lock in the posting service, audited reopen | References to `JournalEntry` posted-only and `FiscalPeriod` of excluded repos; audit actions need declaring in `audit.py` (5.1) | FAS-04 |
| FAS-07 opening balances, import QA | Template import with staging, preview, error log, rollback | Gateway-clearing and tax-credit import categories assume Xendit and 2307 (neither exists here) | FAS-02, FAS-04, FAS-06 |
| FAS-08 cash accounts, statement import, reconciliation | Bank and clearing accounts, CSV import, matching, de-duplication | `BankAccount`/`BankStatement` of an unrelated repo; Xendit clearing; no bank-account rows and no receiving-account field on the payment (2.1, U2); no upload control in HQ (7) | FAS-04; bank-account records (11.2 Q12) |
| FAS-09 supplier bills, expenses, AP | Supplier register, bills, aging, payments settle AP | Nothing legacy named; nothing exists in code (4); needs file store for receipts (6.1) | FAS-04, FAS-08 |
| FAS-10 receivables, invoice settlement | AR control and aging if credit sales ever exist | Reuses "billing source invoices" that do not exist; every payment is prepaid (4). The coordinator holds it until there are credit sales | Held; B5 and B4 (8) |
| FAS-11 fixed assets, loans, equity | Asset register, depreciation, loan split, capital and draws | Nothing named; nothing in code (4) | FAS-04, FAS-02 |
| FAS-12 map billing events to journals | The mapping itself, with a hook beside `confirm_transfer` (3.2); owner's manual BIR rules | Entire event set is legacy (`PaymentAttempt`, `PaymentEvent`, statements, `StatementAdjustment`, Xendit checklist); the manual BIR invoice register is B5's, not this build (8); the correct source is E1 | FAS-01, FAS-32, B3/B4/B5 plans (8) |
| FAS-13 gateway clearing, fee reconciliation | The clearing-ledger idea | Xendit, `PaymentAttempt.succeeded`, `founder_hq` importers: all legacy. No gateway is built; the coordinator holds it | Held until a gateway is approved |
| FAS-14 deferred revenue and recognition schedules | Release schedules for the PHP 4,999 six-month promo and the annual plan | Cites `billing.py`, `payments/*`, hard-coded PHP 2,499; here `plan.py:35-39`; the annual plan is only in PR 106; cycles hold no price (1.1, 8) | FAS-32, FAS-12, PR 106 landing; 11.2 Q1 |
| FAS-15 integration monitoring, recovery, sandbox | Outbox/retry/dead-letter, bounded backfill, staging fixtures | Webhook signature checks have no target (no webhook); the in-transaction option removes the need for an outbox (3.2) | FAS-12; 11.2 Q8 |
| FAS-16 P&L | P&L from posted GL only, comparatives, export | Cites excluded reports code; HQ has no export, table or chart (7) | FAS-04, FAS-05, FAS-06 |
| FAS-17 statement of financial position | Balance sheet, no plug accounts | None named | FAS-16 inputs, FAS-11 |
| FAS-18 cash flow and equity | Direct/indirect cash flow and equity roll-forward | None named | FAS-16, FAS-17 |
| FAS-19 monthly close controller | Close dashboard, exception checks, owner sign-off, snapshot hash | `FiscalPeriod` of an excluded repo; needs a scheduled task (6.2) and approval | FAS-06, FAS-16–18 |
| FAS-20 8% income tax policy engine | Versioned policy, threshold alerts, PHP 250k reduction | No tax code exists (4); policy is a CPA call (11.2 Q4) | FAS-00 rules, FAS-02 |
| FAS-21 CWT 2307 tracker | Credit register tied to the GL | Nothing in code mentions withholding; PR 107 allows a typed withholding on a manual invoice (8), which is a source to link | FAS-20, FAS-04 |
| FAS-22 BIR filing support and calendar | 1701Q/1701A worksheets, deadline calendar, no auto-filing | None named | FAS-20, FAS-21 |
| FAS-23 threshold transition scenarios | Annual-cap projection (100 × 2,499 × 12 = 2,998,800), regime gate | The 100-subscriber basis is not in code; prices from `plan.py` (2.1) | FAS-20, FAS-24 |
| FAS-24 SaaS metrics, cohort model | Metric dictionary (MRR, ARR, churn, cohorts) | "Billing snapshots" do not exist until PR 106; subscription/cycle/transition tables give the raw history (2.1) | FAS-12, PR 106 |
| FAS-25 unit economics by subscriber | Cost drivers, CAC/LTV, margins | Cost sources (Railway, Vercel, SMS, AI) have no tables here; `SmsMessage` has no cost field [I: no cost or fee column in `apps/notifications/models.py`] | FAS-09, FAS-24 |
| FAS-26 budget, cash forecast, risk | Budgets vs actuals, runway, scenarios | None named; nothing in code | FAS-16, FAS-18 |
| FAS-27 private finance dashboard | Owner-only dashboard; merchant roles blocked in UI and API | Needs the owner-only gate (5.3) and HQ chart/table components that do not exist (7) | FAS-05, FAS-16, FAS-24 |
| FAS-28 annual closing | Year-end close, carry-forward, reopen controls | "Reuse existing billing models": only the payment and cycle rows are reusable (2.1) | FAS-06, FAS-19 |
| FAS-29 annual report builder, exports | Package of P&L, BS, CF, equity, notes; PDF/CSV/XLSX; version hash | No PDF/CSV/XLSX code anywhere in `apps/` (PR 107 plans a PDF); CSP limits the staff app (7) | FAS-16–18, FAS-28 |
| FAS-30 security and accounting QA | Double-entry, concurrency, merchant isolation, CSV injection, backup/restore tests; pattern in `test_staff_finance.py`, `test_concurrency.py` (5.1) | `post_entry`, `PaymentAttempt`, Xendit cases are legacy; no finance-role test is possible until a role exists | FAS-00, FAS-32; runs alongside every stage |
| FAS-31 production rollout, runbook | Feature-flagged rollout, handbook, rollback, go/no-go | Production has no Railway services yet (6.2, U4); the runbook pattern is `docs/runbooks/billing.md` | All stages |

## 11. Unknowns, and decisions that belong to Coach Ed or a CPA

Each has a recommended safe default. None is decided here.

### 11.1 Unknowns (cannot be answered by reading `main`)

| # | Unknown | Why unknown | How to learn it without writing |
|---|---|---|---|
| U1 | Whether any real payment exists on staging or production | `collection_live()` is false outside `DEBUG` (section 1.2); the data was not read | A read by Coach Ed or a session he opens for production data; the coordinator's call |
| U2 | Which receiving account got which transfer | Not a field on the payment (section 2.1) | Operator practice; or add a field in a later plan |
| U3 | How many staff identities exist and who they are | Not read | Staging/production read |
| U4 | Whether production has any Railway service, database or Vercel project yet | Memory note and runbook say production is deferred until the MVP is done; the runbook line is [I] (`billing.md:132`), the live state is [U] | Railway dashboard |
| U5 | Runtime verification: test suites were not run | Read-only task | `uv run python manage.py test apps.billing` is the command; not run here |
| U6 | The frontend `origin/main` state after PR 45 | Not read | `git diff 2b84de4..27d42b9 -- apps/hq` |
| U7 | Whether the legacy TWC/NegosyoOnline books have an opening balance to bring over | Task text for FAS-02 names it; no data seen | Coach Ed / CPA |

### 11.2 Decisions for Coach Ed or a CPA (questions with a recommended default)

1. **Revenue recognition for prepaid cycles.** A PHP 4,999 Launch Promo buys 6 months in one payment. Recognise it when cash is confirmed, or spread over the 6 months? *Default:* book the cash to a deferred balance on confirmation and release it by cycle period; a CPA confirms. (Accrual is the stated basis for management financials; the tax basis is separate and is a CPA call.)
2. **Money type: integer centavos or decimal.** *Default:* keep centavos (the existing convention), with a wider integer column for balances and a written rounding rule for any allocation.
3. **What counts as NegosyoOnline's revenue.** Only the subscription cycles, plus overage when B4 lands? *Default:* yes, and nothing that belongs to a Subscriber's buyers, ever.
4. **Tax treatment.** The task says a sole proprietorship currently electing the 8% income tax and non-VAT. *Default:* the books record gross receipts and make no tax computation; any 8% or percentage-tax figure is shown only as a labelled estimate until a CPA approves the rule.
5. **Who may see the books.** Owner only means which people? Does a CPA get read-only access and via what login? *Default:* a single owner capability, no CPA access until named.
6. **Where the capability lives** (group/permission, a flag on the identity, or the gate). *Default:* a gate parameter plus a Django group, decided in FAS-03, with a test per route.
7. **Is the ledger in the same database and Django process as billing?** *Default:* yes (new app, switch-guarded), as the ruling says this backend.
8. **Ledger posting: in the confirmation's transaction, or after?** *Default:* in the same transaction, failing loudly; a missing mapping or period blocks the confirm rather than skipping.
9. **Opening balances and the first book date.** Is there anything before the first confirmed payment (capital, equipment, prior sales)? *Default:* start from zero on the first live payment, and enter any opening position as a reviewed manual journal.
10. **Refund policy.** The code has none and the plans say "no refund". If a refund is ever paid, how is it booked? *Default:* none until a refund feature exists; if one is paid outside the system, a manual reversal journal with a reason.
11. **Out-of-system payments and mismatches** (the operator rule "a wrong amount is settled with the Subscriber outside the system", `staff.py:detail` comment). *Default:* the ledger posts only a confirmed, exactly matching payment; anything else is recorded by hand.
12. **Bank accounts as records.** Should the receiving accounts become rows (so reconciliation can point at them)? *Default:* yes in the ledger design; the variable stays the source for what Subscribers see.
13. **Test versus live books.** How are staging payments kept out of production books, and is there a "test" flag on journals? *Default:* the ledger switch is off everywhere except an environment named live, and no staging entry is ever copied.
14. **Immutability on SQLite versus PostgreSQL.** CI runs SQLite, so a trigger-based guard cannot be tested there. *Default:* application guard plus a PostgreSQL trigger, with a staging check by Coach Ed's approved run.
15. **Calendar and period close.** Calendar year Jan–Dec, Manila dates. Who closes a month, and may a closed month be reopened? *Default:* owner closes; reopen only by an owner reversal with a reason.
16. **Retention.** How long books and source documents are kept (BIR record-keeping). *Default:* never delete; no retention rule touches them (consistent with `retention.md:31`). The legal period is a CPA/legal answer.
17. **Documents the manual BIR invoice register will hold** are B5's. *Default:* the ledger links to a B5 invoice by id and does not duplicate it.

## 12. Acceptance checks of the task, against this report

| FAS-01 test / acceptance line | Answer |
|---|---|
| Every proposed integration maps to actual schema or is marked proposed | Sections 2 and 3 name schema with lines; proposals carry [P]. |
| Merchant sales/funds are not booked as NegosyoOnline operating revenue | Holds today because nothing records them (section 1.5); must be restated when an order table exists. |
| Unknowns, risks, owners and dependencies | Sections 1, 8, 11. Owners of each decision: Coach Ed, a CPA, or the coordinator for sequencing. |
| Entity/data-flow map; tables/API/events; auth boundaries; idempotency; gap analysis; reuse vs extend | Sections 2, 3, 4, 5, 9. |
| Read-only sample report of existing data | **Not done**, on purpose: the brief forbids reading staging or production data. |
| Migration approach | Not part of this inspection; no migration is proposed. The pattern in `docs/runbooks/migrations.md` applies (explicit, reviewed, run by Coach Ed or an approved run). |
| Runtime and test verification | Not done (U5). |

## 13. What I did and did not do

- Did: read `apps/billing`, `apps/payments`, `apps/operators`, `apps/common`, `config/settings.py`, `config/celery.py`, the billing and retention runbooks, `apps/hq` in the frontend, the three open plan diffs, FAS-00 and FAS-01 with their comments myself, and FAS-02 to FAS-32 through a read-only subagent summary (FAS-32 only in part, section 10).
- Did not: run any test or command that writes, open a database, read staging or production, edit code, add a migration, switch or seed, touch `apps/operators/audit.py` or `config/settings.py`, or mark FAS-01 complete.
