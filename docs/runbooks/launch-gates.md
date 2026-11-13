# Launch Gates: Go / No-Go Evidence (DRAFT)

ClickUp z8uq9m7ydn. Draft prepared 2026-11-13 against main `dc17431`. This file lists what must be shown before public launch, and before the first real buyer on staging (staging holds live Santé data). It approves nothing. A gate passes only on linked evidence: a merged pull request, a test module, a runbook, or a dated staging check. A row with no link is Pending or Not run, never Pass.

How to read Status. **Pass**: the evidence in the row exists and is linked. For gates 2 to 4, a merged pull request and its test module show that the behaviour is built and tested in CI. They do not show it working on staging; the walkthrough rows in each table are for that and stay Not run until someone runs and dates them. **Pending**: work or a decision is still open. **Not run**: a check that has not been performed yet. **Open**: an audit finding that is reported and not yet fixed or accepted.

Owners: GATES, SANTE, BILLING, HQ Supervisor. "BE #N" is backend pull request https://github.com/NegosyoOnline/negosyoonline-backend/pull/N and "FE #N" is frontend pull request https://github.com/NegosyoOnline/negosyoonline-frontend/pull/N. Owners for gates 2 to 4 are the list the work belongs to; the Supervisor of each list should confirm them. Test paths are in the backend repository.

## Public claims

The site and Terms state only what launches (G1, FE #55 merged; also FE #56, FE #59, FE #61). HSTS is max-age only (G4, FE #51). The site must never claim unlimited SMS, automatic courier integration, guaranteed delivery or an uptime SLA. The old "up to 1,000 online orders monthly" sentence is deliberately not used: there is no per-order billing.

| Check | Evidence | Status | Owner |
|---|---|---|---|
| Public claim: `https://www-staging.negosyonline.com/referral-terms` (`apps/site/public/referral-terms.html`) promises Subscription Credits of 20% of each qualifying payment. The Terms, the sign-up note and the dash footer link to it, and the dash shows a referral link card. Referral credits are deferred to after launch | Manager ruling 2026-11-13; BILLING task z8uq9mdwt1. Found by the BILLING Supervisor; page checked by fetch 2026-11-13 (200, two "20%" mentions) | Open | Manager to assign (fix wording pending) |

Links: BE #N = `https://github.com/NegosyoOnline/negosyoonline-backend/pull/N`, FE #N = `https://github.com/NegosyoOnline/negosyoonline-frontend/pull/N`.

## Gate 1: Tenant isolation, security hardening, incident readiness

| Check | Evidence | Status | Owner |
|---|---|---|---|
| Tenant isolation suite over owner routes, storefront hosts, members, SMS and private files, with a guard that every owner route is covered (z8uq9m7ycc) | BE #148; round 2 (d785dfb) in BE #150 (02dde96); `apps/common/tests/test_tenant_isolation/` | Pass | GATES |
| Upload size cap, DEBUG fails closed, Redis deploy check (z8uq9mekqj) | BE #148; `apps/common/tests/test_hardening.py` | Pass | GATES |
| Security denial log without email or code (z8uq9mekqj) | BE #148; `apps/common/tests/test_hardening.py` (SecurityLogTests, StorefrontDenialLogTests) | Pass | GATES |
| Facebook deletion callback replay window (z8uq9mekqj): refused over 24 hours old or over 5 minutes ahead; `issued_at` missing, a string or a bool refused | Code merged in BE #148. Tests merged in BE #150 (02dde96), from branch `test/z8uq9mekqj-facebook-replay-tests@898e795`: `apps/accounts/tests/test_facebook.py::SignedRequestReplayWindowTests` (`test_a_request_one_second_over_24_hours_old_is_stale`, `test_a_request_6_minutes_ahead_is_stale`, `test_requests_exactly_on_both_bounds_are_accepted`, `test_a_missing_issued_at_is_bad_not_stale`, `test_a_string_bool_or_null_issued_at_is_bad_not_stale`) and `apps/accounts/tests/test_api_facebook_deletion.py::FacebookDataDeletionTests` (`test_a_request_older_than_24_hours_or_ahead_of_the_clock_is_refused`, `test_a_request_just_inside_both_bounds_is_accepted`, `test_a_missing_string_or_bool_issued_at_is_refused_and_changes_nothing`) | Pass | GATES |
| Incident runbook | `docs/runbooks/incident.md`; BE #148 | Pass | GATES |
| CSP `object-src 'none'` on dash and site (z8uq9mekm5) | FE #58 | Pass | GATES |
| HSTS max-age only (z8uq9mekbe) | FE #51 | Pass | GATES |
| Staging runs the merged build | Staging check 2026-11-13: `/healthz/` 200 on commit `dc17431a27ad` (re-read read-only while drafting, same result) | Pass | GATES |
| `manage.py check --deploy` | BE #148 description: 2 warnings only, HSTS preload and includeSubDomains, deferred on purpose | Pass | GATES |

## Gate 2: Membership Package flow

| Check | Evidence | Status | Owner |
|---|---|---|---|
| Checkout and order placement | BE #135, BE #143; `apps/pack/tests/test_checkout.py`, `apps/pack/tests/test_place.py` | Pass | SANTE |
| Email codes and member sign-in | BE #104, BE #135; `apps/members/tests/test_signin.py`, `apps/members/tests/test_otp.py` | Pass | SANTE |
| Mobile codes | BE #51; `apps/members/tests/test_sms.py`, `apps/accounts/tests/test_api_mobile.py` | Pass | SANTE |
| Agreement acceptance | BE #103, BE #143; `apps/sante/tests/test_agreement.py` | Pass | SANTE |
| Proof upload and new proof | BE #135; `apps/pack/tests/test_member_pack.py`, `apps/pack/tests/test_onboarding.py`; `docs/runbooks/membership-package-routes.md` | Pass | SANTE |
| Owner (Subscriber) review of the order | BE #135; `apps/pack/tests/test_owner.py` | Pass | SANTE |
| Member Dashboard and My Orders | BE #135; `apps/pack/tests/test_dashboard.py`; FE #50, FE #61 | Pass | SANTE |
| Membership Package runbooks | `docs/runbooks/pack.md`, `docs/runbooks/membership-package-routes.md`, `docs/runbooks/members.md`, `docs/runbooks/sante-agreement.md` | Pass | SANTE |
| Walkthrough of the whole flow on staging, dated | None found | Not run | SANTE |

## Gate 3: Subscriber billing by bank transfer

| Check | Evidence | Status | Owner |
|---|---|---|---|
| Subscription lifecycle, trial, cycles | BE #62, BE #66; `apps/billing/tests/test_transitions.py`, `apps/billing/tests/test_sweep.py` | Pass | BILLING |
| Bank transfer payment record, Subscriber routes, proof submit and store | BE #87, BE #88, BE #142, BE #144; `apps/billing/tests/test_transfer.py`, `apps/billing/tests/test_transfer_api.py`, `apps/billing/tests/test_proof_store.py` | Pass | BILLING |
| Six-month Launch Promo | BE #89; `apps/billing/tests/test_promo.py` | Pass | BILLING |
| Operator review in HQ Finance (confirm and reject) | BE #93; `apps/billing/tests/test_staff_finance.py`; FE #41; `docs/runbooks/billing.md` (The Finance routes) | Pass | BILLING |
| Dash Billing page, no usage charge wording | FE #62 | Pass | BILLING |
| Suspension and storefront closing (billing-only, ended site answers the plain 404) | BE #62, BE #66; `apps/sante/tests/test_billing_only.py`, `apps/billing/tests/test_sweep.py`. The ended-site 404 was not checked on staging (`docs/runbooks/billing.md`) | Pending | BILLING |
| Billing runbook | `docs/runbooks/billing.md` | Pass | BILLING |
| Walkthrough on staging: submit proof, operator confirms, then lapse and storefront closes, dated | None found | Not run | BILLING |

## Gate 4: SMS gateway

| Check | Evidence | Status | Owner |
|---|---|---|---|
| Code delivery through the gateway phone | BE #46, BE #47; `apps/notifications/tests/test_sms_gateway.py`, `apps/notifications/tests/test_sms_send.py`; first real phone recorded in BE #82 and `docs/runbooks/sms-gateway.md` | Pass | HQ Supervisor |
| Daily limits (platform and site, start at 2,000) | BE #60; `apps/notifications/tests/test_sms_send.py` (platform limit and site limit tests) | Pass | HQ Supervisor |
| Offline behaviour: code refused with a plain message, email fallback, `sms_gateway_offline` log line at most hourly | `apps/notifications/tests/test_sms_send.py`; `docs/runbooks/sms-gateway.md` (When the phone is offline) | Pass | HQ Supervisor |
| Offline alert that reaches a person | The server only writes a log line today. The alert is the uptime monitor on `/readyz/` (Gate 5, UptimeRobot, set up 2026-11-13). Proved by the Gate 5 row "Uptime alert proof (ops B)": the Down and Up emails were received on 2026-11-13 | Pass | HQ Supervisor |
| Readiness reports a stuck SMS queue | BE #148; `apps/health/test_readyz.py` | Pass | GATES |
| Retention: send records deleted after 1 year, challenges 24 hours after expiry | BE #53; `apps/notifications/tests/test_retention.py`; `docs/runbooks/retention.md`; daily scheduled run BE #145 | Pass | HQ Supervisor |
| SMS gateway runbook | `docs/runbooks/sms-gateway.md` | Pass | HQ Supervisor |
| Security finding (Low): the staging Celery worker logs "running with superuser privileges"; worker and beat run as root. Found during ops B on 2026-11-13 | Fix in ClickUp z8uq9mevfy, on branch `fix/z8uq9mevfy-celery-nonroot`: the start scripts drop to `nobody:nogroup`, plus a one-time Railway start-command change on staging after merge | Open | GATES |

## Gate 5: Backups, restore, monitoring, support and exit, mobile and accessibility

| Check | Evidence | Status | Owner |
|---|---|---|---|
| `/readyz/` with db, cache, beat and sms_queue checks | BE #148; `apps/health/test_readyz.py`. Staging check 2026-11-13: `/readyz/` 200, all four checks true (re-read read-only while drafting, same result) | Pass | GATES |
| Staging runs backend release round 1 (BE #149, merged at 58271aa on 2026-11-13) | Supervisor check 2026-11-13: `https://api-staging.negosyonline.com/healthz/` answers 200 with commit `58271aa2868d`; `/readyz/` answers with all checks true | Pass | GATES |
| Django admin removed on staging (BE #149) | Supervisor check 2026-11-13: `https://api-staging.negosyonline.com/admin/` answers 404 | Pass | GATES |
| HQ API host health (BE #149) | Supervisor check 2026-11-13: `https://hq-api-staging.negosyonline.com/healthz/` answers 404 by design. The HQ host serves only HQ paths (`apps/operators/tests/test_hq.py`, `HqHostTests.test_the_hq_host_serves_only_hq`; Manager ruling 2026-11-13). Staging health is checked on api-staging only | Pass (by design) | GATES |
| Backups, restore and monitoring runbook | `docs/runbooks/backups-and-restore.md`; BE #148 | Pass | GATES |
| Railway backups exist on staging | Coach Ed, staging Postgres > Backups, 2026-11-13: at first no schedule and no backups, PITR off (a gap). He set a schedule (\"Next backup in 12 hours\") and took an on-demand volume backup, 2026-11-13 19:33, 1.11 GB (`docs/runbooks/backups-and-restore.md` section 1) | Pass | HQ Supervisor |
| Backup schedule boxes confirmed in the Backups tab | Which schedule boxes are ticked is not yet confirmed | Pending | GATES |
| Point-in-time recovery decision before production | PITR is off on purpose: it costs more and redeploys the database. Revisit before production | Pending | GATES |
| Restore test (staging dump into a scratch service, inside Railway) | Run 2026-11-13, method in `docs/runbooks/backups-and-restore.md` section 6: `pg_dump` piped into `pg_restore` in a scratch Railway Postgres container over the private network; no data left Railway (ruling 509745). Scratch service created 11:43 UTC; Postgres 18.6 on both sides. Dump started 11:44:52 UTC, restore finished 11:44:55 UTC (3 s), exit 0; database 18 MB. 104 tables and 854 rows on each side, per-table counts identical. `django_migrations`: 62 rows, max id 80, on both sides. On the copy, db `select 1` returned 1 and sms_queue returned 0 rows queued 15 minutes or more; cache and beat not tested (no Redis or worker). Scratch service deleted 11:46 UTC; staging Postgres and variables unchanged. Railway's own backup not tested: a volume backup restores only in place and PITR is off | Pass | GATES |
| Scratch volume deletion confirmed | The scratch service was deleted 11:46 UTC on 2026-11-13. Its volume was deleted at 11:53 UTC on Coach Ed's word. It is detached, and Railway purges it at the end of its 48-hour grace on 2026-11-15 11:47 UTC | Pass | GATES |
| External uptime check: account and monitors | UptimeRobot free plan, Coach Ed's own account, 2026-11-13: 4 monitors at 5-minute intervals (api-staging `/readyz/`, www-staging, dash-staging, the Santé staging storefront), all green. The free plan sends HEAD and cannot pick the method or a keyword; `/readyz/` was fixed to answer HEAD in z8uq9mev7t (BE #155, 8664aa5) | Pass | GATES |
| Uptime alert proof (ops B): stop the worker and beat, receive the Down and Up emails | Run 2026-11-13 on staging, approved under ruling 512065. Beat, then worker, were stopped with the dashboard Stop at 12:01:46 UTC (T0). `/readyz/` answered 503 with `"beat": false` from 12:04:52 UTC, and was still 503 at 12:16:41. Worker, then beat, were redeployed from the same image (8664aa5, no rebuild) at 12:16:03 UTC (T1). `/readyz/` was back to 200 with every field true at 12:17:12 UTC. `/healthz/` answered 200 at every check (15). The worker was ready at 12:16:13 and the heartbeat succeeded, with no failed task in the log. UptimeRobot's Down and Up emails for the api monitor were received, as confirmed by Coach Ed on 2026-11-13; he did not give the times. No variable or setting was changed | Pass | GATES |
| Sentry with personal data scrubbed | Code merged in BE #148, off until `SENTRY_DSN` is set. Account created 2026-11-13: org "NS", project python-django, 14-day trial then the free Developer plan, no card. Error monitoring only; logs, tracing, profiling, metrics and the repo link are off. No DSN is set anywhere. At go-live a fresh client key is created in the project settings and used. The DSN waits 30 days after the Privacy line is live (Coach Ed) | Pending | GATES |
| Support and exit runbook | `docs/runbooks/support-and-exit.md`; BE #148 | Pass | GATES |
| Export dry run on a test account named as a test (never a real member) | Run 2026-11-13 11:41 UTC on staging api, actor the staff user, ref EXPORT-DRYRUN-1, test account `acc_95tg3whptrx3vgxwmr63`, site `site_zaqj1svyvdfaean2nn0w` (business "Test Explore": the test sign-up, a test address). Keys: account (7 fields), policy_acceptances 1, sign_in_providers 0, sessions 1, audit_events 5, mobile null, mobile_challenges 0, email_send_records 1, onboarding 2, sites 1, sante_site_settings 1; payment, member, Santé registration, pack and bank transfer lists 0. Only that account's email and ids appear: no token, password, code, provider user id or secret. Exactly one `account_exported` operator audit entry, 11:41:24 UTC, counts only, matching the file. The file was deleted. Code: BE #52; `apps/accounts/tests/test_export.py` | Pass | GATES |
| Account disposal and deletion request steps | BE #52; `apps/accounts/tests/test_disposal.py`; `docs/runbooks/retention.md` | Pass | GATES |
| Help Center, five articles (z8uq9mekvc) | FE #60; task ACCEPTED | Pass | GATES |
| Help Center, second subtask (z8uq9memmp), including article 5 | FE #64 (merged 5144a12); task ACCEPTED; Coach Ed approved the article 5 staging visual test on 2026-11-13 | Pass | GATES |
| Mobile and accessibility audit (z8uq9m7ydk), summary | ClickUp z8uq9m7ydk comments 1100430000510246 (pass 1, signed-out pages), 1100430000510420 (pass 2, signed-in pages and keyboard) and 1100430000510829 (pass 3, member signed-in pages and form error states) and 1100430000511422 (pass 4, dash and HQ form error states) and 1100430000512390 and 1100430000512690 (pass 5, parts 1 and 2, GATES Supervisor, local preview backend 02dde96 and frontend 1fe7d6d), 2026-11-13. Five passes on local TEST builds and previews, headless Chrome at 390 and 1400 px, manual (no axe or Lighthouse). No High findings. Rows below are the findings and the gaps; the audit passes when each is fixed or accepted by Coach Ed | Pending | HQ Supervisor |
| Audit: site eyebrow, compare and timeline gold text contrast (2.26 to 3.96, target 4.5), and the `#switch-mode` button 23 px high at 390. Medium | FE #66 (merged at 1fe7d6d, 2026-11-13), branch `fix/z8uq9m7ydk-site-contrast@cf028b4`: `--gold-text: #8A6513` on the eyebrow, compare eyebrow and timeline years; `.switch-mode .link-inline { min-height: 24px }`. Approved by Coach Ed as "Site gold text: #8A6513 on light backgrounds" (rulings log comment 1100430000512065). Verified by fetch of `https://www-staging.negosyonline.com/assets/css/site.css` on 2026-11-13 | Pass | GATES |
| Audit: dash Help Center h3 subheadings invisible in dark theme (contrast 1.05). Medium | Fixed on main by FE #64 (5144a12). Readable in dark theme through FE #65 (a037c94, z8uq9memqv ACCEPTED 2026-11-13), which carries the regression test | Pass | GATES |
| Audit: dash Business Settings seal file input `#bs-seal-file` has no label. Low | FE #65 a037c94, label on `#bs-seal-file` live on dash-staging; z8uq9memqv ACCEPTED 2026-11-13 | Pass | GATES |
| Audit: Santé storefront home images about 3.3 MB at 390 (4.3 MB at 1400). Medium | Pass 1 comment 1100430000510246; no fix yet | Open | SANTE |
| Audit: storefront product card image links are empty (no name, a tab stop). Medium | Pass 1 comment 1100430000510246; no fix yet | Open | SANTE |
| Audit: storefront contrast (star rating 2.20, contact labels 3.79, "Clear Basket" 3.58, "We typically respond" 4.02). Medium | Pass 1 comment 1100430000510246; no fix yet | Open | SANTE |
| Audit: storefront home shows three h1s; Basket icon link is 22 px. Low | Pass 1 comment 1100430000510246; no fix yet | Open | SANTE |
| Audit: storefront has no skip link and only the browser's default 1 px focus ring. Low | Pass 2 comment 1100430000510420; no fix yet | Open | SANTE |
| Audit: HQ Finance "Replaced" tab sits off-screen at 390 with no scroll cue. Low | Pass 2 comment 1100430000510420; no fix yet | Open | HQ Supervisor |
| Audit: public site home page overflowed sideways at 390 (comment 1100430000509383) | Pass 1 confirmed fixed: page is 390 wide, the comparison table scrolls in its own box | Pass | GATES |
| Audit: Dashboard Membership Pack card says the order has not shipped while My Orders shows it Shipped and Delivered. Medium | Pass 3 comment 1100430000510829; no fix yet; tracked on SANTE task z8uq9memuj | Open | SANTE |
| Audit: My Orders small text below 4.5:1 ("Placed" date 4.02, "Total" line 4.02, "Delivered" badge 4.36). Low | Pass 3 comment 1100430000510829; no fix yet; goes to the contrast batch | Open | SANTE |
| Audit: sign-in contact-step error is linked to the field but not announced, and focus drops to the page body. Low | Pass 3 comment 1100430000510829; no fix yet; tracked on SANTE task z8uq9memuj | Open | SANTE |
| Audit: change-email step 1 (confirm code) error not announced, focus drops to the body, empty submit gives no feedback; change mobile likely shares it. Low | Pass 3 comment 1100430000510829; no fix yet; tracked on SANTE task z8uq9memuj | Open | SANTE |
| Audit: My Orders "View Details" toggle has aria-expanded but no aria-controls. Low | Pass 3 comment 1100430000510829; no fix yet; tracked on SANTE task z8uq9memuj | Open | SANTE |
| Audit: My Orders 4-step tracker wraps 3 + 1 at 390 (cosmetic; only if the approved design shows one row). Low | Pass 3 comment 1100430000510829; no fix yet; tracked on SANTE task z8uq9memuj | Open | SANTE |
| Audit: membership landing page weighs about 0.9 to 1.2 MB, mostly images. Low | Pass 1 comment 1100430000510246; no fix yet | Open | SANTE |
| Audit pass 4 fix: form error focus and messages L1, L3, L4, L5 and client-side email length L2 (z8uq9men7r) | `fix/z8uq9men7r-form-errors@ca82932`, live on dash-staging via FE #66 (1fe7d6d); local visual test passed; Coach Ed's staging visual test OK in the Manager session, 2026-11-13 ("this are accepted. visual test done"); z8uq9men7r ACCEPTED 2026-11-13; builder worktree and branch removed | Pass | GATES |
| Audit pass 4 fix: dash dark-theme focus outline was gold #F2D99B, now light blue (z8uq9menbg) | `fix/z8uq9menbg-dash-focus-blue@73ded19`, live via FE #66 (1fe7d6d); local visual test passed; Coach Ed's staging visual test OK in the Manager session, 2026-11-13 ("this are accepted. visual test done"); z8uq9menbg ACCEPTED 2026-11-13; builder worktree and branch removed | Pass | GATES |
| Audit pass 4 fix: server-side email length limit of 254 (L2, z8uq9men7t) | `fix/z8uq9men7t-email-length@df30c81`; BE #150 02dde96; staging POST `/v1/auth/email-code/request/` with a 256-character email returned 400 "String should have at most 254 characters" (2026-11-13); z8uq9men7t ACCEPTED | Pass | GATES |
| Audit pass 4: Dash Payments QR upload with a non-image file leaves the crop panel stuck (M1). Medium | Pass 4 comment 1100430000511422; no fix yet | Open | SANTE |
| Audit pass 4: Dash Billing "Send proof" is disabled with no hint, and focus goes to the body (M2). Medium | Pass 4 comment 1100430000511422; no fix yet | Open | BILLING |
| Audit pass 4: HQ New SMS is disabled with no reason, and the textarea allows 400 characters against a 160 limit (M3). Medium | Pass 4 comment 1100430000511422; no fix yet | Open | HQ Supervisor |
| Audit pass 4: Dash Payments add account has weak messages, aria and focus (L6); pack-order Reject dialog (L7). Low | Pass 4 comment 1100430000511422; no fix yet | Open | SANTE |
| Audit pass 4: Dash Billing focus after an invalid submit (L8). Low | Pass 4 comment 1100430000511422; no fix yet | Open | BILLING |
| Audit pass 4: HQ Finance issuer form shows one summary error (L9); Confirm and Reject lack aria-invalid and focus (L10); HQ Contacts add (L11); HQ Contacts groups and CSV show a stale error (L12); HQ SMS inbox "Find number" gives no message (L13); HQ Bulk SMS is not flagged (L14); HQ sign-in puts aria-live on the whole main (L15). Low | Pass 4 comment 1100430000511422; no fix yet | Open | HQ Supervisor |
| Audit pass 5: covered: an expired (10-minute) code, change-mobile code step, Product Orders and Pack Order shipment forms, HQ invoice books "Add a book", the CSV file-type error, change-email steps 2 and 3, change-mobile beyond step 1. Not fully covered: the change-email and change-mobile code steps were checked over the API only (a browser extension overlay blocks automation at one-time-code fields); Usage Admit and Dismiss is not reachable at launch by design (`usage_enabled()` needs storage metering, which is off; `apps/billing/plan.py:179`) and was reviewed from the code only | Pass 5 comments 1100430000512390 and 1100430000512690 | Pass | GATES |
| Audit pass 5 follow-up: re-check Usage Admit and Dismiss in a browser when usage is turned on after launch | Pass 5 comment 1100430000512690 | Pending | GATES |
| Audit pass 5: Pack Order "Add shipment" error messages are not linked to their fields (no aria-describedby), and focus stays on Courier after a single error (L16). Low | Pass 5 comment 1100430000512690; no fix yet | Open | SANTE |
| Audit pass 5: Product Orders "Mark shipped" error messages are not linked to their fields (no aria-describedby) (L17). Low | Pass 5 comment 1100430000512690; no fix yet | Open | SANTE |
| Audit pass 5: HQ invoice book form shows one summary error, with no aria-invalid or aria-describedby and no focus move (same pattern as L9) (L18). Low | Pass 5 comment 1100430000512690; no fix yet | Open | HQ Supervisor |
| Audit pass 5: HQ Contacts upload: a non-CSV text file or an .xlsx file reaches step 2 with every line refused and "0 new contacts... The 0 numbers...", with no hint to save as CSV; nothing is saved (L19). Low | Pass 5 comment 1100430000512690; no fix yet | Open | HQ Supervisor |

## Gate 6: Runbooks

This is the "runbooks for each" line of the working statement.

| Check | Evidence | Status | Owner |
|---|---|---|---|
| Incident | `docs/runbooks/incident.md` | Pass | GATES |
| Backups, restore, readiness, monitoring | `docs/runbooks/backups-and-restore.md` | Pass | GATES |
| Support and exit | `docs/runbooks/support-and-exit.md` | Pass | GATES |
| Retention, deletion and export | `docs/runbooks/retention.md` | Pass | GATES |
| Billing | `docs/runbooks/billing.md` | Pass | BILLING |
| No backup of private files (Vercel Blob private store); the restore test covers the database only. Medium | Payment proofs and other private uploads would be lost with no recovery if the store or account were lost, and some are records kept for years. Not a launch blocker on its own; whether to add a backup is a later decision. `docs/runbooks/backups-and-restore.md` sections 1 and 6 | Open | GATES |
| SMS gateway | `docs/runbooks/sms-gateway.md` | Pass | HQ Supervisor |
| Membership Package | `docs/runbooks/pack.md`, `docs/runbooks/membership-package-routes.md` | Pass | SANTE |
| Migrations | `docs/runbooks/migrations.md` | Pass | GATES |

## Still open

- Worker and beat run as root on staging: fix z8uq9mevfy (GATES).
- Backup schedule boxes to confirm in the Backups tab (GATES).
- Point-in-time recovery decision before production (GATES).
- No backup of private files (Vercel Blob private store); the restore test covers the database only (GATES).
- NPC P5 section 3 and section 5 wording about files in the restore test, to be corrected by its owner (HQ/NPC kit).
- Sentry `SENTRY_DSN`: waits 30 days after the Privacy line is live (Coach Ed).
- Public claim row on referral credits (Open).
- Mobile and accessibility audit z8uq9m7ydk: findings above still Pending or Open (GATES, SANTE, BILLING, HQ Supervisor); Usage Admit and Dismiss to be re-checked in a browser once usage is turned on after launch (GATES).
- Dated staging walkthroughs for gates 2 and 3 (Not run); ended-site 404 not checked on staging.
- Production does not exist yet. Every staging item must be repeated for production once it does.

## Deferred after launch (not gated)

- Cash on delivery.
- Xendit webhook-only orders.
- J&T booking and tracking.
- Returns and damaged stock.
- Track Order guest lookup.
- Stock correctness.
- The order allowance and overage ledger.
