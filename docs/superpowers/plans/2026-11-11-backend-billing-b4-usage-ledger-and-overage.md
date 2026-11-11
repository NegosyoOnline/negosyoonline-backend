# Billing Build B4: usage ledger and storage overage (plan)

Status: **approved and merged as PR 108 (2026-11-11), amended on 2026-11-12 to drop the order half** (section 2). Docs only: no code, migration, variable or staging action is in the amendment pull request.

ClickUp task: z8uq9mdwrz (https://app.clickup.com/t/z8uq9mdwrz). Parent: z8uq9m8h46. First read against `origin/main` at `52d8b6d`; amended against `7d7b944` (B3 fully merged).

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9mdwrz, description (no comments, checklists, custom fields or attachments; status was Open) |
| Parent | ClickUp z8uq9m8h46, description and its 4 comments (all about the legacy build) |
| Ruling | Coach Ed, 2026-11-11, membership package coordinator chat (z8uq9m92fv): "NegosyoOnline does not bill the Santé owner, they run their own Santé business, we do not meddle with their business, we are a SaaS company, why would we bill on products they sell?" Recorded in his saved rulings as "no per-order billing, SaaS only". Relayed by the coordinator on 2026-11-12 |
| B1 plan | `docs/superpowers/plans/2026-11-11-backend-billing-b1-subscription-lifecycle.md`: D14, D15, sections 5.2 to 5.4, 13 |
| B2a, B3 plans | `...-b2a-bank-transfer-launch-promo.md`, `...-b2a-pr4-hq-finance-page.md`, `...-b3-annual-and-promo-plans.md` |
| Scope | `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`, sections 6, 7, 8, 9, 10, 12 |
| Code | `apps/billing/{models,plan,clock,services,api,staff,tasks}.py`, migrations `0001` to `0005`, `apps/operators/{audit,models}.py` (`OperatorAuditEntry`), `docs/runbooks/billing.md`, `config/settings.py` |
| Contract | P6.1 contract r25, legacy repository. **Not read:** the file was not found on this machine. Every rule below comes from the Task, which restates the D, Q and L rows |

## 2. The 2026-11-12 amendment: orders are dropped

The Task and the first version of this plan metered orders: 1,000 processed orders per cycle and PHP 5 per order after. Coach Ed's ruling of 2026-11-11 (Sources, "Ruling"), made 30 minutes after this plan was approved, says NegosyoOnline does not bill a Subscriber on what the Subscriber sells and that billing is the SaaS plan only. His latest explicit ruling outranks the Task's rule, so:

**Dropped, entirely:** the `orders` usage kind, `record_order`, the 1,000-order allowance, the `overage_order` charge line, the PHP 5 per extra order, the order numbering (`ordinal`), the order exclusion rules (D2, D3, Q1 as applied to orders, Q2, L18), the order tests, and the order block of the usage read. Nothing counts, numbers or prices an order, and none is ever asked of a producer. The Task's own text keeps the order rules (it is the record of the older ruling); this plan is where they stop applying.

**Kept, unchanged in rule:** the ledger, `ChargeLine`, the held, late, review and write-off routes and their audit, the collections and the frozen amount, the operator review routes, the usage read, and **storage** (5 GB included per period, PHP 30 per started GB, one immutable snapshot at the exact end of the period). Storage is platform usage, not a charge on a Subscriber's sales. The cut is not widened beyond orders.

**Needed from nobody else.** Commerce's order producer is no longer a B4 dependency; only the (future) media upload producer is.

## 3. Deliverable, acceptance and scope, traced

| Deliverable | Source |
|---|---|
| `UsageEntry` and `ChargeLine`, kind `storage` only, no `video`, no `orders` | Task, "Adapted"; Ruling |
| One intake function, `record_storage_snapshot`; nothing outside billing computes an allowance or an overage | Task, "Adapted" |
| The storage snapshot (5 GB, PHP 30 per started GB, immutable) | Task, "Storage" |
| The late (72 hours), held, review and write-off routes, with audit | Task, "Timing, holds and review" |
| When overage is billed: with the renewal; on a usage-only final collection; each month of a longer term on a usage-only collection the month after | Task, "When overage is billed" |
| `GET sites/{site_id}/billing/usage/` | Task, "Adapted" |
| The operator review actions, audited with `OperatorAuditEntry` | Task, "Needs Coach Ed's separate approval" |
| Amendments to scope sections 6, 7, 8 and 9 | Task, "Adapted"; section 13 |

Accepted when (the Task, with the order lines struck by the Ruling): storage rounds up per started GB and is never recomputed; a replay never double counts; a snapshot past 72 hours, after service end, on a suspended site, or in an unpaid cycle follows its ruled route and leaves an audit record; the rules are tested through the intake function, not with hand-made ledger rows; tests pass in CI on SQLite and PostgreSQL.

**Not in B4:** orders and any per-order charge (Ruling); the media producer itself; transactional SMS; statements and PDFs (B5); Membership Pack rules; video; the dashboard page for usage.

## 4. Decisions in this plan

| # | Decision | Kind |
|---|---|---|
| U1 | Three new tables in `apps/billing`: `UsageEntry`, `ChargeLine`, `UsageEvent`. Nothing is added to a B1, B2a or B3 table except two columns on `BankTransferPayment` (section 7) | Task; choice for `UsageEvent` (append-only record of every routing decision, the pattern of `SubscriptionTransition`) |
| U2 | A **period** is a monthly allowance window, from B3's `clock.allowance_periods` and `services.allowance_period_at`: a one-month cycle has one, the Launch Promo six, the Annual Plan twelve. The 5 GB applies per period | Task ("each monthly allowance period"); coordinator ruling 2026-11-12 (promo per month); B3 |
| U3 | One `ChargeLine` per storage snapshot over 5 GB (PHP 30 per started GB). A line is immutable in what it charges and where it came from; it moves from `open` to `settled` or `written_off` once and never back | Task |
| U4 | `GB` is 1,073,741,824 bytes (binary) | choice; one constant in `plan.py` |
| U5 | Overage is billed on the next collection that follows the close of its period; a collection takes the lines that exist when the proof is sent (section 7) | Task, "When overage is billed"; coordinator ruling (early payment: next collection) |
| U6 | `BILLING_USAGE_ENABLED`, off by default. Off: the intake writes nothing and answers `disabled`, `GET billing/usage/` answers 404, B2a and B3 amounts are unchanged | Task: any flag needs separate approval; coordinator relayed it as dark behind the flag |
| U7 | Storage is **not metered** until the media upload task decides L13. A constant `plan.STORAGE_METERED = False` makes `record_storage_snapshot` answer `not_metered` and write nothing. Everything else is built and tested with the constant patched | Task, L13 |
| U8 | Review actions are POST routes in the HQ staff app under `finance/usage/...`. No Django admin screen. The page itself is a frontend task | Brief; scope amendment 2026-11-11 (hq is the only admin door) |
| U9 | Each operator action writes `OperatorAuditEntry` through `audit.record`, declared kept whole, with a reason | Task; NPC Build 6 |

## 5. Tables

All in `apps/billing`. Money is integer centavos with a currency code. Write only through `apps/billing/usage.py` (new file; it takes the subscription's row lock the way `services._write` does, so the lifecycle and the ledger share one lock order: Subscription, then entries).

### 5.1 `UsageEntry`

One row per snapshot the ledger has decided about. A snapshot at or below 5 GB, or never measured, has no row (Task; L49).

| Column | Notes |
|---|---|
| `subscription` | FK, `PROTECT` |
| `cycle` | FK to `BillingCycle`, `PROTECT`. Null for a row in no cycle |
| `period_index` | 1-based, as `allowance_period_at` reports |
| `kind` | `storage`. The only value; the column stays because the scope document has it and a later meter would need it |
| `source_reference` | Unique, at most 64 characters, given by the producer (for example `storage:<site>:<period end>`). The idempotency key of the intake |
| `quantity` | The measured bytes, positive |
| `occurred_at` | The instant measured |
| `received_at` | When the intake wrote it |
| `status` | `counted`, `held`, `needs_review`, `not_billable`, `written_off` (5.4) |
| `reason` | A short code (5.4). Empty for a plain `counted` row |
| `detail` | JSON: the facts the producer gave, never a personal value |

Constraints: unique `source_reference`; unique (`cycle`, `period_index`) where status is `counted` (one snapshot per period); `quantity` positive. Rows are never deleted. A row changes only along the transitions in 5.4, each written as a `UsageEvent`.

### 5.2 `ChargeLine`

| Column | Notes |
|---|---|
| `subscription`, `cycle`, `period_index` | As the entry |
| `entry` | One-to-one to the `UsageEntry` that caused it, `PROTECT`. This is what makes a replay unable to charge twice |
| `kind` | `overage_storage`. B5 and B6 add `base` and `referral_credit` when they build them |
| `quantity` | The started GB charged |
| `amount_centavos`, `currency` | 3,000 per started GB |
| `status` | `open`, `settled`, `written_off` |
| `settled_payment` | FK to `BankTransferPayment`, set once with `settled` |
| `closed_at`, `closed_reason` | Set once with `settled` or `written_off`. A write-off keeps the cause (L41) |
| `labels` | JSON list, for example `["late_admitted"]`, so B5 can print "labelled adjustment" without guessing |
| `created_at` | |

Constraints: unique `entry`; amount positive; `settled` needs `settled_payment`; `written_off` needs `closed_reason`. `save()` refuses a change to any column but `status` and the close columns, and refuses a second close.

### 5.3 `UsageEvent`

Append-only, one row per decision: `entry`, `code`, `actor` (`intake`, `sweep`, `operator`), `idempotency_key` (unique), `recorded_at`, `detail` (reasons, the operator's reason, the audit entry id). It is the audit record the Task asks for on the system's routes; an operator's action also writes `OperatorAuditEntry` (U9).

### 5.4 Statuses and reasons

| Status | Reason codes | Meaning |
|---|---|---|
| `counted` | | The snapshot of the period; its line exists |
| `held` | `cycle_pending` | The cycle is `pending` (payment needs attention). Nothing is charged yet (L36) |
| `needs_review` | `late_over_72h`, `after_service_end`, `no_cycle` | An operator decides (section 8). Never charged by itself, never discarded |
| `not_billable` | `trial_cycle`, `test_site` | Skipped on purpose and kept for audit (Q1; L1) |
| `written_off` | `cycle_lapsed`, `after_final_settlement`, `suspension_unreturned`, `reviewed_not_billable` | Valid, but not charged, with its cause (L36, L16, L21, L41, R-4c) |

## 6. The intake: `record_storage_snapshot`

`apps/billing/usage.py`, called by media upload later. It is the only code that prices storage overage (Task). It takes a keyword-only `now` for tests and returns a small result object (`status`, `reason`, `charge_centavos`, `replayed`); it does not raise for a business outcome, because a producer must not retry a decision.

`record_storage_snapshot(site, *, source_reference, period_end_at, measured_bytes, measured_at, now=None)`. Decisions, in this order, in the transaction that holds the subscription's row lock, after `catch_up(subscription, now)`:

1. **Replay.** If `source_reference` exists, return its row's result unchanged (`replayed=True`). A second snapshot for the same period under a new reference returns the first (`replayed`), whatever the bytes. **Nothing recomputes a historic period**: there is no function that does.
2. **Switch (U6).** Off: `disabled`, nothing written.
3. **Not metered (U7).** `not_metered`, nothing written (L13).
4. **No subscription:** `not_counted/no_subscription`, nothing written.
5. **Exact end (Task).** `measured_at` must equal `period_end_at`, and `period_end_at` must be the end of a period of one of the subscription's cycles (from `clock.allowance_periods`); otherwise `not_counted/not_at_period_end`, nothing written.
6. **Within allowance (Task).** At or below 5 GB: `within_allowance`, no row. A period never measured has nothing: no snapshot, hold or write-off (L49).
7. **Test site (Q1).** `billing_exempt`: a `not_billable/test_site` row.
8. **Cycle.** The cycle holding the period. Trial: `not_billable/trial_cycle`. No cycle holds it (after service end, in a suspension gap): `needs_review/no_cycle` or `after_service_end` (L6; the suspended-site case of L20 is this route); it creates no charge and never reactivates service.
9. **Late (L2).** `now` more than 72 hours after the period's effective end (`end_at`, or the service end if service ended earlier): `needs_review/late_over_72h`.
10. **Cycle state (L5, L36).** `pending`: `held/cycle_pending`. `lapsed`: `written_off/cycle_lapsed`, with the cause. `paid`: continue.
11. **Charge.** One `UsageEntry` (`counted`) and one `ChargeLine`: `ceil((bytes - 5 GB) / GB)` started GB at 3,000 centavos, `open`.
12. Write the `UsageEvent` (key `intake:<source_reference>`).

When a cycle holding a `held` row resolves (hooks in `services.recovered`, in `_lapse_pending` and in the sweep's `suspended_after_attention`), the same transaction calls `usage.release_held(cycle, paid)`: **paid** makes the line, **lapsed** writes it off with `cycle_lapsed`. L36 says "approved route"; this is it.

D4 and Q3 (merchant business media only) is the producer's input, not billing's; billing cannot tell a receipt from a banner. The function's contract says so.

## 7. Where the money goes: collections

B2a's `submit_transfer` freezes `amount_centavos` = the plan price (B3 now a versioned catalogue price). B4 makes the frozen amount **price + the open overage that is due**, and keeps B2a's rule that only an operator's confirmation settles anything.

- **What is due.** The `open` lines of periods that have ended at `now` and that no confirmed payment holds. A line of a period still running is not due, so a payment sent before the period ends does not include it; the next collection takes it (U5; ruled by the coordinator).
- **Freezing.** `BankTransferPayment` gains `usage_centavos` (the overage inside `amount_centavos`) and `usage_line_max_id` (the highest `ChargeLine.id` included; the period test above is repeated at confirmation). A replaced or rejected proof frees nothing, because nothing was attached; the next proof recomputes.
- **Confirmation.** `confirm_transfer` recomputes the same set (`id <= usage_line_max_id`, period ended at the time frozen). If its total differs from `usage_centavos` the confirmation answers 409 `usage_changed` and settles nothing; the operator rejects and the owner sends the new amount. Otherwise it settles the cycle (B2a) and marks each line `settled` with this payment, in one transaction, with the audit entry B2a already writes (its `detail` gains `usage_centavos`).
- **`payment_options` and the owner's payment response** gain `usage: {centavos, lines}`, so the page shows base and overage apart. Each offer's `amount_centavos` includes it.
- **A late line.** A line made after a payment froze (an operator admitted a late snapshot, section 8) has a higher id, so it is not in that payment. It stays `open` and the next collection takes it, labelled `late_admitted` (L7, L12, L16: never an alteration of a settled amount).
- **Final usage-only collection** (ruled in B4). When service ends with a paid period (cancelled paid cycle, or a final cycle) and open lines remain, the subscription can pay them with no further base fee: `plan_code = usage_only`, no cycle, `amount_centavos` = the lines. A failed or missing payment starts no new cycle (Task). Constraint `billing_transfer_confirmed_in_full` is replaced by one that allows a confirmed `usage_only` payment with no cycle. Each month of a longer term (promo, annual) uses the same route for the month after.
- **Suspended, then cancelled, then paid (R-4d).** The lines of the last paid cycle stay `open` and the usage-only route collects them. **Suspended and never back (R-4c):** the account owes nothing; the sweep writes the lines off with `closed_reason = suspension_unreturned` at `deletion_due_at` (B1 D14), consistent with L36 and L21 (ruled by the coordinator).
- **Closed and settled (L16, L21).** A line that would be made for a subscription that is cancelled with no `open` line and a settled final usage collection is written off at once as `after_final_settlement`. No debit statement, no amendment, a reasoned record.
- `GET sites/{site_id}/billing/`'s `outstanding_centavos` (hard-coded 0 today, `services.py`) becomes the total of due `open` lines. The route's shape does not change.

## 8. Operator review

Reasons `late_over_72h` need an operator to admit or dismiss; `after_service_end` and `no_cycle` need one only to record that nothing is owed (L6 forbids charging them). POST routes added to `billing_staff.ROUTES`, under B2a's rules (origin, session, 404 while `BILLING_USAGE_ENABLED` is off, no id in an address):

| Route | Does |
|---|---|
| `finance/usage/review/list/` | The `needs_review` rows, oldest first, paged: site id, reason, the dates, the bytes. No personal data |
| `finance/usage/review/detail/` | One row with its events |
| `finance/usage/review/admit/` | `late_over_72h` only. Needs a reason. Makes the line, labelled `late_admitted` (section 7), or writes it off with `after_final_settlement` when the closed-and-settled rule applies. A cycle that is `pending` makes it `held`; a `lapsed` one `written_off/cycle_lapsed` |
| `finance/usage/review/dismiss/` | Any `needs_review` row. Needs a reason. Becomes `written_off/reviewed_not_billable`: kept, never charged, never reactivates service |

Each action takes the row lock, is idempotent under a key, writes a `UsageEvent` and one `OperatorAuditEntry` (`billing_usage_admitted`, `billing_usage_dismissed`, declared kept whole in `apps/operators/audit.py`, the operator's reason in the entry's `reason`). There is no Django admin screen. The page, and its place in the HQ menu, belong to the frontend task.

## 9. The owner's read: `GET sites/{site_id}/billing/usage/`

Signed in, through `site_for` (another account's or a removed site answers 404 before any billing row is read), no body. 404 while the switch is off, or the site has no subscription.

```
{
  "cycle":   {"sequence": 2, "start_at": "...", "end_at": "..."},
  "period":  {"index": 1, "start_at": "...", "end_at": "..."},
  "storage": {"metered": false, "included_gb": 5,
              "last_snapshot": null, "held": 0, "pending_review": 0},
  "due_centavos": 0
}
```

`last_snapshot` is the frozen result of the last closed period (`period_end_at`, `measured_bytes`, `billed_gb`, `amount_centavos`) or null. Held and under-review rows are counts, never charges. `due_centavos` is the open overage of closed periods, the figure the next collection includes. Trial: every figure zero, `billable: false`. No row number, no source reference, no producer detail. Throttled per account as B1's reads are.

## 10. Migrations (approved with this plan; run on staging by the coordinator, never in production)

| Migration | Adds | Safe before it runs? |
|---|---|---|
| `0006_usage_ledger` (depends on B3's `0005_paid_cycle_has_snapshot`) | `UsageEntry`, `ChargeLine`, `UsageEvent`, their constraints. The schema is smaller than first planned: no `ordinal`, no order kind, no order constraints | Yes. It only adds tables, and no code reads them while `BILLING_USAGE_ENABLED` is off |
| `0007_transfer_usage` (depends on 0006) | `BankTransferPayment.usage_centavos` (default 0), `usage_line_max_id` (default 0), and the replaced `billing_transfer_confirmed_in_full` constraint (section 7) | Yes for the columns. The constraint swap is validated against existing rows, which satisfy both forms (every confirmed row has a cycle) |

**B4 adds no column to `BillingCycle`.** Its tables point to a cycle by foreign key and read B3's `term_months` and `plan_version`. B5 adds no billing migration. The numbers stay placeholders; if the order changes, the later file is renamed with its `dependencies`, never an applied one edited.

## 11. Files touched and order against B5

New: `apps/billing/usage.py`; `apps/billing/migrations/0006_usage_ledger.py`, `0007_transfer_usage.py`; `apps/billing/tests/test_usage_storage.py`, `test_usage_routes.py`, `test_usage_collection.py`, `test_usage_review.py`, `test_usage_api.py`, `test_usage_concurrency.py`; a `docs/runbooks/billing.md` section.

Changed:

| File | Change | Shared with |
|---|---|---|
| `apps/billing/models.py` | Three models; two columns on `BankTransferPayment` | B5 |
| `apps/billing/services.py` | `usage.release_held` hooks in `recovered`, `_lapse_pending` and the sweep; `submit_transfer`, `payment_options`, `confirm_transfer` amounts; `overview.outstanding_centavos` | B5 |
| `apps/billing/plan.py` | The storage constants, `STORAGE_METERED`, a `usage_only` pseudo-plan | |
| `apps/billing/clock.py`, B3's catalogue | Not changed: B4 calls `clock.allowance_periods` and `services.allowance_period_at` | B3 (merged) |
| `apps/billing/api.py`, `staff.py` | The usage route; four staff routes | B5 |
| `apps/operators/audit.py` | Two declared actions | Other sessions |
| `config/settings.py` | `BILLING_USAGE_ENABLED` | Other sessions (added lines only; the second to merge keeps both) |
| Scope document | The amendment of section 13 | |

B4 before B5: B5 reads `ChargeLine` and the `usage_only` payment to print overage lines and the "labelled adjustment". B3 has merged; B4 reuses it and does not edit B3 code. Wording elsewhere that names an order allowance (for example in B3's offers or plan text) is not edited here and goes on a list for the coordinator.

**Pull requests** (each from the latest `origin/main`, small, dark behind `BILLING_USAGE_ENABLED`; before each, the open PRs and their files are listed and the coordinator told of any overlap):

1. **Ledger and storage intake.** Tables, migration `0006`, the storage constants, `record_storage_snapshot`, `release_held`, the flag.
2. **Collections.** Migration `0007`, the amounts in B2a's functions, the `usage_only` payment, the write-offs, `outstanding_centavos`.
3. **Review routes and the owner's read.** Staff routes, the two audit actions, `GET billing/usage/`, runbook.

## 12. Tests

All time is passed in; no test reads the clock. Every rule is driven through `record_storage_snapshot`, never by creating a ledger row by hand.

**Storage and routes**
- At and below 5 GB: no row. 5 GB + 1 byte: one started GB, 3,000. 6 GB: 3,000. 6 GB + 1 byte: 6,000.
- A replay (same reference, same or other bytes) returns the first result and leaves rows, lines and events unchanged. A second reference for the same period returns the first. No function recomputes a period.
- Not at the period end, or not a period end of the subscription: refused, nothing written. `STORAGE_METERED = False`: `not_metered`, nothing written. An unmeasured period: no row at all (L49). Switch off: `disabled`.
- Test site: `not_billable/test_site` with its event. Trial cycle: `not_billable/trial_cycle`.
- Periods: a six-month promo cycle has a snapshot per month, each with its own 5 GB; period 3's snapshot is charged on its own bytes. An annual cycle likewise.
- Late: received 72 hours after the effective end is charged; one second later is `needs_review/late_over_72h` (the 3:00 PM Manila boundary, including a month-end anchor).
- After service end, in a suspension gap, on a suspended site, after a final cycle: `needs_review`, no charge, no reactivation.
- `pending` cycle: `held`. Then `recovered`: the line appears once. Then `lapsed`: `written_off/cycle_lapsed`, with the cause in the event.

**Collections** (with B2a's and B3's helpers)
- A renewal's frozen amount is price + the overage of ended periods; a running period's line is excluded and shows up on the next collection.
- Confirm settles the cycle and exactly those lines in one transaction; a line made after the freeze is untouched; a change between freeze and confirmation answers `usage_changed` and settles nothing.
- A replaced or rejected proof attaches nothing; the next one recomputes.
- Usage-only: no base, no cycle; a failed or missing payment starts no new cycle; R-4d is collected; R-4c writes off at `deletion_due_at` with the cause; a line for a closed, settled account is written off at once (L16, L21). A settled amount is never altered.
- B2a and B3 payment tests pass unchanged with the switch off.

**Review and API**
- Each staff route: signed out 401, switch off 404, wrong origin 403. Admit makes one labelled line; a second admit is a replay; admit is refused for `after_service_end`; dismiss writes off; both write a `UsageEvent` and an `OperatorAuditEntry`; no personal value in either.
- `GET billing/usage/`: signed out 401; another account's site, a removed site and a made-up id answer one identical 404; the shape above; counts of held and under-review rows; nothing else leaks. It joins the route-inventory and isolation tests; none is loosened.

**Concurrency (PostgreSQL only, skipped on SQLite as B1's are)**
- The same reference from two threads: one row, one line.
- A snapshot racing `recovered` of its pending cycle: held and released once, never twice.
- Confirmation racing a new line: the settled set matches the frozen set.

**Repository checks before each pull request:** `uv run python manage.py makemigrations --check --dry-run`, `check`, `test`, `check --deploy` with `DJANGO_DEBUG=false`; PostgreSQL locally if available, otherwise stated as verified only by CI.

## 13. Amendments to the scope document (made in the amendment pull request)

- **Section 2, MVP line:** "usage overage charges" reads "storage usage overage charges".
- **Section 6, billing.** `UsageEntry`: subscription, cycle, period index, kind (`storage`; no `orders`, no `video`), source reference (unique), quantity (bytes), occurred_at, received_at, status, reason. `ChargeLine`: subscription, cycle, period index, one-to-one entry, kind (`overage_storage`; B5 and B6 add `base` and `referral_credit`), quantity, amount, status (`open`, `settled`, `written_off`), the settling payment, the close cause. `UsageEvent`: append-only decisions. `BankTransferPayment` gains `usage_centavos`, `usage_line_max_id`.
- **Section 7, included per cycle:** "5 GB storage per monthly period; overage PHP 30 per started GB. NegosyoOnline does not meter or charge orders: a Subscriber's sales are their own business (Coach Ed, 2026-11-11). Video is not in the launch product: no allowance, meter or charge (contract D6, D9)." Then: a period's storage overage is billed on the next collection after it closes; a late-admitted charge goes on the next collection with a label and never alters a settled amount; usage in an unpaid cycle is never billed; storage is not metered until the media upload task decides the platform renditions.
- **Section 9, item 5:** "Usage and overage. Storage usage entries and overage charge lines."
- **Section 12:** a dated entry for this amendment and its source.

## 14. Approvals

Given: this plan, its two migrations (section 10), the flag `BILLING_USAGE_ENABLED` and the two audit actions, with the order half dropped by Coach Ed's ruling of 2026-11-11, applied by the coordinator on 2026-11-12. Running either migration is the coordinator's step on staging; setting the flag is a separate yes; production is never covered. Nothing here deletes a row: billing records are kept 5 years.

## 15. Rulings and routine details

Coordinator rulings of 2026-11-11 and 2026-11-12: a payment sent before its period ends leaves that period's overage to the next collection; the usage-only final payment route is in B4; the R-4c write-off at the deletion date, consistent with L36 and L21; the page's place in the HQ menu is the frontend task's choice; the Launch Promo gets its allowance per month for each of the six months (B3's rule, reused); orders are dropped.

Routine details decided (reversible): binary gigabyte (U4); `dismiss` writes a row off rather than adding a status; `UsageEvent` as a third table; `kind` kept on `UsageEntry` with one value.
