# Branch notes: z8uq9mhgn7 DOMAINS B1 BE (custom-domain engine)

Branch `feat/z8uq9mhgn7-domains-b1-be`, cut from origin/main cd34f47. No TWCinc code was ported;
the design is NegosyoOnline's own (ClickUp z8uq9mcyu0).

## Design sources used

Folder `design-z8uq9mcyu0-custom-domains` (Supervisor side-session notes): `bunny-addendum-r12-2026-11-21.md`
(supersedes every Railway line), `plan.md` r4.1 to r4.5 (the "Superseded r3 plan" was not built),
`design.md` R4.0 to R4.14, `approvals.md` and `decisions.md` (what is not allowed). `audit.md` was
not read in full.

## What B1 contains

`apps/domains`: models and `0001_initial` exactly per plan r4.5 (all tables B2 and B3 use);
`names.py` (normalisation, Public Suffix List loader, refusal list); `providers/` (Vercel adapter with
6 allowlisted calls and the project id pinned, Cloudflare DNS adapter with the platform-zone guard,
in-memory fakes that are transports under the real adapters); `probe.py` (SSRF-guarded HTTPS probe);
`machine.py` (state machine `registered` to `ended`, hourly check, retry, removal); `tasks.py`
(sweep, hourly check, daily sweep) with `sweeps.py` (zone audit, Vercel comparison, registration
read); `hosts.py` (`site_for_hostname`, `site_for_probe`, `canonical_host`); `storefront_api.py`
(domain-probe); `lifecycle.py`; `checks.py`; `docs/runbooks/custom-domains.md`.
Switches: `DOMAINS_ENABLED`, `DOMAINS_PROVIDER` (`off`/`fake`/`real`), `DOMAINS_PROVIDER_PAUSED`.

## Files outside apps/domains

* `config/settings.py`: INSTALLED_APPS entry, the DOMAINS_* / token-name settings, three beat entries.
* `.env.example`: names only.
* `apps/sites/api_public.py`: `site_for_host` calls the second branch for a host outside the parent host.
* `apps/sites/custom_hosts.py`: `active_host` falls back to `apps.domains.hosts.canonical_host`, so
  `public_host`, `public_base_url`, `custom_host_if_asked` and the three emailed links
  (`apps/pack/notices.py`, `policies.py`, `xendit.py`, all already calling `custom_hosts`) follow a
  live domain with no edit to those files.
* `apps/storefront/api.py`: one `add_router` line for the domain-probe route.
* `docs/runbooks/custom-domains.md`, `docs/branch-notes/z8uq9mhgn7-domains-b1-be.md`.
No other app's migration or file was touched.

## Deviations from plan r4 and choices made (smaller, fail-closed option)

1. **Public Suffix List not shipped.** Per approvals D1 it is not downloaded. Missing file: every name
   refused, `domains.W001` warning (shown even with the switch off, so `check --deploy` shows it).
   Tests use a hand-written fixture.
2. **Reuse of `custom_hosts` (addendum (c)):** no second resolution path. `served()` keeps the manual
   `SiteCustomHost` first, then the subdomain, then the domains branch; a name already connected on the
   manual path is refused as a claim name.
3. **`canonical_host` and the three emailed links** are done through the existing
   `custom_hosts.public_host` (it already feeds all three), so only `active_host` changed.
4. **Staff routes are not built** (the task's scope list omits them; plan r4.1 lists them under B1).
   HQ retry/disconnect, `OWNER_ROUTES` entries and the owner screens go to B2 with the purchase routes.
   `machine.retry` and `machine.request_removal` exist for them to call.
5. **Registration read:** the Registrar adapter is B2, so `registration_read` reads through an optional
   `registrar_read` object. The fake provider has one; the real mode has none, and the task then reports
   "skipped". Nothing in B1 calls a Registrar endpoint.
6. **Payment gate in the worker:** `has_paid_cycle` is a B2 billing edit. B1's setup steps only require
   the site not deleted and not on operator hold (the full gate for re-attach arrives with B2).
7. **Served states:** only `live` and `attention` serve (the design text also lists `conflict`, which in
   r4 is a purchase state and never serves). Only the `www.` hostname is served by Django; the apex is
   answered by Vercel's 308.
8. **Certificate state:** Vercel's project-domain API does not expose the certificate, so "cert ok" is
   `verified` and not `misconfigured` from the config read; the HTTPS probe is the final proof.
9. **Lifecycle** uses `deletion_due_at`, `service_ended_at`, the cancelled states, a missing
   subscription and a deleted site; it does nothing while `BILLING_ENABLED` is off (unknown never removes).
   Resend removal is B3.
10. **Extra constraints** beyond the r4.5 text (harmless, noted for review): `domains_ledger_single_row`
    (id is 1), `domains_tldprice_usd` (currency USD only), index `domains_event_kind_at`.
11. Provider answers are never stored raw; events store whitelisted `detail` only.
12. A forged claim name makes the step refuse with an alert and an event, and moves nothing.
13. `DOMAINS_PROVIDER=fake` without `DJANGO_DEBUG=true` is a system-check error and `providers.current()`
    returns None.

## Not done (out of B1)

Registrar adapter, purchase guards, owner routes, `on_first_payment` hook, caps, Resend, any
frontend, the `domain-probe` storefronts route, the PSL file.

## Suites (local, run on the code commit before this notes commit)

* `apps.domains`: 150 tests, all pass (SQLite; also inside the full PG run).
* Full suite SQLite: 5526 tests, OK (71 skipped, the PostgreSQL-only ones).
* Full suite PostgreSQL 14 (own local instance, UTF8, fresh port) + Redis 7 (own instance, fresh port):
  5526 tests, OK (1 skipped). CI uses postgres 16; not run locally.
* `manage.py check --deploy`: only `domains.W001` (public suffix list missing, intended) plus the known
  HSTS warnings (W005, W021). `makemigrations --check`: no changes.
* Run serially: `--parallel` needs `tblib`, which is not a project dependency (CI adds it with `uv run --with`),
  so it was not used.
* The existing `apps/billing/tests/test_tasks.py` pins the task and schedule lists; it was updated for
  the three domains tasks (a test edit outside apps/domains).
