# Billing Build B2a, PR 4: the Finance page in HQ, where an operator reviews bank transfer proofs (plan)

Status: **plan only, for Coach Ed's approval.** No code, migration, variable or staging action is in this pull request.

ClickUp task: z8uq9me581 (https://app.clickup.com/t/z8uq9me581). Read against backend `origin/main` at `e65fb96` and frontend `origin/main` at `b8bf183`.

## 1. Sources

- Coach Ed, 2026-11-11, coordinator chat: "lets build now the finance page.. other session needs this".
- Coach Ed, 2026-11-11, recorded in comment 1100430000489733 on the task: "There will be no django admin site". "For the payment approval, we will be creating a finance menu under the HQ."
- The task z8uq9me581, "Rules carried from settled rulings" and "Accepted when", and comment 1100430000489390 (the approved B2a plan and its answers, Q11: a wrong amount is rejected and the money is handled outside the system).
- The B2a plan `docs/superpowers/plans/2026-11-11-backend-billing-b2a-bank-transfer-launch-promo.md`, sections 4.4, 5.1, 5.2, 8 and 11 (row 4). This plan is that row 4, in the form of the later ruling.
- Backend code: `apps/billing/{services,models,proofs,plan,api}.py`, `apps/operators/{staff_views,middleware,hq_views,audit,models}.py`, `apps/common/{errors,pagination}.py`.
- Frontend code on `origin/main` (PR 36 is merged): `apps/hq/README.md`, `public/assets/js/hq.js`, `next.config.ts`, `src/lib/hq-env.mjs`, `tests/rules.test.mjs`.

A rule below with no source is marked **choice** (a reversible detail decided here, listed again in section 10) or **question** (section 11).

## 2. What exists, and what this adds

Already built and merged (PRs 87, 88, 89), unchanged by this plan:

- `services.confirm_transfer(payment, operator, amount_received_centavos=…)`, `services.reject_transfer(payment, operator, reason=…, note=…)`, `services.proof_for_operator(payment, operator)`.
- The audit actions `billing_payment_confirmed`, `billing_payment_rejected`, `billing_proof_viewed`, all kept whole, written by those three functions.
- `/staff/v1/` on the hq host: `staff_views.dispatch` checks the one allowed `Origin` before the path, `_gate` requires a session that passed both steps, and every answer is `no-store`, `noindex`, `X-Frame-Options: DENY`.

This plan adds:

1. One read function in `apps/billing/services.py`: `review_queue(status)`.
2. A new file `apps/billing/staff.py`: five Finance routes that call the existing services.
3. One page in the frontend `apps/hq`: `/finance/`.

It changes no settlement rule, no table and no existing route.

## 3. Open points with the HQ/SMS coordinator

`apps/operators/staff_views.py`, `apps/operators/middleware.py`, `/staff/v1/` and the frontend `apps/hq` belong to the HQ/SMS coordinator's hierarchy. This plan reads them and proposes; it edits none until that coordinator answers. Three points are open. Each has the form this plan assumes, so the rest can be read now.

| # | Open point | What the code shows today | Assumed until answered |
|---|---|---|---|
| O1 | How a route is registered | `staff_views.ROUTES` is a dictionary of exact paths, one method each. No path holds an id. The body limit is 2048 bytes | Five exact-path entries are added to `ROUTES`, pointing at functions in `apps/billing/staff.py`. That one edit in `staff_views.py` is made by, or agreed with, the HQ hierarchy. The gate is applied as it is for `me` and `console` |
| O2 | Whether staff roles exist | None. `operators.User` is Django's user; `AdminIdentity` has no role field | Every session that passed both steps may use Finance. See question Q1 |
| O3 | When the console shell lands | Frontend stage A is merged: sign-in pages only. `/` still serves the sign-in document; the README says stage B points it at the console | The Finance page is built as its own document. The "Finance" entry in the console menu is one link, added when the shell exists |

If O1 is answered differently (for example, routes with the payment id in the path), only the addresses in section 5 change. The shapes, codes and rules stay.

## 4. Rules every Finance route keeps

1. **Origin.** `dispatch` refuses any other `Origin` with 403 `origin_not_allowed` before the path is looked at. Unchanged.
2. **Session.** A session that passed both steps, by the same `_gate`: 401 `unauthenticated` with no session, 401 `second_step_required` before the authenticator step.
3. **The operator** passed to the services is `session.identity.user`, the staff `User` the audit entry names.
4. **Closed while off.** Each route first asks `plan.collection_live()`. When it is false the answer is 404 `not_found` and no billing table is read. `collection_live()` is false unless `BILLING_ENABLED` and `BILLING_BANK_TRANSFER_ENABLED` are on, a receiving account is set and a proof store is available. With `HQ_STAFF_ORIGIN` unset the routes do not exist at all, as today.
5. **Order of checks:** origin, path, method, session, switch. A signed-out caller therefore gets 401, not 404, with the switch off. This is the same order `me` and `console` have (**choice**: no change to `dispatch`).
6. **Errors** use the one shape `{"error": {"code", "message", "fields"?}}`. `apps/billing/staff.py` turns an `ApiError` from a service into that body and its status; `dispatch` would otherwise answer 500.
7. **No query string and no id in an address.** The hq app rule is "No number, name, address or code in a page address, a query string". Every Finance route is therefore a `POST` to an exact path with a small JSON body, including the three that only read (**choice**). This also fits `ROUTES` as it is.
8. **Audit.** Confirm, reject and each proof read write the existing entries, inside the same transaction as the change. Listing the queue and reading one payment write no entry (**choice**: they show no proof; the three sensitive actions are the ones the task names).
9. **No personal value leaves that the page does not need.** The storage key, the file hash and the Subscriber's account id are never sent.

## 5. The route contract

Base: `HQ_STAFF_API_BASE`, for example `https://hq-staging.negosyonline.com/staff/v1`. Every call is `POST`, `Content-Type: application/json`, with credentials, through `NOHq.call` (or `NOHq.file` for the proof, section 6.3). Times are ISO 8601 with offset. Money is integer centavos.

### 5.1 Errors every route can answer

| Status | Code | When |
|---|---|---|
| 403 | `origin_not_allowed` | Any other origin |
| 404 | `not_found` | Unknown path; the switch is off; no payment has that id |
| 405 | `method_not_allowed` | Not `POST` |
| 401 | `unauthenticated` | No session |
| 401 | `second_step_required` | The authenticator step is not passed |
| 500 | `server_error` | Anything unexpected |

The page treats 401 as `NOHq.placeFor` does (go to sign-in or verify), and status 0 or 500 as "could not be reached".

### 5.2 A payment, as Finance sees it

```json
{
  "id": "btp_…",
  "status": "submitted",
  "site": {"id": "site_…", "name": "Ana's Store"},
  "plan": {"code": "launch_promo", "name": "Launch Promo", "months": 6},
  "amount_centavos": 499900,
  "currency": "PHP",
  "transferred_on": "2026-11-20",
  "transfer_reference": "…",
  "submitted_at": "…",
  "decided_at": null,
  "decided_by": null,
  "amount_received_centavos": null,
  "reject_reason": null,
  "reject_note": null,
  "proof": {"content_type": "image/jpeg", "size_bytes": 412345},
  "cycle": null
}
```

- `status`: `submitted` (under review), `confirmed`, `rejected`, `replaced`.
- `site.name` is `Site.display_name`. `plan.name` and `plan.months` come from `plan.PLANS`.
- `decided_by` is the staff username once decided. `cycle` is `{"sequence", "start_at", "end_at"}` once confirmed.
- The Subscriber's email is not in this shape. Whether the detail shows it is question Q2.

### 5.3 `POST finance/payments/list/`

Body: `{"status": "submitted", "limit": 20, "cursor": ""}`. `status` is required and is one of the four values. `limit` (1 to 100, default 20) and `cursor` are optional.

200: `{"items": [payment, …], "next_cursor": "btp_…" | null, "under_review_count": 3}`. Items are oldest submitted first, for every status (**choice**: the shared `apps.common.pagination.page`, which pages by row order). `under_review_count` is the number of `submitted` payments, for the menu badge and the page heading.

| Status | Code | When |
|---|---|---|
| 400 | `invalid_request` | `status` missing or not one of the four; `fields.status` |
| 400 | `invalid_cursor` | The cursor is not a payment id |

### 5.4 `POST finance/payments/detail/`

Body: `{"payment": "btp_…"}`.

200: the payment of 5.2 plus:

```json
{
  "subscription": {"state": "trial_active", "trial_end_at": "…", "service_ended_at": null},
  "history": [
    {"code": "submitted", "actor": "owner", "at": "…"},
    {"code": "confirmed", "actor": "operator", "at": "…"}
  ]
}
```

`history` is the payment's `BankTransferEvent` rows, oldest first. `subscription.state` is the stored state.

404 `not_found` for an id that is not a payment. 400 `invalid_request` with `fields.payment` when the body has none.

### 5.5 `POST finance/payments/proof/`

Body: `{"payment": "btp_…"}`.

200: the file itself. Headers: `Content-Type` (`image/jpeg`, `image/png` or `application/pdf`, as stored from the file's first bytes), `Content-Disposition: inline; filename="proof.jpg|png|pdf"`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`. The file has no address of its own: it is read from the private store and streamed in this answer only. Each successful answer writes one `billing_proof_viewed` entry. The proof of a decided or replaced payment can still be read, and is audited the same way.

404 `not_found` for an unknown id. No file is read and no entry is written for one.

### 5.6 `POST finance/payments/confirm/`

Body: `{"payment": "btp_…", "amount_received_centavos": 499900}`. The operator types the amount that arrived; the page never fills it in.

200: the payment, now `confirmed`, with its `cycle`.

| Status | Code | When | What the page does |
|---|---|---|---|
| 400 | `invalid_request` | `amount_received_centavos` missing or not a whole number; `fields` | Shows the field error |
| 409 | `amount_mismatch` | The amount typed is not the amount due | Keeps the payment open; nothing was written |
| 409 | `not_under_review` | The payment was rejected, or replaced by a newer proof | Says so and reloads the queue |
| 409 | `plan_not_offered` | A Launch Promo proof for a site that has since had a paid cycle | Says the promo was already used; the operator rejects it |
| 409 | `cancel_scheduled` | The subscription is cancelled and its paid period still runs | Says so; nothing was written |
| 409 | `paid_ahead` | The next period is already paid | Says so; nothing was written |
| 409 | `past_deletion_date` | The site is past its deletion date | Says so; nothing was written |
| 409 | `billing_exempt` | The site is exempt from billing | Says so; nothing was written |
| 409 | `invalid_transition` | The subscription's state allows no payment | Says so; nothing was written |

**Confirm twice.** A payment already confirmed is answered 200 with the same payment. No second cycle, transition, event or audit entry is written. This holds for a double click, a retry after a lost answer, and two operators at once: the second waits on the subscription's row lock and then sees `confirmed`. (`confirm_transfer`, tested in `test_transfer.py` and `test_concurrency.py`.)

**Confirm against a replaced proof.** Confirm names one payment id. If the Subscriber sent a newer proof while the operator was looking, the old row is `replaced` and the confirm answers 409 `not_under_review`. Nothing is settled. The newer proof is a different row with its own id, which the operator must open and look at before it can be confirmed. An operator can never settle a proof they did not open.

**`plan_not_offered`.** The Launch Promo is bought once. If a site sent a promo proof and another payment for it was confirmed first, the promo proof can no longer be settled. The page offers Reject with the reason `duplicate` preselected (**choice**: preselected, not forced).

### 5.7 `POST finance/payments/reject/`

Body: `{"payment": "btp_…", "reason": "amount_mismatch", "note": ""}`. `reason` is one of `unreadable`, `amount_mismatch`, `not_received`, `wrong_account`, `duplicate`, `other`. `note` is optional, at most 300 characters, and required when the reason is `other`. The Subscriber sees the reason and the note.

200: the payment, now `rejected`. Nothing about the subscription changes.

| Status | Code | When |
|---|---|---|
| 400 | `invalid_request` | Unknown reason (`fields.reason`); note too long, or missing for `other` (`fields.note`) |
| 409 | `not_under_review` | The payment was confirmed or replaced |

Reject twice answers 200 with the payment as first rejected; the second reason and note are not written.

## 6. The Finance page (frontend `apps/hq`)

### 6.1 Address and files

One address, `/finance/` (**choice**: one document that changes view in the browser, as `auth.html` does, because no id may be in a page address). Which payment is open is held in memory only; a reload returns to the queue.

| File | Change |
|---|---|
| `apps/hq/public/finance.html` | New. No inline script, style attribute or style block |
| `apps/hq/public/assets/js/finance.js` | New. Calls only `NOHq.call` and `NOHq.file` |
| `apps/hq/public/assets/js/hq.js` | Adds `file(path, body)` (section 6.3). HQ hierarchy's file |
| `apps/hq/public/assets/css/hq.css` | Finance classes, reusing the existing tokens |
| `apps/hq/next.config.ts` | One rewrite, `/finance/` to `/finance.html`, and its redirect. HQ hierarchy's file |
| `apps/hq/src/lib/hq-env.mjs` | CSP: `img-src 'self' blob:` (section 6.3). HQ hierarchy's file |
| `apps/hq/tests/finance.test.mjs` | New |
| `apps/hq/README.md` | The page row and the rule about the proof |

Nothing is stored in the browser. Nothing is written to the console. No address or query string holds a value.

### 6.2 Screens, states and words

On load the page calls `finance/payments/list/` for `submitted`. A 401 follows `NOHq.placeFor`. A 404 shows the closed state.

**Screen 1: the queue.**

- Heading: "Finance". Sub-heading: "Bank transfer payments".
- Tabs: "Under review (3)", "Confirmed", "Rejected", "Replaced".
- One row per payment: site name, plan name, amount ("PHP 4,999.00"), transfer date, submitted ("11 Nov 2026, 9:02 AM", by `NOHq.when`), and for a payment under review how long it has waited ("2 hours", "3 days").
- Row action: "Review" (under review) or "View" (the others).
- "Load more" when `next_cursor` is set.

| State | Words |
|---|---|
| Loading | "Loading payments…" |
| Empty, under review | "No payments are waiting for review." |
| Empty, other tabs | "No payments here yet." |
| Closed (404) | "Bank transfer payments are not switched on." |
| Not reached (0 or 500) | "Finance could not be reached. Try again." with a "Try again" button |

**Screen 2: one payment.** Opened from a row. Calls `detail/` and then `proof/`.

- "Back to payments".
- Site name and site id. Plan and term ("Launch Promo, 6 months"). **Amount due**, large. Transfer date. The bank's reference, or "Not given". Submitted time. Status.
- The proof: an image is shown in the page. A PDF shows "Open the proof (PDF)", which opens it in a new tab. While loading: "Loading the proof…". If it fails: "The proof could not be loaded." with "Try again".
- History: one line per event ("Submitted by the Subscriber, 11 Nov 2026, 9:02 AM").
- For a decided payment: "Confirmed by {username}, {time}. Paid period: {start} to {end}." or "Rejected by {username}, {time}. Reason: {reason}. {note}". For a replaced one: "The Subscriber sent a newer proof."
- For a payment under review, two actions:

**Confirm.** A field "Amount received (PHP)", empty, and the button "Confirm payment". The page turns what is typed into centavos as text, never as a decimal number. Then one confirmation step in the page: "Confirm PHP 4,999.00 received for {site name}? This starts the paid period and cannot be undone." with "Confirm payment" and "Cancel". The button is disabled while the call runs.

**Reject.** A list "Reason" and a field "Note to the Subscriber (optional)", then "Reject proof". The reasons, in words:

| Code | Words |
|---|---|
| `unreadable` | "The proof cannot be read" |
| `amount_mismatch` | "The amount is not the amount due" |
| `not_received` | "The money has not arrived" |
| `wrong_account` | "Sent to a different account" |
| `duplicate` | "This payment was already counted" |
| `other` | "Another reason (write a note)" |

Messages after an action:

| Answer | Words |
|---|---|
| Confirm 200 | "Payment confirmed. The paid period runs {start} to {end}." |
| Reject 200 | "Proof rejected. The Subscriber can send another." |
| `amount_mismatch` | "That is not the amount due (PHP 4,999.00). If a different amount arrived, reject the proof." |
| `not_under_review` | "This payment is no longer under review. It was decided or replaced by a newer proof." then the queue reloads |
| `plan_not_offered` | "This site already used the Launch Promo. Reject this proof." |
| `cancel_scheduled` | "This subscription is cancelled and its paid period is still running. It cannot be paid for now." |
| `paid_ahead` | "The next period of this site is already paid." |
| `past_deletion_date` | "This site is past its deletion date and can no longer be paid for." |
| `billing_exempt` | "This site is exempt from billing." |
| `invalid_transition` | "This subscription cannot take a payment in its current state." |
| `invalid_request` | The field's own message, beside the field |
| 0 or 500 | "That did not go through. Check the payment before trying again." then `detail/` is called again |

The last row matters: after a lost answer the page re-reads the payment, so the operator sees whether the confirmation was written. Confirming again is safe either way (5.6).

### 6.3 The proof in the browser

The proof must not get an address anyone can copy. The page asks for it with `NOHq.file("finance/payments/proof/", {payment})`, a new function in `hq.js` that makes the same call as `call` and resolves the answer as a blob. `finance.js` shows an image through an object address (`URL.createObjectURL`), which exists only in that tab's memory, and releases it when the payment is closed. For this the CSP gains `blob:` in `img-src` and nothing else. A PDF is opened in a new tab from the same kind of object address; no frame is added to the page (**choice**: the CSP keeps "no frame").

Both changes are in files of the HQ hierarchy (`hq.js`, `hq-env.mjs`) and are part of O1 to agree.

### 6.4 Look approval and the local preview

This is a new screen, so it needs Coach Ed's look approval before it is merged. The approval is asked on the local preview first, then on staging only when staging can show it (section 9).

Local preview, all on this computer:

- Backend: `http://ggwp.negosyotest.com:8000` (local, http, port 8000) with `DEBUG`, `HQ_HOST=ggwp.negosyotest.com`, `HQ_STAFF_ORIGIN=http://hq.negosyotest.com:3003`, `BILLING_ENABLED=1`, `BILLING_BANK_TRANSFER_ENABLED=1`, `BILLING_LAUNCH_PROMO_ENABLED=1`, a made-up test receiving account in `BILLING_TRANSFER_ACCOUNTS`, and `BILLING_PROOF_DIR` pointing at a local folder. All in the ignored local environment file.
- Frontend: `HQ_STAFF_API_BASE=http://ggwp.negosyotest.com:8000/staff/v1 pnpm build`, `pnpm start --port 3003`. Page: `http://hq.negosyotest.com:3003/finance/` (local, http, port 3003).
- Test data, local database only: one local staff identity, and four test sites with one proof each (a JPEG under review, a PDF under review, one confirmed, one rejected), sent through the Subscriber routes.
- The review packet gives the commit, the two addresses above, the test sign-in steps and a checklist: the queue, each tab, an image proof, a PDF proof, a confirmation, a wrong amount, a rejection, the closed state, light and dark theme, a phone-width window.

## 7. Backend files and tests

| File | Change |
|---|---|
| `apps/billing/services.py` | Adds `review_queue(status)`: the payments of one status with site and cycle, after `_require_transfers()`; and `transfer_for_operator(payment_id)`: one payment by public id or 404. Read only |
| `apps/billing/staff.py` | New. The five functions of section 5, the Finance shape of 5.2, the `ApiError` to JSON step, the switch check |
| `apps/operators/staff_views.py` | Five entries in `ROUTES` (O1). HQ hierarchy's file |
| `apps/billing/tests/test_staff_finance.py` | New |
| `docs/runbooks/billing.md` | "The Finance page": what each action does and writes; removes the line under "Not in this runbook yet" |

Tests, written first, on SQLite and PostgreSQL:

- Each route: 403 from another origin; 401 signed out; 401 before the second step; 405 on `GET`; 404 with the switch off, with a query count showing no billing table is read.
- The queue: only the asked status; oldest first; paging; `under_review_count`; bad status and bad cursor.
- Detail: the shape of 5.2 and 5.4 exactly; no storage key, hash or account id anywhere in the answer; unknown id 404.
- Proof: the bytes and the four headers; one `billing_proof_viewed` entry per read, naming the signed-in staff user; none for an unknown id.
- Confirm: one paid cycle of the right length and amount for Monthly and for the Launch Promo; the audit entry names the signed-in staff user; confirm twice writes nothing more; a replaced payment answers `not_under_review` and settles nothing; `plan_not_offered`; `amount_mismatch`; a non-number amount; each other 409 of 5.6.
- Reject: the subscription rows are unchanged; the reason and note are stored; the Subscriber can send another proof; reject twice; every `fields` case.
- Two confirmations at once, and a confirmation against a replacing proof, through the routes (PostgreSQL, as `test_concurrency.py`).
- The existing `test_staff_routes.py` passes unchanged.

Frontend tests (`node --test`): `rules.test.mjs` passes with the new files; `finance.test.mjs` covers each state and each message of 6.2, the centavo conversion (including "4,999", "4999.00", "4999.5" and text), that the confirm button is disabled during a call, and that the object address is released.

## 8. Migrations

None. No table or column is added. The queue filters `BankTransferPayment` by `status`; at launch volume that needs no index. If the table grows, an index on `(status, id)` is a later migration of its own, asked for then.

## 9. Pull requests, in order

| # | Repository | Pull request | Waits for |
|---|---|---|---|
| A | backend | This plan (docs only) | Coach Ed's approval |
| B | backend | `review_queue`, `apps/billing/staff.py`, the tests, the runbook, and the five `ROUTES` entries | A approved; O1 and O2 answered |
| C | frontend | The Finance page, `NOHq.file`, the CSP line, the rewrite | B merged (the contract is fixed); O1 and O3 answered; then Coach Ed's look approval on the local preview before merge |

Each is cut from the latest `origin/main` and lists the open pull requests and their files before it is opened. B is dark: it adds routes that answer 404 while the switch is off.

**What staging can and cannot show.** A proof store exists only for local development (`proofs.available()` needs `DEBUG`). Until the private store is approved and built (question 3 of the B2a plan, still owed), `collection_live()` is false on staging, so the Finance routes answer 404 there and the page shows its closed state. After B and C merge, staging can prove only: the routes are closed, sign-in is still required, and the page loads. A real review on staging needs the private store, a receiving account and the switch, each Coach Ed's separate approval.

## 10. Routine choices in this plan, Coach Ed's to overrule

| # | Choice | Why | Source |
|---|---|---|---|
| C1 | Every Finance route is a `POST` to an exact path with a JSON body | The hq rule forbids values in a query string; `ROUTES` matches exact paths only | `apps/hq/README.md`; `staff_views.ROUTES` |
| C2 | The order of checks stays origin, path, method, session, switch | No change to `dispatch`; a signed-out caller learns nothing new | `staff_views._answer` |
| C3 | Listing and reading a payment write no audit entry; confirm, reject and each proof read do | The task names the operator actions; the three entries exist | Task; `audit.ACTIONS` |
| C4 | The queue is oldest first for every status | The shared pager; the coordinator's brief | `apps/common/pagination.py` |
| C5 | One page address, `/finance/`; the open payment is held in memory | No id in a page address | `apps/hq/README.md` |
| C6 | An image proof is shown in the page from an in-memory object address; a PDF opens in a new tab; the CSP gains only `blob:` for images | The proof gets no address; the page keeps "no frame" | Task: "never a public URL"; `hq-env.mjs` |
| C7 | The operator types the amount received; the page never fills it in | The approved rule: the amount confirmed must equal the amount due | Task; `confirm_transfer` |
| C8 | One confirmation step in the page before confirming | A confirmation cannot be undone | B2a plan 5.2 |
| C9 | On `plan_not_offered` the Reject form preselects `duplicate` | The proof must be rejected; the operator may still change the reason | `_settle_transfer` |
| C10 | After a lost answer the page re-reads the payment | The operator must know whether it was written; a repeat is safe | `confirm_transfer` |
| C11 | `decided_by` shows the staff username | The operator needs to see who decided; it is staff data, shown only to staff | `BankTransferPayment.decided_by` |
| C12 | No index and no migration | Launch volume | Section 8 |

## 11. Questions for Coach Ed

**Q1. Who may confirm and reject payments?** No staff roles exist in the code today. Recommended: at launch, every staff member who passed two-step sign-in may use Finance, and each action is recorded with their name. A separate Finance role is added when HQ roles are built. Risk if approved: any signed-in staff member can settle a payment. Risk if not: Finance waits for the roles build.

**Q2. Does the payment screen show the Subscriber's email?** By your answer to Q11, a wrong amount is rejected and the money is handled outside the system, which means contacting the Subscriber. Recommended: yes, show the account email on the one-payment screen only, never in the queue. It is personal data shown to signed-in staff, and it adds one field to 5.4. If no: the operator finds the Subscriber another way.

**Q3. The look of the Finance page.** It is a new screen and needs your approval on the local preview (section 6.4) before it is merged.

Still owed from the B2a plan and not asked again here: the receiving account, the private proof store with its token and any dependency, the switches in a shared environment. Finance cannot be used on staging or production before those.

The question recorded on the task as open, what confirms a proof between launch and the day the Finance page can be opened, is answered by building this page first; no terminal command is planned.

## 12. Not in this plan

The emails (PR 5). The console shell and its menu (HQ hierarchy). Staff roles. A refund or any money movement. Statements and the BIR register (B5). Any gateway. Any variable, switch, store or production action.

## 13. Approvals asked

1. This plan, with its routine choices (section 10).
2. Answers to Q1 and Q2.

Not approved by approving this plan, each still its own yes: the look of the page (Q3, asked on the preview), every variable and switch in a shared environment, the private proof store, and every production action.
