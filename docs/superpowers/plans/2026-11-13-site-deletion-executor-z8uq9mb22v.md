# Site deletion executor: human-approved erasure of an ended site's store data (plan)

Status: **plan only, for the Manager's review and Coach Ed's approval.** No code, migration, variable, switch, test or staging action is in this branch. It touches deleting data, so nothing is built until the owner decisions in section 12 are answered.

ClickUp task: z8uq9mb22v (https://app.clickup.com/t/z8uq9mb22v), P10.3 "Build human-approved data lifecycle". Parent z8uq9mb226 (Billing Phase 10). Design and policy tasks: z8uq9mb22u (P10.2), z8uq9mb22t (P10.1). Supervisor: BILLING, main task z8uq9m8h46. Read against `origin/main` at `02dde96`; refreshed 2026-11-13 against `d229a28` (see "Changes since 78581f5" next).

## Changes since 78581f5 (2026-11-13, refreshed against `origin/main` d229a28)

Every item was checked in the code, not copied from the brief. Decisions D1 to D11 and their recommendations are unchanged; D2 to D4 gain a note. Five new questions, D12 to D16, are in section 12.

1. **The "parked until February 2027" premise is wrong.** The first real `deletion_due_at` is about **2026-12-24**: a site whose trial ended without payment is deleted 30 days after its service end (`DELETION_NEVER_PAID`, `apps/billing/plan.py:24`; `_dates_after`, `apps/billing/services.py:434`), and two staging trials end 2026-11-24 and 2026-11-25 (Manila). Dates start only while `plan.collection_live()` (`plan.py:148`, R-4a). The Manager's 2026-11-13 decision to park the build until after launch needs to be re-made against this date (section 13 sets the schedule).
2. **BP-3 (PR #152) now purges Pack buyer data at the same deletion point, without approval.** `apps/pack/purge.py` (task `pack.purge`, hourly, `PACK_PURGE_ENABLED`) clears Pack buyer fields at `billing.deletion_point` (`apps/billing/services.py:334`). The executor must not repeat or fight it. Section 3a sets who owns each Pack table and the order. Consequence: the executor does **not** call `blank_packs_of` for a site; it calls `purge.purge_site`. This replaces the earlier "flag in the pack function" for proof files with the purge's own gate, `PROOF_EXPORT_READY_FROM` / `files_deletable` (`apps/pack/purge.py:34,53`).
3. **The account step contradicts the proof-file rule (found in the code).** `dispose_account` calls `blank_packs_of(account)`, which deletes every pack proof file (`apps/pack/retention.py:78-81`), ignoring `PROOF_EXPORT_READY_FROM`. The 78581f5 plan kept proof files for the site step but its account step would have deleted them. Question D14.
4. **G4b (PR #153) is now a promise the executor must keep exactly.** `apps/billing/notices.py:212-216` (`_export_notice`) tells the owner that after `deletion_due_at` we delete or anonymise the store data, including "the receiving-account details you saved for your store (account name, account number and QR code)". Section 3b maps each word to a step and lists the gaps: the receiving-account blank is **switched off by `PAYMENT_METHODS_ENABLED`** (D13), a suspended site gets no such notice (D12), and the Pack purge ignores holds (D15).
5. **Names and routes fixed.** `prepare_disposal` is at `apps/billing/services.py:1485` (was 1444). `usage.write_off_unreturned(now)` (`apps/billing/usage.py:290`) takes only `now`, sweeps every suspended subscription, and is called today only by the billing sweep when usage is on (`services.py:1635`); the old text "runs it for that subscription" is corrected in section 4. The registry `negosyoonline_platform/isolation.py` **does not exist**; tenant isolation is a route test (`apps/sites/tests/test_isolation.py`, `routes_under_a_site`), and staff routes are plain `ROUTES` dicts merged in `apps/operators/staff_views.py:202`. Section 8 is corrected. `docs/runbooks/retention.md` already has a Pack purge section; section 10 adds to it.
6. **Rulings applied.** One staff account for months, admin@negosyoonline.com (rulings log comments 1100430000509990 and 510008 on z8uq9med6x): the approver in HQ is that one account, there is no second approver, and a staff-user feature is not built. No new Django admin screens; all staff actions are HQ routes. Staging holds real Santé member data from the first real member (comment 1100430000509745): no rehearsal on staging data with real members, a local database first (sections 6.6, 9, 13).
7. **Terms wording.** "At 90 calendar days" (Terms, as quoted by the Supervisor; the public page was not read) against an approval-driven run is the known gap of D2, unchanged. The G4b text says "After {date}", which approval delay does not break.
8. **PR count: 3 (2 backend, 1 frontend), unchanged.** New backend scope fits inside PR 1 and PR 2.

## 1. Sources

- z8uq9mb22v in full: entry gate (P10.1 and P10.2 accepted, plus a separate migration approval); scope "start with dry-run and explicit per-batch approval. Do not automate production deletion"; five acceptance lines (support export stays; eligible only after service end; dry run never mutates, approved batch idempotent and fully audited; retained records follow the retention matrix; no autonomous production deletion). It has no comments.
- z8uq9mb22u (P10.2, design, no comments): the list of what a design must hold (state model, data map, delete versus anonymise, operator roles, dry-run report, per-batch owner approval, immutable audit log, failure, retry and restore boundaries, export). **P10.2 was never written as a document**; this plan covers its list for the executor and is the design the gate asks for. It is not an "accepted" P10.2 until the owners accept it.
- z8uq9mb22t (P10.1): the 2026-11-02 ruling (a deletion request from a paid subscriber is a cancellation at the end of the paid term, no refund; the 30-day export window and 90-day clock start at service end). The BILLING Supervisor's comment of 2026-11-13 says the public Data Deletion wording does not yet follow that ruling and goes to the Manager separately. This plan does not change a public page.
- z8uq9mb226 (parent): the retention floor (5 years, L61, L62, r26), "No automatic production deletion of Billing records", and that the Pack day-90 purge belongs to the buyer portal task z8uq9mbk0m, not here.
- Comment 1100430000509626 on z8uq9mb383 (G4): O3 (L75, day 44 for a site that never paid, 90 days after service end for a paid one), O1 (purge, 30-day export, buyer portal before launch: Coach Ed's to decide, nothing built on it).
- The rulings log (z8uq9med6x), newest first. Rulings that bind this plan: no Django admin screens, operator work goes in HQ under Finance; billing and tax records kept 5 years (L76); launch payment is bank transfer; one staff account only (Coach Ed's); staging is treated as live data from the first real member; the Manager decides routine items and asks only for payments, accounting, privacy, legal terms, credentials and production; batches of ready branches go up as one PR; Legal wording stays minimal.
- Code, read on `origin/main`: `apps/billing/{models,plan,services,usage,tasks,proofs}.py`, `apps/accounts/retention.py`, `apps/{sites,sante,payments,members,pack,shop,meta_ads}/retention.py`, `apps/common/{retention,private_files,disposal}.py`, `apps/operators/audit.py`, `apps/statements/models.py`, `apps/invoices/models.py`, the models of every app. Runbooks: `docs/runbooks/{retention,support-and-exit,storage,migrations,backups-and-restore,billing}.md`.
- Added in the refresh: `apps/pack/{purge,buyer_notice,tasks,checks,models}.py` and `pack.0005_buyer_notice`; `apps/billing/notices.py` (`_export_notice`, `_export_notice_emails`); `config/settings.py` (`OWNER_NOTICES_ENABLED` 167, `PAYMENT_METHODS_ENABLED` 182, `PACK_PURGE_ENABLED` 212, beat entries `pack-purge`, `retention-daily`); `docs/runbooks/retention.md` (Pack purge section) and `docs/runbooks/billing.md`. ClickUp comments were not re-read; the ruling comment ids are as given in the Supervisor's brief.
- Not read: the public Terms and Privacy pages on staging (quoted by the Supervisor, taken as given), the frontend repository, `docs/runbooks/storage.md` beyond its first lines (it concerns the storage ledger, not deletion). Nothing was run against any environment.

A rule below with no source is marked **choice** (reversible; listed in section 11) or **decision** (section 12).

## 2. What exists and what is missing

Built (`origin/main`):

| Piece | Where | What it does |
|---|---|---|
| Account disposal | `dispose_account`, `apps/accounts/retention.py:110`; `manage.py dispose_account` | Blanks the account and **every** site of it, in place, keeps the rows; refuses while a paid term runs or a proof is under review (`billing.prepare_disposal`, `apps/billing/services.py:1485`) |
| Per-app blank functions | `blank_sites_of`, `blank_payments_of`, `blank_members_of`, `blank_packs_of`, `blank_shop_of`, `blank_meta_of`, `blank_settings_of`, all taking an **account**; **most return `{}` and do nothing while their feature switch is off** (`PAYMENT_METHODS_ENABLED`, `MEMBERS_ENABLED`, `PACK_ENABLED`, `SHOP_ENABLED`, `SANTE_SETTINGS_ENABLED`) | The anonymise rules; each deletes private files first and undoes the disposal if a file cannot be deleted |
| Daily retention | `retention.run`, `manage.py run_retention --dry-run`, `RETENTION_ENABLED`, `DOB_ERASE_ENABLED` | Age-based rules (sessions, sign-in codes, email records, audit values, checkout tokens, registration details at 90 days, never-verified accounts) |
| Clocks | `Subscription.service_ended_at`, `download_until`, `deletion_due_at` (`plan.py:22-24`, `services.py:425-445`) | Day 30 and day 90 after service end; day 30 only for a site that never paid (L75); none while payment collection is not live (R-4a). Paying is refused from `deletion_due_at` (`past_deletion_date`) |
| Pack buyer purge (BP-3) | `apps/pack/purge.py`, task `pack.purge`, hourly, `PACK_PURGE_ENABLED` and `PACK_ENABLED` | At `deletion_point`, without approval: Pack buyer fields, tokens, registration details, proof text fields; proof files only once `PROOF_EXPORT_READY_FROM` allows (it is `None`). Receiving accounts left out |
| Owner export notice (G4b) | `apps/billing/notices.py:_export_notice_emails`, `OWNER_NOTICES_ENABLED` | One email per service end (cancelled and cancelled-no-payment-method states only), within 48 hours of the dates starting |
| Audit | `OperatorAuditEntry`, `audit.record`, actions declared in `ACTIONS` | Immutable; `account_disposed`, `retention_run` |

Missing, the subject of this plan: **nothing acts on `deletion_due_at`**. No code finds an ended site whose date has passed, no human approves it, and nothing erases that one site's store data while the account stays (another site) or goes (no other site). The Terms and Privacy pages promise exactly that. **The first real date is about 2026-12-24**, not February 2027 (two staging trials end 2026-11-24 and 2026-11-25), so the build is on the path of the first deletion date.

Two facts that shape the design:

1. Account disposal works per **account**, and its feature-switch gates are a trap for an executor (see 3b). A site's date is per **site**, and an account may hold several sites. The per-app blank functions must work on a set of sites. They are refactored to take a site queryset (the account version stays as a one-line wrapper), which changes no behaviour and is pinned by the existing tests.
2. The code's rule is **anonymise in place, keep the row** (rows are `PROTECT`-linked, orders and the Subscriber's books must add up, a web address is never reissued). The executor follows that rule and deletes only private files, sessions, tokens and sign-in code rows, as disposal already does.

## 3. What the executor does to a site

The unit is one **ended site**. "Erase" below means the account disposal's own operations, applied to that site's rows. Nothing here is a new rule: each line cites the function it reuses. Where a table is marked **new**, the existing disposal does not cover it today.

| Table or file | Erase: delete or blank | Kept | Reuse |
|---|---|---|---|
| `sites.Site` | contact email and mobile, social links, display name (`Deleted site #<pk>`), `deleted_at` set | row, template, dates, public id | `site_retention.blank_sites_of` |
| `sites.Subdomain` | active name retired | the name stays taken, never reissued | same |
| `sites.SiteBusinessProfile` | business and registered name, TIN, DTI number, BIR RSN, address, complaints contact, privacy email; the BIR seal file deleted from the private store | row, shipping estimates | same |
| `sante.SanteSiteSettings` | sponsor ID | row | `blank_settings_of` |
| `payments.SitePaymentAccount` | account name and number, every account marked removed, each **QR image deleted** from the private store. **Done whether or not `PAYMENT_METHODS_ENABLED` is on** (D13): the executor reads this table directly, as `dispose_account` reads the mobile tables | row, kind, provider name, account type, dates | `blank_payments_of`, with the switch gate not applied for the executor |
| `members.Member` | name, delivery address (BP-3 clears the address first, section 3a); email and mobile replaced by unusable placeholders | row and public id (orders point at it) | `blank_members_of` |
| `members.MemberSession`, `MemberOtpChallenge` | deleted | | same |
| `members.MemberRecoveryCheck`, `MemberContactAudit`, `MemberSecurityHold` | nothing (masked values only) | all | same |
| `pack.PackOrder` | buyer name (`Deleted buyer`), delivery address | row, number, status, frozen price | **owned by the Pack purge**, `purge.purge_site` (section 3a) |
| `pack.PackProofSubmission` | sender name, account number, sender bank, reference, visitor address and the account snapshot (public id, account name, last four) blanked; **the proof files are kept in the private store** until task z8uq9mbu0p ships (L67: the store-data export must carry proof images first; Manager decision 2026-11-13) | row, amount, dates, bank name, **proof file** | **owned by the Pack purge**; its own gate `files_deletable` / `PROOF_EXPORT_READY_FROM` decides the files |
| `pack.CheckoutToken` | all deleted | | Pack purge |
| `pack.SanteRegistration` | date of birth, two Santé usernames, `details_purged_at` set | row, status, events | Pack purge |
| `pack.PackProofDecision`, `PackOrderEvent`, `PackAgreementAcceptance`, `PackEnrollmentAuthorization`, `pack.PackBuyerNotice` (new, `pack.0005`: ids, dates, counts, no address) | nothing | all (append-only evidence or records without a personal value) | |
| `pack.SanteRegistrationEvent` | note and a member's actor label, by the Pack purge only (the sanctioned append-only bypass) | rows | Pack purge |
| `shop.ShopOrder`, `ShopProofSubmission` | receiver name, mobile, email, delivery address, reference, account name, visitor address; **proof files deleted**; and, **new (C10)**, the snapshot `account_number_last4`, which `blank_shop_of` leaves behind (`apps/shop/retention.py:20,51`) | rows, lines, totals, events | `blank_shop_of`, plus the last-four blank |
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

### 3a. The Pack purge (BP-3) and the executor: who owns what

BP-3 runs **by itself** at the deletion point (task `pack.purge`, hourly, `PACK_PURGE_ENABLED`, off everywhere until Coach Ed says). The executor runs **after a human approves**, later. Both end at the same state, and both are plain assignment to empty values, so neither conflicts with the other and a second pass changes nothing. To keep one implementation of each rule:

| Table or field | Owner | Why |
|---|---|---|
| `SanteRegistration` (DOB, usernames), `SanteRegistrationEvent` (note, actor label), `PackOrder` (buyer name, address), `CheckoutToken`, `PackProofSubmission` text fields, `MemberSession` and `MemberContactAudit` visitor address, `Member.delivery_address`, pack proof **files** | **Pack purge** (`apps/pack/purge.py`) | Already built and approved (block B). The executor calls `purge.purge_site(site_id, now)`, which re-reads the point under the site lock and is idempotent. It does not call `blank_packs_of` for a site |
| `Member` name, email, mobile, session rows, sign-in code rows | **Executor** (`blank_members_of`, site form) | The purge leaves them "until the store-data deletion executor exists" (`docs/runbooks/retention.md`, Pack purge section) |
| `SitePaymentAccount` (receiving accounts) | **Executor** | BP-3 left them out (`apps/pack/purge.py` docstring) |
| Site, business profile, Santé settings, shop, Meta, site inbox | **Executor** | Not in the purge |

Order when both are on: the purge clears Pack buyer data in the hour after the deletion point; the executor runs on approval, days later, and its call to `purge_site` finds nothing to do and counts zero. With the purge **off** (its default) and the executor on, the approved run performs the Pack part itself through the same function, so the Pack data is cleared at approval time instead of the hour after the point. Both orders end the same; the purge needs no approval, the executor does. The purge runs for every ended site, **including one an operator holds** (D15).

Proof files: the purge's `files_deletable` is the single gate. While `PROOF_EXPORT_READY_FROM` is `None` the executor deletes no pack proof file either. The account step must respect it (D14).

### 3b. The G4b promise, word by word

`_export_notice` (`apps/billing/notices.py:212-216`) says: "After {date}, as our Terms say, we delete or anonymise the store data of {site}. This includes the receiving-account details you saved for your store (account name, account number and QR code). We do not delete what the law requires us to keep, or what we must hold for a dispute or a fraud check. Billing records are kept as required."

| Words | Delivered by | Gap |
|---|---|---|
| "delete or anonymise the store data of {site}" | the whole of section 3 | none, once the executor exists and a batch is approved |
| "receiving-account details ... account name, account number and QR code" | `SitePaymentAccount`: name and number blanked, QR image deleted from the private store, row marked removed, for every account of the site, removed ones included | **`blank_payments_of` answers `{}` while `PAYMENT_METHODS_ENABLED` is off** (`apps/payments/retention.py:35`). If that switch were off when a site is erased, the account number would stay. The executor reads the table directly (D13). The shop proof's `account_number_last4` stays under today's `blank_shop_of` (C10 fixes it); the Pack proof copies are blanked by the purge |
| "not ... what the law requires us to keep" | the kept list below | none |
| "or what we must hold for a dispute or a fraud check" | holds (section 5) and the refusals of D4 | holds stop the executor only; the Pack purge ignores them (D15) |
| "Billing records are kept as required" | the kept list below | none |
| (not said) | a **suspended** site gets only the "paused" email, which promises no deletion; the export notice goes to the two cancelled states only and only within 48 hours (`_export_notice_emails`, `apps/billing/notices.py:395-405`) | D12 |

### The account step

After the sites of an approved item are erased, the executor looks at the account:

- If the account has **another site that is not erased** (active, in trial, or ended and not yet due), the account is untouched. The sign-in, the sessions, the linked sign-in providers, the mobile number and the other site stay.
- If **every** site of the account is erased, the account is disposed of with the existing `dispose_account(rule="site_erasure")` (`apps/accounts/retention.py:110`), called with a new keyword that keeps pack proof files while `files_deletable` is false (D14): placeholder email, no name, sessions, sign-in links and mobile deleted, policy-acceptance address blanked, send records of its address deleted. This is the Terms line "and your account data if you have no other website".
- Existing refusals stay: an account that owns the Santé catalog owner site is not disposed of (`disposal_refusals`); the item records `account_kept: catalog_owner` and the site is still erased.
- `dispose_account` needs `RETENTION_ENABLED` as today. A batch that would dispose of an account with the switch off is refused at the dry run, not half-done.

## 4. Who is eligible

A site is eligible when **all** hold at the moment of the dry run and again, under the lock, at the moment of erasure:

1. It has a `Subscription` that is not `billing_exempt` (the Santé owner site and any exempt site are never listed).
2. The state is one of `ENDED_STATES` (suspended, cancelled after service end, cancelled with no payment method) and `service_ended_at` is set. A cancellation inside its paid term has `now < service_ended_at`: never eligible.
3. `deletion_due_at` is set and `now >= deletion_due_at`. A site whose dates are not started (never paid, collection not live) has none and is never listed.
4. `now >= download_until` is implied by 3 (30 days always precedes 90 or equals 30).
5. No `BankTransferPayment` is `submitted` (a held payment): refused, reason `payment_under_review`, the same refusal `prepare_disposal` makes.
6. No `ChargeLine` is `open`. A suspended site's open lines are written off at its deletion date by the existing `write_off_unreturned` (R-4c, `apps/billing/usage.py:290`); the executor calls it once before listing, then re-checks. `usage.write_off_unreturned(now)` takes only `now`, covers every due suspended subscription, and today runs only inside the billing sweep while usage is on (`apps/billing/services.py:1635`); usage is off at launch, so open lines are rare. A cancelled site with an open line is refused, reason `unpaid_usage`, for an operator decision (the Billing Supervisor's tool for that is `usage_dismiss`).
7. No hold (section 5).
8. The site is not already erased (the item's `erased` status, section 6).
9. The Subscriber has not paid since: a paid reactivation clears `deletion_due_at` (`_clear_service_end`), so the site drops out. Payment is refused from the date, so there is no race after it.

10. **A flag, not a condition (D12):** whether the service-end export notice was sent for this service end (a `BillingNotice` of code `export_notice`, key `export_notice:<subscription>:<service end timestamp>`). The dry run shows `notice_sent: yes` or `no`, and `no_notice_for_state` for a suspended site. It blocks nothing; the approver reads it.

Eligibility reads `Subscription.deletion_due_at`; it does not recompute the 90 or 30 days, so the rule stays in one place (`plan.py`).

## 5. Holds

A hold stops erasure of a site for a reason a human sets (a legal request, a fraud or dispute review, a regulator's request). There is no hold column today: `Site.operator_hold` exists but means "the site is not served while staff look" and has no setter in the application.

New table `ErasureHold` (migration `lifecycle.0001`, section 8): `site` (FK, PROTECT), `reason_code` (a short list: `legal`, `fraud`, `dispute`, `regulator`, `other`), `reference` (a number or ticket reference, never a name or address, the same rule as `--request-ref`), `placed_by`, `placed_at`, `released_by`, `released_at`. At most one open hold per site (partial unique constraint). Placing and releasing are HQ staff actions, each writing an audit entry (`erasure_hold_set`, `erasure_hold_released`).

A hold records the "dispute or fraud check" of the G4b promise. It stops the executor. It does **not** stop the Pack purge, which has no holds table and is held only by turning `PACK_PURGE_ENABLED` off for every site (`docs/runbooks/retention.md`); D15 asks that the purge consult `ErasureHold`. A held site is shown in the dry run under "held" with its reason code, never erased, and keeps its dates, so it becomes eligible the day the hold is released. A hold placed after approval and before execution stops that item: the executor reads holds under the lock (section 6).

Billing records are unaffected by a hold: they were never in the erase set.

## 6. How a human approves it

No Django admin screen and no autonomous run. One staff account exists for months, admin@negosyoonline.com (rulings log comments 1100430000509990 and 510008 on z8uq9med6x), so the approver in HQ is that one account, there is no second-approver design and no staff-user management; the control is **a frozen list, a typed confirmation, and an audit trail**, not four eyes.

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
2. Delete the private files of the site (BIR seal, payment QR images, shop proofs; **not** pack proofs, which the Pack purge keeps until `PROOF_EXPORT_READY_FROM` is set by z8uq9mbu0p, L67). The Pack part is a call to `purge.purge_site` (section 3a), made first: a Pack failure leaves the item `failed` with nothing else blanked. A file already gone is not an error; a file that cannot be deleted raises `StoreError`, the item's transaction rolls back and the item becomes `failed` (`store_unavailable`), so no row is left pointing at a file that was removed while its personal fields were not blanked. (This is the order disposal uses today.)
3. Blank and delete the section 3 rows, with the feature-switch gates of `blank_payments_of`, `blank_members_of`, `blank_shop_of`, `blank_meta_of` and `blank_settings_of` not applied (D13).
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

`SITE_ERASURE_ENABLED` is unset everywhere. Nothing runs in an environment until Coach Ed's word names that environment and the switch (section 12, D9). Production is never covered by an earlier approval, matching `CLAUDE.md` and the retention runbook. **Staging holds real Santé member data from the first real member** (comment 1100430000509745): no rehearsal runs on staging data with real members; every rehearsal runs on a throwaway local database, and the first run on staging is a batch of one site that Coach Ed names after reading its dry run.

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

None of these holds a personal value (ids, dates, counts, codes). The tables are staff-only: no subscriber route reads them, and the staff routes are `ROUTES` dicts like `apps/billing/staff.py:287`, merged in `apps/operators/staff_views.py:202`. (The earlier text named a registry `negosyoonline_platform/isolation.py`; it does not exist. The isolation tests are route tests under `{site_id}`, `apps/sites/tests/test_isolation.py`, and no subscriber route is added.) Each table's retention is "kept" with the audit trail (a batch is a record that data was erased).

Switches, **all default off, none turned on by this plan**:

| Variable | Gates |
|---|---|
| `SITE_ERASURE_ENABLED` | the propose, approve, hold and run routes and commands (404 or refusal while off), the Celery task, and every read of the lifecycle tables. The dry run works with it off, because it reads only the existing tables |
| `RETENTION_ENABLED` (exists) | the account step, as today |
| `PACK_PURGE_ENABLED` (exists, BP-3) | not read by the executor; the executor does the Pack part itself when it is off (section 3a) |
| `OWNER_NOTICES_ENABLED` (exists, G4b) | not read by the executor; it must be on **before** the first service end is announced for the promise to have been made (section 13) |

Release order: the code is released with the switch off before the migration is applied (the same rule as the other switch-gated apps in `docs/runbooks/migrations.md`); the Supervisor runs `lifecycle.0001` on staging under the standing approval for plan-specified migrations (2026-11-11) once this plan is approved. Production is never covered. The runbook (section 10) is updated in the same PR.

No new Railway variable other than the switch. No new dependency.

## 9. Tests

All on SQLite by default, and on PostgreSQL where the partial unique constraints and triggers matter (the CI matrix already runs both).

- **Refactor safety.** The existing disposal tests for sites, payments, members, packs, shop, meta and Santé pass unchanged after the functions take a site queryset; one new test shows the account wrapper and the site function give the same counts.
- **Eligibility (section 4).** Parametrised: each state in and out of `ENDED_STATES`; before, at and after `deletion_due_at`; never-paid with and without collection live; billing-exempt; paid-term-running; `submitted` proof; open and written-off charge lines (suspended site: written off first, then eligible); reactivated by payment (dates cleared); hold open and released; already erased.
- **Dry run never mutates.** A snapshot of every table's row count and of the audit log before and after; no lock left; output contains no personal value (a test plants a distinctive name, email and number and asserts none appears).
- **Approval.** Proposal requires the switch; the confirmation must match; an item cannot be added after proposal; exclusion works; an expired proposal cannot be approved; a cancelled one cannot run; running an unapproved batch refuses.
- **Per-table erase.** For each table in section 3: personal fields blank, kept fields unchanged, row counts unchanged (anonymise in place), private files deleted (fake store records the keys) for the seal, QR images and shop proofs, while every **pack proof file is kept**, the files of **another** site of the same account untouched.
- **Kept list.** A test fails if the run changes any row or file of `billing`, `statements`, `invoices`, `ledger`, `OperatorAuditEntry` (apart from new rows), `AccountAuditEvent`, or deletes any key under `billing/proofs/` or the invoice scan prefix.
- **Multi-site account.** Account with two sites, one due: only that site erased, the account, its sessions and the other site untouched. Both erased in one batch: the account is disposed of. Second site not yet due: the account is kept. Catalog owner account: site erased, account kept, `account_kept: catalog_owner`.
- **Idempotence.** Run a batch twice; two concurrent runs (two transactions, PostgreSQL) leave one `erased` item per site and one `site_data_erased` per site; a duplicated Celery tick is a no-op; the partial unique constraint refuses a second `erased` row.
- **Pack proofs (L67).** After a run every pack proof file is still in the fake store and its key is still on the row, while the sender fields are blank; the pack file-delete step is off by default and a test fails if it runs before z8uq9mbu0p turns it on; the `site_data_erased` file count excludes pack proofs.
- **Failure.** A store that fails on the third file: the item is `failed`, the site's personal fields are **not** blanked, no audit entry, no `erased` row; a later run with the store fixed erases it; a file already gone counts as success; five failures set `needs_attention`.
- **Race.** A payment proof submitted, a hold placed, or a payment confirmed between approval and execution: the item is `skipped`, nothing is erased.
- **Audit.** One `site_data_erased` per erased site in the same transaction (rolled back with it), `KEPT`, no personal value, names of fields only; the audit action names are all declared.
- **Switch.** With `SITE_ERASURE_ENABLED` unset: every route answers 404, the Celery task returns at once and reads no lifecycle table, the dry run still works. With `RETENTION_ENABLED` unset: the account step is refused at the dry run and at the run.
- **Export.** `export_account` still works for an account whose site is inside its 30 days and on the day of the batch, and for an account with another live site after one site was erased; it works while the switch is off. (The support export is by hand and untouched.)
- **Pack purge overlap (section 3a).** Purge on, then executor: the executor's Pack pass counts zero and the audit counts agree. Purge off, executor on: the executor clears the same fields. Both orders give identical rows. No pack proof file is deleted by either while `PROOF_EXPORT_READY_FROM` is `None`, the account step included.
- **G4b promise (section 3b).** With `PAYMENT_METHODS_ENABLED`, `MEMBERS_ENABLED`, `SHOP_ENABLED` and `SANTE_SETTINGS_ENABLED` each off in turn, an erased site has no receiving-account name or number and no QR key, the QR file is gone from the fake store, and `account_number_last4` of shop proofs is empty.
- **Hold and notice flag.** A held site is never erased; the dry run shows `notice_sent` for a cancelled site with and without a `BillingNotice`, and `no_notice_for_state` for a suspended one.
- **Isolation.** Staff routes answer 404 to a subscriber session and to a non-staff session; no subscriber route is added.
- Repository checks: `uv run python manage.py check --deploy`, `makemigrations --check`, the full suite.

Local rehearsal for the Supervisor, on a throwaway local database, before any staging run (no rehearsal on staging data with real members): create two accounts with sites, advance the clock past `deletion_due_at`, dry run, propose, approve, run, read the audit entries.

## 10. Runbooks, export, restore

- `docs/runbooks/retention.md` gains "An ended site's data" (next to its Pack purge section, whose line "Not covered: the Subscriber's receiving accounts" is updated to say the executor covers them) (the table of section 3, the procedure of section 6, the refusals), and the line "No billing row is deleted or blanked" stays true. `docs/runbooks/support-and-exit.md` section 5 ("The windows") gains the approval step and the weekly review (decision D2). `docs/runbooks/migrations.md` records `lifecycle.0001` when it is run.
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
- C8: no daily notification; the HQ list shows the count of due and near-due sites. The first date is about 2026-12-24.
- C10: the site erase also blanks `ShopProofSubmission.account_number_last4` (a fragment of the receiving-account number that `blank_shop_of` leaves), so the G4b promise "account number" is kept for every copy. Provider name stays.
- C9: the executor and its routes live in `apps/lifecycle` (new), calling the existing app functions, so no table of another app changes.

## 12. Owner decisions

Each has one recommendation, the risk and the exact words that approve it. The Manager may decide D1, D5 and D10 inside the rulings (routine, no change of public promise); D2 to D4, D6 to D9, D11 and D12 to D16 touch privacy, legal wording or a migration and go to Coach Ed. D12 to D16 are new in the refresh; each has one recommendation. Approving the plan as a whole is one sentence (below the table).

| # | Decision | Recommendation | Risk | Approving words |
|---|---|---|---|---|
| D1 | Erase by anonymising in place, not by removing rows | Yes. It is what the Terms say ("deletes or anonymises"), what disposal does today, and what `PROTECT` foreign keys and the Subscriber's books need | Personal fields are gone, but empty rows and placeholders remain; an auditor sees that a site existed | "Erase by anonymising in place." |
| D2 | Approval unit and cadence | Per batch of 1 to 25 sites, Coach Ed approves in HQ with the typed confirmation; the **first batch in each environment is one site**; he reviews the due list weekly, so the delay after day 90 is at most about 7 days. The Terms say "at 90 calendar days" (as quoted by the Supervisor), so an approval delay is a small gap between promise and practice. This is the known gap; the G4b email says "After {date}" and is not broken by it. The approver is the one staff account | A site stays 1 to 7 days past its date; an approver who confirms without reading | "Approve in batches in HQ, first batch of one site, weekly review." |
| D3 | How a legal or fraud hold is recorded | The new `ErasureHold` table (section 5). It matches the G4b words "a dispute or a fraud check". Alternative: reuse `Site.operator_hold`, with no migration, but that also turns the site off and has no setter | A forgotten hold keeps data past its date; a table is more to build | "Use the ErasureHold table." |
| D4 | Refuse erasure while a proof is under review or a charge line is open | Yes: a held payment or an open charge line blocks the item until an operator decides it | A site stays beyond its date while the money question is open; Terms already say billing records are kept | "Refuse while a payment is held or usage is open." |
| D5 | Keep the Subscriber's `BillingProfile` (company, billing email, address, TIN) and the statements' bill-to snapshot until the 5-year floor | Yes, as written in the parent task and L76. It is the Subscriber's own billing identity on records the law makes us keep | Personal details outlive the site by 5 years; the Privacy page already says so | "Keep billing profile and statements for 5 years." |
| D6 | Keep the Subscriber's own bank-transfer proofs (the file, the sender's name and account number) for 5 years | Yes. `docs/runbooks/billing.md` already says a proof is a billing record kept 5 years; the Pack buyers' proofs are different and are deleted | Bank details kept 5 years in a private store; access is staff-only and logged (`billing_proof_viewed`). Nothing deletes them at year 5 until a later task | "Keep billing proofs 5 years." |
| D7 | Dispose of the account in the same approved batch when its last site is erased | Yes, shown in the list as "account will be disposed of", so the approver sees it. Alternative: a second approval per account | Irreversible; a person who still wanted the account loses it (paying is refused from the date anyway) | "Dispose of the account with its last site." |
| D8 | Migration `lifecycle.0001_initial` (new app, three tables, no existing table changed) | Approve, and run on staging by the Supervisor under the 2026-11-11 standing approval once the plan is approved. Production: separately, later | A new app to maintain; the tables hold ids, dates and counts only | "Approved: migration lifecycle.0001_initial, run on staging." |
| D9 | Switch `SITE_ERASURE_ENABLED` default off, turned on per environment only with his word | Yes. Not on staging before a rehearsal on a local database; not on staging data that holds real members until the first batch is reviewed; production never by this approval | A switch left on in a shared environment lets approved batches run | "Switch stays off; I will name each environment." |
| D10 | Site-owned SMS inbox rows are erased with the site (new function), the do-not-text list kept | Yes. Neither disposal nor a rule touches site-owned inbox rows today, and a Subscriber's customer texts are personal data of the site | The do-not-text list keeps a number the person asked us not to text; that is the existing NPC position | "Erase site inbox rows, keep the do-not-text list." |
| D11 | Store emails already written (members, contact address) | Leave them to the existing 2-year `email_messages` rule. Alternative: delete by site at erasure (needs the member's address before it is blanked, more code) | Up to 2 years of send records with a buyer's address after the site is erased | "Leave email records to the 2-year rule." |
| D12 | A site whose owner was not sent the G4b service-end email: a **suspended** site gets only the "paused" email (no deletion promise), and a cancelled site gets the export notice only if `OWNER_NOTICES_ENABLED` was on within 48 hours of the dates starting (`apps/billing/notices.py:395-405`) | List it in the dry run with `notice_sent: no` (section 4, item 10) and let the one approver decide per item. Do not block: an owner who cannot be reached would otherwise never be erased, and the Terms promise the date. Alternative: block until notified, which needs a resend path that does not exist | A site erased whose owner was never told the export window | "Show notice status in the dry run; do not block." |
| D13 | Make the executor erase receiving accounts, members, shop, Meta and Santé settings whether or not their feature switch is on (`PAYMENT_METHODS_ENABLED` etc.). Today `blank_payments_of` and the others do nothing while off (`apps/payments/retention.py:35`) | Yes, read the tables directly, the way disposal reads the mobile tables ("never behind sms_on()", `apps/accounts/retention.py`). Without it the G4b sentence about account number and QR code could be untrue for a site erased with the switch off | The change touches the existing per-app functions (a keyword, default unchanged for account disposal) | "Erase receiving accounts regardless of feature switches." |
| D14 | Pack proof files in the account step: `dispose_account` deletes every pack proof file (`apps/pack/retention.py:78-81`), against L67 and `PROOF_EXPORT_READY_FROM` | Pass a keyword from the executor so the account step keeps pack proof files while `files_deletable` is false; a person's own disposal request is unchanged | Another small change in `apps/accounts/retention.py` and `apps/pack/retention.py`; if refused, the account step deletes pack proof files before the export carries images | "Keep pack proof files in the executor's account step." |
| D15 | Holds and the Pack purge: the purge ignores `ErasureHold`, so a held site's Pack buyers are still cleared at the date (`docs/runbooks/retention.md`: the only hold is turning the purge off for all sites) | Yes: `purge_site` returns without clearing a held site, in PR 2. Without it the "dispute or a fraud check" sentence is not true for Pack buyer data | Changes an approved BP-3 behaviour; the purge then depends on the lifecycle table | "The Pack purge honours ErasureHold." |
| D16 | The two staging trials ending 2026-11-24 and 2026-11-25 will reach `deletion_due_at` about 2026-12-24 (section 13). What happens if the executor is not live | They wait. Nothing deletes without approval, and the G4b text says "After {date}", not "on". The cost is the known gap of D2, not a new one. Recommended schedule in section 13 | A date passes with data kept; if the sites hold real member data, Coach Ed may prefer a manual run, which is not planned | "Dates may wait for approval; follow the section 13 schedule." |

**Manager decisions, 2026-11-13:** D1, D5 and D10 are decided as recommended. The build is **parked until after launch** (nothing is due before about February 2027). D2 to D4, D6 to D9 and D11 remain for Coach Ed, with D12 to D16 added by the refresh. The "parked until after launch" decision is superseded by the 2026-12-24 date and is for the Manager to re-make.

One sentence for the whole plan: **"Approved: the site deletion executor plan z8uq9mb22v as written (docs/superpowers/plans/2026-11-13-site-deletion-executor-z8uq9mb22v.md), decisions D1 to D16 as recommended, including migration lifecycle.0001_initial."** This approves the build and the staging migration. It approves no switch, no run in any environment, and nothing in production.

Each **batch** is approved at run time in HQ: the typed confirmation `erase batch <id> <n> sites` after reading the listed counts. That is the only approval that erases data.

## 13. Pull requests

CI is capped; the release train opens one combined PR per repository per round. The work is two deployable backend slices and one frontend, all dark behind `SITE_ERASURE_ENABLED`:

1. **Backend PR 1, read-only.** Refactor the blank functions to take a site queryset (account wrappers kept), the eligibility function, `erasure_dry_run`, the read-only HQ list route, the lifecycle app with `lifecycle.0001` and the three models (no route writes them yet), the tests for refactor safety, eligibility, dry-run immutability, the kept-list guard and isolation. **Nothing in this PR can delete.** It can ride a release round with other branches.
2. **Backend PR 2, the executor.** Propose, approve, hold and run routes and commands, the Celery task, the account step, the audit actions, the new inbox function, the runbook changes, and the tests of sections 6 to 9. This is the PR that deletes; it follows PR 1 in the same or the next round, after the Supervisor's local rehearsal (section 9) and a read-through by the Manager of the erase functions.
3. **Frontend PR, HQ "Lifecycle" page** (new, under Finance or its own menu, in the HQ staff app, one approver; the frontend repository's own plan): the dry-run list, propose, the typed approval, holds, the batch status. It calls the staff routes of PR 2 and needs no new backend. Count: **3 PRs, 2 backend, 1 frontend** (unchanged after the refresh; D13 to D15 add small edits inside PR 2). If the Manager prefers fewer CI runs, PRs 1 and 2 can be one backend PR; that costs review clarity (the part that deletes sits beside the part that cannot), so the recommendation is two.

### Schedule against the first deletion date

First `deletion_due_at`: about **2026-12-24** (trials ending 2026-11-24 and 2026-11-25, plus 30 days; real dates are read from the subscription rows). Order, with the latest sensible date for each:

1. **Before the first service end is announced (about 2026-11-24): `OWNER_NOTICES_ENABLED` on staging** (Coach Ed's approval of the wording is needed first, `docs/runbooks/billing.md`), so the G4b email, and with it the promise, goes out. It is sent only within 48 hours of the dates starting (`WINDOW`, `apps/billing/notices.py:29`); later, no email is ever sent for that site and D12's flag shows `notice_sent: no`. This is not part of this plan's PRs.
2. **Backend PR 1 merged about 2026-12-01**, then `lifecycle.0001` run on staging by the Supervisor under the 2026-11-11 standing approval once the plan is approved.
3. Local rehearsal (section 9), then **backend PR 2 merged about 2026-12-10**, then the frontend PR. Both backend PRs ship dark.
4. **By 2026-12-24: `SITE_ERASURE_ENABLED` on in the environment Coach Ed names**, and the weekly review begins (D2). The first batch is one site that Coach Ed names after reading its dry run.
5. If a date passes first, **nothing happens to that site**: the executor is off or unapproved, the site keeps its data and its dates, and it simply waits for approval. Nothing is lost, paying stays refused from the date, and the G4b sentence ("After {date}") still holds. The Pack purge, if its own switch is on, clears Pack buyer data on time regardless.

No deletion date therefore passes with the promise broken: the only way to break it is to erase a site whose receiving accounts are not blanked, and section 3b, D13 and the G4b test close that.

## 14. Not decided here and not built

- The sweep that removes billing records after their 5 years (the retention floor's `evidence_retain_until`, `OperatorFilingRecord`; the parent task owns it; needs Finance's constants). Earliest real need: 2031.
- The public wording of Data Deletion, Terms and Privacy (HQ and the Manager; the Billing Supervisor's note on z8uq9mb22t).
- The buyer portal purge (BP-3, built, off) and the 30-day buyer export (G4 O1, Coach Ed's, z8uq9mbk0m); this plan only coordinates with the purge (section 3a).
- Backups: only the disclosed 89-day limit (section 10).
- A person's individual erasure request for data of a site still in service: that is `dispose_account` and `sms_inbox_delete`, unchanged.
- The Pack buyers' proof files: deleted only after z8uq9mbu0p (store-data export with proof images) ships (L67; Manager decision 2026-11-13). Until then the executor blanks the proof fields and keeps the files; the file-delete step for pack proofs is switched in by that task. Section 6.4 step 2 and the files tests apply to the other files meanwhile.
