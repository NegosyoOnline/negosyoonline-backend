# Custom domains (apps/domains, branch B1 engine)

ClickUp z8uq9mhgn7 (build), design z8uq9mcyu0. This is the engine: models, names, the state
machine, the Vercel and Cloudflare DNS adapters, the HTTPS probe, serving and lifecycle removal.
The purchase (Registrar adapter, Buy, caps) is branch B2 and Resend email is B3; neither is here.
Nothing in this file has been done on any environment. Staging runs on Bunny containers `api`,
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

* `domains.sweep` (every minute) advances claims in `registered`, `zone`, `connecting`,
  `activating` and `removal_pending`, one step each, one claim at a time.
* `domains.hourly_check` reads Vercel and probes each `live` or `attention` claim. Six hours of
  failure is `attention`; 14 days of it is `failed`; a recovery returns to `live`.
* `domains.daily_sweep` (05:00 Manila) queues lifecycle removals, runs the zone audit, compares the
  Vercel project's domain list with the recorded hostnames and reads each registration (the read
  is skipped until the Registrar adapter exists).
* Timers: 72 hours in `zone` and `connecting`, 24 in `activating`.

## States this branch moves

`registered` to `zone` to `connecting` to `activating` to `live`; `attention`, `failed`, `conflict`
and `removal_pending` to `ended`. A claim reaches `registered` only through the purchase or a
re-attach, which arrive with B2: until then claims exist only in tests. `failed` is retried in
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
