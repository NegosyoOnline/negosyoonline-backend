# Billing Build B4: usage ledger and overage (plan)

Status: **plan only, for Coach Ed's approval.** No code, migration, variable or staging action is in this pull request.

ClickUp task: z8uq9mdwrz (https://app.clickup.com/t/z8uq9mdwrz). Parent: z8uq9m8h46. Read against `origin/main` at `52d8b6d`.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9mdwrz, description (no comments, checklists, custom fields or attachments as of 2026-11-11; status was Open) |
| Parent | ClickUp z8uq9m8h46, description and its 4 comments (all about the legacy build) |
| Brief | The coordinator's brief for this builder, 2026-11-11 |
| B1 plan | `docs/superpowers/plans/2026-11-11-backend-billing-b1-subscription-lifecycle.md`: D14, D15, sections 5.2 to 5.4, 13 |
| B2a plans | `...-b2a-bank-transfer-launch-promo.md` and `...-b2a-pr4-hq-finance-page.md` |
| Scope | `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`, sections 6, 7, 8, 9, 10, 12 |
| Code | `apps/billing/{models,plan,clock,services,api,staff,tasks}.py`, `apps/billing/migrations/0001,0002`, `apps/operators/{audit,models}.py` (`OperatorAuditEntry`), `docs/runbooks/billing.md`, `config/settings.py` |
| Contract | P6.1 contract r25, legacy repository. **Not read:** the file was not found on this machine. Every rule below is taken from the Task, which restates the D, Q and L rows. Where a rule needs a row the Task does not quote, it is marked **choice** or put to the coordinator (section 12) |

Open pull requests touching the billing area on 2026-11-11: none. #103 (`apps/operators/audit.py`, `config/settings.py`) and #104 (`config/settings.py`) are from other builds and touch two files this plan also touches (section 11).

## 2. What "After B2" means now

The Task says "After B2". B2 (z8uq9mdwrw, the gateway, cards, webhooks, retries) is Open and, by Coach Ed's ruling, off the launch path. What B4 needs from "B2" is only this: **a cycle that is paid, pending or lapsed, a payment that settles it, and an operator who confirms it.** B2a is merged and supplies exactly that:

| B4 needs | Where it is today |
|---|---|
| `BillingCycle.settlement` (`not_billable`, `pending`, `paid`, `lapsed`) and the cycle dates | B1, `models.py` |
| A payment that settles a cycle only on an operator's confirmation | B2a, `confirm_transfer` -> `_settle_transfer` |
| The amount of a payment frozen when it is sent, and checked at confirmation | B2a, `BankTransferPayment.amount_centavos`, constraint `billing_transfer_confirmed_in_full` |
| `collection_live()` | B2a, `plan.py` |
| An operator route pattern in the HQ staff app | B2a PR 4, `apps/billing/staff.py` (`ROUTES`, `finance/...`) |
| `OperatorAuditEntry`, `audit.record`, declared actions | NPC Build 6 |

So B4 starts now. It does not wait for the gateway B2. It **does** change the amount B2a collects (section 7): that is the one place where B4 edits B2a's code. The annual usage-only route still needs B3 (section 11).

## 3. Deliverable, acceptance and scope, traced

| Deliverable | Source |
|---|---|
| `UsageEntry` and `ChargeLine`, no `video` kind | Task, "Adapted to the new backend" |
| One intake function per kind (orders, storage), nothing outside billing computes an allowance or an overage | Task, "Adapted" |
| The order allowance (1,000 per cycle, PHP 5 after) and its exclusion rules | Task, "Settled rules", "Orders" |
| The storage snapshot (5 GB, PHP 30 per started GB, immutable) | Task, "Storage" |
| The late (72 hours), held, review and write-off routes, with audit | Task, "Timing, holds and review" |
| When overage is billed: with the renewal; on a usage-only final collection; annual later | Task, "When overage is billed" |
| `GET sites/{site_id}/billing/usage/` | Task, "Adapted" |
| The operator review actions, audited with `OperatorAuditEntry` | Task, "Needs Coach Ed's separate approval" |
| Amendments to scope sections 6 and 7 | Task, "Adapted" |

Accepted when (the Task, in its words): 1,000 orders give zero overage, the 1,001st gives exactly one immutable PHP 5 record, a replay never double counts; the exclusion rules are tested through the intake function; a row past 72 hours, after service end, on a suspended site or in an unpaid cycle follows its ruled route and leaves an audit record; storage rounds up per started GB and is never recomputed; tests pass in CI on SQLite and PostgreSQL.

**Not in B4** (the Task): the order and media producers; transactional SMS; statements and PDFs (B5); Membership Pack rules; video; the annual plan itself (B3); the dashboard page for usage; platform renditions in the storage meter (L13, section 9).

## 4. Decisions in this plan

| # | Decision | Kind |
|---|---|---|
| U1 | Three new tables in `apps/billing`: `UsageEntry`, `ChargeLine`, `UsageEvent`. Nothing is added to a B1 table | Task; choice for `UsageEvent` (the append-only audit of every routing decision, the pattern of `SubscriptionTransition`) |
| U2 | A **period** is a monthly allowance window. For a one-month cycle it is the cycle. For a longer cycle (the Launch Promo's six months; B3's annual) it is each month of the cycle, cut by `clock.cycle_end(start, anchor, 1)`. The 1,000 and 5 GB apply per period | Task: "each monthly allowance period" (annual). **Question 1** asks Coach Ed to confirm it for the promo |
| U3 | One `ChargeLine` per overage order (PHP 5.00, 500 centavos) and one per storage snapshot. A line is immutable in what it charges and where it came from; it moves from `open` to `settled` or `written_off` once, and never back | Task: "exactly one immutable PHP 5 record" |
| U4 | An order is numbered by the order it reaches the intake function, under the subscription's row lock. The 1,001st to arrive in a period carries the first fee | choice. The Task counts "processed" orders and gives no other order |
| U5 | Overage is billed on the next collection that follows the close of its period, and a collection takes the lines that exist when the proof is sent (section 7) | Task, "When overage is billed"; choice for the freezing detail |
| U6 | `GB` is 1,073,741,824 bytes (binary) | choice; one constant in `plan.py` |
| U7 | `BILLING_USAGE_ENABLED`, off by default. Off: the intake functions write nothing and answer `disabled`, `GET billing/usage/` answers 404, B2a's amounts are unchanged | Task: "any flag" needs separate approval. **Needs his yes** |
| U8 | Storage is **not metered** until the media upload task decides L13. A constant `plan.STORAGE_METERED = False` makes `record_storage_snapshot` answer `not_metered` and write nothing. Everything else about storage is built and tested with the constant patched | Task, L13 |
| U9 | Review actions are POST routes in the HQ staff app under `finance/usage/...`, next to B2a's `finance/payments/...`. No Django admin screen | Brief; scope amendment 2026-11-11 (hq is the only admin door). The page itself is a frontend task. **Flagged to the coordinator** |
| U10 | Each operator action writes `OperatorAuditEntry` through `audit.record`, with a declared action kept whole and a reason | Task; NPC Build 6 |

## 5. Tables

All in `apps/billing`. Money is integer centavos with a currency code. Write only through `apps/billing/usage.py` (new file; it calls the lock and the clock helpers in `services.py`, so the lifecycle and the ledger share one lock order: Subscription, then entries).

### 5.1 `UsageEntry`

One row per source record the ledger has decided about.

| Column | Notes |
|---|---|
| `subscription` | FK, `PROTECT` |
| `cycle` | FK to `BillingCycle`, `PROTECT`. The cycle that holds the placed time (rule R1). Null for a row in no cycle |
| `period_index` | Small integer, 0 for a one-month cycle (U2) |
| `kind` | `orders` or `storage`. No `video` |
| `source_reference` | Unique, at most 64 characters, given by the producer (for example `order:ord_...`, `storage:<site>:<cycle end>`). The idempotency key of the whole intake |
| `quantity` | `orders`: 1. `storage`: the measured bytes |
| `occurred_at` | `orders`: the durable order-placed time. `storage`: the instant measured |
| `received_at` | When the intake wrote it |
| `status` | `counted`, `held`, `needs_review`, `not_billable`, `written_off` (5.4) |
| `reason` | A short code (5.4). Empty for a plain `counted` row |
| `ordinal` | `orders`, status `counted` only: 1, 2, 3 ... within the period. Null otherwise |
| `detail` | JSON: the facts the producer gave (channel, outcome, payment-verified time), never a buyer or a personal value |

Constraints: unique `source_reference`; unique (`cycle`, `period_index`, `ordinal`) where `ordinal` is not null; unique (`cycle`, `period_index`) where `kind = storage` and status is `counted` (one snapshot per period); `ordinal` set only for `orders` and `counted`; `quantity` positive. Rows are never deleted. A row changes only along the transitions in 5.4, each one written as a `UsageEvent`.

### 5.2 `ChargeLine`

| Column | Notes |
|---|---|
| `subscription`, `cycle`, `period_index` | As the entry |
| `entry` | One-to-one to the `UsageEntry` that caused it, `PROTECT`. This is what makes a replay unable to charge twice |
| `kind` | `overage_order` or `overage_storage`. B5 and B6 add `base` and `referral_credit` to the scope document's list when they build them; B4 builds neither |
| `quantity` | `overage_order`: 1. `overage_storage`: the started GB charged |
| `amount_centavos`, `currency` | 500 per order; 3,000 per started GB |
| `status` | `open`, `settled`, `written_off` |
| `settled_payment` | FK to `BankTransferPayment`, set once with `settled` |
| `closed_at`, `closed_reason` | Set once with `settled` or `written_off`. A write-off keeps the cause (L41) |
| `labels` | JSON list, for example `["late_admitted"]`, so B5 can print "labelled adjustment" without guessing |
| `created_at` | |

Constraints: unique `entry`; amount positive; `settled` needs `settled_payment`; `written_off` needs `closed_reason`. `save()` refuses a change to any column but the three close columns and `status`, and refuses a second close, as `OperatorAuditEntry` refuses an update.

### 5.3 `UsageEvent`

Append-only, one row per decision: `entry`, `code`, `actor` (`intake`, `sweep`, `operator`), `idempotency_key` (unique), `recorded_at`, `detail` (reasons, the operator's reason, the audit entry id). It is the audit record the Task asks for on the system's routes; an operator's action also writes `OperatorAuditEntry` (U10).

### 5.4 Statuses and reasons

| Status | Reason codes | Meaning |
|---|---|---|
| `counted` | | Has an ordinal (orders) or is the storage snapshot of the period; its line exists if it is over the allowance |
| `held` | `cycle_pending` | The cycle is `pending` (payment needs attention). Nothing is numbered or charged yet (L36) |
| `needs_review` | `late_over_72h`, `after_service_end`, `no_cycle`, `payment_time_unavailable` | An operator decides (section 8). Never counts, never discarded |
| `not_billable` | `trial_cycle`, `test_site` | Skipped on purpose and kept for audit (Q1; the trial rule of L1) |
| `written_off` | `cycle_lapsed`, `after_final_settlement`, `reviewed_not_billable` | Valid, but not charged, with its cause (L36, L16, L21, L41) |

## 6. The intake functions

`apps/billing/usage.py`, called by commerce and media later. They are the only code that numbers an order or prices an overage (Task). Both take a keyword-only `now` for tests and return a small result object (`status`, `reason`, `ordinal`, `charge_centavos`, `replayed`); none of them raises for a business outcome, because a producer must not retry a decision.

### 6.1 `record_order(site, *, source_reference, placed_at, channel, outcome, payment_verified_at=None, now=None)`

- `channel`: `direct` or `xendit`.
- `outcome`, the producer's fact about the order: `placed`, `cancelled` (a cancelled created order still counts), `proof_rejected` (D3, counted once), or a non-counting outcome: `checkout_failed`, `abandoned`, `payment_expired`, `webhook_unsuccessful`, `payment_wrong_amount`, `payment_closed`, `payment_replaced` (Q2).
- **Writes nothing for a non-counting outcome.** It answers `not_counted` with the outcome. Nothing is stored because the order may still become countable: the producer calls again when it does, with the same `source_reference`.
- `xendit` counts only after verified payment (D2): `outcome = placed` with no verified payment answers `not_counted/payment_not_verified`. Verified with no `payment_verified_at` is L18: a `needs_review` row, reason `payment_time_unavailable`.

Order of decisions, each in the transaction that holds the subscription's row lock (the lock B1's `_write` takes), after `catch_up(subscription, now)`:

1. **Replay.** If `source_reference` exists, return its row's result unchanged (`replayed=True`). Nothing is written, nothing is counted twice.
2. **Switch.** `BILLING_USAGE_ENABLED` off: `disabled`, nothing written.
3. **No subscription:** `not_counted/no_subscription`, nothing written.
4. **Test site (Q1).** `billing_exempt` (B1 D7): a `not_billable/test_site` row. Always, whatever the dates.
5. **Cycle (R1).** The cycle with `start_at <= placed_at < effective_end`, where `effective_end` is `end_at`, or the service end if service ended earlier. The placed time decides, never the intake time or the payment time (L1). A delayed webhook cannot move an order across a cutoff.
   - Trial cycle: `not_billable/trial_cycle`. An order created in the trial and verified after it is the same row.
   - No cycle holds the time (after service end, in the gap of a suspension, after a final cycle): `needs_review/after_service_end` (L6; the suspended-site case of L20 is this same route). It never counts and creates no overage.
6. **Late (L2).** `now` more than 72 hours after `effective_end` of that cycle: `needs_review/late_over_72h`.
7. **Cycle state (L5, L36).**
   - `pending`: `held/cycle_pending`.
   - `lapsed`: `written_off/cycle_lapsed`, with the cause (L5: usage in an unpaid cycle is never billed).
   - `paid`: continue.
8. **Number it (U4).** `ordinal = 1 + the highest ordinal in the period`. Over 1,000: add a `ChargeLine` of 500 centavos, `open`. No check ever blocks or slows checkout: the function never raises at the 1,000th order.
9. Write the `UsageEvent` (key `intake:<source_reference>`).

When the cycle that holds a `held` row resolves (hook in `services.recovered` and in the lapse in `_lapse_pending`, plus the sweep's `suspended_after_attention`), the same transaction calls `usage.release_held(cycle, paid)`: **paid** numbers the held rows in order of `occurred_at` and creates their lines; **lapsed** writes them off with `cycle_lapsed`. L36 says "approved route"; this is it.

### 6.2 `record_storage_snapshot(site, *, source_reference, cycle_end_at, measured_bytes, measured_at, now=None)`

- Not metered (U8): `not_metered`, nothing written (L13). Switch off: `disabled`.
- Measured once, at the exact end of the cycle (Task): `measured_at` must equal that cycle period's end; a different instant answers `not_counted/not_at_cycle_end`, nothing written.
- At or below 5 GB: no source record, `within_allowance` (Task). Unmeasured cycle: nothing exists, so no snapshot, hold or write-off (L49).
- Above 5 GB: one `UsageEntry` (`storage`, `quantity` = bytes) and one line: `ceil((bytes - 5 GB) / GB)` started GB at 3,000 centavos. The snapshot is immutable; a second call for the same period, whatever the bytes, returns the first (`replayed`). **Nothing recomputes a historic cycle**: there is no function that does.
- Routes as 6.1 steps 4 to 7: exempt site, trial cycle (no row: storage in a trial has no cycle to bill), late over 72 hours, held in a `pending` cycle, written off in a `lapsed` one.
- D4 and Q3 (merchant business media only) is the producer's input, not billing's. The function's contract says so; billing cannot tell a receipt from a banner.

## 7. Where the money goes: collections

B2a's `submit_transfer` freezes `amount_centavos` = the plan price. B4 makes the frozen amount **price + the open overage that is due**, and keeps B2a's rule that only an operator's confirmation settles anything.

- **What is due.** The `open` lines of periods that have ended at `now` and that no confirmed payment holds. A line of a period still running is not due, so a payment sent before the period ends does not include it; it is taken by the next collection (U5; **Question 2**).
- **Freezing.** `BankTransferPayment` gains `usage_centavos` (the overage inside `amount_centavos`) and `usage_line_max_id` (the highest `ChargeLine.id` included; the period test above is repeated at confirmation). A replaced or rejected proof frees nothing, because nothing was attached; the next proof recomputes.
- **Confirmation.** `confirm_transfer` recomputes the same set (`id <= usage_line_max_id`, period ended at the time frozen). If its total differs from `usage_centavos` the confirmation answers 409 `usage_changed` and settles nothing; the operator rejects it and the owner sends the new amount. Otherwise it settles the cycle (B2a) and marks each line `settled` with this payment, in one transaction, with the audit entry B2a already writes (its `detail` gains `usage_centavos`).
- **`payment_options` and the owner's payment response** gain `usage: {centavos, lines}` so the page shows base and overage apart. `amount_centavos` per offer includes it.
- **A late line.** A line created after a payment froze (an operator admitted a late order, section 8) has a higher id, so it is not in that payment. It stays `open` and is taken by the next collection, labelled `late_admitted` (L7, L12, L16: never an alteration of a settled amount).
- **Final usage-only collection.** When service ends with a paid period (B1: cancelled paid cycle, or a final cycle) and open lines remain, the subscription can pay them with no further base fee: `plan_code = usage_only`, no cycle, `amount_centavos` = the lines. A failed or missing payment starts no new cycle (Task). Constraint `billing_transfer_confirmed_in_full` is replaced by one that allows a confirmed `usage_only` payment with no cycle. **Question 3** asks whether this payment route belongs in B4 or in B5's statements.
- **Suspended, then cancelled, then paid (R-4d).** The lines of the last paid cycle stay `open` and are collected by the usage-only route. **Suspended and never back (R-4c):** the account owes nothing; the sweep writes the lines off with `closed_reason = suspension_unreturned` at `deletion_due_at` (B1 D14). **Question 4** confirms this reading.
- **Closed and settled (L16, L21).** If a line would be created for a subscription that is cancelled with no `open` line and a settled final usage collection, or whose period's lines were all settled and the account is closed, it is created as `written_off/after_final_settlement` at once. No debit statement, no amendment, a reasoned record.
- `GET sites/{site_id}/billing/`'s `outstanding_centavos` (hard-coded 0 today, `services.py:302`) becomes the total of due `open` lines. The route's shape does not change.

## 8. Operator review

Reasons `late_over_72h` and `payment_time_unavailable` need an operator. `after_service_end` and `no_cycle` need one to record that nothing is owed (L6 forbids counting them). All of it is POST routes added to `billing_staff.ROUTES`, under the same rules as B2a's (origin, session, closed to a 404 while `BILLING_USAGE_ENABLED` is off, no id in an address):

| Route | Does |
|---|---|
| `finance/usage/review/list/` | The `needs_review` rows, oldest first, paged. Site id, kind, reason, the dates, the quantity. No buyer data |
| `finance/usage/review/detail/` | One row with its events |
| `finance/usage/review/admit/` | `late_over_72h` and `payment_time_unavailable` only. Needs a reason. Numbers the order into its cycle and creates its line, labelled `late_admitted` (section 7), or writes it off with `after_final_settlement` when section 7's closed-and-settled rule applies. The cycle must be `paid`; a `pending` one makes it `held`, a `lapsed` one `written_off/cycle_lapsed` |
| `finance/usage/review/dismiss/` | Any `needs_review` row. Needs a reason. Becomes `written_off/reviewed_not_billable`: kept, never counted, never reactivates service |

Each action takes the row lock, is idempotent under a key, writes a `UsageEvent` and one `OperatorAuditEntry` (`billing_usage_admitted`, `billing_usage_dismissed`, declared kept whole in `apps/operators/audit.py`, reason in the entry's `reason`). The HQ page for it is frontend work and needs its own task; where it sits is **Question 5**. There is no Django admin screen.

## 9. The owner's read: `GET sites/{site_id}/billing/usage/`

Signed in, through `site_for` (another account's or a removed site answers 404 before any billing row is read), no body. 404 while the switch is off, or the site has no subscription.

```
{
  "cycle":   {"sequence": 2, "start_at": "...", "end_at": "..."},
  "period":  {"index": 0, "start_at": "...", "end_at": "..."},
  "orders":  {"included": 1000, "used": 1012, "remaining": 0, "overage_count": 12,
              "overage_centavos": 6000, "projected_centavos": 6000, "held": 0, "pending_review": 0},
  "storage": {"metered": false, "included_gb": 5, "last_snapshot": null}
}
```

`used` and `overage_count` are counted rows only; held and under-review rows are shown as counts, never as charges. `last_snapshot` is the frozen result of the last closed period (`period_end_at`, `measured_bytes`, `billed_gb`, `amount_centavos`) or null. `projected_centavos` is the overage of rows already counted (no forecast). Trial: the cycle is the trial and every figure is zero with `billable: false`. No row number, no source reference, no producer detail. Throttled per account as B1's reads are.

## 10. Migrations (each needs Coach Ed's approval)

| Migration | Adds | Safe before it runs? |
|---|---|---|
| `0003_usage_ledger` (depends on `0002_bank_transfer`) | `UsageEntry`, `ChargeLine`, `UsageEvent`, their constraints | Yes. It only adds tables, and no code reads them while `BILLING_USAGE_ENABLED` is off |
| `0004_transfer_usage` (depends on 0003) | `BankTransferPayment.usage_centavos` (default 0), `usage_line_max_id` (default 0), and the replaced `billing_transfer_confirmed_in_full` constraint (U3, section 7). | Yes for the columns. The constraint swap is validated against existing rows, which satisfy both forms (confirmed rows all have a cycle) |

Two migrations because the first only adds tables and the second touches a B2a table that staging already holds rows in; each can be run, and checked, on its own. If question 3 is answered "not in B4", `0004` loses the constraint swap and the `usage_only` plan. **Numbers are placeholders:** B3 and B5 also add `apps/billing` migrations; whichever merges second renames its file and its `dependencies` (section 11). Run on staging by the coordinator under the standing approval, never in production.

## 11. Files touched and order against B3 and B5

New: `apps/billing/usage.py`; `apps/billing/migrations/0003_usage_ledger.py`, `0004_transfer_usage.py`; `apps/billing/tests/test_usage_orders.py`, `test_usage_storage.py`, `test_usage_routes.py`, `test_usage_collection.py`, `test_usage_review.py`, `test_usage_api.py`, `test_usage_concurrency.py`; a `docs/runbooks/billing.md` section.

Changed in B4's pull requests, **shared with other builds**:

| File | Change | Shared with |
|---|---|---|
| `apps/billing/models.py` | Three models; two columns on `BankTransferPayment` | B3 (plan/annual), B5 (statements, `BillingProfile`) |
| `apps/billing/services.py` | `usage.release_held` hooks in `recovered` and `_lapse_pending`; `submit_transfer`, `payment_options`, `confirm_transfer` amounts; `overview.outstanding_centavos` | B3 (annual in `_settle_transfer`), B5 |
| `apps/billing/plan.py` | The allowance and price constants, `STORAGE_METERED`, a `usage_only` pseudo-plan | B3 |
| `apps/billing/clock.py` | `allowance_periods(cycle)` | B3 (`months`) |
| `apps/billing/api.py`, `staff.py` | The usage route; four staff routes | B5 |
| `apps/operators/audit.py` | Two declared actions | Open PR #103 |
| `config/settings.py` | `BILLING_USAGE_ENABLED` | Open PRs #103, #104 (added lines only; second to merge keeps both) |
| Scope document | The amendment below | B1 amendment, B2a amendment |

**Order.**
1. **B4 before B5.** B5 reads `ChargeLine` and the `usage_only` payment to print overage lines and the "labelled adjustment".
2. **B4 and B3.** B3's plan (annual, the six-month promo term) and B4 meet at three points: `clock.allowance_periods` (B4 writes it for any `months`; B3 only has to set `months = 12`); `_settle_transfer` (B3 adds a plan, B4 does not touch the plan switch); `plan.PLANS`. They do not need to be serialised if each keeps to its lines. If B3 merges first, B4 rebases its `plan.py` and `models.py` edits; if B4 merges first, B3 does. The annual usage-only route (each monthly period's overage on a usage-only collection the following month) is **B3's pull request**, built on B4's `usage_only` payment and `allowance_periods`; B4 builds and tests the same route for the Launch Promo (U2).
3. Migrations: `0003` and `0004` are written with the lowest free numbers at the time of the pull request. A conflict is resolved by renaming the later file and its `dependencies`, never by editing an applied one.

**Pull requests** (each cut from the latest `origin/main`, small, dark behind `BILLING_USAGE_ENABLED`):

1. **Ledger and order intake.** Tables, migration `0003`, `plan` constants, `allowance_periods`, `record_order`, `release_held`, the amendment. Tests: sections 12 (orders, routes).
2. **Storage intake.** `record_storage_snapshot`, `STORAGE_METERED`. Tests: storage.
3. **Collections.** Migration `0004`, the amounts in B2a's functions, the `usage_only` payment (if question 3 says so), the write-offs, `outstanding_centavos`.
4. **Review routes and the owner's read.** Staff routes, the two audit actions, `GET billing/usage/`, runbook.

## 12. Tests

All time is passed in; no test reads the clock. Every exclusion is driven through `record_order`, never by creating a ledger row by hand (Task).

**Orders**
- 1,000 `placed` orders: zero lines. The 1,001st: exactly one line of 500 centavos, `open`. The 1,002nd: a second. A replay of any of them (same reference, same or different facts) returns the first result, and counts, lines and events are unchanged.
- The allowance resets on renewal: the first order of the next paid cycle is ordinal 1, though the calendar month did not change.
- Non-counting outcomes (each of `checkout_failed`, `abandoned`, `payment_expired`, `webhook_unsuccessful`, `payment_wrong_amount`, `payment_closed`, `payment_replaced`): nothing is written; the same order later sent as `placed` and verified counts once. `cancelled` and `proof_rejected` count once.
- `xendit` without verified payment: not counted. Verified with a time: counted in the cycle of the **placed** time. Verified with no time: `needs_review/payment_time_unavailable`, no ordinal.
- Placed one second before and exactly at a cycle end: each in its own cycle even when received days later (L1). Placed in the trial and received after it: `not_billable/trial_cycle`.
- Test site (exempt): `not_billable/test_site`, no ordinal, no line, with its event.
- Late: received 72 hours after the end counts; one second later is `needs_review/late_over_72h` (boundaries of the 3:00 PM Manila cutoff, including a cycle that ended at a month-end anchor).
- After service end, in a suspension gap, on a suspended site, after a final cycle: `needs_review/after_service_end` or `no_cycle`; never counted, no line; reactivation is not triggered.
- `pending` cycle: `held`, no ordinal, no line. Then `recovered`: held rows are numbered in `occurred_at` order and the overage line appears on the right one. Then `lapsed`: `written_off/cycle_lapsed` and the cause is in the event.
- Periods: a six-month promo cycle numbers per month; order 1,001 in month 2 is a fee, order 1,000 in month 3 is not.
- Checkout is never blocked: the function returns normally at the 1,000th and 1,001st order.
- Switch off: every function writes nothing and answers `disabled`.

**Storage**
- 5 GB exactly and below: no row. 5 GB + 1 byte: one started GB, 3,000. 6 GB: 3,000. 6 GB + 1 byte: 6,000. 5 GB + 1 GB + 1: 6,000.
- Immutable: a second snapshot for the period with other bytes returns the first; no function recomputes; the line is unchanged.
- Not at the period end: refused, nothing written. `STORAGE_METERED = False`: `not_metered`, nothing written. An unmeasured period: no row at all (L49).
- Exempt, trial, late, pending, lapsed: as orders.

**Collections** (`test_usage_collection.py`, with B2a's helpers)
- A renewal payment's frozen amount is price + the overage of ended periods; a period still running is excluded and shows up on the next collection.
- Confirm settles the cycle and exactly those lines in one transaction; a line created after the freeze is untouched; a change between freeze and confirmation answers `usage_changed` and settles nothing.
- A replaced or rejected proof attaches nothing; the next one recomputes.
- Usage-only: no base, no cycle, a failed or missing payment starts no new cycle; R-4d (suspended, then cancelled) is collected; R-4c writes off at `deletion_due_at` with the cause; a line for a closed, settled account is written off at once (L16, L21). A paid or final statement is never altered (there is nothing to alter).
- Existing B2a payment tests pass unchanged with the switch off.

**Review and API**
- Each staff route: signed out 401, switch off 404, wrong origin 403 (B2a's route tests extended). Admit makes one line, labelled; a second admit is a replay; admit is refused for `after_service_end`; dismiss writes off; both write a `UsageEvent` and an `OperatorAuditEntry`; no personal value in either.
- `GET billing/usage/`: signed out 401; another account's site, a removed site and a made-up id answer one identical 404; the shape above; counts of held and under-review rows; nothing else leaks. It is added to the route-inventory and isolation tests; none is loosened.

**Concurrency (PostgreSQL only, skipped on SQLite as B1's are)**
- 50 threads sending orders 990 to 1,040: ordinals 1..N unique, exactly 40 lines.
- The same reference from two threads: one row.
- An order racing `recovered` of its pending cycle: held and released once, never twice.
- Confirmation racing a new overage line: the settled set matches the frozen set.

**Repository checks before each pull request:** `uv run python manage.py makemigrations --check --dry-run`, `check`, `test`, `check --deploy` with `DJANGO_DEBUG=false`; PostgreSQL locally if available, otherwise stated as verified only by CI.

## 13. Amendments to the scope document (proposed, not edited here)

- **Section 6, billing.** Replace the `UsageEntry` and `ChargeLine` lines with: `UsageEntry`: subscription, cycle, period index, kind (`orders`, `storage`; no `video`), source reference (unique), quantity, occurred_at, received_at, status, reason, ordinal. `ChargeLine`: subscription, cycle, period index, one-to-one entry, kind (`overage_order`, `overage_storage`; B5 and B6 add `base` and `referral_credit`), quantity, amount, status (`open`, `settled`, `written_off`), the settling payment, the close cause. `UsageEvent`: append-only decisions. `BankTransferPayment` gains `usage_centavos`, `usage_line_max_id`.
- **Section 7.** After the included-per-cycle bullet add: allowances apply per monthly period; a period's overage is billed on the next collection after it closes; a late-admitted charge goes on the next collection with a label and never alters a settled amount; usage in an unpaid cycle is never billed; storage is not metered until the media upload task decides the platform renditions.
- **Section 8.** `GET sites/{site_id}/billing/usage/` shape (section 9 of this plan).
- **Section 9, item 5.** Names the intake functions.
- **Section 12.** A dated entry for Coach Ed's approval of this plan and what it does not cover.

## 14. Approvals and what is not covered

Asked now: this plan; the two migrations (section 10); the flag `BILLING_USAGE_ENABLED` (U7); the two audit actions (U10); the answers to the questions below.

Not covered, each his own yes, staging only: running either migration; setting the flag; any production action. Not changed: no job, no worker, no Railway variable beyond the flag. Billing and tax records are kept 5 years: nothing here deletes a row.

## 15. Questions

Already put to the coordinator with this plan; each has a recommendation and what continues meanwhile.

1. **The Launch Promo's allowance.** Is it 1,000 orders and 5 GB per month of the six, or per six months? *Recommend per month* (U2), the rule the Task gives for the Annual Plan. If six months: one period of 1,000 across the term and the overage billed at term end.
2. **A payment sent before its period ends.** It cannot include that period's overage. *Recommend* the overage is taken by the next collection (U5). The alternative, refusing early payment while overage is open, would change B2a.
3. **Where does the usage-only payment live?** B4 (this plan: needs `0004`'s constraint swap and a `usage_only` plan) or B5 with the statements. *Recommend B4*, so final overage can be collected before statements exist.
4. **R-4c.** For a paid site that is suspended and never returns, the plan writes its open lines off at the deletion date. Confirm this is the reading.
5. **The review page.** The routes are under `finance/usage/review/...`; the frontend page is a separate task. Is it a second tab on the Finance page or a menu entry of its own?

Routine details I decided (reversible; listed for the coordinator's report): U4 numbering by arrival; U6 binary gigabyte; "arriving after service ended" means a placed time with no cycle, not a late receipt (that is the 72 hour rule); `dismiss` writes a row off rather than adding a fourth status; `UsageEvent` as a third table.
