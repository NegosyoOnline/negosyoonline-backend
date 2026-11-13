# Site deletion executor: human-approved erasure of an ended site's store data (plan)

Status: **plan only, for the Manager's review and Coach Ed's approval.** No code, migration, variable, switch, test or staging action is in this branch. It touches deleting data, so nothing is built until the owner decisions in section 12 are answered.

ClickUp task: z8uq9mb22v (https://app.clickup.com/t/z8uq9mb22v), P10.3 "Build human-approved data lifecycle". Parent z8uq9mb226 (Billing Phase 10). Design and policy tasks: z8uq9mb22u (P10.2), z8uq9mb22t (P10.1). Supervisor: BILLING, main task z8uq9m8h46. Read against `origin/main` at `02dde96`.

## 1. Sources

- z8uq9mb22v in full: entry gate (P10.1 and P10.2 accepted, plus a separate migration approval); scope "start with dry-run and explicit per-batch approval. Do not automate production deletion"; five acceptance lines (support export stays; eligible only after service end; dry run never mutates, approved batch idempotent and fully audited; retained records follow the retention matrix; no autonomous production deletion). It has no comments.
- z8uq9mb22u (P10.2, design, no comments): the list of what a design must hold (state model, data map, delete versus anonymise, operator roles, dry-run report, per-batch owner approval, immutable audit log, failure, retry and restore boundaries, export). **P10.2 was never written as a document**; this plan covers its list for the executor and is the design the gate asks for. It is not an "accepted" P10.2 until the owners accept it.
- z8uq9mb22t (P10.1): the 2026-11-02 ruling (a deletion request from a paid subscriber is a cancellation at the end of the paid term, no refund; the 30-day export window and 90-day clock start at service end). The BILLING Supervisor's comment of 2026-11-13 says the public Data Deletion wording does not yet follow that ruling and goes to the Manager separately. This plan does not change a public page.
- z8uq9mb226 (parent): the retention floor (5 years, L61, L62, r26), "No automatic production deletion of Billing records", and that the Pack day-90 purge belongs to the buyer portal task z8uq9mbk0m, not here.
- Comment 1100430000509626 on z8uq9mb383 (G4): O3 (L75, day 44 for a site that never paid, 90 days after service end for a paid one), O1 (purge, 30-day export, buyer portal before launch: Coach Ed's to decide, nothing built on it).
- The rulings log (z8uq9med6x), newest first. Rulings that bind this plan: no Django admin screens, operator work goes in HQ under Finance; billing and tax records kept 5 years (L76); launch payment is bank transfer; one staff account only (Coach Ed's); staging is treated as live data from the first real member; the Manager decides routine items and asks only for payments, accounting, privacy, legal terms, credentials and production; batches of ready branches go up as one PR; Legal wording stays minimal.
- Code, read on `origin/main`: `apps/billing/{models,plan,services,usage,tasks,proofs}.py`, `apps/accounts/retention.py`, `apps/{sites,sante,payments,members,pack,shop,meta_ads}/retention.py`, `apps/common/{retention,private_files,disposal}.py`, `apps/operators/audit.py`, `apps/statements/models.py`, `apps/invoices/models.py`, the models of every app. Runbooks: `docs/runbooks/{retention,support-and-exit,storage,migrations,backups-and-restore,billing}.md`.
- Not read: the public Terms and Privacy pages on staging (quoted by the Supervisor, taken as given), the frontend repository, `docs/runbooks/storage.md` beyond its first lines (it concerns the storage ledger, not deletion). Nothing was run against any environment.

A rule below with no source is marked **choice** (reversible; listed in section 11) or **decision** (section 12).

## 2. What exists and what is missing

Built (`origin/main`):

| Piece | Where | What it does |
|---|---|---|
| Account disposal | `dispose_account`, `apps/accounts/retention.py:110`; `manage.py dispose_account` | Blanks the account and **every** site of it, in place, keeps the rows; refuses while a paid term runs or a proof is under review (`billing.prepare_disposal`, `apps/billing/services.py:1444`) |
| Per-app blank functions | `blank_sites_of`, `blank_payments_of`, `blank_members_of`, `blank_packs_of`, `blank_shop_of`, `blank_meta_of`, `blank_settings_of`, all taking an **account** | The anonymise rules; each deletes private files first and undoes the disposal if a file cannot be deleted |
| Daily retention | `retention.run`, `manage.py run_retention --dry-run`, `RETENTION_ENABLED`, `DOB_ERASE_ENABLED` | Age-based rules (sessions, sign-in codes, email records, audit values, checkout tokens, registration details at 90 days, never-verified accounts) |
| Clocks | `Subscription.service_ended_at`, `download_until`, `deletion_due_at` (`plan.py:22-24`, `services.py:393-408`) | Day 30 and day 90 after service end; day 30 only for a site that never paid (L75); none while payment collection is not live (R-4a). Paying is refused from `deletion_due_at` (`past_deletion_date`) |
| Audit | `OperatorAuditEntry`, `audit.record`, actions declared in `ACTIONS` | Immutable; `account_disposed`, `retention_run` |

Missing, the subject of this plan: **nothing acts on `deletion_due_at`**. No code finds an ended site whose date has passed, no human approves it, and nothing erases that one site's store data while the account stays (another site) or goes (no other site). The Terms and Privacy pages promise exactly that. No real ending is due before about February 2027, so this is off the launch path.

Two facts that shape the design:

1. Account disposal works per **account**. A site's date is per **site**, and an account may hold several sites. The per-app blank functions must work on a set of sites. They are refactored to take a site queryset (the account version stays as a one-line wrapper), which changes no behaviour and is pinned by the existing tests.
2. The code's rule is **anonymise in place, keep the row** (rows are `PROTECT`-linked, orders and the Subscriber's books must add up, a web address is never reissued). The executor follows that rule and deletes only private files, sessions, tokens and sign-in code rows, as disposal already does.

## 3. What the executor does to a site

The unit is one **ended site**. "Erase" below means the account disposal's own operations, applied to that site's rows. Nothing here is a new rule: each line cites the function it reuses. Where a table is marked **new**, the existing disposal does not cover it today.

| Table or file | Erase: delete or blank | Kept | Reuse |
|---|---|---|---|
| `sites.Site` | contact email and mobile, social links, display name (`Deleted site #<pk>`), `deleted_at` set | row, template, dates, public id | `site_retention.blank_sites_of` |
| `sites.Subdomain` | active name retired | the name stays taken, never reissued | same |
| `sites.SiteBusinessProfile` | business and registered name, TIN, DTI number, BIR RSN, address, complaints contact, privacy email; the BIR seal file deleted from the private store | row, shipping estimates | same |
| `sante.SanteSiteSettings` | sponsor ID | row | `blank_settings_of` |
| `payments.SitePaymentAccount` | account name and number, every account marked removed, each **QR image deleted** from the private store | row, kind, bank name, dates | `blank_payments_of` |
| `members.Member` | name, delivery address; email and mobile replaced by unusable placeholders | row and public id (orders point at it) | `blank_members_of` |
| `members.MemberSession`, `MemberOtpChallenge` | deleted | | same |
| `members.MemberRecoveryCheck`, `MemberContactAudit`, `MemberSecurityHold` | nothing (masked values only) | all | same |
| `pack.PackOrder` | buyer name, delivery address | row, number, status, frozen price | `blank_packs_of` |
| `pack.PackProofSubmission` | sender name, account number, reference, visitor address blanked; **the proof files are kept in the private store** until task z8uq9mbu0p ships (L67: the store-data export must carry proof images first; Manager decision 2026-11-13) | row, amount, dates, **proof file** | same, with the file-delete step skipped for pack proofs |
| `pack.CheckoutToken` | all deleted | | same |
| `pack.SanteRegistration` | date of birth, two Santé usernames | row, status, events | same |
| `pack.PackProofDecision`, `PackOrderEvent`, `PackAgreementAcceptance`, `PackEnrollmentAuthorization`, `SanteRegistrationEvent` | nothing | all (append-only evidence) | |
| `shop.ShopOrder`, `ShopProofSubmission` | receiver name, mobile, email, delivery address, reference, account name, visitor address; **proof files deleted** | rows, lines, totals, events | `blank_shop_of` |
| `meta_ads.MetaSettings`, `MetaOrderContext` | token and test event code blanked; order contexts deleted | `MetaDelivery` (no personal data) | `blank_meta_of` |
| `inbox` rows with `owner_kind="site"` (conversations and their messages, contacts, groups, bulk batches and recipients) | deleted **(new)** | the do-not-text list (kept until the person asks, NPC) | one new function; today neither disposal nor a rule touches site-owned inbox rows |
| `notifications.SmsMessage` (has `site`) | nothing | rows follow their own 1-year rule | |
| `notifications.EmailMessage` (store emails to members, to the contact address) | nothing | rows follow their own 2-year rule `email_messages` | see decision D11 |
| `catalog.*` | nothing | shared, belongs to no account | |

Kept whole, by the retention floor (L76, parent z8uq9mb226) and the published pages ("Billing and tax records are kept at least 5 years"). **The executor writes to none of these tables and deletes none of these files:**

- `billing.Subscription` (and its `deletion_due_at`), `SubscriptionTransition`, `BillingCycle`, `BankTransferPayment` **with its proof file and the sender details the Subscriber typed**, `BankTransferEvent`, `BillingNotice`, `RenewalConsent`, `UsageEntry`, `ChargeLine`, `UsageEvent`;
- `statements.IssuerProfile`, `BillingProfile`, `StatementNumberSeries`, `Statement`, `StatementLine`, `StatementDelivery`;
- `invoices.*` including `InvoiceScan` files;
- `ledger.*`;
- `operators.OperatorAuditEntry` (the personal values in it are blanked by their own 1-year rule, nothing else);
- `accounts.AccountAuditEvent` (its own 1-year and 2-year rules).

A test (section 9) fails the build if the executor touches any table or file in this list. The floor's own functions `evidence_retain_until` and `OperatorFilingRecord` (parent task) are **not built** (grep finds neither) and are not needed here: the executor never deletes a floor row, so it cannot breach the floor. The sweep that removes floor records after their 5 years is a separate, later task; the earliest such record is not 5 years old before 2031.

### The account step

After the sites of an approved item are erased, the executor looks at the account:

- If the account has **another site that is not erased** (active, in trial, or ended and not yet due), the account is untouched. The sign-in, the sessions, the linked sign-in providers, the mobile number and the other site stay.
- If **every** site of the account is erased, the account is disposed of with the existing `dispose_account(rule="site_erasure")`: placeholder email, no name, sessions, sign-in links and mobile deleted, policy-acceptance address blanked, send records of its address deleted. This is the Terms line "and your account data if you have no other website".
- Existing refusals stay: an account that owns the Santé catalog owner site is not disposed of (`disposal_refusals`); the item records `account_kept: catalog_owner` and the site is still erased.
- `dispose_account` needs `RETENTION_ENABLED` as today. A batch that would dispose of an account with the switch off is refused at the dry run, not half-done.

## 4. Who is eligible

A site is eligible when **all** hold at the moment of the dry run and again, under the lock, at the moment of erasure:

1. It has a `Subscription` that is not `billing_exempt` (the Santé owner site and any exempt site are never listed).
2. The state is one of `ENDED_STATES` (suspended, cancelled after service end, cancelled with no payment method) and `service_ended_at` is set. A cancellation inside its paid term has `now < service_ended_at`: never eligible.
3. `deletion_due_at` is set and `now >= deletion_due_at`. A site whose dates are not started (never paid, collection not live) has none and is never listed.
4. `now >= download_until` is implied by 3 (30 days always precedes 90 or equals 30).
5. No `BankTransferPayment` is `submitted` (a held payment): refused, reason `payment_under_review`, the same refusal `prepare_disposal` makes.
6. No `ChargeLine` is `open`. A suspended site's open lines are written off at its deletion date by the existing `write_off_unreturned` (R-4c, `apps/billing/usage.py:290`); the executor runs it first for that subscription, then re-checks. A cancelled site with an open line is refused, reason `unpaid_usage`, for an operator decision (the Billing Supervisor's tool for that is `usage_dismiss`).
7. No hold (section 5).
8. The site is not already erased (the item's `erased` status, section 6).
9. The Subscriber has not paid since: a paid reactivation clears `deletion_due_at` (`_clear_service_end`), so the site drops out. Payment is refused from the date, so there is no race after it.

Eligibility reads `Subscription.deletion_due_at`; it does not recompute the 90 or 30 days, so the rule stays in one place (`plan.py`).

## 5. Holds

A hold stops erasure of a site for a reason a human sets (a legal request, a fraud or dispute review, a regulator's request). There is no hold column today: `Site.operator_hold` exists but means "the site is not served while staff look" and has no setter in the application.

New table `ErasureHold` (migration `lifecycle.0001`, section 8): `site` (FK, PROTECT), `reason_code` (a short list: `legal`, `fraud`, `dispute`, `regulator`, `other`), `reference` (a number or ticket reference, never a name or address, the same rule as `--request-ref`), `placed_by`, `placed_at`, `released_by`, `released_at`. At most one open hold per site (partial unique constraint). Placing and releasing are HQ staff actions, each writing an audit entry (`erasure_hold_set`, `erasure_hold_released`).

A held site is shown in the dry run under "held" with its reason code, never erased, and keeps its dates, so it becomes eligible the day the hold is released. A hold placed after approval and before execution stops that item: the executor reads holds under the lock (section 6).

Billing records are unaffected by a hold: they were never in the erase set.

## 6. How a human approves it

No Django admin screen and no autonomous run. One staff account exists (Coach Ed's, ruling 2026-11-13), so there is no second person; the control is **a frozen list, a typed confirmation, and an audit trail**, not four eyes.

### 6.1 The dry run (reads only)

- `manage.py erasure_dry_run [--as-of DATE]` and the HQ staff route `lifecycle/erasure/list/`. It runs the section 4 test over every subscription and returns, per eligible site: site public id, template, owner account public id, `service_ended_at`, `deletion_due_at`, days overdue, count per table that would be erased, number of private files that would be deleted (never a key), whether the account would be disposed of (no other live site) or kept (and why), and the section 3 account refusals. Not-eligible sites that are near (due within 14 days) appear in a second list with the date. Held and refused sites appear in a third, each with a reason code.
- It **writes nothing**: no row, no audit entry, no lock held after the read. It works with the switch off (like `run_retention --dry-run`). Its output holds ids, dates and counts only, never a name, email, number or address.
- A test counts every row of every table before and after and fails on any difference.

### 6.2 Proposing a batch

HQ route `lifecycle/erasure/propose/` (command `erasure_propose`): the operator picks sites from the dry-run list (a batch is 1 to 25 sites, **choice**) and the executor writes an `ErasureBatch` (`proposed`) with one `ErasureItem` per site, each snapshotting the site, its subscription, its `deletion_due_at`, the planned counts and a digest of the whole list. Proposing needs the switch `SITE_ERASURE_ENABLED`. It deletes nothing. A proposal expires after 7 days (choice); an expired one is cancelled and the list is made again, so an old list never runs against changed data.

### 6.3 Approving

HQ route `lifecycle/erasure/approve/`. The staff member sees the frozen list and its counts and types the confirmation: the batch id and the number of sites (for example `erase batch ebt_x9 7 sites`). The route refuses anything else. Approval can drop individual items (status `excluded`) but cannot add one. It records `approved_by`, `approved_at`, a reason of up to 300 characters, and the list digest, and writes the audit entry `erasure_batch_approved`. The reason is a note, not a personal value; no email, name or number is accepted in it (the same `--request-ref` check).

Per-site approval is the same flow with a batch of one. Decision D2 sets the rule for the first batch in each environment.

### 6.4 Running

An approved batch runs by `manage.py erasure_run BATCH` and by the Celery task `lifecycle.run_approved` (beat, every 5 minutes, takes only approved batches; with no approved batch it does nothing and reads no data table). Both need `SITE_ERASURE_ENABLED`; the account step also needs `RETENTION_ENABLED` (section 3).

Per item, in its own transaction, lock order as everywhere (Account, Onboarding, Subscription), plus the site row:

1. Lock; re-read section 4 and section 5 under the lock. If the site no longer qualifies (paid again, date changed, a hold placed, a proof submitted, an open charge line) the item becomes `skipped` with a reason code and **nothing is erased**.
2. Delete the private files of the site (BIR seal, payment QR images, pack and shop proofs). A file already gone is not an error; a file that cannot be deleted raises `StoreError`, the item's transaction rolls back and the item becomes `failed` (`store_unavailable`), so no row is left pointing at a file that was removed while its personal fields were not blanked. (This is the order disposal uses today.)
3. Blank and delete the section 3 rows.
4. Mark the item `erased` with `erased_at` and the counts.
5. If the account has no other live site, call `dispose_account` for it in the same transaction (rule `site_erasure`).
6. Write the audit entry `site_data_erased` in the same transaction as the erasure (section 7).

A failed item stays `failed`; `erasure_run BATCH` (or the next task tick) retries failed items of an approved batch, up to 5 attempts (choice), after which the batch shows `needs_attention` in the HQ list. The approval covers the retries because the list is the same. A batch whose items are all erased, skipped or excluded becomes `done`.

### 6.5 Idempotence

- `ErasureItem` has a partial unique constraint on `site` where `status = 'erased'`: a site can be erased once, by construction. That constraint is the marker; no column is added to `Site` or `Subscription`.
- Every blank step is "set to empty" or "delete the rows", so running it again changes nothing and counts zero; every private-file delete accepts "already gone".
- A second run of a done batch, a duplicated Celery tick, or two runs racing on one batch are no-ops: the second finds the item `erased` or waits on the site lock and then finds it `erased`.
- `dispose_account` already answers "already disposed of".
- A test runs each batch twice and compares row counts and audit entries.

### 6.6 Production

`SITE_ERASURE_ENABLED` is unset everywhere. Nothing runs in an environment until Coach Ed's word names that environment and the switch (section 12, D9). Production is never covered by an earlier approval, matching `CLAUDE.md` and the retention runbook.

## 7. Audit

Declared in `apps/operators/audit.py` first (a name not declared is refused by `record()`), all `KEPT` (no personal value in any of them):

| Action | When | Detail |
|---|---|---|
| `erasure_batch_proposed` | proposal | batch id, number of items, list digest |
| `erasure_batch_approved` | approval | batch id, items kept and excluded, digest, reason |
| `erasure_batch_cancelled` | cancel or expiry | batch id, reason |
| `site_data_erased` | **one per site**, in the same transaction as the erasure | site public id, batch id, `deletion_due_at`, count per table, number of files deleted, the **names** of the blanked fields (`BLANKED` dicts of the apps), whether the account was disposed of |
| `site_erasure_skipped` | an item stopped at execution | site public id, reason code |
| `erasure_hold_set`, `erasure_hold_released` | HQ | site public id, reason code, reference |
| `account_disposed` | existing, rule `site_erasure` | unchanged shape |

Never a name, email, number, address, key or file name. The operator is the approving staff user (the executor writes `operator=None` for the Celery run and names the approver in the detail, as the retention run does for the system, **choice**). The audit rows are immutable and outlive the data they describe, so a restore from backup can be checked against them (section 10).

## 8. Migration, switches, settings

**One migration, in a new app `apps/lifecycle`: `lifecycle.0001_initial`. It needs Coach Ed's separate approval (decision D8).** It adds three tables and changes no existing table:

| Table | Columns | Notes |
|---|---|---|
| `ErasureBatch` | `public_id` (`ebt_`), `status` (`proposed`, `approved`, `running`, `done`, `needs_attention`, `cancelled`), `proposed_by`, `proposed_at`, `approved_by`, `approved_at`, `approval_reason`, `list_digest`, `expires_at` | staff user FKs, `PROTECT` |
| `ErasureItem` | `batch` FK, `site` FK (`PROTECT`), `subscription_id` (snapshot), `deletion_due_at` (snapshot), `planned_counts` (JSON), `status` (`pending`, `excluded`, `skipped`, `erased`, `failed`), `reason_code`, `attempts`, `counts` (JSON), `erased_at`, `account_disposed` | partial unique (`site`) where `status='erased'`; check constraints on status values |
| `ErasureHold` | as section 5 | partial unique (`site`) where `released_at IS NULL` |

None of these holds a personal value (ids, dates, counts, codes). The tables are site-keyed, so they are registered in `negosyoonline_platform/isolation.py` as the isolation test requires (the surplus-entry rule in `test_isolation.py`); the plan's tests include that. Each table's retention is "kept" with the audit trail (a batch is a record that data was erased).

Switches, **all default off, none turned on by this plan**:

| Variable | Gates |
|---|---|
| `SITE_ERASURE_ENABLED` | the propose, approve, hold and run routes and commands (404 or refusal while off), the Celery task, and every read of the lifecycle tables. The dry run works with it off, because it reads only the existing tables |
| `RETENTION_ENABLED` (exists) | the account step, as today |

Release order: the code is released with the switch off before the migration is applied (the same rule as the other switch-gated apps in `docs/runbooks/migrations.md`); the Supervisor runs `lifecycle.0001` on staging under the standing approval for plan-specified migrations (2026-11-11) once this plan is approved. Production is never covered. The runbook (section 10) is updated in the same PR.

No new Railway variable other than the switch. No new dependency.

## 9. Tests

All on SQLite by default, and on PostgreSQL where the partial unique constraints and triggers matter (the CI matrix already runs both).

- **Refactor safety.** The existing disposal tests for sites, payments, members, packs, shop, meta and Santé pass unchanged after the functions take a site queryset; one new test shows the account wrapper and the site function give the same counts.
- **Eligibility (section 4).** Parametrised: each state in and out of `ENDED_STATES`; before, at and after `deletion_due_at`; never-paid with and without collection live; billing-exempt; paid-term-running; `submitted` proof; open and written-off charge lines (suspended site: written off first, then eligible); reactivated by payment (dates cleared); hold open and released; already erased.
- **Dry run never mutates.** A snapshot of every table's row count and of the audit log before and after; no lock left; output contains no personal value (a test plants a distinctive name, email and number and asserts none appears).
- **Approval.** Proposal requires the switch; the confirmation must match; an item cannot be added after proposal; exclusion works; an expired proposal cannot be approved; a cancelled one cannot run; running an unapproved batch refuses.
- **Per-table erase.** For each table in section 3: personal fields blank, kept fields unchanged, row counts unchanged (anonymise in place), private files deleted (fake store records the keys), the files of **another** site of the same account untouched.
- **Kept list.** A test fails if the run changes any row or file of `billing`, `statements`, `invoices`, `ledger`, `OperatorAuditEntry` (apart from new rows), `AccountAuditEvent`, or deletes any key under `billing/proofs/` or the invoice scan prefix.
- **Multi-site account.** Account with two sites, one due: only that site erased, the account, its sessions and the other site untouched. Both erased in one batch: the account is disposed of. Second site not yet due: the account is kept. Catalog owner account: site erased, account kept, `account_kept: catalog_owner`.
- **Idempotence.** Run a batch twice; two concurrent runs (two transactions, PostgreSQL) leave one `erased` item per site and one `site_data_erased` per site; a duplicated Celery tick is a no-op; the partial unique constraint refuses a second `erased` row.
- **Failure.** A store that fails on the third file: the item is `failed`, the site's personal fields are **not** blanked, no audit entry, no `erased` row; a later run with the store fixed erases it; a file already gone counts as success; five failures set `needs_attention`.
- **Race.** A payment proof submitted, a hold placed, or a payment confirmed between approval and execution: the item is `skipped`, nothing is erased.
- **Audit.** One `site_data_erased` per erased site in the same transaction (rolled back with it), `KEPT`, no personal value, names of fields only; the audit action names are all declared.
- **Switch.** With `SITE_ERASURE_ENABLED` unset: every route answers 404, the Celery task returns at once and reads no lifecycle table, the dry run still works. With `RETENTION_ENABLED` unset: the account step is refused at the dry run and at the run.
- **Export.** `export_account` still works for an account whose site is inside its 30 days and on the day of the batch, and for an account with another live site after one site was erased; it works while the switch is off. (The support export is by hand and untouched.)
- **Isolation.** `test_isolation.py` accepts the three tables.
- Repository checks: `uv run python manage.py check --deploy`, `makemigrations --check`, the full suite.

Local rehearsal for the Supervisor, on a throwaway local database, before any staging run: create two accounts with sites, advance the clock past `deletion_due_at`, dry run, propose, approve, run, read the audit entries.

## 10. Runbooks, export, restore

- `docs/runbooks/retention.md` gains "An ended site's data" (the table of section 3, the procedure of section 6, the refusals), and the line "No billing row is deleted or blanked" stays true. `docs/runbooks/support-and-exit.md` section 5 ("The windows") gains the approval step and the weekly review (decision D2). `docs/runbooks/migrations.md` records `lifecycle.0001` when it is run.
- **Support export stays available and access-controlled**: it is the existing by-hand `export_account` (Subscriber, registered email, request reference, `account_exported` audit entry). The executor never writes, moves or deletes an export. Before approving a batch the operator checks the mailbox for an open export or deletion request about any listed site (the support mailbox thread is the record, ruling relayed 2026-11-13; a machine cannot check it), and the HQ confirmation line says so. A site with an open request is held (section 5) or excluded.
- **Restore.** Erasure cannot be undone from the application. Backups hold the data until they expire: the monthly volume backup is kept 89 days (`docs/runbooks/backups-and-restore.md`), so erased data persists in backups for up to about 89 days after the run. That is a disclosed limit, not a bug; the Privacy wording about backups belongs to HQ. After a restore, data erased after the restore point comes back; the runbook adds a step: read the `site_data_erased` and `account_disposed` entries kept outside the database (the audit log is in the restored database too and is older than the restore point, so the list comes from the support mailbox and the PR/ClickUp record of each batch) and erase those sites again. An automatic "re-apply" is not built (**choice**; revisit if a restore ever happens).
- Sentry and logs carry counts and codes only (the existing rule).

## 11. Choices made (reversible, none needs Coach Ed)

- C1: anonymise in place (section 2), the existing rule. Rows are never removed.
- C2: the unit is the site; the account step follows only when no other live site remains.
- C3: a batch is 1 to 25 sites; a proposal expires in 7 days; an item retries up to 5 times.
- C4: the dry run writes nothing, not even an audit entry (as `run_retention --dry-run`).
- C5: no new email to the Subscriber. The existing billing notices already carry the last date to pay (`notices.py`), and the G4 owner item O2 (a general service-end email) is owned by G4-R, not by this task.
- C6: the executor refuses (never overrides) while a proof is under review or a charge line is open on a cancelled site.
- C7: `site_data_erased` is written with `operator=None` for the Celery run and names the approving staff user in the detail.
- C8: no daily notification; the HQ list shows the count of due and near-due sites. The first date is about February 2027.
- C9: the executor and its routes live in `apps/lifecycle` (new), calling the existing app functions, so no table of another app changes.

## 12. Owner decisions

Each has one recommendation, the risk and the exact words that approve it. The Manager may decide D1, D5 and D10 inside the rulings (routine, no change of public promise); D2 to D4, D6 to D9 and D11 touch privacy, legal wording or a migration and go to Coach Ed. Approving the plan as a whole is one sentence (below the table).

| # | Decision | Recommendation | Risk | Approving words |
|---|---|---|---|---|
| D1 | Erase by anonymising in place, not by removing rows | Yes. It is what the Terms say ("deletes or anonymises"), what disposal does today, and what `PROTECT` foreign keys and the Subscriber's books need | Personal fields are gone, but empty rows and placeholders remain; an auditor sees that a site existed | "Erase by anonymising in place." |
| D2 | Approval unit and cadence | Per batch of 1 to 25 sites, Coach Ed approves in HQ with the typed confirmation; the **first batch in each environment is one site**; he reviews the due list weekly, so the delay after day 90 is at most about 7 days. The Terms say "at 90 calendar days", so an approval delay is a small gap between promise and practice | A site stays 1 to 7 days past its date; an approver who confirms without reading | "Approve in batches in HQ, first batch of one site, weekly review." |
| D3 | How a legal or fraud hold is recorded | The new `ErasureHold` table (section 5). Alternative: reuse `Site.operator_hold`, with no migration, but that also turns the site off and has no setter | A forgotten hold keeps data past its date; a table is more to build | "Use the ErasureHold table." |
| D4 | Refuse erasure while a proof is under review or a charge line is open | Yes: a held payment or an open charge line blocks the item until an operator decides it | A site stays beyond its date while the money question is open; Terms already say billing records are kept | "Refuse while a payment is held or usage is open." |
| D5 | Keep the Subscriber's `BillingProfile` (company, billing email, address, TIN) and the statements' bill-to snapshot until the 5-year floor | Yes, as written in the parent task and L76. It is the Subscriber's own billing identity on records the law makes us keep | Personal details outlive the site by 5 years; the Privacy page already says so | "Keep billing profile and statements for 5 years." |
| D6 | Keep the Subscriber's own bank-transfer proofs (the file, the sender's name and account number) for 5 years | Yes. `docs/runbooks/billing.md` already says a proof is a billing record kept 5 years; the Pack buyers' proofs are different and are deleted | Bank details kept 5 years in a private store; access is staff-only and logged (`billing_proof_viewed`). Nothing deletes them at year 5 until a later task | "Keep billing proofs 5 years." |
| D7 | Dispose of the account in the same approved batch when its last site is erased | Yes, shown in the list as "account will be disposed of", so the approver sees it. Alternative: a second approval per account | Irreversible; a person who still wanted the account loses it (paying is refused from the date anyway) | "Dispose of the account with its last site." |
| D8 | Migration `lifecycle.0001_initial` (new app, three tables, no existing table changed) | Approve, and run on staging by the Supervisor under the 2026-11-11 standing approval once the plan is approved. Production: separately, later | A new app to maintain; the tables hold ids, dates and counts only | "Approved: migration lifecycle.0001_initial, run on staging." |
| D9 | Switch `SITE_ERASURE_ENABLED` default off, turned on per environment only with his word | Yes. Not on staging before a rehearsal on a local database; not on staging data that holds real members until the first batch is reviewed; production never by this approval | A switch left on in a shared environment lets approved batches run | "Switch stays off; I will name each environment." |
| D10 | Site-owned SMS inbox rows are erased with the site (new function), the do-not-text list kept | Yes. Neither disposal nor a rule touches site-owned inbox rows today, and a Subscriber's customer texts are personal data of the site | The do-not-text list keeps a number the person asked us not to text; that is the existing NPC position | "Erase site inbox rows, keep the do-not-text list." |
| D11 | Store emails already written (members, contact address) | Leave them to the existing 2-year `email_messages` rule. Alternative: delete by site at erasure (needs the member's address before it is blanked, more code) | Up to 2 years of send records with a buyer's address after the site is erased | "Leave email records to the 2-year rule." |

**Manager decisions, 2026-11-13:** D1, D5 and D10 are decided as recommended. The build is **parked until after launch** (nothing is due before about February 2027). D2 to D4, D6 to D9 and D11 remain for Coach Ed.

One sentence for the whole plan: **"Approved: the site deletion executor plan z8uq9mb22v as written (docs/superpowers/plans/2026-11-13-site-deletion-executor-z8uq9mb22v.md), decisions D1 to D11 as recommended, including migration lifecycle.0001_initial."** This approves the build and the staging migration. It approves no switch, no run in any environment, and nothing in production.

Each **batch** is approved at run time in HQ: the typed confirmation `erase batch <id> <n> sites` after reading the listed counts. That is the only approval that erases data.

## 13. Pull requests

CI is capped; the release train opens one combined PR per repository per round. The work is two deployable backend slices and one frontend, all dark behind `SITE_ERASURE_ENABLED`:

1. **Backend PR 1, read-only.** Refactor the blank functions to take a site queryset (account wrappers kept), the eligibility function, `erasure_dry_run`, the read-only HQ list route, the lifecycle app with `lifecycle.0001` and the three models (no route writes them yet), the tests for refactor safety, eligibility, dry-run immutability, the kept-list guard and isolation. **Nothing in this PR can delete.** It can ride a release round with other branches.
2. **Backend PR 2, the executor.** Propose, approve, hold and run routes and commands, the Celery task, the account step, the audit actions, the new inbox function, the runbook changes, and the tests of sections 6 to 9. This is the PR that deletes; it follows PR 1 in the same or the next round, after the Supervisor's local rehearsal (section 9) and a read-through by the Manager of the erase functions.
3. **Frontend PR, HQ "Lifecycle" page** (new, under Finance or its own menu, in the HQ staff app; the frontend repository's own plan): the dry-run list, propose, the typed approval, holds, the batch status. It calls the staff routes of PR 2 and needs no new backend. Count: **3 PRs, 2 backend, 1 frontend.** If the Manager prefers fewer CI runs, PRs 1 and 2 can be one backend PR; that costs review clarity (the part that deletes sits beside the part that cannot), so the recommendation is two.

Order: PR 1, the staging migration (Supervisor, after approval), the local rehearsal, PR 2, the frontend PR, a staging rehearsal on a test account named as a test (no real member data; staging is treated as live), then waiting. No site is due before about February 2027. Nothing is scheduled for production.

## 14. Not decided here and not built

- The sweep that removes billing records after their 5 years (the retention floor's `evidence_retain_until`, `OperatorFilingRecord`; the parent task owns it; needs Finance's constants). Earliest real need: 2031.
- The public wording of Data Deletion, Terms and Privacy (HQ and the Manager; the Billing Supervisor's note on z8uq9mb22t).
- The buyer portal purge and 30-day export (G4 O1, Coach Ed's, z8uq9mbk0m).
- Backups: only the disclosed 89-day limit (section 10).
- A person's individual erasure request for data of a site still in service: that is `dispose_account` and `sms_inbox_delete`, unchanged.
- The Pack buyers' proof files: deleted only after z8uq9mbu0p (store-data export with proof images) ships (L67; Manager decision 2026-11-13). Until then the executor blanks the proof fields and keeps the files; the file-delete step for pack proofs is switched in by that task. Section 6.4 step 2 and the files tests apply to the other files meanwhile.
