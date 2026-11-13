# Launch Gates: Go / No-Go Evidence (DRAFT)

ClickUp z8uq9m7ydn. Draft prepared 2026-11-13 against main `dc17431`. This file lists what must be shown before public launch, and before the first real buyer on staging (staging holds live Santé data). It approves nothing. A gate passes only on linked evidence: a merged pull request, a test module, a runbook, or a dated staging check. A row with no link is Pending or Not run, never Pass.

How to read Status. **Pass**: the evidence in the row exists and is linked. For gates 2 to 4, a merged pull request and its test module show that the behaviour is built and tested in CI. They do not show it working on staging; the walkthrough rows in each table are for that and stay Not run until someone runs and dates them. **Pending**: work or a decision is still open. **Not run**: a check that has not been performed yet.

Owners: GATES, SANTE, BILLING, HQ Supervisor. "BE #N" is backend pull request https://github.com/NegosyoOnline/negosyoonline-backend/pull/N and "FE #N" is frontend pull request https://github.com/NegosyoOnline/negosyoonline-frontend/pull/N. Owners for gates 2 to 4 are the list the work belongs to; the Supervisor of each list should confirm them. Test paths are in the backend repository.

## Public claims

The site and Terms state only what launches (G1, FE #55 merged; also FE #56, FE #59, FE #61). HSTS is max-age only (G4, FE #51). The site must never claim unlimited SMS, automatic courier integration, guaranteed delivery or an uptime SLA. The old "up to 1,000 online orders monthly" sentence is deliberately not used: there is no per-order billing.

Links: BE #N = `https://github.com/NegosyoOnline/negosyoonline-backend/pull/N`, FE #N = `https://github.com/NegosyoOnline/negosyoonline-frontend/pull/N`.

## Gate 1: Tenant isolation, security hardening, incident readiness

| Check | Evidence | Status | Owner |
|---|---|---|---|
| Tenant isolation suite over owner routes, storefront hosts, members, SMS and private files, with a guard that every owner route is covered (z8uq9m7ycc) | BE #148; `apps/common/tests/test_tenant_isolation/` | Pass | GATES |
| Upload size cap, DEBUG fails closed, Redis deploy check (z8uq9mekqj) | BE #148; `apps/common/tests/test_hardening.py` | Pass | GATES |
| Security denial log without email or code (z8uq9mekqj) | BE #148; `apps/common/tests/test_hardening.py` (SecurityLogTests, StorefrontDenialLogTests) | Pass | GATES |
| Facebook replay window (z8uq9mekqj) | BE #148 (named in its description). No test module was checked for this draft | Pending | GATES |
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
| Offline alert that reaches a person | The server only writes a log line today. An email alert needs the uptime account (Gate 5) | Pending | HQ Supervisor |
| Readiness reports a stuck SMS queue | BE #148; `apps/health/test_readyz.py` | Pass | GATES |
| Retention: send records deleted after 1 year, challenges 24 hours after expiry | BE #53; `apps/notifications/tests/test_retention.py`; `docs/runbooks/retention.md`; daily scheduled run BE #145 | Pass | HQ Supervisor |
| SMS gateway runbook | `docs/runbooks/sms-gateway.md` | Pass | HQ Supervisor |

## Gate 5: Backups, restore, monitoring, support and exit, mobile and accessibility

| Check | Evidence | Status | Owner |
|---|---|---|---|
| `/readyz/` with db, cache, beat and sms_queue checks | BE #148; `apps/health/test_readyz.py`. Staging check 2026-11-13: `/readyz/` 200, all four checks true (re-read read-only while drafting, same result) | Pass | GATES |
| Backups, restore and monitoring runbook | `docs/runbooks/backups-and-restore.md`; BE #148 | Pass | GATES |
| Railway backups exist on staging | Not seen yet | Pending | HQ Supervisor |
| Restore test (staging dump into a scratch service; deleting the dump is a required privacy step) | Written, not run (`docs/runbooks/backups-and-restore.md` section 6); waits for Coach Ed's word | Not run | GATES |
| External uptime check with email alert | Account not created (Coach Ed) | Pending | GATES |
| Sentry with personal data scrubbed | Code merged in BE #148, off until `SENTRY_DSN` is set. Account and DSN not created; Sentry waits 30 days after the Privacy line is live (Coach Ed) | Pending | GATES |
| Support and exit runbook | `docs/runbooks/support-and-exit.md`; BE #148 | Pass | GATES |
| Export dry run on a test account named as a test (never a real member) | Not run (Coach Ed's word). Code: BE #52; `apps/accounts/tests/test_export.py` | Not run | GATES |
| Account disposal and deletion request steps | BE #52; `apps/accounts/tests/test_disposal.py`; `docs/runbooks/retention.md` | Pass | GATES |
| Help Center articles (z8uq9mekvc) | FE #60; FE #64 (article 5) | Pass | SANTE |
| Help Center article 5 staging OK | Waits for Coach Ed | Pending | SANTE |
| Help Center second subtask (z8uq9memmp) | No merged pull request found for it in the lists read | Pending | SANTE |
| Mobile and accessibility audit (z8uq9m7ydk) | HQ Supervisor is running it; no report linked yet | Pending | HQ Supervisor |

## Gate 6: Runbooks

| Check | Evidence | Status | Owner |
|---|---|---|---|
| Incident | `docs/runbooks/incident.md` | Pass | GATES |
| Backups, restore, readiness, monitoring | `docs/runbooks/backups-and-restore.md` | Pass | GATES |
| Support and exit | `docs/runbooks/support-and-exit.md` | Pass | GATES |
| Retention, deletion and export | `docs/runbooks/retention.md` | Pass | GATES |
| Billing | `docs/runbooks/billing.md` | Pass | BILLING |
| SMS gateway | `docs/runbooks/sms-gateway.md` | Pass | HQ Supervisor |
| Membership Package | `docs/runbooks/pack.md`, `docs/runbooks/membership-package-routes.md` | Pass | SANTE |
| Migrations | `docs/runbooks/migrations.md` | Pass | GATES |

## Still open

- Railway backups on staging not yet seen (HQ Supervisor).
- Restore test not run; needs Coach Ed's word (GATES).
- Uptime check account and its email alert not created (Coach Ed); this is also what turns the SMS offline log line into an alert.
- Sentry account and `SENTRY_DSN` not created; waits 30 days after the Privacy line is live (Coach Ed).
- Export dry run on a test account named as a test (Coach Ed's word).
- Mobile and accessibility audit z8uq9m7ydk (HQ Supervisor).
- Help Center article 5 staging OK (Coach Ed); z8uq9memmp has no evidence yet.
- Facebook replay window: no test module confirmed in this draft.
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
