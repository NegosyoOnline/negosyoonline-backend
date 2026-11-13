# Buyer portal for paid Membership Pack buyers of a Subscriber site that is off (plan)

Status: **plan only, for approval.** No code, test, migration, variable or staging action is in this branch.

ClickUp task: z8uq9mbk0m (https://app.clickup.com/t/z8uq9mbk0m). Parent: G4 z8uq9mb383, under the BILLING Supervisor's main task z8uq9m8h46. Read against `origin/main` at `02dde96`. The owner decisions are in the companion file `2026-11-13-buyer-portal-z8uq9mbk0m-decisions.md` (blocks A to F).

## 1. Sources and what was not read

- The task z8uq9mbk0m (about 96 KB, read in full, including "Day-76 buyer notice", "Pack personal-data purge", "Test cases"). It has no comments.
- The rulings log z8uq9med6x, all 25 comments (newest 2026-11-13). The ones that bear on this plan: 509378 and 512065 (the manager decides routine things; privacy, legal wording, payments stay Coach Ed's), 509745 (staging holds live data from the first real member: no wipe, no test order on the Santé site), 512065 (staging switches already approved by a ruling are turned on by the manager; production never).
- G4 z8uq9mb383 and its comment 1100430000509626 (O1 to O6). G4b z8uq9mbbkj and P10.1 z8uq9mb22t for the two wording items.
- The new code: `apps/members`, `apps/pack`, `apps/storefront`, `apps/sites/api_public.py`, `apps/billing` (`services.py`, `plan.py`), `apps/pack/retention.py`, `docs/runbooks/retention.md`, `docs/runbooks/support-and-exit.md`, `docs/runbooks/pack.md`.
- The frontend, through `git show origin/main:<path>`: `apps/storefronts/src/lib/site.ts`, `dashboard-route.ts`, `app/dashboard/orders/route.ts`, `apps/site/public/data-deletion.html`, `terms.html`, `cancellation-refund.html`.
- The legacy repository was not read. Every legacy file and line number in the task is treated as a description of intent, re-checked below against the new code.
- UNREAD: the other files of `apps/payments` (receiving accounts) and the whole of `apps/accounts/retention.py`; the plan for the day-90 purge says "check at build" where it depends on them. The export task z8uq9mbu0p was not read (its text is only quoted by the buyer-portal task).

## 2. Why, and what is true today

When a Santé site stops being served, every buyer-side route answers the neutral 404, so a paid Membership Pack buyer cannot sign in or see My Orders.

| Where | What it does today |
|---|---|
| `apps/storefront/services.py:20-26` `served_sante_site` | `served(host)` and the template check. The one test for "this site is on" |
| `apps/sites/api_public.py:54-70` `served` | Active subdomain, site not deleted, no `operator_hold`, and `billing.position(site).storefront_served` |
| `apps/members/api.py:27-31` `_site`, `apps/pack/api.py:40-45` `_site` | Both use `served_sante_site`. Unserved, unknown, foreign host, or switch off: the same 404 |
| `apps/pack/placement.py:104` | `place_pack_order` re-checks `storefront_served` under the Site row lock |
| `apps/billing/services.py:102-130` `position` | `Position(state, storefront_served, billing_only, subscription, at)`. Unknown fails closed (`NOT_SERVED`, which is not `billing_only`) |
| frontend `apps/storefronts/src/lib/site.ts` `siteFor`, `dashboard-route.ts` `dashGet` | A request whose site is not served gets `neutralNotFound()` before anything else runs |

Nothing named `buyer_portal`, `BUYER_PORTAL_READY` or `PROOF_EXPORT_READY_FROM` exists. There is no Django middleware that picks a urlconf by host (the legacy design): host to site is decided by the storefronts server and by the Django calls it makes. So "the route exception" is a **separate, named set of Django routes plus a separate set of storefront pages**, not a widened state set.

### Facts of the new backend that change the legacy task

| Legacy text | New code | Effect on this plan |
|---|---|---|
| "A suspension sets no clock; a suspended site's portal has no end date" | `_end_service` (`services.py:384-390`) is called for suspension and for both cancelled states (`services.py:529, 542, 559, 562`) and sets `download_until` and `deletion_due_at`. Paid before: 90 days. Never paid: 30 days (`plan.py:23-24`, L75). The task text "day 104" is replaced by this (O3) | The portal ends at `deletion_due_at` for a suspended site too (decision A2) |
| "No clock while NegosyoOnline cannot be paid" | Same (R-4a): `_dates_after` answers `(None, None)` for a never-paid site until `plan.collection_live()` | A site with no `deletion_due_at` keeps its portal and is purged by nothing |
| Xendit orders, `paid` status, invoice reference, `SiteXenditCredential` | Xendit is not built. `PAYMENT_OK = (VERIFIED,)` (`constants.py:110`), payment method is always `direct` (bank or e-wallet transfer with proof) | Every Xendit clause of the task is dropped (L69, "paid" status, mismatch alerts, key revoke text) |
| `PlatformSwitch` row `buyer_portal_enabled`, admin form, model `clean()` | No such table. Switches are environment variables read from `config/settings.py`; no Django admin screens are added (decision "no Django admin, staff work in HQ") | The sale gate is a code constant, no switch, no migration (section 6) |
| Django admin classes hiding evidence columns, `OperatorFinanceGrant`, Finance proof view, `finance_resolve_pack_payment` | No Pack admin; the manual-tool task z8uq9mbbkk is a separate task | Dropped from this task; see section 8 for what the purge keeps instead |
| "Test site has no subscription, portal never ends" | There is no `is_test_site`. A `billing_exempt` subscription is always served (`services.py:117`), so the portal is never reached for it | Same effect, no code |
| `SERVED_STATES`, `BILLING_ONLY_STATES`, `deletion_point_reached` | `Position.billing_only`, `Position.subscription`; no deletion-point read exists | One new read, `billing.deletion_point(site, at)` (section 4.1) |
| Registration-age purge (`sweep.py:63-70`) "retired by L52" | The new backend has its own age-based erase, `apps/pack/retention.py` `registration_details`, rule `pack_registration_details`, behind `DOB_ERASE_ENABLED`, "ruling D3" (date of birth and usernames, 90 days after the last status change, never depends on the subscription). No Pack sweep, no reminder job, no registration retry job exists yet | Conflict with L52. Decision B1 |
| `negosyoonline_platform/isolation.py` registry of site-owned tables | `apps/common/tests/test_tenant_isolation/` | A new table is registered there if it has a Site FK |

## 3. The route exception (parts C1, buildable at once)

**Rule.** A request for the buyer portal is answered only by routes that live in one new module, `apps/pack/api_portal.py`, mounted by one line in `apps/storefront/api.py` under `/storefront/v1/portal/`. Nothing in `apps/members/api.py`, `apps/pack/api.py` or `served()` changes: the existing `_site` helpers still answer 404 for a site that is not served. That is the "named branch, never a silent bypass" the ruling asks for. `served()` and `SERVED_STATES` equivalents (`storefront_served`) are untouched.

### 3.1 Which site opens the portal

A new function `portal_site(host)` in `apps/pack/portal.py`. It returns the site only when all of these hold, and otherwise the same 404 shape as an unknown host:

1. `MEMBERS_ENABLED` and `PACK_ENABLED` are on (the portal reads member and pack tables).
2. The host maps to an active subdomain of a site that is not soft-deleted, whose template is Santé, and that has no `operator_hold` (a held site is staff-controlled; fail closed). This needs the host-to-site lookup that `served()` already does. Plan: factor the lookup out of `served()` into `site_for_host(host)` in `apps/sites/api_public.py` (a pure move; `served()` calls it; its tests stay as they are).
3. `billing.position(site).billing_only` is true. That means the subscription is suspended, or cancelled after its service end, or cancelled with no payment method. A served site, an unknown site, a site with no subscription and a read error are not `billing_only`, so they never reach the portal (unknown is not served, G4).
4. The deletion point has not been reached: `now < billing.deletion_point(site, now)`, or `deletion_point` is `None` (no clock started). Section 4.1.

A transient billing read error inside step 3 is caught by `position` (it returns `NOT_SERVED`), so the portal answers the neutral 404 and logs; it never says "deleted".

### 3.2 The routes (all `POST`, behind the storefront credential like the rest of `/storefront/v1/`, `Cache-Control: no-store`, no cookie set by Django)

| Route | Body | Answer |
|---|---|---|
| `portal/site/` | `host` | `{display_name, accent_color, closes_at, banner}` or 404. No owner data, no billing reason, no contact beyond what `site_chrome` already shows publicly |
| `portal/signin/request/`, `resend/`, `accept-email/`, `verify/` | as `/member/signin/*` | Same answers, same pacing and decoys; they call `members.signin` with the portal site (`signin.py` has no served check) |
| `portal/me/` | `host, token` | `{first_name}` only, for a member with a paid order, else 404 |
| `portal/signout/` | `host, token` | `{status}` |
| `portal/orders/` | `host, token` | `{cards, closes_at, banner}`, read-only cards (section 5) |
| `portal/proof/` | `host, token, proof_id` | The buyer's own proof file bytes, same headers as `member_pack_proof` (`private, no-store`, `nosniff`) |

A test lists every route of the portal router and fails if one is added without being named in this table. No portal route writes anything except the member session (sign-in, sign-out), the existing sign-in challenge rows and the existing sign-in audit rows. There is no checkout, no resubmit, no registration details, no contact change, no account page.

### 3.3 How a buyer is identified

- The site comes only from `host`; a session of site A never opens site B (`sessions.find_session` already requires `site_id` and `member.site_id` equal to the site, not revoked, not expired, 30 days).
- A session issued before the suspension stays valid inside the portal. Revoked sessions (contact change, recovery) stay revoked.
- Portal access requires a signed-in Member of this site who owns at least one `PackOrder` on this site with `payment_status in PAYMENT_OK` (verified). A member with only under-review or rejected orders gets the neutral 404 from `me` and `orders` (the sign-in itself answers as it does today, so it does not reveal whether a contact belongs to a paid buyer).
- Order and proof routes also check `order.member_id == session.member_id` and `order.site_id == site.pk`; a mismatch answers the same 404, so ids cannot be enumerated.

## 4. When the portal ends

### 4.1 `billing.deletion_point(site, at=None)` (new read, buildable at once)

Returns the instant the site's data is due for deletion, as `overview()` computes it (`services.py:253-260`): the stored `deletion_due_at`, or, when a time-driven transition is due and the sweep has not yet written it, the date that transition would write (`_dates_after`). Returns `None` when no clock exists (billing exempt, a state that is still served, or a never-paid site before collection is live). It writes nothing and never reads sweep progress, so the cut-off does not depend on sweep lag. It lives in `apps/billing/services.py` beside `overview`, and fails closed in the direction of "not available": an exception makes `portal_site` answer 404.

At `now >= deletion_point` the portal answers the neutral 404 and shows no retained data, including data kept for legal reasons.

### 4.2 Consequences to state (no new question beyond decision A2)

- Day 30 changes nothing: the export window (`download_until`) is not an end of the portal (L42).
- A suspended site now has a clock, so its portal ends at the same instant as its data: 90 days after suspension for a paid site, 30 days for a site that never paid.
- Reactivation before the point clears the dates and the normal site returns; the portal then answers 404 because the site is served again.
- Live4More ends its trial 2026-11-25 17:07 Manila. If no payment method is active it becomes `cancelled_no_payment_method` at that instant; its portal then opens, and, once collection is live, ends 30 days later. The portal must be on staging before that instant.

## 5. Allowed and forbidden data

Allowed, for the buyer's own verified order(s) only, built in a new serializer `apps/pack/portal.py` `card()` (it does not reuse `dashboard.card`, which carries address, receiver mobile and registration actions):

- order number, placed date, amount (centavos and text), payment method label ("Bank or e-wallet transfer"), payment status label, verified date;
- fulfilment: courier label, tracking number, shipped date, delivered date, the four-step progress;
- the buyer's own proofs: `id`, `seq`, submitted time, amount sent, reference number, content type, and the file through `portal/proof/`;
- membership registration status: the label and line from `panels.MEMBER_REG_LABELS` and `member_reg_line`, no form, no `details_form`, no action;
- a neutral banner: "This store is not taking orders." plus the closing instant (section 7). Never the billing reason.

Forbidden, and tested by key-set assertions on the JSON: date of birth, desired and alternate username, delivery address and receiver mobile, full name beyond the first name, email, mobile, the rejection lock dialog and the Subscriber's receiving accounts, the Subscriber's contact details, any Billing figure or state name, other buyers, other sites, commission or earnings data, the registration success evidence, `sante_member_id`.

Any write: new order, checkout step, proof resubmit, details, contact change, account page.

## 6. The L45 sale gate (buildable at once)

L45: the portal is required before any Membership Pack can be sold; unrelated products are not blocked.

- Constant `BUYER_PORTAL_READY = True` in `apps/pack/portal.py`, set to True in the same change that adds the portal routes (as the task says). It is a code constant, not a switch.
- `place_pack_order` (`placement.py:79`, the only creator of a `PackOrder`, called from `checkout_place` in `apps/pack/api.py`) and `checkout_contact` (the entry) refuse with the same 404 as an unserved site while the constant is False. The refusal sits beside the existing `storefront_served` re-check (`placement.py:104`) and before any file is stored. Product orders (`apps/shop`) are never touched.
- There is no operator switch and no `PlatformSwitch` table in the new backend, so no migration and no environment variable. This avoids a real risk: a default-off switch would close the Pack sale on staging, where real members buy, until someone set it.
- The task also tied the sale to `PROOF_EXPORT_READY_FROM`. Not carried over: the export constant guards only the deletion of proof images (section 8), per site, fail-closed. Decision B3.
- Tests: constant False blocks `place_pack_order` and `checkout_contact`; True allows; a `ProductOrder` path is unaffected; a test asserts the constant is True on main (so a revert shows up).

## 7. The banner and the day-76 notice

- **Banner (buildable at once, no wording decision):** inside the portal, from `deletion_point - 14 days` until the point, derived from `deletion_point` alone. Text: "This store stopped taking orders on {service end date}. You can view your order here until {time} Philippine time on {date}. After that this page closes." It makes no statement about deletion, so it is true before the purge exists. Date and time are Manila time; the closing instant is shown exactly (the portal closes at the instant, 3:00 PM when it comes from a paid cycle end).
- **Email (part C3, waits on decisions B and C):** a transactional email to each member with a verified order, 14 x 24 hours before the point, hourly Celery task with its own beat entry, sent once. The record needs idempotence: `EmailMessage` has no key, so a new table `PackBuyerNotice(site FK, member FK, deletion_due_at, sent_at, unique(site, member, deletion_due_at))` is needed. **Migration `apps/pack/migrations/0005_buyer_notice.py`, needs Coach Ed's separate approval** (decision B4). Retries: a failed send is retried every 6 hours while `now < deletion_point`, at most 6 times, and the job re-reads the subscription before each send and sends only while the state is ended and `deletion_point` equals the recorded value. A missed window (outage past the point) sends nothing and logs once. No SMS. The wording is decision C; it names deletion only when the purge ships (C3 is not enabled without the purge).
- The task's operator alerts (`buyer_deletion_notice_missed`, held payment) need an alert table that does not exist here; use `logger.error` with the site pk only, and a line in the runbook. A choice.

## 8. The day-90 purge (part C2, waits on decision B)

What is wanted: at `deletion_point`, the personal fields of the Pack buyers are cleared even for a site that is not served, and a deletion promise is made only once this runs.

Scope for the MVP, by field (the new models, checked against `apps/pack/models.py` and `apps/members/models.py`):

| Table | Cleared at the point | Notes |
|---|---|---|
| `SanteRegistration` | `date_of_birth`, `desired_username`, `alternate_username`; set `details_purged_at` | Same code as `retention.registration_details`, minus the age test; every status, finished or not. Status stays as an audit record; actions on a purged registration are refused (verify at build that `registration.py:78` and `_apply_details` cover every entry; add the missing ones) |
| `SanteRegistrationEvent` | free text (reported text, note), member actor label | append-only queryset update, the one sanctioned bypass already used by account disposal |
| `PackOrder` | `buyer_name` to a placeholder, `delivery_address` to `{}` | |
| `Member` | `delivery_address` to `{}` | name, mobile, email stay (same as the task: they stay until the store-data deletion executor) |
| `CheckoutToken` | every row of the site deleted | including a soft-deleted site |
| `MemberSession.ip_advisory`, `MemberContactAudit.ip_advisory` | to empty | |
| `PackProofSubmission` | sender name, sender account number, sender bank, `ip_advisory`, reference number, transfer time, account snapshot; then the file is deleted and `file_key` cleared | **file deletion only while `PROOF_EXPORT_READY_FROM` allows it** (below). Everything else clears at the point |
| Subscriber receiving accounts (`apps/payments`) | holder name, number, QR image, as `docs/runbooks/retention.md` "What disposal does" already does for an account | UNREAD detail: reuse the disposal helper per site if it exists; otherwise a separate slice. Does not block C1 |

Trigger: an hourly Celery task in the Pack app (own `tasks.py`, own beat entry), behind a new switch `PACK_PURGE_ENABLED` (default off, environment variable; needs Coach Ed's word to turn on per environment, like `RETENTION_ENABLED`). It selects sites whose subscription is in an ended state with `deletion_point` reached, whether or not the site is served, soft-deleted included. Each row under `select_for_update` in a transaction; the work is assignment to empty values, so a second pass changes nothing, and rows created after a restore are purged by the next cycle. Each site's failure is logged with the site pk only and the loop continues. A reactivated site has no `deletion_point`, so nothing runs. A payment in flight does not delay the purge (L53).

Proof images (L65, L67, L71, L72, fail closed): the file is deleted only when the constant `PROOF_EXPORT_READY_FROM` is set to a date that is not later than the start of the site's 30-day export window (the service end), so the export capability existed for the whole window. Today the account export (`apps/pack/retention.py` `export_packs_of`) holds no image bytes, so the constant stays `None`, the images stay in the private store and are not shown by any route (the portal closes at the point, the owner routes answer 403 `billing_only`), and the personal columns still clear. The store-data export extension z8uq9mbu0p sets the constant in the change that adds images to the export.

Dropped from the task, with the reason (nothing is dropped silently; each is for decision B2):
- `PackPurgeRun`, `PackRetentionHold` tables and the holds service (L68): no hold UI exists and there is no Django admin. Until one is wanted, a documented hold is handled by turning `PACK_PURGE_ENABLED` off, which is blunt; or by a later task.
- `finance_resolve_pack_payment`, `OperatorFinanceGrant` use, the Finance proof view, the Pack admin gates (L55, L58, L59, L63): owned by the manual-tool task z8uq9mbbkk and HQ, not by the portal.
- Xendit credential clearing and Xendit alerts (L69): Xendit is not built.
- The task's alert kinds: replaced by log lines (section 7).

## 9. Frontend (separate repository, part F)

Pages on the storefront host, new files only, all `GET` pages with form `POST` handlers in the style of `dashboard/*`:

| Route | What |
|---|---|
| `/portal/` | If the site is portal-open: sign-in (same code-by-email or SMS flow as `dashboard/sign-in`), or the orders page when signed in |
| `/portal/orders/` | The read-only cards, the banner, a sign-out button |
| `/portal/proof/[id]/` | Streams the buyer's own proof through `portal/proof/` |
| `/portal/sign-out/` | `portal/signout/` |

`siteFor` and every existing page stay as they are. The `/portal/*` handlers call `portal/site/` instead of `siteFor`, so a served site's `/portal/` answers the neutral 404 and `/dashboard/*` still answers neutral for an unserved site. A served site must not show the portal: `portal/site/` answers 404 for it. Pages reuse the Santé member CSS shell (`member-base-css.ts`) with no gold focus border (the light blue rule). No new text beyond the banner and the neutral "not taking orders" note. The storefronts tests add: route list fixed, unserved site `/dashboard/*` still neutral, portal shows only fields of the contract.

## 10. Switches

| Name | Default | Effect |
|---|---|---|
| (none for the portal) | n/a | The portal answers whenever `MEMBERS_ENABLED` and `PACK_ENABLED` are on and the site is `billing_only` and before its point. It must never be switchable off after sales (task: "turning the switch off after sales must not lock a paid buyer out") |
| `PACK_PURGE_ENABLED` | off | The hourly purge (part C2). Needs Coach Ed's word per environment |
| `PACK_BUYER_NOTICE_ENABLED` | off | The day-76 email (part C3). Not enabled without the purge. Needs Coach Ed's word per environment |

## 11. Tests (named, written first)

C1 (backend): portal_site conditions (suspended, cancelled after service end, `cancelled_no_payment_method` open; trial, active, attention, exempt, unknown, soft-deleted, held, template not Santé, MEMBERS/PACK off: 404); deletion point at days 29, 30, 89, 90 and one second either side of the instant, with the sweep not run; a never-paid site (day 44) and a site with no clock; reactivation returns the site and ends the portal; wrong buyer, unpaid buyer, rejected buyer, other site's session; write attempts refused; the route list equals the table in 3.2; every response is checked for the forbidden keys; expired and revoked sessions; a billing read error answers neutral and logs; the sale gate (section 6); `served()` and the existing member and pack routes still 404 for an unserved site (regression, unchanged tests).
C2: purge cases listed in section 8, including idempotence, unserved and soft-deleted site, no purge without a clock or after reactivation, a failing site not stopping the loop, images kept while the constant is `None`, registration actions refused after the purge, tenant isolation test passes.
C3: send once per member and `deletion_due_at`, retry rules, none after the point, none for a reactivated site, none for a member without a verified order, none for another site, no SMS, the time in the text equals `deletion_point`.
F (frontend): as section 9.

## 12. Migrations

| Part | Migration | Approval |
|---|---|---|
| C1 portal, gate, banner | **none** | not needed |
| C3 notice record | `apps/pack/migrations/0005_buyer_notice.py` (one table, additive) | needs Coach Ed's separate approval; under his standing approval it may be written and run on staging by the Supervisor only once an approved plan specifies it (decision B4) |
| C2 purge | none planned (assignments to empty values; no new event kinds, no run table) | not needed |

## 13. PR split and order

| PR | Repo | Contents | Needs |
|---|---|---|---|
| 1 | backend | C1: `site_for_host` refactor, `billing.deletion_point`, `apps/pack/portal.py`, `apps/pack/api_portal.py`, the sale gate, banner data, tests, runbook `docs/runbooks/pack.md` section on the portal, `support-and-exit.md` section 5 line | nothing but the plan's approval; no migration, no variable. Can be built now |
| 2 | frontend | `/portal/*` pages and tests (needs PR 1's routes for the real call; built against the contract) | PR 1 on staging |
| 3 | backend | C2 purge + C3 notice, migration 0005, switches, tests, `docs/runbooks/retention.md` | decisions B and C |

Why this few: PR 1 carries everything that is already ruled and needs no migration, so it can ship before 2026-11-25. PR 2 is unavoidable (separate repository). PR 3 stays apart because it waits on owner decisions and a migration; if decisions B and C arrive before PR 1 is ready, 1 and 3 combine into one backend PR (two in all). Under the release train the Supervisor reports each as a ready branch; the release builders combine them per round.

## 14. What is buildable now and what waits

| Buildable at once, no owner decision | Waits |
|---|---|
| Portal routes, site resolution, `deletion_point`, identity, data allowed (section 3 to 5), sale gate (6), banner without a deletion sentence (7), frontend pages (9), tests, runbooks | A: confirm the clocks (portal ends at day 90 or 44, suspended included). Small; built on the recommended answer meanwhile |
| | B: purge scope, D3 versus L52, images and the export, migration 0005 |
| | C: day-76 email wording (and the banner's deletion sentence) |
| | D, E, F: L70 screen text, service-end email, Data Deletion page (separate items, other tasks) |

## 15. Where the legacy spec no longer applies (summary)

Xendit and `paid` status; `PlatformSwitch` and its admin form; the middleware urlconf swap; day 104 (now L75: day 90, or day 44 for a never-paid site, and O3); "suspension sets no clock"; Django admin gating and the Finance resolver; `PackPurgeRun` and holds (deferred); the registration-age purge retirement (open, decision B1); the alert tables; the registry entries for tables this plan does not add.
