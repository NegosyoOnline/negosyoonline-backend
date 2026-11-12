# Billing HQ-F: HQ Finance screens for usage review, issuer details and the invoice register (plan)

Status: **plan only, for approval.** No code, test, migration, variable or PR is part of this document. The build is in the frontend repository (`negosyoonline-frontend`, app `apps/hq`); this plan lives in the backend repository beside the B4 and B5 plans whose routes it calls.

ClickUp task: z8uq9mek5t. Parent: z8uq9m8h46. Depends on z8uq9mdwt0 (B5). Written 2026-11-13 by a plan-only builder.

## 1. Sources

Read:

- Task z8uq9mek5t (no comments, no subtasks). Its only dependency is z8uq9mdwt0. Parent z8uq9m8h46 (description not re-read in full; its rulings reach this plan through the B5 plan and task).
- Task z8uq9mdwt0 (B5): "Settled rules; do not reopen" (exact strings, no BIR compliance, PTI, EIS or ESP claim) and "Accepted when".
- Task z8uq9mekpt (B5 PR 4, scans) and its comment 1100430000509362 (Q7 answered: scan upload, verify and reject go in `apps/invoices/staff.py` `ROUTES`, POST only, multipart parsed inside the view, no edit to `staff_views.py`, 8 MB, JPEG/PNG/PDF).
- Task z8uq9mek5q (DASH-1) comments, in particular 1100430000509243 item i: "HQ Finance detail (finance.js): HQ agreed; it goes in the billing frontend PR."
- Rulings log, task z8uq9med6x, newest first (read; the ones that bind here are in section 2).
- B5 plan `docs/superpowers/plans/2026-11-11-backend-billing-b5-statements-and-bir-register.md` sections 4, 5.2, 6, 9.2, 10, 14, 15.
- B4 plan `docs/superpowers/plans/2026-11-11-backend-billing-b4-usage-ledger-and-overage.md` section 8 (review routes, U8: "the page itself is a frontend task").
- Backend at `origin/main` e8faf61: `apps/billing/staff.py` (usage review), `apps/statements/staff.py` and `services.py` (issuer, books), `apps/invoices/staff.py` and `services.py` (register), `apps/operators/staff_views.py` (how staff routes are called), `apps/statements/switch.py`, `apps/billing/usage.py` (review rules).
- Backend branch `origin/feat/z8uq9mekpt-b5-invoice-scans` at 48f8bab (B5 PR 4, **not merged**): `apps/invoices/staff.py` and `scans.py`. The scan routes below are read from it; if the merged code differs, the merged code wins and this plan is corrected.
- Frontend, read only: `apps/hq` as in the worktree `.claude/worktrees/z8uq9me128-sms-inbox-design-7ebfa7` (HQ files identical to `origin/main` 4b8c279 except `next.config.ts`, `hq-env.mjs`, `env.test.mjs`, `hsts.test.mjs`, which are unrelated): `public/finance.html`, `assets/js/{finance,hq,menu,shell}.js`, `assets/css/hq.css`, `next.config.ts`, `src/lib/hq-env.mjs`, `README.md`, `package.json`, `tests/{page,dom,finance,menu,rules}.test.mjs`.

**Not read** (marked here so nothing below is taken as checked):

- The DASH-1 plan `plan-z8uq9mek5q-dash-billing-page.md` and the sender-details plan (a shared notes folder outside the repos). The DASH-1 branch `feat/z8uq9mek5q-dash-billing` holds no `apps/hq` change yet, so what DASH-1 will change in `finance.js` and `finance.html` is known only from comment i above (sender details on the payment detail).
- The B3 plan, parent task z8uq9m8h46's full description, and `hq.css` beyond the `fin-*`, `batch`, `chip` and `dash-card` rules.
- Nothing was run: no local server, no browser, no test.

## 2. Rules that bind these screens

From the sources above and the rulings log (all are quoted from the task or the log, none invented):

- Staff work goes in the HQ dashboard, never Django admin; no new Django admin screen (B5 plan section 4; ruling of 2026-11-11 as recorded there). `rules.test.mjs` already fails any page that names the Django Admin.
- Exact strings, used verbatim where shown: **WRITE THIS ON PAPER**; **DO NOT issue invoices in a single delayed Monday batch.**; **Internal Billing Statement — Not an Official BIR Receipt or BIR Electronic Invoice.** (the last only on statement screens, section 7). The label for a subscriber-visible scan, "Copy of manually issued BIR-registered Non-VAT Invoice", and "Payment received; official invoice copy pending upload" are the subscriber's wording; HQ may show them only as "what the Subscriber sees" and never as a status claim.
- No page, error text or label claims BIR compliance, PTI, EIS or ESP. A test greps the served HQ files for those words (section 6).
- Focus border light blue like dash, never gold; card titles never focusable (ruling "Focus border light blue, no gold"). HQ already has `tests/focus.test.mjs`; every new page is added to it.
- HQ app rules in `apps/hq/README.md`: no inline script, no `style` attribute or block, nothing from another host, nothing stored in the browser but the theme, no id, name, number or amount in an address or query string, only `hq.js` calls the backend, no console output.
- Legal wording minimal (ruling "Legal wording, bare compliance"): the pages add no explanatory legal text beyond the exact strings above.
- No per-order billing anywhere; billing is the SaaS plan only (not touched here).
- Visual test requests are written page by page: link first, then exactly what to test (ruling 2026-11-13).

## 3. What the staff routes are (all `POST`, JSON, `/staff/v1/`)

Called as `NOHq.call("POST", path, body)` after the two-step session; the Origin check, the session, `X-Frame-Options` and the 500 wrapper are in `staff_views.py`. Each group answers **404** while its switch is off, so each page needs a "not switched on" view like the existing `v-closed`.

| Group | Routes (on main unless marked) | Switch |
|---|---|---|
| Usage review (B4) | `finance/usage/review/{list,detail,admit,dismiss}/` | `BILLING_ENABLED` and `BILLING_USAGE_ENABLED` |
| Issuer (B5 PR 2) | `finance/issuer/{get,set,verify}/` | register or statements switch (`issuer_enabled`) |
| Invoice books (B5 PR 2) | `finance/invoices/books/`, `books/set/`, `books/active/` | `BILLING_INVOICE_REGISTER_ENABLED` |
| Register (B5 PR 3) | `finance/invoices/{queue,detail,record,void,reconcile}/` | register |
| Scans (B5 PR 4, branch 48f8bab, not merged) | `finance/invoices/scan/` (the file), `upload/` (multipart: fields `invoice`, `scan`), `verify/` and `reject/` (body `scan`, reject also `reason`) | register |
| Statements (B5 PR 6 to 10) | `finance/statements/{list,detail,pdf,adjust,resend}/` | **not built** |

Shapes the screens depend on, from the code:

- **Usage list**: body `limit`, `cursor`; answer `items[]` (`entry`, `kind`, `status`, `reason`, `site {id,name}`, `cycle`, `period_index`, `period_end_at`, `received_at`, `measured_bytes`), `next_cursor`, `needs_review_count`. Detail adds `history[]`. `admit` and `dismiss` take `entry` and `reason` (at most 300 characters) and answer the updated entry. Admit is allowed only for reason `late_over_72h` (otherwise `409 not_admissible`); the other reasons seen in the review queue are `no_cycle` and `after_service_end`, which can only be dismissed.
- **Issuer get**: `{current, pending}`, each an issuer (`issuer`, `legal_name`, `trade_name`, `tin`, `branch_code`, `registered_address`, `cor_number`, `contact_email`, `tax_type`, `cor_date`, `created_at`, `verified_at`, `verification_note`) or null. `set` takes the same fields (`cor_date` as YYYY-MM-DD) and answers 201 with an **unverified** profile; `verify` takes `issuer` and `note` and can refuse `409 already_verified` or `409 superseded`. Values come from Coach Ed's COR and are never in code or a seed (B5 plan Q3).
- **Books**: `books/` returns every book (`series`, `serial_from`, `serial_to`, `serial_width`, `atp_reference`, `atp_date`, `printer_name`, `active`, `issuer`); `books/set/` adds one (refuses `serial_range_overlaps`, `issuer_not_verified`); `books/active/` takes `series`, `serial_from`, `active`. A book is identified by (`series`, `serial_from`), not an id.
- **Queue**: body `filter` one of `ready`, `awaiting_upload`, `awaiting_verification`, `published`, `exceptions`, `all`, and `limit` (at most 200; **no cursor**). Answer: `notice`, `filter`, `groups[]` (`paid_date`, `items[]` with `item`, `status`, `site`, `site_name`, `plan`, `total_centavos`, `age_days`, `is_test`, `serial`, `exceptions[]`), `count`, `pending_count`. Exception codes: `unknown_plan_wording`, `overdue` (days), `serial_out_of_range`, `serial_gap`, `serial_out_of_order`, `issued_before_payment`, `issued_late` (days), `service_dates_differ`, `discount_or_withholding`.
- **Item detail** (body `item`): the frozen sale (`site`, `site_name`, `plan`, `period_start/end`, `paid_at`, `sale_date`, `age_days`, `total_centavos`, `lines`, `payment`, `statement`, `is_test`), `notice`, `suggested` (the WRITE THIS ON PAPER worksheet: `label`, `header`, `number`, `date`, `sale_type`, `sold_to`, `rows`, `total_sales_centavos`, `deductions`, `signature`), `exceptions`, `invoices[]` (each with `exceptions`, and on branch 48f8bab `scans[]`), `books[]` (active, with `next_expected_hint`), `events[]`.
- **Record** (body): `item`, `series`, `serial_from`, `serial_number`, `issued_on` (no default), `sale_type` (`cash` or `charge`), `buyer_name`, `buyer_tin`, `buyer_address`, `description`, `service_start`, `service_end`, `total_sales_centavos`, `discount_centavos`, `discount_kind`, `withholding_centavos`. Refusals the page must word: `duplicate_serial`, `amount_mismatch`, `book_inactive`, `not_pending`, `already_recorded`, and `invalid_request` with `fields`. **Void** takes `invoice`, `reason`.
- **Reconcile**: body `from`, `to` (dates, optional); answer `items[]` (`serial`, `status`, `issued_on`, amounts, `payment_date`, `paid_centavos`, `site`, `site_name`, `item`, `statement`, `adjustments[]`, `exceptions[]`), `count`. No paging.
- **Scans** (branch 48f8bab): scan object `scan`, `status` (`uploaded`, `verified`, `rejected`), `reject_reason` (`unreadable`, `wrong_invoice`, `amount_mismatch`, `other`; reject takes **no note**), `content_type`, `size_bytes`, `uploaded_at`, `decided_at`. Refusals: `duplicate_scan`, `not_recorded`, `not_uploaded`, `scan_store_unavailable` (503), `invalid_request` for size or type. The file comes back only from `scan/`, with no address.

Gaps found in the routes (reported, not fixed here; each is a backend task if wanted): the queue has no cursor (a limit of 200 is the ceiling); reconcile has no paging; `issuer/get` returns no history of past profiles; no route lists items by site; there is no staff route for an unmatched or rejected-scan count beyond the queue filters.

## 4. Pages, one by one

The HQ app is static pages, one document per feature, with views that change in the browser and **no value in an address**; what is open is held in memory so a reload returns to the list (the Finance, Contacts and Bulk pattern). Plan: keep `/finance/` as it is (payments) and add pages under it, reached by a **Finance sub-navigation** (a row of links at the top of each Finance page: Payments, Usage review, Issuer, Invoices, Reconciliation). The side menu keeps one Finance entry, marked current for every `/finance/` address. Pages reuse the existing shell, `dash-card`, `chip`, `fin-tabs`, `batch` list rows, modal dialogs and the house combo, with the same focus and keyboard behaviour.

| Page | Address | Document |
|---|---|---|
| Payments (exists) | `/finance/` | `finance.html`, `finance.js` |
| Usage review | `/finance/usage/` | `finance-usage.html`, `finance-usage.js` |
| Issuer and invoice books | `/finance/issuer/` | `finance-issuer.html`, `finance-issuer.js` |
| Invoice register: queue, one sale, record, scan | `/finance/invoices/` | `finance-invoices.html`, `finance-invoices.js` |
| Reconciliation | `/finance/invoices/reconcile/` | `finance-reconcile.html`, `finance-reconcile.js` |
| Statements (later PR) | `/finance/statements/` | not in this plan's build |

### 4.1 Usage review (`/finance/usage/`)

Shows: a list of usage records that need review, oldest first (`needs_review_count` in the heading), each row the site name, the reason in words (`late_over_72h` "Arrived more than 72 hours after the period ended", `no_cycle` "No billing cycle", `after_service_end` "Measured after service ended"; an unknown reason is left blank, never shown raw), the measured size (bytes shown as GB with two decimals), the period end and the time received. Load more by cursor.

Opens one record in the page (id in memory): the same facts, the cycle number, the history list (event and actor in words, the same rule as payments: an unknown code or actor is left out).

Does: **Admit** (only when the reason is `late_over_72h`) and **Dismiss** (every record), each a dialog with a required reason of at most 300 characters, the dialog pattern of Confirm and Reject payment. A refusal is worded for `not_admissible`; a 404 returns to the list. After a decision the page returns to the list with a one-line message ("Admitted" or "Dismissed, not billable"). No other usage action; no amount is shown or typed (a decision changes status only, the line's price is the backend's).

Not shown: personal data (the route carries none), the producer detail.

### 4.2 Issuer and invoice books (`/finance/issuer/`)

Two cards.

**Issuer details.** Shows the current verified profile (legal name, trade name, TIN, branch code, registered address, tax type, COR number and date, contact email, verified date and note) and, when there is one, the pending unverified profile with a Verify button. With no current profile, the card says issuing is blocked until one is verified (plain fact, no legal text). **Record new details** opens a form (all fields of `set`; `cor_date` as a date) that creates an unverified profile; a change never edits a profile (versioned, B5 C4), and the card says so in one line. **Verify** opens a dialog with a note (at most 300 characters) and the line that the staff member has checked the values against the COR; refusals `already_verified` and `superseded` are worded. No value is prefilled, defaulted or stored in the browser; none is in code (B5 Q3).

**Invoice books.** Lists books (series, serial range, width, ATP reference and date, printer, active). **Add a book** (form of `books/set/`, refused with a plain message when no issuer is verified or the range overlaps) and an **Active** switch per book (`books/active/`). A book cannot be deleted or edited; the page offers neither.

### 4.3 Invoice register (`/finance/invoices/`)

**Queue view.** The fixed notice at the top: "DO NOT issue invoices in a single delayed Monday batch." followed by the second sentence of the backend notice ("Write each invoice when its payment arrives; Monday is for scanning and reconciling."), held in the page (not read from the answer, see decision D7). Tabs: Ready to write, Issued awaiting upload, Awaiting verification, Published, Exceptions (the five filters; `all` is not a tab). The count of pending items sits on the first tab. Rows are grouped under a heading per paid date (oldest first), each row: site name, plan name in words, amount, age in days counted from the sale date, a `TEST` chip when `is_test`, the serial once recorded, and one chip per exception in words (overdue "Overdue by N days", serial gap, out of order, out of range, issued before payment, issued late by N days, service dates differ, discount or withholding present, unknown plan wording). Open a row to see the sale. Limit 100 per call; if `count` equals the limit the page says more exist and offers no cursor (route gap, section 3).

**One sale view** (id in memory), a split page like the payment page:

- Left card, **the sale**: site, plan, period, paid date and sale date (labelled as separate from the issue date), amount, the frozen lines, the payment reference, the statement number when there is one, the `TEST` chip, the event history (event words only), and the exceptions in words.
- Right card, **WRITE THIS ON PAPER**: the worksheet in paper-form order from `suggested`: header (issuer), number ("Type the preprinted serial from the paper"), date, Cash or Charge, Sold To, the description rows, the total, the deductions note, the signature note. Read only; copy of the route's wording as it is in code today. The page adds nothing legal.

Below, by state:

- **Ready to write (pending):** the **Record the paper invoice** form: book (a choice from the active books of the answer; shows the next expected serial as a hint only, never filled in), serial number, **actual issue date (empty, required, no default)**, Cash or Charge (Cash selected), buyer name, TIN, address (prefilled from `suggested.sold_to` as text the operator can change, because the buyer details are what the paper shows), description (prefilled from the first suggested row), service start and end (prefilled from the period), total sales (prefilled with the amount paid, entered in pesos and sent as centavos by the shared parser), discount with its kind, withholding. Plain hint under the deductions: never Xendit's fee, its VAT or remittance withholding (the route's own words). Submit shows the refusals in words, including `duplicate_serial` and `amount_mismatch`. A flag-not-refuse exception returned after recording is shown on the recorded view, not as a block.
- **Recorded (awaiting upload), uploaded, verified:** the recorded invoice (serial, issue date, amounts, buyer), its exceptions, **Void** (dialog, required reason, at most 300 characters; the sale returns to Ready to write and the row, scan and events stay), and the scan area of 4.4. No edit and no delete anywhere.

**Reconciliation** is its own page (4.5).

### 4.4 Scans (inside 4.3; depends on B5 PR 4, task z8uq9mekpt)

Marked as depending on branch 48f8bab being merged; planned against that branch.

- **Upload** (invoice recorded, no live scan): a file picker and an Upload button. The page checks type (JPEG, PNG or PDF) and size (at most 8 MB) before sending, as a courtesy only; the backend decides by the first bytes and its answer is shown. The request is a multipart POST to `finance/invoices/upload/` with fields `invoice` and `scan` through a **new `NOHq.upload(path, formData)`** in `hq.js` (same credentials and error shape as `call`, no `Content-Type` header set by hand so the browser writes the boundary). Refusals worded: type, too large, `duplicate_scan` ("This file is already attached to an invoice"), `not_recorded`, `scan_store_unavailable`.
- **View** (invoice uploaded or verified): the file is read with `NOHq.file("finance/invoices/scan/", {scan})`, held in the tab's memory, shown from an object address, released on close; JPEG and PNG in an image frame, PDF as a file tile with a Preview button opening the held object in a new tab (exactly the proof behaviour; no CSP change is needed, `img-src 'self' blob:` already stands). Each view is audited by the backend (`billing_invoice_scan_viewed`).
- **Verify and Reject** (scan `uploaded`): the invoice values (serial, issue date, amount due, buyer) sit beside the scan. **Verify** opens a dialog: "Confirm the file is legible and matches the serial, date and amount." **Reject** opens a dialog with the four reasons (unreadable, wrong invoice, amount differs, other); no note field because the route takes none. A rejected scan stays listed; the invoice returns to "awaiting upload" and a new file can be uploaded. A verified scan shows no replace or remove control (immutable).
- What the Subscriber sees is shown as one line for a verified scan ("The Subscriber can open this copy") and, before it, "The Subscriber sees: payment received, invoice copy pending", in words of the page; the subscriber label text itself is not repeated beyond what the task fixes.

### 4.5 Reconciliation (`/finance/invoices/reconcile/`)

A table with an optional **from** and **to** issue date (YYYY-MM-DD, the existing date-input pattern; empty means all): serial, issue date, face amount and amount due against payment date and amount paid, site, statement number, adjustments (`NOCN` numbers and totals), and the exceptions in words. Read only; no export in this plan. On phones the table becomes stacked rows (the `batch` row pattern). No paging, so the page states how many rows it shows (`count`).

### 4.6 Statements (later PR, not built)

The statement routes (B5 PRs 6 to 10: list, detail, PDF, adjust, resend) are not built, and the PDF dependency and the statement email wording are still Coach Ed's. **Left out of this plan's build.** When they exist, the screens are: a list and one statement (number, kind, totals, delivery state), a PDF view (file tile and Preview, as for scans), Adjust (reduction, reason) and Resend. Every statement screen carries the exact label "Internal Billing Statement — Not an Official BIR Receipt or BIR Electronic Invoice." and shows `TEST` when the document is not live. A short plan addendum and its own mockup come with that PR (section 8, HQ-F4).

## 5. Files that change (frontend repository, `apps/hq`)

New files (no conflict with any other session):

- `public/finance-usage.html`, `finance-issuer.html`, `finance-invoices.html`, `finance-reconcile.html`
- `public/assets/js/finance-common.js` (money, pesos-to-centavos, day and age helpers, the combo and dialog helpers, **copied** from `finance.js`, see D5), `finance-usage.js`, `finance-issuer.js`, `finance-invoices.js`, `finance-reconcile.js`
- tests: `finance-usage.test.mjs`, `finance-issuer.test.mjs`, `finance-invoices.test.mjs`, `finance-scans.test.mjs`, `finance-reconcile.test.mjs`

Edited files, and who else touches them:

| File | Change | Shared with |
|---|---|---|
| `public/finance.html` | the Finance sub-navigation row (markup only) | **DASH-1 PR (z8uq9mek5q) edits `finance.html` and `finance.js` for the sender details (comment 1100430000509243 item i)** |
| `public/assets/js/finance.js` | **not edited by HQ-F** | DASH-1 PR (sender details); the FAS and SMS-inbox worktrees have older copies |
| `public/assets/js/menu.js` | current-page rule: Finance is current for every `/finance/` address (today it is `pathname === href`) | HQ Supervisor (menu.js is the HQ app's shared file; the Contacts entry already uses a prefix rule) |
| `public/assets/js/hq.js` | new `NOHq.upload` | HQ Supervisor |
| `public/assets/css/hq.css` | a few classes (sub-navigation, worksheet card, grouped queue heading, exception chips, stacked table on phones) | HQ Supervisor; DASH-1 may add sender-detail classes |
| `next.config.ts` | rewrites for the four new documents (`/finance/usage/`, `/finance/issuer/`, `/finance/invoices/`, `/finance/invoices/reconcile/`) and redirects for the `.html` names | HQ Supervisor (the SMS and Contacts pages added theirs the same way) |
| `README.md` | the Pages table rows | HQ Supervisor |
| `tests/page.mjs` | the `fetch` stub parses `opts.body` as JSON; it must pass a `FormData` body through for the upload test | HQ Supervisor |
| `tests/menu.test.mjs`, `tests/focus.test.mjs`, `tests/rules.test.mjs` | new cases only | HQ Supervisor |

**Order, stated:** one open PR per repository, so these serialise anyway. (1) The DASH-1 frontend PR (z8uq9mek5q) merges first, because it already has HQ's agreement to edit `finance.js` and `finance.html`. (2) HQ-F PRs then branch from the new `origin/main` and add only the sub-navigation to `finance.html`, leaving `finance.js` alone, so there is nothing to merge in either file. (3) Before each HQ-F PR opens, list open frontend PRs and their files (builder brief) and tell the Supervisor of any overlap in `menu.js`, `hq.js`, `hq.css` or `next.config.ts`. If DASH-1 slips behind, HQ-F1 can go first without touching `finance.html` by leaving the sub-navigation out of the Payments page until DASH-1 merges (the new pages carry it; Payments gets the row in HQ-F2). That fallback costs one extra edit to `finance.html` and is the Supervisor's call.

## 6. Tests

Each is a Node test in `apps/hq/tests` driven through `page.mjs` (no new dependency), one file per page, all run by `pnpm test`.

- **Usage:** list loads and pages by cursor; 404 shows the "not switched on" view; admit only offered for `late_over_72h`; dismiss for all; both require a reason (empty, over 300 characters refused in the page, backend refusal words); `not_admissible` and 404 handled; no raw reason or actor is shown for an unknown code; a reload returns to the list.
- **Issuer and books:** current and pending cards; set sends exactly the fields and `cor_date` as a date; verify with and without a note; `already_verified`, `superseded`, `issuer_not_verified`, `serial_range_overlaps` worded; no prefill; no storage use.
- **Register queue and sale:** the five tabs call `queue/` with the right `filter`; groups by paid date; exception chips in words; the exact string "DO NOT issue invoices in a single delayed Monday batch." is present and "WRITE THIS ON PAPER" is present on the sale view; actual issue date starts empty; the serial is never filled from the hint; pesos are sent as centavos; `amount_mismatch` and `duplicate_serial` words; void needs a reason; `TEST` chip shown; no write control after `verified`.
- **Scans:** upload builds a `FormData` with `invoice` and `scan` and no hand-set `Content-Type`; pre-check refuses a wrong type and a file over 8 MB without calling; `duplicate_scan` word; the file is read through `NOHq.file`, held, shown from an object address and released on close (the proof test's pattern); PDF shows the tile; Verify and Reject send `scan` (and `reason`); rejected returns to awaiting upload; no replace after verify.
- **Reconcile:** empty and filled range; amounts, adjustments and exceptions shown; no raw value for an unknown exception.
- **Rules:** extend `rules.test.mjs` so that no served HQ file contains "BIR compliant", "PTI", "EIS", "ESP" or "Django admin" (case-insensitive, word boundaries), and so that every new page is covered by the no-inline-script, no-style, no-storage, no-console, only-`hq.js`-calls-the-backend checks that already run over every file in `public/`. `menu.test.mjs`: Finance is current on `/finance/usage/` and the other new addresses. `focus.test.mjs`: new pages' focus rule.
- **Repository checks:** `pnpm test` in `apps/hq`, `pnpm lint`, `pnpm build` at the root. CI runs them; run them locally first.
- **Backend:** none. This plan adds no backend code. Anything the screens need from the backend (the route gaps in section 3) is a separate backend task.

## 7. Local visual test

- **HQ app:** `http://hq.negosyotest.com:3003/` (local, http, port 3003; `apps/hq/package.json` `dev` uses 3003 and the README gives `pnpm start --port 3003`). It points at the local API by building with `HQ_STAFF_API_BASE=http://ggwp.negosyotest.com:8000/staff/v1`; the backend needs `HQ_HOST=ggwp.negosyotest.com` and `HQ_STAFF_ORIGIN=http://hq.negosyotest.com:3003` (README). Not run in this round.
- **Backend switches for local data (local only; no shared environment is touched):** `BILLING_ENABLED`, `BILLING_USAGE_ENABLED`, `BILLING_INVOICE_REGISTER_ENABLED` on; the private file store configured as the B2a proof store is, for the scan upload. Test data needed: a needs-review usage record of each reason; a confirmed payment so a pending invoice item exists; an issuer profile and a book are entered through the pages themselves.
- **Sign-in:** the packet opens with the staff sign-in email, how to get the code and the authenticator step (ruling "Review packet login block").
- **Packet form (ruling 2026-11-13):** per page, the link first, then exactly what to test: `/finance/usage/`, `/finance/issuer/`, `/finance/invoices/` (queue, one sale, record, upload, verify, reject, void), `/finance/invoices/reconcile/`. Screenshots to the phone as for other packets.
- The scan pages cannot be exercised locally until B5 PR 4 is merged and its store works locally; until then the visual test of 4.4 is the mockup only.

## 8. PR split (frontend repository; one open PR at a time; each ships dark behind the backend switch)

A page is "off" when its backend switch is off: the routes answer 404 and the page shows its not-switched-on card, as the Payments page does today. No frontend flag is added.

| PR | Contents | Needs | Backend state |
|---|---|---|---|
| **HQ-F1** | Finance sub-navigation, `finance-common.js`, **Usage review** and **Issuer and invoice books**, menu rule, rewrites, README rows, tests | Plan approved; mockup of 4.1 and 4.2 approved; DASH-1 PR merged (or the fallback in section 5) | All routes on `main` |
| **HQ-F2** | **Invoice register** queue, one sale, record, void, and **Reconciliation** | HQ-F1; mockup of 4.3 and 4.5 approved | Routes on `main` (B5 PRs 2 and 3) |
| **HQ-F3** | **Scans**: `NOHq.upload`, upload, view, verify, reject, the `page.mjs` change | HQ-F2; **B5 PR 4 (z8uq9mekpt) merged**; mockup of 4.4 approved; Q7 already answered (comment 1100430000509362) | Branch 48f8bab, not merged |
| **HQ-F4 (later, not planned in detail)** | **Statements** screens (4.6) | B5 PRs 6 to 10 merged; Q1, Q2, Q4 decided by Coach Ed; its own plan addendum and mockup | Not built |

Why three and not one: F1 and F2 use routes already on `main` and can be tested and verified on staging now; F3 waits for PR 4; F4 waits for the whole statements build. Each can be deployed and left dark. If PR 4 merges before HQ-F2 opens, **F3 folds into F2** (two PRs, not three), which keeps CI use down. F1 and F2 are not merged because the register needs a verified issuer and a book first, so F1 must be testable alone.

ClickUp tasks: the Supervisor creates the builder tasks (one for F1 to F3, F4 later); this plan creates none.

## 9. What needs a mockup approved by Coach Ed before build

New visual design needs his approval before build (the Supervisor's brief for this plan; the marching instruction lists brand and mockup approval among the things only he gives). Reused components keep the approved look of the Payments page, but the following are **new layouts** and need a mockup, page by page, local and static, shown by link:

1. **Invoice sale page with the WRITE THIS ON PAPER worksheet and the Record form** (4.3). The largest new design: the worksheet in paper-form order and the form beside it.
2. **Grouped queue** (paid-date headings, age, exception chips) and **Reconciliation table with its phone layout** (4.3, 4.5).
3. **Scan verify layout**: scan beside the invoice values, Verify and Reject dialogs (4.4).
4. **Issuer details and books page** (4.2): two cards, the new-details form.
5. **Usage review list and decision dialogs** (4.1): reuses the payments queue and dialogs almost entirely, so it carries the least risk; show it in the same packet.
6. **The Finance sub-navigation** itself (a new element on the existing Payments page).

Recommendation: one mockup packet for items 1 to 6 before HQ-F1 starts, with desktop and phone widths, dark and light theme. Statements (4.6) get their own later.

## 10. Open decisions (each with a recommendation)

| # | Decision | Recommendation |
|---|---|---|
| D1 | **Page structure:** separate documents under `/finance/...` with a sub-navigation, or tabs inside the one `finance.js` page | Separate documents. `finance.js` is already 500 lines and shared with DASH-1; separate files keep each PR small and conflict-free |
| D2 | **Menu:** one Finance entry (current for every `/finance/` address) or one entry per page | One entry plus the sub-navigation, so the side menu stays short and `menu.js` changes by one rule |
| D3 | **PR split:** F1, F2, F3 (F3 folds into F2 if PR 4 is merged first), F4 later | As in section 8 |
| D4 | **Order with DASH-1:** DASH-1 PR merges before HQ-F1 | Yes; fallback in section 5 if it slips |
| D5 | **Shared helpers:** copy `money`, `centavos`, `day`, combo, dialog code into `finance-common.js` rather than extract them from `finance.js` | Copy now (leaves `finance.js` untouched while DASH-1 edits it); a later cleanup PR removes the duplicate once both are merged. Duplicated code is the cost |
| D6 | **Shared HQ files** (`menu.js`, `hq.js`, `hq.css`, `next.config.ts`, `page.mjs`, `README.md`) belong to the HQ Supervisor's app; HQ agreed only to the `finance.js` detail change | The BILLING Supervisor asks the HQ Supervisor through the Manager before HQ-F1 opens, naming the exact small edits in section 5 |
| D7 | **Notice text:** the page holds the "DO NOT issue invoices in a single delayed Monday batch." sentence itself (a test pins the exact string) rather than printing the backend's `notice` field | Page-held, so the exact wording cannot change by a backend edit; the answer's `notice` is ignored (no raw backend text shown, the HQ rule) |
| D8 | **Verify wording:** a confirmation dialog stating the file is legible and matches serial, date and amount, with no tick boxes (the route takes no checklist) | As written in 4.4 |
| D9 | **Record form prefill:** buyer, description, service dates and total are prefilled from the sale and editable; issue date and serial never | As in 4.3. The suggestion is already "WRITE THIS ON PAPER" text; only date and serial have a no-default rule |
| D10 | **Queue limit:** 100 rows, no cursor, a "more exist" line | Accept for launch (few sales); a backend cursor is a later task if the queue grows |
| D11 | **Reconciliation export:** none | None in this plan; ask later if the accountant needs one |
| D12 | **Scan formats in the picker:** JPEG, PNG, PDF only, 8 MB (the Supervisor's routine decision in comment 1100430000509362; webp is not accepted) | As decided |
| D13 | **Statements screens (HQ-F4):** wait for the statement build | Wait; plan them when PRs 6 to 10 exist and Coach Ed has decided the PDF dependency and email wording |
| D14 | **Local port and API host** for the visual test | hq.negosyotest.com:3003 and ggwp.negosyotest.com:8000 as the README says; confirm when the preview runs |

None of these touches money, access control, privacy or legal wording; D6 and the mockup in section 9 are the two that need a person other than the builder.

## 11. Out of this plan

Any backend code or route change; the statement screens' build; the subscriber-side Billing page (DASH-1); the daily alert screen (B5 PR 10, Q9); an HQ export; deleting or editing any record; turning any switch on in a shared environment; any claim of BIR, PTI, EIS or ESP status.
