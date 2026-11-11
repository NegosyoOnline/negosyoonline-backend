# FAS-02: accounting policy matrix and opening-balance design

ClickUp: [z8uq9md5dd](https://app.clickup.com/t/z8uq9md5dd), folder "Financial Accounting System", under FAS-00 `z8uq9md5db`. Date: 2026-11-11. Status: **proposal, not approved.** Nothing here is approved by Coach Ed, and every policy marked "CPA" needs a CPA's confirmation before it is built into a posting rule. This document changes no code, test, migration, switch, seed or deploy, and no staging or production data was read.

Sources: the FAS-02 task text; the FAS-01 inspection report (`docs/superpowers/specs/2026-11-11-fas-01-accounting-inspection.md`, branch `origin/docs/z8uq9md5dc-fas-01-inspection`, PR 110), cited here as "FAS-01 §n"; and the coordinator's rulings of 2026-11-11 (new codebase only, bank transfer with proof is the launch payment, no gateway, no new Django admin, the manual BIR invoice register is Billing B5's). The FAS-02 task text names Xendit balances and 2307 credits; those parts are legacy and are answered in section 3.5.

Labels: **[I]** read in the FAS-01 report or the task text, **[P]** my proposal, **[U]** unknown, not decidable by reading. Tax statements below are my understanding of Philippine rules and were **not verified against a source**; each is flagged "CPA to confirm".

Scope boundary: this document sets the rules the books follow. It does not design accounts or journal lines (FAS-32) or tables, access and deployment (FAS-03). Where I depend on them, section 5 states the assumption.

---

## 0. Decision packet (for the coordinator to bring to Coach Ed one at a time)

These six decisions block the build of the posting engine (FAS-04) or the billing-to-journal mapping (FAS-12). Each has a recommended default. The reasoning and options are in section 2 under the policy number shown.

| # | Decision, in plain words | Blocks | Recommended default | Who decides |
|---|---|---|---|---|
| **D1** | **When does a prepaid subscription count as earned income?** A customer pays PHP 4,999 for 6 months in one transfer (or 2,499 for a month; an annual plan is planned). Do we call it all income on the day we confirm the money, or hold it as "owed service" and release it month by month? | FAS-12, FAS-14 | Hold it and release it in equal monthly slices over the paid term, starting from the service start date, not the payment date. Last slice absorbs any leftover centavo. (P-03, P-04) | Coach Ed; CPA confirms |
| **D2** | **What is one unit of money in the books, and how are splits rounded?** | FAS-04, FAS-12 | Whole centavos in integers, as the rest of the backend does; no fractions anywhere. When an amount is split, every slice is rounded down and the leftover centavos go to the last slice, so the total is always exact. (P-12) | Coach Ed (it is a money rule); FAS-03 owns the column type |
| **D3** | **Which date does a received payment carry in the books?** The Subscriber's transfer date, the operator's confirmation date, or the bank's date. | FAS-04, FAS-12 | The Manila date on which the operator confirms the payment. The Subscriber's transfer date and the bank date are stored as references and compared at reconciliation. (P-05) | Coach Ed; CPA confirms for cut-off |
| **D4** | **Does the ledger compute any tax?** | FAS-12 | No. Journals carry gross amounts only: no tax, VAT or 8% line. A separate "gross receipts" view is built from confirmed payments, labelled an estimate until a CPA approves the rule. (P-20) | Coach Ed and CPA |
| **D5** | **Which money events become a journal automatically?** | FAS-04, FAS-12 | Only a confirmed bank-transfer payment whose amount matches exactly. Everything else (a wrong amount settled outside the system, a refund, a fee, an unidentified deposit) is a manual, reviewed journal with a reason. (P-06, P-07, P-08) | Coach Ed |
| **D6** | **How are mistakes fixed, and when is a month closed?** | FAS-04, FAS-06 | A posted journal is never edited; a mistake is reversed by a new journal that points at the original, then re-entered correctly. The owner closes a month only after the month has ended; reopening needs the owner, a written reason and an audit entry. (P-13, P-14) | Coach Ed |

**Needed before the first live posting, but not before the engine can be built:** the books start date and the opening journal (P-02, section 3), the test-versus-live rule (P-16), and the retention period (P-17). **Needed before any tax number is shown:** P-20 to P-22, CPA only.

---

## 1. How the policy matrix reads

Each row in section 2 gives the options, a recommended default, who decides, and what the choice changes in the ledger. "Routine" means a reversible detail that I or the coordinator may decide and log; "Coach Ed" and "CPA" are owner or professional decisions that must not be taken silently (coordinator ruling, 2026-11-11). Where a row builds on one of the 17 questions of FAS-01 §11.2, it cites it as "FAS-01 Q#".

Facts the policies rest on [I, FAS-01]:

- The only money-in event today is a confirmed `BankTransferPayment`. The amount is on the payment, not on the billing cycle. The operator's confirmation time is stored as `paid_at` and `decided_at`; the Subscriber's claimed date is `transferred_on`. (FAS-01 §1.1)
- Prices: monthly 249,900 centavos; Launch Promo 499,900 centavos for 6 months; an annual plan of 2,499,000 centavos exists only in PR 106's plan. (FAS-01 §2.1, §8)
- Every payment is prepaid: nothing can be owed before payment; no refund, credit note, receivable, expense, bank-account record, tax table or chart of accounts exists. (FAS-01 §4)
- Time zone is Asia/Manila; service periods start at 3:00 PM Manila on the anchor day. (FAS-01 §3.2)
- No real payment can be collected outside a developer machine yet, so the books will probably start empty of sales. [U1]

---

## 2. Policy matrix

### 2.1 Basis, calendar and periods

| ID | Policy | Options | Recommended default | Who decides | What it changes in the ledger |
|---|---|---|---|---|---|
| **P-01** | **Basis of accounting** | (a) Accrual for management accounts, tax worked out separately (the task's statement). (b) Cash basis throughout. (c) Accrual for the books and the tax also accrual. | (a). Management financials are accrual; tax is a separate reconciliation done by or with the CPA (the task text and FAS-01 Q1). | Coach Ed; CPA confirms the tax side | Under (a) the books hold deferred revenue, prepaid costs and accruals, so the ledger has non-cash journals and period-end entries. Under (b) none of those exist. The tax base is then not read off the revenue accounts (see P-20). |
| **P-02** | **Books start date** | (a) The first day of the month in which the first live payment is confirmed. (b) 1 January of the year of the first live payment. (c) The date of the opening journal, chosen freely. | (a), with the opening journal dated that same day. Costs incurred earlier enter only through the opening journal (section 3), not as back-dated entries. CPA to confirm how pre-launch costs reach the tax return, since the tax year is the whole calendar year. | Coach Ed; CPA | Sets the first period and the date of the opening journal. No entry may be dated before it. It is a stored value, not a code constant. |
| **P-09** | **Accounting period and calendar** | (a) Calendar month inside a Jan–Dec year, Manila dates. (b) 4-4-5 weeks. | (a) (task text; FAS-01 Q15). A journal's period is the calendar month of its Manila date. A payment confirmed at 23:30 Manila on the 31st belongs to that day, not to the next UTC day. | Routine (it restates the task text and the `Asia/Manila` setting) | Period is derived from the Manila entry date; a UTC timestamp is stored beside it and never decides the period. |
| **P-14** | **Close and reopen** | (a) Owner closes; reopen only by the owner with a reason. (b) Automatic close after N days. (c) Soft close (warning) then lock. | (a) with two states after open: *closed* (owner may reopen) and *locked* (the year-end; reopen needs a written reason and an audit entry). A month cannot be closed before its last Manila day has passed. A posting dated in a closed period is refused with a clear message rather than moved to another period. | Coach Ed | The posting engine checks the period state in the same transaction. Reports are produced from closed periods; a reopen is itself an audited event. (FAS-01 Q15) |

### 2.2 Revenue

| ID | Policy | Options | Recommended default | Who decides | What it changes in the ledger |
|---|---|---|---|---|---|
| **P-03** | **Revenue recognition for monthly, 6-month Launch Promo and annual terms** | (a) Income when the payment is confirmed. (b) Equal slices over the paid term, one per anniversary month of the cycle. (c) Daily allocation over the exact service period. | (b). A monthly payment is one slice. The Launch Promo (499,900) is six slices: 83,316 × 5 + 83,320. The annual plan (2,499,000) is twelve slices of 208,250. Each slice is recognised in the Manila calendar month in which the slice starts. If service ends early with no refund (a cancellation, a suspension), the unreleased remainder is recognised on the date service ends. A CPA confirms; (c) is the alternative if monthly accuracy by calendar month matters more than simplicity. (FAS-01 Q1) | Coach Ed; CPA | One confirmed payment becomes a *deferred* credit, and a schedule of release journals follows: 1, 6 or 12 per payment. The schedule needs a trigger (a scheduled task, which is a Railway setting change that needs Coach Ed's approval, FAS-01 §6.2) or release at the time the first post of a later period is made. Under (a) there is no schedule and revenue jumps with sales. Under (c) every payment has a daily schedule and monthly sums need sub-centavo handling (P-12). |
| **P-04** | **Early payment (cash before the service starts)** | (a) Treat like any payment and hold in deferred income from confirmation. (b) Hold in a separate "received in advance" balance until the cycle exists. | (a). The code lets a payment made during the trial or before a due date open a cycle that still starts later (`early=True`, FAS-01 §3.2), so the cash date and the service start differ by days. The slices start at the service start (`cycle.start_at`, Manila date), not at the confirmation. | Routine (follows from P-03) | None beyond P-03: the schedule's start date comes from the cycle, the journal date (P-05) from the confirmation. |
| **P-05** | **Which date a received payment carries** | (a) Operator's confirmation date (Manila). (b) The Subscriber's `transferred_on`. (c) The bank statement's credit date, once reconciled. | (a). It is the moment the system knows and cannot be edited by the Subscriber. `transferred_on` is a Subscriber-typed date and must not decide the period. The other two dates are stored and compared at bank reconciliation. At a month or year end, a transfer that the bank shows in the old period but that was confirmed after it is picked up by a reviewed manual adjustment dated in the old period before closing. | Coach Ed; CPA for the cut-off | The journal's entry date and period equal the Manila confirmation date. A mismatch report of "bank date versus journal date" is needed for reconciliation (FAS-08). |
| **P-06** | **What counts as NegosyoOnline revenue** | (a) Only Subscriber payments for subscription terms, and overage when B4 lands. (b) Also anything that passes through for Subscribers. | (a), and nothing belonging to a Subscriber's own buyers, ever (FAS-01 §1.5, Q3). No setup fee or other charge exists in the billing code today, so none is booked; if one is added later it is a new policy row, not an assumption. | Coach Ed | The mapping (FAS-12) has one revenue line family; any source other than a confirmed Subscriber payment is refused as an automatic source. |
| **P-07** | **Overage and write-off of usage (Billing B4)** | (a) Revenue when cash is confirmed. (b) Revenue when the usage line is created, with a receivable. | (a) until credit sales exist; then a write-off of an unpaid usage line has no ledger effect because nothing was booked. FAS-10 (receivables) is held by the coordinator. Revisit when PR 108 lands and the receivable question is opened. | Coach Ed; CPA if (b) | Under (a) no receivable account is needed. Under (b) a receivable, an allowance and a write-off journal are required. |
| **P-08** | **Receipts that do not match, and refunds** | (a) Post only an exactly matching confirmed payment; everything else by hand. (b) Post the received amount whatever it is. | (a). A wrong amount is settled with the Subscriber outside the system (a rule already in the operator flow, FAS-01 Q11) and the confirm is refused (409). A refund does not exist in the product (FAS-01 §4, Q10): if one is ever paid, it is a manual reversal journal with a reason, never an edit. A deposit that appears in the bank with no matching confirmed payment sits in an "unidentified receipts" liability until it is matched or returned, entered by a reviewed manual journal. | Coach Ed | The automatic source is one table row (a confirmed payment). Manual journals need a reason field, a reviewer and an attached evidence reference. A refund policy needs a ClickUp task of its own before a refund feature exists. |

### 2.3 Money, corrections and evidence

| ID | Policy | Options | Recommended default | Who decides | What it changes in the ledger |
|---|---|---|---|---|---|
| **P-12** | **Unit of money and rounding in centavos** | (a) Integer centavos (the backend convention). (b) Decimal pesos with two places or more. | (a). The FAS task text says "Postgres decimal"; FAS-01 §6.4 records that the code uses integer centavos and leaves the choice open. A split never creates a fraction: each slice is the amount divided and rounded down, and the leftover centavos (0 to n−1) go to the last slice, so slices always add up to the payment. A tax estimate (8%) is rounded to the nearest centavo, half up, once per return line, never per payment. A balance column is wider than the 32-bit payment column (FAS-01 §6.4). | Coach Ed (money rule); FAS-03 for the column type | The posting engine accepts only integers; any decimal input is refused. One allocation function is used by every schedule, so the rule exists in one place. |
| **P-13** | **Corrections** | (a) Reversal journal plus a new correct journal. (b) Edit in place while the period is open. | (a) for every posted journal (task text; FAS-01 §9). The reversal carries the original's reference and a reason, is dated on the day it is made unless the original's period is still open (then the same period is allowed), and never deletes or alters the original. A wrongly confirmed payment cannot be undone in billing today (FAS-01 §4); the ledger is corrected, the billing row is not, and that gap goes to the coordinator when it matters. | Coach Ed | A journal has a "reverses" link and a status; drafts are editable, posted rows are not. The same function supplies refunds and write-offs. |
| **P-17** | **Retention of books and source documents** | (a) Never delete. (b) Delete after a legal period. | (a). No retention rule touches billing or tax records already (`docs/runbooks/retention.md`, FAS-01 §6.1). The legal period for books of accounts and receipts is a CPA/legal answer; a commonly quoted period is ten years from the last entry, **not verified here**. The ledger keeps a document reference on every journal and does not delete the file behind it. | CPA / legal; routine for the technical part | An evidence reference on every journal line group; the private-file store for receipts (FAS-01 §6.1). |
| **P-16** | **Test versus live books** | (a) Separate environments: only the production environment holds live books; staging holds a labelled test book that is never copied. (b) One database with a test flag on each journal. | (a), and in addition the book carries an environment label shown on every report, so a staging report cannot be mistaken for a real one. There is no `is_test` flag on billing rows (FAS-01 §3.2), and a staging payment is indistinguishable from a real one except by environment. The posting switch is off everywhere until Coach Ed switches it on in one named environment (FAS-01 Q13). | Coach Ed | A book-level environment field; no import path from staging to production; the opening journal rehearsal (section 3.4) runs only in a test book. |

### 2.4 Costs, assets and owner's money

No expense, bill, asset, loan or owner-draw table exists in the product (FAS-01 §4). Until FAS-09 and FAS-11 are built, these policies are applied through reviewed manual journals.

| ID | Policy | Options | Recommended default | Who decides | What it changes in the ledger |
|---|---|---|---|---|---|
| **P-10** | **Expenses, accruals and prepaid expenses** | (a) Expense when incurred; keep a prepaid balance only above a threshold and over a multi-month term; accrue costs incurred but unpaid at each month end. (b) Expense when paid. | (a). A prepaid cost (a one-year domain, an annual software or hosting plan) above the threshold in P-11 that covers more than one month after the payment month is held as prepaid and released in equal monthly slices from the month of use, rounded as in P-12. A supplier bill dated in the month but unpaid is an accrued liability until paid. Recurring suppliers (hosting, email, SMS) are expensed in the month of service. | CPA for the threshold; routine for the rest | Needs prepaid and accrued-liability account classes (FAS-32) and release schedules of the same kind as P-03. Cash-basis (b) would need neither. |
| **P-11** | **Fixed assets, depreciation and materiality** | (a) Capitalise an item that is above a threshold and lasts more than a year; straight-line, monthly, from the month it is first used; full-month convention; no residual value. (b) Expense everything. | (a). Proposed starting values for the CPA to confirm or change: capitalisation threshold PHP 15,000 per item; computers and phones 3 years; furniture and office equipment 5 years. These numbers are my placeholders, **not** a legal or professional recommendation. Below the threshold: expense. | CPA; Coach Ed for the threshold | An asset register (FAS-11), a monthly depreciation journal, accumulated depreciation as a contra balance. |
| **P-15** | **Development cost (the software NegosyoOnline builds)** | (a) Expense as incurred. (b) Capitalise qualifying development costs as an intangible asset. | (a) for launch: no assessment of capitalisation criteria is on file, the work is continuous and mixed with maintenance, and (a) is the simpler and more conservative choice. A CPA assesses (b) once there is a stable product and costs that can be tracked by project. | CPA | Under (a) no intangible account is used. Under (b) a cost-tracking by project and an amortisation schedule are needed. |
| **P-18** | **Owner capital and drawings** | (a) Contributions to *owner's capital*; withdrawals to *owner's drawings*; neither goes through profit or loss. A business cost paid personally by the owner and not to be repaid is a contribution; one to be repaid is a payable to the owner. (b) Treat owner money as loans. | (a). Each contribution or withdrawal is a reviewed manual journal with the date, the bank record and the owner's written purpose. A transfer between the business receiving account and the owner is never revenue or expense. Which of capital or payable applies to a given payment is the owner's statement at the time, recorded in the journal. | Coach Ed; CPA | Two equity account classes; the year-end close carries profit into capital. A sole proprietor has no share capital, and the capital account is an owner's account, not a shareholder record. |
| **P-19** | **Cash, cash equivalents and bank charges** | (a) Cash is the balance of NegosyoOnline's own receiving accounts (banks and e-wallets named in `BILLING_TRANSFER_ACCOUNTS`); no cash equivalents; bank charges expensed when charged. (b) Include investments as equivalents. | (a). Bank charges are taken from the statement and booked monthly as an expense. A Subscriber pays any transfer fee: the confirmed amount received must equal the amount due exactly (`billing_transfer_confirmed_in_full`), so a fee is never deducted from a receipt. | Routine | One account per receiving account (whether the receiving account becomes a row is FAS-01 Q12); a monthly bank-charge journal from the statement (FAS-08). |

### 2.5 Tax position

All three rows are for the CPA and Coach Ed. I do not decide tax policy; the defaults only keep the books from deciding it.

| ID | Policy | Options | Recommended default | Who decides | What it changes in the ledger |
|---|---|---|---|---|---|
| **P-20** | **Tax position and the 8% base** | (a) The ledger records gross amounts only and computes no tax; the 8% figure is a separate estimate. (b) The ledger posts tax lines. | (a) (FAS-01 Q4). The task states a sole proprietor on the 8% income-tax option, non-VAT. As I understand it (CPA to confirm), the 8% applies to gross sales or receipts above PHP 250,000 for the year, for a taxpayer whose gross is within the PHP 3,000,000 non-VAT limit, in place of graduated rates and percentage tax. The tax base is *gross receipts for the tax*, which can differ from accrual revenue after deferral (P-03): a 6-month payment is a receipt on the day it is received. So the estimate is built from confirmed payments (a payments-based "gross receipts" view), **not** from the revenue accounts. The view is labelled an estimate. | CPA; Coach Ed | No tax account, tax line or tax-code field on any journal. A gross-receipts query on payments, and a threshold watch at PHP 3,000,000 (FAS-20, FAS-23). |
| **P-21** | **Non-VAT status and the invoice register** | (a) Non-VAT; no output tax anywhere. (b) VAT-registered. | (a) (task text). The manual BIR invoice register is Billing B5's (PR 107), not the ledger's; the ledger links to a B5 item by id and does not duplicate it (FAS-01 Q17). The point at which VAT would apply is a regime change (FAS-23) and a new policy, not an edit to this one. | CPA | None. A link field to the B5 item on the payment journal. |
| **P-22** | **Withholding certificates (2307) and credits** | (a) Not tracked now. (b) Tracked from the first certificate. | (a) until a Subscriber withholds; the B5 plan allows a typed withholding on a manual invoice (FAS-01 §8), and that day it becomes a credit register (FAS-21). | CPA | None today (section 3.5). |

### 2.6 Where I could not settle a row

- **Setup fees, refunds, credit notes, chargebacks, receivables, gateway fees:** none exists in the product (FAS-01 §4), so no policy is needed yet. They are listed in P-06 to P-08 as "refused as an automatic source", and each becomes a new row with a ClickUp task when the feature is approved.
- **Early termination without refund:** the remainder-release default in P-03 is my reading of "no refund" in the cancel flow (FAS-01 §4); whether any other forfeiture rule applies to the operator's suspension is [U].
- **Which receiving account got which transfer** is not stored (FAS-01 U2). The bank side of every payment journal therefore cannot be chosen by the engine; see Assumptions about FAS-03 in section 5.

---

## 3. Opening-balance design

### 3.1 What could exist before the first live payment

Nothing in the product shows an opening position [U7]. This is the full list of what could exist; the first column marks which I expect and what evidence each needs. All of it is a question for Coach Ed (and a CPA), not a reading of data.

| Item | Likely? | Evidence that proves it | Where it lands (class) |
|---|---|---|---|
| Owner's money put into the business (cash into a receiving account, or business costs paid personally) | Likely | Bank or e-wallet record; the owner's dated statement | Owner's capital, or payable to the owner (P-18) |
| Bank and e-wallet balances of the business's own receiving accounts on the books start date | Likely, perhaps zero | Bank statement dated on or just after the start date | Cash |
| Equipment bought for the business (computers, phones) | Possible | Receipt or invoice with date and amount | Fixed assets and accumulated depreciation (P-11) |
| Costs paid ahead (annual domain, hosting, software, SMS credit) with service still to run | Possible | Receipt showing the term | Prepaid expenses (P-10) |
| Unpaid supplier bills | Possible | The bill | Accounts payable / accrued |
| Amounts owed to the owner for costs he paid and wants back | Possible | The owner's list with receipts | Payable to owner |
| Capitalised development cost | Not under the default (P-15) | n/a | n/a |
| Deferred revenue from payments received before the books start | Not expected: no real payment can be collected before the proof store is built [U1] | Confirmed payments and their cycles | Deferred revenue, plus the release schedule from P-03 |
| Receivables, loans, tax credits | Not expected (every payment is prepaid, FAS-01 §4) | n/a | n/a |
| A legacy position carried from another business or earlier system (TWC) | **Unknown** [U7]. The ruling is that legacy code and data are not a source. If Coach Ed says a legacy balance really belongs to this sole proprietorship, it enters like any other line with its own evidence; otherwise nothing is imported. | The owner's written statement and the document | Decided when stated |

### 3.2 How it is entered: the two methods

| | **A. One reviewed manual opening journal** | **B. Import with preview, duplicate guard and rollback** |
|---|---|---|
| What it is | The owner (or the operator for him) enters the opening lines in the normal journal form as a draft, attaches each line's evidence, previews the trial balance, and posts. | A CSV template with columns for account, amount in centavos, evidence reference and effective date; validation; a dry run that shows the journal and the trial balance without writing; a duplicate guard on the file hash and batch key; rollback of a batch. |
| Size of the work | No new feature beyond the manual journal (FAS-04), a checklist and a one-time rehearsal. | A parser, validator, preview, batch table, rollback and its own tests: the whole of FAS-07 as written. |
| Fit | The expected opening has a handful of lines. | Useful if the opening has dozens of lines or recurs (a re-import, a restated year). Neither is expected. |
| Duplicate risk | Controlled by a unique opening marker: one posted opening journal per book. | Controlled by file hash and batch key; more machinery to test. |
| Rollback | Before posting: discard the draft. After posting: a reversal journal (P-13) and a new draft. | The same two cases; the importer adds a batch reversal. |

**Recommendation [P]: method A for launch.** It meets every acceptance line of the task with the least new code, and it keeps the owner reading each line. Method B stays on the shelf as FAS-07 and is worth building only if the opening position turns out to be large. Coach Ed decides; if he chooses A, FAS-07 shrinks to the checklist and the rehearsal below.

### 3.3 The rules of the opening journal (both methods)

1. **One opening journal per book**, dated the books start date (P-02), kind *opening*, with a unique marker so it cannot be posted twice. A second attempt is refused and points at the first.
2. **Balanced by construction.** Debits equal credits. The difference between the assets and the liabilities is shown on its own line as the owner's opening equity, with the owner's sign-off. It is never a hidden balancing entry, and it is never a profit or loss line.
3. **Known accounts only.** Every line names an existing account (FAS-32); an unknown account stops the save. No line posts to revenue or expense.
4. **Evidence and effective date on every line.** A line without an evidence reference, a date, and the person who supplied it cannot be posted. The reference points at a file in the private store or a described document; the ledger does not hold the file's content.
5. **Source records unchanged.** The opening journal reads nothing from billing and writes nothing to it. A test proves that billing rows are identical before and after.
6. **Draft, review, post.** The creator cannot be the only reviewer where a second person exists; with one owner, the review is the owner's explicit confirmation of the previewed trial balance. After posting it is immutable.
7. **Reconciliation to evidence.** Cash lines tie to a bank statement dated at or just after the start date; payable lines tie to a bill schedule. The reconciliation is a dated checklist attached to the journal.
8. **Rehearsal first.** The whole procedure is run in a test book (P-16) with invented numbers marked as such, before the live book is touched. No rehearsal data is copied.

### 3.4 Smallest approach for launch [P]

1. Coach Ed states, in writing, what exists on the books start date from the table in 3.1 (a short list; "nothing" is a valid answer and the book then starts at zero).
2. A CPA reviews the list and the start date (checkpoint C2 in section 4).
3. A test-book rehearsal of the opening journal as a draft (needs FAS-04's draft and post only).
4. In the live book, enter the reviewed lines as one draft, preview the trial balance, owner confirms, post.
5. File the evidence checklist with the journal.

If the answer to step 1 is "nothing", there is no opening journal; the first journal in the book is the first confirmed payment.

### 3.5 FAS-02 task items that do not apply today

| Task item | Status | Why |
|---|---|---|
| Inventory of NegosyoOnline **bank/Xendit balances** and gateway clearing | **Bank balances: applicable (section 3.1). Gateway balances: not applicable.** | Launch payment is bank transfer with proof; no gateway is built and Xendit is disapproved (appeal pending) per the rulings. There is no gateway balance, fee or clearing account to import. The legacy Xendit balances belong to the legacy system, which is not a source. If a gateway is approved later, a new opening line and policy row are added then (FAS-13 is held). |
| **2307 tax credits** import | **Not applicable today.** | No withholding exists in the product; the only potential source is a typed withholding on a manual B5 invoice (FAS-01 §8), and no live invoice exists. The credit register is FAS-21 and starts empty. P-22 states when that changes. |
| Mapping of "legacy accounts" | Not applicable by default. | No legacy chart is a source (ruling). If a legacy balance is brought over (3.1, last row), each line is mapped by hand to a FAS-32 account and recorded in the journal's evidence, not by an automatic mapping table. |
| Import templates, dry run, duplicate prevention, rollback | Applicable only under method B. | See 3.2. Under method A the same safeguards are met by the draft preview, the unique opening marker and reversal. |

### 3.6 The task's acceptance lines against this design

| Acceptance line (FAS-02) | Met by |
|---|---|
| Opening debit = credit | Rule 2 (balanced by construction), enforced by the posting engine's balanced-entry check (FAS-04). |
| No unknown account postings | Rule 3. |
| Unchanged source records | Rule 5, with a before-and-after test. |
| Reconciliation to bank statements and payable schedules | Rule 7. |
| Import can be run twice safely | Rule 1 (unique opening marker); under method B also the file hash and batch key. |
| Every imported amount tied to evidence and effective date | Rule 4. |
| CPA approval checkpoints | Section 4. |

---

## 4. CPA and owner approval checkpoints

| # | Checkpoint | Before | What is approved |
|---|---|---|---|
| **C1** | Revenue recognition, journal date and what posts (D1, D3, D5; P-03 to P-08) | Build of FAS-12 and FAS-14 | The mapping from a confirmed payment to journals, with a worked example for 249,900, 499,900 and 2,499,000. |
| **C2** | Books start date and opening position (P-02; section 3) | The opening journal is posted in the live book | The list of items, their evidence and the trial balance preview. |
| **C3** | Fixed-asset threshold, lives, prepaid rule and development-cost treatment (P-10, P-11, P-15) | FAS-11 and the first month close | The numbers in P-11 and the treatment in P-15. |
| **C4** | Tax statements and the gross-receipts estimate (P-20 to P-22) | Any tax number is shown to anyone | The 8% base, the PHP 250,000 deduction, the PHP 3,000,000 watch and the label of the estimate. |
| **C5** | Retention period (P-17) | Production go-live | The period and the owner of the document store. |
| **C6** | Year-end close and reopen rule (P-14) | The first year-end | Who closes, what a reopen needs. |

---

## 5. Assumptions about FAS-32 and FAS-03

These are my assumptions, not decisions. The coordinator reconciles them across the three documents.

**About FAS-32 (chart of accounts, event-to-journal matrix).**
1. There are account classes for: cash (one per receiving account or one pooled account, FAS-32's choice), prepaid expenses, fixed assets with accumulated depreciation, accounts payable and accrued liabilities, payable to owner, unidentified receipts, deferred revenue, owner's capital, owner's drawings, subscription revenue, expenses, bank charges and depreciation.
2. The confirmed-payment journal has two steps: on confirmation, cash against deferred revenue; then, per slice, deferred revenue against subscription revenue. If FAS-32 designs a different shape (for example, recognising in full when the term is monthly) it must still give the same totals as P-03.
3. A distinct line for the owner's opening equity exists, or FAS-32 says which account plays that role (section 3.3, rule 2). I have named no account number or name.
4. No tax account, VAT account or 8% account exists in v1 (P-20).

**About FAS-03 (data model, access, deployment).**
1. Amounts are integers in centavos in a wide column (P-12); a journal line has no decimal.
2. A journal has: an entry date (Manila date), a separate creation timestamp, a kind (normal, opening, reversal, adjustment), a link to the journal it reverses, a reason, an evidence reference, and a unique key per source (the payment's reference for a confirmed payment; an opening marker for the opening journal). Drafts are editable; posted rows are not.
3. A period has a state: open, closed, locked; the posting transaction checks it (P-14). A stored "books start date" per book exists, and no entry is dated before it.
4. A book has an environment label; test and live books are separate (P-16).
5. The bank side of a payment journal needs the receiving account; the payment row does not store it today (FAS-01 U2). FAS-03 must decide whether to add a field to the payment or let the operator choose the account on the confirmation screen; until then, FAS-12 cannot name the bank account on the debit line. This is a dependency I flag to the coordinator.
6. Posting for a confirmed payment happens inside the confirmation's transaction and fails loudly (FAS-01 Q8's default), including when the period is closed. I assumed this; I did not decide it.
7. Owner-only access applies to creating, reviewing, posting, closing and reopening, and the opening journal is created only by the owner capability.

---

## 6. Routine decisions made while writing this document

Listed for the report; each is reversible and open to change.

| What | Why | Source |
|---|---|---|
| Period = Manila calendar month; entry date is the Manila date (P-09) | Restates the task text (calendar year Jan–Dec) and the `Asia/Manila` setting | Task text; FAS-01 §3.2 |
| Early payments start the revenue slices at the cycle start, not the confirmation (P-04) | Follows from P-03 and the cycle's own dates | FAS-01 §3.2 |
| Bank charges expensed monthly from the statement; no fee deducted from a receipt (P-19) | The confirm rule already demands the exact amount | FAS-01 §2.1 |
| Leftover-centavo rule: last slice absorbs it (P-12) | Totals stay exact; the smallest rule | Own proposal |
| Opening journal method A recommended over an import | Fewest new moving parts for an expected handful of lines | Own proposal |
| 8% tax estimate rounded once per return line, half up (P-12) | Keeps rounding out of per-payment rows | Own proposal; CPA to confirm |
| Placeholder numbers in P-11 (PHP 15,000; 3 and 5 years) | Something concrete for a CPA to change | Own proposal, **not** advice |

## 7. Open questions beyond the packet

1. Does Coach Ed want method A or B for the opening (section 3.2)? Recommended: A.
2. Is any amount from the legacy TWC business to be carried into this sole proprietorship's books [U7]? Recommended: no, unless he says so in writing.
3. Does a CPA exist yet, and does the CPA need read access to the books (FAS-01 Q5)? Recommended: a named CPA reviews exports, with no login until FAS-03's access design is approved.
4. Is a mid-year start (P-02, option a) acceptable to the CPA for the tax year? Recommended: ask at C2.

## 8. What I did and did not do

Did: read the FAS-02 task (no comments on it), the FAS-01 report in full, and `CLAUDE.md` and the builder brief. Wrote this document only.

Did not: read staging or production, run a command that writes, touch code, a migration, a switch, a seed, `config/settings.py`, `apps/operators/audit.py` or the MVP scope spec, or decide any tax or accounting policy. Tax statements are unverified. No test or check was run because no code changed.
