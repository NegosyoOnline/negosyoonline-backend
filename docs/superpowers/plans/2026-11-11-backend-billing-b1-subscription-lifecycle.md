# Billing Build B1: subscription lifecycle and cycles (plan)

Date: 2026-11-11. ClickUp task: z8uq9mdwrv (https://app.clickup.com/t/z8uq9mdwrv), parent z8uq9m8h46.
Branch: `feat/z8uq9mdwrv-billing-b1-subscription-lifecycle`, cut from `origin/main` at `7de44b3`.
Status: APPROVED by Coach Ed on 2026-11-11 ("Approve as recommended", relayed by the coordinator). Section 2 records the approval, what it covers and each ruling. The build is four pull requests (section 11).

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9mdwrv, description (no comments, checklists, custom fields or attachments as of 2026-11-11; status Open) |
| Parent | ClickUp z8uq9m8h46, description and its 4 comments (all about the legacy build) |
| Brief | The coordinator's brief for this builder, 2026-11-11, with Coach Ed's rulings listed in it |
| NPC rulings | NPC readiness spec, revision 13, legacy repository, read-only: `.claude/docs/superpowers/specs/2026-11-03-negosyoonline-npc-readiness.md`. Read before the approval: the rulings table (lines 36-54), section 2, sections 3.5 and 3.6, and lines 214, 220, 226 of 3.2.5. Read after it, before any code: the rest of 3.2, and 3.7 to the end (findings at the end of section 2) |
| Contract | P6.1 contract r25, legacy repository, read-only: rulings table (lines 57-149), Gate G4 (218-258), G4a transition table (317-327). Read for rules only |
| Scope | `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`, sections 3, 5, 6, 7, 8, 9, 10, 12 |
| Code | `apps/sites/{services,models,api,labels,retention}.py`, `apps/accounts/{models,retention}.py`, `apps/common/retention.py`, `apps/notifications/services.py`, `apps/operators/background.py`, `config/{settings,api,urls}.py`, `.github/workflows/ci.yml`, `railway.toml`, `docs/runbooks/{migrations,retention}.md`, at `7de44b3` |
| Legacy behaviour | Two date rules only, read in the legacy `billing.py` to confirm behaviour, nothing copied: the trial ends exactly 14 days after it starts (not at 3:00 PM); a cycle ends at 3:00 PM Asia/Manila on the anchor day of the next month |

Open pull requests in this repository on 2026-11-11: none.

## 2. Decisions, as ruled

**Approval.** Coach Ed approved this plan on 2026-11-11, in the coordinator chat, with the words "Approve as recommended". The coordinator relayed the approval and the rulings below to this builder the same day. Each ruling is the answer this plan recommended to him.

**The approval covers:** the plan with the rulings below; the scope amendment text (D12, section 9); creating the migration file `apps/billing/migrations/0001_initial.py` (D5); adding the Celery dependency (D3).

**It does not cover, and each is still his own yes, on staging only:** running the migration; setting `BILLING_ENABLED`; running the backfill, dry run included; creating the `worker` service; creating the `beat` service; any other variable. Also not covered: any ClickUp write, and anything in production.

| # | Ruling |
|---|---|
| D1 | Payment is accepted until the deletion date of that site. Day 44 for a trial that ended with no payment method (14 + 30). Service end + 90 days if the site ever paid |
| D2 | The dates count from the trial end, not from the site's creation (R-TERMS-1). The gap this leaves is not built in B1 and is decided with B2 |
| D3 | Celery, with Redis as the broker through `REDIS_URL` |
| D4 | `worker` and `beat` on staging and the variable `BILLING_ENABLED`, off by default. Each is created or set only on his separate yes |
| D5 | One migration, three tables, in `apps/billing` only. Nothing in `sites` or `accounts` |
| D6 | The backfill is a command, run on staging only on his separate yes |
| D7 | `billing_exempt`: set once by a terminal command with operator, reason and audit entry, never cleared |
| D8 | Reactivate only withdraws a scheduled cancellation inside the paid period. Otherwise 409 `payment_required` |
| D9 | A cancel after the renewal time with no recorded outcome ends service at once; the pending base fee is not due; the 90-day clock starts |
| D10 | Trial reminder emails are B2's |
| D11 | 403 `billing_only` on `PATCH sites/{site_id}/` and `GET`/`PATCH sites/{site_id}/business-profile/`. The site list, site detail, `account/setup/`, account and billing endpoints answer exactly as today |
| D12 | The scope amendment in section 9 |
| D13 | `prepare_disposal`. Inside a paid term it cancels and refuses for now with `paid_term_running`; otherwise it cancels and the disposal proceeds. Follows the Task's P10.1. The coordinator brings the Annual Plan case back to Coach Ed at B3 |
| D14 | Follow R-4b, R-4c and R-4d: a suspension records its dates; payment is refused at or after the deletion date; a later cancellation does not restart the clock |
| D15 | Never-paid subscriptions keep `download_until` and `deletion_due_at` empty until collection is live, so they have no last date to pay. B2 writes `clocks_started` on the go-live day |

The rest of this section gives each ruling's detail and its source.

### D1. The last date to pay

- **Sources.** L26: a payment can restore service "only before the existing day-104 refusal point". Day 104 was the trial end (day 14) plus the 90-day deletion date. L75: for a website whose trial ended without any payment the deletion date is 30 days after service end. R-4 (NPC rulings): never-paid accounts are deleted 30 days after the trial ends. R-4b: a suspended subscriber "can pay and reactivate until that date".
- **Rule.** One rule for every ended or suspended subscription: a payment is accepted only before that site's deletion date.
  - Trial ended with no payment method: service end + 30 days, which is **day 44** of the trial (14 + 30).
  - Cancelled, paid at least once: service end + 90 days.
  - Suspended: the suspension instant + 90 days if it ever paid, + 30 days if it never paid (D14).
- **Example.** Account created 11 November 2026, 10:00 AM Manila. Trial ends 25 November 2026, 10:00 AM. Once collection is live (D15), payment is refused from **25 December 2026, 10:00 AM**.
- The payment is judged by its authenticated success time, not by when we learn of it (contract G4a).
- The last date to pay is not a stored column. It is always the deletion date, and `GET billing/` shows it as `pay_by`. B1 enforces it in the one function that records a reactivation payment. B2 calls that function; B1 takes no payment.

### D2. A first site created after the 14 days have passed

- **R-TERMS-1** ("decided by the published Terms, not a new ruling"): the first website gets no new free trial; its trial starts when the account is created; a first store built after the trial ended "joins ... as it is (for example \"Cancelled - no payment method\"), and the subscriber pays to continue. Its normal deletion date stays as it is."
- **Rule.** Site creation writes the trial from the account's creation date and, when the 14 days have already passed, ends it in the same transaction with service end at the trial-end instant (day 14). The clocks count from that instant, never from the site's creation.
- **Gap, not built in B1.** Once collection is live, a first site created after day 44 is past its deletion date at the moment it is created and can never be paid for. B1 adds **no refusal** to `create_first_site`. Before collection is live nobody is harmed, because by D15 no clock runs. Coach Ed decides the gap with B2 (section 13).

### D3. The task queue

- Celery (`celery[redis]`, the 5.x line; the exact pin is set when `uv lock` runs, in pull request 4), with Redis as the broker through the existing `REDIS_URL`. No result store. The schedule is a fixed list in settings, so no schedule table, no second new package and no migration for it. The broker uses a separate database number on the same Redis server, so clearing the cache can never drop queued work; this needs no new variable.
- Still to verify in pull request 4: the version pin, and whether django-redis `clear()` flushes the whole database.

### D4. Railway: two services and one variable (staging only)

- **Services.** `worker` and `beat`, in the `negosyoonline-backend` project, staging environment, built from this repository, with the same variables as `api`. No public domain, no health path. `beat` runs as exactly one instance.
- **Variable.** `BILLING_ENABLED`, on `api`, `worker` and `beat`. Off by default. Not a secret. While it is off: site creation writes no subscription, the billing endpoints and the by-host lookup answer 404, the sweep does nothing. It exists so the code can be merged and deployed before the tables are created (the SMS build did the same).
- **What runs when.** `beat` queues one job, the lifecycle sweep, every 5 minutes. `worker` runs it. Nothing else is scheduled in B1.
- **Each is a separate approval:** creating `worker`; creating `beat`; setting `BILLING_ENABLED=1`. Production is not part of this.

### D5. Migration

- One migration: `apps/billing/migrations/0001_initial.py` (section 8). The file is approved. Running it on staging, per `docs/runbooks/migrations.md`, is a separate approval.
- No migration in `apps/sites`, `apps/accounts` or any other app.

### D6. Backfill of sites made before B1 (separate approval to run on staging, dry run included)

- It is a management command, not a migration: `manage.py billing_backfill_subscriptions`. Without `--apply` it only prints the counts. It can be run again safely: a site that already has a subscription is skipped.
- For each site that is not removed and has no subscription it writes exactly what site creation writes (D2), with the trial starting at the account's creation date.
- Removed sites (their account was disposed of) get no subscription. They are never served and owe nothing.
- **Effect on staging.** When `BILLING_ENABLED` is set and the backfill runs, every existing site whose account is older than 14 days goes off at once (storefront 404, owner Billing-only), and nothing can bring it back until B2 takes payments, except the exemption in D7. A site whose account is younger than 14 days stays on until its own day 14.
- How many sites that is: not known. Nothing has been run against staging.
- Order on staging, each step on its own yes: merge, run the migration, set `BILLING_ENABLED=1`, dry-run the backfill, read the counts, run it with `--apply`.

### D7. Test sites are never billed (Q1): `billing_exempt`

- **Sources.** The Task lists "an approved test site is never billed" as settled. R-5a and R-D1 (NPC rulings) describe a legacy test store that has no billing link and is created only by the admin. The new backend has no such store: `create_first_site` is the only creation path and, with B1, every site gets a subscription.
- **Rule.** A yes/no field `billing_exempt` on the subscription. It is set once, by a terminal command that requires an operator name and a reason and writes an admin audit entry. It is never cleared: no command, screen or endpoint clears it.
- An exempt site is served and is never charged. Nothing changes an exempt subscription: the sweep skips it, and a cancel, a withdrawal and every payment function refuse it with `billing_exempt`.
- It does not give the site R-5a's "deleted right away when closed": that belongs to store disposal, not B1.

### D8. What "reactivate" does before payments exist

- Every way back from an ended or suspended state needs a successful payment (L14, L17), which is B2. The one case that needs no money: a paid-cycle cancellation whose paid period has not ended yet.
- **Rule.** `POST billing/reactivate/` withdraws a scheduled cancellation while the paid period is still running: the subscription is `active` again and will renew. No charge, no refund, no date changes. In every other case it answers 409 `payment_required`; B2 replaces that answer with the payment route.

### D9. Cancelling after the renewal time, before any payment outcome is recorded

- The Parent's comment of 2026-10-26 lists three cases Coach Ed had not ruled in legacy. Two are settled by the rules since then: "reactivating a cancelled trial early" (a trial cancellation ends service at once; coming back needs a payment, B2) and "cancelling a trial after day 14 before its first run" (the state is computed from the time, so the trial has already ended and the cancel answers "already cancelled").
- **Rule for the third.** A paid period has ended, the renewal has no recorded outcome yet, and the owner cancels: it is treated as a cancellation during renewal recovery. Service ends at once, the pending base fee is not due, and the 90-day clock starts at that moment.
- This cannot occur on staging until B2 records payments. B1 writes and tests the rule.

### D10. Trial reminder emails

Not in B1. B2 sends them, 3 days and 24 hours before trial end, with the payment-method page they point to and wording Coach Ed approves.

### D11. The Billing-only rule on the owner's other endpoints (L8)

- L8: suspended, or cancelled after service end, "all owner functions turn off except sign-in and a Billing-only screen".
- **Rule.** B1 turns off, with 403 `billing_only`: `PATCH sites/{site_id}/`, `GET` and `PATCH sites/{site_id}/business-profile/`. It leaves on, answering exactly as today: sign-in and sign-out, the account endpoints, `GET sites/`, `GET sites/{site_id}/`, `GET account/setup/`, and the billing endpoints. A test lists every route under `sites/{site_id}/` and fails if one is neither turned off nor allowed by name.
- This edits `apps/sites/api.py`, which the Subscriber Onboarding coordinator owns (section 12). The Billing-only screen itself is a dashboard page and is not part of B1.

### D12. Amendment to the scope document

The text is in section 9. It is committed with pull request 1.

### D13. An account disposal while a subscription exists: `prepare_disposal`

- **Sources.** R-3: on a deletion request while a final bill is owed, "delete the customer and store data at once. Keep only the subscriber's name and billing email until the final bill is issued and settled, then delete those too." R-4c, R-4d: a suspended subscriber's data is deleted at suspension + 90 days whether or not usage is unpaid. So money owed does not stop a disposal. The Task (P10.1, 2026-11-02): "An account-deletion request from an active subscriber is a cancellation scheduled for the end of the paid term; no immediate deletion, no refund."
- **Ruling.** Follow the Task's P10.1: inside a paid term the request cancels and the disposal waits for the end of the term. Everywhere else the cancellation ends service at once and the disposal proceeds.
- **What B1 builds.** `billing.services.prepare_disposal(account, now, actor)`, called by `dispose_account`:
  - Trial, payment requires attention, suspended: it cancels the subscription (the `cancelled` transition, actor `operator`), service ends at once, and the disposal proceeds in the same transaction.
  - Paid term running: it cancels (service ends at the end of the term), and the disposal is refused for now with the reason `paid_term_running` and that date. The operator runs the command again after it.
  - Already ended, exempt, or no subscription: nothing to do; the disposal proceeds.
  - A dry run changes nothing and reports what it would cancel and whether it would be refused.
  - It locks the account's subscription rows (order: Account, Onboarding, Subscription).
- Billing rows are never deleted or blanked by a disposal. They hold no personal value: only the site, states, dates and settlement. The 5-year rule for billing and tax records is untouched.
- **What waits.** B2: refuse while a payment attempt is open or held for review (NPC spec 3.6), and the R-3a hold, in which the name, the sign-in email and the business name on the bill stay until the amount is paid; it changes what `dispose_account` blanks, so it is brought with the NPC coordinator. B4: R-4c and R-4d usage. B5: wait for the final statement to be delivered. In B1 nothing can be owed, so no hold can arise.
- The automatic disposal of a store and account at the deletion date is not built in B1: the Task says only the dates are recorded.

### D14. A suspension has dates (R-4b, R-4c, R-4d)

- **R-4b** (NPC rulings, 2026-11-03): "Suspension counts as the end of the service." The deletion clock starts on the day of suspension: 90 days if the subscriber paid at least once, 30 days if never paid. "The subscriber can pay and reactivate until that date. No other period is added."
- **R-4c:** a paid subscriber who is suspended and never comes back does not owe the unbilled usage; the account and data are deleted 90 days after the suspension.
- **R-4d:** a subscriber who is suspended and then cancels still owes the unpaid usage of the last paid cycle, billed at the cancel; "the cancellation never restarts the clock".
- The contract r25 says the opposite in its G4a table ("No deletion clock, because a suspension deletes nothing") and at line 274. Coach Ed's ruling of 2026-11-11 is that R-4b, R-4c and R-4d are followed.
- **What B1 builds.** Both suspension transitions record `service_ended_at` = the suspension instant, `download_until` = + 30 days and `deletion_due_at` = + 90 days (ever paid) or + 30 days (never paid, and only once collection is live, D15). The reactivation function refuses a payment at or after the deletion date. A cancellation made while suspended keeps those dates and starts no new clock. The subscription stays `suspended` until the owner cancels or a payment succeeds; B1 cancels nothing automatically.
- **Money, not B1.** R-4c and R-4d usage are for B4, with B2.

### D15. What B1 records before payment collection is live (R-4a)

- **R-4a:** "The never-paid 30-day clock starts only after the system has officially ended the trial (closed for no payment method, or cancelled by the subscriber), and only once payment collection is live. A subscriber who was ready to pay is never deleted because NegosyoOnline could not charge them." The NPC spec's go-live list (3.12, item 2) repeats it.
- B1 has no collection. So every trial that ends in B1, on site creation, in the sweep and in the backfill, ends before collection is live.
- **Rule.**
  - B1 still ends the trial: the state is `cancelled_no_payment_method` and `service_ended_at` is the trial-end instant (for a cancellation during the trial: `cancelled`, at the cancel time). The storefront is off (Q4: no free service).
  - B1 leaves `download_until` and `deletion_due_at` **empty** on a never-paid subscription, and `GET billing/` says why with `clocks_started: false` and an empty `pay_by`. Nothing in B1 can refuse a payment on such a site by date, and no retention rule can pick it up, because there is no date.
  - Whether collection is live is one function, `apps/billing/plan.py: collection_live()`, that answers "no" in B1. No variable is added for it. The dates a never-paid subscription gets once it answers "yes" are written and tested in B1.
  - A subscription that paid at least once always gets its dates. In B1 that happens only in tests.
  - **On the day collection goes live (B2):** B2 writes one `clocks_started` transition for each never-paid ended subscription with empty dates, effective at the go-live instant, setting both dates to go-live + 30 days. From then on a trial that ends gets its dates at once. So every person who could not pay has a full 30 days from the day paying became possible.
- **Consequence.** Until B2, no never-paid site has a deletion date or a last date to pay. The backfill (D6) writes none.

### Read after the approval: the rest of the NPC readiness spec

On 2026-11-11, after the approval and before any code, this builder read the parts of the NPC readiness spec r13 it had not read: the rest of 3.2, and 3.7 to the end. Nothing in them changes a ruling above or adds work to B1. What touches billing, and where it goes:

- **Download date (3.2.5, NPC-70):** the download date is never later than the deletion date, and a cancellation after a suspension never shows a later one. B1 already meets this: download is + 30 days, deletion + 30 or + 90, and a cancellation while suspended keeps both dates.
- **The disposal claim (3.2.5):** `disposal_claimed_at` on the subscription, the refusals keyed on it (reactivation, first collection, cancel, profile save, statement adjustment) and the profile blanking at the claim belong to the automatic disposal of a store, which B1 does not build. They are listed in section 13 for B2 and the NPC coordinator.
- **`store_create_refusal` in `create_first_site` (3.2.1):** an NPC rule for a held or disposed account. Not B1; B1 adds no refusal there (D2).
- **Export window for a suspended store (3.2.2), the send guard (3.2.3), the time rules (3.7), the date-of-birth erase (3.8), the policy pages, the filing text and hq (4 to 8):** nothing for B1.

## 3. Questions answered by the coordinator

- **Q1 (answered by the coordinator, 2026-11-11: confirmed; no endpoint or task reaches these functions in B1). Where B1 stops and B2 starts.** The Task gives B2 "the payment-time rules". B1 cannot meet its own acceptance ("a paying site is never shut by sweep lag", "a paid-cycle cancel keeps service to period end") without paid cycles. This plan therefore writes, in B1, the transitions a payment outcome causes (first payment, renewal, failure, recovery, reactivation), each as one service function that takes the outcome time and our reference. B1 calls them only from tests. B2 owns everything that decides what the outcome is: the provider, the attempt, the amount, the authenticated time, held and disputed payments, and the automatic restore after suspension with its guards (L33, L35, L37, L39, L40). Confirm this split.
- **Q2 (answered: B1 does it, behind `BILLING_ENABLED`). The Santé Site Owner label.** Scope section 12 says the label gains the subscription condition "when Billing exists", in `apps/sites/labels.py`. This plan does it in B1 (section 6.4). That file belongs to the Onboarding coordinator. Confirm B1 does it, or that their session does.
- **Q3 (answered, 2026-11-11: whichever pull request merges second merges `main` first and keeps both sides; the Onboarding sessions have nothing open or unpushed in these three files and add no route under `sites/{site_id}/`). Order with the Onboarding coordinator** for `apps/sites/services.py`, `apps/sites/api.py` and `apps/sites/labels.py` (section 12).
- **Q4 (answered: confirmed, all left out). Left out of B1 on purpose:** `GET plan/`, `PUT billing/profile/` and the `BillingProfile` table (B5 needs them for statements), `billing/usage/`, `billing/statements/`, `billing/payment-method/setup/`, a schedule for `run_retention`, and moving the sign-in emails from the web thread to the queue. Say if any belongs in B1.

## 4. Deliverable, acceptance and scope, traced

| Deliverable | Source |
|---|---|
| One subscription and one trial per website, created with the site | Task, "Objective", "Adapted" |
| Billing cycles with real start and end times, 3:00 PM Asia/Manila | Task, "Dates after service end" |
| The six states and every transition, written once, idempotent, with its effective time | Task, "States", "Accepted when" |
| Cancel and reactivate | Task, "Objective", "Cancellation" |
| Storefront availability from the subscription state; `GET public/sites/by-host/` | Task, "Suspension and site availability", "Adapted" |
| Worker and beat services for the sweep | Task, "Adapted" |
| The scope amendment | Task, "Adapted" |
| The disposal step, `prepare_disposal` (D13) | Brief, item 9; `docs/runbooks/retention.md` line 113 |

Accepted when (Task, word for word in meaning): each transition written once and idempotent, replays and concurrent runs change nothing; a site past due date plus 7 days is suspended even if the sweep has not run, and a paying site is never shut by sweep lag; trial cancel ends service at once with no charge or statement, a paid-cycle cancel keeps service to period end; another account's site answers 404 on every billing endpoint; by-host answers 404 for a suspended or ended site and serves the others; tests pass in CI on SQLite and PostgreSQL.

Not in B1 (Task): payment method, charging, webhooks, retries, automatic restore (B2); annual and promo (B3); usage (B4); statements (B5); referrals (B6); deletion or anonymisation of data (only the dates are recorded); Membership Pack; the buyer portal. Also not in B1: any page, any hq screen, any Django Admin list for billing.

## 5. Models, states and transitions

A new app, `apps/billing` (scope section 3). Other apps reach it only through `apps/billing/services.py`.

### 5.1 Tables

**`Subscription`**, one per site.

| Field | Meaning |
|---|---|
| `site` | One-to-one, protected |
| `state` | The state last written by a transition. Never read on its own to decide service (5.3) |
| `trial_start_at`, `trial_end_at` | The only place trial dates live. End = start + 14 days, to the second |
| `method_at_trial_end` | Yes, no or not yet decided. Whether a payment method was active at the trial-end instant. Written once and never changed (Task, "decided once ... and frozen") |
| `anchor_day` | The Manila day of the month cycles renew on. Set when cycle 1 opens and again when a reactivation opens a new cycle |
| `attention_started_at` | The scheduled due date the failed payment was for |
| `cancel_requested_at` | When the owner cancelled |
| `service_ended_at` | When service ended or will end. A trial that ended with no method: the trial-end instant. A suspension: the suspension instant (D14). A paid-cycle cancellation: the end of the paid period, in the future. Any other cancellation: the cancel time. A cancellation while suspended leaves it as the suspension set it |
| `download_until` | `service_ended_at` + 30 days. Empty on a never-paid subscription until collection is live (D15) |
| `deletion_due_at` | `service_ended_at` + 90 days if at least one cycle was ever paid, + 30 days if none was. Empty on a never-paid subscription until collection is live (D15). It is also the last date to pay (D1); there is no separate column for that. Recorded only: B1 deletes nothing |
| `billing_exempt` | D7. Set once, never cleared |
| `created_at`, `updated_at` | |

**`SubscriptionTransition`**, append-only.

| Field | Meaning |
|---|---|
| `subscription` | Protected |
| `code` | Which transition (5.4) |
| `from_state`, `to_state` | |
| `actor` | `site_creation`, `backfill`, `sweep`, `owner`, `payment`, `operator` |
| `idempotency_key` | Unique. The second write with the same key is refused by the database and treated as already done |
| `effective_at` | The time the change took effect by rule. Never the time the code ran |
| `recorded_at` | When the row was written |
| `detail` | The cycle sequence and, for a payment, our reference. No personal value |

**`BillingCycle`**

| Field | Meaning |
|---|---|
| `subscription`, `sequence` | Unique together. The trial is sequence 0; paid cycles count from 1 |
| `kind` | `trial` or `paid` |
| `start_at`, `end_at` | With time zone. A check constraint requires end after start |
| `settlement` | `not_billable` (the trial), `pending`, `paid`, `lapsed` |
| `paid_at`, `payment_reference` | Filled by the payment functions. Empty in B1 outside tests |

Amounts are not stored in B1: no charge is created here. The plan price (PHP 2,499, 249,900 centavos) is one constant in `apps/billing/plan.py`, shown by `GET billing/`. `ChargeLine` and the amount of each cycle arrive with B2.

### 5.2 Cycle dates

One pure function, `cycle_end(start_at, anchor_day)`, is the only place cycle length lives:

- A cycle ends at 3:00 PM Asia/Manila on the anchor day of the month after the month it starts in.
- If that month has no such day, it ends on the month's last day. The shortening does not carry forward: anchor 31 gives 28 February, then 31 March.
- Cycle 1 starts at the trial-end instant; a reactivation cycle starts at its payment time. The anchor is the Manila calendar day of that start.
- December rolls into January.

### 5.3 State is computed from the time

One read, `services.position(site, at=now)`, answers every "what state is this site in" question: the by-host lookup, the billing endpoints, the Billing-only rule, the label, the disposal step and the sweep's own choice of what is due. It writes nothing. It returns the state at that instant, whether the storefront is served, whether the owner is Billing-only, and the dates.

| Rows say, and the time is | State reported | Storefront |
|---|---|---|
| Trial, before trial end | `trial_active` | served |
| Trial, at or past trial end, no method active at the trial-end instant | `cancelled_no_payment_method` | off |
| Trial, past trial end, a method was active, no outcome yet, before trial end + 7 days | `trial_active` | served |
| The same, at or past trial end + 7 days (L11) | `suspended` | off |
| Paid cycle, before its end | `active` | served |
| Paid cycle ended, renewal outcome not yet known, before its end + 7 days | `active` | served |
| The same, at or past its end + 7 days (L11) | `suspended` | off |
| Payment requires attention, before the due date + 7 days | `payment_requires_attention` | served |
| Payment requires attention, at or past the due date + 7 days | `suspended` | off |
| Suspended | `suspended` | off |
| Cancelled, before service end | `cancelled`, with the service-end date | served |
| Cancelled, at or after service end | `cancelled` | off |
| Cancelled for no payment method | `cancelled_no_payment_method` | off |
| Exempt (D7), whatever the rows say | the stored state | served |
| No subscription, `BILLING_ENABLED` off, or any error while reading | none | off |

So the sweep only catches the stored rows up with what `position` already says. A late sweep shuts nobody early and serves nobody late.

In B1 no payment-method table exists, so "a method was active at the trial-end instant" is always "no". It is asked through one function, `apps/billing/methods.py: active_at(site, instant)`, whose body B2 replaces with a read of the payment-method history as of that instant.

Billing-only (L8) is true when the reported state is `suspended`, `cancelled` at or after service end, or `cancelled_no_payment_method`. It is never true for an exempt subscription, for a site with no subscription, or while `BILLING_ENABLED` is off.

### 5.4 Transitions

Every write goes through one function, `services._apply`, inside a transaction that holds the subscription's row lock. It checks the from-state, inserts the transition with its key, and updates the subscription and its cycles. If the key already exists, nothing changes and the earlier result is returned. Every public write first calls `catch_up`, in the same transaction, which applies the time-driven transitions that are due (the rows marked "sweep" below). So a cancel or a payment never acts on a stale state, and none waits for the sweep. An owner's write catches up to the current time. A payment function catches up to the payment's outcome time, so the payment is judged by when it succeeded and not by when we learned of it (D1).

An exempt subscription (D7) is changed by nothing: the sweep skips it and every write below refuses it with `billing_exempt`.

| Code | From | To | Effective at | Idempotency key | Fired by |
|---|---|---|---|---|---|
| `trial_started` | (new) | `trial_active` | The account's creation time | `trial_started:<sub>` | Site creation; backfill. Opens cycle 0 |
| `trial_ended_no_method` | `trial_active` | `cancelled_no_payment_method` | `trial_end_at` | `trial_ended:<sub>` | Sweep; `catch_up`. Freezes `method_at_trial_end` = no. Service end = `trial_end_at`. Dates by the rule below (30 days; empty before collection is live). No charge, no statement |
| `first_payment_succeeded` | `trial_active` (method active) | `active` | `trial_end_at` | `paid:<reference>` | Payment function (B2 calls it). Cycle 1 from trial end, paid |
| `first_payment_failed` | `trial_active` (method active) | `payment_requires_attention` | `trial_end_at` | `failed:<sub>:1` | Payment function. Cycle 1 pending |
| `renewed` | `active` | `active` | The ended cycle's `end_at` | `paid:<reference>` | Payment function. Next cycle paid |
| `renewal_failed` | `active` | `payment_requires_attention` | The ended cycle's `end_at` | `failed:<sub>:<n>` | Payment function. Next cycle pending |
| `recovered` | `payment_requires_attention` | `active` | The outcome time | `paid:<reference>` | Payment function, only for an outcome time before the due date + 7 days. The pending cycle becomes paid from its original start |
| `suspended_after_attention` | `payment_requires_attention` | `suspended` | Due date + 7 days | `suspended:<sub>:<n>` | Sweep; `catch_up`. Pending cycle becomes `lapsed`. Service end = that instant; dates by the rule below (D14) |
| `suspended_no_outcome` | `active` or `trial_active` (method active) | `suspended` | Due date + 7 days | `suspended:<sub>:<n>` | Sweep; `catch_up` (L11). Opens the due cycle if missing, then marks it `lapsed`. Service end = that instant; dates by the rule below (D14) |
| `cancelled` | `trial_active`, `active`, `payment_requires_attention`, `suspended` | `cancelled` | The request time | `cancelled:<sub>:<k>`, k = 1 + earlier cancellations | Owner; operator through `prepare_disposal` (D13). Service end by the table below |
| `cancellation_withdrawn` (D8) | `cancelled`, before service end | `active` | The request time | `withdrawn:<sub>:<k>` | Owner. Clears the cancel, service-end, download and deletion dates |
| `reactivated` | `suspended`, `cancelled` at or after service end, `cancelled_no_payment_method` | `active` | The outcome time | `paid:<reference>` | Payment function, refused at or after the deletion date when there is one (D1). New paid cycle from the outcome time; new anchor day; the old unpaid cycle stays `lapsed` (L14, L17); cancel, service-end, download and deletion dates cleared |

`clocks_started` is B2's transition (D15). B1 does not write it.

**The dates after service end, one rule for every row.** `download_until` = `service_ended_at` + 30 days. `deletion_due_at` = `service_ended_at` + 90 days if any cycle of the subscription was ever paid, + 30 days if none was. If none was ever paid and collection is not live, both stay empty (D15). "Ever paid" is read from the cycles: at least one with settlement `paid`.

Where a cancellation ends service:

| State when cancelled | Service ends | Clock |
|---|---|---|
| Trial | At once | 30 days (never paid) |
| Payment requires attention, no payment ever succeeded | At once | 30 days |
| Paid cycle running | End of the paid period | 90 days from that end |
| Payment requires attention after a paid cycle | At once; the pending base fee is not due | 90 days |
| Paid cycle ended, no outcome yet (D9) | At once; the pending base fee is not due | 90 days |
| Suspended (D14, R-4d) | It already ended at the suspension | Unchanged: the dates the suspension recorded stay. The cancellation starts no new clock |

Both `cancelled` states refuse a second cancel with 409 `already_cancelled`.

**A suspension that is never paid and never cancelled (D14).**

| State | What ends it in B1 | Last date to pay | Deletion date |
|---|---|---|---|
| `suspended`, never paid for again, never cancelled | Nothing. It stays `suspended` until the owner cancels or a payment succeeds | The deletion date | Suspension + 90 days (ever paid) or + 30 days (never paid, once collection is live) |

B1 cancels no suspension automatically and deletes nothing at the date: only the dates are recorded (Task).

Site created after trial end (D2): site creation writes `trial_started` and then `trial_ended_no_method` in the same transaction, with service end at the trial-end instant.

## 6. Where it joins the existing code

### 6.1 Site creation

`apps/sites/services.py`, `create_first_site`: one added call, `billing.services.start_subscription(site, account)`, after `SiteBusinessProfile.objects.create(site=site)` and before `record.first_site = site`. It runs inside the existing transaction and under the Onboarding lock already held, so the Site, its Subdomain, its business profile and its Subscription are created together or not at all. With `BILLING_ENABLED` off the call does nothing.

`apps/billing` imports `apps.sites.models` only, never `apps.sites.services`, so there is no import loop.

Lock order, everywhere: Account, then Onboarding, then Subscription. Billing writes take only the Subscription lock.

### 6.2 `GET /v1/public/sites/by-host/?host=`

New file `apps/sites/api_public.py`, a router with no sign-in, added in `config/api.py`.

- The host is untrusted input. It is lowercased; a port and a trailing dot are dropped. It must be exactly one label followed by `.` and `SITE_PARENT_HOST`. The label must pass the existing subdomain format rule. Nothing from the host is used to build a query or a URL except that one label, as a lookup value.
- Served only when: the name is in the ledger with state `active`; its site is not removed; no operator hold; and `billing.services.position(site)` says served.
- 200 body: `id`, `template`, `display_name`, `accent_color`, `facebook_url`, `instagram_url`, `tiktok_url`. Nothing else: no email, mobile, business profile, account or billing detail.
- Everything else answers the same 404 body: a missing or malformed host, a host outside the parent, a reserved or retired name, an unknown name, a removed site, a held site, a suspended or ended site, no subscription, `BILLING_ENABLED` off, an empty `SITE_PARENT_HOST`, and any error while reading the billing position. **Unknown fails closed.**
- Caching: a 200 carries `Cache-Control: public, max-age=N`, where N is 60 seconds or the seconds left until the site's next scheduled change (trial end, grace end, service end), whichever is smaller. A 404 carries `max-age=30`. So a cache can never serve a site past the moment it goes off by more than the storefront's own clock skew.

### 6.3 Billing endpoints

New file `apps/billing/api.py`, signed-in only, added in `config/api.py`. Every endpoint reaches its site through `sites.services.site_for(request.account, site_id)`, so a missing site, a removed site and another account's site answer the same 404, before any billing row is read.

| Endpoint | B1 behaviour |
|---|---|
| `GET sites/{site_id}/billing/` | The position: `state`, `storefront_served`, `billing_only`, the plan (name, 249900 centavos, PHP, monthly, 14 trial days), trial start and end, the current cycle (sequence, start, end, settlement), `next_due_at`, `attention_ends_at`, `cancel_requested_at`, `service_end_at`, `download_until`, `deletion_due_at`, `pay_by` (D1; always the deletion date), `clocks_started` (D15), `billing_exempt`, `outstanding_centavos` (0 in B1), `can_cancel`, `can_reactivate`. Reads only |
| `POST sites/{site_id}/billing/cancel/` | Empty body; an unknown key is refused. Applies `cancelled` and answers with the same shape as the GET. 409 `already_cancelled` |
| `POST sites/{site_id}/billing/reactivate/` | D8: withdraws a scheduled cancellation, or 409 `payment_required` |

The two POSTs are state-changing, so the existing Origin rule applies to them unchanged. Each is throttled per account. Each writes an account audit event (`subscription_cancelled`, `subscription_cancellation_withdrawn`) through `accounts.services.record_event`.

Not built in B1: `billing/profile/`, `billing/payment-method/setup/`, `billing/usage/`, `billing/statements/...` (Q4).

### 6.4 The label

`apps/sites/labels.py`: the eligible-site rule gains "and the site's subscription is on trial, active or needing payment attention at this moment". Coach Ed's Phase 6 rule stays exactly as it is: the account is not closed and owns a Santé site that is not removed and not on hold; the label is for operators only, is worked out on each read, is never stored and is never returned to the Subscriber. The Billing condition is added to that rule and replaces no part of it.

The condition is on the state, as scope section 12 words it, so a cancellation still inside its paid period (state `cancelled`, storefront still served) does not count. Because the label is one database query over many accounts, billing exposes `services.state_filter(now, states)`, a query condition that mirrors `position`. A test builds one subscription for every row of the table in 5.3 and asserts the condition and `position` report the same state on each. With `BILLING_ENABLED` off the label behaves as today.

### 6.5 The Billing-only rule (D11)

`apps/sites/api.py`: the three endpoints named in D11 call one helper that raises 403 `billing_only` when `position` says so. With `BILLING_ENABLED` off the helper does nothing.

Not changed by B1, whatever the subscription state: `GET sites/`, `GET sites/{site_id}/` and `GET account/setup/`. They answer exactly as today for a suspended or ended site: the same status, the same fields, no new `state`, no new `blocked_reason`, no refusal. The dashboard shows "We couldn't load your setup" for an unknown `state` or `blocked_reason` or a failed `GET sites/`, so a new value there would break it. The Billing-only fact is read from `GET sites/{site_id}/billing/` only. `apps/sites/onboarding.py` is not touched.

### 6.6 An account disposal while a subscription exists (D13)

- Billing exposes `services.prepare_disposal(account, now, actor, dry_run=False)`. It locks the account's subscription rows, so a cancel or a payment cannot slip between it and the blanking. For each of the account's subscriptions:
  - trial, payment requires attention, suspended: it applies the `cancelled` transition with actor `operator`; service ends at once (a suspended one keeps its dates);
  - paid term running: it applies `cancelled`, with service ending at the end of the term, and reports the reason `paid_term_running` with that date. A subscription already cancelled whose paid term is still running reports the same reason and is not written again;
  - already ended, exempt, or none: nothing.
  It returns what it cancelled and the list of reasons. The list is empty when the disposal may proceed. A dry run writes nothing and returns what it would cancel and the reasons.
- `apps/accounts/retention.py`, `dispose_account`: one added step, after the account is re-read under its lock and before anything is counted or blanked. If the list is not empty, a real run raises `NotDisposable` after the cancellation is committed, and blanks nothing; the operator runs the command again after the date. A dry run returns the counts with the cancellations and reasons added, so the operator sees them before trying. The existing rules, counts, audit entry and switch are not changed.
- `never_verified_accounts`: an unverified account cannot create a site, so it can never be refused. The rule catches `NotDisposable` anyway, skips that account and carries on.
- `apps/accounts/management/commands/dispose_account.py`: prints the cancellations and the reasons, and exits with an error when refused.
- `docs/runbooks/retention.md`, line 113: "check by hand ... whether the subscriber still owes a final bill" is replaced by what `prepare_disposal` does and what it does not yet know.
- Billing rows are never deleted or blanked by a disposal.
- **What B2 must add** to `prepare_disposal`: refuse while a payment attempt is submitted, pending or held for review, or a disputed payment is open (L22); and the R-3a hold. **B4:** unpaid final usage (R-4c, R-4d). **B5:** wait for an issued statement to be delivered. Until B2 ships, `outstanding_centavos` is always 0, and the runbook says so.

## 7. Worker and beat

Files: `config/celery.py` (the Celery app), `config/__init__.py` (loads it), settings for the broker and the schedule, `apps/billing/tasks.py` (one task), `railway.worker.toml` and `railway.beat.toml` (each service is pointed at its own file in the Railway dashboard, so `railway.toml` and the `api` service are untouched).

- `worker` start command: `uv run celery -A config worker --loglevel=info --concurrency=2`.
- `beat` start command: `uv run celery -A config beat --loglevel=info --schedule=/tmp/celerybeat-schedule`.
- With no `REDIS_URL` (local development, the SQLite CI job) tasks run in-process, so no broker is needed to run the tests.
- The sweep task, every 5 minutes: if `BILLING_ENABLED` is off, return. Otherwise select the subscriptions whose rows are behind `position` (trial past its end; attention past 7 days; a paid cycle past its end + 7 days with no successor), in batches, and call `catch_up` on each in its own transaction. A failure on one subscription is logged and does not stop the rest. The task result is a count per transition, logged; no personal value.
- Two sweeps at once are safe: each subscription is taken with `SELECT ... FOR UPDATE SKIP LOCKED`, and the transition key is unique. A sweep that dies midway leaves completed subscriptions done and the rest for the next run.
- `manage.py billing_sweep` runs the same function from a terminal, with `--dry-run` that prints what is due and writes nothing. It is how the sweep is checked on staging before the services exist.
- Not done in B1: moving the sign-in emails off the web thread (`apps/operators/background.py`), a schedule for `run_retention`, any reminder email (D10).

The dependency is approved (D3). Still separate approvals, in order: the `worker` service; the `beat` service; `BILLING_ENABLED=1` (D4).

## 8. Migrations

| Migration | Adds |
|---|---|
| `apps/billing/migrations/0001_initial.py` | `Subscription`, `SubscriptionTransition`, `BillingCycle`. Constraints: one subscription per site; unique transition `idempotency_key`; unique (`subscription`, `sequence`); cycle `end_at` after `start_at`; `trial_end_at` after `trial_start_at`; at most one `pending` cycle per subscription. `billing_exempt` is a column of `Subscription` (D7) |

No other migration. No data migration: the backfill is the command in D6. The migration only adds tables, so it is safe to run while the current code is live, and the code is safe to deploy before it runs because `BILLING_ENABLED` is off.

## 9. Amendment to the scope document (D12)

In `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`:

**Section 6, billing.** `UsageEntry` kind: remove `video`. `Subscription`: add the frozen trial-end method answer, the anchor day, and the download and deletion dates. `SubscriptionTransition`: add the code, the actor and `effective_at`. `BillingCycle`: add `kind`; settlement gains `not_billable`.

**Section 7.** Replace the four "billing values" bullets with:

- plan: PHP 2,499 per month; cycles renew at 3:00 PM Asia/Manila on the anchor day of each month, or the month's last day when that day does not exist;
- included per cycle: 1,000 orders and 5 GB storage; overage PHP 5 per order and PHP 30 per GB storage. Video is not in the launch product: no allowance, meter or charge (contract D6, D9);
- no payment outcome by the due date plus 7 days: suspended. The storefront is served during the trial, a paid cycle and the 7-day attention period. State is computed from the current time, never from how far the sweep has run;
- a trial that reaches day 14 with no active payment method becomes `cancelled_no_payment_method`, with no charge and no statement;
- after service end: a 30-day download window; the deletion date is 30 days after service end for a website whose trial ended without any payment, and 90 days otherwise (L75). A suspension counts as the end of the service for these dates (R-4b), and a cancellation after a suspension does not restart them. The never-paid dates start only once payment collection is live (R-4a). Payment is accepted only before the deletion date (D1). Billing and tax records are kept 5 years and no clock here touches them.

The sentence about a first site created after the 14 days gains the rule of D2: its dates count from the trial end, not from the site's creation. The retry schedule line ("retries after 1, 3 and 5 days") moves to B2's plan and is marked unconfirmed until then.

**Section 9, item 4.** Becomes the six Billing builds B1 to B6, with their ClickUp tasks; "Xendit customer and card setup, webhook inbox, charging and retries" are B2.

**Section 11.** "Confirmation of the billing values in section 7" is struck through and answered by this amendment, except the retry schedule.

**Section 12.** A dated entry: Coach Ed's approval of this plan, the rulings D1 to D15, `BILLING_ENABLED`, the worker and beat services, the disposal step, and what the approval does not cover (the migration run, the variable, the services, the backfill run).

## 10. Tests

All in `apps/billing/tests/` unless a file says otherwise. Time is always passed in or frozen; no test reads the real clock.

**Cycle dates (pure, no database)**
- Anchor 31: 31 January 2027 gives 28 February 2027 3:00 PM, then 31 March, 30 April, 31 May.
- Leap year: 31 January 2028 gives 29 February 2028 3:00 PM; anchor 29 from 29 January 2027 gives 28 February 2027, then 29 March 2027; anchor 29 from 29 February 2028 gives 29 March 2028.
- Anchor 30 across February in a normal and a leap year.
- December to January, year rolls.
- Manila day against UTC day: a start at 11:30 PM UTC on 31 March is 1 April in Manila, anchor 1.
- A start before, exactly at and after 3:00 PM on the anchor day; every end is exactly 3:00 PM Manila and is stored with its time zone.
- Trial end is exactly 14 days after the start, to the second, whatever the hour.

**Position (the table in 5.3)**
- One test per row, each at one second before and exactly at its boundary.
- A subscription whose sweep never ran: a trial past day 14 reports `cancelled_no_payment_method`; attention past 7 days reports `suspended`; a paid cycle past end + 7 days reports `suspended`. None of these wrote a row.
- A paid site one second after its 3:00 PM cycle end, with no sweep and no outcome, is still served; at end + 7 days it is not.
- No subscription, the switch off, and an exception inside the read each report "not served".

**Transitions**
- Each row of 5.4: allowed from-states pass, every other state is refused, the effective time is the rule's time and not the run time (run the code a day late and compare).
- Replay: the same call twice changes nothing the second time and returns the first result; row counts are equal.
- `method_at_trial_end` cannot be changed once written.
- Trial cancel: service ends at the request time, no cycle marked payable; both dates empty while collection is not live (D15), and 30 days from the cancel with collection switched on in the test.
- A trial that ends with no method: service end is the trial-end instant; both dates empty while collection is not live; day 44 with it switched on.
- Paid-cycle cancel: still served one second before the period end, off at the end; 90-day clocks from that end.
- Cancel during attention after a paid cycle: immediate; the pending cycle is `lapsed`; 90-day clock from the cancel.
- Suspension (D14): both suspension transitions record service end at the suspension instant, download + 30 days, deletion + 90 days when a cycle was ever paid; a never-paid suspension has empty dates while collection is not live and + 30 days with it switched on.
- Cancel from suspended (R-4d): the state is `cancelled`, and service end, download and deletion dates are exactly the ones the suspension recorded.
- Cancel after renewal time with no outcome (D9).
- Withdrawal (D8): only before service end; afterwards 409.
- Reactivation: refused at and after the deletion date and accepted one second before; day 44 for a never-paid site with collection switched on, service end + 90 days for a cancelled paid one, suspension + 90 days for a suspended paid one; never refused by date while the dates are empty (D15); new anchor day; old cycle stays `lapsed`; the dates are cleared.
- A payment function judges by the outcome time: a recovery whose outcome time is before the due date + 7 days is applied even when it is recorded later and the sweep has not run; one at or after it is refused.
- Exempt (D7): position reports served; the sweep skips it; cancel, withdrawal and every payment function refuse with `billing_exempt` and write nothing.
- Site created after trial end (D2): `trial_started` and `trial_ended_no_method` in one transaction; service end is the trial-end instant, not the creation time.
- `catch_up` runs first inside cancel and reactivate: a cancel sent on day 15 of an unswept trial answers `already_cancelled` and the stored rows are caught up.

**Sweep**
- Moves every due subscription and nothing else; a second run is a no-op.
- One subscription raising does not stop the others.
- Switch off: writes nothing.
- `billing_sweep --dry-run` writes nothing.

**Concurrency and row locks (PostgreSQL only; skipped on SQLite, which has no row locks; the CI PostgreSQL job runs them, as `apps/sites/tests/test_concurrency.py` already does)**
- Two sweeps in two threads over the same due subscriptions: every transition exists exactly once.
- A sweep holding a subscription's lock while a second sweep runs: the second skips it and finishes.
- Cancel racing the sweep at trial end: one of the two orders, never both transitions, never a cancelled trial with the wrong service end.
- Two cancels at once: one transition, one 200 and one 409 or two identical answers.
- A payment function racing a cancel on the same subscription: serialised by the row lock; the result matches one of the two orders.
- Two site creations at once for one account still create one site and one subscription.
- Disposal racing a cancel: one `cancelled` transition; never a blanked account with a running subscription.
- The unique-key constraint itself: a direct second insert with the same key raises.

**Site creation and backfill** (`apps/sites/tests/test_services.py`, `apps/billing/tests/test_backfill.py`)
- Creating a site creates its subscription, cycle 0 and `trial_started`, with trial dates from the account's creation.
- A failure while creating the subscription rolls back the Site, the Subdomain and the profile.
- Switch off: no subscription, site creation unchanged.
- Backfill: dry run writes nothing; apply creates one per site without one; a second apply creates none; removed sites are skipped; an old account's site gets the ended state with service end at day 14 and empty dates.
- `billing_exempt` command (`apps/billing/tests/test_exempt.py`): needs an operator and a reason; writes the admin audit entry; a second run changes nothing; there is no way to clear it.

**API** (`apps/billing/tests/test_api.py`, `apps/sites/tests/test_api_public.py`, `apps/sites/tests/test_isolation.py`)
- Every billing endpoint: signed out is 401; another account's site, a removed site and a made-up id answer the same 404 body, and no billing row of the other account changes.
- Cancel and reactivate without an allowed Origin are refused; an unknown key in the body is refused.
- The response never holds a database row number.
- By-host: 200 for trial, active, attention and a cancellation still inside its paid period; 404 for suspended, cancelled after service end, cancelled for no payment method, removed, held, retired name, reserved name, unknown name, wrong parent host, malformed host, missing host, no subscription, switch off, and a forced error in the billing read. All 404 bodies are identical. The 200 body holds only the listed fields. `max-age` never passes the next boundary.
- Billing-only (D11): the three endpoints answer 403 when suspended or ended and work otherwise; the route inventory test.
- For a suspended site and for an ended site, `GET sites/`, `GET sites/{site_id}/` and `GET account/setup/` answer byte for byte what they answer for the same site on trial: same status, same keys, same `state` and `blocked_reason`.
- `apps/sites/tests/test_isolation.py` already fails when a site route is added without the foreign, missing and removed-site 404 checks, and holds `test_a_patch_to_my_second_site_leaves_my_first_site_unchanged`. The billing routes are added to those checks. The by-host tests and the route inventory test sit beside the existing tests; none is replaced, removed or loosened.
- OpenAPI: the new paths are in `/v1/openapi.json`.

**Label** (`apps/sites/tests/test_labels.py`): the parity test of 6.4; existing label tests still pass with the switch off.

**Retention** (`apps/accounts/tests/test_retention.py`, `apps/billing/tests/test_disposal.py`)
- Trial, attention and suspended: the disposal cancels the subscription (actor `operator`) and proceeds; a suspended one keeps its dates.
- Paid term running: the subscription is cancelled to the end of the term, the disposal is refused with `paid_term_running` and that date, and nothing is blanked; run again after the date, it proceeds. A second run before the date writes no second cancellation.
- Already ended, exempt and no subscription: the disposal runs as before.
- The dry run writes nothing and names what it would cancel and the reason.
- No billing row is deleted or blanked by a disposal.
- Every existing retention test still passes unchanged.

**Repository checks before each pull request:** `uv run python manage.py makemigrations --check --dry-run`, `check`, `test`, and `check --deploy` with `DJANGO_DEBUG=false`; the PostgreSQL run locally if a local PostgreSQL is available, otherwise stated as verified only by CI.

## 11. Pull requests

Each is cut from the latest `origin/main`, small, and safe to merge on its own because `BILLING_ENABLED` is off.

1. **Core.** `apps/billing` models, the migration file, cycle dates, `position`, the transitions, `catch_up`, the sweep function and `billing_sweep` command, their tests, the scope amendment, this plan. `apps.billing` in the app list and the `BILLING_ENABLED` setting.
2. **Joins.** Site creation, the backfill command, the billing endpoints, the `billing_exempt` command, the disposal step (`prepare_disposal`), the runbooks.
3. **Availability.** By-host, the Billing-only rule, the label.
4. **Queue.** The dependency, `config/celery.py`, the task, the two Railway files, `docs/runbooks/billing.md`.

B1 has no page of its own, so there is no visual check. After each merge the staging check is `/healthz/` and, once the migration and the variable are approved, `GET billing/` and by-host for a test site.

## 12. Files touched

New:
- `apps/billing/__init__.py`, `apps.py`, `models.py`, `plan.py`, `clock.py`, `methods.py`, `services.py`, `api.py`, `tasks.py`
- `apps/billing/migrations/__init__.py`, `0001_initial.py`
- `apps/billing/management/__init__.py`, `management/commands/__init__.py`, `billing_sweep.py`, `billing_backfill_subscriptions.py` `billing_exempt.py`
- `apps/billing/tests/` (`__init__.py`, `base.py`, `test_clock.py`, `test_position.py`, `test_transitions.py`, `test_sweep.py`, `test_concurrency.py`, `test_backfill.py`, `test_api.py`, `test_disposal.py`)
- `apps/sites/api_public.py`, `apps/sites/tests/test_api_public.py`
- `config/celery.py`, `railway.worker.toml`, `railway.beat.toml`
- `docs/runbooks/billing.md`

Changed, shared with other sessions:
- `apps/sites/services.py`: one call inside `create_first_site` (Onboarding coordinator's file)
- `apps/sites/api.py`: the Billing-only helper on three endpoints (Onboarding coordinator's file; D11)
- `apps/sites/labels.py`: the subscription condition (Onboarding coordinator's file; Q2)
- `apps/sites/tests/test_services.py`, `test_isolation.py`, `test_labels.py`, `base.py`: added cases and a helper
- `apps/accounts/retention.py`: the one step in `dispose_account` and the skip in `never_verified_accounts`
- `apps/accounts/management/commands/dispose_account.py`: prints the cancellations and the refusal
- `apps/accounts/tests/test_retention.py`: added cases

Changed, not shared as far as this session knows:
- `config/settings.py`: `apps.billing` in the app list, `BILLING_ENABLED`, the Celery settings
- `config/api.py`: two routers added
- `config/__init__.py`: loads the Celery app
- `pyproject.toml`, `uv.lock`: the dependency (D3)
- `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`: the amendment
- `docs/runbooks/retention.md`: line 113
- `README.md`: only if it lists the services or variables

Not touched: `apps/notifications` (so nothing of the SMS coordinator's, and neither `sms_messages` nor `mobile_challenges`), `apps/operators`, `apps/common`, `apps/accounts/models.py`, `apps/sites/models.py`, `railway.toml`, `.github/workflows/`, the frontend repository, and anything in the legacy repository.

## 13. Known gaps and points for later builds

- **For B2, to bring to Coach Ed (D2):** once collection is live, a first site created after day 44 is past its deletion date when it is created and can never be paid for. B1 builds no refusal in `create_first_site`. B2 brings the choice: refuse site creation for such an account, or another rule.
- **For B2 (D15):** the `clocks_started` transition on the go-live day, and `collection_live()` answering "yes".
- **For B2, a success learned late:** a payment that succeeded before the due date + 7 days but is learned after the sweep wrote `suspended` is refused by `recovered` (`invalid_transition`), because the subscription is no longer in `payment_requires_attention`. `reactivated` would accept it, but would open a new cycle from the old outcome time instead of settling the pending one. B2 decides how a late-learned success is applied.
- **Built in pull request 2, beyond section 12:** `apps/operators/audit.py` gained the declared action `billing_exempt_set` (kept whole), because D7 needs an admin audit entry and `audit.record` refuses an undeclared action; `apps/operators/tests/test_audit.py` now also reads `apps/billing`. `docs/runbooks/billing.md` is started in pull request 2 for the backfill, exempt and sweep commands; pull request 4 adds the worker and beat services to it.
- **In-scope choices of pull request 2:** a site with no subscription answers 404 on the billing endpoints; `clocks_started` is true only when a deletion date exists; `next_due_at` during payment attention is the due date owed; `current_cycle` is the latest stored cycle; a cancel or reactivate takes no body or `{}`, and the site is checked before the body, so another account's site answers 404 whatever is sent.
- **Built in pull request 3, for B2:** `services.state_filter` reads the stored `method_at_trial_end` only. A trial past its end whose answer is not yet frozen counts there as "no method", which is what `methods.active_at` answers in B1. When B2 gives `active_at` a real body, `state_filter` must read the payment-method history the same way, or the label and `position` disagree for an unswept trial between its end and the sweep. The parity test in `apps/sites/tests/test_labels.py` covers the frozen answer only.
- **In-scope choices of pull request 3:** an exempt site counts for the label by its stored state, as `position` reports it, so a site exempted after it ended is served and gets no label. The by-host lookup has no rate limit: a storefront calls it on every uncached page, and the answers are cacheable (60 seconds at most for a 200, 30 for a 404). A site with no subscription is not Billing-only (5.3), so its owner endpoints work while the by-host lookup answers 404. The Billing-only tests are in the new `apps/sites/tests/test_billing_only.py`, not in section 12's file list.
- **Verified in pull request 4 (D3):** the lock holds `celery` 5.7.0 with `kombu` 5.7.0; `redis` stays at 8.1.0. django-redis `clear()` is `FLUSHDB`: it empties the cache's whole Redis database, so the broker's own database number is needed, as D3 planned. The broker is `REDIS_URL` on the cache's number plus one (`config/celery.py: broker_url`).
- **In-scope choices of pull request 4:** a queued sweep expires after 5 minutes, so a stopped worker leaves no backlog; `CELERY_ACCEPT_CONTENT` is JSON only; `railway.beat.toml` sets `numReplicas = 1`; the sweep walks the due subscriptions one by one, each in its own transaction, as the function of pull request 1 does, and is not split into batches. Section 12 lists `config/__init__.py` and `apps/billing/tasks.py`; the tests are in the new `apps/billing/tests/test_tasks.py`. `README.md` is not changed: it lists no service, and `BILLING_ENABLED` is already there.
- **For B2 and B5, a ruling relayed by the coordinator (2026-11-11):** Coach Ed confirmed files live in Vercel Blob, not R2.
- **For B2 and the NPC coordinator:** the disposal claim of the NPC readiness spec 3.2.5 (`disposal_claimed_at`, the refusals keyed on it, the profile blanking at the claim) and the R-3a hold. They belong to the automatic disposal of a store, which B1 does not build.
- **For B3:** the Annual Plan case of D13 (an account-deletion request inside an annual term). The coordinator brings it to Coach Ed.
- An operator can cancel a subscription only through an account disposal (D13). There is no hq screen and no separate command for it in B1.
- Known effect, not a decision: a site that cancelled and is still inside its paid period stops counting for "Santé Site Owner" from the cancellation, although its storefront is still served. The approved label wording (scope section 12, 2026-11-11) names trial, active and needing payment attention, and the label authorizes nothing (6.4).
- The account export (`export_account`) does not include billing rows. Not asked for by the Task.
- The dashboard's Billing page and Billing-only screen are frontend work and need their own task.
- The suspension email (L28) is its own task under the contract and is not in B1.
- Nothing deletes or anonymises at the deletion date. B1 records the date only (Task).
- The retry schedule in scope section 7 is not confirmed and is B2's to settle.

## 14. Approvals

Given by Coach Ed on 2026-11-11 ("Approve as recommended"):

1. The plan, with the rulings D1 to D15 in section 2.
2. The scope amendment text (D12).
3. Creating the migration file (D5).
4. The new dependency (D3).

Still to ask, one at a time, on staging only: running the migration; `BILLING_ENABLED=1`; the backfill run, dry run included (D6); the `worker` service; the `beat` service; any other variable.
