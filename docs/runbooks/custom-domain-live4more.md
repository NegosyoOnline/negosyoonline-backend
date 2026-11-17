# Custom domain for one store (Live4More, `live4moreonline.store`)

ClickUp z8uq9mg8gw. Authority: Coach Ed's ruling, comment 1100430000556400 on z8uq9med6x (2026-11-17): a focused, admin-managed custom domain for the existing Live4More Santé store. Apex is canonical, `www` redirects to the apex, the platform address keeps working during rollout. No subscriber domain UI, no automation.

**Nothing in this file has been done.** Not approved until Coach Ed's separate word on the concrete change list and the rollback: DNS changes, attaching the domain in Vercel, Railway or Vercel variables, running the migration on staging, assigning the host on staging, deploy. Production is untouched. Never call the domain connected or launch-ready until HTTPS and the customer journey are verified on the real domain.

## 1. How a custom host reaches the right store

1. The browser opens `https://live4moreonline.store/...`. DNS points the name at Vercel; the Vercel project `negosyoonline-storefronts` answers.
2. The storefronts server (not the browser) calls Django on the API host with the request host as a value: `GET /storefront/v1/site/?host=live4moreonline.store`, and `host` in the body of every other `/storefront/v1/` call. The Django host the call goes to is always the API host, so `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and `API_ALLOWED_ORIGINS` do **not** change for a custom domain.
3. `site_for_host` (`apps/sites/api_public.py`) first matches the host exactly against `SiteCustomHost` (`apps/sites/models.py`), then falls back to the unchanged subdomain rule. Either way the site must not be removed, not on operator hold, and `served` by billing. Unknown, retired or unassigned hosts answer the same 404. `www.live4moreonline.store` is deliberately not a known host: the storefronts proxy strips `www.` and redirects (308) to the apex before any lookup.
4. Answers carry `custom_host` (public site call and `/storefront/v1/site/`): the site's custom host, non-empty only when the looked-up host is exactly that host; `""` on the platform address.

Rules for a stored host (`apps/sites/custom_hosts.py`): lowercase, no port, no trailing dot, plain ASCII dotted name with a letters-only top-level domain; non-ASCII and `xn--` names, IP addresses, anything equal to or under `SITE_PARENT_HOST`, and any platform host (`ALLOWED_HOSTS`, hosts of `DASHBOARD_URL`, `SITE_URL`, `API_ALLOWED_ORIGINS`, `HQ_HOST`) are refused. The host is unique across all sites for ever (a retired host stays taken); a site has at most one active host (database constraints).

## 2. Findings from the host-path inspection (backend)

| Area | Finding |
|---|---|
| Lookup | One function decides: `site_for_host`/`served`. Every storefront route (checkout, member, Meta, portal, chat) goes through `served_sante_site` or `site_for_host`, so the custom host inherits every gate with no further change. |
| Tenant isolation | No inference from fragments: the custom host is an exact match; a subdomain still needs exactly one label under `SITE_PARENT_HOST`. A custom host can never sit under the parent host, so the two paths cannot collide. |
| `ALLOWED_HOSTS`, proxy headers | Not involved: Django only ever sees the API host. `SECURE_PROXY_SSL_HEADER` is unchanged. |
| CSRF / CORS | Storefront pages call Django server-side with a bearer credential; storefront origins stay out of `API_ALLOWED_ORIGINS`. No change. |
| Cookies | Member cookies are host-only and set by the storefronts server, so a session on the platform address does not carry over to the custom domain (and back). Do not promise a transfer. Buyers who signed in on the platform address sign in again. |
| Links in emails | Built by `custom_hosts.public_host/public_base_url(site)`: the active custom host, else `{subdomain}.{SITE_PARENT_HOST}`. Used by the secure order-access link and "visit your dashboard" links (`apps/pack/notices.py`) and the website host in policies (`apps/pack/policies.py`). Emails sent before the switch keep the platform links, which keep working. SMS: no store link is built in the backend. |
| Payments | Launch payment is a bank transfer with a proof upload (`docs/runbooks/pack.md`); no gateway return, cancel or webhook URL is generated. If a gateway is added later its return URLs must use `public_base_url`. |
| Meta Pixel / CAPI | `event_source_url` is the page URL the browser reports, kept only when its hostname equals the request host (`meta_ads/services.clean_url`), so it works on the custom host unchanged. The Pixel id is per site, not per domain. In Meta, add the domain under the business's domain list and verify it if Events Manager asks; if the browser Pixel/CAPI pair shows duplicates, check `event_id` dedup after the first real test. |
| Not changed | `site_out().host` (the owner dashboard's displayed platform host) stays the platform address. Subscriber dashboards live on the platform dash host. |

## 3. Hosting and DNS configuration sheet

All values are **read from the provider at the time**; never typed from memory or guessed.

- **Vercel project:** `negosyoonline-storefronts` (the project that serves `*.storefronts-staging.negosyonline.com`). Attach **both** `live4moreonline.store` and `www.live4moreonline.store` to this project: the storefronts proxy redirects `www` to the apex with a 308, which only works if Vercel serves `www` from the same project. Which environment (staging first, production later) the domain is attached to is decided in the change list that Coach Ed approves.
- **Ownership verification:** Vercel shows the required verification record (if any) when the domain is added. Copy exactly what Vercel shows: record type, name, value: `<read from Vercel>`.
- **Records to create** (at the DNS provider of the domain, Cloudflare per the design notes): apex record `<read from Vercel: type / name / value>`; `www` record `<read from Vercel>`. In Cloudflare, set the records to DNS only (not proxied) unless the approved change list says otherwise, so Vercel can issue the certificate.
- **Preserve mail and other records:** before changing anything, export the zone and list every existing `MX`, `TXT` (SPF `v=spf1`, DKIM selectors, DMARC `_dmarc`), `CAA` and other record; changes touch only the apex and `www` address records (and the Vercel verification record). If the apex currently has records of the same type, Vercel's required replacement is a decision for the change list, not for this sheet.
- **HTTPS:** after DNS resolves, Vercel issues the certificate. Verify on both names: `curl -sI https://live4moreonline.store/` (200 or the intended redirect, valid certificate) and `curl -sI https://www.live4moreonline.store/` (308 to the apex). Do not turn on HSTS `includeSubDomains` or preload for the domain.
- **Application configuration:** none on the backend or Vercel for the domain itself (no `ALLOWED_HOSTS`/CSRF/CORS/variable change). The only backend step is attaching the host to the site (section 4) after the migration is applied. Frontend behaviour (apex canonical, `www` redirect, `/` opens the membership landing) is in the frontend change of this task.

## 4. Attaching the host (staging, only after Coach Ed approves)

1. Deploy the code, then run the additive migration `sites.0004_sitecustomhost` (one new table; follow `docs/runbooks/migrations.md`; staging only, under the approved plan).
2. Read-only check on the target service. It finds the store from its active web address, prints the site id, name, template and owner, and writes nothing:

   ```bash
   uv run python manage.py site_custom_domain --site-subdomain live4more --host live4moreonline.store
   ```

   The operator verifies that the printed site is Live4More's real store (owner and name) before going on. The command never assumes a database id.
3. Apply: the same command with `--apply`. It refuses if the host belongs to another site or the site already has a different active host, and does nothing if it is already attached. It never creates a site, copies no data and changes no owner.
4. Check: `GET /v1/public/sites/by-host/?host=live4moreonline.store` answers 200 with `custom_host` set; the same site's platform host answers 200 with `custom_host` empty; `www.live4moreonline.store` answers 404 from Django (the proxy redirects it before asking).

## 5. Rollback

- **Fast, no data change:** retire the host: `site_custom_domain --site-subdomain live4more --host live4moreonline.store --retire --apply`. The domain then answers 404 from Django and the platform address keeps serving. Re-attach later with the same command without `--retire` (it reactivates the retired row).
- Remove the domain from the Vercel project and restore the DNS records from the zone export taken in section 3, only if the domain is to leave the project. Mail records were never touched.
- Links already sent to buyers on the custom domain stop working until the host is re-attached; platform links in older emails keep working.
- Rolling back the code needs no schema change (the table can stay). Dropping `SiteCustomHost` is a separate reviewed migration, never part of a rollback.

## 6. Production transition

- Same domain, same paths: customer-facing URLs do not change. The production Site is the production Live4More store, found with the same command on production by its active web address and verified by the operator (site id, name, owner); a staging site id is never reused.
- A domain switch is not a data migration: no member, customer, order or proof moves, no owner changes, no data copy. The staging store and its test data are separate from production's.
- Attach the domain to the production storefronts project only when staging is verified and DNS is moved in one planned change; the host can be attached to only one site and one environment at a time, so staging and production do not serve the same host together.
- Production payment and tracking configuration (bank account details, Meta Pixel id and token, Meta domain verification, consent banner text) are set on the production Site and verified there; they are not copied from staging.
- Everything in this task needs Coach Ed's separate approval for production.

## 7. Staging walkthrough (after approval and attach)

Use test buyers and the approved bank-transfer-with-proof procedure in `docs/runbooks/pack.md` and `docs/runbooks/launch-gates.md`: a test proof file, a test amount, no real charge and no real customer record.

1. Landing: `https://live4moreonline.store/` opens the membership landing; `https://www.live4moreonline.store/` redirects once to the apex; no loop.
2. Registration / sign-in on the custom host (cookie is host-only; sign in again if you were signed in on the platform address).
3. Checkout: contact step, then place the order with a test proof.
4. Confirmation: the buyer's email arrives; its links point at `live4moreonline.store`.
5. Owner review of the proof (dashboard on the platform dash host), approve it.
6. Member access: sign in with the buyer's contact on the custom host and open the member pages and the order.
7. Platform address: `https://live4more.storefronts-staging.negosyonline.com/...` still serves the same store.
8. Meta: a Pixel PageView and Purchase test event show the custom host as source with no duplicates.
