# Support and Exit Runbook

How staff handle a support request, a data export, and what closes when a site ends (ClickUp z8uq9mekrv, under z8uq9m7yde). It adds no promise: the periods below are the ones the published Terms, Privacy Policy, Cancellation and Refund Policy and Data Deletion page already state. Where this page and a published page differ, the published page is what the customer was told: tell the Supervisor.

## 1. How a request arrives

By email to support@negosyoonline.com or by call or text to +63 917 309 8619 (the Terms, "Support and Complaints", step 1).

- A privacy request (access, correction, deletion, objection) may also arrive at dpo@negosyoonline.com (Privacy Policy, section on privacy rights). It goes into the privacy-request log kept by the operator as part of the NPC practice kit (the spreadsheet described under the privacy manual P2, section 5; NPC Build 5 kit item D2), not in HQ. There is no HQ page for it.
- A deletion request has its own steps: "A deletion request" in `docs/runbooks/retention.md`.

## 2. Reference number

- Format `NS-YYYYMMDD-NN`: the date the request arrived, then a two-digit running number for that date.
- It is given in the acknowledgement reply and kept in the subject line of every later message of the thread.
- The mailbox thread is the support record for a support request (Coach Ed's ruling, relayed 2026-11-13). Nothing is copied to ClickUp or to any other tool. The one exception is a privacy request, which also gets a row in the privacy-request log of section 1.
- A support record never holds a sign-in code, a proof of payment or any payment detail. If a customer sends one, do not quote or forward it.
- Where a command asks for `--request-ref`, give the reference number. It must be a number, never a name or an address: the command refuses an address, because the value is written to the audit record (`apps/accounts/retention.py:101-106`).

## 3. The clock

All of this is the published promise, no more:

| Step | Period | Source |
|---|---|---|
| Acknowledge, with a reference number | within 1 business day | Terms, "Support and Complaints", step 2 |
| Resolve, or explain in writing what we found, what we will do, and when | within 7 business days of the complaint | step 3 |
| Escalation, when the customer replies with the reference number and asks for it | the founder gives a final written response within 7 business days of the escalation request | step 4 |
| Data deletion request | reply within 1 business day, deletion done within 30 calendar days, tell the customer when done | Data Deletion page, "How to ask" |

Business days are Monday to Friday, excluding Philippine public holidays (Terms, end of "Support and Complaints"). The same 1 and 7 business-day periods appear in the Privacy Policy (privacy requests) and the Cancellation and Refund Policy (Complaints).

## 4. A data export

The published pages say: the customer emails support from the account email, and we send an export of the store data (Terms, "Your data after the service ends"; Data Deletion page; Cancellation and Refund Policy). For a Subscriber the download period is 30 calendar days from the end of the service (section 5).

1. Confirm the request comes from the account's registered email. If it does not, do not run anything: reply asking the customer to write from the account email.
2. Give the request its reference number (section 2).
3. Run `export_account` as set out in "An export request" in `docs/runbooks/retention.md`, with the reference number as `--request-ref`. Do not copy the command here; that page is the one that stays current.
4. If the site is inside its 30 days and sold Membership Packs, also run `export_pack_files` for it (retention.md, "The Pack files of an ended site") with the same reference number, and send both files together. For a person's own request on a site that is still served, use `--basis request`, and always before any `dispose_account` of an account that sold Packs.
5. Send the file to the registered email only, from the DPO mailbox, then delete your copy of the file (retention.md, same section: "Sending is done by hand; nothing here sends it").
6. Running it against staging or production needs Coach Ed's word each time, including a dry run, which runs only on a test account named as a test (`docs/runbooks/retention.md`, "What needs Coach Ed's approval, each time and per environment"). The environment's migrations must be applied first ("Before any command here").

The export changes nothing and writes one audit entry, `account_exported`, with counts only. `export_pack_files` writes `pack_files_exported`, counts only.

## 5. What closes when a subscription ends

Only where `BILLING_ENABLED` is on. With it off, no site is limited and the lookup answers 404 for every host (`docs/runbooks/billing.md`, variable table).

A site is "ended" when its subscription is suspended, cancelled after its service end, or cancelled with no payment method (`ENDED_STATES`, `apps/billing/services.py:39`). A cancellation inside the paid period is still served (`apps/billing/services.py:119-124`). A site exempt from billing is served.

When the site is ended:

- **The storefront and the Santé checkout and member routes answer 404**, the same 404 as for a name nobody holds. They all go through `served()` (`apps/sites/api_public.py:54-70`; `apps/storefront/services.py:21-26`; used by `apps/pack/api.py`, `apps/members/api.py`).
- **The owner is limited to Billing.** Owner routes that check the position answer 403 `billing_only` (`_owner_site`, `apps/sites/api.py:145-155`). The routers that use it (through `_owner_site`, or `member_site` which calls it) are sites, sante, catalog, payments, pack, shop, meta, training, media library, member recovery and notices, and inquiries (`config/api.py`). Billing routes and reading the site itself stay available. The route inventory test `RouteInventoryTests` in `apps/sites/tests/test_billing_only.py` lists every route under a site for sites, billing, sante, catalog, payments, pack, shop and meta as turned off or allowed by name. Billing stays available so the owner can pay and reactivate (Terms, "Payment Requires Attention" paragraphs).
- **The windows.** From the end of the service: 30 days to download (`DOWNLOAD_WINDOW`, `apps/billing/plan.py:22`), and deletion due at 90 days (`DELETION_AFTER_PAID`, plan.py:23) or 30 days for a site that never had a successful payment (`DELETION_NEVER_PAID`, plan.py:24). They are set in `apps/billing/services.py:393-408`. The Pack files archive (`export_pack_files`) is refused outside the 30 days and at or after the deletion point. The published periods are the same (Terms, "Your data after the service ends"; Privacy Policy, section 8).
- **Approval and weekly review.** At the deletion date nothing is erased by itself: Coach Ed reviews the due list in HQ weekly and approves a batch by typing its confirmation (`docs/runbooks/retention.md`, "An ended site's data"). The first batch in each environment is one site. A site with an open export or deletion request is held or excluded first. Until the executor is switched on in an environment, a site past its date simply keeps its data.
- **Cancel while suspended:** the day of the suspension stays the end of the service for the data (same published paragraphs).

- **A paid Membership Pack buyer can still see their own order.** Until the deletion point (90 days after the service end, 30 days for a site that never paid) the buyer signs in at the store's `/portal/` and reads their verified order. It is a separate set of routes (`docs/runbooks/pack.md`, "The buyer portal"); the routes above still answer 404. At the point the portal answers the same 404 as for a name nobody holds.

**A hold.** A site an operator has put on hold (`operator_hold`) is not served: the lookup excludes it (`apps/sites/api_public.py:60-63`). Its owner is not limited to Billing by that, because the Billing-only check reads the billing position only (`apps/sites/api.py:145-155`). No published page describes a hold; do not tell a customer more than that the site is off while staff look at it, and take any question about it to the Supervisor.

**What is kept.** Billing and tax records, including Billing Statements and the invoice register, are kept at least 5 years (Privacy Policy, section 8; Data Deletion page, "What we may keep"). No retention rule touches them and a disposal keeps the statement snapshots (`docs/runbooks/retention.md`, "Billing statements and the invoice register"; "What disposal does"). Do not blank or delete them on request before then (Data Deletion page).

## 6. What support staff must never do

- Never read, repeat or forward a sign-in code to anyone.
- Never change an account's email on request alone. The change is made only through the account's own sign-in.
- Never send an export, or any account data, to an address other than the account's registered email.
- Never put a code, a proof or a payment detail into the support record (section 2).
- Never promise a period, a refund or an outcome the published pages do not state. Quote them.
