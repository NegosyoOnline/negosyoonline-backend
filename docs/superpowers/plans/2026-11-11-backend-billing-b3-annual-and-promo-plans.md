# Billing Build B3: the Annual Plan, versioned plans, and what is left of the Launch Promo (plan)

Status: **plan only, for Coach Ed's approval.** No code, migration, variable or staging action is in this pull request.

ClickUp task: z8uq9mdwry (https://app.clickup.com/t/z8uq9mdwry). Parent: z8uq9m8h46. Read against `origin/main` at `52d8b6d`.

## 1. Sources

- The Task z8uq9mdwry: objective, "Settled rules", "Adapted to the new backend", "Not in this task", the four owed items, "Accepted when". It has no comments.
- The Parent z8uq9m8h46: "FINAL OWNER PLAN-RULE" and "FINAL SIX-MONTH LAUNCH PROMOTION" (2026-11-08); its comments are Billing Phase 1 shipping notes and do not bear on this plan.
- The merged code: `apps/billing` (`plan.py`, `clock.py`, `methods.py`, `models.py`, `services.py`, `api.py`, `staff.py`), `apps/billing/tests`, `config/settings.py`, `docs/runbooks/billing.md`.
- The plans: B1 `2026-11-11-backend-billing-b1-subscription-lifecycle.md` (D13, section 13, the "For B3" line), B2a `2026-11-11-backend-billing-b2a-bank-transfer-launch-promo.md` (sections 3, 6, 12, 13, 14), and the scope document `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md` (section 9 item 4, section 10, the B2a entry in section 12).
- The legacy repository was not needed and was not read.

Where a rule below has no source it is marked **choice** (a reversible detail I decided) or **question** (section 12).

## 2. What B2a already built for the Launch Promo

B2a is merged (`9a90649`, `263a012`, `e65fb96`, `1130802`: pull requests 1 to 4; its pull request 5, the emails, is not built). Checked in the code, not in the plan:

| Piece | Where | State |
|---|---|---|
| The promo as a plan: `LAUNCH_PROMO = Plan("launch_promo", …, 499_900, 6)` | `apps/billing/plan.py` | Built |
| A six-month cycle: `clock.cycle_end(start_at, anchor, months)` is the calendar date six months on, never 180 days. Tests: 8 Nov to 8 May, 31 Aug to 28 Feb, 31 Aug 2027 to 29 Feb 2028, 30 Sep to 30 Mar | `apps/billing/clock.py`, `tests/test_promo.py` | Built |
| Offering: `plan.offers`, `promo_open` (switch `BILLING_LAUNCH_PROMO_ENABLED` on, and no paid cycle ever), one purchase per site, re-checked at confirmation (`plan_not_offered`) | `plan.py`, `services._settle_transfer` | Built |
| One payment, one cycle: a confirmed PHP 4,999.00 proof opens one six-month cycle with `months=6`; the transition's `detail` holds `plan` and `amount_centavos`; paid early in the trial the cycle still starts at trial end | `services.confirm_transfer`, `_settle_transfer`, `first_payment_succeeded` and the other payment functions (all take `months`) | Built |
| "Then PHP 2,499 monthly": after the promo the only plan that can be bought is Monthly. `payment_options` shows `then: {plan: monthly, amount_centavos: 249900, interval: month, first_due_at: <promo end>}` | `services.payment_options`, `GET sites/{id}/billing/payment/` | Built, as a **display** only |
| The first Monthly cycle starts at the exact promo end: a Monthly payment confirmed up to 7 days late, or any time before, opens the next cycle from the promo's `end_at` and ends it on the same anchor day a month on | `services.renewed(early=True)` | Built, for a payment the Subscriber sends |
| Cancellation before the renewal keeps the paid-through service | `services.cancel` (service to the latest cycle's `end_at`) | Built in B1 |
| Existing subscribers unchanged | The promo is bought by sending a proof; nothing converts anyone | Holds |
| Promo and Monthly offered by bank transfer, the HQ Finance page that confirms them | `apps/billing/staff.py`, `/staff/v1/` | Built |

What B2a deliberately did **not** build and the Task asks for:

| Task rule | B2a | Where it goes in this plan |
|---|---|---|
| "Every subscription stores a versioned price and term snapshot. A historic cycle is never re-priced from the current plan" | Only `BankTransferPayment` (`plan_code`, `amount_centavos`) and the transition `detail` hold the plan. `BillingCycle` holds no plan, price or term. The cycle's price today is whatever `plan.PLANS` says | Section 4 |
| "Plans are data with versions"; `GET plan/` | Plans are two constants with no version. No `plan/` route | Sections 4 and 8 |
| The Annual Plan | Not built, on purpose (B2a 6.1) | Section 5 |
| Consent captured at purchase for "PHP2,499 monthly after six months", kept as a record | Not built. No consent record exists | Section 6 |
| "The first monthly charge is attempted only if a reusable payment method is still active and consent is valid. Without one, offer method setup or manual payment with a reminder" | There is no charge, so no attempt. Manual payment exists; the reminder is B2a pull request 5 (unbuilt, waiting on wording) | Section 6 |
| An upcoming-renewal notice before the first monthly charge | B2a pull request 5, unbuilt | Question 5 |
| Promo start and end dates, eligibility | A switch, no date in code, eligibility "never had a paid cycle" (B2a question 6, not answered with a date) | Question 8 |

## 3. What "After B2" means now that B2a exists

The Task says "Order: After B2". When it was written, B2 was the whole of payment collection through Xendit. Since then:

- Xendit was disapproved (appeal) and PayMongo is pending. The scope document (section 9 item 4) now lists **B2a** (bank transfer with proof, built) *before* **B2** (the gateway, "built when a gateway is approved").
- B2a's plan (section 13) already states "B3 becomes the Annual Plan only".

So this plan reads "After B2" as **"after B2a, and not waiting for B2"**. B3 is built on top of B2a's bank-transfer payment: an Annual payment is a proof, confirmed by an operator, that settles one 12-month cycle, exactly as the promo settles one six-month cycle. Everything in the Task that needs a gateway is built as a **seam** (a pure function B2 will call), not as a charge:

- the automatic PHP 2,499 attempt at month six;
- "the payment method may be replaced but not removed while the term is active or a usage balance is unpaid" (there is no payment-method table until B2; section 5.5);
- retry and recovery of a failed automatic renewal (B2's rules; B1's `renewal_failed` and `recovered` already accept `months`).

If Coach Ed wants B3 held until a gateway exists, the Annual Plan could not be sold at all, so this is question 1.

## 4. Versioned plans and the price snapshot

### 4.1 Decisions

| # | Decision | Kind |
|---|---|---|
| V1 | The plan catalogue stays **in code**, one frozen record per plan version, in `apps/billing/plan.py`. It is data (a table of plan, version, price, term, allowance periods, what follows), not a global price, but it is changed by a reviewed pull request, not by a screen | choice, question 2 |
| V2 | Every **paid** `BillingCycle` carries its own snapshot: `plan_code`, `plan_version`, `price_centavos`, `term_months`. A cycle's price is read from the cycle, never from `plan.py`. `BankTransferPayment` gains `plan_version` and `term_months` beside its existing `plan_code` and `amount_centavos` | Task: "versioned price and term snapshot" |
| V3 | A pending or lapsed cycle (a renewal that was due and not paid) snapshots the terms **due**: those of `renewal_terms` (section 6.2). When a payment settles a pending cycle (`recovered`), its snapshot is overwritten with the plan actually paid, as its `end_at` already is | choice |
| V4 | The trial cycle (sequence 0) carries no snapshot | Task: the trial has no price |
| V5 | A new price is a **new version** (`monthly` v2). Nothing edits v1. A cycle opened under v1 keeps v1 forever | Task: "A historic cycle is never re-priced" |

### 4.2 The catalogue

```python
@dataclass(frozen=True)
class Plan:
    code: str
    version: int
    name: str
    price_centavos: int
    months: int              # the length of the term one payment buys
    allowance_periods: int   # monthly allowance periods inside the term (section 7)
    renews_to: str           # the plan code that follows the term
    needs_consent: bool      # the Launch Promo only

MONTHLY      = Plan("monthly",      1, "NegosyoOnline Monthly",              249_900,   1,  1, "monthly", False)
ANNUAL       = Plan("annual",       1, "NegosyoOnline Annual",             2_499_000,  12, 12, "annual",  False)
LAUNCH_PROMO = Plan("launch_promo", 1, "NegosyoOnline Launch Promo (6 Months)", 499_900, 6, 6, "monthly", True)
```

- PHP 24,990.00 is `2_499_000` centavos. It equals 10 × PHP 2,499.00; the saving against twelve months is `12 × 249_900 − 2_499_000 = 499_800` centavos = PHP 4,998.00. A test asserts both, so a typo in the price cannot pass.
- `renews_to`: Monthly to Monthly, Annual to Annual ("Cancellation stops the next renewal"), Promo to Monthly ("PHP 4,999 never repeated"). **Choice** for Annual: the Task says annual terms renew and cancellation stops the next renewal; it does not say the renewal is Annual again, which is the plain reading.
- `allowance_periods` for the promo is 6 **by assumption** (question 3).
- `PLAN_NAME`, `PRICE_CENTAVOS` and `CURRENCY` stay as aliases of Monthly v1, so `GET sites/{id}/billing/` is unchanged byte for byte.
- `PLANS` is keyed by code and returns the **current** version. `plan.version_of(code, version)` returns a historic one; a stored row with an unknown version raises, never falls back.

## 5. The Annual Plan

Every rule below is the Task's "Annual" block; the right-hand column says what already does it and what B3 adds.

| Rule | Already done by | B3 adds |
|---|---|---|
| One 12-month term, paid in advance, one cycle | `cycle_end(months=12)`; the payment functions take `months` | `ANNUAL` in the catalogue; `offers` returns it |
| End-of-month and leap years | `cycle_end`: the end is the same day twelve months on, or the month's last day, and the shortening does not carry forward | Tests (section 11): start 29 Feb 2028, anchor 29, ends 28 Feb 2029; anchor 31 start 31 Jan 2027 ends 31 Jan 2028 and 29 Feb 2028 is not an end; a payment confirmed in a leap year |
| Cancellation stops the next renewal; no prorated refund; service continues through the term | `services.cancel` ends service at the latest cycle's `end_at`; nothing refunds anything; a paid-ahead renewal also survives (the latest cycle is the paid-ahead one) | Tests only: cancel in month 5 of an annual term keeps service to month 12; cancel after paying the renewal ahead keeps both; no code is written to refund |
| Monthly and annual change **only at renewal** | B2a pays always start at the running cycle's end (or the trial end, or the confirmation time for an ended site) | A rule and its test: a payment never starts a term before the running term ends. Plus the pay-ahead window of 5.3 |
| One annual base statement, never amended; no monthly base statement | Statements are B5 | Nothing. B3 gives B5 a cycle whose `term_months`, `price_centavos` and `plan_code` say it is annual |
| Twelve monthly allowance periods of 1,000 orders and 5 GB, no carry-over; no video | Usage is B4 | Section 7: the twelve periods as a pure function. B3 stores no usage and no allowance numbers |
| Overage per period frozen at its cutoff, billed on a separate usage-only statement the next month | B4 and B5 | Nothing. Section 7 says what B4 needs from B3 |

### 5.1 Switch (A-10)

`BILLING_ANNUAL_ENABLED`, off by default, works only while `collection_live()` is true. With it off `offers` never returns Annual, `GET plan/` never lists it and a submitted `plan=annual` is refused with the existing 400 `plan` "This plan is not offered." A confirmed Annual proof cannot exist while it is off, but if the switch is turned off after a proof was sent, that proof can still be confirmed (the promo behaves the same way in B2a: "a promo proof already sent can still be confirmed"). This is the server side of A-10; hiding it on the public site is a frontend task.

### 5.2 Who can buy Annual

Any site that can buy Monthly: on trial, in a paid term (at its renewal, 5.3), in recovery, suspended or ended before the deletion date. **Choice**, and question 7 asks whether the Annual should be closed to a site still inside its promo term other than at its end (it already is, by 5.3).

### 5.3 Paying a long term ahead (new refusal)

B2a lets one renewal be paid at any time before the running cycle ends (`already_paid_ahead` stops a second). For a one-month cycle that is at most a month; for a 12-month term it would let a Subscriber pay the next year in the first week. There is no refund of a paid term, and a paid-ahead year makes cancellation and the deletion rules harder to explain.

- **Choice, question 6:** a renewal proof is accepted from **30 days before** the running term ends. Earlier, `POST …/payments/` answers 409 `too_early` with the date from which it is accepted. The window is `RENEWAL_WINDOW = timedelta(days=30)` in `plan.py`.
- It does not apply to the first payment (trial), to recovery, or to a suspended or ended site. For a one-month cycle the window is at least as long as the cycle, so Monthly behaves as today.
- `payment_options` reports it (`not_payable_reason: "too_early"` with `payable_from`).

### 5.4 The account-deletion case in B1's D13

B1 refuses an account disposal while a paid term runs (`paid_term_running`, with the term's end), and its plan says "the coordinator brings the Annual Plan case back to Coach Ed at B3". With Annual that refusal can last up to a year. The rule is the Parent's P10.1: a deletion request from an active Subscriber is a cancellation scheduled for the end of the paid term, no refund, no immediate deletion. **This plan changes nothing**: the rule is the same for a one-month, six-month or twelve-month term. Question 9 puts it to Coach Ed so the answer is explicit. Until answered: unchanged.

### 5.5 The payment method cannot be removed during a term

There is no payment-method table or route in the backend (B2 builds them). Nothing in B3 can remove one. The rule is recorded as a **requirement on B2**: removal is refused while the latest cycle's end is in the future (any plan) or a usage balance is unpaid (B4); replacing is allowed. B3 offers the first half as `services.term_running(subscription, at)` (true while a paid cycle covers `at`), so B2 asks one function. No other code.

## 6. The Launch Promo: the consent record and the conversion gate

### 6.1 The consent record (new table)

`RenewalConsent`, one row per promo purchase, written in the same transaction as the `submitted` payment:

| Column | Notes |
|---|---|
| `subscription` | FK, `PROTECT` |
| `payment` | One-to-one to `BankTransferPayment`, `PROTECT`. One consent per proof; a replaced proof's consent stays with it |
| `plan_code`, `plan_version` | The term bought (`launch_promo`, 1) |
| `then_plan_code`, `then_price_centavos` | What follows (`monthly`, 249,900), frozen from the catalogue |
| `shown_first_due_at` | The first monthly date the buyer was shown; empty when the term starts at the confirmation (an ended or suspended site), with `starts_at_confirmation = true` |
| `text_version` | The id of the consent wording the buyer saw (`promo-consent-1`). The wording itself is not in the backend (question 4) |
| `accepted_by` | FK to `accounts.Account`, `PROTECT` |
| `accepted_at` | Server time |

Rows are never deleted or edited (`delete()` raises; no update path), for the five-year record. **No IP address or browser string is stored**: not asked for, and it would be a personal value that the retention rules would then have to handle. Question 4c asks whether he wants either.

- The submit route gets one field, `renewal_consent` (`"true"`), **required when the chosen plan has `needs_consent`**, refused with 400 `fields.renewal_consent` otherwise, together with `consent_version`, which must equal the version the offer showed (`offers[].consent.version`). A stale version is refused: the buyer saw different words.
- A plan with `needs_consent` false ignores both fields (an Annual or Monthly buyer is not asked).
- The consent record says "PHP 2,499 monthly after six months". At bank-transfer launch it does **not** authorise any debit, because there is none. It is the record of what the buyer was told about the price after six months, and it is the authorisation B2 will need if a gateway is added. This is stated in the runbook so no one reads it as a mandate.

### 6.2 `renewal_terms`: the single answer to "what is due next, and when"

`services.renewal_terms(subscription)` returns, for a subscription with a paid cycle: the plan that follows (`latest.plan.renews_to`, at the **current** version of that plan), its amount, the term in months, `due_at` (= the latest cycle's `end_at`) and the anchor day. It reads the latest paid cycle's snapshot, never the current price table for the plan it came from.

- For the promo: Monthly, PHP 2,499.00, due at the exact promo end. For Annual: Annual, PHP 24,990.00 (at the version in force when the renewal is paid; the **renewal price is the price of the renewal's own version**, not the old term's. Question 10.) For Monthly: Monthly.
- `payment_options`' `then` block is built from it, so the display and the rule cannot differ. For the promo it is unchanged; for Annual it is `{plan: annual, interval: year, …}`.
- Pending and lapsed cycles (V3) take their snapshot from it.
- It is also what B2a's reminder emails (pull request 5) and B2's charger read. B3 sends no email and charges nothing.

### 6.3 `conversion_decision`: fails safe, charges nothing

`services.conversion_decision(subscription, at)` answers what a charge at the first renewal of a promo term would be allowed to do:

| Situation | Answer |
|---|---|
| The subscription is exempt, cancelled or suspended | `none` (no charge, no cycle) |
| The latest cycle is not a promo term, or `at` is before its `end_at` | `none` |
| No consent record on the promo's payment, or its `then_*` terms differ from `renewal_terms` | `manual`, reason `no_valid_consent` |
| No reusable payment method (`methods.reusable(site)`; always false until B2) | `manual`, reason `no_reusable_method` |
| Consent and a reusable method both hold | `charge`, with `renewal_terms` and the idempotency key `renewal:<subscription>:<next sequence>` |

- **Fails safely with no charge** (Task): every branch but the last returns a decision with no side effect, and the last also returns only a decision. B3 contains no code that debits.
- At launch the answer is always `manual`, which is what B2a already does: the Subscriber sends a Monthly proof, with the reminder of pull request 5.
- A repeated call returns the same answer and the same key. With the unique (`subscription`, `sequence`) on the cycle and the payment functions' `paid:<reference>` key, a double call or a duplicate webhook cannot open two cycles. Tests prove "no overlapping cycles": each cycle's `start_at` equals the one before's `end_at`, over a promo, six Monthly terms and a leap-year February.

### 6.4 What is *not* changed in the promo

The price, the term, the switch, one purchase per site, the HQ Finance page and the display are B2a's and are untouched. The promo's start and end dates and eligibility stay in question 8; no date is put in code.

## 7. Allowance periods: what B3 gives B4

B4 (the usage ledger, `z8uq9mdwrz`) attributes orders and storage to a monthly allowance period. B3 makes the periods exist without storing anything:

```python
clock.allowance_periods(term_start, anchor_day, months, periods)  # -> [(start, end), …]
```

- Period *k* ends at `cycle_end(term_start, anchor, k)` (not chained from period *k−1*), so the shortening of a short month never carries forward: anchor 31 gives 28 February, then 31 March. Period 1 starts at `term_start`. All boundaries are 3:00 PM Manila.
- Monthly: one period, the cycle. Annual: twelve. Promo: six (question 3).
- `services.allowance_period_at(subscription, instant)` returns the cycle, the period index and its bounds, or none when no paid cycle covers the instant (the trial's period is B4's rule, not B3's).
- The twelve periods tile the 12-month term exactly (the last ends at the cycle's `end_at`), tested on every anchor day 1 to 31 across a leap year.
- B3 stores no usage, no allowance quantities (1,000 orders, 5 GB) and no overage price. Those belong to B4's ledger and plan. "No carry-over" is a property of B4's rows (one balance per period) and B4 tests it.
- **If B4 wants period rows instead of the function** (to attach statements to them), B4 adds that table in its own migration from the function's output. B3 does not guess.

## 8. The API

All under `/v1/`.

### 8.1 `GET plan/` (new)

The plans on offer, for a pricing or checkout page. Public, no sign-in, read-only, no personal data.

```json
{"plans": [
  {"code": "monthly", "version": 1, "name": "NegosyoOnline Monthly", "amount_centavos": 249900,
   "currency": "PHP", "months": 1, "interval": "month"},
  {"code": "annual", "version": 1, "name": "NegosyoOnline Annual", "amount_centavos": 2499000,
   "currency": "PHP", "months": 12, "interval": "year", "saving_centavos": 499800}
]}
```

- Monthly always. Annual only while `BILLING_ANNUAL_ENABLED` and collection are on. The **Launch Promo is not listed** unless Coach Ed wants it advertised there (question 4b): it is "information only" and in no legal page, and eligibility is per site, so the signed-in `payment/` route is where it is offered. Until answered: not listed.
- Reads no table. It is the catalogue plus two switches.

### 8.2 `GET sites/{id}/billing/payment/` (extended)

`offers[]` gains the Annual entry when offered, and every entry gains `version`, `term_months` (alias of `months`) and, for a plan with consent, `consent: {"required": true, "version": "promo-consent-1", "then": {…}, "first_due_at": …}`. `then` for Annual is `{plan: annual, amount_centavos, interval: year, first_due_at}`. New `not_payable_reason`: `too_early` with `payable_from`. Existing fields keep their meaning.

### 8.3 `POST sites/{id}/billing/payments/` (extended)

`plan` may be `annual`. New optional fields `renewal_consent`, `consent_version` (6.1). New 409 `too_early`. No other change; a rejected-for-consent request writes nothing and stores no file (validation precedes the store call, as it does for the other fields).

### 8.4 Unchanged

`GET sites/{id}/billing/` and its shape; the cancel and reactivate routes; the proof route; the Finance routes (they show `plan` as the code, so `annual` appears without a change; the staff page's table is checked in a test).

## 9. Settings

| Setting | Default | Meaning |
|---|---|---|
| `BILLING_ANNUAL_ENABLED` | off | "1" offers the Annual Plan, and only while collection is live. Off: `plan=annual` is refused and `GET plan/` omits it (A-10) |

One line in `config/settings.py` and one row in `docs/runbooks/billing.md`, which also gets the Annual and the consent rows and the "consent is not a debit mandate" sentence. Setting it in any shared environment needs Coach Ed's separate approval.

## 10. Migrations

All in `apps/billing`. Written only after approval, run on staging by the coordinator after merge, never on production without his separate yes.

| Migration | Adds | Safe before it runs? |
|---|---|---|
| `0003_plan_snapshot` | `BillingCycle.plan_code` (blank), `plan_version`, `price_centavos`, `term_months` (all nullable); `BankTransferPayment.plan_version`, `term_months` (nullable); the `RenewalConsent` table | The new columns are nullable and the new code writes them. **Deploy order matters** (below) |
| `0004_plan_snapshot_backfill` | Data only (`RunPython`, a no-op in reverse). Every `kind = paid` cycle: from its payment's plan if it has one (`plan_version = 1`, `term_months` and `price_centavos` from the payment), else Monthly v1 (`249_900`, 1). Every payment: `plan_version = 1`, `term_months` from its plan | Yes. Idempotent: it fills only rows still empty |
| `0005_paid_cycle_has_snapshot` | A check constraint on `BillingCycle`: `kind = trial` or all four snapshot fields are set | Yes, once 0004 has run. Separate from 0004 because PostgreSQL refuses to alter a table with rows changed earlier in the same transaction |

- **Deploy order.** Pull request 1 holds the model fields and the migrations only, and no code reads or writes the new columns. So it is safe to deploy before the migrations run (the B1 and B2a pattern: "code is deployed before its migration is run"), but Django's own model load would not select the new columns until a query touches them, and every query on `BillingCycle` does. Therefore on staging the coordinator applies `0003`, `0004`, `0005` in that order right after pull request 1 deploys, and pull requests 2 to 4 are merged only after that. In between, `BILLING_ENABLED` is the only protection, as for B1 and B2a: nothing in a shared environment is switched on by this plan.
- **Staging data.** Staging holds test subscriptions and B2a test payments. The backfill covers them; the test for 0004 builds one cycle of each kind (trial, paid by promo, paid by Monthly, pending, lapsed) and checks the result.
- **Production.** There is none (production is deferred until the MVP). The backfill still has to be correct for the first production database, which will start from `0001` and have no rows.
- No other migration. Annual needs no table of its own: a cycle with `term_months = 12`.

## 11. Pull requests, in build order

Each is cut from the latest `origin/main`, is dark while its switch is off, and lists the open pull requests and their files before it is opened.

| # | Pull request | Files | Tests |
|---|---|---|---|
| 1 | **Snapshot columns and consent table.** Model fields, the three migrations, the model tests. Nothing reads or writes them | `apps/billing/models.py`, `apps/billing/migrations/0003…0005`, `apps/billing/tests/test_models.py`, `test_migrations.py` | Migration 0004 on each cycle kind; 0005 refuses a paid cycle with a missing field; the consent row refuses a delete; both pass on SQLite and PostgreSQL |
| 2 | **The versioned catalogue and the snapshot written.** `Plan` with `version`, `renews_to`, `allowance_periods`, `needs_consent`; `ANNUAL`; `_open_cycle` and `BankTransferPayment` write the snapshot; V3 for pending and lapsed; `renewal_terms`; `term_running`; the aliases | `apps/billing/{plan,services}.py`, `tests/test_plans.py`, `tests/test_transfer.py` | Prices (`2_499_000`, saving `499_800`); a v2 price does not re-price a v1 cycle; every existing test unchanged; `GET billing/` byte for byte; `recovered` overwrites a pending snapshot |
| 3 | **Annual.** `BILLING_ANNUAL_ENABLED`, `offers`, the pay-ahead window, `too_early`, the API extension, `GET plan/` | `apps/billing/{plan,services,api}.py`, `config/settings.py`, `docs/runbooks/billing.md`, scope amendment | `test_annual.py`: one proof opens one 12-month cycle; the dates of section 5; cancel keeps the term; change only at renewal; switch off refuses and omits; `too_early`; the Finance page lists `annual` |
| 4 | **Consent and the conversion gate.** `RenewalConsent` written at submit, the two fields, `conversion_decision`, `methods.reusable`, `allowance_periods` | `apps/billing/{models,services,api,clock,methods}.py`, `tests/test_consent.py`, `test_conversion.py`, `test_periods.py` | Promo without consent refused with nothing stored; stale version refused; one consent per proof; each row of 6.3; the chain promo to Monthly to Monthly with no overlap, on anchors 8, 29, 30 and 31 and through 29 Feb 2028; 12 periods tile the term on every anchor; the recognition figures (below) |

**The recognition figures.** The Task asks that "181-day example, earned as of Dec 31 2026 = PHP 1,491.41, remaining PHP 3,507.59; total earned at completion exactly PHP 4,999.00" be held in tests, and also excludes the recognition journals. I read that as a **pure function and its test**, no journal and no table: `earned_centavos(price, start_date, end_date, as_of_date)` = round(price × days elapsed ÷ days in the term) with days counted in Manila dates, the start date included, and exact at the end. For 8 Nov 2026 to 8 May 2027 that is 54 days of 181 at 31 Dec 2026: `499_900 × 54 / 181 = 149_141` centavos earned and `350_759` remaining; at the end `499_900`. It lives in `apps/billing/earned.py`, is used by nothing, and is the figure B5 or the accounting build reads. Question 11 asks whether to build it here.

Tests run on SQLite and PostgreSQL in CI as for B1 and B2a; the concurrency tests (two confirmations of one Annual proof; a confirmation against a replacement) run on PostgreSQL as `test_concurrency.py` does. After each merge the staging check is `/healthz/` and, once the migrations are applied and a switch is approved, `GET /v1/plan/` and the routes of section 8 for a test site.

## 12. Tables, migrations and files, and the order against B4 and B5

B4 (usage ledger) and B5 (statements and the BIR register) are being planned in parallel. They are not on `origin/main` and no plan of theirs is in the shared notes folder, so what follows is what B3 needs and what I assume about them. **Question 12.**

| What | B3 touches | Likely also touched by |
|---|---|---|
| `apps/billing/models.py` | `BillingCycle` (four fields, one constraint), `BankTransferPayment` (two fields), new `RenewalConsent` | B4 (usage tables, a foreign key to a cycle), B5 (statement tables, cycle foreign keys) |
| `apps/billing/migrations` | `0003`, `0004`, `0005` | B4 and B5, each at least one migration after B3's |
| `apps/billing/services.py` | `_open_cycle`, `_settle_transfer`, `payment_options`, `submit_transfer`, new `renewal_terms`, `conversion_decision`, `term_running`, `allowance_period_at` | B4 (usage functions), B5 (statement functions) |
| `apps/billing/plan.py`, `clock.py` | The catalogue, `allowance_periods` | B4 reads `allowance_periods` |
| `apps/billing/api.py` | The payment routes, `GET plan/` | B4 (`billing/usage/`), B5 (`billing/statements/`) |
| `apps/billing/staff.py`, `apps/operators/audit.py` | Nothing (checked: no new audit action) | B5 (invoice register) |
| `config/settings.py`, `docs/runbooks/billing.md`, the scope document | One setting, one runbook block, one amendment entry | All three |

**Order needed: B3's pull request 1 (the migrations) before B4's and B5's first migration.** Both of them need to say which cycle and which plan an amount belongs to, and that is exactly what B3 adds to `BillingCycle`: B5's annual base statement is "one statement for the cycle whose `term_months` is 12 at `price_centavos`", and B4's usage entries attach to a period of a cycle. Migrations are numbered linearly, so two plans each writing `0003` would collide. Proposal:

1. B3 pull request 1 takes `0003`, `0004`, `0005` and merges first. Its content is columns, a table and a backfill only, so it is small and quick to review.
2. B4 and B5 number their migrations from `0006` and rebase on it, or use the next free number at their own merge time. The coordinator tells each plan writer.
3. B3's later pull requests (2 to 4) touch services and API and do not need a migration, so they may merge before or after B4 and B5; they conflict with them only in `services.py` and `api.py`, and on additions to different functions. If B4 or B5 change `_open_cycle` or `payment_options`, that is a conflict to resolve with both sides kept.

If B4 or B5 would rather write their migrations first, B3's three renumber to follow; nothing in B3 depends on a number.

## 13. Scope-document amendment (proposed, not made here)

The Task says "The backend scope document lists annual billing as out of scope; the builder's plan amends it", and the brief says not to edit that document in a plan. Pull request 3 would carry:

- Section 10 (out of scope): delete the bullet "Annual billing."
- Section 9 item 4: the B3 line becomes "B3 (z8uq9mdwry): the Annual Plan, versioned plans with a price and term snapshot on each cycle, the Launch Promo consent record, and `GET plan/`."
- Section 7: add that an Annual term is PHP 24,990.00 for 12 months, paid in advance by bank transfer with proof like any other term, renews as Annual, and gives twelve monthly allowance periods; and that the Launch Promo's consent is recorded and is not a debit mandate.
- Section 8: add `GET plan/` to the billing row.
- Section 12: one dated entry pointing to this plan.

## 14. Not in this build

Any gateway, card, token, webhook, automatic charge, retry schedule or payment-method table (B2). Usage entries, allowance quantities, overage prices and usage-only statements (B4, B5). Annual and monthly base statements, the BIR register, invoices (B5). Referral credit on an annual payment (B6). Revenue recognition **journals** and the accounting system. Refunds and proration of any kind. The PHP 399 Auto Courier Tracking add-on. The public pages, the pricing toggle and the checkout screens (frontend). Moving existing subscribers to a plan. The reminder emails (B2a pull request 5). Any production action.

## 15. Questions for the coordinator

Each has one recommended answer. "Until answered" is what the build does meanwhile. Items 1, 4a, 8, 13 and 14 are the Task's four owed items and the order question, put as questions as the Task asks.

1. **Order.** Build B3 now on B2a and leave the gateway pieces as seams (section 3), or hold B3 until B2? Recommend: now. Reason: the Annual Plan can only be paid by bank transfer, B2 may be months away, and B2a already settles any term with `months`. Until answered: pull request 1 only (columns and table), which is safe either way.
2. **Plans as data.** Is "plans are data with versions" met by a versioned catalogue in code (V1), or does he want a plan table he edits from the HQ dashboard? Recommend: code. Reason: a price is a contract term and a reviewed change with a version is safer than a screen; the HQ dashboard would need its own plan and audit. A table can be added later without changing the snapshot. Until answered: code.
3. **Promo allowance.** Does a six-month Launch Promo term get 1,000 orders and 5 GB for **each** of its six months (six periods), or one allowance for the whole term? Recommend: each month, 6 periods. Reason: B1's rule is "included per cycle: 1,000 orders and 5 GB", the promo is "a prepaid six-month subscription", and a single 6,000-order pool would let one month eat the rest. Not stated in the Task or the Parent. Until answered: `allowance_periods = 6` and B4 is told it is open.
4. **Where the buyer sees the promo's terms and consent text** (owed). Recommend: (a) on the dashboard Billing page's pay step for a promo, directly above the consent checkbox and the Submit button, not in a legal page, showing the PHP 4,999.00 now, the PHP 2,499.00 monthly after, the first date, and "cancel any time before then to stop it"; the wording is his to approve and lives in the frontend, the backend stores only `text_version`; (b) `GET plan/` does not list the promo; (c) no IP address or browser string is recorded. Reason: it matches "information only, in no legal page" and keeps the record small. Until answered: the consent fields exist and the version is `promo-consent-1`, but the promo cannot be switched on in a shared environment.
5. **The upcoming-renewal notice.** The Task requires a notice before the first monthly charge; B2a pull request 5 owns the emails and waits for wording. Confirm B3 builds none and only exposes `renewal_terms`. Recommend: yes, one email build. Until answered: none.
6. **Pay-ahead window.** Accept a renewal proof from 30 days before the term ends (5.3), or leave B2a's "any time before"? Recommend: 30 days. Reason: for a 12-month term "any time" invites a second year paid in week one with no refund. Until answered: built as 30 days.
7. **Annual for a promo site.** Confirm that the Annual Plan can be bought by a promo site only at the promo's end (the same renewal rule), never to replace the rest of the promo. Recommend: yes (5.3 already does this). Until answered: as recommended.
8. **Promo start and end dates, and who may take it** (owed, his to set). B2a's rule stands: his switch, no date in code, never-paid sites. Until answered: nothing changes and the promo is not offered anywhere shared.
9. **Account deletion inside an annual term** (B1 D13). Keep the Parent's P10.1 rule (cancellation to term end, no refund, no early deletion) for a 12-month term? Recommend: yes. Reason: one rule for every term. Until answered: unchanged.
10. **The renewal price of an Annual term.** When an Annual term ends, is the renewal at the Annual price in force then (the next version) or at the price the old term paid? Recommend: the price in force at the time of the renewal payment, shown before the buyer pays (`renewal_terms` already says so). Reason: "a historic cycle is never re-priced" protects the paid term, not the next one. Until answered: as recommended.
11. **The recognition figures.** Build the pure `earned_centavos` function and the two tests here, or leave all of it to B5 and the accounting build? Recommend: here, since the Task asks for the figures in tests. Until answered: built in pull request 4.
12. **Order against B4 and B5.** Confirm section 12: B3's `0003` to `0005` merge first and B4 and B5 number from `0006`. If their plans already name numbers or tables on `BillingCycle`, say so. Until answered: as written.
13. **A-2: the PHP 399 Auto Courier Tracking add-on on an annual term** (owed, before any annual payment). No add-on exists in code, so nothing in B3 touches it. It needs its own phase or a Terms amendment. Recommend: nothing in B3; an annual payment is the base only. Until answered: Annual is switched on for no one.
14. **BIR's answer on an Internal Billing Statement before PTI** (owed) and **A-10** (hide or gate Annual on the public site). The first is Coach Ed's, outside the code. A-10 is met on the server side by `BILLING_ANNUAL_ENABLED` (5.1); the public-site half is the frontend task. Confirm that is enough for A-10. Until answered: Annual stays off everywhere.

## 16. Decisions listed for the report (routine, reversible)

| What | Why | Source |
|---|---|---|
| Annual renews as Annual | The Task says an annual term renews and cancellation stops the next renewal | Task, "Annual" |
| The snapshot goes on `BillingCycle` and not in a new table | One row per cycle already; the payments and transitions keep their copies | Task, "Shared" |
| No check constraint in the first migration | PostgreSQL refuses it in the same transaction as a data change | Django and PostgreSQL behaviour |
| `GET plan/` is public and does not list the promo | The promo is information only and eligibility is per site | Task, "Launch Promo"; ruling 2026-11-10 |
| No IP or browser string in the consent row | Not asked for; it is a personal value with a retention rule | `CLAUDE.md`, privacy rules |
| The consent row is not a debit mandate | No gateway exists; saying otherwise would be a customer promise | Parent: "never debit without authorisation" |

## 17. Approvals asked

1. This plan, with V1 to V5.
2. Writing migrations `0003_plan_snapshot`, `0004_plan_snapshot_backfill` and `0005_paid_cycle_has_snapshot` (run on staging by the coordinator after merge, under the standing approval; never production).
3. The scope amendment of section 13.
4. The answers to section 15.

Still to ask one at a time, later: `BILLING_ANNUAL_ENABLED` and `BILLING_LAUNCH_PROMO_ENABLED` in any shared environment; the consent wording; any change to public wording; anything in production.
