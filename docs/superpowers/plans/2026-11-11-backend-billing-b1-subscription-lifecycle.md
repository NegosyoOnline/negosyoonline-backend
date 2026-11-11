# Billing Build B1: subscription lifecycle and cycles (plan)

Date: 2026-11-11. ClickUp task: z8uq9mdwrv (https://app.clickup.com/t/z8uq9mdwrv), parent z8uq9m8h46.
Branch: `feat/z8uq9mdwrv-billing-b1-subscription-lifecycle`, cut from `origin/main` at `7de44b3`.
Status: PLAN ONLY, not approved. No application code, migration, dependency or settings change exists on this branch. Coach Ed said "yes, start B1" on 2026-11-11; the Task says the plan needs his approval before any code.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9mdwrv, description (no comments, checklists, custom fields or attachments as of 2026-11-11; status Open) |
| Parent | ClickUp z8uq9m8h46, description and its 4 comments (all about the legacy build) |
| Brief | The coordinator's brief for this builder, 2026-11-11, with Coach Ed's rulings listed in it |
| NPC rulings | NPC readiness spec, revision 13, legacy repository, read-only: `.claude/docs/superpowers/specs/2026-11-03-negosyoonline-npc-readiness.md`. Read: the rulings table (lines 36-54), section 2, sections 3.5 and 3.6, and lines 214, 220, 226 of 3.2.5. Not read: the rest of 3.2 and sections 3.7 onward |
| Contract | P6.1 contract r25, legacy repository, read-only: rulings table (lines 57-149), Gate G4 (218-258), G4a transition table (317-327). Read for rules only |
| Scope | `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`, sections 3, 5, 6, 7, 8, 9, 10, 12 |
| Code | `apps/sites/{services,models,api,labels,retention}.py`, `apps/accounts/{models,retention}.py`, `apps/common/retention.py`, `apps/notifications/services.py`, `apps/operators/background.py`, `config/{settings,api,urls}.py`, `.github/workflows/ci.yml`, `railway.toml`, `docs/runbooks/{migrations,retention}.md`, at `7de44b3` |
| Legacy behaviour | Two date rules only, read in the legacy `billing.py` to confirm behaviour, nothing copied: the trial ends exactly 14 days after it starts (not at 3:00 PM); a cycle ends at 3:00 PM Asia/Manila on the anchor day of the next month |

Open pull requests in this repository on 2026-11-11: none.

## 2. Items for Coach Ed

Each is a separate yes. The recommended answer is what the rest of the plan assumes. Nothing here is a ruling until he gives it.

### D1. The last date to pay after a trial that ended with no payment (Task, "Open point")

> **Changed on 2026-11-11 by section 2A** (the NPC readiness rulings). Read 2A first; where this text and 2A differ, 2A is the plan.


- **Checked.** L26: a payment can restore service "only before the existing day-104 refusal point". Day 104 was never its own rule: it is the trial end (day 14) plus the 90-day deletion date. L75 says that for a website whose trial ended without any payment, the deletion date is 30 days after service end, not 90.
- **Arithmetic.** Day 0 is the moment the trial starts. Service ends at day 14. 14 + 30 = **day 44**. Before L75 it was 14 + 90 = day 104.
- **Example.** Account created 11 November 2026, 10:00 AM Manila. Trial ends 25 November 2026, 10:00 AM. Payment is refused from **25 December 2026, 10:00 AM** (day 44). The old rule would have refused from 23 February 2027, 10:00 AM (day 104).
- **Recommended.** One rule for every ended subscription: payment is accepted only before the deletion date of that site. Never-paid site: service end plus 30 days (day 44 of the trial). A site that paid at least once: service end plus 90 days. The payment is judged by its authenticated success time, not by when we learn of it (contract G4a).
- **Consequence.** A never-paid subscriber has 30 days after the trial to come back, not 90. On that same day the 30-day download window also closes. With any later refusal date a person could pay for a site whose data is already due for deletion.
- B1 stores and shows this date and enforces it in the one function that records a reactivation payment. B2 calls that function; B1 takes no payment.

### D2. A first site created after the 14 days have already passed

> **Changed on 2026-11-11 by section 2A** (the NPC readiness rulings). Read 2A first; where this text and 2A differ, 2A is the plan.


- **Checked.** Scope section 7: the trial counts from account creation, and a first site created after day 14 "starts in `cancelled_no_payment_method`". The Task: the download window and the deletion clock begin at service end. Taken literally, service ended at day 14, before the site existed. A site created on day 50 would be born past its pay-back date (D1) and past its deletion date.
- **Options.**
  - (a) **Recommended.** The state history says the trial ended at day 14. The clocks (download window, deletion date, pay-back date) start when the site is created: 30 days from site creation.
  - (b) The clocks start at day 14 whatever the site's age. A site created after day 44 can never be paid for.
- **Consequence.** With (a) every new site has 30 days in which it can be paid for, and no free service: its storefront is off until a payment succeeds (L14).

### D3. New dependency: the task queue

- **Recommended.** Celery (`celery[redis]`, the 5.x line; the exact pin is set when `uv lock` runs, after approval), with Redis as the broker through the existing `REDIS_URL`. No result store. The schedule is a fixed list in settings, so no schedule table, no second new package and no migration for it. The broker uses a separate database number on the same Redis server, so clearing the cache can never drop queued work; this needs no new variable.
- **Alternative.** No new dependency: one extra Railway service that runs a management command in a loop. Cheaper now, but B2 (charging, retries, webhook work) and B5 (statement email) need a real queue, and it would be replaced then.
- **Consequence.** One new package and its own dependencies in `uv.lock`. Nothing else changes until the services in D4 exist.

### D4. Railway: two services and one variable (staging only)

- **Services.** `worker` and `beat`, in the `negosyoonline-backend` project, staging environment, built from this repository, with the same variables as `api`. No public domain, no health path. `beat` runs as exactly one instance.
- **Variable.** `BILLING_ENABLED`, on `api`, `worker` and `beat`. Off by default. Not a secret. While it is off: site creation writes no subscription, the billing endpoints and the by-host lookup answer 404, the sweep does nothing. It exists so the code can be merged and deployed before the tables are created (the SMS build did the same).
- **What runs when.** `beat` queues one job, the lifecycle sweep, every 5 minutes. `worker` runs it. Nothing else is scheduled in B1.
- **Each is a separate approval:** creating `worker`; creating `beat`; setting `BILLING_ENABLED=1`. Production is not part of this (no production services until the MVP is complete).

### D5. Migration

- One migration: `apps/billing/migrations/0001_initial.py` (section 8). Two approvals: creating the file in a pull request, and running it on staging per `docs/runbooks/migrations.md`.
- No migration in `apps/sites`, `apps/accounts` or any other app.

### D6. Backfill of sites made before B1 (a data backfill; separate approval to run on staging)

- **This is a data backfill.** It writes rows for existing sites on staging. It needs Coach Ed's own approval to run, separately from the plan, the migration and the variable.
- It is a management command, not a migration: `manage.py billing_backfill_subscriptions`. Without `--apply` it only prints the counts. It can be run again safely: a site that already has a subscription is skipped.
- For each site that is not removed and has no subscription it writes exactly what site creation writes (section 5), with the trial starting at the account's creation date. A site whose 14 days have already passed gets the ended state, with the clocks of D2.
- Removed sites (their account was disposed of) get no subscription. They are never served and owe nothing.
- **Effect on staging.** When `BILLING_ENABLED` is set and the backfill runs, every existing site whose account is older than 14 days goes off at once (storefront 404, owner Billing-only), and nothing can bring it back until B2 takes payments, except the exemption in D7 (a). A site whose account is younger than 14 days stays on until its own day 14.
- How many sites that is: not known. Nothing has been run against staging. The dry run prints the count per resulting state, and it runs only after Coach Ed approves it.
- Order on staging: merge, run the migration, set `BILLING_ENABLED=1`, dry-run the backfill, read the counts, run it with `--apply`.

### D7. Test sites are never billed (Q1), but the new backend has no test-site mark

> **Changed on 2026-11-11 by section 2A** (the NPC readiness rulings). Read 2A first; where this text and 2A differ, 2A is the plan.


- **Checked.** The Task lists "an approved test site is never billed" as settled. Legacy held it in the interim site states, which are not rebuilt. In the new backend every site, including Coach Ed's own and the Santé reference site, would reach day 14 and go off.
- **Options.**
  - (a) **Recommended.** A yes/no field `billing_exempt` on the subscription. Set and cleared only by a terminal command that requires an operator name and a reason and writes an admin audit entry. An exempt site is served, is never charged, and the sweep leaves it alone. No screen is added (hq is the only admin door).
  - (b) Nothing in B1. Every site follows the normal lifecycle; test sites on staging go off 14 days after their account was made.
- **Consequence.** (a) adds one column to the migration in D5 and one command. With (b) the rule waits for a later build and staging test sites must be recreated every two weeks.

### D8. What "reactivate" does before payments exist

- **Checked.** Every way back from an ended or suspended state needs a successful payment (L14, L17), which is B2. The one case that needs no money: a paid-cycle cancellation whose paid period has not ended yet.
- **Recommended.** In B1, `POST billing/reactivate/` withdraws a scheduled cancellation while the paid period is still running: the subscription is `active` again and will renew. No charge, no refund, no date changes. In every other state it answers 409 `payment_required`; B2 replaces that answer with the payment route.
- **Alternative.** No withdrawal: a cancelled subscriber waits for the period to end and pays again. Then B1's `reactivate` only ever answers 409.

### D9. Cancelling after the renewal time, before any payment outcome is recorded

- **Checked.** The Parent's comment of 2026-10-26 lists three cases Coach Ed had not ruled in legacy. Two are settled by the rules since then:
  - "Reactivating a cancelled trial early": a trial cancellation ends service at once; coming back needs a payment (B2).
  - "Cancelling a trial after day 14 before its first run": the state is computed from the time, so at day 14 with no method the trial has already ended; the cancel answers "already cancelled".
- **Still open.** A paid period has ended, the renewal has no recorded outcome yet, and the owner cancels.
- **Recommended.** Treat it as a cancellation during renewal recovery (Task, "Cancellation"): service ends at once, the pending base fee is not due, and the 90-day clock starts at that moment.
- This case cannot occur on staging until B2 records payments. B1 writes and tests the rule.

### D10. Trial reminder emails (3 days and 24 hours before trial end)

- **Checked.** The Task lists the reminders as a settled rule. The email must say "how to add a method", and the page for that is B2. The exact wording is subscriber-facing and has not been written or approved.
- **Recommended.** Not in B1. B2 sends them, with the payment-method page they point to and wording Coach Ed approves. B1's sweep leaves the place for them.
- **Alternative.** B1 sends them now, with wording Coach Ed approves first, saying that payment methods open soon.

### D11. The Billing-only rule on the owner's other endpoints (L8)

- **Checked.** L8: suspended, or cancelled after service end, "all owner functions turn off except sign-in and a Billing-only screen". Today the site endpoints know nothing about billing.
- **Recommended.** B1 turns off, with 403 `billing_only`: `PATCH sites/{site_id}/`, `GET` and `PATCH sites/{site_id}/business-profile/`. It leaves on: sign-in and sign-out, the account endpoints, `GET sites/`, `GET sites/{site_id}/` and `GET account/setup/` (the dashboard needs them to show the Billing-only screen), and the billing endpoints. A test lists every route under `sites/{site_id}/` and fails if one is neither turned off nor allowed by name.
- **Consequence.** This edits `apps/sites/api.py`, which the Subscriber Onboarding coordinator owns. The order must be agreed (section 12). The Billing-only screen itself is a dashboard page and is not part of B1.

### D12. Amendment to the scope document

The text is in section 9. It is committed with the first pull request once the plan is approved.

### D13. The "nothing owed" check before an account is disposed of (R-3)

> **Changed on 2026-11-11 by section 2A** (the NPC readiness rulings). Read 2A first; where this text and 2A differ, 2A is the plan.


- **Recommended rule.** An account cannot be disposed of while any of its sites has a subscription that has not ended, or an outstanding balance. "Ended" means cancelled, or cancelled for no payment method, with the service-end time in the past. Trial, active, payment requires attention, suspended, and a cancellation whose paid period is still running are all "not ended".
- **Consequence.** A person who asks for deletion during a trial or a paid period must cancel first (or an operator does it for them; see section 13). A suspended subscriber must cancel before disposal, because a suspended site "may retain an outstanding payment record" (L20). This matches the ruling that a deletion request from an active subscriber is a cancellation scheduled for the end of the paid term, with no immediate deletion.
- Billing rows are never deleted or blanked by a disposal. They hold no personal value: only the site, states, dates and amounts. The 5-year rule for billing and tax records is untouched.

### D14. What ends a suspension that is never paid or cancelled (two sources disagree)

> **Changed on 2026-11-11 by section 2A** (the NPC readiness rulings). Read 2A first; where this text and 2A differ, 2A is the plan.


- **Contract r25.** Nothing ends it. A suspension has no deletion date and no last date to pay (quotes in section 5.4).
- **Legacy code, not in the contract.** A comment in the legacy `billing.py` cites rulings "R-4, R-4a, R-4b (Coach Ed 2026-11-03)": the data of a suspended site may be disposed of 90 days after the day of suspension if it ever paid, 30 days if it never paid; and a cancellation after a suspension does not restart that clock, the earlier date wins. The contract r25 does not contain the label R-4b. This session has not seen the ruling itself, only the code comment.
- **Where they differ for B1.** (1) Whether a suspended subscription has a deletion date and so a last date to pay. (2) The clock of a cancellation made while suspended: the table in 5.4 starts it at the cancellation; R-4b would keep the earlier date, counted from the suspension.
- **Not chosen here.** The plan follows the contract, the Task's named source, and writes no new rule. No subscription can be suspended on staging before B2 records payments, so this can be settled as a point for B2 without holding B1. If Coach Ed confirms R-4b, B1 changes only two things: the subscription records the suspension-based dates when it is suspended, and the "suspended" row of the cancellation table keeps the earlier date.

## 2A. Reconciliation with the NPC readiness rulings (added 2026-11-11)

The coordinator named a source this plan had not read: the NPC readiness spec r13, with Coach Ed's rulings of 2026-11-03 and 2026-11-04 (R-3, R-3a, R-4, R-4a to R-4d, R-TERMS-1, R-5a, R-D1). The contract r25 names none of them: a search of it finds no "R-3a", "R-4a", "R-4b", "R-4c", "R-4d", "R-TERMS" and no "day of suspension". The D numbers below are unchanged.

| # | Now | Short reason |
|---|---|---|
| D1 | Settled by rulings, with one new part (D15) | R-4, R-4b, L26, L75 give the date; R-4a says when its clock may start |
| D2 | Settled against this plan's first recommendation; one gap left | R-TERMS-1 |
| D7 | Still a decision, narrowed | R-5a and R-D1 describe a mechanism the new backend does not have |
| D13 | Reworked; one conflict left for Coach Ed | R-3, R-3a, R-4b, R-4c, R-4d against the Task's P10.1 rule |
| D14 | Conflict between two documents; recommendation changed to R-4b | R-4b, R-4c, R-4d against the contract's G4a wording |
| D15 | New | R-4a: what B1 records before payment collection is live |

### D14, revised. A suspension has a deletion date (R-4b), if Coach Ed confirms it over the contract's wording

- **R-4b** (NPC spec, ruled 2026-11-03, his words quoted in the row): "Suspension counts as the end of the service." The deletion clock starts on the day of suspension: 90 days if the subscriber paid at least once, 30 days if never paid. "The subscriber can pay and reactivate until that date. No other period is added."
- **R-4c** (2026-11-03): a paid subscriber who is suspended and never comes back does not owe the unbilled usage; the account and data are deleted 90 days after the suspension.
- **R-4d** (2026-11-03): a subscriber who is suspended and then cancels still owes the unpaid usage of the last paid cycle, billed at the cancel; "the cancellation never restarts the clock".
- **Contract r25**, G4a table: "No deletion clock, because a suspension deletes nothing (`terms.html:71`)", and line 274: "A suspended site that was never cancelled has no `deletion_due_at`, so nothing is purged". This is design text citing the Terms page, not a numbered ruling. The contract's r24 text was approved on 2026-11-04 and r25 on 2026-11-06 changed drift only. The contract nowhere acknowledges R-4b. Which sentence was written first cannot be told from the two files.
- **Recommended.** Follow R-4b, R-4c and R-4d: they are explicit rulings in his words, and the contract line is a description of the Terms. Coach Ed confirms.
- **What changes in B1 if confirmed.** Both suspension transitions record `service_ended_at` = the suspension instant, `download_until` = + 30 days and `deletion_due_at` = + 90 days (ever paid) or + 30 days (never paid). `pay_by` is that deletion date, and `reactivated` refuses a payment at or after it. A cancellation made while suspended keeps those dates and starts no new clock. Section 5.4's "never paid and never cancelled" row then reads: stays `suspended`; last date to pay and deletion date = suspension + 90 or 30 days; nothing is cancelled automatically. The "suspended" row of the cancellation table reads: service already ended at the suspension; the clock is unchanged.
- **Money, not B1.** R-4c (no usage owed if still suspended at the date) and R-4d (last paid cycle's usage billed at the cancel) are for B4, with B2.

### D1, revised. Settled: payment is accepted until the deletion date

- R-4: never-paid accounts are deleted 30 days after the trial ends. R-4b: a suspended subscriber "can pay and reactivate until that date". L26 with L75: the refusal point is the deletion date, which for a never-paid site is 30 days, not 90. So the rule this plan recommended is the ruled one: **day 44** for a no-method trial (14 + 30); service end + 90 days for a cancelled subscriber who paid; suspension + 90 or 30 days for a suspended one (D14).
- Left for Coach Ed: only to confirm the arithmetic, and D15, because R-4a decides when the 30-day clock may start.

### D15, new. What B1 records before payment collection is live (R-4a)

- **R-4a:** "The never-paid 30-day clock starts only after the system has officially ended the trial (closed for no payment method, or cancelled by the subscriber), and only once payment collection is live. A subscriber who was ready to pay is never deleted because NegosyoOnline could not charge them." The NPC spec's go-live list (line 522) repeats it: until the sweep and collection are live, the never-paid rule "must not act".
- B1 has no collection. So every no-method trial that ends in B1, on site creation, in the sweep and in the backfill, ends before collection is live.
- **Recommended.**
  - B1 still ends the trial: the state is `cancelled_no_payment_method` and `service_ended_at` is the trial-end instant (a cancellation during the trial: the cancel time). The storefront is off (Q4: no free service).
  - B1 leaves `download_until`, `deletion_due_at` and `pay_by` **empty** on a never-paid subscription, and `GET billing/` says why with `clocks_started: false`. Nothing in B1 can refuse a payment on such a site by date, and no retention rule can pick it up, because there is no date.
  - Whether collection is live is one function in `apps/billing/plan.py` that answers "no" in B1. No variable is added for it in B1.
  - **On the day collection goes live (B2):** B2 writes one `clocks_started` transition for each never-paid ended subscription with empty dates, effective at the go-live instant, setting all three dates to go-live + 30 days. From then on a trial that ends gets its dates at once, as section 5.4 says. So every person who could not pay has a full 30 days from the day paying became possible.
- **Alternative.** At go-live, the dates are counted from each site's own service end. Then a trial that ended more than 30 days before go-live is past its pay-back date on the first day anyone can pay, which is what R-4a forbids.
- **Consequence.** Until B2, no never-paid site has a deletion date or a pay-back date. The backfill (D6) writes none.

### D2, revised. Settled by R-TERMS-1: the dates stay where they are; one gap

- **R-TERMS-1** ("decided by the published Terms, not a new ruling"): the first website gets no new free trial; its trial starts when the account is created; a first store built after the trial ended "joins ... as it is (for example \"Cancelled - no payment method\"), and the subscriber pays to continue. Its normal deletion date stays as it is."
- So this plan's first recommendation, (a), is withdrawn. The clocks count from the trial end (day 14), not from the site's creation: option (b).
- **Before collection is live** this harms nobody: by D15 no clock runs.
- **Gap, once collection is live.** A first site created after day 44 is past its pay-back date and its deletion date at the moment it is created. It can never be paid for. The new backend differs from legacy here: legacy had a billing Site from sign-up, the new backend creates the subscription only with the site.
- **Recommended for the gap.** Keep R-TERMS-1's dates, and have site creation refuse an account whose pay-back date has passed, with a plain message to contact support, so that no unpayable site is created. This is a new refusal in `create_first_site` (the Onboarding coordinator's file) and needs Coach Ed's yes. Without it, B1 creates the site as R-TERMS-1 says and it stays off for good.

### D7, revised. Still a decision: the rulings describe a test store the new backend cannot make

- **R-5a:** only test stores (`is_test_site`) are deleted right away when closed; a non-test store created from the admin side follows the normal subscriber rules. **R-D1:** the store-to-billing link is written only inside `create_first_site`; "the admin creates test stores only". In the NPC spec a test store has no billing link at all (3.5, T-TEST: "`billing_site` is null"), and `is_test_site` is read-only after creation. That is how a legacy test site is "never billed": it has no subscription.
- **The new backend** has no `is_test_site` field, and no way for an admin to create a site: hq is the only admin door and `create_first_site` is the only creation path. Every site is created by a subscriber and, with B1, gets a subscription.
- **So the gap stays.** `billing_exempt` (option (a)) is the nearest equivalent: a mark on a subscriber-created site, set by a terminal command with operator, reason and audit entry. To match the ruling's "read-only after creation" it is set once and not cleared; clearing would need its own ruling. It does not give the site R-5a's "deleted right away when closed": that belongs to store disposal, not B1.
- **Alternatives.** (b) nothing in B1. (c) a separate task builds admin-created test stores with no subscription, as R-D1 describes; B1 then needs nothing, and position treats "no subscription" as not served unless that task says otherwise.
- Recommendation unchanged: (a), now set-once.

### D13, revised. Money owed does not stop a disposal; a running paid term does

- **R-3:** on a deletion request while a final bill is owed, "delete the customer and store data at once. Keep only the subscriber's name and billing email until the final bill is issued and settled, then delete those too." **R-3a:** those are the person's name, the sign-in email and the business name on the bill. **R-4c, R-4d:** a suspended subscriber's data is deleted at suspension + 90 days whether or not usage is unpaid.
- So this plan's first rule, "refuse while a subscription has not ended or a balance is outstanding", is withdrawn, and so is its consequence that a suspended account can never be disposed of.
- **The conflict left for Coach Ed.** The Task (P10.1, 2026-11-02): "An account-deletion request from an active subscriber is a cancellation scheduled for the end of the paid term; no immediate deletion, no refund." R-3 (2026-11-03) says delete at once, and the NPC spec's request path cancels and then disposes of the store at once, with no date rule (3.2.5, line 226). For a subscriber inside a paid term these give different days.
- **Recommended.** Follow the Task, which is the newer text and restates P10.1 as settled: inside a paid term the request cancels, and the disposal waits for the end of the term. Everywhere else the two agree, because the cancellation ends service at once.
- **What B1 builds.** `billing.services.prepare_disposal(account, now, actor)` called by `dispose_account`:
  - Trial, payment requires attention, suspended: it cancels the subscription (the `cancelled` transition, actor `operator`), service ends at once, and the disposal proceeds in the same transaction.
  - Paid term running: it cancels (service ends at the end of the term), and the disposal is refused for now with the reason `paid_term_running` and that date. The operator runs the command again after it.
  - Already ended, or no subscription: nothing to do; the disposal proceeds.
  - A dry run changes nothing and reports what it would cancel and whether it would be refused.
  - It locks the account's subscription rows (order: Account, Onboarding, Subscription).
- This closes the first gap in section 13: the operator's disposal command is the route that cancels for a subscriber.
- **What waits.** B2: refuse while a payment attempt is open or held for review (NPC spec 3.6). B4: R-4c and R-4d usage. B5: wait for the final statement to be delivered. B2 with the NPC coordinator: the R-3a hold, in which three fields stay until the amount is paid; it changes what `dispose_account` blanks, so it is not B1's to change. In B1 nothing can be owed, so no hold can arise.
- The automatic disposal of a store and account at the deletion date (the NPC spec's paid and never-paid triggers) is not built in B1: the Task says only the dates are recorded.

## 3. Questions for the coordinator (not for Coach Ed unless the coordinator says so)

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
| The "nothing owed" check | Brief, item 9; `docs/runbooks/retention.md` line 113 |

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
| `service_ended_at` | When service ended or will end. For a paid-cycle cancellation it is the end of the paid period, in the future |
| `download_until`, `deletion_due_at` | Service end plus 30 days; service end plus 30 days (never paid) or 90 days (paid at least once). Recorded only: B1 deletes nothing |
| `billing_exempt` | Only if D7 (a) is approved |
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

One read, `services.position(site, at=now)`, answers every "what state is this site in" question: the by-host lookup, the billing endpoints, the Billing-only rule, the label, the "nothing owed" check and the sweep's own choice of what is due. It writes nothing. It returns the state at that instant, whether the storefront is served, whether the owner is Billing-only, and the dates.

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
| No subscription, `BILLING_ENABLED` off, or any error while reading | none | off |

So the sweep only catches the stored rows up with what `position` already says. A late sweep shuts nobody early and serves nobody late.

In B1 no payment-method table exists, so "a method was active at the trial-end instant" is always "no". It is asked through one function, `apps/billing/methods.py: active_at(site, instant)`, whose body B2 replaces with a read of the payment-method history as of that instant.

Billing-only (L8) is true when the reported state is `suspended`, `cancelled` at or after service end, or `cancelled_no_payment_method`.

### 5.4 Transitions

Every write goes through one function, `services._apply`, inside a transaction that holds the subscription's row lock. It checks the from-state, inserts the transition with its key, and updates the subscription and its cycles. If the key already exists, nothing changes and the earlier result is returned. Every public write first calls `catch_up`, in the same transaction, which applies the time-driven transitions that are due (the rows marked "sweep" below). So a cancel or a payment never acts on a stale state, and none waits for the sweep.

| Code | From | To | Effective at | Idempotency key | Fired by |
|---|---|---|---|---|---|
| `trial_started` | (new) | `trial_active` | The account's creation time | `trial_started:<sub>` | Site creation; backfill. Opens cycle 0 |
| `trial_ended_no_method` | `trial_active` | `cancelled_no_payment_method` | `trial_end_at` | `trial_ended:<sub>` | Sweep; `catch_up`. Freezes `method_at_trial_end` = no. Sets service end, download window, deletion date (30 days). No charge, no statement |
| `first_payment_succeeded` | `trial_active` (method active) | `active` | `trial_end_at` | `paid:<reference>` | Payment function (B2 calls it). Cycle 1 from trial end, paid |
| `first_payment_failed` | `trial_active` (method active) | `payment_requires_attention` | `trial_end_at` | `failed:<sub>:1` | Payment function. Cycle 1 pending |
| `renewed` | `active` | `active` | The ended cycle's `end_at` | `paid:<reference>` | Payment function. Next cycle paid |
| `renewal_failed` | `active` | `payment_requires_attention` | The ended cycle's `end_at` | `failed:<sub>:<n>` | Payment function. Next cycle pending |
| `recovered` | `payment_requires_attention` | `active` | The outcome time | `paid:<reference>` | Payment function, only for an outcome time before the due date + 7 days. The pending cycle becomes paid from its original start |
| `suspended_after_attention` | `payment_requires_attention` | `suspended` | Due date + 7 days | `suspended:<sub>:<n>` | Sweep; `catch_up`. Pending cycle becomes `lapsed`. No deletion date: a suspension deletes nothing |
| `suspended_no_outcome` | `active` or `trial_active` (method active) | `suspended` | Due date + 7 days | `suspended:<sub>:<n>` | Sweep; `catch_up` (L11). Opens the due cycle if missing, then marks it `lapsed` |
| `cancelled` | `trial_active`, `active`, `payment_requires_attention`, `suspended` | `cancelled` | The request time | `cancelled:<sub>:<k>`, k = 1 + earlier cancellations | Owner. Service end by the table below |
| `cancellation_withdrawn` (D8) | `cancelled`, before service end | `active` | The request time | `withdrawn:<sub>:<k>` | Owner. Clears the cancel and service-end dates |
| `reactivated` | `suspended`, `cancelled` after service end, `cancelled_no_payment_method` | `active` | The outcome time | `paid:<reference>` | Payment function, refused at or after the pay-back date (D1). New paid cycle from the outcome time; new anchor day; the old unpaid cycle stays `lapsed` (L14, L17); service-end, download and deletion dates cleared |

Where a cancellation ends service:

| State when cancelled | Service ends | Clock |
|---|---|---|
| Trial | At once | 30 days (never paid) |
| Payment requires attention, no payment ever succeeded | At once | 30 days |
| Paid cycle running | End of the paid period | 90 days from that end |
| Payment requires attention after a paid cycle; suspended | At once; the pending base fee is not due | 90 days if any payment ever succeeded, else 30 |
| Paid cycle ended, no outcome yet (D9) | At once | 90 days |

Both `cancelled` states refuse a second cancel with 409 `already_cancelled`.

**A suspension that is never paid and never cancelled (see D14).**

| State | What ends it in B1 | Last date to pay | Deletion date |
|---|---|---|---|
| `suspended`, never paid for again, never cancelled | Nothing. It stays `suspended` until the owner cancels or a payment succeeds | None | None |

- The contract r25 sets no end. G4a, row "No outcome by the scheduled date plus 7 days": "No deletion clock, because a suspension deletes nothing". Its purge section (line 274): "A suspended site that was never cancelled has no `deletion_due_at`, so nothing is purged". It names no automatic cancellation and no limit, and its only payment refusal point is the deletion date, which a suspended site does not have.
- So D1's rule sets no last date for a suspended site: B1's `reactivated` function accepts a payment on a suspended subscription whenever it comes. B1 writes no automatic cancellation of a suspension.
- Consequence, for Coach Ed to see: a suspended subscription never becomes "ended", so the "nothing owed" check (D13) refuses to dispose of that account until the owner or an operator cancels it, and B1 has no operator route to cancel (section 13).

"Ever paid" is read from the cycles: at least one with settlement `paid`.

Site created after trial end (D2): site creation writes `trial_started` and then `trial_ended_no_method` in the same transaction, with the clocks from the site's creation time.

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
| `GET sites/{site_id}/billing/` | The position: `state`, `storefront_served`, `billing_only`, the plan (name, 249900 centavos, PHP, monthly, 14 trial days), trial start and end, the current cycle (sequence, start, end, settlement), `next_due_at`, `attention_ends_at`, `cancel_requested_at`, `service_end_at`, `download_until`, `deletion_due_at`, `pay_by` (D1), `outstanding_centavos` (0 in B1), `can_cancel`, `can_reactivate`. Reads only |
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

### 6.6 The "nothing owed" check (R-3)

> Changed by section 2A, D13 revised: the function is `prepare_disposal`, it cancels rather than refuses, and the only refusal in B1 is `paid_term_running`. The file list and the lock order below still hold.

- Billing exposes `services.disposal_blocks(account, now)`. It returns a list of reasons, empty when nothing blocks. B1's reasons: `subscription_not_ended` (per site, with the state and, when known, the service-end date). It locks the account's subscription rows, so a cancel or a payment cannot slip between the check and the blanking.
- `apps/accounts/retention.py`, `dispose_account`: one added step, after the account is re-read under its lock and before anything is counted or blanked. If the list is not empty, a real run raises `NotDisposable` and changes nothing; a dry run returns the counts with the reasons added, so the operator sees the refusal before trying. The existing rules, counts, audit entry and switch are not changed.
- `never_verified_accounts`: an unverified account cannot create a site, so it can never be blocked. The rule catches `NotDisposable` anyway, skips that account and carries on.
- `apps/accounts/management/commands/dispose_account.py`: prints the reasons and exits with an error.
- `docs/runbooks/retention.md`, line 113: "check by hand ... whether the subscriber still owes a final bill" is replaced by what the check does and what it does not yet know.
- **What B2 must add** to `disposal_blocks`: a payment attempt that is submitted, pending or held for review; an open disputed payment (L22); a pending cycle whose base fee is still due. **B4:** unpaid final usage of an already-paid period. **B5:** an issued statement that is not settled. Until B2 ships, `outstanding_centavos` is always 0, and the runbook says so.

## 7. Worker and beat

Files: `config/celery.py` (the Celery app), `config/__init__.py` (loads it), settings for the broker and the schedule, `apps/billing/tasks.py` (one task), `railway.worker.toml` and `railway.beat.toml` (each service is pointed at its own file in the Railway dashboard, so `railway.toml` and the `api` service are untouched).

- `worker` start command: `uv run celery -A config worker --loglevel=info --concurrency=2`.
- `beat` start command: `uv run celery -A config beat --loglevel=info --schedule=/tmp/celerybeat-schedule`.
- With no `REDIS_URL` (local development, the SQLite CI job) tasks run in-process, so no broker is needed to run the tests.
- The sweep task, every 5 minutes: if `BILLING_ENABLED` is off, return. Otherwise select the subscriptions whose rows are behind `position` (trial past its end; attention past 7 days; a paid cycle past its end + 7 days with no successor), in batches, and call `catch_up` on each in its own transaction. A failure on one subscription is logged and does not stop the rest. The task result is a count per transition, logged; no personal value.
- Two sweeps at once are safe: each subscription is taken with `SELECT ... FOR UPDATE SKIP LOCKED`, and the transition key is unique. A sweep that dies midway leaves completed subscriptions done and the rest for the next run.
- `manage.py billing_sweep` runs the same function from a terminal, with `--dry-run` that prints what is due and writes nothing. It is how the sweep is checked on staging before the services exist.
- Not done in B1: moving the sign-in emails off the web thread (`apps/operators/background.py`), a schedule for `run_retention`, any reminder email (D10).

Separate approvals, in order: the dependency (D3); the `worker` service; the `beat` service; `BILLING_ENABLED=1` (D4).

## 8. Migrations

| Migration | Adds |
|---|---|
| `apps/billing/migrations/0001_initial.py` | `Subscription`, `SubscriptionTransition`, `BillingCycle`. Constraints: one subscription per site; unique transition `idempotency_key`; unique (`subscription`, `sequence`); cycle `end_at` after `start_at`; `trial_end_at` after `trial_start_at`; at most one `pending` cycle per subscription. `billing_exempt` only if D7 (a) is approved |

No other migration. No data migration: the backfill is the command in D6. The migration only adds tables, so it is safe to run while the current code is live, and the code is safe to deploy before it runs because `BILLING_ENABLED` is off.

## 9. Amendment to the scope document (D12)

In `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`:

**Section 6, billing.** `UsageEntry` kind: remove `video`. `Subscription`: add the frozen trial-end method answer, the anchor day, and the download and deletion dates. `SubscriptionTransition`: add the code, the actor and `effective_at`. `BillingCycle`: add `kind`; settlement gains `not_billable`.

**Section 7.** Replace the four "billing values" bullets with:

- plan: PHP 2,499 per month; cycles renew at 3:00 PM Asia/Manila on the anchor day of each month, or the month's last day when that day does not exist;
- included per cycle: 1,000 orders and 5 GB storage; overage PHP 5 per order and PHP 30 per GB storage. Video is not in the launch product: no allowance, meter or charge (contract D6, D9);
- no payment outcome by the due date plus 7 days: suspended. The storefront is served during the trial, a paid cycle and the 7-day attention period. State is computed from the current time, never from how far the sweep has run;
- a trial that reaches day 14 with no active payment method becomes `cancelled_no_payment_method`, with no charge and no statement;
- after service end: a 30-day download window; the deletion date is 30 days after service end for a website whose trial ended without any payment, and 90 days otherwise (L75). Payment is accepted only before the deletion date (D1). Billing and tax records are kept 5 years and no clock here touches them.

The sentence about a first site created after the 14 days gains the clock rule of D2. The retry schedule line ("retries after 1, 3 and 5 days") moves to B2's plan and is marked unconfirmed until then.

**Section 9, item 4.** Becomes the six Billing builds B1 to B6, with their ClickUp tasks; "Xendit customer and card setup, webhook inbox, charging and retries" are B2.

**Section 11.** "Confirmation of the billing values in section 7" is struck through and answered by this amendment, except the retry schedule.

**Section 12.** A dated entry: Coach Ed's approval of this plan, the decisions D1 to D13 as he rules them, `BILLING_ENABLED`, the worker and beat services, the "nothing owed" check, and what the approval does not cover (the migration run, the variable, the services, the backfill run).

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
- Trial cancel: service ends at the request time, 30-day clocks, no cycle marked payable.
- Paid-cycle cancel: still served one second before the period end, off at the end; 90-day clocks from that end.
- Cancel during attention after a paid cycle and from suspended: immediate; the pending cycle is `lapsed`.
- Cancel after renewal time with no outcome (D9).
- Withdrawal (D8): only before service end; afterwards 409.
- Reactivation: refused at and after the pay-back date and accepted one second before; day 44 for a never-paid site, service end + 90 days for a paid one; new anchor day; old cycle stays `lapsed`.
- Site created after trial end (D2).
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
- Disposal racing a cancel: the disposal either sees the ended subscription or is refused; never a blanked account with a running subscription.
- The unique-key constraint itself: a direct second insert with the same key raises.

**Site creation and backfill** (`apps/sites/tests/test_services.py`, `apps/billing/tests/test_backfill.py`)
- Creating a site creates its subscription, cycle 0 and `trial_started`, with trial dates from the account's creation.
- A failure while creating the subscription rolls back the Site, the Subdomain and the profile.
- Switch off: no subscription, site creation unchanged.
- Backfill: dry run writes nothing; apply creates one per site without one; a second apply creates none; removed sites are skipped; an old account's site gets the ended state.

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
- A real disposal of an account with a trial, active, attention, suspended or not-yet-ended cancelled subscription is refused and changes nothing; the dry run names the reason.
- After the subscription ended, the disposal runs as before and no billing row is deleted or changed.
- Every existing retention test still passes unchanged.

**Repository checks before each pull request:** `uv run python manage.py makemigrations --check --dry-run`, `check`, `test`, and `check --deploy` with `DJANGO_DEBUG=false`; the PostgreSQL run locally if a local PostgreSQL is available, otherwise stated as verified only by CI.

## 11. Pull requests

Each is cut from the latest `origin/main`, small, and safe to merge on its own because `BILLING_ENABLED` is off.

1. **Core.** `apps/billing` models, the migration file, cycle dates, `position`, the transitions, `catch_up`, the sweep function and `billing_sweep` command, their tests, the scope amendment.
2. **Joins.** Site creation, the backfill command, the billing endpoints, the "nothing owed" check, the runbooks.
3. **Availability.** By-host, the Billing-only rule, the label.
4. **Queue.** The dependency, `config/celery.py`, the task, the two Railway files, `docs/runbooks/billing.md`.

B1 has no page of its own, so there is no visual check. After each merge the staging check is `/healthz/` and, once the migration and the variable are approved, `GET billing/` and by-host for a test site.

## 12. Files touched

New:
- `apps/billing/__init__.py`, `apps.py`, `models.py`, `plan.py`, `clock.py`, `methods.py`, `services.py`, `api.py`, `tasks.py`
- `apps/billing/migrations/__init__.py`, `0001_initial.py`
- `apps/billing/management/__init__.py`, `management/commands/__init__.py`, `billing_sweep.py`, `billing_backfill_subscriptions.py` (and `billing_exempt.py` only if D7 (a))
- `apps/billing/tests/` (`__init__.py`, `base.py`, `test_clock.py`, `test_position.py`, `test_transitions.py`, `test_sweep.py`, `test_concurrency.py`, `test_backfill.py`, `test_api.py`, `test_disposal.py`)
- `apps/sites/api_public.py`, `apps/sites/tests/test_api_public.py`
- `config/celery.py`, `railway.worker.toml`, `railway.beat.toml`
- `docs/runbooks/billing.md`

Changed, shared with other sessions:
- `apps/sites/services.py`: one call inside `create_first_site` (Onboarding coordinator's file)
- `apps/sites/api.py`: the Billing-only helper on three endpoints (Onboarding coordinator's file; D11)
- `apps/sites/labels.py`: the subscription condition (Onboarding coordinator's file; Q2)
- `apps/sites/tests/test_services.py`, `test_isolation.py`, `test_labels.py`, `base.py`: added cases and a helper
- `apps/accounts/retention.py`: the one check in `dispose_account` and the skip in `never_verified_accounts`
- `apps/accounts/management/commands/dispose_account.py`: prints the refusal
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

## 13. Known gaps this plan does not close

- An operator cannot cancel a subscription on a subscriber's behalf: there is no hq screen and no command for it in B1. A person who asks for deletion while subscribed cancels from the dashboard, or this is added by ruling.
- Known effect, not a decision: a site that cancelled and is still inside its paid period stops counting for "Santé Site Owner" from the cancellation, although its storefront is still served. The approved label wording (scope section 12, 2026-11-11) names trial, active and needing payment attention, and the label authorizes nothing (6.4).
- The account export (`export_account`) does not include billing rows. Not asked for by the Task.
- The dashboard's Billing page and Billing-only screen are frontend work and need their own task.
- The suspension email (L28) is its own task under the contract and is not in B1.
- Nothing deletes or anonymises at the deletion date. B1 records the date only (Task).
- The retry schedule in scope section 7 is not confirmed and is B2's to settle.

## 14. Approvals this plan asks for, each separate

1. The plan itself, with the answers to D1, D2, D7, D8, D9, D10, D11, D13, D14 and D15, as revised in section 2A.
2. The scope amendment text (D12).
3. Creating the migration file (D5).
4. The new dependency (D3).
5. Later, one at a time, on staging only: running the migration; `BILLING_ENABLED=1`; the backfill run (D6); the `worker` service; the `beat` service.
