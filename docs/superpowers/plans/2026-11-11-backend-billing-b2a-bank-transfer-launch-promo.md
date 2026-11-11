# Billing Build B2a: launch payment by bank transfer with proof, and the six-month Launch Promo (plan)

Status: **plan only, for Coach Ed's approval.** No code, migration, variable or staging action is in this pull request.

ClickUp task: z8uq9me581 (https://app.clickup.com/t/z8uq9me581). Parent: z8uq9m8h46. Read against `origin/main` at `cb7f531`.

## 1. Sources

- The Task z8uq9me581: objective, the rulings of 2026-11-11, "Rules carried from settled rulings", the eight owed items, "Accepted when". It has no comments.
- The Parent z8uq9m8h46: "FINAL OWNER PLAN-RULE" and "FINAL SIX-MONTH LAUNCH PROMOTION" (2026-11-08), the manual BIR invoicing ruling, the shared non-negotiables.
- The B1 plan `docs/superpowers/plans/2026-11-11-backend-billing-b1-subscription-lifecycle.md`: D1, D2, D10, D13, D15, sections 5.2 to 5.4 and section 13 ("for B2").
- The scope document `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`, sections 7, 8 and 9.
- The code: `apps/billing`, `apps/operators`, `apps/payments`, `apps/notifications`, `apps/common`, `config/settings.py`, `config/api.py`, `docs/runbooks/billing.md`.
- The legacy repository was not needed and was not read.

Where a rule below has no source it is marked **choice** (a reversible detail I decided) or **question** (section 12).

## 2. What B1 already gives

| File | What it holds today |
|---|---|
| `apps/billing/models.py` | `Subscription` (one per site, six states), `SubscriptionTransition` (append-only, unique `idempotency_key`), `BillingCycle` (settlement `not_billable`, `pending`, `paid`, `lapsed`; `paid_at`; `payment_reference`; at most one `pending` per subscription). No amount and no plan is stored |
| `apps/billing/plan.py` | One plan as constants (`PLAN_NAME`, `PRICE_CENTAVOS` 249,900), the periods (`TRIAL`, `GRACE` 7 days, the download and deletion periods) and `collection_live()`, which returns `False` |
| `apps/billing/clock.py` | `cycle_end(start_at, anchor)`: 3:00 PM Manila on the anchor day of the next month, or that month's last day. One month only |
| `apps/billing/methods.py` | `active_at(site, instant)`: always `False` |
| `apps/billing/services.py` | `position`, `overview`, `state_filter`; the writes `_write` and `_apply` under the subscription's row lock, with `catch_up` first; `cancel`, `withdraw_cancellation`; the payment outcome functions `first_payment_succeeded`, `first_payment_failed`, `renewed`, `renewal_failed`, `recovered`, `reactivated`, each keyed `paid:<reference>` or `failed:<sub>:<n>`, called only by tests; `prepare_disposal`; `set_exempt`; `sweep` |
| `apps/billing/api.py` | `GET sites/{site_id}/billing/`, `POST .../billing/cancel/`, `POST .../billing/reactivate/`. Signed in, through `site_for`, 404 for another account's site |
| `apps/billing/tasks.py`, `management/commands/` | The sweep every 5 minutes; `billing_sweep`, `billing_backfill_subscriptions`, `billing_exempt` |
| Staging | `billing.0001_initial` is applied and `BILLING_ENABLED=1` is set (`docs/runbooks/billing.md`). So the B1 tables are read on every request there |

What B1 left for the payment build (its section 13): `collection_live()` answering yes; the `clocks_started` transition on the go-live day (D15); a real body for `methods.active_at` and the same read in `state_filter`; the disposal refusal while a payment is under review (D13); the first-site-after-day-44 gap (D2); the trial reminder emails (D10).

`apps/payments` holds a Subscriber's own receiving accounts. It has no upload: a QR file is refused. So there is no private-upload pattern in the backend to follow, and this build writes the first one. `apps/payments` is not changed.

## 3. Decisions in this plan

| # | Decision | Kind |
|---|---|---|
| P1 | No column is added to a B1 table. Everything new is in new tables | choice: staging reads the B1 tables now, and code is deployed before its migration is run |
| P2 | The switch is `BILLING_BANK_TRANSFER_ENABLED`, off by default, and works only while `BILLING_ENABLED` is on. `collection_live()` answers yes only when it is on **and** a receiving account is configured | Task (R-4a rule); the second half is a choice, so the page can never show "pay" with no account |
| P3 | One payment row per proof. Its amount and plan are frozen when it is submitted | Parent: "frozen historical snapshots" |
| P4 | The outcome time of a transfer is the operator's confirmation time, never the upload time or the date on the slip | Task: "Only an operator's recorded confirmation settles a cycle"; "starts a new paid cycle at the confirmation time" |
| P5 | A rejection writes no subscription transition. The lifecycle goes on exactly as if no proof had been sent | Task: "a rejected proof changes nothing and can be replaced" |
| P6 | A proof under review at the trial-end instant is what `methods.active_at` reports. So such a trial stays served for the 7 days of B1 and no longer | Task: "may delay suspension only inside that window (L32)" |
| P7 | A payment confirmed before its due date opens the next cycle at the due date, paid. At most one cycle can be paid ahead | choice inside the rules; question 10 asks Coach Ed to confirm the trial case |
| P8 | The plan of a cycle lives on the payment that settled it and in the transition's `detail`. The next amount due after any paid cycle is the Monthly price | Task: "then PHP 2,499.00 monthly. Never PHP 4,999 again" |
| P9 | The operator's actions are service functions that take the staff user. The screen that calls them is its own pull request, built after question 2 | Task item 2 |
| P10 | No email is sent until its wording is approved (question 8) | Task item 8; brief: no invented wording |

## 4. Tables

All in `apps/billing`. One migration, `0002_bank_transfer`, adds the three tables of 4.1 to 4.3.

### 4.1 `BankTransferPayment`

| Column | Notes |
|---|---|
| `public_id` | `btp_…` from `apps.common.ids.new_public_id`. The only id the API shows |
| `subscription` | FK, `PROTECT` |
| `reference` | Our own reference, unique, at most 64 characters: the `public_id`. It is the `reference` B1's payment functions take, so the settlement key is `paid:<public_id>` |
| `plan_code`, `amount_centavos`, `currency` | Frozen at submission from `plan.py` |
| `status` | `submitted`, `confirmed`, `rejected`, `replaced` |
| `transferred_on` | The date the Subscriber says the transfer was made (Manila date). Not in the future |
| `transfer_reference` | The bank's or e-wallet's reference number as the Subscriber typed it, optional, at most 64 characters |
| `proof_key`, `proof_content_type`, `proof_size`, `proof_sha256` | Where the file is in the private store (4.4) and what it is |
| `submitted_by` | FK to `accounts.Account`, `PROTECT` |
| `submitted_at` | |
| `decided_at`, `decided_by` | `decided_by` is FK to the staff `User`, `PROTECT`, empty for `replaced` |
| `amount_received_centavos` | What the operator saw in the bank record. Set on confirmation only |
| `reject_reason`, `reject_note` | `unreadable`, `amount_mismatch`, `not_received`, `wrong_account`, `duplicate`, `other`; the note is shown to the Subscriber, at most 300 characters |
| `cycle` | One-to-one to `BillingCycle`, empty until confirmed |

Constraints: unique `public_id` and `reference`; at most one `submitted` row per subscription (a partial unique constraint, as B1's `billing_one_pending_cycle`); unique `cycle`; `status = confirmed` only with `cycle`, `decided_by` and `amount_received_centavos = amount_centavos` (a check constraint, so a mismatched amount cannot be stored as confirmed).

Rows are never deleted (`delete()` raises, as `SitePaymentAccount` does). Nothing blanks them: L76, five years.

### 4.2 `BankTransferEvent`

Append-only, one row per state change of a payment, as `SubscriptionTransition` is for a subscription: `payment` FK, `code`, `actor` (`owner` or `operator`), `idempotency_key` unique, `recorded_at`, `detail`.

| Code | Key | Written when |
|---|---|---|
| `submitted` | `bt_submitted:<payment>` | The proof is stored and the row created |
| `replaced` | `bt_replaced:<payment>` | A newer proof took its place before a decision |
| `confirmed` | `bt_confirmed:<payment>` | The operator confirmed; same transaction as the cycle |
| `rejected` | `bt_rejected:<payment>` | The operator rejected |

### 4.3 `BillingNotice`

Used only by the emails of pull request 5: `subscription` FK, `code`, `idempotency_key` unique (for example `renewal_reminder:<sub>:<cycle>:<days>`), `sent_at`. It is in migration `0002` so that one migration covers the build; nothing writes it before pull request 5.

### 4.4 The proof file

- Accepted: JPEG, PNG or PDF, decided by the file's first bytes, never by its name or the browser's type. At most 5 MB. One file per payment. **Choice.**
- The store is one small interface in `apps/billing/proofs.py`: `put(key, content, content_type)` and `get(key)`. The key is random and holds no name, email or site address.
- There is no URL to the file anywhere. It is read only through the two routes that check who is asking (the Subscriber's in 7.5, the operator's in 8), which send it with `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff` and `Content-Disposition: inline` with a neutral file name.
- Which store stands behind the interface is question 3. Until it is answered the only implementation is a local directory (`BILLING_PROOF_DIR`), allowed only when `DEBUG` is on or under test, and a Django system check refuses to start with the switch on and no approved store.

## 5. States and transitions

### 5.1 A payment

```
(none) --submit--> submitted --confirm--> confirmed   (final; settles exactly one cycle)
                       |------reject----> rejected    (final; nothing else changes)
                       |------submit again--> replaced (final) + a new `submitted` row
```

- **Submit** needs: the switch on; the subscription not exempt; a plan that is offered to it now (6.3); nothing paid ahead already; not a cancellation that is still inside its paid period (the Subscriber withdraws it first with the existing `reactivate` route); the time before the deletion date, when there is one (D1).
- **Replace.** A submit while a row is `submitted` marks the old row `replaced` and creates the new one, in one transaction under the subscription's row lock. After a rejection a submit is simply a new row.
- **The same file twice.** A submit whose file hash and plan equal the row now under review returns that row with 200 and writes nothing.
- **Confirm** needs the row to be `submitted` and the amount the operator enters to equal `amount_centavos`. A different amount is refused with `amount_mismatch` and nothing changes; the operator then rejects with that reason (Task: "a mismatch is not settled automatically"). Confirming a row that is already `confirmed` returns the same result and writes nothing. Confirming a `rejected` or `replaced` row is refused with `not_under_review`.
- **Reject** needs the row to be `submitted`, a reason code, and a note when the reason is `other`. Rejecting a row already `rejected` returns it unchanged.
- Every one of these runs through B1's `_write`, so it holds the subscription's row lock and `catch_up` has run first. Two operators, or an operator and a replacing Subscriber, are serialised by that lock; the loser sees the final state.

### 5.2 How a confirmation settles a cycle

One new function, `services.settle_transfer(subscription, payment, at)`, called only by the confirmation, picks B1's payment function by the state at the confirmation time `at`:

| State at `at`, after `catch_up` | What is written | Cycle |
|---|---|---|
| `trial_active`, before trial end | `first_payment_succeeded`, with a new `early=True` argument that lifts B1's `not_due` refusal | Cycle 1 from `trial_end_at`, paid. The trial days are kept (question 10) |
| `trial_active`, at or after trial end (a proof was under review at the trial-end instant, P6) | `first_payment_succeeded`, as B1 | Cycle 1 from `trial_end_at`, paid |
| `active`, the paid cycle still running | `renewed`, with `early=True` | Next cycle from the running cycle's `end_at`, paid |
| `active`, past the cycle's end, inside the 7 days | `renewed`, as B1 | Next cycle from the ended cycle's `end_at`, paid |
| `payment_requires_attention` | `recovered`, as B1 | The pending cycle becomes paid. Not reachable in B2a (nothing writes a failure), kept for completeness |
| `suspended`, `cancelled` at or after service end, `cancelled_no_payment_method` | `reactivated`, as B1 | New cycle from `at`, new anchor day; the lapsed cycle stays lapsed. Refused at or after the deletion date |
| `cancelled`, before service end | Refused, `cancel_scheduled` | None |
| Latest cycle starts after `at` (already paid ahead) | Refused, `already_paid_ahead` | None |

- The cycle's length comes from the payment's plan (6.2). The transition's `detail` gains `plan` and `amount_centavos` beside B1's `cycle` and `reference`.
- The idempotency key is B1's `paid:<reference>`. With the unique `cycle` on the payment, the unique (`subscription`, `sequence`) on the cycle and the `bt_confirmed` key, a repeated or concurrent confirmation cannot open two cycles or settle twice.
- The confirmation, the cycle, the transition, the event and the audit entry are one transaction. If any part fails, none is written.
- B1's "success learned late" gap (its section 13) does not arise: by P4 the confirmation is the outcome.

### 5.3 The lifecycle around a proof

- `methods.active_at(site, instant)`: true when the switch is on and a payment of the site's subscription was under review at that instant (`submitted_at <= instant`, and not decided or decided after it). With the switch off it returns `False` without reading the table.
- `services.state_filter` gets the same read as an `Exists` on the payment table for a trial past its end whose answer is not yet frozen, so the label and `position` agree (B1 section 13). With the switch off the subquery is not added.
- A renewal needs no change: B1 already serves an `active` site for 7 days past its due date and then writes `suspended_no_outcome`, whether or not a proof is waiting.
- `prepare_disposal` gains one reason, `payment_under_review`, when a `submitted` payment exists (D13: "refuse while a payment attempt is … held for review"). The R-3a hold and the disposal claim stay unbuilt, as in B1.

### 5.4 The clocks start (D15)

When `collection_live()` first answers yes, the sweep writes one `clocks_started` transition for each never-paid ended subscription whose dates are empty: effective at the sweep's time, key `clocks_started:<sub>`, state unchanged, `download_until` and `deletion_due_at` set to that time plus 30 days. `_due_ids` selects them only while collection is live. Turning the switch off later does not take the dates back. **Choice:** the sweep does it rather than a command, so the step cannot be forgotten; the go-live instant is then within 5 minutes of the switch.

## 6. The Launch Promo as a plan with a term

### 6.1 `plan.py`

```python
@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    price_centavos: int
    months: int          # the length of one cycle bought with this plan

MONTHLY = Plan("monthly", "NegosyoOnline Monthly", 249_900, 1)
LAUNCH_PROMO = Plan("launch_promo", "NegosyoOnline Launch Promo (6 Months)", 499_900, 6)
PLANS = {p.code: p for p in (MONTHLY, LAUNCH_PROMO)}
```

- `PLAN_NAME`, `PRICE_CENTAVOS`, `CURRENCY` and the periods stay, so every B1 caller and `GET billing/` answer as today.
- The promo is a plan that buys one six-month cycle. It has no "renewal": after it, the only plan that can be bought is Monthly. That is the whole of "then PHP 2,499.00 monthly".
- **The Annual Plan is not built.** There is no `annual` row, no annual price in code, and no rule for annual cancellation or usage statements. `months` is the one thing B3 can reuse; B3 decides the rest.
- The promo's name is taken from the Parent's invoice guide ("Launch Promo (6 Months)"). **Choice**; it is a label the dashboard may replace.

### 6.2 `clock.py`

`cycle_end(start_at, anchor, months=1)`: 3:00 PM Manila on the anchor day of the month `months` after the one the cycle starts in, or that month's last day. B1's rule is unchanged for `months=1`. `services._open_cycle` takes the plan and passes `plan.months`.

| Cycle starts (Manila) | Anchor | Promo ends | Next Monthly cycle ends |
|---|---|---|---|
| 8 Nov 2026 | 8 | 8 May 2027, 3:00 PM | 8 Jun 2027 |
| 31 Aug 2026 | 31 | 28 Feb 2027 | 31 Mar 2027 |
| 31 Aug 2027 | 31 | 29 Feb 2028 (leap year) | 31 Mar 2028 |
| 30 Sep 2026 | 30 | 30 Mar 2027 | 30 Apr 2027 |

The first row is the Parent's own example. The end is the calendar date six months on, never 180 or 6 × 30 days. The 3:00 PM cutoff is B1's (its section 5.2) and is not reopened here.

### 6.3 What is offered (`plan.offers(subscription, now)`)

- **Monthly:** offered whenever a payment can be made.
- **Launch Promo:** offered only while its own switch `BILLING_LAUNCH_PROMO_ENABLED` is on, inside the promo's dates if dates are set, and only to a subscription that has never had a paid cycle. Its dates and who is eligible are question 6; no date is in code.
- Both rules live in this one function, so the answers to questions 4 and 6 change one place.

## 7. The API for the dashboard Billing page

All under `/v1/`, signed in, through `site_for`: a missing site, a removed site and another account's site answer the same 404 before any billing row is read. A guessed payment id, or a payment of another site, answers 404. With `BILLING_BANK_TRANSFER_ENABLED` off (or `BILLING_ENABLED` off) every route below answers 404 and no new table is read. They stay open to a suspended or ended site: the billing routes are not under the Billing-only rule (D11).

`GET sites/{site_id}/billing/` is **unchanged** in shape and in what it reads.

### 7.1 `GET sites/{site_id}/billing/payment/`

What the page needs to show "how to pay".

```json
{
  "payable": true,
  "not_payable_reason": null,
  "offers": [
    {
      "plan": "launch_promo",
      "name": "NegosyoOnline Launch Promo (6 Months)",
      "amount_centavos": 499900,
      "currency": "PHP",
      "months": 6,
      "period_start_at": "2026-11-25T02:10:00Z",
      "period_end_at": "2027-05-25T07:00:00Z",
      "then": {"plan": "monthly", "amount_centavos": 249900, "interval": "month",
               "first_due_at": "2027-05-25T07:00:00Z"}
    },
    {"plan": "monthly", "name": "NegosyoOnline Monthly", "amount_centavos": 249900,
     "currency": "PHP", "months": 1, "period_start_at": "…", "period_end_at": "…", "then": null}
  ],
  "transfer_accounts": [
    {"kind": "bank", "provider_name": "…", "account_name": "…", "account_number": "…"}
  ],
  "under_review": null,
  "latest": null,
  "proof_rules": {"max_bytes": 5242880, "content_types": ["image/jpeg", "image/png", "application/pdf"]}
}
```

- `not_payable_reason`: `under_review` is not one of them (a proof can be replaced). Values: `paid_ahead`, `cancel_scheduled`, `past_deletion_date`, `billing_exempt`.
- `period_start_at` and `period_end_at` are empty for an ended or suspended site, where the period starts at the confirmation time; `starts_at_confirmation: true` is sent instead.
- `transfer_accounts` comes from the setting of question 1, never from a table or from code.
- `under_review` and `latest` are a payment (7.3) or `null`.

### 7.2 `POST sites/{site_id}/billing/payments/`

Multipart form: `plan` (required), `proof` (the file, required), `transferred_on` (`YYYY-MM-DD`, required), `transfer_reference` (optional). Limit: 10 per account per hour (`apps.common.throttle.hit`).

- 201 with the payment (7.3). 200 with the existing payment when the same file is sent again while it is under review.
- Writes the account event `billing_proof_submitted` (`apps.accounts.services.record_event`), with the site and the payment id only.

| Status | Code | When |
|---|---|---|
| 400 | `invalid_request` with `fields` | `proof` missing, too large, or not JPEG, PNG or PDF; `plan` unknown or not offered; `transferred_on` missing, malformed or in the future; `transfer_reference` too long; an unknown field |
| 404 | `not_found` | The site is not the caller's, or the switch is off |
| 409 | `billing_exempt` | The site is exempt |
| 409 | `paid_ahead` | A paid cycle that has not started yet already exists |
| 409 | `cancel_scheduled` | The subscription is cancelled and still inside its paid period |
| 409 | `past_deletion_date` | The last date to pay has passed (D1) |
| 429 | `rate_limited` | The limit above |

### 7.3 A payment, as the API shows it

```json
{
  "id": "btp_…",
  "status": "submitted",
  "plan": "launch_promo",
  "amount_centavos": 499900,
  "currency": "PHP",
  "transferred_on": "2026-11-20",
  "transfer_reference": "…",
  "submitted_at": "…",
  "decided_at": null,
  "reject_reason": null,
  "reject_note": null,
  "proof": {"content_type": "image/jpeg", "size_bytes": 412345},
  "cycle": null
}
```

`cycle` is `{"sequence", "start_at", "end_at"}` once confirmed. The operator's name, the amount the operator typed and the storage key are never sent.

### 7.4 `GET sites/{site_id}/billing/payments/` and `GET …/payments/{payment_id}/`

The list is newest first, paged with `apps.common.pagination.page` (`limit`, `cursor`, answer `{"items": [...], "next_cursor": …}`). The detail is one payment.

### 7.5 `GET sites/{site_id}/billing/payments/{payment_id}/proof/`

The file itself, to the owning Subscriber only, with the headers of 4.4. 404 for anything else.

## 8. The operator's actions

**What exists today for operators** (`apps/operators` on `origin/main`):

1. The hq host (`HQ_HOST`): a two-step sign-in (emailed code, then authenticator) and, behind it, the Django admin lists (`HqAdminSite`, mounted only in `hq_urls.py` at `admin/`). Every list there today is read-only or masked.
2. `/staff/v1/` on the hq host, only while `HQ_STAFF_ORIGIN` is set: `me`, `console`, `sign-in`, `code`, `verify`, `sign-out`. These are the sign-in routes for the separate staff app; no business page is behind them yet.
3. Terminal commands, for example `billing_exempt`, each with an operator and an audit entry.

Which of these carries the review screen is question 2. The plan fixes only what is the same for any surface:

- `services.confirm_transfer(payment, operator, amount_received_centavos)` and `services.reject_transfer(payment, operator, reason, note)`, with the rules of 5.1 and 5.2. `operator` is the staff `User` of a session that passed both steps; `None` is refused.
- `services.review_queue()`: the `submitted` payments, oldest first, with the site's public id and name, the plan, the amount due, the stated transfer date and reference, and how long each has waited.
- `services.proof_for_operator(payment, operator)`: the file, with the headers of 4.4.
- New audit actions in `apps/operators/audit.py`, all `KEPT` (no personal value): `billing_payment_confirmed`, `billing_payment_rejected`, `billing_proof_viewed`. `target_type="site"`, `target_id` the site's public id; `detail` holds the payment id, plan, amount, the cycle sequence (confirmed) or the reason code (rejected). The reject note is not copied into the audit entry.
- With the switch off the surface is closed and reads nothing, as `SwitchedReadOnlyAdmin` does in `apps/payments/admin.py`.

`apps/operators/audit.py` is the only file of another owner this build edits (three lines, as B1 did for `billing_exempt_set`). The build tells the coordinator before that pull request.

## 9. Settings

| Setting | Default | Meaning |
|---|---|---|
| `BILLING_BANK_TRANSFER_ENABLED` | off | "1" turns on everything in this plan, and only while `BILLING_ENABLED` is on |
| `BILLING_LAUNCH_PROMO_ENABLED` | off | "1" offers the promo (6.3) |
| `BILLING_TRANSFER_ACCOUNTS` | empty | The receiving accounts (question 1). Empty means `collection_live()` is false |
| `BILLING_PROOF_DIR` | empty | Local and test only (4.4) |

Each is one line in `config/settings.py` and one row in `docs/runbooks/billing.md`. Setting any of them in a shared environment needs Coach Ed's separate approval.

## 10. Migrations

| Migration | Adds | Safe before it runs? |
|---|---|---|
| `apps/billing/migrations/0002_bank_transfer.py` | `BankTransferPayment`, `BankTransferEvent`, `BillingNotice` and their constraints (4.1 to 4.3) | Yes. It only adds tables, changes no B1 table, and no code reads them while `BILLING_BANK_TRANSFER_ENABLED` is off |

No other migration and no data migration. Order on staging: merge and deploy, the coordinator applies `billing.0002_bank_transfer`, and only then may the switch be asked for. If question 3 is answered with a store that needs a table, that table is a separate migration `0003_bank_transfer_proof_store.py`, written only after the answer.

## 11. Pull requests, in build order

Each is cut from the latest `origin/main`, is dark while the switch is off, and lists the open pull requests and their files before it is opened.

| # | Pull request | Files | Tests |
|---|---|---|---|
| 1 | **Payment record and settlement.** The switch, `collection_live()`, the three tables and `0002`, the proof store interface, submit, replace, confirm, reject, `settle_transfer`, `early=True`, `methods.active_at`, `state_filter`, `clocks_started`, the disposal reason, the three audit actions. Monthly amount only. The scope amendment (section 13) | `apps/billing/{models,plan,methods,services,proofs}.py`, `apps/billing/migrations/0002_bank_transfer.py`, `apps/operators/audit.py`, `config/settings.py`, the scope document, `docs/runbooks/billing.md` | `test_transfer.py`: each row of 5.2; confirm twice; two confirmations at once and confirm against replace (PostgreSQL, as `test_concurrency.py`); amount mismatch; reject changes no subscription row; replace; same file twice; a proof at trial end holds the trial 7 days and no longer; rejected before trial end does not; `state_filter` parity; `clocks_started` once, 30 days, not undone; disposal refused; switch off reads no new table (query count); audit entries; file type by content, size limit |
| 2 | **Subscriber API.** The five routes of section 7 | `apps/billing/api.py` | `test_transfer_api.py`: shapes; every error row of 7.2; Subscriber A gets 404 on B's site, payment and proof; guessed id 404; signed out 401; every route 404 with the switch off; headers of the proof answer; the rate limit; `GET billing/` unchanged byte for byte |
| 3 | **Launch Promo term.** `Plan` rows, `cycle_end(months)`, `offers`, the promo switch | `apps/billing/{plan,clock,services,api}.py`, `config/settings.py` | `test_promo.py`: the four rows of 6.2 and 29 Feb as a start day; promo then Monthly, never promo twice; not offered after any paid cycle; not offered with its switch off; a promo payment confirmed opens one cycle of PHP 4,999.00 and the next offer is PHP 2,499.00; paid early in the trial; paid after the trial ended |
| 4 | **Operator review surface.** After question 2 | By the answer: `apps/billing/admin.py`, or routes beside `staff_views.py` agreed with the SMS coordinator | Only a two-step session reaches it; confirm, reject and view each write their audit entry; closed with the switch off |
| 5 | **Emails and reminders.** After question 8 | `apps/billing/notices.py`, `apps/billing/tasks.py`, the beat schedule in `config/settings.py` | Each email once per key; none to a disposed address; none with the switch off |

Pull requests 1 to 3 need no answer from section 12 to be written and tested locally. Nothing is switched on in a shared environment before questions 1, 2 and 3 are answered. After each merge the staging check is `/healthz/` and, once `0002` is applied and a switch is approved, the routes of section 7 for a test site.

The acceptance criteria map: "exactly one paid cycle of the right length and amount", "a rejected proof changes nothing and can be replaced" (1, 3); "the exact six-month date and PHP 2,499.00 next" (3); "A can never read B's proof or billing data" (2, 4); "switch off: 404 and no new table read" (1, 2); "SQLite and PostgreSQL" (CI runs both, as for B1).

## 12. Questions for Coach Ed

Each has one recommended answer. "Until answered" is what the build does meanwhile.

1. **Which bank or e-wallet account do Subscribers pay to, and where is it kept?**
   - Recommend: he gives the account or accounts (kind, bank or wallet name, account name, account number); they are kept in one Railway variable, `BILLING_TRANSFER_ACCOUNTS`, per environment, and shown only to a signed-in Subscriber on the Billing page.
   - Reason: changing where money is sent is then an approved variable change by him, never a code change or an admin form; staging can hold a test value.
   - Until answered: the setting is empty, so `collection_live()` is false and nothing can be paid anywhere. No account is written in code, tests use made-up test values.
2. **Who confirms a proof, and on which screen: the Django admin lists on hq, or the new HQ staff pages?**
   - Recommend: the Django admin on hq for launch, as one list with a confirm and a reject form; move it to the HQ staff pages when those have their first business page.
   - Reason: it exists today behind the two-step sign-in, and the staff pages have only their sign-in routes. The actions are service functions, so the move changes the screen and nothing else. The SMS coordinator (z8uq9me128) should confirm this does not cut across its plan.
   - Until answered: pull requests 1 to 3 are built; pull request 4 waits. Confirmation can be exercised only in tests.
3. **Where are proof images stored?**
   - Recommend: a private Vercel Blob store (the `negosyoonline-private` of NPC Build 2), read and written only by the backend with a token held in a Railway variable.
   - Reason: he already ruled that files live in Vercel Blob (B1 plan, section 13); Vercel documents private stores that answer only to a token; B5's invoice scans need the same store. It needs his approval of the store, the token variable and possibly one dependency. The alternative, if he wants no new store before launch, is to keep the bytes in a PostgreSQL table of their own (migration `0003`), private by construction, and move them later.
   - Until answered: the local-directory store, for local runs and tests only; the switch cannot be turned on in a shared environment.
4. **Is Monthly at PHP 2,499 also sold by bank transfer at launch, or only the promo?**
   - Recommend: both. The page leads with the promo.
   - Reason: Monthly by bank transfer must exist anyway for month seven (question 5) and for anyone the promo does not cover; offering it costs nothing more.
   - Until answered: both are built; which are offered is one function (6.3).
5. **What does a promo Subscriber pay at month six if no gateway is approved by then?**
   - Recommend: PHP 2,499.00 for one month, by bank transfer with proof, the same way; a reminder before the due date; the 7 days of B1 after it.
   - Reason: it is the only way to pay that will exist, and it keeps the promise "then PHP 2,499.00 monthly". No automatic debit is implied anywhere.
   - Until answered: this is what is built.
6. **When does the promo start and end, and who may take it?**
   - Recommend: it starts when he turns its switch on, ends on a date he names, and is for a site that has never had a paid cycle, once.
   - Reason: "launch offer" needs an end he chooses; "never paid" is checkable from the rows and stops a second PHP 4,999 term.
   - Until answered: the promo has its own switch, off; no date is in code; eligibility is "never had a paid cycle".
7. **How long does the operator have to confirm, and what does the Subscriber see meanwhile?**
   - Recommend: a working target he sets (one business day is suggested) that the page does not promise; the Subscriber sees "under review" with the time it was sent, and can replace the proof. The operator's list shows the oldest first and how long each has waited. An email to one operator address he names, on each new proof, is worth adding.
   - Reason: by the settled rule a waiting proof holds a site only inside the 7 days, so a slow review can suspend a Subscriber who paid on time; the target and the alert are what prevent that. A promised time would be a customer promise.
   - Until answered: no time is shown or promised and no operator email is sent; the queue shows the waiting time.
8. **What do the emails say?**
   - Recommend: he approves wording for four: payment confirmed (plan, amount, period), proof not accepted (reason, how to send another), renewal reminder (amount, due date, how to pay; 7 days and 3 days before), and the trial reminders of B1's D10 (3 days and 24 hours before trial end), which now point to the Billing page.
   - Reason: each is a statement to a customer about money. The reminder days are a suggestion that leaves room for a review.
   - Until answered: no email is sent and pull request 5 waits. No wording is drafted in this plan.

Found in the sources while planning, and owed with the above:

9. **A first site created after its account's day 44 (B1's D2 gap).** Once collection is live its deletion date is already past when it is created, so it can never be paid for. Recommend: for a site created after its trial has ended, count the 30 days to pay from the site's creation. Reason: the same reasoning as R-4a; the person could not have paid earlier. Until answered: nothing is built for it; such a site gets 409 `past_deletion_date`.
10. **Paying during the trial.** Recommend: the paid period starts when the trial ends, so the free days are kept. Reason: the Parent's example counts the six months from the service start, and starting at the confirmation would take trial days away from someone who pays early. Until answered: built as recommended (5.2, first row).
11. **A transfer of the wrong amount, or one to be returned.** No rule exists. Recommend: the operator rejects it with the reason, and the money is returned or completed outside the system by him; nothing is built. Until answered: exactly that; no refund, credit or partial payment exists in code.

## 13. Amendment to the scope document

Not in this pull request. Pull request 1 carries it, once the plan is approved:

- Section 7: after "The retry schedule … moves to the Billing Build B2 plan", add that at launch a subscription is paid by bank transfer with proof, confirmed by an operator (B2a, z8uq9me581), behind `BILLING_BANK_TRANSFER_ENABLED`; that a proof under review at the trial-end instant counts as the payment method for the 7 days; and that the Launch Promo is PHP 4,999.00 once for six months, then PHP 2,499.00 monthly.
- Section 8: the routes of section 7 of this plan in the billing row. The Xendit webhook and the "Xendit is the provider" line stay, marked as B2, built when a gateway is approved.
- Section 9, item 4: add B2a before B2; B3 becomes the Annual Plan only.
- Section 12: one dated entry pointing to this plan.

## 14. Not in this build

Any gateway, card, token or webhook. The Annual Plan (B3). Usage and overage (B4). Statements, the BIR register and the "pending manual issuance" item (B5); B5 reads `BankTransferPayment` rows with `status = confirmed`. Referral credits (B6). The dashboard Billing page and the operator's screen design. Refunds, partial payments and credits. Deleting or blanking any payment row or proof. The R-3a hold and the disposal claim. Billing rows in the account export. Any production action.

## 15. Approvals asked

1. This plan, with P1 to P10.
2. Writing migration `0002_bank_transfer` (run on staging by the coordinator after merge, under the standing approval).
3. The scope amendment of section 13.
4. The answers to section 12.

Still to ask one at a time, later: each variable of section 9 in a shared environment; the private store and its token; the operator email address; anything in production.
