# Branch notes: z8uq9mhpbb FREE-DOMAIN FD-BE

Branch `feat/z8uq9mcyu0-fd-be` (from origin/main b4314bb). Task z8uq9mhpbb, under z8uq9mcyu0. Plan:
`docs/session-state/packets/morning-2026-11-21/free-domain-y1-plan.md` (FD1, FD3, FD4). No TWCinc code.
No migration, no new package, no variable set, no provider call, no staging change.

## API contract (for the FE builder)

### 1. Owner `GET /v1/sites/{site_id}/domain/` gains `free_until`

```
{"eligible", "locked_reason", "extensions", "registered_name", "free_until", "domain"}
```
`free_until`: `"YYYY-MM-DD"` (UTC date) when the site's registration is `succeeded` (registrar
`expires_at` when recorded, else registration date + 12 months), otherwise `null`. No price, no year-2
amount. `extensions` may now hold several (FD-CAP, Coach Ed 24 Nov: limit by price, cap US$13): each extension whose
daily price read allows it (standard tier, USD, renewal price present, first-year price at or under the ceiling;
the renewal price is stored but not capped), `.com` first when offered, then the rest alphabetically. Any other
name is refused with exactly: "This name is not included. Please choose another." The default sentinel list is
`com,net,org,store,shop,online,site,xyz` (`DOMAINS_ALLOWED_TLDS` overrides); it only says which extensions the
daily read covers, price decides.

The `domain` object gains `renewal_usd_cents` (int or null): the year-2 price in US cents, set when the
registration's checked name equals the claim's name and the currency is USD (quoted, confirmed, awaiting and
after), null otherwise, including bridge imports and ended claims. No first-year price is ever returned.

### 2. HQ staff `POST /staff/v1/domains/import-bridge/` (bridge import of a hand-bought domain)

Same staff session as the other `domains/*` routes; 404 while `DOMAINS_ENABLED` is off.

Request: `{"site": "<site public id>", "name": "brand.com"}` (apex name, no www).

Responses (all `Cache-Control: no-store`):
* `202 {"site": "<site id>", "name": "brand.com", "status": "requested" | "pending"}`. Nothing is bought.
  The registrar read happens in the worker's minute sweep (within about a minute); poll the list route. `pending` = an identical request is
  already waiting.
* `404 not_found`: domains off, unknown site.
* `400 invalid_request` with `fields.name = ["<plain reason>"]`: not a valid bare name.
* `409 import_refused` with `fields.reason = ["<code>"]`, codes: `name_refused` (platform name, subdomain,
  suffix, already connected, no suffix list), `site_off` (deleted or not Santé), `no_subscription`,
  `exempt` (billing-exempt), `has_registration` (site already has a domain for life, or an import is
  pending for another name), `has_claim` (a claim is open), `name_held` (another site holds the name).

`POST /staff/v1/domains/list/` gains `"imports": [{"site", "site_name", "name", "state", "reason", "at"}]`,
the latest import per site: `state` is `pending`, `done` or `refused`. A `refused` row's `reason` (worker
codes): `not_in_our_account`, `no_expiry`, `status_in_progress|failed|action_required|blocked`,
`read_failed_<code>`, `status_failed_<code>`, `paused`, `provider_unavailable`, `exempt`, `site_off`,
`no_subscription`, `has_claim`, `not_pending`, `not_allowed`. On `done` the new claim (state `registered`)
appears in `claims` and the normal states follow.

## What it does

* FD1: `DOMAINS_ALLOWED_TLDS` default is `com` when the variable is unset or empty; a value overrides.
* FD4: `owner.free_until(reg)`.
* FD3: `apps/domains/bridge.py`, same two halves as "confirmed registered". Request half writes the name
  onto the site's registration row (`intent`, unsent, no claim: nothing can buy it) and an
  `import_requested` event; worker half (`tasks.import_bridge`, run by the minute sweep within about a minute; no new celery task, because a billing test pins the task list and the task forbids apps/billing edits) re-checks every
  rule under the purchase lock, reads the registrar (`get` must show the name in our account, with
  `expires_at`; status not in progress/failed/action required/blocked), records the registration
  `succeeded` with `expires_at`/`auto_renew`, and starts a claim at `registered`. DNS, Vercel and Resend
  run only through the existing machine and its switches. `register` is never called. Any refusal gives the
  name back. Audited: `domains_import_requested`, `domains_bridge_imported`, `domains_import_refused`
  (new KEPT actions in `apps/operators/audit.py`), plus `DomainEvent` rows with the operator.

## Choices and open facts

1. **Cap (FD-CAP).** `DOMAINS_MAX_COST_USD` stays env-driven (unset = 0 = buy nothing); the Manager sets 13 on
   staging at release (Supervisor scope update). The ceiling now applies to the first-year price only; the
   renewal check was dropped (Coach Ed: bill the exact domain price at year 2). The W004 deploy check is
   unchanged and still right.
2. **`succeeded` row of an import** carries `register_sent_at` = import time and `send_count` = 1: the table
   check constraint `domains_registration_sent_consistent` requires it, and it avoids a migration. No
   `register_sent` event is written, so the daily caps do not count an import.
3. Imports do not require the paid cycle at request time (the existing machine waits at `registered` until
   the payment gate holds); they refuse billing-exempt, deleted and non-Santé sites.
4. `free_until` is the UTC date of `expires_at`.
5. Files outside apps/domains: `config/settings.py` (default), `apps/operators/audit.py` (3 action names),
   `docs/runbooks/custom-domains.md`. None in apps/billing.

## Tests

New: `apps/domains/tests/test_default_tlds.py` (default, override, cap unchanged; subprocess reads settings),
`BridgeImportTests` in `test_staff_routes.py` (route table incl. `import-bridge/`, 404 while off, request
and audit, idempotent repeat, bad and platform names, exempt/deleted, claim/registration, name held, worker
import with no `register`, normal machine step after import, every worker refusal gives the name back, lost
task backstop), `FreeUntilTests` in `test_owner_api.py`. Fake provider only; no network.
