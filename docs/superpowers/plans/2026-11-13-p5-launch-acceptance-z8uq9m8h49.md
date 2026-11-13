# P5 Billing launch acceptance (plan)

Status: **plan only, for the Manager's approval.** No code, test, migration, variable, flag or staging action is in this branch. It is one markdown file.

ClickUp task: z8uq9m8h49 (https://app.clickup.com/t/z8uq9m8h49), "Billing MVP P5, Billing & E-Invoicing End-to-End Acceptance". Parent: z8uq9m8h46 (BILLING Supervisor). Read against `origin/main` at `8664aa5` (backend release round 7). Branch `plan/z8uq9m8h49-launch-acceptance`, no PR.

## 1. Sources and scope

| Source | Used for |
|---|---|
| Task z8uq9m8h49, description and its 8 comments | The 11 scenarios, the evidence list, the test-environment ruling (2026-11-02: isolated synthetic fixture, real producers and worker driven directly, no storefront path, no real merchant) |
| Comment 1100430000517730 (BILLING Supervisor, 2026-11-13) | Scope: one backend acceptance module on a synthetic fixture; scenarios 1 and 4 to 10; only the bank-transfer parts of 11; Xendit, automatic restore and the manual resolution tool blocked; no migration, no new rule, no staging data |
| Rulings log z8uq9med6x (newest first, to 1100430000512863) | 509745 (staging is live, no test seed there), 512065 (routine owner decisions), 509929/509948 (no PR from a builder; local checks first). Nothing in it changes this scope |
| Memory/rulings relayed by the Manager | **No per-order billing, SaaS only** (Coach Ed, 2026-11-11, recorded in B4 section 2): Subscribers are never metered or charged per order or product sale. Launch payment is bank transfer with proof. No extra usage charge at launch (2026-11-13: "there will be no extra charge during lunch. we will turn this on later") |
| Plans read | B1 (`...-b1-subscription-lifecycle.md`), B2a (`...-b2a-bank-transfer-launch-promo.md`), B3 (`...-b3-annual-and-promo-plans.md`), B4 (`...-b4-usage-ledger-and-overage.md`, with its 2026-11-12 amendment), B5 (`...-b5-statements-and-bir-register.md`) |
| Code read | `apps/billing/{plan,services,usage,notices,methods,api,models,tasks}.py`, `apps/statements/{issue,adjust,pdf,documents,api,staff,models,managers,switch,tasks}.py`, `apps/invoices/{services,staff,scans,models}.py`, `apps/ledger/{switch,models}.py`, `apps/lifecycle/erase.py`, `apps/pack/purge.py`, `apps/payments/retention.py`, `config/settings.py`, `.github/workflows/ci.yml`, `docs/runbooks/billing.md`, and the existing tests that build the fixtures |

The legacy repository was not read. Nothing in this plan is a new rule: each assertion below cites a rule that is already in a merged plan or in code.

## 2. Findings on main (read before the scenario table)

**F1. Nothing on main meters or charges orders.** Checked: `apps/billing` (no `orders` kind; `UsageEntry.Kind` has one value, `storage`, `models.py:328`; `ChargeLine.kind` is `overage_storage`; no `record_order`, no ordinal, no PHP 5 constant, no 1,000 constant; `rg` for `per.order|overage_order|record_order|ordinal|1_000` in `apps` and `config` finds only comments saying the opposite: `billing/usage.py:4`, `billing/plan.py` storage comment, `shop/models.py:6`, `pack/models.py:6`), `apps/statements` (`StatementLine.Kind` is base, usage, credit, adjustment; `issue._lines` writes a base line and storage-overage lines only, `issue.py:54`), `apps/invoices` (`_lines` copies the same lines), `apps/ledger` (the FAS books; no order table, and nothing calls its posting switch, see F4) and B4's usage ledger (storage only, `usage.py`). B4 was amended on 2026-11-12 to drop the order half. **No code to fix; nothing to report as still metering.** The only remaining per-order text is in the task description (scenarios 2, 3, 4 and the order lines of 11), which is the record of the older ruling.

**F2. Storage is not metered at launch.** `plan.STORAGE_METERED = False` (`plan.py:158`) and `plan.usage_enabled()` needs both `BILLING_USAGE_ENABLED` and that constant. At launch `record_storage_snapshot` answers `not_metered` and writes nothing, `GET billing/usage/` answers 404, the staff usage review routes answer 404, `outstanding_centavos` is 0 (`tests/test_usage_off_at_launch.py`). The measuring producer (media upload, P6.2d, z8uq9mb38y) does not exist. Coach Ed's 2026-11-08 ruling already keeps scenario 5 deferred behind P6.2d.

**F3. Task text is older than the rulings in code. The code is authoritative; the plan asserts the code and lists the difference:**
- *Never-paid deletion date.* The task says 90 days after service end for a day-14 trial with no payment. Main sets **30 days** (L75: `plan.DELETION_NEVER_PAID`, `services._dates_after` `services.py:434`; scope document line 129; `docs/runbooks/support-and-exit.md:56`). Paid and suspended sites get 90. So "payment before day 104" is **day 44** on main, and the refusal is `409 past_deletion_date` (`services._refuse_unpayable`, `services.py:818`). Dates exist only while `plan.collection_live()` (R-4a).
- *Draft then finalise.* There is no draft. A confirmed payment gets one finished statement in one step (`issue.issue_for_payment`, `issue.py:120`; safety net `ensure_statements`, `issue.py:232`).
- *Electronic delivery evidence.* `StatementDelivery` exists but **no code writes it**; the only reader is the owner list's `delivery_state` (`statements/api.py:162`). The statement email wording (B5 Q4) is not approved.
- *Owner uploads the receipt image.* On main **staff** record the handwritten invoice and upload its scan (`invoices/services.upload_scan`, `services.py:448`), staff verify it, and the owner reads the verified copy (`billing/api.py:529`, `copy_for_owner`, `services.py:609`). This is the B5 design (manual invoice workbench ruling, 2026-11-08). There is no owner upload route.
- *Replacement statement.* Only a reduction exists (`adjust.adjust`, `adjust.py:68`). An increase is documented as a new ordinary statement with `original` set, and no service makes one.
- *Audit evidence of a rejected edit or delete.* Immutability is enforced (`statements/managers.py`: `Immutable`; PostgreSQL triggers `statements.0002`), but a refused attempt writes no audit row. Audit rows exist for creation, view, adjustment and each register step.
- *`payment_requires_attention` and a failed first charge.* `first_payment_failed` and `renewal_failed` (`services.py:669, 703`) have **no production caller** (only tests and a future gateway). With bank transfer only, a site goes `active -> suspended_no_outcome` at cycle end plus 7 days, or `trial_active -> cancelled_no_payment_method` / `suspended_no_outcome`. The attention state is unreachable at launch.
- *Automatic restore, held-pre-limit orders, referral credit.* No code for any of them. Referral credit is Build B6 (`StatementLine.Kind.CREDIT`, always zero).

**F4. The FAS ledger is not wired to billing.** `apps/ledger` (`AccountingEntity`, journals, `LEDGER_POST_BILLING_ENABLED`, `ledger/switch.py:12`) has no caller in `apps/billing`, `apps/statements` or `apps/invoices`; the posting writer is "a later piece" (`ledger/models.py` docstring). So "authoritative source ledger" in the reconciliation means the billing source tables (section 6), not FAS journals. Reconciling to FAS journals is **blocked** until a posting writer exists.

**F5. No BIR adapter exists on main.** There is no PTI/EIS/ESP/BIR client module. "Adapter is mock/sandbox only" can only be proved as an absence (section 5, scenario 10).

**F6. "LS-1" launch switches.** No file or setting named LS-1 exists in this repository. The launch switches are the settings in `config/settings.py:136-163, 252-255`: `BILLING_ENABLED`, `BILLING_BANK_TRANSFER_ENABLED`, `BILLING_LAUNCH_PROMO_ENABLED`, `BILLING_ANNUAL_ENABLED`, `BILLING_USAGE_ENABLED`, `BILLING_INVOICE_REGISTER_ENABLED`, `BILLING_STATEMENTS_ENABLED`, `BILLING_LIVE_DOCUMENTS`, `OWNER_NOTICES_ENABLED`, and the ledger pair. The module sets them itself with `override_settings`; it reads no deployed value.

## 3. Design of the one module

**File:** `apps/statements/tests/test_launch_acceptance.py` (new; the only file added). Statements already imports billing and invoices, so the module imports downwards only. Run it alone with `manage.py test apps.statements.tests.test_launch_acceptance`.

**No application change.** No model, migration, setting, service, route or helper in application code is added or edited. Test-only helpers live inside the module (`reconcile`, `text_of` reuse from `apps/statements/tests/test_pdf.py:20`).

**Fixture (synthetic, built per test class in `setUpTestData`/`setUp`):**
- Two tenants, A (full journey) and B (isolation), each an `Account` + `Site` + `Subscription` from `apps/billing/tests/base.py` (`make_subscription` `base.py:41`, `make_active` `base.py:57`; `statements/tests/base.py:new_subscription` `base.py:15`). Fake emails `@example.test`; no real merchant and no staging row. `Subscription.billing_exempt` stays false.
- Bank transfer on with a made-up receiving account and an in-memory proof store: `transfers_on()` (`base.py:119`), operator `make_operator()` (`base.py:136`), proofs `submit()` (`base.py:147`).
- Statement store: real renderer and a temp-dir private store, as `test_pdf.py:145` does (`override_settings(PRIVATE_BLOB_READ_WRITE_TOKEN="", PRIVATE_FILES_DIR=<tmp>, DEBUG=True)`), so the PDF bytes are really stored and re-read.
- Issuer and book: `make_issuer(verified=True)` (`statements/tests/base.py:27`) and `make_book()` (`base.py:43`): made-up values, `tax_type` non-VAT. These are test values and are never real issuer data (section 8).
- Settings: `BILLING_ENABLED`, `BILLING_BANK_TRANSFER_ENABLED`, `BILLING_STATEMENTS_ENABLED`, `BILLING_INVOICE_REGISTER_ENABLED`, `OWNER_NOTICES_ENABLED` on; `BILLING_USAGE_ENABLED`, `BILLING_LIVE_DOCUMENTS` off (every document is `is_test`); `plan.STORAGE_METERED` left False, except in the clearly marked class `PostLaunchStorageRehearsal` (K5b) that patches it as B4's own tests do (`test_usage_off_at_launch.py:21`).
- Time: always passed in (`now=`/`at=`), never read from the clock, using the constants in `billing/tests/base.py` (`T0`, `TRIAL_END`, `CYCLE_1_END`).
- Drivers: `services.*` and `sweep`, `notices.run`, `issue.issue_for_payment`/`ensure_statements`, `adjust.adjust`, the invoices services, and the real HTTP routes through `SitesApiTestCase` (owner, `/v1/sites/{id}/billing/...`) and `StaffTestCase` (staff, `/staff/v1/finance/...`), the same harnesses `statements/tests/test_owner_api.py` and `test_staff.py` use.

**Layout:** one `TestCase` class per scenario (`Scenario1TrialBoundary`, `Scenario5Storage`, ... `Scenario11Bank`), a shared `AcceptanceCase` base for the fixture, one `TransactionTestCase` class for concurrency (`skipUnless(connection.vendor == "postgresql")`, like `billing/tests/test_concurrency.py:28`) and one `ReconciliationTests` class used by scenarios 5, 6, 7 on their journeys. No test re-implements a price, a date or a number: expected values come from the stored rows or from the constants in `plan.py`.

## 4. Scenario table

Key: **KEPT** (tested), **BLOCKED** (listed, not tested, reason), **DROPPED** (not tested, not wanted, reason). IDs are used in section 5.

### Scenario 1, trial and subscription boundary

| ID | Item | Verdict | Reason or existing call |
|---|---|---|---|
| 1a | Trial state and cycle supplied by the lifecycle | KEPT | `start_subscription` (`services.py:505`), `overview` (`services.py:241`), `GET billing/` (`billing/api.py:109`) |
| 1b | Day 14 with no payment: `cancelled_no_payment_method`, no charge, no statement | KEPT | `sweep` (`services.py:1601`) at `TRIAL_END` |
| 1c | 30 days of download access | KEPT | `download_until = service_end + 30d` (`plan.DOWNLOAD_WINDOW`) |
| 1d | Active data marked for deletion **90** days after service end | KEPT, **asserts 30** | Code: L75 gives 30 days for a never-paid site, 90 for a paid one (F3). Question Q1 |
| 1e | "Subject to retained records" | KEPT | Billing rows survive and are readable after the dates pass (also K11l). No clock deletes billing records (scope line 129) |
| 1f | No internal statement is represented as paid or BIR-authorised during trial | KEPT | No `Statement`, `ManualInvoiceItem` or `BankTransferPayment` row; `GET statements/` empty; `GET billing/` shows no paid cycle |
| 1g | A proof under review at day 14 keeps the trial waiting; confirmed in the 7 days it opens cycle 1 | KEPT | `methods.active_at` (`methods.py:17`), `first_payment_succeeded` (`services.py:648`) via `confirm_transfer` |

### Scenario 2, included order allowance (1,000 orders)

| ID | Item | Verdict | Reason |
|---|---|---|---|
| 2 | Exactly 1,000 eligible orders in a cycle; zero PHP 5 overage records; usage display | **DROPPED** | Hard condition: no per-order metering, charge, allowance or display (Coach Ed, 2026-11-11; B4 section 2). No order allowance exists in the approved Billing spec, and none is in code (F1). The nearest real plan limit is the storage allowance, covered by scenario 5 |

### Scenario 3, order overage and idempotency (1,001st order)

| ID | Item | Verdict | Reason |
|---|---|---|---|
| 3a | One immutable PHP 5 source record, one invoice line, dashboard projection | **DROPPED** | Same ruling. There is no PHP 5 step to prove |
| 3b | Replay of the event, webhook or job does not double count, double line or double send | **DROPPED as written** | The order half is gone. The real replay guarantees are tested elsewhere in this module: storage `source_reference` replay (K5b), `confirm_transfer` replay (K9), statement `source_key` (K6), notice keys (K11h/K11k) |

### Scenario 4, excluded and cancelled order behaviour

| ID | Item | Verdict | Reason |
|---|---|---|---|
| 4a | Abandoned, failed, expired and unsuccessful-payment orders excluded | **DROPPED** | Order exclusion rules (D2, D3, Q1 as applied to orders, Q2, L18) were struck from B4 by the same ruling. Nothing counts an order |
| 4b | A cancelled order counted once | **DROPPED** | Same |
| 4c | Guard instead (KEPT, new): nothing in the system can create an order charge | KEPT | K4: `UsageEntry.Kind.values == ["storage"]`, `ChargeLine.Kind` has no order value, `StatementLine.Kind` has none, no statement or invoice line description mentions an order, and a full journey with orders-like activity (placing a Pack/shop order row on A's site) leaves every billing table unchanged. Proves "billing is the SaaS plan only" without testing a charge |

### Scenario 5, storage source

| ID | Item | Verdict | Reason or call |
|---|---|---|---|
| 5a | Launch posture: snapshots not charged, no usage route, no usage line on a statement, `outstanding_centavos` 0 | KEPT | `record_storage_snapshot` (`usage.py:104`) answers `not_metered`; `GET billing/usage/` 404 (`billing/api.py:150`); `usage.due`/`outstanding` (`usage.py:224, 285`) |
| 5b | Controlled snapshots above and below 5 GB; the statement **consumes** the `ChargeLine`, never recomputes; reconcile snapshot, line, PDF, JSON, page totals | KEPT, **post-launch rehearsal, flagged** | Needs `plan.STORAGE_METERED` patched True, the same patch B4's tests use. Asserts only existing rules: 5 GB included per allowance period, PHP 30 per started GB over (`plan.py` constants), decimal GB, one snapshot per period. Not a launch behaviour. Question Q2 |
| 5c | Video: no video source record is created or billed | KEPT | `UsageEntry.Kind.values == ["storage"]`; no `video` anywhere in billing tables, statements or the usage read |
| 5d | Producer measuring real stored bytes | BLOCKED | P6.2d (z8uq9mb38y) does not exist; only controlled snapshots are possible |
| 5e | A future video launch needs its own P5 rerun | NOTE | Recorded in section 9, no test |

### Scenario 6, Internal Billing Statement, physical receipt, immutability

| ID | Item | Verdict | Reason or call |
|---|---|---|---|
| 6a | Statement from frozen sources, finalised once, `NOBS-YYYY-######` numbering | KEPT, **no draft stage** | `issue_for_payment` (`issue.py:120`); gap-free numbers (`_next_number`, `issue.py:112`); source key `payment:<id>`; replay returns the first. Draft step does not exist (F3) |
| 6b | Mandatory label; immutable issuer, bill-to, price and source snapshots | KEPT | `Statement.label == LABEL` (`models.py:19`), PDF text contains it (`pdf.py:72`); later edit of the issuer profile, billing profile and plan catalogue leaves the stored `issuer`, `bill_to`, lines and PDF hash unchanged |
| 6c | Owner-only access; PDF/download consistency | KEPT | owner list, detail, PDF routes (`statements/api.py:179, 202, 223`); stored bytes read back through `documents.read_pdf` (`documents.py:47`) hash to `pdf_sha256`; rendered text equals stored lines and total |
| 6d | Electronic delivery evidence | **BLOCKED** | No writer of `StatementDelivery`, no approved statement email wording (F3, B5 Q4). Guard only: no billing email carries or links a statement (checked in K10) |
| 6e | Owner uploads an image of the physical receipt | **DROPPED as written** | Design is staff-operated (F3). The real flow is 6f |
| 6f | Staff record the handwritten invoice, upload and verify its scan; owner gets the verified copy only; sha256/size recorded; audit rows; link to the statement; statement facts unchanged | KEPT | `record_invoice` (`invoices/services.py:327`), `upload_scan` (`:448`), `verify_scan` (`:505`), `copy_for_owner` (`:609`), staff routes (`invoices/staff.py:192`), owner route (`billing/api.py:529`); item `statement` link set by `issue._link_item` (`issue.py:193`). The statement's row, lines and PDF hash are compared before and after |
| 6g | Edit or delete after finalisation is rejected | KEPT | `Statement.save()` on a stored row, `QuerySet.update/delete/bulk_update`, `StatementLine`, and on PostgreSQL a raw `UPDATE`/`DELETE` (trigger) all refuse (`managers.py`, `statements.0002`) |
| 6h | Audit evidence of the refused edit or delete | **BUILT** (z8uq9mevfx) | A refused edit or delete of a statement, a statement line or an invoice item now writes one `billing_record_edit_refused` / `billing_record_delete_refused` row (`managers.audit_refusal`). `test_a_refused_edit_leaves_audit_evidence` asserts it. Audit rows that already existed are asserted too: `billing_statement_viewed`, `billing_statement_adjusted`, `billing_invoice_recorded/scan_uploaded/verified`, `billing_payment_confirmed` |

### Scenario 7, correction flow

| ID | Item | Verdict | Reason or call |
|---|---|---|---|
| 7a | Credit note: original unchanged; every adjustment references it; history, PDF, JSON and totals reconcile | KEPT | `adjust.adjust` (`adjust.py:68`), `NOCN-YYYY-######`, `remaining` (`adjust.py:59`); owner list shows both with signed totals; staff detail/list (`statements/staff.py:151, 178, 221`); the adjustment PDF names the original and the reason; `invoices.reconcile` (`invoices/services.py:668`) lists the corrections |
| 7b | Reductions cannot pass the original's total | KEPT | `409 exceeds_remaining`; two adjustments in sequence |
| 7c | Replacement (increase) statement | **BLOCKED** | No service makes one (F3) |

### Scenario 8, tenant isolation and access

| ID | Item | Verdict | Reason or call |
|---|---|---|---|
| 8a | B cannot read A's billing, payments, proof, statements, statement PDF, invoice copy, usage, profile | KEPT | every owner route in section 5 K8 answers the **same** 404 body for A's id, a made-up id and a removed site (`site_for`, `sites/services.py:78`); statement numbers and cursors of A give 404/`invalid_cursor` to B |
| 8b | B cannot infer, upload or export | KEPT | sending a proof to A's site (`POST payments/`) is 404 and writes no `BankTransferPayment`; `PUT profile/` on A's site is 404 and writes nothing; list responses for B hold no A row, count or cursor |
| 8c | B cannot retry delivery | N/A | No delivery or retry route exists (6d). Listed as a guard: the route table has none |
| 8d | Signed-out access | KEPT | 401 on every owner route |
| 8e | Staff-only surfaces are closed to owners | KEPT | An owner session on `/staff/v1/finance/...` is refused |

### Scenario 9, concurrent and retry resilience

| ID | Item | Verdict | Reason or call |
|---|---|---|---|
| 9a | Duplicate `confirm_transfer` (two operators at once, and a replay) | KEPT (PostgreSQL for the race, both DBs for the replay) | one cycle, one transition, one `billing_payment_confirmed` audit row (`services.py:1148`) |
| 9b | Concurrent issue of statements: `issue_for_payment` x N and `ensure_statements` at once | KEPT (PostgreSQL) | one statement per payment, numbers unique and gap-free (`issue.py:112`, `select_for_update`) |
| 9c | Duplicate register item and duplicate scan | KEPT | `open_item_safely` x2 gives one item; the same file uploaded twice gives `duplicate_scan` and one `InvoiceScan` (`invoices/services.py:109, 448`) |
| 9d | Duplicate proof | KEPT | the same file for the same plan returns the payment already there; a new file replaces the one under review (`submit_transfer`, `services.py:887`) |
| 9e | Duplicate notices run | KEPT | `notices.run` (`notices.py:419`) twice sends each key once (`_send_once`, `notices.py:80`) |
| 9f | Duplicate usage snapshot | KEPT in 5b only | same `source_reference` returns the first result, one `ChargeLine` (`usage.py:104`) |
| 9g | Duplicate delivery entries, receipt-image links | N/A for deliveries (6d); covered by 9c for scans | |
| 9h | Duplicate payment events from a gateway | BLOCKED | No gateway on the launch path |

### Scenario 10, BIR-boundary validation

| ID | Item | Verdict | Reason or call |
|---|---|---|---|
| 10a | BIR adapter is mock/sandbox only | KEPT as an absence | No module, setting or dependency named PTI, EIS, ESP or a BIR endpoint exists (source scan of non-test `apps/` and `config/`), and the whole module runs with outbound sockets blocked |
| 10b | No production BIR transmission | KEPT | same socket block: any connect attempt fails the test |
| 10c | No UI/email/PDF/API/test data/release note claims PTI, EIS, BIR transmission, ESP, BIR compliance, an official receipt or a tax invoice | KEPT for generated outputs | Scan, after removing the exact `LABEL` text, of: both PDFs' text, every owner JSON body, every staff finance JSON body, the plan list `GET /v1/plan/` (`billing/api.py:196`), every billing email body from `notices.py`, the owner-visible register strings. **Release notes** are not in this repository and are not tested (read by the Supervisor/Manager). **Test data** is covered by asserting the fixture issuer/book values are the made-up ones. Allowed exception, named in Q4: the register copy label "Copy of manually issued BIR-registered Non-VAT Invoice" (`invoices/services.py` `COPY_LABEL`) |
| 10d | Every generated MVP output carries the non-official label; an uploaded receipt image stays evidence only | KEPT | each statement and adjustment: `label`, PDF text, owner detail `label`. The scan route returns the image with `Content-Disposition` and the copy label, and creates no `Statement` |
| 10e | Statements are marked test while `BILLING_LIVE_DOCUMENTS` is off | KEPT | `is_test` true on statements and register items (`issue.py:158`) |

### Scenario 11, suspension, recovery, late payment, restore and review (bank transfer only)

| ID | Item | Verdict | Reason or call |
|---|---|---|---|
| 11a (K11a) | Suspension: no outcome by due date plus 7 days suspends to Billing-only; owner gets the suspension email once; no new usage accrues | KEPT | `_due` (`services.py:147`), `sweep`; `_transition_emails` (`notices.py:337`); usage: nothing accrues at launch (5a), and in 5b a snapshot on a suspended site goes to `needs_review/no_cycle` |
| 11b (K11b) | Recovery before the limit | KEPT as a **late renewal inside the 7 days** | A proof confirmed after cycle end and before end+7d settles through `renewed`, the next cycle starting from the old cycle's end (`services.py:684`, `_settle_transfer` `:1117`) |
| 11c | Recovery in `payment_requires_attention` (matching amount, time inside deadline, no second cycle) | **BLOCKED** | State unreachable with bank transfer: `renewal_failed`/`first_payment_failed` have no production caller (F3). The recovery transition itself is unit-tested in `test_transitions.py` |
| 11d (K11d) | Recovery at or after the limit: starts a new paid cycle at the confirm time; the expired cycle stays non-billable and gets no usage | KEPT | `reactivated` (`services.py:747`) via `confirm_transfer` on a `suspended` site; expired cycle `lapsed` |
| 11e | Late automatic charge with referral credit | **BLOCKED** | Automatic charge needs the Xendit gateway; referral credit is Build B6 (no code) |
| 11f | Automatic restore after a payment authenticated before the limit (T6n/T6, both in-flight and `pending_too_long`) | **BLOCKED** | Needs an authenticated Xendit time and the restore (G4a, not built). Bank transfer has no "authenticated before, confirmed after" instant: the operator's confirmation time is the payment time |
| 11g | Restore end-date guard (OD11) | **BLOCKED** | Follows 11f |
| 11h | Orders in the dark interval recorded as `after_service`; held pre-limit orders (OD12); "PAID on a suspended site" for a Pack order | **DROPPED** | Order counting is gone (F1); the Xendit PAID path is not on the launch path. L49 (a lapsed unmeasured storage cycle creates no row) is kept in 5b |
| 11i | Ineligible-case review (restore guards 1 to 5) | **BLOCKED** | Follows 11f |
| 11j | Manual resolution tool (OD9, L47, L50, L56, L60, L64) | **BLOCKED** | Waits on z8uq9mbbkk |
| 11k (K11k) | Held payment within the window settles normally; past the window suspends, the review row is kept, service is not left active | KEPT (bank form) | A proof `submitted` (under review): confirmed inside the window it settles (see 1g/11b); unresolved at the window end the sweep suspends and the proof stays `submitted` for the operator; a later confirmation reactivates before the deletion date |
| 11l (K11l) | Day 14 with no active method; payment before day **44** restores with a new paid cycle; at or after day 44 refused | KEPT, **44 not 104** | `_refuse_unpayable` (`services.py:818`), `submit_transfer`. Q1 |
| 11m | Card expiry in flight or on day 15 replaced on day 16 | **BLOCKED** | Cards are Xendit; no card exists at launch |
| 11n | Disputed payment: a new payment refused while a money-holding review row is open | **BLOCKED** as written | Xendit money-hold model. Bank form (a second proof replaces the one under review) is tested in 9d |
| 11o | Review routes: order past 72 hours; payment with no authenticated Xendit time | **DROPPED / BLOCKED** | Orders dropped; Xendit time blocked |
| 11p | Review routes (storage): late snapshot admitted or dismissed; closed account owing nothing written off (`after_final_settlement`); review row on a suspended site kept, then admitted on reactivation | KEPT in 5b only | `usage.admit`/`dismiss` (`usage.py:371, 404`), `closed_and_settled` (`:270`), `release_held` (`:191`), staff routes `finance/usage/review/*` |
| 11q (K11q) | Service-end export notice for `cancelled` and `cancelled_no_payment_method`: once, replay sends nothing; text names the 30-day export, how to ask, its end date, the deletion of the receiving-account details; no promise about proof images; not unconditional (law/dispute/fraud hold wording) | KEPT | `_export_notice_emails` (`notices.py:388`), `_export_notice` (`notices.py:193`), behind `OWNER_NOTICES_ENABLED`, within the 48-hour window. The Xendit-key revoke advice is **not in the text** and is BLOCKED; "Billing-only screen shows the same text" is a frontend line and is not in this backend module |
| 11r | Day-90 clearing of Subscriber payment details | KEPT **partly**, flagged | Receiving accounts (holder name, number, QR): `payments.retention.blank_payments` through the erasure executor (`lifecycle/erase.py:145`) on a synthetic ended site, with its approval harness (`lifecycle/workflow.py`, switches `SITE_ERASURE_ENABLED`, `RETENTION_ENABLED`): cleared and removed from readers; billing records, statements and register rows unchanged (the retained-records half of 1e); a hold (`ErasureHold`) leaves them untouched. The Xendit credential parts (ciphertexts, masked email, `actor_account_id`, `webhook_key`) are **BLOCKED** (no such model on main). "A restored site shows no direct or Xendit method" is a frontend/payments-settings line. `pack.purge` deliberately leaves receiving accounts out (`pack/purge.py` docstring). Q3 asks whether to keep the erasure harness in this module |
| 11s | Z8uq9mbu0p (proof images in the export) | NOTE | The notice must not mention images until that task ships; asserted by 11q |

## 5. Kept tests: calls, fixture and assertions

Every test below is a method of the one module. "Fixture" names the state it builds; helper names are the existing ones in section 3.

**K1 (scenario 1).** *Fixture:* tenant A subscription made at `T0` with `transfers_on()` so `plan.collection_live()` is true. *Calls:* `overview` at day 13 (trial_active, `can_cancel`), `sweep(now=TRIAL_END)`, `overview` at `TRIAL_END`, `GET /v1/sites/{id}/billing/` as owner A. *Assert:* state `cancelled_no_payment_method`; `billing_only` true and `storefront_served` false; `service_end_at == TRIAL_END`; `download_until == TRIAL_END + 30 days`; `deletion_due_at == TRIAL_END + 30 days` (Q1); `outstanding_centavos == 0`; zero `BillingCycle` with `settlement=paid`, zero `BankTransferPayment`, `Statement`, `ManualInvoiceItem`, `ChargeLine`; `GET statements/` returns `items: []`; the transition list is `trial_started`, `trial_ended_no_method` only. *Second case:* a proof submitted at day 13 and still unreviewed at day 14: `sweep` at `TRIAL_END` writes nothing (trial waits), `confirm_transfer` at day 16 opens cycle 1 from `TRIAL_END` (`first_payment_succeeded` early), and at `TRIAL_END + 7 days` with no decision the sweep writes `suspended_no_outcome`.

**K4 (scenario 4 guard).** Pure schema and behaviour guard as in the table. Also runs `Subscription` through a full paid cycle while a Pack order and a shop order are created for A's site with the existing builders (`pack/tests/owner_base.make_order`, `shop/tests/owner_base.make_order`, as `lifecycle/tests/run_base.py` imports them) and asserts the `ChargeLine`, `UsageEntry`, `StatementLine` and `ManualInvoiceItem.lines` counts and sums are the same before and after.

**K5a (launch posture).** *Calls:* `record_storage_snapshot(site, source_reference=..., period_end_at=CYCLE_1_END, measured_bytes=7 GB, measured_at=CYCLE_1_END, now=CYCLE_1_END+1s)` with `BILLING_USAGE_ENABLED` on and `STORAGE_METERED` False, then with the switch off. *Assert:* result `not_metered` (then `disabled`); no `UsageEntry`, `ChargeLine`, `UsageEvent`; `GET billing/usage/` 404; a confirmed renewal at `CYCLE_1_END` freezes `usage_centavos == 0`, the statement has one `base` line, `outstanding_centavos == 0`.

**K5b (post-launch rehearsal, `PostLaunchStorageRehearsal`).** *Fixture:* A active; `mock.patch.object(plan, "STORAGE_METERED", True)` and `BILLING_USAGE_ENABLED`. *Calls:* snapshots of exactly 5 GB (no row, `within_allowance`), 5 GB plus 1 byte and 7 GB (2 started GB over; `ceil((bytes - 5 GB) / GB)`), a replay of the same `source_reference`, a second snapshot for the same period under a new reference (returns the first); then renewal `submit_transfer`/`confirm_transfer` at the next cycle, `issue_for_payment`. *Assert:* amounts equal `ChargeLine.amount_centavos` (not recomputed in the test: the expected value is `lines * plan.STORAGE_OVERAGE_CENTAVOS_PER_GB` read from the constant); one `ChargeLine` per snapshot over the allowance, immutable (`save()` of a changed amount raises); the payment froze `usage_centavos` and the statement's `usage` line has `source_ref == f"charge:{line.pk}"` and the same amount; owner `GET billing/usage/` figures equal the stored rows; the late-72-hours, suspended-site (`needs_review/no_cycle`), `cycle_pending` hold, lapsed (`written_off/cycle_lapsed`) and unmeasured-lapsed (no row, L49) routes follow `usage._route` (`usage.py:169`) and each writes a `UsageEvent`; `usage.admit` and `usage.dismiss` through the staff routes each write one `OperatorAuditEntry`; a cancelled site's `usage_only` payment and the `after_final_settlement` write-off. This class is the only place `STORAGE_METERED` is patched and is labelled "post-launch, not a launch behaviour".

**K6 (scenario 6).** *Fixture:* A active, verified issuer, book, billing profile for A. *Calls:* `submit` + `confirm_transfer` (renewal at `CYCLE_1_END`), `issue_for_payment`, again `issue_for_payment`, and `ensure_statements()`; then `record_invoice`, `upload_scan` (the PNG bytes in `billing/tests/base.py`), `verify_scan`; owner and staff reads. *Assert:* exactly one `Statement`, number matches `^NOBS-\d{4}-\d{6}$` with the Manila year, `source_key == f"payment:{payment.public_id}"`, replay returns the same row, no second number consumed (`StatementNumberSeries.last_value` unchanged); `label == LABEL`; `issuer`, `bill_to` equal the snapshot taken before the issuer profile and billing profile are later changed (`IssuerProfile` is versioned, a new unverified version does not alter the stored snapshot); `pdf_sha256 == sha256(documents.read_pdf(statement))`; PDF text rows contain the number, the label, each line description and `money(total)`; owner detail lines equal stored `StatementLine` rows; the register item's `statement` is the statement; the scan's `sha256` and `size` equal the uploaded bytes; the owner copy route returns the bytes only after `verify_scan` (404 before); statement row, lines and PDF hash are identical before and after the register steps; `Statement.save()` on a stored row, `Statement.objects.update/delete`, `StatementLine.objects.bulk_update` raise `Immutable`; on PostgreSQL a raw `UPDATE statements_statement` and `DELETE` fail; audit rows exist for confirm, record, upload, verify and each staff PDF view.

**K7 (scenario 7).** *Calls:* `adjust.adjust(operator, statement.public_id, "billing_error", [{"description": ..., "amount_centavos": -50000}])` twice (second beyond the remainder), through the staff route `finance/statements/adjust/` (`statements/staff.py:221`). *Assert:* original `Statement` row, lines and PDF hash unchanged; each adjustment has `original`, `NOCN-` number, negative total, own PDF whose text names the original number and reason; `adjust.remaining(original) == original.total + sum(corrections)`; third adjustment past the remainder is `409 exceeds_remaining`; adjusting an adjustment is `409 not_adjustable`; owner list shows original and corrections with signed totals that add to the net; `invoices.reconcile()` shows the corrections under the original's statement; one `billing_statement_adjusted` audit row per adjustment.

**K8 (scenario 8).** *Fixture:* tenants A and B each with a statement, a proof, a register item with a verified scan, and (5b only) a usage entry. *Calls:* B's client against every owner route with A's ids, with a made-up id and signed out: `billing/`, `billing/usage/`, `billing/payment/`, `billing/payments/` (GET, POST), `payments/{id}/`, `payments/{id}/proof/`, `statements/`, `statements/{number}/`, `statements/{number}/pdf/`, `invoices/`, `invoices/{id}/copy/`, `profile/` (GET, PUT), plus A's `cursor` values. *Assert:* A's id and a made-up id return identical status and error body (404 `not_found`); signed out is 401; no write happens on A (row counts and `updated_at` unchanged); B's lists contain no A row, count or cursor; an owner session on a staff route is refused.

**K9 (scenario 9).** `ConcurrencyTests(TransactionTestCase)`, PostgreSQL only, using `run_together` (`sites/tests/test_concurrency.py:16`): two threads `confirm_transfer` on the same payment; N threads `issue_for_payment` on N payments plus `ensure_statements`; two threads `open_item_safely`; two `upload_scan` of the same bytes; two `notices.run`. *Assert:* one cycle/transition/audit row; N statements numbered 1..N with no gap and no duplicate; one item; one scan and one `duplicate_scan` error; each notice key sent once. A sequential replay version of each runs on SQLite as well.

**K10 (scenario 10).** Scans described in the table. *Calls:* render every output the fixture produced and `GET /v1/plan/`; run `notices.run` for every email kind the fixture can trigger (proof received, approved, rejected, trial reminders, renewal reminders, paused, trial ended, export notice) and read `mail.outbox`; walk the non-test source with `pathlib` for the forbidden module names; patch `socket.socket.connect` to raise for the whole class. *Banned strings (case-insensitive, after removing `LABEL`):* `PTI`, `EIS`, `ESP`, `BIR compliant`, `BIR-authorized`/`authorised`, `BIR transmission`, `official receipt`, `tax invoice`, `accredit`, `certif`. *Assert:* none present; every statement and adjustment has the label in `label`, in PDF text and in the owner detail; `is_test` is true.

**K11 (scenario 11, bank-transfer lines).** One class `Scenario11Bank`:
- *K11a:* A active with cycle 1 paid; `sweep(now=CYCLE_1_END + 7 days)` writes `suspended_no_outcome` effective at exactly that instant; `overview.billing_only`; `notices.run` sends the "site is paused" email once (rerun sends nothing); no usage rows.
- *K11b:* A active; `submit` at `CYCLE_1_END + 1 day`, `confirm_transfer` at `CYCLE_1_END + 3 days`: cycle 2 starts at `CYCLE_1_END` (no gap), `settlement=paid`, state `active`, one statement for that payment.
- *K11d:* A suspended (as `make_suspended`, `base.py:74`); `submit`/`confirm_transfer` before `deletion_due_at`: new paid cycle starts at the confirmation instant; the lapsed cycle stays `lapsed` and untouched; the new payment's statement covers the new cycle only.
- *K11k:* the proof-under-review cases of K1 and an ACTIVE site whose renewal proof is still `submitted` at cycle end plus 7 days: suspended, payment row still `submitted`, later `confirm_transfer` reactivates.
- *K11l:* trial ended without payment (K1); `submit_transfer` at `TRIAL_END + 43 days` accepted and confirmed (`reactivated`, new cycle at the confirm time, `outstanding` 0); at `deletion_due_at` the call raises `409 past_deletion_date`; a never-paid subscription before collection is live has no dates.
- *K11q:* `OWNER_NOTICES_ENABLED` on; one `cancelled` site (cancel inside a paid cycle, `service_ended_at` reached) and one `cancelled_no_payment_method` site; `notices.run(now=service_end + 1h)` sends one export notice to each, a rerun none. *Assert* the body contains: the 30-day export, the support address, `download_until` date, `deletion_due_at` date, "receiving-account details"; contains none of "image", "proof image", "Xendit"; contains the law/dispute/fraud hold sentence; subject is the approved one (`test_owner_notices.py` already pins it).
- *K11r (flagged, Q3):* synthetic ended site with a `SitePaymentAccount`, a hold on a second site; run `workflow.propose/approve/run_batch` at `deletion_due_at`; *assert* the held site's account is untouched, the other's holder name, number and QR key are blank and its `removed_at` set, and `Subscription`, `BillingCycle`, `BankTransferPayment`, `Statement`, `StatementLine`, `ManualInvoiceItem` counts and hashes are unchanged.

**K12 (structure guard, from the task's "no duplicate calculation path").** Scan non-test sources: `STORAGE_OVERAGE_CENTAVOS_PER_GB`, `STORAGE_INCLUDED_GB`, `GB_BYTES` and `started_gb` appear only in `apps/billing/plan.py` and `apps/billing/usage.py` (true on main today); only `statements/issue.py` and `invoices/services.py` create statement or item lines, and both read `ChargeLine.amount_centavos`; there is one billing route module and one `billing/usage/` route. Cheap, deterministic, fails when someone adds a second calculation path.

## 6. Reconciliation check

Implemented once as the test helper `reconcile(subscription)` and called at the end of K5b, K6 and K7 (and over the whole fixture in a final `ReconciliationTests.test_everything_adds_up`). It **reads stored values only and recomputes no price**; any expected figure comes from the stored row it is compared against.

1. **Sources.** For each confirmed `BankTransferPayment`: `amount_received_centavos == amount_centavos`; `base = amount_centavos - usage_centavos`; `usage_centavos == sum(ChargeLine.amount_centavos where settled_payment=payment)`; every such line is `settled`, with `closed_at` set. The cycle it settled is `paid` and holds the plan snapshot (`plan_code`, `plan_version`, `price_centavos`). (At launch usage is zero and the check reduces to the base.)
2. **Frozen statement lines.** `sum(StatementLine.amount_centavos) == Statement.total_centavos == payment.amount_received_centavos`; the `base` line equals `base`; each `usage` line's `source_ref` is `charge:<pk>` of exactly one of the settled lines, with the same amount; no line without a source.
3. **Register item.** `ManualInvoiceItem.total_centavos == Statement.total_centavos`; `sum(item.lines[*].amount_centavos) == item.total_centavos`; `item.statement` is the statement; a recorded `ManualInvoice.total_sales_centavos == item.total_centavos`.
4. **Owner API totals.** `GET statements/` row `total_centavos`, `GET statements/{number}/` lines and total, `GET billing/payments/` row `amount_centavos`, `GET billing/invoices/` row and `GET billing/` `outstanding_centavos` (equals `usage.due` for the subscription) all equal the stored values above; the sum of statement totals plus adjustment totals for the site equals the sum of payments received minus reductions.
5. **PDF facts.** For the statement and each adjustment: `sha256(read_pdf) == pdf_sha256`, `len == pdf_size`; the text drawn (`text_of`) contains the number, the issue date, the period, the payment reference, each line's description, quantity, unit amount and amount, and the total, in PHP with two decimals, and the label.
6. **Corrections.** `remaining(original) == original.total + sum(adjustment totals)`; each adjustment's PDF names the original; the owner net equals the original less the corrections.
7. **Not included:** FAS journal postings (F4, blocked), JSON export of statements (no such export route exists on main; the staff and owner JSON bodies are the JSON facts), and any visual comparison.

## 7. How to run

All commands from the repository root, with `uv` and this repo's own `.venv`.

**SQLite (default, no `DATABASE_URL`):**
```
uv run python manage.py test apps.statements.tests.test_launch_acceptance
```
The concurrency class skips itself (row locks need PostgreSQL). Sequential replay cases run.

**Local PostgreSQL (throwaway, on a fresh port, never another session's service):**
```
# one-time: a scratch cluster in the session scratchpad, on a port nothing else uses (for example 55439)
initdb -D "$SCRATCH/pg-p5" -U postgres --auth=trust -E UTF8
pg_ctl -D "$SCRATCH/pg-p5" -o "-p 55439 -k $SCRATCH" -l "$SCRATCH/pg-p5.log" start
createdb -h 127.0.0.1 -p 55439 -U postgres p5_acceptance
DJANGO_SECRET_KEY=local-only-long-randomized-value-0000000000000000 DJANGO_DEBUG=false \
DATABASE_URL=postgresql://postgres@127.0.0.1:55439/p5_acceptance \
REDIS_URL=redis://127.0.0.1:<fresh redis port>/0 \
uv run python manage.py test apps.statements.tests.test_launch_acceptance
# afterwards: pg_ctl -D "$SCRATCH/pg-p5" stop, remove the directory
```
This mirrors the CI job's environment (`.github/workflows/ci.yml:49-53`). The module also touches the existing statements and billing suites, so before reporting the branch ready, run the full suite on both databases as the release train requires: `uv run python manage.py test --parallel` on SQLite and the same with the PostgreSQL variables above, plus `uv run python manage.py makemigrations --check --dry-run`, `manage.py check`, `manage.py check --deploy`.

Expected shape of a green run: one module, about 45 to 60 test methods, no skips on PostgreSQL, the PostgreSQL-only classes skipped on SQLite. Each run must print its own result; none is claimed in this plan.

## 8. PR count, files touched

**PR count: 1** (no PR is opened by the builder; the Release Builder combines it). One branch for the module: `test/z8uq9m8h49-launch-acceptance`, cut from the latest `origin/main` after this plan is approved.

**Files touched (build):**
- New: `apps/statements/tests/test_launch_acceptance.py`.
- Nothing else. No application file, migration, setting, fixture file, seed or runbook change. (Optional, only if the Manager wants a record in the repository: one short "Acceptance run" paragraph in `docs/runbooks/billing.md`; not planned.)

**Files touched by this plan branch:** `docs/superpowers/plans/2026-11-13-p5-launch-acceptance-z8uq9m8h49.md` only.

**Overlap check at build time:** before reporting ready, list open PRs and compare changed files (builder brief, "Branches and conflicts"). The module is a new file, so overlap is unlikely.

## 9. Unresolved issuer and BIR data that stays blocked, not guessed

The fixture uses made-up test values only (`make_issuer`, `make_book`, `TEST_ACCOUNTS`). The following are real data Coach Ed supplies and no test asserts or contains a real value:

- **Issuer profile (from the Certificate of Registration):** legal name, trade name, TIN, branch code, registered address, tax type (Non-VAT), COR number, COR date, contact email, and the staff verification of that profile. Until verified, statement issue is refused (`Refused("no_issuer")` / `issuer_incomplete`, `issue.py:79`).
- **Invoice book (from the paper book / ATP):** ATP reference, ATP date, series, serial range, serial width, printer name.
- **Receiving accounts:** the real NegosyoOnline account names and numbers (entered in HQ Finance, Accounts).
- **`BILLING_LIVE_DOCUMENTS`:** off; real documents are `is_test` until Coach Ed approves it.
- **Statement email wording** (B5 Q4) and its delivery switch: not approved; no delivery evidence can exist.
- **`BILLING_STAFF_ALERT_EMAIL`**, the private Blob token on staging, and the billing and ledger switches in shared environments: his own.
- **BIR matters, all out of scope and unclaimed:** PTI application, EIS certification, ESP accreditation, BIR production transmission, any BIR compliance statement, an official receipt or tax invoice from the system. Release notes carry none of these (checked by reading, not by this module).
- **FAS ledger posting** (F4) and the **Xendit gateway** items in section 4 stay blocked with their tasks (z8uq9mbbkh G4a, z8uq9mbbkk resolution tool, P6.2d z8uq9mb38y, z8uq9mbu0p proof-image export, Build B6 referral credit).

Scenario 11 and 5d cannot be closed by this module (Coach Ed, 2026-11-08: "Do not close this task until both scenarios are covered"). The module covers their launch-path form only; the task needs a follow-up rerun when the blocked lines ship.

## 10. Evidence this module does and does not give (task "Evidence required")

| Task evidence | Covered here |
|---|---|
| Automated results for all scenarios | Scenarios 1, 5 (launch form and flagged rehearsal), 6, 7, 8, 9, 10 and the bank-transfer lines of 11. Not 2, 3, 4 (dropped), not the blocked lines |
| Local visual proof (history, detail, label, PDF, receipt image, usage, isolation, correction) | **Not covered.** These are frontend screens; a backend module gives no visual proof. The Supervisor decides who gathers it (frontend billing builder, Coach Ed's staging test) |
| Reconciliation record | Yes, section 6 (billing source tables = statement lines = totals = owner API = PDF), printed by the test run |
| Unresolved issuer/BIR list | Section 9 |
| No duplicate Billing page or duplicate usage-calculation path | The calculation half is K12. "No duplicate Billing page" is a frontend check, not in this module |

## 11. Open questions for the Supervisor (each has a recommendation; none blocks writing the plan)

- **Q1. Never-paid deletion date (30 vs 90 days; day 44 vs day 104).** The task text says 90 and 104; main (L75, scope line 129, support runbook) says 30 and 44. Recommended: assert main (30 / 44) and treat the task text as superseded. Consequence if Coach Ed wants 90: a rule change in B1, outside this task.
- **Q2. Keep K5b, the post-launch storage rehearsal?** It patches `STORAGE_METERED`, as B4's own tests do, and is the only proof the statement consumes the charge lines. Recommended: keep it in a separate, labelled class. If the Manager reads "no extra charge at launch" as "do not exercise overage at all", drop K5b and the storage review items of 11p, and scenario 5 reduces to K5a and K5c.
- **Q3. Keep K11r (erasure and receiving-account clearing) in this module?** It exercises the lifecycle executor (task z8uq9mb22v), which has its own tests and its own approval switches. Recommended: keep one thin case (accounts cleared, held site untouched, billing records unchanged), because it is the only place "subject to retained records" is checked end to end. Drop it if the Supervisor wants the module free of the erasure harness.
- **Q4. Is the register copy label "Copy of manually issued BIR-registered Non-VAT Invoice" an allowed phrase in the BIR-claim scan?** It is shown for a real, staff-verified paper invoice. Recommended: allow exactly that string, on the invoice-copy route only, and fail on any other BIR-registration wording.
- **Q5. Scenario numbering.** Your note says "1 and 4 to 10" and the comment says "1 and 3 to 10". Both include scenarios that are order-based (3 and 4). This plan drops 2, 3 and 4 and adds the K4 guard. Confirm.
- **Q6. Visual-proof evidence (section 10)** is outside a backend module. Recommended: leave it to the frontend/staging test and record it separately before the task is closed.
- **Q7. Audit row for a refused edit (6h)** and **statement delivery (6d)** are real gaps against the task text, not test omissions. Recommended: report them to the Manager as findings on B5; this plan does not ask for code.

## 12. As built (2026-11-13) and the answers received

Approved by the Manager at `ad59ddb` ("The builder may write apps/statements/tests/test_launch_acceptance.py as planned"). Answers from the Supervisor (ClickUp comment 1100430000517799): Q1 assert main (30 days, refusal from day 44); Q2 keep K5b in its own labelled class; Q3 keep K11r, one thin case; Q4 allow exactly the copy label, on the invoice-copy route only; Q5 scenarios 2, 3, 4 and the order lines of 11 dropped, K4 replaces them; Q6 visual proof stays outside the module; Q7 the audit row on a refused edit is written as an expected failure naming subtask z8uq9mevfx, no application code; `StatementDelivery` stays blocked (PR 8).

The module is `apps/statements/tests/test_launch_acceptance.py` (the only file added besides this plan). Differences from the plan above, all in the direction of calling less:

- **Storage review (K5b, 11p)** calls `usage.admit` and `usage.dismiss` directly (with their audit rows asserted), not the `finance/usage/review/*` staff routes, which B4's own tests already cover.
- **Scenario 8e** (an owner using a staff route) is covered by the staff gate case in `StaffFinanceTests` (signed out and foreign origin are refused on all four statement routes); no non-staff session is built.
- **6h** is `test_a_refused_edit_leaves_audit_evidence`. It was an expected failure until z8uq9mevfx; the marker is removed and the audit row is built (see the 6h row). A database-trigger refusal (raw SQL) still leaves no row: a trigger has no application context.
- **K4's order activity** uses the shop order builder (verified, cancelled, pending review) on the site; the Pack order builder is not used.
- **K10** also runs the billing emails (trial reminders, proof received, rejected, approved, renewal reminder, paused, trial ended, export notice) and checks each for the banned claims, for attachments, and for any statement number.
- **K12** is `NoSecondCalculationPathTests` (a plain `unittest.TestCase`, no database).
- Test classes: `TrialBoundaryTests`, `OrdersAreNeverBilledTests`, `StorageAtLaunchTests`, `PostLaunchStorageRehearsal`, `NoSecondCalculationPathTests`, `StatementJourneyTests`, `EmailsMakeNoBirClaimTests`, `PlanListMakesNoBirClaimTests`, `OwnerApiTests`, `StaffFinanceTests`, `SuspensionAndRecoveryTests`, `ServiceEndNoticeTests`, `ErasureKeepsBillingRecordsTests`, `ConcurrencyTests` (PostgreSQL only), `ReconciliationTests`.
