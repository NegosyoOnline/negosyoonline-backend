# Backend MVP scope and design (2026-11-10)

Status: design approved by Coach Ed on 2026-11-10. Amended the same day to add Facebook sign-in (see section 12). This document is the written MVP scope that `CLAUDE.md` requires before features, models or integrations are built. It contains no code. Each slice in section 9 gets its own implementation plan and approval before work starts.

## 1. Purpose

Rebuild the NegosyoOnline platform backend in this repository as a clean Django API. The legacy implementation inside TWCinc is the behaviour reference only: read it to understand a rule, never copy its code, dependencies or configuration (amended on 2026-11-11 for the Santé port, see section 12). A separate session builds the frontend against the API described here.

## 2. Decisions

| Topic | Decision |
|---|---|
| Signup | 14-day free trial starts at signup. No payment at signup. |
| Subdomain | Chosen by the subscriber during onboarding, after signup, when the first site is created. |
| Sites per account | One site per account in the MVP. The schema allows several without a rebuild. |
| Subscriber sign-in | Passwordless email code, plus Google and Facebook. Other Facebook features are deferred. |
| In the MVP | Referral codes and credit, usage overage charges, PDF billing statements, mobile number OTP. |
| Production | Railway production services are built only when the MVP is complete. API host: `api.negosyoonline.com`. |

## 3. Architecture

- One Django project, a modular monolith. Small apps by domain under `apps/`:
  `accounts`, `sites`, `billing`, `payments`, `statements`, `notifications`, `operators`, plus the existing `health`.
- One HTTP API under `/v1/`, built with Django Ninja. It publishes an OpenAPI description at `/v1/openapi.json`; the frontend generates its client from that file. The description is the contract.
- An app talks to another app only through that app's service functions, never by writing its tables directly.
- Django owns identity, tenancy, authorization, money, webhook validation and auditability. The frontend is untrusted.
- Background work (charging, lifecycle sweeps, statements, email sending) runs in a worker and a beat service on Railway, built from this repo. They are added with slice 4, not before. SMS sending does not wait for them: the web service queues a text and NegosyoOnline's phone collects it (section 12, 2026-11-11).

## 4. Identity and sign-in

- A subscriber is an `Account`: unique email, no password. It is not a Django user.
- Operators are Django staff users. `AUTH_USER_MODEL` is a custom `operators.User`, set in slice 1 because it must exist before the first migration. The admin's console, hq, is rebuilt by Coach Ed's ruling of 2026-11-10 (ClickUp task z8uq9mdwdx): Django renders it on its own host, with no script and no external asset. By his ruling of 2026-11-11 (ClickUp task z8uq9mdxvt) hq is the only admin door: it requires two-step sign-in, and the Django admin lists open only from it, on the hq host; see section 12.
- Email code: 6 digits, held in the cache with an expiry and an attempt limit, rate-limited per email and per IP.
- Google: server-side OAuth redirect flow. The callback signs the account in and redirects to the dashboard. Google vouches for the email.
- Facebook: server-side OAuth redirect flow requesting only `public_profile` and `email`. Facebook does not vouch for the email, and may return none. So the first time a Facebook identity is seen:
  - the email is the one Facebook returned, or one the subscriber enters if Facebook returned none;
  - the subscriber confirms that email with the email code;
  - only then is the identity linked to the existing account with that email, or a new account created.
  Later sign-ins with the same Facebook identity need no code.
- Google and Facebook sign-ins are matched first by the provider's own user id, then by email. An account can hold one identity per provider.
- Meta requires a data deletion callback. The API verifies the signed request with the app secret, removes that account's Facebook identity, records an audit event and answers with a confirmation code and status URL.
- Provider access tokens are used only during the callback and are never stored.
- Session: on sign-in the API sets a cookie holding an opaque random token. Only the token's SHA-256 is stored, with expiry (30 days), revocation time, device label and masked IP.
  - The cookie is `HttpOnly`, `Secure`, `SameSite=Lax` and host-only on the API host, so it is never sent to subscriber storefront hosts.
  - The dashboard and site call the API from the browser with credentials included.
  - CORS allows credentials only for the configured site and dashboard origins.
  - Every state-changing request must carry an `Origin` in that same allowlist. Storefront origins are never in it.
- Verifying an email by code revokes that account's earlier sessions.

## 5. Tenancy rules

- Every site-owned row carries its `site`. Every dashboard endpoint under `/v1/sites/{site_id}/` checks that the site belongs to the signed-in account and answers 404 otherwise.
- URLs and responses use opaque public ids (for example `acc_…`, `site_…`), never database sequence numbers.
- Subdomain rules, carried from legacy:
  - lowercase letters, digits and hyphens; starts and ends with a letter or digit; 30 characters at most;
  - infrastructure labels (such as `www`, `api`, `dash`, `admin`) and brand names are reserved;
  - a subdomain is never reissued. A name a site was renamed away from, or whose site was deleted, stays taken unless an operator releases it with an audit entry.
- Availability check answers one of: `available`, `taken`, `reserved`, `invalid`.
- The `Host` header is untrusted input. The storefronts app passes the host to the API, which looks it up; the host is never used to build queries or URLs directly.
- Sites are soft-deleted only.

## 6. Data model

One line per table. Money is stored as integer centavos with a currency code. Ledger tables are append-only.

**accounts**
- `Account`: email (unique), name, email_verified_at, referral_code (unique), referred_by, closed_at.
- `ExternalIdentity`: account, provider (`google`, `facebook`), provider user id (unique per provider), linked_at. One row per account per provider.
- `Session`: account, token hash, expires_at, revoked_at, device label, masked IP, last_used_at.
- `PolicyAcceptance`: account, policy version, policies accepted, IP, accepted_at.
- `AccountMobile`: account (one each), verified mobile number, verified_at. A row exists only for a verified number; two accounts may hold the same number.
- `MobileOtpChallenge`: account, the number being verified, code hash, expires_at, attempts, used_at, superseded_at, the id of the send record that carried the code. Deleted 24 hours after expiry.
- `AccountAuditEvent`: account, what changed, when.

**sites**
- `Site`: account, template, display name, contact email and mobile, accent colour, social links, operator_hold, created_at, deleted_at. One table replaces legacy's billing Site and platform Site.
- `Subdomain`: name (unique, lowercase), site, state (`active`, `retired`, `operator_reserved`), retired_at, released_at. The single source of truth for "never reissued". A site has exactly one active subdomain.
- `SiteBusinessProfile`: site (one each), business name, TIN, DTI number, address, shipping estimates.

**billing**
- `Subscription`: site (one each), state, trial start and end, the frozen answer to whether a payment method was active at trial end, the anchor day, attention, cancel and service-end dates, the download and deletion dates, `billing_exempt`. The only place trial dates live.
- `SubscriptionTransition`: subscription, code, from state, to state, actor, idempotency key (unique), `effective_at` (when the change took effect by rule), recorded_at, detail.
- `BillingCycle`: subscription, sequence, kind (`trial`, `paid`), start, end, settlement (`not_billable`, `pending`, `paid`, `lapsed`), paid_at, payment reference.
- `UsageEntry`: cycle, kind (`orders`, `storage`), quantity, source reference (unique).
- `ChargeLine`: cycle, kind (`base`, `overage`, `referral_credit`), amount.
- `ReferralCredit`: referrer account, referred account (unique), amount, applied cycle.
- `BillingProfile`: site (one each), company name, TIN, address.

**payments**
- `PaymentCustomer`: site, provider, provider customer id.
- `PaymentMethod`: site, provider, provider method id, brand, last four, status.
- `PaymentAttempt`: cycle, our reference (unique), idempotency key (unique), amount, status, outcome source.
- `WebhookEvent`: provider, provider event id (unique together), body hash, received_at, processed_at.

**statements**
- `Statement`: cycle, number (unique, from a numbered series), issued_at, totals, PDF storage key.
- `StatementLine`: statement, description, amount.
- `StatementDelivery`: statement, channel, status, at.

**notifications**
- `EmailMessage`: recipient, category, status, provider reference.
- `SmsMessage`: recipient, category, purpose, sending class, status, reason, emailed, site, the phone that took it (in place of a provider reference), and its times. No message body is stored: the text of a code or a status text waits encrypted in the cache for at most 10 minutes.
- `SmsDevice`: one row per gateway phone: name, token hash, active, forwards incoming texts (off by default), last contact. `SmsPolicy`: one row: sending stopped, the platform daily limit, the per-site daily limit.
- Inbox conversation text (incoming texts and replies on a subscriber's own line) will be stored, in its own table, created by the Dedicated SMS Line add-on (z8uq9mb19f) and not in the MVP slices here. See section 12, 2026-11-11.

**operators**
- `User`: Django staff user.
- `OperatorAuditEntry`: operator, action, target, reason, detail, personal values and when they were blanked, at. Rows are never deleted.
- `AdminIdentity`: user, email (unique), encrypted authenticator secret, last used time step, failed attempts, locked until, credential version, active, enrolled_at.
- `AdminRecoveryCode`: identity, code hash, used_at.
- `AdminSignInChallenge`: identity, code HMAC, nonce, expires_at, attempts, consumed_at.
- `AdminSession`: identity, token hash, stage, credential version, verified_at, expires_at, revoked_at.

Not rebuilt from legacy: launch gate, platform switches, per-account gate exceptions, interim site states, the Santé selection approval table, the unused `Account.mobile`, and the duplicate trial dates on Account.

## 7. Subscription states and site availability

States, carried from legacy: `trial_active`, `active`, `payment_requires_attention`, `suspended`, `cancelled`, `cancelled_no_payment_method`.

- Signup creates the Account only. Creating the first site creates the Site, its Subdomain and its Subscription in one transaction.
- The 14-day trial is counted from account creation, as in legacy. The Subscription copies those dates when it is created. If the first site is created after the 14 days have passed, the subscription starts in `cancelled_no_payment_method` and the site goes live once a card is added and the first charge succeeds. Its dates count from the trial end, not from the site's creation (section 12, 2026-11-11, D2).
- A storefront is served only when the site is not deleted, has no operator hold, and its subscription is `trial_active`, `active` or `payment_requires_attention`. This fixes a legacy gap where billing state never drove storefront availability.
- Billing values, confirmed by the Billing Build B1 plan (section 12, 2026-11-11):
  - plan: PHP 2,499 per month; cycles renew at 3:00 PM Asia/Manila on the anchor day of each month, or the month's last day when that day does not exist;
  - included per cycle: 1,000 orders and 5 GB storage; overage PHP 5 per order and PHP 30 per GB storage. Video is not in the launch product: no allowance, meter or charge (contract D6, D9);
  - no payment outcome by the due date plus 7 days: suspended. The storefront is served during the trial, a paid cycle and the 7-day attention period. State is computed from the current time, never from how far the sweep has run;
  - a trial that reaches day 14 with no active payment method becomes `cancelled_no_payment_method`, with no charge and no statement;
  - after service end: a 30-day download window; the deletion date is 30 days after service end for a website whose trial ended without any payment, and 90 days otherwise (L75). A suspension counts as the end of the service for these dates (R-4b), and a cancellation after a suspension does not restart them. The never-paid dates start only once payment collection is live (R-4a). Payment is accepted only before the deletion date (D1). Billing and tax records are kept 5 years and no clock here touches them.
- The retry schedule after a failed payment (legacy: retries after 1, 3 and 5 days) is not confirmed. It moves to the Billing Build B2 plan.
- At launch a subscription is paid by bank transfer with proof, confirmed by an operator (Billing Build B2a, z8uq9me581), behind the switch `BILLING_BANK_TRANSFER_ENABLED`, off by default. An upload is never a payment: only the operator's recorded confirmation settles a cycle. A proof under review at the trial-end instant counts as the payment method for the 7 days and no longer. The Launch Promo is PHP 4,999.00 once for six months, ending on the exact six-month date, then PHP 2,499.00 monthly. A first site created after its trial ended has 30 days to pay from its own creation.

## 8. API contract

All paths are under `/v1/`. Errors use one shape: `{"error": {"code": "...", "message": "...", "fields": {...}}}` with a normal HTTP status (400, 401, 403, 404, 409, 429). Lists are paginated with a cursor.

| Area | Endpoints |
|---|---|
| Public | `GET plan/`, `GET referrals/{code}/`, `GET public/sites/by-host/?host=` |
| Sign-in | `POST auth/email-code/request/`, `POST auth/email-code/verify/`, `GET auth/google/start/`, `GET auth/google/callback/`, `GET auth/facebook/start/`, `GET auth/facebook/callback/`, `POST auth/facebook/confirm-email/`, `POST auth/logout/` |
| Account | `GET me/`, `GET account/setup/`, `GET PATCH account/`, `POST account/email/start/`, `POST account/email/confirm/`, `GET account/sessions/`, `POST account/sessions/sign-out-others/`, `POST account/connections/{provider}/start/`, `POST account/connections/{provider}/disconnect/`, `POST account/mobile/start/`, `POST account/mobile/verify/`, `GET account/referrals/` |
| Sites | `GET POST sites/`, `POST sites/subdomain-check/`, `GET PATCH sites/{site_id}/`, `GET PATCH sites/{site_id}/business-profile/` |
| Billing | `GET sites/{site_id}/billing/`, `PUT sites/{site_id}/billing/profile/`, `POST sites/{site_id}/billing/payment-method/setup/`, `POST sites/{site_id}/billing/cancel/`, `POST sites/{site_id}/billing/reactivate/`, `GET sites/{site_id}/billing/payment/`, `GET POST sites/{site_id}/billing/payments/`, `GET sites/{site_id}/billing/payments/{payment_id}/`, `GET sites/{site_id}/billing/payments/{payment_id}/proof/`, `GET sites/{site_id}/billing/usage/`, `GET sites/{site_id}/billing/statements/`, `GET sites/{site_id}/billing/statements/{number}/`, `GET sites/{site_id}/billing/statements/{number}/pdf/` |
| Webhooks | `POST webhooks/xendit/`, `POST webhooks/facebook/data-deletion/` |

`public/sites/by-host/` is the storefront lookup. It is unauthenticated and cacheable, returns only public branding, template and availability, and answers 404 for unknown, reserved or unavailable hosts.

The SMS gateway phone uses three calls that are outside `/v1/` and are not in the OpenAPI description: `POST /gateway/v1/poll`, `POST /gateway/v1/report` and `POST /gateway/v1/received`. They carry the phone's own token, not a subscriber's session.

Payments and webhooks:
- At launch the only way to pay a subscription is a bank transfer with proof (B2a); it calls no provider. The three lines below are Billing Build B2, built when a gateway is approved.
- Xendit is the provider. Card details are entered on Xendit's hosted page; the API never sees card numbers.
- Webhooks: body size cap, constant-time check of the callback token, insert into `WebhookEvent`, and a duplicate event answers 200 without reprocessing. Events are matched only by our own reference.
- Every charge carries an idempotency key, so a retry can never double-charge.

## 9. Build order

Each slice has its own plan, tests and approval. Migrations are a reviewed release step per `docs/runbooks/migrations.md`.

1. **Foundation.** App layout, custom `operators.User`, Django Ninja, error shape, public ids, settings for origins, test tooling, OpenAPI published.
2. **Accounts and sign-in.** Account, email code, sessions, policy acceptance, account settings, then Google, then Facebook.
3. **Sites and subdomains.** Site, Subdomain ledger, trial subscription start, subdomain check, site creation and settings, business profile, public host lookup.
4. **Billing.** Six builds, each with its own ClickUp task, plan and approval:
   - B1 (z8uq9mdwrv): subscription lifecycle and cycles, state transitions, cancel and reactivate, the public host lookup. Adds the worker and beat services and the task queue dependency.
   - B2a (z8uq9me581): launch payment by bank transfer with proof, confirmed by an operator, and the six-month Launch Promo. Built before B2.
   - B2 (z8uq9mdwrw): payment method and collection. Xendit customer and card setup, webhook inbox, charging and retries. Built when a gateway is approved.
   - B3 (z8uq9mdwry): Annual Plan.
   - B4 (z8uq9mdwrz): usage ledger and overage (item 5).
   - B5 (z8uq9mdwt0): Billing Statements (item 6).
   - B6 (z8uq9mdwt1): referral codes and Subscription Credits (item 7).
5. **Usage and overage.** Usage entries and overage charge lines.
6. **Statements.** Numbered statements, PDF, email delivery.
7. **Referrals.** Referrer lookup at signup, credit ledger applied to the base charge.
8. **Mobile OTP.** Texts go through NegosyoOnline's own phone gateway on a Globe SIM (see section 12). Design: `docs/superpowers/specs/2026-11-10-sms-gateway-mobile-verification-design.md`. This slice does not wait for slices 4 to 7.

The frontend can start against slice 2 as soon as its OpenAPI description is published on staging.

## 10. Out of scope

- Commerce: products, orders, buyer checkout, buyer payments, members, payouts, the Santé pack, media library. These live in other legacy apps that have not been mapped; they need their own scope after this one (amended on 2026-11-11 for the Santé port, see section 12). The usage figures in slice 5 depend on orders and media, so slice 5 ships its ledger and charging first and is fed real numbers when commerce exists.
- Facebook features other than sign-in (pages, posting, catalogue, messaging).
- Custom domains per subscriber (for example `live4moreonline.store`).
- Annual billing.
- Data retention and disposal automation beyond the dates recorded on the subscription, with two exceptions. The SMS retention job is in scope with slice 8: send records are deleted after 1 year and mobile verification challenges 24 hours after expiry. The account-side retention rules of NPC Build 7 (z8uq9mdwdy) are in scope as manual commands behind `RETENTION_ENABLED`, which is off by default: accounts whose email was never verified are disposed of 30 days after sign-up; an account is disposed of on request; expired sessions and admin sign-in codes are deleted 30 days after they expire; email send records are deleted after 2 years; an account and its sites are exported on request. NPC Build 10 (z8uq9mdzve) adds, behind the same switch: the personal values of admin audit entries are blanked 1 year after the entry was written, and the rows are kept; an account's audit events are deleted after 1 year, except `email_changed` events, which are kept 2 years; and an account disposal also deletes the email send records of the addresses the account used before an email change. Still out of scope: their schedule (slice 4), the rules that need billing, store disposal, customer erasure, and anything that touches billing or tax records.
- Production deployment.

## 11. Open decisions

- ~~SMS provider for mobile OTP (blocks slice 8 only).~~ Answered on 2026-11-10: NegosyoOnline's own phone gateway on a Globe SIM. See section 12.
- ~~Confirmation of the billing values in section 7 (blocks slice 4 only).~~ Answered on 2026-11-11 by the Billing Build B1 amendment (section 12), except the retry schedule after a failed payment, which the B2 plan confirms.
- Where statement PDFs are stored (blocks slice 6 only).

## 12. Amendments

- 2026-11-10: Facebook sign-in moved from out of scope into the MVP after the Meta app review approved `public_profile` and `email`. `GoogleIdentity` became `ExternalIdentity` so one table serves both providers. Sections 2, 4, 6, 8, 9 and 10 were updated. The Meta app id and secret are Railway variables added when slice 2 is built.
- 2026-11-10: slice 3 decisions, approved by Coach Ed with the plan `docs/superpowers/plans/2026-11-10-backend-slice3-sites-onboarding.md`:
  - Section 8 gains `GET account/setup/`: the onboarding state, derived on each read from persisted fields. It is served by the sites app.
  - Section 6, `sites`: `SiteBusinessProfile` also holds the registered name, BIR serial number, complaints contact name, privacy contact email and a "shipping estimates confirmed" flag; the social links on `Site` are an Instagram link and a TikTok link. A new table, `Onboarding`, holds one row per account: the first site, when setup started, and when the minimum was first completed.
  - Section 7: slice 3 creates the Site, its Subdomain and its business profile in one transaction but no Subscription. Slice 4 adds the Subscription to that transaction and creates one for each site made before it, with trial dates taken from the account's creation date, as this section already says.
  - Section 9 item 3: "trial subscription start" and "public host lookup" move to slice 4, because storefront availability depends on the subscription state.
  - Operator actions on the subdomain ledger (rename, release, reserve a name) are not in slice 3; they need `OperatorAuditEntry`.
- 2026-11-10: Account Settings, from Coach Ed's ruling on ClickUp task z8uq9mach9 dated 2026-11-10 (every section of the Account Settings page is live at launch):
  - Section 8 gains `POST account/connections/{provider}/start/` and `POST account/connections/{provider}/disconnect/`, where the provider is `google` or `facebook`. The start answers with the provider's address and marks the signed state cookie as a connect; the provider's existing sign-in callback then links the identity to the account that started, signs nobody in, creates no account and matches nothing by email, and returns to the dashboard at `/account/#connect=<provider>&result=<result>`. The results are `connected`, `in_use`, `already_connected`, `cancelled`, `failed` and `invalid_state`; the start itself answers `not_configured` or `already_connected`. Email-code sign-in always remains.
  - `GET account/` also returns `writes_open`, `providers`, `devices`, `signed_in_count`, `mobile`, `email_changed_at` and `email_change_available_at`. `PATCH account/`, `POST account/email/confirm/` and the disconnect answer with the same shape. The account still has one `name`; the dashboard splits it into two boxes.
  - Confirming a new email also emails the old address and pauses further email changes for 24 hours (`email_change_cooldown`, 409). The pause is read from the `email_changed` audit event; no column was added.
  - A connect and a disconnect each email a security notice to the account's email.
  - `POST account/sessions/sign-out-others/` also returns `signed_in_count`.
  - No model changed. The connected provider's email is not stored or shown.
- 2026-11-10: admin two-step sign-in and hq, from Coach Ed's rulings of 2026-11-10 on ClickUp task z8uq9mdwdx (the NPC work is rebuilt in this backend; hq is rebuilt) and the design he approved on 2026-11-06. Sections 4 and 6 were updated. Plan: `docs/superpowers/plans/2026-11-10-backend-npc-build6-admin-two-step-hq.md`.
  - hq sign-in is an emailed 6-digit code, then an authenticator code or one recovery code. Django admin sign-in is the password, then an authenticator code or one recovery code. One check serves both.
  - Sessions last 12 hours; the email code 10 minutes; five wrong codes pause sign-in on both for 15 minutes and end live sessions; ten one-time recovery codes.
  - Identities are added, enrolled, reset and disabled only with a terminal command. No browser path exists.
  - The first hq page shows the verified card, the admin's last 10 sign-in events and Sign out. The health page and settings screens are not in the MVP.
  - Django admin enforcement sits behind `ADMIN_TWO_STEP_REQUIRED`, off by default. hq answers only on `HQ_HOST`; empty means hq is off.
  - Until slice 4 adds the worker, the sign-in code and the lockout notice are sent from a background thread of the web service.
- 2026-11-10: SMS provider, from Coach Ed's ruling of 2026-11-10 on ClickUp task z8uq9md33v (NPC Build 1). Sections 9 and 11 were updated.
  - NegosyoOnline builds its own gateway: its own phone with a SIM on an ordinary Globe mobile plan. No send endpoint in the legacy system is used and no legacy code is copied.
  - Only sign-in codes and order-status texts are sent. Only the recipient number and the message text leave. No marketing. Globe has no access to the database.
  - The text of a sign-in code or an order-status text is never stored in the database. Send records are kept 1 year, then deleted by a retention job. (Corrected on 2026-11-11: this line first said the text of a message is never stored. Inbox conversation text will be stored; see the 2026-11-11 entry.)
  - The paid Dedicated SMS Line add-on (z8uq9mb19f) is a later task that depends on this one.
  - The design is `docs/superpowers/specs/2026-11-10-sms-gateway-mobile-verification-design.md`. It was proposed on 2026-11-10 and approved on 2026-11-11; see the next entry.
- 2026-11-11: SMS gateway and mobile verification design approved, on ClickUp task z8uq9md33v. Coach Ed, 2026-11-11: "Approve the design". Sections 3, 6, 8 and 10 were updated. Design: `docs/superpowers/specs/2026-11-10-sms-gateway-mobile-verification-design.md`; its section 2.1 holds each ruling with its source. Plan: `docs/superpowers/plans/2026-11-10-backend-npc-build1-sms-gateway.md`.
  - The approval covers the design and the five-pull-request delivery order. It does not cover creating either migration, the Railway variable `SMS_GATEWAY_ENABLED`, creating the Android app's repository, or turning incoming texts on for any phone. Each is approved separately.
  - The phone runs NegosyoOnline's own small Android app, in its own repository. It asks the API host for waiting texts under `/gateway/v1/`; no Firebase and no third-party service is in the path.
  - A sign-in code is sent at once. The text of a code or an order-status text waits encrypted in the cache for at most 10 minutes and is never in PostgreSQL.
  - Inbox conversation text will be stored, in its own table, created by the Dedicated SMS Line add-on (z8uq9mb19f). The app can already pass incoming texts to the server behind a per-phone switch that is off by default and stays off for the system phone; until the add-on's table exists the server stores none. How long inbox text is kept is not settled.
  - Daily limits: they start at 2,000 per site and 2,000 for the platform (Coach Ed, 2026-11-11; first set at 4,000), kept with the kill switch in one settings row the admin edits; each change is audited.
  - The notice for a changed mobile number goes by email only. `GET account/mobile/` is not built.
  - The NPC filing and policy wording must be checked before any inbox text is stored and before any reply or marketing text is sent. This amendment changes no filing or policy text.
- 2026-11-11: hq is the only admin door, from Coach Ed's ruling of 2026-11-11 on ClickUp task z8uq9mdxvt ("lets remove the django admin login.. all will now be in hq dashboard"). Section 4 was updated. Plan: `docs/superpowers/plans/2026-11-11-backend-npc-build9-hq-only-admin-door.md`.
  - This replaces two lines of the 2026-11-10 "admin two-step sign-in and hq" entry above: the Django admin no longer has a password sign-in or its own second step, and `ADMIN_TWO_STEP_REQUIRED` no longer exists.
  - The Django admin lists are kept as they are and served only on `HQ_HOST` at `/admin/`, to an hq session that passed the emailed code and the authenticator or recovery code. The user they see is the staff user linked to that admin identity; Django permissions are unchanged. No other host serves `/admin/`.
  - The lists use Django's own CSS and script, served from the hq host to a verified session only. The hq pages themselves stay without script.
  - The first hq page links to the lists. Sign out on a list is hq's sign-out. New admin screens are built in hq.
- 2026-11-11: account-side retention, from ClickUp task z8uq9mdwdy (NPC Build 7) and Coach Ed's approval of its plan on 2026-11-11 ("Approve as recommended"). Section 10 was updated. Plan: `docs/superpowers/plans/2026-11-11-backend-npc-build7-account-retention.md`.
  - One retention mechanism serves every build: a registry of rules in `apps/common/retention.py`, `manage.py run_retention` with a dry run, and one `retention_run` audit entry per run with the count per rule. The SMS build registers its own rules there.
  - `RETENTION_ENABLED` is off by default and is checked inside each account-side rule: off, a run of them deletes and blanks nothing, whoever starts it.
  - Built first: expired subscriber sessions, admin sign-in codes, hq sessions and Django session rows are deleted 30 days after they expire; email send records after 2 years. Built next, in its own pull request: account disposal (personal fields blanked, the row kept, each blanking in the audit record), the never-verified rule, the send guard and the export. No model changed and no migration is needed.
  - No schedule is part of this. Runbook: `docs/runbooks/retention.md`.
- 2026-11-11: retention gaps left after Build 7, from ClickUp task z8uq9mdzve (NPC Build 10) and Coach Ed's approval of its plan on 2026-11-11 ("Approve as recommended"). Section 10 was updated. Plan: `docs/superpowers/plans/2026-11-11-backend-npc-build10-retention-gaps.md`.
  - Admin audit log (ruling D4): the rule `admin_audit_personal_values` blanks the personal values of admin sign-in audit entries 1 year after they were written and stamps each row. No audit entry is deleted. An action declared as kept whole can no longer be written with a personal value.
  - `create_first_site` refuses an account whose `closed_at` is set, checked under the lock a disposal takes.
  - Security records: the rule `account_audit_events` deletes an account's audit events 1 year after they were written. An `email_changed` event is kept 2 years, an exception Coach Ed approved: it is the only record of an address the account used before, and the send records of that address live 2 years.
  - Account disposal also deletes the email send records of each address the account used before an email change: those written up to the moment of the change, plus the change notice sent to the old address. Admin categories are excluded, as in Build 7.
  - No model changed and no migration is needed. The rules are behind `RETENTION_ENABLED` and run only by `manage.py run_retention`.
- 2026-11-11: the Santé capability check and the derived "Santé Site Owner" label, from ClickUp task z8uq9machh (Phase 6) and the design Coach Ed approved on 2026-11-11 (comment 1100430000480348 on that task). No model changed, no migration, no new endpoint, no Railway variable and no frontend change.
  - Capability check. `services.site_for(account, site_id, template=templates.SANTE)` is how a Santé-only feature reaches its site. A site whose stored template differs answers the same 404 as a missing, removed or foreign site. Without the argument `site_for` behaves as before. The check does not look at `operator_hold` or the subscription state: those decide whether a site is served (section 7), not whether its owner may manage it. Every Santé-only endpoint must use it and must not read the template, a label or a "this is Santé" flag from the request.
  - Label rule. An Account is a "Santé Site Owner" when it is not closed and owns at least one Site with template Santé, `deleted_at` empty and `operator_hold` false. When Billing exists, the site's subscription must also be on trial, active or needing payment attention; that condition is added in `apps/sites/labels.py`, the one place the rule lives.
  - The label is worked out on each read and never stored: `apps/sites/labels.py` holds the wording, `eligible_sante_sites()`, `with_sante_site_owner(accounts)` and `labels_for(account)`. It is for support, reporting and communications and never authorizes anything.
  - Who sees it. Operators only, in the admin lists reached through hq: a yes/no column "Santé Site Owner" and a filter of the same name on the Subscribers list, and a yes/no column "Counts for Santé Site Owner" on the Sites list. No Subscriber API returns it: not `GET account/`, `GET account/setup/` or `GET sites/`.
  - No gate is built. Santé is the only site type: the server offers only Santé and refuses any other type, with nothing created. There is no launch gate, creation switch or allow list to look for.
  - Handoff to Santé setup (a contract; nothing of Santé's is built here):
    - What is handed over: a Site with `template = "sante"`, owned by one Account, created by the Subscriber in onboarding with no operator step and no approval. That stored template is the only proof that a site is a Santé site.
    - How a Santé feature checks it: through `site_for(account, site_id, template=templates.SANTE)`. Never from the request, the dashboard, a label or the navigation.
    - Where Santé setup lives: at Site level, under the selected site. Not in Account Settings and not in the profile menu. A second Santé site of one Account has its own Santé setup.
    - Sponsor ID (Santé's rule, restated, not built here): one sponsor ID per Site, entered by the Subscriber, their own. It starts blank. It is never filled from Coach Ed's ID, an operator, the referrer of the Account, another site of the same Account, an environment variable, a seed or a migration. A site without one cannot start member registration; a blank sponsor ID blocks nothing else: not site creation, not onboarding completion, not Business Settings. Onboarding does not ask for it. Its storage, format, validation, audit and page belong to the Santé task that builds registration.
    - This phase adds no "Santé setup" link or button, because the page does not exist. The entry point is added by the Santé task that ships the page.
    - Santé features must not use "Santé Site Owner" for any decision. If Santé needs "does this account have a Santé site", it asks the Site table through the sites service.
- 2026-11-11: the SMS daily limits start at 2,000 per site and 2,000 for the platform, not 4,000 (Coach Ed, 2026-11-11: "for the fair-use limits, let's start with 2000 sms per day with the same sending interval"; ClickUp task z8uq9md33v). The SMS gateway entry above was updated. The sending interval is unchanged. Migration `notifications.0003_sms_daily_limits_start_at_2000`.
- 2026-11-11: Billing Build B1, subscription lifecycle and cycles, from ClickUp task z8uq9mdwrv and Coach Ed's approval of its plan on 2026-11-11 ("Approve as recommended"). Plan: `docs/superpowers/plans/2026-11-11-backend-billing-b1-subscription-lifecycle.md`. Sections 6, 7, 9 and 11 were updated.
  - Rulings, each in section 2 of the plan:
    - D1: payment is accepted until the deletion date of that site: day 44 for a trial that ended with no payment method (14 + 30), service end + 90 days if the site ever paid.
    - D2: for a first site created after the 14 days, the dates count from the trial end, not from the site's creation. The gap this leaves (a first site created after its own deletion date) is not built in B1 and is decided with B2.
    - D3: the task queue is Celery, with Redis as the broker through `REDIS_URL`.
    - D4: two Railway services, `worker` and `beat`, on staging, and the variable `BILLING_ENABLED`, off by default. While it is off, site creation writes no subscription, the billing endpoints and the host lookup answer 404, and the sweep does nothing.
    - D5: one migration, three tables, in `apps/billing` only.
    - D6: the subscriptions of sites made before B1 are written by a command, not a migration.
    - D7: `billing_exempt` on the subscription marks an approved test site, which is served and never billed. It is set once by a terminal command with an operator, a reason and an audit entry, and is never cleared.
    - D8: before payments exist, reactivate only withdraws a scheduled cancellation inside the paid period; otherwise it answers 409 `payment_required`.
    - D9: a cancellation after the renewal time with no recorded payment outcome ends service at once; the pending base fee is not due; the 90-day clock starts.
    - D10: the trial reminder emails are B2's.
    - D11: a suspended or ended site answers 403 `billing_only` on `PATCH sites/{site_id}/` and `GET`/`PATCH sites/{site_id}/business-profile/`. The site list, site detail, `account/setup/`, the account endpoints and the billing endpoints answer as before.
    - D12: this amendment.
    - D13: the disposal step. An account disposal first cancels the account's subscriptions (`prepare_disposal`). Inside a paid term it cancels at the end of the term and the disposal is refused for now with `paid_term_running`; otherwise service ends at once and the disposal proceeds. Billing rows are never deleted or blanked by a disposal.
    - D14: a suspension records its dates (R-4b, R-4c, R-4d): payment is refused at or after the deletion date, and a later cancellation does not restart the clock.
    - D15: a never-paid subscription keeps its download and deletion dates empty until payment collection is live (R-4a), so it has no last date to pay. B2 starts those clocks on the go-live day.
  - The approval covers the plan with these rulings, this amendment, the migration file `apps/billing/migrations/0001_initial.py` and the Celery dependency.
  - The approval does not cover, and each is still Coach Ed's own yes, on staging only: running the migration, setting `BILLING_ENABLED`, running the backfill (dry run included), creating the `worker` service, creating the `beat` service, any other variable. Production is not part of it.
- 2026-11-11: the Santé Barley Site MVP is ported into NegosyoOnline, from Coach Ed's rulings of 2026-11-11 on ClickUp task z8uq9m92fv (comments 1100430000483854 and 1100430000484157) and his approval the same day of this scope amendment and of the slice plan ("Yes, approve" to each). Sections 1 and 10 carry a pointer to this entry. This entry is words only: it adds no code, model, migration, dependency, variable or service.
  - The port ruling. The Santé Barley Site MVP (z8uq9m92fv) was built and approved in TWCinc. It is ported and adapted into this backend and the frontend, not rebuilt from scratch and not copied wholesale. For this work only, section 1's "never copy its code" is replaced by: TWCinc Santé code may be copied and adapted, never imported. No TWC dependency, secret or deployment configuration is carried over. For everything else section 1 stands.
  - Section 10. It no longer excludes products, orders, buyer checkout, buyer payments, members, payouts and the Santé pack. The media library, custom domains and production deployment stay out of scope.
  - Rendering and sessions. `apps/storefronts` in the frontend renders the Santé site and the Member dashboard on its server, with the approved CSS, JS and images unchanged. Its server holds the member, cart and checkout cookies on the site's own host and calls Django on a new storefront-only surface with one server secret. Storefront origins stay out of the API allowlist; section 4 is unchanged. The secret itself is not yet approved: neither its Railway variable nor its Vercel variable.
  - Texts. One-time codes (member sign-in, cash-on-delivery verification, withdrawal request) and order-status texts may go to members and buyers. Security notices and the Subscriber's new-withdrawal notice go by email only. No text carries a link. For members and buyers this widens the 2026-11-10 SMS provider entry's "Only sign-in codes and order-status texts are sent" to include cash-on-delivery verification codes and withdrawal-request codes; "No marketing" stands. The privacy wording for these recipients is checked before any member text is switched on.
  - Launch control. No gate is built, as already ruled in the Santé capability entry above. Each Santé surface ships behind its own settings switch, off by default.
  - Unchanged and restated: one site per account (section 2); subdomains only; Santé is the only site type.
  - Planned backend apps, named only: Santé settings, catalog, members, orders, pack, product checkout, earnings. Naming them here approves none of them. Each model, migration, dependency, variable and service still needs its own approval.
  - Open, and not approved by this entry: where private files live; per-site Xendit keys and webhook; whether E8 earnings is approved for porting.
- 2026-11-11: the storefront surface and its server secret, from ClickUp task z8uq9me4yt (Santé port slice 3). Coach Ed approved the secret's two variables for staging on 2026-11-11 ("Approved", z8uq9m92fv comment 1100430000488789), which answers "The secret itself is not yet approved" in the entry above; he sets the values himself. The surface is `/storefront/v1/` in `apps/storefront`, outside `/v1/`, behind the switch `STOREFRONT_ENABLED`, off by default, and the credential is `STOREFRONT_SERVER_SECRET`, the same name on Railway `api` and on the Vercel storefronts project. It refuses a site that is not served exactly as `GET /v1/public/sites/by-host/` decides it. No model, migration or dependency. Turning the switch on in staging is its own approval; production is not covered. Runbook: `docs/runbooks/storefront.md`.
- 2026-11-11: Billing Build B2a, launch payment by bank transfer with proof and the six-month Launch Promo, from ClickUp task z8uq9me581. Coach Ed approved the plan `docs/superpowers/plans/2026-11-11-backend-billing-b2a-bank-transfer-launch-promo.md`, its migration `billing.0002_bank_transfer` and this amendment on 2026-11-11 ("Approve all as recommended", recorded in comment 1100430000489390). Sections 7, 8 and 9 were updated. Proofs are confirmed in the Django admin for launch. The answer to B1's D2 gap: a first site created after its trial ended has 30 days to pay from its own creation. Not approved by this entry, each still his own yes: the receiving account, the private proof store with its token and any dependency, each variable and switch in a shared environment, the wording of the emails, the promo's end date, and every production action.
