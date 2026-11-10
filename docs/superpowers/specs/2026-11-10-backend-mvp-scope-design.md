# Backend MVP scope and design (2026-11-10)

Status: design approved by Coach Ed on 2026-11-10. Amended the same day to add Facebook sign-in (see section 12). This document is the written MVP scope that `CLAUDE.md` requires before features, models or integrations are built. It contains no code. Each slice in section 9 gets its own implementation plan and approval before work starts.

## 1. Purpose

Rebuild the NegosyoOnline platform backend in this repository as a clean Django API. The legacy implementation inside TWCinc is the behaviour reference only: read it to understand a rule, never copy its code, dependencies or configuration. A separate session builds the frontend against the API described here.

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
- Operators are Django staff users. `AUTH_USER_MODEL` is a custom `operators.User`, set in slice 1 because it must exist before the first migration. Operators use the Django admin. The admin's console, hq, is rebuilt by Coach Ed's ruling of 2026-11-10 (ClickUp task z8uq9mdwdx): Django renders it on its own host, with no script and no external asset. hq and the Django admin both require two-step sign-in; see section 12.
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
- `Subscription`: site (one each), state, trial start and end, attention, cancel and service-end dates. The only place trial dates live.
- `SubscriptionTransition`: subscription, from state, to state, trigger, idempotency key (unique), at.
- `BillingCycle`: subscription, sequence, start, end, settlement (`pending`, `paid`, `lapsed`).
- `UsageEntry`: cycle, kind (`orders`, `storage`, `video`), quantity, source reference (unique).
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
- The 14-day trial is counted from account creation, as in legacy. The Subscription copies those dates when it is created. If the first site is created after the 14 days have passed, the subscription starts in `cancelled_no_payment_method` and the site goes live once a card is added and the first charge succeeds.
- A storefront is served only when the site is not deleted, has no operator hold, and its subscription is `trial_active`, `active` or `payment_requires_attention`. This fixes a legacy gap where billing state never drove storefront availability.
- Billing values carried from legacy, to be confirmed in the slice 4 plan before they are coded:
  - plan: PHP 2,499 per month, monthly anniversary cycles;
  - included per cycle: 1,000 orders, 5 GB storage, 50 GB video; overage PHP 5 per order, PHP 30 per GB storage, PHP 10 per GB video;
  - failed payment: retries after 1, 3 and 5 days, then a 7-day attention period before suspension;
  - after cancellation: 30-day download window, deletion at 90 days.

## 8. API contract

All paths are under `/v1/`. Errors use one shape: `{"error": {"code": "...", "message": "...", "fields": {...}}}` with a normal HTTP status (400, 401, 403, 404, 409, 429). Lists are paginated with a cursor.

| Area | Endpoints |
|---|---|
| Public | `GET plan/`, `GET referrals/{code}/`, `GET public/sites/by-host/?host=` |
| Sign-in | `POST auth/email-code/request/`, `POST auth/email-code/verify/`, `GET auth/google/start/`, `GET auth/google/callback/`, `GET auth/facebook/start/`, `GET auth/facebook/callback/`, `POST auth/facebook/confirm-email/`, `POST auth/logout/` |
| Account | `GET me/`, `GET account/setup/`, `GET PATCH account/`, `POST account/email/start/`, `POST account/email/confirm/`, `GET account/sessions/`, `POST account/sessions/sign-out-others/`, `POST account/connections/{provider}/start/`, `POST account/connections/{provider}/disconnect/`, `POST account/mobile/start/`, `POST account/mobile/verify/`, `GET account/referrals/` |
| Sites | `GET POST sites/`, `POST sites/subdomain-check/`, `GET PATCH sites/{site_id}/`, `GET PATCH sites/{site_id}/business-profile/` |
| Billing | `GET sites/{site_id}/billing/`, `PUT sites/{site_id}/billing/profile/`, `POST sites/{site_id}/billing/payment-method/setup/`, `POST sites/{site_id}/billing/cancel/`, `POST sites/{site_id}/billing/reactivate/`, `GET sites/{site_id}/billing/usage/`, `GET sites/{site_id}/billing/statements/`, `GET sites/{site_id}/billing/statements/{number}/`, `GET sites/{site_id}/billing/statements/{number}/pdf/` |
| Webhooks | `POST webhooks/xendit/`, `POST webhooks/facebook/data-deletion/` |

`public/sites/by-host/` is the storefront lookup. It is unauthenticated and cacheable, returns only public branding, template and availability, and answers 404 for unknown, reserved or unavailable hosts.

The SMS gateway phone uses three calls that are outside `/v1/` and are not in the OpenAPI description: `POST /gateway/v1/poll`, `POST /gateway/v1/report` and `POST /gateway/v1/received`. They carry the phone's own token, not a subscriber's session.

Payments and webhooks:
- Xendit is the provider. Card details are entered on Xendit's hosted page; the API never sees card numbers.
- Webhooks: body size cap, constant-time check of the callback token, insert into `WebhookEvent`, and a duplicate event answers 200 without reprocessing. Events are matched only by our own reference.
- Every charge carries an idempotency key, so a retry can never double-charge.

## 9. Build order

Each slice has its own plan, tests and approval. Migrations are a reviewed release step per `docs/runbooks/migrations.md`.

1. **Foundation.** App layout, custom `operators.User`, Django Ninja, error shape, public ids, settings for origins, test tooling, OpenAPI published.
2. **Accounts and sign-in.** Account, email code, sessions, policy acceptance, account settings, then Google, then Facebook.
3. **Sites and subdomains.** Site, Subdomain ledger, trial subscription start, subdomain check, site creation and settings, business profile, public host lookup.
4. **Billing core.** Xendit customer and card setup, webhook inbox, cycles, charging and retries, state transitions, cancel and reactivate. Adds the worker and beat services and the task queue dependency.
5. **Usage and overage.** Usage entries and overage charge lines.
6. **Statements.** Numbered statements, PDF, email delivery.
7. **Referrals.** Referrer lookup at signup, credit ledger applied to the base charge.
8. **Mobile OTP.** Texts go through NegosyoOnline's own phone gateway on a Globe SIM (see section 12). Design: `docs/superpowers/specs/2026-11-10-sms-gateway-mobile-verification-design.md`. This slice does not wait for slices 4 to 7.

The frontend can start against slice 2 as soon as its OpenAPI description is published on staging.

## 10. Out of scope

- Commerce: products, orders, buyer checkout, buyer payments, members, payouts, the Santé pack, media library. These live in other legacy apps that have not been mapped; they need their own scope after this one. The usage figures in slice 5 depend on orders and media, so slice 5 ships its ledger and charging first and is fed real numbers when commerce exists.
- Facebook features other than sign-in (pages, posting, catalogue, messaging).
- Custom domains per subscriber (for example `live4moreonline.store`).
- Annual billing.
- Data retention and disposal automation beyond the dates recorded on the subscription, except the SMS retention job, which is in scope with slice 8: send records are deleted after 1 year and mobile verification challenges 24 hours after expiry.
- Production deployment.

## 11. Open decisions

- ~~SMS provider for mobile OTP (blocks slice 8 only).~~ Answered on 2026-11-10: NegosyoOnline's own phone gateway on a Globe SIM. See section 12.
- Confirmation of the billing values in section 7 (blocks slice 4 only).
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
  - Daily limits: 4,000 per site and 4,000 for the platform, kept with the kill switch in one settings row the admin edits; each change is audited.
  - The notice for a changed mobile number goes by email only. `GET account/mobile/` is not built.
  - The NPC filing and policy wording must be checked before any inbox text is stored and before any reply or marketing text is sent. This amendment changes no filing or policy text.
