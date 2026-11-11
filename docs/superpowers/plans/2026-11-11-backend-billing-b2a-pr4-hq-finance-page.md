# Billing Build B2a, PR 4: the Finance page in HQ, where an operator reviews bank transfer proofs (plan)

Status: **plan only, for Coach Ed's approval.** No code, migration, variable or staging action is in this pull request.

ClickUp task: z8uq9me581 (https://app.clickup.com/t/z8uq9me581). Read against backend `origin/main` at `e65fb96` and frontend `origin/main` at `b8bf183`.

## Answers, 2026-11-11

Coach Ed approved this plan at `e06ed33`: "Approve as recommended" (ClickUp z8uq9me581, comment 1100430000490108).

- **Q1: (a).** Every two-step verified staff identity may confirm and reject, each action audited. No permission table and no migration.
- **Q2: yes.** The Subscriber's account email is on the one-payment answer (`detail/`) only, as `subscriber_email`, never in the queue.
- **Q3: yes** to the one CSP line, `img-src 'self' blob:`.

Conditions of the HQ/SMS coordinator's yes to the lines in its files (relayed 2026-11-11):

For the routes (PR B):

- `staff_views.py` gains the lines of 3.1 exactly as written, and `apps/operators/tests/test_staff_routes.py` stays unchanged.
- A test in `apps/billing/tests` holds that every key of `billing_staff.ROUTES` starts with `finance/` and none equals an existing staff path, because `ROUTES.update` overwrites silently.
- `apps/billing/staff.py` imports nothing from `apps.operators.staff_views` and keeps its own body reader.
- The proof answer is not a streaming response (5.5, corrected).

For the page (PR C), completing 6.3:

- `finance.js` decides by the answer's exact type. `image/jpeg` and `image/png` go into the `<img>`. `application/pdf` alone may be opened in a new tab. Any other type shows "The proof could not be loaded." and is never opened. Tested in `finance.test.mjs`. Reason: a `blob:` address opened in a new tab is a document of the staff app's own origin.
- To be proven on the local preview in Chrome and Safari before the page relies on it: a PDF opens from a `blob:` address under the inherited `default-src 'none'`, and the new tab is not stopped as a pop-up because it opens after the fetch. If either fails, the fallback is a download link, which needs no CSP change. A frame, `object-src` or any second CSP word goes back to the coordinator first.
- `hq.js` `file()` as written in 6.3. Tests for it may be added to `apps/hq/tests/client.test.mjs`, added lines only.
- Stage B of the HQ app is merged (frontend `main` at `264f003`). The final lines for the page:
  - Menu: `apps/hq/public/assets/js/menu.js`, the `MENU` list at lines 7 to 9. One line is added after line 8, `{ href: "/finance/", label: "Finance", icon: "<path of a 24 by 24 line icon>" }`, and nothing else in that file. Finance stays below "Admin console". In `apps/hq/tests/menu.test.mjs` the `deepEqual` on line 12 and the length on line 18 change.
  - `next.config.ts`: in `beforeFiles` after line 20, `{ source: "/finance/", destination: "/finance.html" }`; in `redirects` after line 30, `{ source: "/finance.html", destination: "/finance/", permanent: false }`.
  - The page copies the shell of `apps/hq/public/index.html` (the aside with `<nav class="side-nav" id="side-nav">`, the topbar, the user card `acct-row` and avatar `acct-btn` that start hidden) and loads `config.js`, `hq.js`, `menu.js`, then `finance.js`. The shell's classes are already in `hq.css`.
  - The user card and Sign out are not written by the page. The HQ builder first moves that wiring from `console.js` into a shared script; its name, load order and element ids are sent when it merges.
- PR C is cut from `main` only after PR B is merged and that shared script is merged.

Correction to 3.3 and section 9: the Vercel project `negosyoonline-hq-staging` now exists (z8uq9me128, comment 1100430000490033). It has no name, DNS record, variable or deployment, so the HQ app is still not reachable and no operator can open the page.

Corrections found while building the routes: the registration in `staff_views.py` (3.1) is 17 added lines, not 16, with the text unchanged; and `plan.name` for the promo is "NegosyoOnline Launch Promo (6 Months)", as `plan.py` names it, not "Launch Promo" as the example in 5.2 shows.

## 1. Sources

- Coach Ed, 2026-11-11, coordinator chat: "lets build now the finance page.. other session needs this".
- Coach Ed, 2026-11-11, recorded in comment 1100430000489733 on the task: "There will be no django admin site". "For the payment approval, we will be creating a finance menu under the HQ."
- Coach Ed, 2026-11-11, recorded on the task: "No, wait for HQ". Proofs are confirmed only on the HQ Finance page; no interim terminal command is built or planned.
- The HQ/SMS coordinator's answers of 2026-11-11, relayed by this task's coordinator: how a route is registered, that no staff permission is built, and that the console shell (stage B) is being built now, ahead of the cookie proof, by Coach Ed's ruling on z8uq9me128.
- The SMS inbox design `docs/superpowers/specs/2026-11-11-sms-inbox-hq-design.md`, R3, R18 and sections 4.4 and 7 (`InboxAccess`), for question Q1.
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

## 3. What is agreed with the HQ/SMS coordinator, and the exact lines in its files

`apps/operators/staff_views.py`, `apps/operators/middleware.py`, `apps/operators/checks.py`, `/staff/v1/` and the frontend `apps/hq` belong to the HQ/SMS coordinator's hierarchy. This plan edits **nothing** in `middleware.py` or `checks.py`: the Origin check and the two-step requirement stay the one shared path, and no copy of either is written in `apps/billing`. Every line this plan would add to a file of theirs is written out below and goes to that coordinator, through this task's coordinator, before any edit.

### 3.1 Registration in `apps/operators/staff_views.py` (agreed: added lines only, nothing reordered)

The handlers live in the new `apps/billing/staff.py`, which imports nothing from `apps/operators` but `audit` (through the services) and so cannot create an import cycle. `test_boundary.py` forbids `apps/operators` importing another app's `models`; `apps.billing.staff` is not one.

Added to the imports, after `from apps.accounts.sessions import client_ip`:

```python
from apps.billing import staff as billing_staff
```

Added after the closing brace of `ROUTES`, before `_answer`:

```python


def _for_staff(view):
    """A route another app owns. It is called with the staff user of a session that passed
    both steps, and never without one."""
    def gated(request):
        session, refusal = _gate(request)
        if refusal:
            return refusal
        return view(request, session.identity.user)
    return gated


ROUTES.update(
    {path: ("POST", _for_staff(view)) for path, view in billing_staff.ROUTES.items()}
)
```

`billing_staff.ROUTES` is `{"finance/payments/list/": queue, "finance/payments/detail/": detail, "finance/payments/proof/": proof, "finance/payments/confirm/": confirm, "finance/payments/reject/": reject}`. Each handler takes `(request, operator)`. The session check is `_gate`, the one `me` and `console` use; the Origin check, the method check, the preflight answer, the CORS headers and `X-Frame-Options` are `dispatch` and `_answer`, untouched. A Finance handler reads its own JSON body, with the same 2048-byte limit.

That is 16 added lines and no changed line. Nothing else in `apps/operators` is edited.

### 3.2 Staff permissions

Nothing is built: every two-step verified staff identity is equal today, and Coach Ed has not ruled who may confirm money. It is question Q1 (section 11), with both shapes. Sections 4 to 9 are written for shape (a); what shape (b) adds is in Q1.

### 3.3 The console shell

Stage B of the HQ staff app, the console shell, is being built now by the HQ/SMS coordinator's builder. The Finance page is one page and one menu entry inside that shell. The menu entry is one added line in one place; the file and line are sent with the merged stage B head, and section 6.1 is completed then. The Finance page pull request is cut only after stage B merges. The backend routes do not wait for it.

**When an operator can actually open the page.** Not when these pull requests merge. The HQ app is reachable only after a Vercel project, its name, its DNS, `HQ_STAFF_ORIGIN` on the environment and the cookie proof. The Vercel project `negosyoonline-hq-staging` exists (z8uq9me128, comment 1100430000490033) but has no name, DNS record, variable or deployment; the rest is not approved or run. Until then no operator can open the Finance page anywhere but on a local preview. And if the cookie proof fails, the way the page signs its calls may change, which may mean rework of the Finance page.

## 4. Rules every Finance route keeps

1. **Origin.** `dispatch` refuses any other `Origin` with 403 `origin_not_allowed` before the path is looked at. Unchanged.
2. **Session.** A session that passed both steps, by the same `_gate`, applied in `staff_views.py` (3.1), never by a copy: 401 `unauthenticated` with no session, 401 `second_step_required` before the authenticator step.
3. **The operator** passed to the services is `session.identity.user`, the staff `User` the audit entry names.
4. **Closed while off.** Each route first asks `plan.collection_live()`. When it is false the answer is 404 `not_found` and no billing table is read. `collection_live()` is false unless `BILLING_ENABLED` and `BILLING_BANK_TRANSFER_ENABLED` are on, a receiving account is set and a proof store is available. With `HQ_STAFF_ORIGIN` unset the routes do not exist at all, as today.
5. **Order of checks:** origin, path, method, session, switch. A signed-out caller therefore gets 401, not 404, with the switch off. This is the same order `me` and `console` have (**choice**: no change to `dispatch`).
6. **Errors** use the one shape `{"error": {"code", "message", "fields"?}}`. `apps/billing/staff.py` turns an `ApiError` from a service into that body and its status; `dispatch` would otherwise answer 500.
7. **No query string and no id in an address.** The hq app rule is "No number, name, address or code in a page address, a query string". Every Finance route is therefore a `POST` to an exact path with a small JSON body, including the three that only read (**choice**). This also fits `ROUTES` and `_answer` as they are.
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

200: the file itself. Headers: `Content-Type` (`image/jpeg`, `image/png` or `application/pdf`, as stored from the file's first bytes), `Content-Disposition: inline; filename="proof.jpg|png|pdf"`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`. The file has no address of its own: it is read whole from the private store and sent as the body of this answer only. It is not a streaming response: the whole answer is built inside the handler, so a failure is still answered by `dispatch` with its 500 and its headers. Each successful answer writes one `billing_proof_viewed` entry. The proof of a decided or replaced payment can still be read, and is audited the same way.

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

One address, `/finance/`, a page inside the console shell (**choice**: one document that changes view in the browser, as `auth.html` does, because no id may be in a page address). Page files are under `apps/hq/public/`, scripts under `apps/hq/public/assets/js/`, and every call goes through `window.NOHq` in `hq.js`. Which payment is open is held in memory only; a reload returns to the queue.

| File | Change |
|---|---|
| `apps/hq/public/finance.html` | New. No inline script, style attribute or style block |
| `apps/hq/public/assets/js/finance.js` | New. Calls only `NOHq.call` and `NOHq.file` |
| `apps/hq/public/assets/js/hq.js` | Adds `file(path, body)`, the exact lines of 6.3. HQ hierarchy's file |
| `apps/hq/public/assets/css/hq.css` | Finance classes, reusing the existing tokens |
| `apps/hq/next.config.ts` | One rewrite, `/finance/` to `/finance.html`, and its redirect, in the form stage B gives its pages. HQ hierarchy's file |
| The console shell's menu | One added line, "Finance". File and line: sent with the merged stage B head |
| `apps/hq/src/lib/hq-env.mjs`, `apps/hq/tests/env.test.mjs` | CSP: `img-src 'self' blob:`, the one line of 6.3. HQ hierarchy's files |
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

### 6.3 The proof in the browser: a security change to the staff app's CSP, for Coach Ed

The proof must be seen by the operator and must not get an address anyone can copy. Today's CSP (`buildCsp` in `apps/hq/src/lib/hq-env.mjs`) allows images from the app itself only (`img-src 'self'`) and calls to the one staff address only (`connect-src`). A proof is neither a file of the app nor may it be loaded by an `<img>` pointing at the backend: that would need the backend's address in `img-src`, would send no JSON body, and would put the payment id in an address.

**Chosen, the narrowest that works:** the page fetches the proof through `hq.js` (already allowed by `connect-src`), holds it in the tab's memory, and shows an image from a `blob:` address made by `URL.createObjectURL`. A `blob:` address is a random name that exists only in that tab; it is released when the payment is closed and dies with the tab. A PDF is not shown in the page: "Open the proof (PDF)" opens the same kind of `blob:` address in a new tab, which needs no CSP change and adds no frame.

The exact change, one line of `buildCsp`:

```diff
-    "img-src 'self'",
+    "img-src 'self' blob:",
```

and the same line in the expected list in `apps/hq/tests/env.test.mjs` (line 30).

**What it allows that was refused before:** an `<img>` on any staff page may show an image that script on that page already holds in memory. **What it still refuses:** an image from any other host, any frame, any inline script or style, and a call to any host but the staff address. `connect-src`, `script-src`, `style-src`, `frame-ancestors` and the rest are unchanged. It gives a page no new way to send data out. The risk it adds is small: if a script of the app were ever compromised it could already read what the page reads; `blob:` in `img-src` adds only the ability to display it.

The exact lines added to `apps/hq/public/assets/js/hq.js`, so that `hq.js` stays the only file that calls the backend. After the `call` function:

```js
  /* A private file from the staff routes, as {ok, status, code, blob}. The file is held in this tab's
     memory only; the caller shows it from an object address and releases it. */
  function file(path, body) {
    var opts = { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}) };
    return fetch(cfg.staffApi + "/" + path, opts)
      .then(function (r) {
        if (r.ok) return r.blob().then(function (b) { return { ok: true, status: r.status, code: "", blob: b }; });
        return r.json().catch(function () { return {}; }).then(function (data) {
          var e = data && data.error;
          return { ok: false, status: r.status, code: e && typeof e.code === "string" ? e.code : "", blob: null };
        });
      })
      .catch(function () { return { ok: false, status: 0, code: "", blob: null }; });
  }
```

and `file: file` added to the `window.NOHq` object on the last line. `rules.test.mjs` ("only hq.js calls the backend") holds as it is.

Both files are the HQ hierarchy's. The CSP line is a security change and is Coach Ed's own yes (section 13).

### 6.4 Look approval and the local preview

This is a new screen, so it needs Coach Ed's look approval before it is merged. The approval is asked on the local preview first, then on staging only when staging can show it (section 9). The preview is built on the merged stage B shell.

Local preview, all on this computer:

- Backend: `http://ggwp.negosyotest.com:8000` (local, http, port 8000) with `DEBUG`, `HQ_HOST=ggwp.negosyotest.com`, `HQ_STAFF_ORIGIN=http://hq.negosyotest.com:3003`, `BILLING_ENABLED=1`, `BILLING_BANK_TRANSFER_ENABLED=1`, `BILLING_LAUNCH_PROMO_ENABLED=1`, a made-up test receiving account in `BILLING_TRANSFER_ACCOUNTS`, and `BILLING_PROOF_DIR` pointing at a local folder. All in the ignored local environment file.
- Frontend: `HQ_STAFF_API_BASE=http://ggwp.negosyotest.com:8000/staff/v1 pnpm build`, `pnpm start --port 3003`. Page: `http://hq.negosyotest.com:3003/finance/` (local, http, port 3003).
- Test data, local database only: one local staff identity, and four test sites with one proof each (a JPEG under review, a PDF under review, one confirmed, one rejected), sent through the Subscriber routes.
- The review packet gives the commit, the two addresses above, the test sign-in steps and a checklist: the queue, each tab, an image proof, a PDF proof, a confirmation, a wrong amount, a rejection, the closed state, light and dark theme, a phone-width window.

## 7. Backend files and tests

| File | Change |
|---|---|
| `apps/billing/services.py` | Adds `review_queue(status)`: the payments of one status with site and cycle, after `_require_transfers()`; and `transfer_for_operator(payment_id)`: one payment by public id or 404. Read only |
| `apps/billing/staff.py` | New. `ROUTES` and the five handlers of section 5, each taking `(request, operator)`; the Finance shape of 5.2; the `ApiError` to JSON step; the switch check. It checks no Origin and no session itself |
| `apps/operators/staff_views.py` | The 16 added lines of 3.1 and nothing else. HQ hierarchy's file |
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

None, if Q1 is answered (a). If it is answered (b), one additive migration for the permission table, as Q1 says. No table or column is added. The queue filters `BankTransferPayment` by `status`; at launch volume that needs no index. If the table grows, an index on `(status, id)` is a later migration of its own, asked for then.

## 9. Pull requests, in order

| # | Repository | Pull request | Waits for |
|---|---|---|---|
| A | backend | This plan (docs only) | Coach Ed's approval |
| B | backend | **The routes, dark.** `review_queue`, `transfer_for_operator`, `apps/billing/staff.py`, the tests, the runbook, and the 16 lines of 3.1 in `staff_views.py` | A approved; the HQ/SMS coordinator's yes to the lines of 3.1; Q1 answered. It depends on nothing else of the HQ hierarchy |
| C | frontend | **The Finance page.** One page and one menu entry inside the console shell, `NOHq.file`, the CSP line | B merged (the contract is fixed); stage B of the HQ app merged; the HQ/SMS coordinator's yes to the lines of 6.3; Coach Ed's yes to the CSP change; then his look approval on the local preview before merge |

Each is cut from the latest `origin/main` and lists the open pull requests and their files before it is opened. B is dark: its routes do not exist while `HQ_STAFF_ORIGIN` is unset and answer 404 while the bank transfer switch is off.

**Merged is not usable.** After B and C merge, an operator still cannot review a payment on staging or production. Two separate things are missing, each needing Coach Ed's own approvals:

1. **The HQ app must be reachable:** a Vercel project, its name, DNS, `HQ_STAFF_ORIGIN`, and the cookie proof. The project `negosyoonline-hq-staging` exists, with no name, DNS record, variable or deployment; nothing else is approved or run. A failed cookie proof may mean rework of the Finance page.
2. **Bank transfer must be live:** a proof store exists only for local development (`proofs.available()` needs `DEBUG`). Until the private store is approved and built (question 3 of the B2a plan, still owed), and a receiving account and the switch are set, `collection_live()` is false, the Finance routes answer 404 and the page shows its closed state.

By Coach Ed's ruling of 2026-11-11 ("No, wait for HQ") there is no other way to confirm a proof. Launch payments by bank transfer therefore depend on both.

## 10. Routine choices in this plan, Coach Ed's to overrule

| # | Choice | Why | Source |
|---|---|---|---|
| C1 | Every Finance route is a `POST` to an exact path with a JSON body | The hq rule forbids values in a query string; `ROUTES` matches exact paths only | `apps/hq/README.md`; `staff_views.ROUTES` |
| C2 | The order of checks stays origin, path, method, session, switch | No change to `dispatch`; a signed-out caller learns nothing new | `staff_views._answer` |
| C3 | Listing and reading a payment write no audit entry; confirm, reject and each proof read do | The task names the operator actions; the three entries exist | Task; `audit.ACTIONS` |
| C4 | The queue is oldest first for every status | The shared pager; the coordinator's brief | `apps/common/pagination.py` |
| C5 | One page address, `/finance/`; the open payment is held in memory | No id in a page address | `apps/hq/README.md` |
| C6 | An image proof is shown in the page from an in-memory object address; a PDF opens in a new tab; the CSP gains only `blob:` for images (his own yes, Q3) | The proof gets no address; the page keeps "no frame" | Task: "never a public URL"; `hq-env.mjs` |
| C7 | The operator types the amount received; the page never fills it in | The approved rule: the amount confirmed must equal the amount due | Task; `confirm_transfer` |
| C8 | One confirmation step in the page before confirming | A confirmation cannot be undone | B2a plan 5.2 |
| C9 | On `plan_not_offered` the Reject form preselects `duplicate` | The proof must be rejected; the operator may still change the reason | `_settle_transfer` |
| C10 | After a lost answer the page re-reads the payment | The operator must know whether it was written; a repeat is safe | `confirm_transfer` |
| C11 | `decided_by` shows the staff username | The operator needs to see who decided; it is staff data, shown only to staff | `BankTransferPayment.decided_by` |
| C12 | No index and no migration | Launch volume | Section 8 |

## 11. Questions for Coach Ed

**Q1. Who may confirm and reject payments?** Nothing is built: every staff identity that passed two-step sign-in is equal today. Two shapes:

- **(a) Every verified staff identity** may open Finance, confirm and reject. Each action is audited with who did it. No table, no migration; this plan as written.
- **(b) An explicit per-person permission,** following the one pattern designed for the SMS inbox (`InboxAccess`, R3 and R18 of the SMS design): a row per staff user in a new table, for example `FinanceAccess` with `can_decide` (without it the user may only look), `granted_by` and `granted_at`; granted and revoked by a terminal command that writes an audit entry; a superuser has no access without a row; a signed-in user without a row gets `not_found` on every Finance route. **This needs a table and so a migration** (`billing.0003_finance_access`, additive), a command, two audit actions, and you naming who holds it. It adds about one pull request before B.

Recommended: **(a) for launch**, because the staff identities today are the few you enrolled yourself, every confirmation names its operator in the audit record, and (b) can be added later without changing a route. Choose (b) now if anyone who will hold an HQ sign-in at launch should not be able to confirm money. Risk of (a): any staff member with an HQ sign-in can settle a payment. Cost of (b): one migration and one more pull request before the routes.

**Q2. Does the payment screen show the Subscriber's email?** By your answer to Q11, a wrong amount is rejected and the money is handled outside the system, which means contacting the Subscriber. Recommended: yes, show the account email on the one-payment screen only, never in the queue. It is personal data shown to signed-in staff, and it adds one field to 5.4. If no: the operator finds the Subscriber another way.

**Q3. The CSP change of section 6.3.** One line, `img-src 'self' blob:`, so that a proof image can be shown without an address. Recommended: yes.

**Q4. The look of the Finance page.** It is a new screen and needs your approval on the local preview (section 6.4) before it is merged.

Still owed from the B2a plan and not asked again here: the receiving account, the private proof store with its token and any dependency, the switches in a shared environment. Finance cannot be used on staging or production before those.

## 12. Not in this build

**No terminal command for confirming or rejecting a proof.** By Coach Ed's ruling of 2026-11-11 ("No, wait for HQ") proofs are confirmed only on the HQ Finance page, and no interim command is built or planned. Launch payments therefore depend on the HQ app being reachable: the Vercel project, its name, DNS, `HQ_STAFF_ORIGIN` and the cookie proof.

Also not here: the emails (PR 5). The console shell and its menu (HQ hierarchy). Staff roles. A refund or any money movement. Statements and the BIR register (B5). Any gateway. Any variable, switch, store or production action.

## 13. Approvals asked

1. This plan, with its routine choices (section 10).
2. Answers to Q1 and Q2.
3. The CSP change (Q3).

Not approved by approving this plan, each still its own yes: the look of the page (Q4, asked on the preview), the HQ app's Vercel project, name, DNS, `HQ_STAFF_ORIGIN` and cookie proof, every variable and switch in a shared environment, the private proof store, and every production action.
