# Custom domains (apps/domains, branches B1 engine, B2 purchase and B3 store email)

ClickUp z8uq9mhgn7 (build), design z8uq9mcyu0. This is the engine: models, names, the state
machine, the Vercel and Cloudflare DNS adapters, the HTTPS probe, serving and lifecycle removal.
The purchase (Registrar adapter, Buy, caps, staff actions) is branch B2, described in the section
"Purchase (B2)" below; the store email on the customer's domain (Resend) is branch B3, in the section "Store email (B3)" below. Nothing in this file has been done on any
environment. Staging runs on Bunny containers `api`,
`worker` and `beat` (see `docs/runbooks/bunny-staging.md`).

## Switches (all off by default; names only, values are never printed)

| Name | Where | Meaning |
|---|---|---|
| `DOMAINS_ENABLED` | api and worker | `1` turns the feature on. Off: no table is read, no provider is called, `served()` takes no second branch, the three tasks return at once. |
| `DOMAINS_PROVIDER` | worker | `off` (default), `fake` (in-memory fakes, needs `DJANGO_DEBUG=true`, local only) or `real`. The api container stays `off`. |
| `DOMAINS_PROVIDER_PAUSED` | worker | `1` is the kill switch: every provider call is refused, every timer pauses, removals queue as `removal_pending`. Setting it needs no approval. The Bunny worker reads settings at start, so set the variable and restart the worker; the time to take effect is measured before the first real purchase (addendum M3). |
| `DOMAINS_PLATFORM_ZONES` | worker | Comma-separated names of the platform zones the DNS adapter must never write in (for example `negosyonline.com`, `negosyoonline.com`, `santebarleyph.com`, `live4moreonline.store`). Empty: the real DNS adapter refuses everything and the daily audit reports an error. |
| `CLOUDFLARE_ACCOUNT_ID`, `VERCEL_STOREFRONTS_PROJECT_ID` | worker | Non-secret ids the adapters pin. |
| `CLOUDFLARE_DNS_API_TOKEN`, `VERCEL_API_TOKEN` | worker only | Secrets. Coach Ed enters them in the Bunny dashboard himself. The `api` and `beat` containers hold none. |
| `DOMAINS_PSL_FILE` | optional | Path of the Public Suffix List file. Default `apps/domains/data/public_suffix_list.dat`. |

`SITE_PARENT_HOST` is untouched. `beat` needs no new variable: its schedule is static in
`config/settings.py` and each task checks `DOMAINS_ENABLED` when it runs on the worker.

## The Public Suffix List

The list is vendored in the repo at `apps/domains/data/public_suffix_list.dat` (Coach Ed approved the
download on 2026-11-22, ClickUp comment 1100430000610401). This code never downloads it at run time.

* Source: https://publicsuffix.org/list/public_suffix_list.dat
* Licence: MPL-2.0
* Downloaded: 2026-11-22; 342871 bytes; sha256 `017501d3fa7911c60ac7e0f68df4122b07216c585c15dc30e0f0c74b8d13a2b9`

If the file is ever absent, every name is refused (fail closed) and `manage.py check --deploy` shows
`domains.W001`.

To refresh it: download the same URL to a scratch folder, confirm it is plain text and contains
`// ===BEGIN ICANN DOMAINS===`, copy it over the vendored file, update the date, size and sha256 above
(`shasum -a 256`) and in the branch notes, run `uv run python manage.py test apps.domains`, and ship it
through a reviewed branch. Then restart the worker and api (the loader caches by file mtime).

## What the worker does

* `domains.sweep` (every minute) marks sites whose first-payment hook was skipped as eligible,
  advances the purchase states (`checking`, `quoted`, `confirmed`, `awaiting_gate`, `registering`)
  and the setup states (`registered`, `zone`, `connecting`, `activating`, `removal_pending`), one
  step each, one claim at a time.
* `domains.hourly_check` reads Vercel and probes each `live` or `attention` claim. Six hours of
  failure is `attention`; 14 days of it is `failed`; a recovery returns to `live`.
* `domains.daily_sweep` (05:00 Manila) queues lifecycle removals, runs the zone audit, compares the
  Vercel project's domain list with the recorded hostnames, reads each registration (expiry and
  auto-renew, through the Registrar adapter) and runs the sentinel price Check (see B2).
* Timers: 72 hours in `zone` and `connecting`, 24 in `activating`.

## States this branch moves

`registered` to `zone` to `connecting` to `activating` to `live`; `attention`, `failed`, `conflict`
and `removal_pending` to `ended`. A claim reaches `registered` only through the purchase or a
re-attach (see B2). `failed` is retried in
place (`machine.retry`, back to `zone`), never to a purchase step. A registration that is not
`succeeded` is never worked on. Names: a claim's name is a display copy; before any provider call
it is compared with the site's own registration name, and every hostname is only that name or
`www.` plus it.

## Serving

`apps.sites.api_public.site_for_host` tries the manual `SiteCustomHost` first, then the subdomain,
and only for a host that is not under the parent host the second branch,
`apps.domains.hosts.site_for_hostname`: the `www.` hostname of a `live` or `attention` claim whose
name is the site's own `succeeded` registration. The apex is answered by a Vercel 308 redirect and
never reaches Django. `custom_hosts.active_host` falls back to `canonical_host`, so the three
emailed links and `custom_host` follow the live domain, and fall back to the subdomain otherwise.
`GET /storefront/v1/domain-probe/?host=` (server credential required) answers `{"site": <id>}` by
domain state, not billing, for `activating`, `live` and `attention`.

## Zone protection (one account holds platform zones and customer zones)

The DNS token is allowed on all zones and denied on every platform zone (zone ids, set by Coach
Ed). The code adds its own guards: the adapter reads a zone only for the name of the site's own
`succeeded` registration, refuses every `DOMAINS_PLATFORM_ZONES` name, requires the zone name and
account id to match, writes only A, CNAME, TXT and MX records, DNS-only, named the domain or under
it, and deletes only a recorded id in its recorded zone. A new platform zone: create it, add its
id to the token's deny policy and its name to `DOMAINS_PLATFORM_ZONES` in the same sitting, before
any record is written in it. The daily audit alerts, by name, on a zone that is neither a
`succeeded` registration nor a platform zone, and on a platform zone the token can see.

## Lifecycle

A claim is removed (not just dark) when the site is deleted, its subscription is gone, it is
cancelled past its service end, cancelled with no payment method, or its stored deletion date has
passed (billing must be enabled; unknown never removes). Removal takes down only recorded Vercel
names and DNS records. The registration is kept, an `auto_renew_review` alert is raised, and a
returning Subscriber can re-attach the same registration.

## Alerts

Alerts are error log lines on the `domains` logger (the Sentry log handler picks them up) with
fixed fields: names of zones and domains, codes, never a token, a header or a response body.

## Release order (addendum r12.1)

1. The Supervisor reads `migrate --plan` against staging: it must list only `domains.0001_initial`.
2. The release is approved; the api container applies the migration at start under its lock.
3. Verify `/healthz/` and `/readyz/` and that the `domains_*` tables exist.
4. Only then `DOMAINS_ENABLED=1`. Provider stays `off` until the step 0 parts are done.

## Purchase (B2)

ClickUp z8uq9mhgqt. Code only: no new table (everything is in `domains.0001_initial`). Nothing here
has been done on any environment, and nothing is set on any container until the Manager and Coach Ed
do it.

### The rule (ruling SD1, Coach Ed, ClickUp 604765)

A Subscriber may pick a domain after the first confirmed subscription payment of any plan, and a trial
never counts. An early first payment counts. The gate is: subscription state `ACTIVE`, at least one
`PAID` cycle (`billing.services.has_paid_cycle`), a Santé site that is not deleted and not on operator
hold, and not billing-exempt. The first year of the domain is included; **the year-2 renewal charge is
not built** and needs Coach Ed's go. One registration per site for life; one site per name.

### Variables (names only; all default off, empty or 0 = nothing can be bought)

| Name | Where | Meaning |
|---|---|---|
| `DOMAINS_PURCHASE_ENABLED` | worker | `1` allows a purchase. Off: a Check still works, a Buy for a new name is refused, the worker sends nothing. A re-attach of a `succeeded` registration ignores it (it spends nothing). |
| `CLOUDFLARE_REGISTRAR_API_TOKEN` | worker only | Secret. Coach Ed enters it himself, and only when step 0 part C1 is reached. The `api` and `beat` containers must not hold it: it is what keeps `register` out of a web request. |
| `DOMAINS_MAX_COST_USD` | worker | The price ceiling in dollars. **Value to set at step 0 Part C: 12.00.** Both the registration price and the renewal price must be at or under it, at purchase. Unset or 0 refuses everything. |
| `DOMAINS_ALLOWED_TLDS` | worker, api | Comma-separated extensions that may be offered (for example `com,net`). Empty offers none. |
| `DOMAINS_DAILY_PURCHASE_CAP`, `DOMAINS_DAILY_SPEND_CAP_USD` | worker | Caps over the last 24 hours, counted from `register_sent` events. 0 or unset means no purchase at all. **Values to set at Part C: 3 purchases and 40 dollars.** |
| `DOMAINS_CHECKS_PER_HOUR_SITE`, `DOMAINS_CHECKS_PER_HOUR_ACCOUNT` | api | Owner Check rate limits (default 20 and 200). |
| `DOMAINS_REGISTER_AUTO_RENEW` | worker | `1` adds `auto_renew: true` to the register body. Set only if step 0 shows the API accepts it. |

### The sequence (worker only)

1. The owner types a name: `check` writes a claim in `checking` and an unnamed registration row, and
   queues a read-only Check task (the one-minute sweep is its backstop). The claim becomes `quoted`
   for 15 minutes. The owner never sees a price.
2. `buy` writes the name on the registration row (its own committed transaction; a second site for
   the same name gets "name taken") and moves the claim to `confirmed`. It never enqueues and never
   calls a provider.
3. The sweep takes a `confirmed` claim: payment gate, a fresh Check of the name, the ceiling, then
   step 1 in one committed transaction (ledger lock, then the row lock): every condition re-read, the
   caps counted from events, one conditional UPDATE to `sent` with `send_count = 1`, and the
   `register_sent` event. Only the process that updated one row sends the single `register` POST
   (body exactly `{"domain_name": <name>}`) outside any transaction.
4. A row that has sent is never sent again: the worker only polls `registration-status` and reads the
   registration. `action_required`, `blocked`, `failed`, a refusal to the POST (including
   `domain_unavailable`) and an unknown outcome after 30 minutes all go to `conflict`, with an alert.
   Adoption is only of this row's own sent POST whose outcome is unknown.
5. On success the claim is `registered` and the B1 setup steps take it to `live`.

A crash before step 1 sends nothing and resumes. A crash between step 1 and the POST is a stop: the
restarted job polls, and after 30 minutes the row goes to `conflict` (it never risks a second POST).

### Staff actions (HQ, all audited; closed while `DOMAINS_ENABLED` is off)

`domains/list/`, `domains/retry/` (setup steps only), `domains/disconnect/`, `domains/cancel-request/`
and `domains/confirmed-registered/`. There is no "allow one more send", no "I registered it" and no
manual purchase anywhere.

* **Cancel request**: only a registration in `intent` with nothing sent and the claim not in
  `conflict`. It releases the name, ends the claim and writes `request_cancelled`.
* **Confirmed registered**: the only way out of a registration's `conflict`. It runs in two halves,
  because the `api` container holds no Registrar token. The staff route validates (the row is in
  `conflict`), writes the audit entry and a `confirm_requested` event, queues the worker task
  `domains.confirm_registered` and answers 202 `requested` (a second request while one is pending
  answers `pending` and writes nothing new). The worker takes the purchase lock, re-checks the row is
  still in `conflict`, then reads the provider and refuses unless the name is in our account, the
  status is not `in_progress`, `failed`, `action_required` or `blocked`, and the provider is
  configured and not paused. On a pass the row becomes `succeeded` and the claim continues from
  `registered`; otherwise a `conflict_confirm_refused` event names the reason (`paused`,
  `provider_unavailable`, `not_in_our_account`, `read_failed_*`, `status_*`). It never calls
  `register` and never touches `register_sent_at`, `send_count`, the name or the caps. The HQ list
  shows the last request and its result (`pending`, `done`, `refused`); the minute sweep re-runs a
  request whose task was lost. Anything else in `conflict` waits for Coach Ed, through the Manager.

### The price ceiling and the sentinel

The ceiling is enforced at purchase on both prices and only monitored at renewal. The daily sweep
Checks one random unregistered name per allowed extension and stores each reading in
`DomainTldPrice`. An extension that is not registrable, not standard, not USD or over the ceiling is
withdrawn from the offered list and alerted; a reading older than 36 hours withdraws it too.

### Alerts added by B2

`registration_conflict`, `cap_hit`, `register_not_sent`, `second_send_refused`, `tld_withdrawn`,
`sentinel_failed`, `registration_read_failed`. Names and codes only.

### The first-payment hook

`confirm_transfer` registers one `on_commit` callback (robust): `apps.domains.services.on_first_payment`.
It writes the `eligible` event once, notifies the Subscriber once, and buys nothing. A bug in it is
logged and never fails a Finance confirmation. The minute sweep marks any site the hook missed.

## Store email (B3)

ClickUp z8uq9mhgwb. A claim's email track gives the store its own sending domain in Resend, so its
members' and buyers' emails can come from `<local part>@<the registered domain>`. It runs beside the
website track and never changes it: the website and the purchase do not wait for email, and an
`email_failed` never fails the website. Nothing here has been done on any environment.

### Variables (names only; the switch is off by default)

| Name | Where | Meaning |
|---|---|---|
| `DOMAINS_EMAIL_ENABLED` | worker only | `1` lets the worker run the email track and the daily Resend read. Off: no Resend call is made, no email step runs, and a removal that must delete a Resend domain waits (alert `resend_removal_waiting`) rather than leave it behind. |
| `RESEND_DOMAINS_API_KEY` | worker only, secret | A full-access key (a sending-only key cannot create domains). Coach Ed enters it himself. The `api` and `beat` containers hold none. |
| `RESEND_REGION` | worker | One fixed region: `us-east-1`, `eu-west-1`, `sa-east-1` or `ap-northeast-1`. Anything else reads as unset and the adapter refuses to create. |
| `DOMAINS_MAX_EMAIL_DOMAINS` | worker | How many Subscriber domains the Resend plan can hold beyond the platform's own (the Free plan holds 3 and the platform uses at least 1). Unset or 0 means every email track waits. The website and the purchase never read it. |

`manage.py check --deploy` shows `domains.W005` (names only) while `DOMAINS_EMAIL_ENABLED` is on and
one of the three is unset. The sender lookup runs on the `api` too (it sends member emails) and needs
only `DOMAINS_ENABLED` and `STORE_EMAILS_BRANDED`, not the worker's email switch.

### The track (worker, `apps/domains/email_track.py`)

1. `email_none` or `email_waiting_capacity`: takes a slot if our occupied Resend domains (claims with a
   Resend domain recorded or being made, not `ended`) are under `DOMAINS_MAX_EMAIL_DOMAINS`. Otherwise
   it waits, with `email_waiting_since` set once, one event and one operator alert
   (`email_waiting_capacity`), and resumes when a slot exists (longest waiting first). Capacity never
   blocks or refuses the purchase or the website.
2. `email_creating`: Resend `create` with `name` equal to the site's own succeeded registration name,
   the fixed region and sending only (receiving disabled). The id is recorded. An answer of "exists"
   is never adopted: it is `email_failed` (`domain_exists`) and the daily read lists any unrecorded
   Resend domain.
3. `email_dns`: the records are read again from Resend (`get`) and written into the zone through the
   Cloudflare DNS adapter: TXT, MX or CNAME only, DNS-only, content exactly as Resend returned it,
   names made whole from Resend's relative names, ids recorded in `DomainDnsRecord` (purpose
   `resend`). List before create: an unrecorded record of the same type and name stops the email track
   (`dns_clash`). Nothing is hard-coded.
4. `email_verifying`: Resend `verify` once, then a `get` every 5 minutes (`verify` again while the
   status is `not_started`) for up to 72 hours (Resend's own window) measured from the verify call;
   then `email_failed`. A `failed` status is `email_failed` at once.
5. `email_verified` (Resend says `verified`), then `email_active` once the daily read has confirmed it.
   Every phase before verified also fails after 72 hours.

`email_failed` stays until an operator retries it: the HQ "retry" of a claim whose email track failed
(the website not failed) goes back to `email_dns` when a Resend domain is recorded (never a second
create), else to the start. Every step runs under the claim's single-flight lock, only for a claim in
`registered` to `attention`, only while the payment gate holds, and takes every name from the site's own
succeeded registration (a forged claim name sends nothing).

### The sender

`apps/notifications/store_email.domain_sender(site)` builds `<sender_local_part>@<registration name>`
at send time (no full address is stored) and returns it only while all of this holds: `DOMAINS_ENABLED`
and `STORE_EMAILS_BRANDED` are on; the site's own claim is in `connecting`, `activating`, `live` or
`attention` (not `removal_pending`); its email track is `email_verified` or `email_active`; Resend's
status is `verified` and was read within 72 hours; the claim's name equals the succeeded registration's
name. `temporary_failure`, `failed` or any doubt falls back to the platform address. An explicit
`STORE_SENDERS` pair still wins. The daily read (`domains.daily_sweep`, while the switch is on) is what
refreshes the status, so a worker that is down for three days makes stores fall back.

### Removal and the daily read

Removal (`removal_pending`) deletes the recorded Resend domain by id after a `get` that must return the
registration's name, and the recorded DNS records of both purposes; a Resend domain that is already gone
counts as removed. A refusal (for example a name mismatch) keeps the claim in `removal_pending` with an
alert and does not hold back the DNS deletions. The registration is never deleted. The daily sweep also
compares Resend's domain list with the recorded ids (alerts `resend_unrecorded` and `resend_missing`,
names of the platform's own domains ignored) and re-reads each verified domain (alert
`resend_not_verified`). `DomainEvent.detail` of the email track holds only whitelisted keys
(`events.DETAIL_KEYS`): never a record value, an id, a key or a provider body.

### Alerts added by B3

`email_waiting_capacity`, `email_failed` (with a reason), `resend_removal_waiting`, `resend_unrecorded`,
`resend_missing`, `resend_not_verified`, `resend_name_mismatch`, `resend_read_failed`,
`resend_list_failed`. Not built: the optional per-domain DMARC record (an operator step after verified).
