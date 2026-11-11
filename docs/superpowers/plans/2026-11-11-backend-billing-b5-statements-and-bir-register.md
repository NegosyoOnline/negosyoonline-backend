# Billing Build B5: Billing Statements and the manual BIR invoice register (plan)

Status: **plan only, for Coach Ed's approval.** No code, migration, variable, dependency, flag or staging action is in this pull request.

ClickUp task: z8uq9mdwt0 (https://app.clickup.com/t/z8uq9mdwt0). Parent: z8uq9m8h46. Read against `origin/main` at `52d8b6d`.

## 1. Sources

- The task z8uq9mdwt0: objective, "Settled rules; do not reopen", "Adapted to the new backend", "Accepted when", "Needs Coach Ed's separate approval", "Order". It has no comments, checklists, custom fields or attachments.
- The parent z8uq9m8h46: "CURRENT OWNER RULING — MANUAL BIR INVOICING INTERIM (2026-11-08)", "OWNER-PROVIDED PHYSICAL INVOICE — FORM-MIRRORED MANUAL INVOICE WORKBENCH (2026-11-08)", "E-invoicing and BIR boundary", "Shared non-negotiables". Its four comments are about the legacy build and change nothing here.
- The scope document `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`, sections 6, 8, 11, 12. **This plan does not edit it** (the coordinator's instruction): its proposed amendments are in section 12.
- The merged B1 and B2a code and plans: `apps/billing/*`, `docs/superpowers/plans/2026-11-11-backend-billing-b1-subscription-lifecycle.md`, `...-b2a-bank-transfer-launch-promo.md`, `...-b2a-pr4-hq-finance-page.md`, `docs/runbooks/billing.md`.
- NPC Build 6 and 9 and the staff routes: `apps/operators/{models,audit,staff_views}.py`, `docs/runbooks/retention.md`.
- Coach Ed's rulings of 2026-11-11 as relayed: no new Django admin screens, operator work goes in the HQ dashboard under Finance; launch payment is bank transfer with proof; billing and tax records are kept 5 years (L76).
- The legacy repository was not needed and was not read.

A rule below with no source is marked **choice** (a reversible detail I decided; all are listed in section 13) or **question** (section 14).

## 2. What "After B2" means now

The task says "Order: After B2". B2 as first written was the Xendit gateway: payment methods, `PaymentAttempt`, webhooks. That is not built, and launch payment is bank transfer with proof (B2a), which is merged:

| Merged | What it gives B5 |
|---|---|
| B1 (merged) | `Subscription`, `BillingCycle` (`paid_at`, `payment_reference`), the transitions |
| B2a PRs 1 to 3 (backend 87, 88, 89) | `BankTransferPayment`: plan, amount and currency frozen at submission, `amount_received_centavos`, `decided_at`, `decided_by`; `confirm_transfer` settles exactly one cycle in one transaction under the subscription's row lock |
| B2a PR 4 (frontend 41) | The HQ Finance page and the `/staff/v1/finance/payments/*` routes, so a staff session, the audit pattern and the route-registration pattern already exist |

So, for B5, **"After B2" means "after B2a"**, and "a succeeded payment" / "a paid attempt" (the task's words, from the legacy `PaymentAttempt`) means **a `BillingCycle` that a confirmed `BankTransferPayment` settled**. There is no `PaymentAttempt` table in this backend.

Two consequences:

1. The unit of "one sale" is the **confirmed payment**, not the cycle. *(Amended 2026-11-12, forced by B4 usage_only, PR 129.)* B4 added the `usage_only` payment: a confirmed payment of storage overage with no cycle, which is a sale too. The item is unique on (`source_kind`, `source_ref` = the payment's public id), and its `cycle` is a nullable link (null only for a `usage_only` payment; a term payment still links its cycle). That unique key gives "a paid attempt creates exactly one pending-invoice item" whatever settles the cycle. When the gateway (B2) is built, its success function settles a cycle too and calls the same one function (section 6.1); B5 then needs no change.
2. B5 does not wait for B2 (gateway), only for what B2a already merged. Usage lines wait for B4 and annual description for B3, but both arrive as data into tables this plan makes generic (section 5.3), so B5 can ship first.

## 3. What exists, what B5 reads, what it adds

| Exists | Used by B5 |
|---|---|
| `BankTransferPayment`, `BillingCycle`, `Subscription` | The event and the frozen amounts. **Read only. No column is added to a billing table and `apps/billing` gets no migration** (section 8) |
| `plan.PLANS` (monthly, launch promo) | Plan name and months for descriptions. B3 adds the annual plan to the same dict |
| `apps/billing/proofs.py` (`sniff`, size and type rules, `LocalProofStore`, the unbuilt private store) | The file type check is reused; the private store (question Q1) is shared with B2a |
| `apps/notifications/services` | The only way any app sends an email |
| `apps/operators/audit.py` | Every operator action, declared in `ACTIONS` first |
| `apps/operators/staff_views.py` + `apps/billing/staff.py` | The pattern: a module of POST handlers whose `ROUTES` keys start with `finance/`, registered by three lines in `staff_views.py` |
| Celery worker and beat (staging) | Issue, deliver and alert as retriable jobs |
| `apps/common/{ids,pagination,errors,throttle}` | `public_id`s, cursors, the error shape |

Not in the backend and therefore built here: any private file store, any PDF code, any statement, issuer, number series or invoice table.

## 4. Where the register lives (a deviation from the task text, flagged)

The task says the register "lives in the Django admin". Coach Ed ruled on 2026-11-11 that there will be no new Django admin screens and that operator work goes in the HQ dashboard (Finance menu; the HQ `/admin/` lists are only the old read-only ones). **This plan puts the register in HQ, in the Finance menu, as new staff routes** `finance/invoices/*`, `finance/statements/*` and `finance/issuer/*`, reached through the two-step HQ session that NPC Builds 6 and 9 built. "Behind the admin two-step sign-in of NPC Build 6" and "every action in `OperatorAuditEntry`" both still hold. The pages themselves are a frontend task (the HQ app) and are not in this plan; this plan builds the routes they call.

## 5. Tables

Two new apps. **No table is added to, and no migration is written in, `apps/billing`** (reason in section 8).

### 5.1 `apps/statements`

| Table | Columns | Notes |
|---|---|---|
| `IssuerProfile` | `public_id` (`iss_`), `legal_name`, `trade_name`, `tin`, `branch_code`, `registered_address`, `tax_type` (`non_vat`), `cor_number`, `cor_date`, `contact_email`, `verified_at`, `verified_by` (staff `User`), `verification_note`, `created_at`, `created_by` | **Versioned, not edited**: a change inserts a new row. The newest row with `verified_at` set is the current issuer (a partial unique constraint keeps one "current"). Statement issue is blocked while there is none or a mandatory field is empty. Values come from Coach Ed's COR (question Q3); none is in code or in a seed |
| `BillingProfile` | `site` one-to-one (`PROTECT`), `company_name`, `billing_email`, `address`, `tin` (blank unless supplied), `updated_at` | The scope document lists it under billing; it lives here so billing gets no migration. Written by the owner's `PUT`. Optional: with none, the bill-to falls back to the account's name and email, address and TIN empty (choice C3) |
| `StatementNumberSeries` | `year` (unique), `prefix` (`NOBS`), `last_value` | One row per year and prefix. Locked with `select_for_update` inside the issuing transaction, so the number and the statement commit together or neither does. No gap is possible except by an uncommitted transaction, which rolls the counter back too. A second row set for `NOCN` (adjustments) |
| `Statement` | `public_id` (`stm_`), `number` (unique, `NOBS-YYYY-######`), `document_type` (`statement`, `adjustment`), `kind` (`monthly`, `annual`, `usage_only`, `final_usage`, `launch_promo`), `subscription` FK, `cycle` FK (null for a usage-only or final one without a paid cycle), `source_key` (unique), `original` FK to itself (null unless a correction), `adjustment_reason`, `period_start`, `period_end`, `currency`, `total_centavos`, `is_test`, `issuer` (JSON snapshot), `bill_to` (JSON snapshot), `payment_reference` (our reference string, e.g. `btp_…`), `label` (the exact label text, stored), `pdf_key`, `pdf_sha256`, `pdf_size`, `issued_at` | **Insert-only** (5.4). `source_key` is `payment:<btp_id>:<kind>` *(Amended 2026-11-12, forced by B4 usage_only, PR 129.)* (or `adjustment:<original>:<n>`): the second concurrent issue for the same event hits the unique key and returns the first. A check constraint: an adjustment has `original`, a statement of kind `monthly`/`annual`/`launch_promo` has `cycle` |
| `StatementLine` | `statement` FK, `position`, `kind` (`base`, `usage`, `credit`, `adjustment`), `description`, `quantity`, `unit_centavos`, `amount_centavos` (signed), `source_ref` | Insert-only. The sum of `amount_centavos` equals `Statement.total_centavos`; the issuing function asserts it before commit and a test holds it |
| `StatementDelivery` | `statement` FK, `channel` (`email`), `to_email` (snapshot), `status` (`pending`, `sent`, `failed`), `attempt`, `error_code` (a code, never a message), `idempotency_key` (unique, `deliver:<statement>:<n>`), `created_at`, `sent_at` | The only mutable statement table: delivery state is not part of the document. Kept like any record (no delete) |

### 5.2 `apps/invoices` (the manual BIR register)

| Table | Columns | Notes |
|---|---|---|
| `InvoiceBook` | `issuer` FK, `series` (text, may be blank), `serial_from`, `serial_to`, `serial_width`, `atp_reference`, `atp_date`, `printer_name`, `active` | Read-only evidence copied from the paper book, entered once by staff. Serials outside `serial_from..serial_to` are flagged. The system never advances a serial: it shows the **next expected number as a hint only** |
| `ManualInvoiceItem` | `public_id` (`mbi_`), `cycle` FK (**nullable**, null only for a usage-only payment), unique (`source_kind`, `source_ref`) *(Amended 2026-11-12, forced by B4 usage_only, PR 129.)*, `subscription` FK, `site_public_id`, `site_name`, `source_kind` (`bank_transfer`), `source_ref` (payment public id), `plan_code`, `period_start`, `period_end`, `total_centavos`, `lines` (JSON snapshot: description, quantity, unit, amount), `paid_at`, `sale_date` (Manila date of `paid_at`), `tax_year`, `statement` FK (null, set once when the statement exists), `is_test`, `status` (`pending`, `recorded`, `uploaded`, `verified`; a void returns the item to `pending`), `created_at` | Created in the same transaction as the confirmation (6.1). The snapshot fields never change; only `status` and `statement` move. **No delete** |
| `ManualInvoice` | `public_id` (`min_`), `item` FK, `book` FK, `serial_number`, `issued_on` (**date, not null, no default**), `sale_type` (`cash` default, `charge`), `buyer_name`, `buyer_tin`, `buyer_address`, `description`, `service_start`, `service_end`, `total_sales_centavos`, `discount_centavos`, `discount_kind`, `withholding_centavos`, `amount_due_centavos`, `status` (`recorded`, `uploaded`, `verified`, `voided`), `void_reason`, `recorded_by`, `recorded_at`, `verified_at`, `verified_by` | Constraints: unique (`book`, `serial_number`); **at most one non-voided row per `item`** (partial unique); `amount_due = total_sales − discount − withholding` (check); `discount_kind` set only with a discount. Three separate dates: `issued_on` (actual), `ManualInvoiceItem.sale_date` (payment), and the scan's `uploaded_at` |
| `InvoiceScan` | `public_id` (`scn_`), `invoice` FK, `store_key`, `content_type`, `size`, `sha256`, `status` (`uploaded`, `verified`, `rejected`), `reject_reason` (`unreadable`, `wrong_invoice`, `amount_mismatch`, `other`), `uploaded_at`, `uploaded_by`, `decided_at`, `decided_by` | A rejected scan stays; a new one is uploaded. One live scan per invoice (partial unique). The same `sha256` on two scans is refused (one file cannot back two serials). A verified scan is immutable |
| `InvoiceEvent` | `item` FK, `invoice` FK (null), `code`, `actor`, `idempotency_key` (unique), `recorded_at`, `detail` | Append-only, like `SubscriptionTransition`. Codes: `opened`, `recorded`, `scan_uploaded`, `scan_rejected`, `verified`, `voided`, `statement_linked`. Together with `OperatorAuditEntry` this is the "immutable audit trail after verification" |

### 5.3 Why `StatementLine` and `ManualInvoiceItem.lines` are generic

B3 and B4 are being planned in parallel. A line is `(kind, description, quantity, unit, amount, source_ref)`; B3's annual base and B4's overage rows are more rows of the same shape, not columns. **What B5 needs from them** (to be relayed by the coordinator):

- **B4:** a function that returns the frozen charge lines of a cycle, in order, and that never changes the lines once a statement from them exists. B5 does not read `UsageEntry` and never recalculates usage (task rule). Until B4 exists B5 reads only the base line.
- **B3:** the annual plan as a `Plan` in `plan.PLANS` (`code`, `months=12`). B5 maps plan code to the description wording (6.2); an unknown code produces the plan's own `name`, a line that says so, and an exception flag on the item rather than a wrong sentence.

### 5.4 How immutability is enforced

"Never edited or deleted through UI, API, admin, migration, job or script":

1. **Model:** `Statement`, `StatementLine`, `ManualInvoiceItem` (snapshot fields), `InvoiceEvent` raise on `save()` of an existing row and on `delete()`, as `OperatorAuditEntry` does.
2. **Queryset:** their manager's `update()`, `delete()`, `bulk_update()` and `bulk_create(update_conflicts=…)` raise, so a job or shell script cannot bypass the model methods.
3. **Database (PostgreSQL), recommended:** a `BEFORE UPDATE OR DELETE` trigger on `statements_statement`, `statements_statementline` and `invoices_invoiceevent` that raises, written in its own migration `statements.0002` so it can be dropped without touching the tables. Raw SQL and a future migration are then stopped too, the only layer that covers "migration" and "script". SQLite (local, CI) relies on 1 and 2. Question Q8.
4. **Tests:** every guard above, plus a test that lists every model with `Statement` or `Invoice` in its path and fails if one lacks a guard.

## 6. Behaviour

### 6.1 The pending invoice item (register)

`invoices.services.open_item(cycle, payment)` is called from inside the confirmation's transaction, in `apps/billing/services.py::confirm_transfer`, after the cycle exists and before the audit entry, **only while `BILLING_INVOICE_REGISTER_ENABLED` is on** (a lazy import, so billing does not import invoices at load). It:

1. Inserts the item with the frozen snapshot: cycle period, `payment.amount_received_centavos` as the total, the lines (6.2), `paid_at`, `sale_date`, site name, plan.
2. Is idempotent: the unique payment key makes a repeat return the existing row; an `opened:<cycle>` event key backs it.
3. Cannot fail the payment: the call runs inside its own savepoint, so an error in it is logged (no personal value) and rolls back only the item, not the confirmation; the safety net below then creates the item. Choice C5.
4. Safety net: a Celery task `invoices.ensure_items`, every 5 minutes with the sweep, and a command `invoices_backfill_items --dry-run/--apply`, insert an item for each confirmed payment (including a usage-only one) that has none. This also covers payments confirmed **before the switch was on** (important: the switch must be on before the first real confirmation, or the backfill covers it).

The gateway (B2) will call `open_item` from its own success function with `source_kind="gateway"`.

`is_test`: true unless `BILLING_LIVE_DOCUMENTS=1`. A statement or item made under a test flag is labelled in the PDF, the API and the HQ queue and is never shown as a paid charge (choice C6; question Q10).

### 6.2 Suggested text ("WRITE THIS ON PAPER")

Computed on read from the frozen item, never stored as a document. Per field in paper-form order: header (read-only from the verified issuer and the book), No. (typed; hint only), Date (sale date, shown; the **actual issue date has no default**), Cash/Charge (cash), Sold To (billing profile, else account), Item description, Qty 1, Unit price and Amount, totals box, signature areas (never filled).

Descriptions, from the task, dates as Manila dates in the form `25 Nov 2026 to 25 Dec 2026` (choice C7):

- `NegosyoOnline SaaS subscription — Monthly Plan, <start> to <end>`
- `NegosyoOnline SaaS subscription — Annual Plan, <start> to <end>`
- `NegosyoOnline SaaS Subscription — Launch Promo (6 Months), <start> to <end>`
- Overage: separate rows, one per frozen usage line (B4).

Never in the deductions: Xendit's fee, its VAT or remittance withholding. A discount or withholding is typed by the operator with its kind; it never defaults.

### 6.3 Recording, uploading, verifying, voiding

All are staff routes (section 9), each a service function taking the staff `User`, each writing an `InvoiceEvent` and an `OperatorAuditEntry`.

- **Record** (`pending → recorded`): book, serial, actual issue date, sale type, buyer details, description, service dates, totals. **Refused**: a serial already used in that book (`duplicate_serial`); `total_sales` different from the item's total (`amount_mismatch`); a book that is not active; a missing mandatory field. **Flagged, not refused** (they are facts the operator must see, listed by `exceptions()`): serial outside the book's range, gap or out-of-order serial, issue date before the payment date, issue date later than the sale date (overdue by N days, counted from the sale date), service dates different from the cycle, a withholding or discount present.
- **Upload scan** (`recorded → uploaded`): JPEG, PNG or PDF, decided by the first bytes (`proofs.sniff`), at most 8 MB (choice C8), hashed, stored in the private store, never a URL. Duplicate hash refused.
- **Verify** (`uploaded → verified`): the operator confirms the file is legible and matches serial, date and amount. **Reject** with a reason keeps the scan and returns to `recorded`.
- **Void**: marks the invoice `voided` with a reason; the row, the scan and the events stay. A replacement can then be recorded against the same item (the partial unique allows it). No delete anywhere.
- **Order of states and concurrency:** every transition runs under the item's row lock with its idempotency key, as B1 does.

### 6.4 The queue, alerts, reconciliation

- **Queue** (`finance/invoices/queue/`): items by status filter (Ready to Write, Issued awaiting upload, Awaiting verification, Published, Exceptions), grouped by paid date and by age, age counted from `sale_date`. The text "DO NOT issue invoices in a single delayed Monday batch." sits on the route's answer as a fixed notice; Monday is described as scanning and reconciling only.
- **Daily alert:** a Celery beat task at 8:00 Manila lists the number of pending items and the oldest age. Who receives it and in what form is question Q9; nothing is sent until answered.
- **Reconciliation register** (`finance/invoices/reconcile/`): per verified or recorded invoice, its serial, actual issue date and face amount against payment date, subscriber/site, the statement number and any adjustment (`NOCN`) linked to the sale, with the exceptions. Read only.

### 6.5 What a subscriber sees

Only a **verified** scan of a **recorded, non-voided** invoice whose item belongs to **that subscriber's subscription**, labelled "Copy of manually issued BIR-registered Non-VAT Invoice". Until then: "Payment received; official invoice copy pending upload". Any other state, another subscriber's id or a guessed id answers the same 404. The file has no address; it is sent only by the owner route, `Cache-Control: private, no-store`, `nosniff`, a neutral file name.

## 7. Statements

### 7.1 Issue

`statements.services.issue_for_cycle(cycle)` (Celery task `statements.issue`, queued with `transaction.on_commit` from the same place as `open_item`, retried by `statements.ensure`):

1. Refuse (and retry later, nothing written) while there is no verified issuer profile or a mandatory issuer field is empty, or while the private store is unavailable. This never blocks the payment.
2. In one transaction: lock the payment row, return the existing statement if `source_key` exists; lock the number series row; take the number; read the lines (base from the frozen payment now; B4's frozen lines later) plus a Subscription Credit line only when the credit is not zero (always zero until B6; choice C9); snapshot the issuer and the bill-to; assert `sum(lines) == total`; render the PDF (7.2); store it; insert the statement and its lines; set `ManualInvoiceItem.statement` once.
3. Kind: `monthly` (base plus prior-cycle usage), `annual`, `launch_promo`, `usage_only`, `final_usage`. The promo is not one of the four named kinds; it gets its own value (choice C10).

A repeated or concurrent call returns the first statement; the numbers are gap-free because the counter rolls back with a failed transaction.

### 7.2 The PDF

Generated once at issue from the frozen rows and stored with its SHA-256; it is never regenerated from current data. Content: the exact label **"Internal Billing Statement — Not an Official BIR Receipt or BIR Electronic Invoice."** on every page; number; issue date (Manila); issuer snapshot; bill-to snapshot (TIN only if supplied); period; lines; total in `PHP 2,499.00` form (the word, not the peso sign, so no font has to be embedded); payment reference; a `TEST` band when `is_test`. No word anywhere claims BIR compliance, PTI, EIS or ESP status, and the document is never called a receipt or invoice. Dependency: question Q2.

### 7.3 Corrections

An adjustment (`document_type="adjustment"`, number `NOCN-YYYY-######`, own series row) reduces a statement: negative lines, reason code, `original` required, total never larger than the original's remainder (the sum of earlier adjustments is checked under the original's row lock). An increase is a new ordinary statement with `original` set. Neither touches the original. An operator creates one through `finance/statements/adjust/` with a reason; audited. Adjustments carry the same exact label (choice C11).

### 7.4 Delivery

`StatementDelivery` row per attempt, key `deliver:<statement>:<n>`. The task sends the email through `apps/notifications` to the billing email (profile, else account email) with category `billing_statement`, **no attachment** (the email carries the statement number, the total, the exact label and a link to the dashboard Billing page; choice C12). A failure records `failed` and the beat task retries with backoff up to 5 attempts, then stays `failed` and shows in HQ for a manual resend (`finance/statements/resend/`). An account being disposed of cannot be mailed: `is_disposed_address` is honoured. The wording is question Q4, with a draft in section 14.

## 8. Migrations

| Migration | Adds | Safe before it runs? |
|---|---|---|
| `apps/statements/migrations/0001_initial.py` | `IssuerProfile`, `BillingProfile`, `StatementNumberSeries`, `Statement`, `StatementLine`, `StatementDelivery` and their constraints | Yes: only adds tables; depends on `billing.0002_bank_transfer` and `sites`; no code reads them while `BILLING_STATEMENTS_ENABLED` and `BILLING_INVOICE_REGISTER_ENABLED` are off |
| `apps/invoices/migrations/0001_initial.py` | `InvoiceBook`, `ManualInvoiceItem`, `ManualInvoice`, `InvoiceScan`, `InvoiceEvent` | Yes, same reasoning; depends on `statements.0001` |
| `apps/statements/migrations/0002_immutability_triggers.py` | PostgreSQL triggers of 5.4 (a no-op on SQLite) | Yes. **Only if Q8 is answered yes** |
| *(conditional)* a file table | Only if Q1 is answered "PostgreSQL" | Written only after that answer |

**Why no migration in `apps/billing`:** B3 and B4 will each add `billing.0003`-style migrations in the same app; two leaf migrations with the same parent break `migrate` until merged. B5 depends on `billing.0002_bank_transfer` by name and so stays valid whatever lands after it. **Order relative to B3 and B4: none is needed for migrations**; they can be applied in any order, together, by the coordinator on staging. Approval: each migration file is for Coach Ed's approval with this plan; running them is the coordinator's step on staging per `docs/runbooks/migrations.md`, never production.

## 9. Routes

### 9.1 Owner, under `/v1/` (signed in; `site_for`; a missing, removed or foreign site, or a guessed id, answers the same 404; 404 and no table read while the switch is off)

| Route | Answer |
|---|---|
| `GET sites/{site_id}/billing/` | **Unchanged** (billing overview, B1). It already carries the state, plan and amounts the page needs |
| `GET`, `PUT sites/{site_id}/billing/profile/` | The billing profile; `PUT` takes exactly `company_name`, `billing_email`, `address`, `tin`; unknown fields refused; throttled |
| `GET sites/{site_id}/billing/statements/` | Newest first, cursor; each: number, kind, document type, issue date, period, total, `is_test`, delivery state |
| `GET sites/{site_id}/billing/statements/{number}/` | The statement with its lines and `original` number; the totals are the stored ones |
| `GET sites/{site_id}/billing/statements/{number}/pdf/` | The stored file, headers as 6.5 |
| `GET sites/{site_id}/billing/invoices/` | One row per paid sale: state `pending` ("Payment received; official invoice copy pending upload") or `available` (with the copy label). Never a serial, a scan id or a hash before verification |
| `GET sites/{site_id}/billing/invoices/{invoice_id}/copy/` | The verified scan, 6.5 |

The scope document lists `usage/` and `payment-method/setup/`; they belong to B4 and B2 and are not here.

### 9.2 Staff, under `/staff/v1/` (POST, JSON, the two-step session; keys start `finance/`)

`finance/issuer/{get,set,verify}/`, `finance/statements/{list,detail,pdf,adjust,resend}/`, `finance/invoices/{books,queue,detail,record,scan,upload,verify,void,reconcile}/`. A test holds that no key collides with the existing ones (the B2a rule). The file upload is the one route that cannot be JSON-only: **question Q7** (multipart, to the HQ/SMS coordinator).

## 10. Settings and audit

Each is one line in `config/settings.py` and one row in `docs/runbooks/billing.md`. Setting any in a shared environment needs Coach Ed's approval; none is set by this plan.

| Setting | Default | Meaning |
|---|---|---|
| `BILLING_STATEMENTS_ENABLED` | off | Issue, deliver, the owner statement routes and the staff statement routes; needs `BILLING_ENABLED` and an available private store |
| `BILLING_INVOICE_REGISTER_ENABLED` | off | Creates pending items on confirmation, the staff register routes and the owner invoice routes |
| `BILLING_LIVE_DOCUMENTS` | off | `1` means documents are real. Off: every statement and item is labelled `TEST` |
| the private-store variables | none | Question Q1 |

`manage.py check` gains `statements.E001` (statements on with no store) like `billing.E002`. New KEPT audit actions in `apps/operators/audit.py`: `billing_issuer_set`, `billing_issuer_verified`, `billing_statement_adjusted`, `billing_statement_resent`, `billing_statement_viewed`, `billing_invoice_recorded`, `billing_invoice_scan_uploaded`, `billing_invoice_scan_viewed`, `billing_invoice_verified`, `billing_invoice_scan_rejected`, `billing_invoice_voided`. Details hold ids, serial numbers and amounts, never a name, email, TIN or address.

## 11. Retention, tests and acceptance

**Retention (L76).** Nothing here has a delete path and no `run_retention` rule touches these tables or their files; the retention runbook gains a line saying so. Disposal of an account does not blank or remove statements, deliveries or invoice records: their snapshots are the billing record kept five years (the account's own personal values are blanked as Builds 7 and 10 already do). The coordinator should confirm that reading with Coach Ed once, since the snapshot holds a name and email after the account is closed.

**Tests** (SQLite and PostgreSQL; concurrency on PostgreSQL only, as `test_concurrency.py`):

| Acceptance line | Test |
|---|---|
| Concurrent issue never gives two statements or numbers | Two threads issue the same cycle; one statement, one number; ten cycles at once give ten consecutive numbers; a failed issue leaves no gap |
| A final statement cannot change by any path | Model save and delete, queryset `update`/`delete`/`bulk_*`, raw SQL on PostgreSQL (trigger), an adjustment does not touch the original |
| Totals match to the centavo | Lines sum = statement total = API total = text parsed from the PDF, for monthly 249,900, promo 499,900 and multi-line cases; credit line omitted at zero and subtracted when set |
| A paid attempt makes exactly one pending item | Confirm once, twice, concurrently, and run the safety net: one item; promo and monthly; exempt and trial create none; confirm before the switch then backfill |
| Duplicate serial, mismatched total, cross-subscriber download refused | Unique (book, serial); `amount_mismatch`; subscriber A on B's copy and on B's statement PDF: 404 |
| Unverified or unmatched scan never visible | States `recorded`, `uploaded`, rejected, voided: subscriber route answers `pending`/404 and the file is never served |
| Issue blocked without issuer | No verified issuer, empty mandatory field: nothing issued, payment unaffected, retried when fixed |
| Labels and claims | Exact label on every PDF and email; `TEST` when not live; no text contains "BIR compliant", "PTI", "EIS", "ESP" (a test greps the rendered text) |
| Delivery | Failure then retry sends once; a duplicate task sends once; disposed address skipped |
| Exceptions | Gap, out-of-order, wrong period, overdue, issue-before-payment, withholding present, duplicate hash |
| Parent's acceptance list | Monthly 2,499 and annual 24,990 amounts; Friday payment and Monday scan (the issue date is typed, not defaulted); Xendit fees never in deductions; signature never filled; the audit entries and events exist after verification |

Repository checks: `uv run python manage.py check`, `check --deploy`, `makemigrations --check`, `uv run python manage.py test apps.statements apps.invoices apps.billing`, the full suite.

## 12. Proposed amendments to the scope document (for the coordinator to apply; this PR does not edit it)

- **Section 6, billing:** `BillingProfile` moves to a new `statements` group (no billing migration).
- **Section 6, statements:** `Statement` also holds `document_type`, `kind`, `source_key`, `original`, `is_test`, the issuer and bill-to snapshots and the PDF hash; `StatementLine` gains `kind`, `quantity`, `unit_centavos`, `source_ref`; `StatementDelivery` gains `attempt`, `error_code` and an idempotency key. New tables: `IssuerProfile`, `StatementNumberSeries`.
- **Section 6, new group `invoices`:** `InvoiceBook`, `ManualInvoiceItem`, `ManualInvoice`, `InvoiceScan`, `InvoiceEvent`.
- **Section 8, billing row:** replace `GET PUT sites/{site_id}/billing/profile/` shape with the one in 9.1 and add `GET sites/{site_id}/billing/invoices/` and `.../invoices/{invoice_id}/copy/`. Note that statements need no `{number}` outside the owner's site.
- **Section 11:** "Where statement PDFs are stored" is answered by question Q1 when answered; invoice scans join it.
- **New amendment entry:** "the manual BIR invoice register is in HQ, Finance menu, not Django admin" with the source of section 4.

## 13. Choices made (each reversible; reported to the coordinator)

| # | Choice | Why |
|---|---|---|
| C1 | The sale is a paid `BillingCycle`; the item is unique on the cycle | There is no `PaymentAttempt`; this also fits the later gateway |
| C2 | New apps `statements` and `invoices`; no billing migration | Avoids two leaf migrations with B3 and B4 |
| C3 | Bill-to falls back to the account's name and email when no billing profile exists | Task: "TIN only when supplied"; issue is blocked only by issuer data |
| C4 | Issuer profile is versioned by new rows | A statement snapshots it; an edit must not change history |
| C5 | A failure creating the item must not cancel the payment; a safety net creates it | Task: "payments continue whether or not the invoice is pending" |
| C6 | Documents are `TEST` unless `BILLING_LIVE_DOCUMENTS=1` | Task: "test data is visibly labelled"; the safe default |
| C7 | Dates in descriptions: `25 Nov 2026 to 25 Dec 2026` | The task gives `[start] to [end]` only |
| C8 | Scan limit 8 MB, JPEG/PNG/PDF only (no HEIC) | Larger than a proof's 5 MB because a phone photo of a page is bigger; same sniff rule |
| C9 | A Subscription Credit line is omitted while zero | Noise otherwise; the interface for B6 exists |
| C10 | Kind `launch_promo` added | The four named kinds do not fit B2a's promo |
| C11 | Adjustments use number prefix `NOCN` (the parent names it) and the same exact label | The label rule says every system-generated document |
| C12 | The statement email has no attachment | `apps/notifications` sends no attachment; a link keeps personal data out of mail |

## 14. Open decisions

**For Coach Ed, via the coordinator (short list; each blocks only the part named):**

- **Q1. Private storage for statement PDFs, invoice scans and the B2a proofs (one decision for all three).** Blocks: PDF storage (S2), scans (R2) and the B2a proof switch. Options: (a) a private S3-compatible bucket on the platform, reached only by the backend with credentials in Railway variables, never public, new dependency `boto3`; (b) PostgreSQL, a file table, no dependency or variable, but scans of 2 to 4 MB each make the database large over five years; (c) another provider. **Recommended: (a)**, one bucket, files under random keys, served only through the owner and staff routes. If Railway offers a bucket on the current plan, use it so there is one vendor; otherwise Cloudflare R2. The variables and the dependency are separate approvals.
- **Q2. PDF dependency.** **Recommended: ReportLab** (pure Python wheel, no system libraries, byte-stable output with its invariant mode, BSD). WeasyPrint needs Pango/Cairo system packages on Railway, which `uv sync` cannot supply.
- **Q3. Issuer profile values.** Mandatory: legal name, TIN, branch code, registered address, tax type (Non-VAT), COR number and date, contact email; for the register: ATP reference, series, serial range, printer, ATP date. Coach Ed supplies them from the COR and the paper book; they are entered through HQ, verified by a staff identity, and never put in code, a seed, this repository or a chat. Statement issue stays blocked until then.
- **Q4. Statement email wording.** Draft for approval (no BIR claim, no attachment): Subject `Your NegosyoOnline billing statement NOBS-2026-000123`. Body: "Hello <name>, your billing statement for <site> is ready: total PHP 2,499.00 for <start> to <end>. You can view and download it on your Billing page: <dashboard link>. Internal Billing Statement — Not an Official BIR Receipt or BIR Electronic Invoice. NegosyoOnline" Nothing is sent until the wording is approved.

**For the coordinator to settle with sessions or Coach Ed:**

- **Q5. Order of builds.** Recommended: the register before statements. The pending item must exist before the first real payment is confirmed, and the manual invoice is the document Coach Ed must write at the point of sale; statements are not legally time-critical. Section 15 is in that order.
- **Q6. Switches.** Two separate switches (statements, register) so the register can go live alone.
- **Q7. Multipart staff route** for the scan upload (HQ/SMS coordinator owns `staff_views.py`): the staff dispatch reads a JSON body of at most 2 KB, and a base64 scan would exceed Django's 2.5 MB body limit. Recommended: one multipart route that reads `request.FILES`, allowed by name in `staff_views.py`; the alternative is raising the body limit for that route.
- **Q8. PostgreSQL triggers** for immutability (5.4 layer 3): recommended yes.
- **Q9. Daily alert:** recipients and channel (email to which address; or only the HQ queue badge), and its wording.
- **Q10. `BILLING_LIVE_DOCUMENTS`** as the test/live switch: confirm the name and that production sets it only on Coach Ed's approval.
- **Q11. Refunds and credit notes** for the paper invoice (the parent mentions them): this plan keeps void-and-replace only and the statement adjustments; a recorded paper credit note is a later addition if wanted.
- **Q12. Disposal reading** of section 11 (snapshots kept after closure).

Already with Finance/counsel, not decided here (task): RR 7-2024 treats a "Billing Statement" as supplementary, not an invoice; CPA/RDO confirmation of invoice timing and of a digital copy.

## 15. Pull requests, in build order

Each is cut from the latest `origin/main`, dark while its switch is off, lists the open pull requests and their files before it is opened, and needs this plan approved first. Migrations are written only as section 8 lists.

| # | Pull request | Files | Needs |
|---|---|---|---|
| 1 | **Issuer, series, models.** `statements` and `invoices` apps with both `0001` migrations, the immutability guards, `config/settings.py` apps and switches, the audit names | `apps/statements/*`, `apps/invoices/*`, `apps/operators/audit.py`, `config/settings.py` | Plan approved; migration files approved |
| 2 | **Issuer and book staff routes** (set, verify, books) | `apps/statements/staff.py`, `apps/operators/staff_views.py` (3 lines) | PR 1; HQ/SMS coordinator's yes to the lines |
| 3 | **Register core.** `open_item`, the hook in `confirm_transfer`, safety net task and command, suggested text, record, exceptions, queue, detail, void, reconcile | `apps/invoices/*`, `apps/billing/services.py` (one call), `docs/runbooks/billing.md` | PR 1, 2; no store needed |
| 4 | **Scans and owner invoice routes.** Upload (multipart, Q7), verify, reject, the two owner routes | `apps/invoices/*`, `apps/billing/proofs.py` reuse, store implementation | Q1, Q7 |
| 5 | **Statement issue.** Numbering, lines, snapshots, the issue job and safety net | `apps/statements/*` | PR 1; Q3 for real use |
| 6 | **PDF and store.** Renderer, storage, PDF route, `statements.E001` | `apps/statements/*`, `pyproject.toml`, `uv.lock` | Q1, Q2 |
| 7 | **Owner statement API and billing profile** | `apps/statements/api.py`, `config/api.py` | PR 5, 6 |
| 8 | **Delivery and retries** | `apps/statements/*` | PR 7; Q4 |
| 9 | **Adjustments and staff statement routes** | `apps/statements/*`, `apps/operators/staff_views.py` (3 lines) | PR 5, 6 |
| 10 | **Daily alert** | `apps/invoices/tasks.py`, `config/settings.py` | PR 3; Q9 |

**Relative to B3 and B4:** PRs 1 to 4 and 10 touch neither `plan.py` nor billing models. PRs 3 and 9 add one call in `apps/billing/services.py`; B3 and B4 also edit that file, so whoever merges second rebases (a few lines, no migration order). PR 5 onward reads B4's frozen lines through the function asked for in 5.3: until B4 exists, statements carry the base line only. Annual and usage descriptions need no B5 change when B3 and B4 land.

**Files other sessions have open on 2026-11-11** (for the coordinator): PR 103 edits `apps/operators/audit.py` and `config/settings.py` and the scope document; PR 104 edits `config/settings.py`, `apps/accounts/retention.py`, `docs/runbooks/retention.md` and the scope document. B5's PR 1 touches `audit.py` and `settings.py`, and PR 2 and 9 touch `staff_views.py`; no overlap with 103 and 104 other than adding lines in the same two files, resolved by rebasing.

Out of this plan: any automatic or electronic BIR invoice, the BIR adapter, the accounting journals, the 8% income-tax workpaper, the mock paper-form graphic and print worksheet, the dashboard Billing page and the HQ pages (frontend), and deleting or anonymising any record.
