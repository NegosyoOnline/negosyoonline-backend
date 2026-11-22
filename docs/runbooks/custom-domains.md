# Custom domains (apps/domains, branches B1 engine and B2 purchase)

ClickUp z8uq9mhgn7 (build), design z8uq9mcyu0. This is the engine: models, names, the state
machine, the Vercel and Cloudflare DNS adapters, the HTTPS probe, serving and lifecycle removal.
The purchase (Registrar adapter, Buy, caps, staff actions) is branch B2, described in the section
"Purchase (B2)" below; Resend email is B3 and is not here. Nothing in this file has been done on any
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

The list is a data file that this code never downloads (its download needs Coach Ed's permission).
While the file is missing every name is refused and `manage.py check --deploy` shows `domains.W001`.
Install it only when he has approved the download, then restart the worker and api.

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
| `DOMAINS_MAX_COST_USD` | worker | The price ceiling in dollars (plan: 12.00). Both the registration price and the renewal price must be at or under it, at purchase. Unset or 0 refuses everything. |
| `DOMAINS_ALLOWED_TLDS` | worker, api | Comma-separated extensions that may be offered (for example `com,net`). Empty offers none. |
| `DOMAINS_DAILY_PURCHASE_CAP`, `DOMAINS_DAILY_SPEND_CAP_USD` | worker | Caps over the last 24 hours, counted from `register_sent` events. 0 or unset means no purchase at all. |
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
* **Confirmed registered**: the only way out of a registration's `conflict`. It reads the
  registration from the provider first and is refused unless the name is in our account, the status
  is not `in_progress`, `failed`, `action_required` or `blocked`, and the provider is configured and
  not paused. It spends nothing and changes no send state or cap count. It needs a process that holds
  the Registrar token (see the open question in the branch notes); the `api` container does not.
  Anything else in `conflict` waits for Coach Ed, through the Manager.

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
