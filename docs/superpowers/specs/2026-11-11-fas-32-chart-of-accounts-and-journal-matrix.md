# FAS-32: chart of accounts v1 and the event-to-journal matrix for NegosyoOnline

ClickUp: [z8uq9md624](https://app.clickup.com/t/z8uq9md624), folder "Financial Accounting System" (master FAS-00 `z8uq9md5db`). Date: 2026-11-11.

**Status: approved by Coach Ed on 2026-11-12, as amended by his rulings 7 to 16 (recorded as comments on FAS-00).** The text below has been brought in line with those rulings; where a row or question cites "ruling N", that ruling decides it. Nothing here is built. No code, test, migration, switch, seed or deploy was written or run, and no staging or production data was read. The approval authorises no code, migration, switch, seed or deploy by itself (each stage 2 piece needs its own approved plan), and the accounting policy rows in section 7 that no ruling answers stay open for a CPA.

Sources: the FAS-32 task text (read in full; it is the owner's earlier proposal, written on the legacy TWC code), the FAS-01 inspection report (`docs/superpowers/specs/2026-11-11-fas-01-accounting-inspection.md`, PR 110), `main` at `1af6d25`, and the plans in PRs 106 (Billing B3), 107 (B5) and 108 (B4), read from their branches. Nothing was copied from TWCinc.

## 0. How to read this document

- **[main]** a field, table or function that exists in `main` at `1af6d25`. Paths are `apps/billing/...`.
- **[PR 106]**, **[PR 108]** exists only in that open plan. The row is dependent: it cannot post until that plan is built.
- **[proposed]** my proposal, not in any code or plan.
- **Launch / Later / Held / Dropped** for accounts (section 2.2).
- Money is integer centavos, as everywhere in this backend. The ledger's own column width and rounding are FAS-03's; section 8 states what I assume.

## 1. What changed from the FAS-32 task text

The task was written for a card-and-gateway business. Coach Ed's rulings (2026-11-11) replace that. The re-map:

| Task text | Here |
|---|---|
| `PaymentAttempt.succeeded` is the money-in event | A confirmed `BankTransferPayment` (`confirm_transfer`, `services.py:876`) is the only money-in event [main]. The amount is on the payment, not on the cycle (FAS-01 §1.1). |
| Debit 1030 "Xendit Settlement Receivable" on payment | Debit the bank or e-wallet the Subscriber paid into. Money is in hand at confirmation; there is no clearing step and no gateway. 1030 and 1031 are **Held** until a gateway is approved. |
| E4 payout settlement, the CSV importer, Xendit fee, VAT on fee, CWT on remittance, accounts 5070/5071 | **Dropped for launch.** They exist only for a gateway. No fee is deducted from a manual transfer. 5070 and 5071 are Held. |
| E5 credit note and refund | **Held.** The code has no refund and no credit note; a confirmed payment cannot be undone (FAS-01 §4). 2050 and 4090 are Later. |
| E6 referral and promotional credit, 6040 commissions | **Held.** Not built (`credits.available_credit()` was legacy). PR 107 only names `referral_credit` as a future line kind. |
| E11 unpaid invoice, 1100 receivable | **Held.** Every payment is prepaid; `outstanding_centavos` is hard-coded 0 (`services.py:302`) [main]. See the overage note in section 4.5, because PR 108 changes this. |
| E0 `needs_review` money held by a gateway | Does not exist. The matching rule is: an unconfirmed proof posts nothing. |
| eCash, wallets, member payables | **Dropped.** Not in this backend (FAS-01 §4). |
| Manual BIR invoice register and its tie to journals | Billing B5 (PR 107) owns it. The ledger may link to a B5 invoice by id and never duplicates it. |
| 4015 promo revenue, 2030 deferred revenue, 3010/3020, the 8% note | Kept (section 2), with the open questions in section 7. |

## 2. Chart of accounts v1

### 2.1 Conventions

- A code is a stable four-digit string. A code is never reused or re-meant once posted to. Retiring an account sets an end date; it is never deleted.
- Headers (1000, 2000, 3000, 4000, 5000, 6000, 7000) are not postable. A journal line never points at a header.
- Columns for FAS-03: code, name, type (asset, liability, equity, revenue, expense), normal balance, postable, status, reporting group, effective-from and -to dates. A contra account is marked and keeps its own normal balance.
- Single entity: NegosyoOnline as a sole proprietorship, PHP only. No cost centre at launch.
- I kept the task's codes wherever the account survives, so the owner's earlier proposal stays recognisable. Two codes changed meaning and one is new; each is flagged "changed" or "new" below.

### 2.2 The accounts

Status: **L** = needed at launch; **Later** = real, add when the event first occurs (a new account is an ordinary additive change, so waiting costs nothing); **Held** = exists only for a thing that is not built or not approved; **Dropped** = not needed in this business.

**Assets (normal balance debit)**

| Code | Name | Status | Note |
|---|---|---|---|
| 1010 | Cash on Hand | Later | Only if the owner keeps petty cash. |
| 1020 | Cash in Bank — Security Bank | **L** | The one launch receiving account; holds system key `cash.bank_transfer_default`. Changed: the task's "Operating Bank". Bank names are chart data, not code (ruling 14; §7 Q4). |
| 1021 | E-Wallet Accounts — PHP | Later | Not a launch account (ruling 14): Security Bank is the only receiving account, so `cash.ewallet` is not used at launch. Changed: the task's "Other Bank / Reserve". Added only if a wallet is ever approved as a receiving account. |
| 1030 | Payment Gateway Clearing | **Held** | Gateway only. Reopen with the gateway decision. |
| 1031 | Other Gateway Clearing | Dropped | Merged into 1030 when needed. |
| 1100 | Subscriber Accounts Receivable | **Held** | Credit sales. Only needed if overage is accrued before it is paid (§4.5, Q5). |
| 1110 | Refunds Recoverable / Gateway Disputes | Dropped | Gateway disputes. |
| 1120 | Creditable Withholding Tax (2307) | Later | A Subscriber cannot withhold today: the confirmation requires received = due [main]. Needed only if that rule changes. |
| 1130 | Other Tax Credits | Dropped | No source. |
| 1200 | Prepaid Hosting / Subscriptions | Later | When an annual vendor bill is paid ahead (domains, an annual tool). |
| 1210 | Other Prepaid Expenses | Dropped | Merged into 1200. |
| 1300 | Computer & Office Equipment | Later | Added when the first asset above the capitalisation threshold is bought (FAS-02 P-11; placeholder PHP 15,000, CPA to set). Below it, expense. |
| 1310 | Accumulated Depreciation — Equipment | Later | Contra, credit balance. |
| 1320 / 1330 | Capitalised software and its amortisation | **Held** | Held, matching FAS-02 P-15: software is expensed. |

**Liabilities (credit)**

| Code | Name | Status | Note |
|---|---|---|---|
| 2010 | Accounts Payable — Suppliers | Later | Launch expenses are paid at once, by journal (§5). |
| 2020 | Accrued Operating Expenses | Later | |
| 2030 | Deferred Subscription Revenue | **L** | Every confirmed base payment lands here first (§4). One account for all plans; the plan is on the source row. |
| 2031 | Deferred Other Service Revenue | **Held** | No other service is sold. |
| 2040 | Customer Credits / Unapplied Payments | **Held** | No credit balance or overpayment can exist: the amount must equal the amount due [main]. |
| 2050 | Refunds Payable | Later | When a refund feature exists. |
| 2060 / 2061 | Loans Payable current / long term | Later | |
| 2070 | Payroll & Statutory Payables | Later | No employees stated. |
| 2080 | Other Taxes / Withholding Payable | Later | |
| 2090 | Due to Owner | **new, Later** | For a cost the owner paid personally and means to be repaid (§7 Q8, FAS-02 P-18). Added when first needed. |

**Equity (credit, except drawings)**

| Code | Name | Status | Note |
|---|---|---|---|
| 3010 | Owner Capital Contributions | **L** | Capital in; also the default credit for a business cost the owner paid personally. |
| 3020 | Owner Drawings | **L** | Debit, contra-equity. Not an expense. |
| 3030 | Accumulated Earnings (prior years) | **L** | Receives each closed year's result. How the year is closed is FAS-03/FAS-06. |
| 3040 | Current Year Earnings | **L, derived** | A report line, not an account that takes postings (assumption, §8). |
| 3050 | Opening Balance Equity | **new, L** | Carries the owner's opening equity on its own line in FAS-02's opening journal. |

**Revenue (credit)**

| Code | Name | Status | Note |
|---|---|---|---|
| 4010 | Monthly Subscription Revenue | **L** | Release from 2030 for `plan_code = monthly`. |
| 4015 | Launch Promo (6 Months) Subscription Revenue | **L** | Release from 2030 for `launch_promo`. In the task text. |
| 4020 | Annual Subscription Revenue | Later → after PR 106 | `annual` does not exist in `plan.py:35-39` [main]; [PR 106] adds it. |
| 4030 | Order Overage Revenue | Later → after PR 108 | [PR 108] `ChargeLine` kind `overage_order`. |
| 4040 | Storage Overage Revenue | Later → after PR 108 | [PR 108] kind `overage_storage`. |
| 4050 | Video Overage Revenue | **Dropped** | PR 108 has no video kind. |
| 4060 / 4070 / 4080 | Setup, professional services, other add-on revenue | **Held** | Nothing is sold but the subscription (MVP scope). |
| 4090 | Sales Returns / Refund Allowances | Later | Contra-revenue, debit balance; with refunds. |

Why revenue is split by plan: the owner's task asked for it, and the plan code is already on the payment [main], so the split costs nothing. If the CPA prefers one account with the plan as a tag, the matrix does not change (Q9).

**Cost of service (debit)**

| Code | Name | Status | Note |
|---|---|---|---|
| 5010 | Backend Hosting / Database | **L** | Railway. |
| 5020 | Frontend Hosting / CDN | **L** | Vercel, domains' DNS. |
| 5030 | SMS Delivery | **L** | There is an SMS gateway in the backend. |
| 5040 | Transactional Email Delivery | **L** | Resend. |
| 5050 | Tracking / Courier APIs | **Held** | None used. |
| 5060 | AI & Third-Party API Usage | Later | |
| 5070 | Payment Gateway Processing Fees | **Held** | Gateway only. |
| 5071 | Non-recoverable VAT on Gateway Fees | **Held** | Gateway only. |
| 5080 | Direct Customer Support / Onboarding | Later | |
| 5090 | Other Direct Service Costs | Later | |

**Operating expenses (debit)**

| Code | Name | Status | Note |
|---|---|---|---|
| 6010 / 6020 | Engineering payroll / general payroll | Later | An owner's draw is not wages. |
| 6030 | Sales & Digital Advertising | **L** | |
| 6040 | Sales Commissions / Referrals | **Held** | No commission or referral exists. |
| 6050 | Software & IT Subscriptions | **L** | Tools, domains, mail. |
| 6060 | Admin, Internet, Utilities | **L** | |
| 6070 | Travel / Meetings | Later | |
| 6080 | Accounting, Legal and Professional Fees | **L** | The CPA. |
| 6090 | Licences / Permits / Registration Fees | **L** | BIR and local permits. |
| 6100 | Office Supplies | Later | |
| 6110 / 6120 | Depreciation / Amortisation Expense | Later | With 1300 / 1320. |
| 6130 | Research & Development Expensed | Dropped | Ordinary expenses cover it. |
| 6140 | Bad Debt / Write-offs | **Held** | Only if overage is accrued (§4.5). |
| 6150 | Bank and Transfer Charges | **new, L** | The task had no account for the owner's bank fees; the manual journal in §5 needs one. |
| 6190 | Other Operating Expenses | **L** | |

**Other income and expense**

| Code | Name | Status | Note |
|---|---|---|---|
| 7010 | Bank Interest Income | **L** | Credit. |
| 7020 | Interest Expense | Later | Debit. |
| 7030 | Foreign Exchange Gain / Loss | **Held** | Hosting is billed in USD but the expense is the pesos actually charged, so no gain or loss arises on a card or bank debit. |

Launch needs 20 postable accounts: 1020, 2030, 3010, 3020, 3030, 3050, 4010, 4015, 5010, 5020, 5030, 5040, 6030, 6050, 6060, 6080, 6090, 6150, 6190 and 7010. Accounts marked "Later → after PR" are added by the build that introduces the event.

### 2.3 Tax stance built into the chart

- NegosyoOnline is non-VAT, tax_amount is zero everywhere in the money model [main: no tax column exists]. The chart has **no** output-VAT, input-VAT or percentage-tax account. A future VAT status is a new versioned set of accounts, never a change of these.
- The owner's 8% income tax is the owner's personal tax. It is tracked in a schedule built from gross receipts (FAS-20), not as an expense. **Decided by Coach Ed (ruling 11):** it is an owner's drawing (3020), never a business operating expense, and no tax lines appear in journals; if paid from business funds it is M7 (Dr 3020 / Cr 1020). Whether the 8% schedule and the non-VAT status are right stays for the CPA and the COR (§7 Q6, Q7).

## 3. Journal rules common to every row

These are proposals for FAS-03/FAS-04 to turn into code; they let the matrix be short.

- **Balanced, positive, two-sided.** Each line is a debit or a credit of a positive whole number of centavos on a postable account. Debits equal credits in the journal.
- **Entry date** is a calendar date in Asia/Manila (`settings.py:282`, USE_TZ true). Default for a payment: the Manila date of `decided_at`, with `transferred_on` and `transfer_reference` carried as memo fields (§7 Q3).
- **Idempotency key.** FAS-03 owns the format: `source_type:source_ref:source_event` (FAS-03 §2.3), unique per book (a book is one environment). `source_ref` is the row's `public_id` (for example `btp…`), never a database number [main: `common/ids.py`]. Posting twice with the same key and the same lines is a no-op; the same key with different lines is a hard error. The rule version is a column on the entry, not part of the key, so a later rule version cannot silently post a second entry for the same event; changing a posted result is a reversal plus a new entry. The event names are final: `payment_confirmed` and `revenue_released_<YYYY-MM>`.
- **Source pointer.** Each journal stores source type and source public_id, plus the plan code snapshot and the cycle sequence if any, so a line traces back to a payment.
- **Posted journals are never edited.** A correction is a new journal that reverses it, with a reason.
- **Test data.** Nothing from a non-live environment posts (§6).

## 4. Event-to-journal matrix

Every posting event is "a payment was confirmed" or "a month ended". Amounts below use today's prices in `plan.py:14,35-39` only as worked examples; the ledger takes the amount from the payment row, never from `plan.py` (FAS-01 §1.1).

### 4.1 Source schema each row reads

| Needed value | Where it is | Status |
|---|---|---|
| Payment identity and idempotency | `BankTransferPayment.public_id`, `reference` (unique) | [main] `models.py:158` |
| Cash amount | `amount_received_centavos` (equals `amount_centavos` by constraint `billing_transfer_confirmed_in_full`) | [main] `models.py:160,177,193-200` |
| Plan bought | `BankTransferPayment.plan_code` (`monthly`, `launch_promo`) | [main] |
| Plan version, term | `plan_version`, `term_months` on payment and `BillingCycle`; `price_centavos` on the cycle | [PR 106] |
| Cash date | `decided_at` (operator confirmation); Subscriber's `transferred_on`; bank's `transfer_reference` | [main] |
| Service period | `BillingCycle.start_at`, `end_at` through `payment.cycle` (OneToOne, `models.py:180`) | [main] |
| Which cycle path | `SubscriptionTransition.code` (`first_payment_succeeded`, `renewed`, `recovered`, `reactivated`), key `paid:<reference>` | [main] `services.py:581-584` |
| Usage part of a payment | `usage_centavos`, `usage_line_max_id` on the payment; `ChargeLine` (kind, amount, status, `settled_payment`) | [PR 108] |
| Which receiving account got the money | Nothing, and none is needed at launch: one account, 1020 (ruling 14) | **Not needed;** see §7 Q4 |
| Live or test | Nothing on the payment or subscription | **Missing in main**; the ledger rule is environment-level (§6) |

### 4.1a Accounts the rules post to automatically (`system_key`)

FAS-03's account table has a `system_key`, so a posting rule names a key and never a code. These names are final; FAS-03 adopts them. Every other account is reached only by a manual journal.

| system_key (proposed) | Code | Used by |
|---|---|---|
| `cash.bank_transfer_default` | 1020 | P1–P7: the debit for a confirmed payment, the one launch receiving account (ruling 14, Q4) |
| `cash.ewallet` | 1021 | Not used at launch (ruling 14); added only if a wallet is ever approved as a receiving account |
| `deferred.subscription` | 2030 | P1, P2, P4, P7 credit; R1 debit |
| `revenue.monthly` | 4010 | R1 for `monthly` |
| `revenue.launch_promo` | 4015 | R1 for `launch_promo` |
| `revenue.annual` | 4020 | R1 for `annual` [PR 106] |
| `revenue.overage_order` | 4030 | P4, P5 [PR 108] |
| `revenue.overage_storage` | 4040 | P4, P5 [PR 108] |

If a key has no account, or the account is inactive on the entry date, the posting fails loudly (the confirmation does not settle), as FAS-03 §9 intends.

### 4.2 P1: confirmed payment, monthly plan (1 month)

- **Source row:** `BankTransferPayment`, `status = confirmed`, `plan_code = monthly`. [main]
- **Posts when:** inside the same transaction as `confirm_transfer` (FAS-01 §3.2, proposed default), or never. A failed posting fails the confirmation.
- **Key:** `billing.banktransferpayment:<payment public_id>:payment_confirmed` .
- **Entry date:** Manila date of `decided_at`.
- **Lines (centavos):** Dr 1020 249,900 / Cr 2030 249,900. No revenue yet.
- **Then, month by month (R1, §4.7):** Dr 2030 / Cr 4010.

### 4.3 P2: confirmed payment, Launch Promo (6 months)

- **Source row:** confirmed `BankTransferPayment`, `plan_code = launch_promo`, amount 499,900. [main] One payment, one six-month cycle (`services.py:859-860`).
- **Key:** as P1 (the plan only changes the release account).
- **Lines:** Dr 1020 499,900 / Cr 2030 499,900.
- **Then:** Dr 2030 / Cr **4015** across the cycle (R1).
- **Never:** book the first monthly price as part of the promo. The promo is paid once; the first Monthly payment is a separate P1 at 249,900, with its own cycle starting at the promo's `end_at` [main: `renewed(early=True)`]. No entry is made for the conversion itself: no charge happens without a payment.
- **Worked example:** service 8 Nov 2026 to 8 May 2027 is 181 days (the owner's own example). Earned by 31 Dec 2026: 54 days, `round(499,900 × 54 / 181)` = 149,141 centavos (PHP 1,491.41); still deferred 350,759.

### 4.4 P3: early and late payments (any plan)

The posting is the same Dr bank / Cr 2030. What differs is where the service period sits, which only affects release (R1). Date meanings:

| Case | Path ([main] `_settle_transfer`, `services.py:848`) | Cash date | Release starts |
|---|---|---|---|
| Paid during the 14-day trial | `first_payment_succeeded(early=True)` | confirmation | at the trial end, not at payment: the trial days are kept |
| Paid before the due date | `renewed(early=True)`: the next cycle starts at the running cycle's `end_at` | confirmation | at that `end_at`; the whole amount sits in 2030 until then |
| Paid in the 7 days after a missed due date | `recovered`: the pending cycle becomes paid, `start_at` is the missed due date | confirmation | at the cycle's `start_at`, which is already past: the days already served are released at once in the next release run (see R1, catch-up) |
| Paid after suspension or end | `reactivated`: a new cycle from the payment time | confirmation | at the new cycle's `start_at` |

Early payments never debit revenue before service. A payment that lands in a different calendar year from its service start is the case R1 is built for.

### 4.5 Overage and usage-only payments — dependent on PR 108

All three rows below wait for [PR 108]; nothing exists in `main`.

**P4: base plus overage in one payment.** The frozen amount is the price plus `usage_centavos`, and `confirm_transfer` marks each included `ChargeLine` `settled` with this payment. Source: the confirmed payment and its settled lines.
- **Key:** as P1 (one journal for the payment).
- **Lines, example** (20 orders over the limit at 500 each, 1 started GB at 3,000): Dr 1020 262,900 / Cr 2030 249,900 / Cr 4030 10,000 / Cr 4040 3,000.
- **Check before posting:** `price + sum(settled lines by kind) = amount_received_centavos`. A mismatch posts nothing and is a visible exception.
- **Overage is credited to revenue at confirmation, not to 2030.** The overage is for a period that has already ended (PR 108 counts only closed periods), so the service is delivered. This is the default; the alternative is in Q5.

**P5: usage-only final payment.** `plan_code = usage_only`, no cycle, amount = the lines. Dr 1020 / Cr 4030 and/or 4040 (by line kind). No 2030, no release schedule. Key as above. This is why a posting source is "a confirmed payment", not "a cycle" (FAS-01 §1.1).

**P6: overage write-off.** A `ChargeLine` moving `open → written_off` (reasons `cycle_lapsed`, `after_final_settlement`, `reviewed_not_billable`). **Default: no journal**, because under P4/P5 revenue is recorded only when cash is confirmed, so an unpaid line was never revenue and there is nothing to reverse. Under the accrual alternative in Q5 it becomes Dr 6140 / Cr 1100 for the line amount (4030/4040 is not reduced if the CPA treats it as bad debt). It must never post a credit to revenue.

### 4.6 Annual plan — dependent on PR 106

**P7.** `plan_code = annual`, amount 2,499,000, `term_months = 12` [PR 106]. Dr 1020 2,499,000 / Cr 2030 2,499,000. Release to **4020** over the cycle. Pay-ahead is limited to 30 days before the term ends [PR 106 `RENEWAL_WINDOW`], so a second year is never prepaid in the first week. Key as P1.
- **Worked example:** 8 Nov 2026 to 8 Nov 2027 is 365 days. Earned by 31 Dec 2026: 54 days, `round(2,499,000 × 54 / 365)` = 369,715 centavos (PHP 3,697.15); deferred 2,129,285. That matches the owner's own schedule in the task (PHP 3,697.15 and 21,292.85 deferred).
- Cancellation keeps service to the term's end and refunds nothing [PR 106], so the release simply continues; there is no mid-term write-back.

### 4.7 R1: month-end release of deferred revenue (a time event, no payment)

- **Source rows:** the paid cycle and its payment; a **recognition schedule** row per cycle and month [proposed; FAS-03/FAS-14 model it].
- **Posts when:** after each Manila month ends (a scheduled job needs a Celery beat entry and Railway approval: a `config/settings.py` change that is not part of this PR).
- **Key:** `billing.banktransferpayment:<payment public_id>:revenue_released_<YYYY-MM>` (the month is carried in the event). A rerun is a no-op.
- **Lines:** Dr 2030 `released` / Cr 4010, 4015 or 4020 by `plan_code`. `released` = cumulative earned at month end − already released.
- **Cumulative earned, in whole centavos:** days elapsed counted in Manila calendar days from the date of `start_at` (included) to the date of `end_at` (excluded), over total days in the cycle. `earned(d) = round_half_up(amount × days(d) / total_days)`, and the last month releases whatever remains, so a cycle always totals exactly its amount. The 3:00 PM cutoff (`clock.py`) is ignored: a cycle's first and last calendar dates are whole service days, which reproduces the owner's examples.
- **Monthly worked example:** 8 Nov to 8 Dec 2026, 30 days, 249,900. November 23 days: 191,590. December 7 days: 58,310.
- **Early end of service [proposed, FAS-02 P-03]:** if service ends before the cycle's `end_at` with no refund, the unreleased remainder is released on the date service ends. Under [PR 106] a cancellation keeps service to the term end, so this does not trigger there; it applies only if some later rule ends service early.
- **Catch-up:** if the schedule's earlier months are already closed, the catch-up posts into the first period in state `open` (period states are `open`, `soft_closed` and `locked`; the rules for them are FAS-06's).
- **Trial and not-billable cycles** have no payment and no schedule.
- A cycle whose `end_at` changes after payment (the pending cycle in `recovered()` can be re-ended, `services.py:676-680`) is read at posting time, in the same transaction that created the payment's journal, so the schedule is built from the final cycle.

### 4.8 What must never post

| Row or event | Why |
|---|---|
| `BankTransferPayment` `submitted` | Money not confirmed (FAS-01 E1). |
| `rejected`, `replaced` | No money counted. A replaced proof is superseded by a later one; only a confirmed one posts. |
| Amount mismatch (409 `amount_mismatch`) | No row is written; nothing to post. A mismatch is settled with the Subscriber outside the system and recorded by hand if money moved. |
| A second confirmation of the same payment | Idempotent in billing (`services.py:889-890`) and by key in the ledger. |
| Trial cycle (sequence 0, `not_billable`), a `pending` or `lapsed` cycle | No payment. Lapse does not reverse anything: nothing was booked. |
| Subscription transitions (cancel, suspend, sweep, reactivate without payment) | They move no money. |
| Exempt sites (`billing_exempt`) | They never get a payment (`services.py:363-366`). A manual flag is not a journal. |
| Usage entries and `open` charge lines; held, `needs_review` or `written_off` usage | Not billed, so not revenue (default, Q5). |
| Statements, B5 invoice items, `BillingNotice`, `RenewalConsent`, audit entries | Documents and records, not money. |
| A Subscriber's own sales, orders, funds, their receiving accounts (`SitePaymentAccount`) | Not NegosyoOnline's revenue. No such source exists in `main` (FAS-01 §1.5); if an order table is ever built, it stays out of the ledger's sources. |
| Anything on staging or local databases | Test data (§6). |
| A payment recorded before the books' first date | Opening balances (FAS-02), not a posting. |

## 5. Manual journals at launch

Entered by the owner (the only role, FAS-03), each with a date, a memo, an attachment where there is one, and the same balance and no-edit rules. At the level of accounts:

| # | Event | Debit | Credit |
|---|---|---|---|
| M1 | Capital paid into the business bank | 1020 | 3010 |
| M2 | Expense paid from the business bank | the expense: 5010, 5020, 5030, 5040, 6030, 6050, 6060, 6080, 6090 or 6190 | 1020 |
| M3 | Expense the owner paid personally | the expense | 3010 if the owner does not want it repaid; 2090 Due to Owner if it is to be repaid, by the owner's statement at the time (Q8) |
| M4 | Owner draw / personal withdrawal | 3020 | 1020 |
| M5 | Bank or transfer charge | 6150 | 1020 |
| M6 | Bank interest received | 1020 | 7010 |
| M7 | Owner's income tax paid from business funds (decided, ruling 11: an owner's drawing, not an expense) | 3020 Owner Drawings | 1020 |
| M8 | **Later.** Move between the owner's own accounts (bank to wallet, wallet to bank); one receiving account at launch (ruling 14) | the receiving account | the sending account |
| M9 | **Later.** Reclass of a payment's receiving account; one receiving account at launch (ruling 14, §7 Q4) | the right account | the wrong account |
| M10 | Reversal of a posted journal | the original's credits | the original's debits |

A deposit in the bank that matches no payment and no journal above is **not** posted until the owner says what it is. There is no suspense account in v1, so nothing is parked silently (consistent with the task text: "do not label transfer, expense or owner drawings arbitrarily"). Equipment, loans and vendor bills are Later.

## 6. Test versus live

`BankTransferPayment` and `Subscription` have no test flag [main]. A staging payment and a production payment look the same. So the posting rule must be switched at **environment** level: on only where the environment is the live one, off everywhere else, and no staging entry is ever copied into the books. The name of the switch and the guard are FAS-03's (a new variable in `config/settings.py`, which I do not edit). A ledger test run uses fixtures in its own database only.

## 7. Questions for Coach Ed and a CPA

Each has a recommended default. Rows marked **Decided** are answered by Coach Ed's rulings of 2026-11-12 and cited by ruling number; the others are still open (CPA or Coach Ed).

| # | Question | Recommended default |
|---|---|---|
| Q1 | **Deferred revenue for prepaid terms.** Are the promo and annual payments deferred and released over the service period, and is the monthly plan deferred too? | **Decided: ruling 9 (prepaid income deferred, released by Manila service days).** Defer all plans: every confirmed base payment goes to 2030, then releases. It matches the owner's task text, keeps one rule, and matters mainly at year end. CPA confirms. |
| Q2 | **Day-count and rounding.** Released by calendar days of the service period, cumulative, round half up, last month takes the rest? | **Decided: ruling 9 (release by Manila service days).** Yes (§4.7). It reproduces the owner's PHP 1,491.41 and PHP 3,697.15. |
| Q3 | **Which date books a payment.** Confirmation date, the Subscriber's `transferred_on`, or the bank's own date? | **Decided: ruling 10 (the entry date is the Manila confirmation date).** Confirmation date: it is system-attested and always in an open period; `transferred_on` is only the Subscriber's claim. A transfer made 31 Dec and confirmed 2 Jan falls into January, which a CPA may want in December for the tax year. |
| Q4 | **Which receiving account.** A payment records no account, so the ledger cannot know bank versus wallet. | **Decided: ruling 14.** One receiving account, 1020 "Cash in Bank — Security Bank" (`cash.bank_transfer_default`); no receiving-account field on the payment and no billing change. Bank names are chart data, not code. M8 and M9 are Later. |
| Q5 | **Overage timing and write-offs.** Revenue when the line is created (accrue, with 1100 and 6140) or when the payment is confirmed? | When confirmed (§4.5). No receivable is ever booked; a write-off posts nothing. The CPA may prefer accrual. |
| Q6 | **8% income tax election and non-VAT status.** Is the chart right to have no tax accounts? Who pays the 8% and from what? | **Decided: ruling 11.** The 8% income tax is an owner's drawing (M7: Dr 3020 / Cr 1020), never a business expense, tracked in a separate schedule from gross receipts (FAS-20), with no tax lines in journals. Registration status and thresholds are still verified by the CPA and the COR. |
| Q7 | **Cash versus accrual basis.** Management books are accrual (deferral); the 8% base is gross receipts, which is cash-like. | **Decided: ruling 11 (separate schedule from gross receipts; the ledger is not the tax base).** Keep both: the ledger is accrual, and the tax schedule reads confirmed payment dates. |
| Q8 | **Owner-paid business costs.** Credit capital (3010), or a payable to the owner (2090)? | By the owner's statement at the time of payment (FAS-02 P-18): capital (3010) if not to be repaid, a payable to the owner (2090) if to be repaid. 2090 stays Later. |
| Q9 | **Revenue by plan or one account.** Three revenue accounts for plans, or one with a tag? | Separate accounts (4010, 4015, 4020), as the owner proposed. |
| Q10 | **Account naming and numbering.** The task's codes kept; 1020 changed meaning and 1021 is Later (ruling 14); 6150 and 3050 added; 5070 and 5071 held. | As in §2.2. Since nothing is posted yet, renumbering now is free; after launch it is not. |
| Q11 | **Refunds.** The code refuses to refund; if one is ever paid outside the system, how is it booked? | **Decided: ruling 13 (corrections by reversal only; only the owner closes or reopens a month).** A manual M10 reversal journal with a reason, plus 4090 added when a refund feature exists. |
| Q12 | **Capitalise software and equipment?** | Capitalise an item above a threshold (placeholder PHP 15,000, CPA to set) and expense below it (FAS-02 P-11); 1300, 1310 and 6110 stay Later, added when the first asset is bought. Capitalised software (1320, 1330) stays held (FAS-02 P-15). |
| Q13 | **Foreign-currency vendors.** USD-billed hosting. | Book the pesos actually debited; no 7030. |
| Q14 | **Payment mismatches and the out-of-system settlement.** Posting only exactly-matched confirmed payments. | **Decided: ruling 12 (only exact confirmed bank-transfer payments post automatically, no suspense account).** As §4.8. Anything else is a manual journal. |

## 8. Assumptions about FAS-02 and FAS-03

I stayed inside this document. Where my rows lean on the other two builders' topics, I assumed the following. The coordinator reconciles.

**About FAS-03 (ledger data model, owner-only access, deployment):**
1. Amounts are integer centavos in a wide integer column (not the 32-bit `PositiveIntegerField` the billing payment uses, which tops out near PHP 21.5 million). The default of FAS-01 Q2.
2. A journal has: public id, entry date (Manila), the three source columns (`source_type`, `source_ref`, `source_event`) with the key derived from them, a rule version column (not part of the key), status `draft` or `posted` only (a posted row never changes; "reversed" is derived from the linked reversal entry), a link to the journal it reverses, a reason; lines hold account, debit or credit centavos, and a memo.
3. The account table carries the columns in §2.1; the seed is the Launch accounts here, versioned, with "Later" accounts added by their own change.
4. Payment posting happens in the same transaction as `confirm_transfer`, and fails it if no mapping or open period exists (FAS-01 Q8 default). Release (R1) is a scheduled job, which needs a beat entry; if FAS-03 chooses a different posting design, only §4.2's "Posts when" changes.
5. A recognition schedule is stored per paid cycle and month so release is deterministic and auditable. It may be a table or derivable; the formula in §4.7 is what matters.
6. Only an owner capability can view or enter journals, on every route, with an audit entry per read. Required action names must be declared in `apps/operators/audit.py` (contended by PRs 103/104; I propose none here).
7. Accounts have a parent/header structure in FAS-03, as the headers in §2.1. CI runs PostgreSQL (FAS-03 §2.5), so database-level guards on posted entries can be tested there; nothing here depends on them being untestable.
8. A switch exists and is on only in the live environment (§6). The year is closed by a report (3040 derived) or an explicit close; FAS-03/FAS-06 chooses.

**About FAS-02 (accounting policy matrix, opening balances):**
1. The policy answers in §7 (Q1–Q3, Q5, Q6, Q8, Q12) come from FAS-02's matrix. If FAS-02's recommendation differs, the matrix rows follow the policy, not the other way round.
2. Books start on the first day of the month of the first live confirmed payment (FAS-02 P-02), with one opening journal (kind opening, unique marker) dated that day. Anything earlier (capital, equipment) enters in it, with the owner's opening equity on its own line against 3050.
3. No staging or test payment is back-filled into the books.
4. The first book date, the calendar fiscal year and who may close a period are FAS-02's.

## 9. Out of scope and what this does not do

- No code, schema or migration. The FAS-03 model, the hook in `confirm_transfer` (contended with PR 107's `open_item`) and the beat entry are separate builds.
- No opening balances, no policy decision, no tax computation, no BIR register.
- No read of staging or production: whether any real payment exists is unknown (FAS-01 U1).
- The CPA's review of the policy rows is outstanding.

## 10. Test fixtures the build should carry (so the matrix is checkable)

1. Monthly: 249,900 → P1 journal; R1 for 8 Nov–8 Dec gives 191,590 then 58,310; total 249,900.
2. Promo: 499,900 → 149,141 released by 31 Dec 2026 and 350,759 still deferred; sum of all months exactly 499,900; first Monthly payment is separate at 249,900 from the promo's end.
3. Annual (after PR 106): 2,499,000 → 369,715 by 31 Dec 2026; sum exactly 2,499,000.
4. Early payment in the trial: no release before the trial end.
5. Paid in the grace days (`recovered`): catch-up release in the first open month.
6. Overage (after PR 108): 262,900 splits 249,900 / 10,000 / 3,000; a split that does not sum is refused.
7. Usage-only: no 2030 line and no schedule.
8. Replay of a confirmation: one journal (the same `source_type:source_ref:source_event`). Same key, different lines: error.
9. Rejected, replaced, mismatched, trial, exempt: zero journals.
10. Debits equal credits in every journal; a posted journal cannot be changed.
