# Branch notes: z8uq9mhgqt DOMAINS B2 BE (domain purchase)

Branch `feat/z8uq9mhgqt-domains-b2-be`, cut from the B1 head 8706e99 (`feat/z8uq9mhgn7-domains-b1-be`),
so it carries B1 and must be released after it. No TWCinc code was ported; the design is NegosyoOnline's
own (ClickUp z8uq9mcyu0). No new migration (`domains.0001_initial` from B1 holds every table), no new
package, no provider call, no token, no variable, no staging change, no purchase.

## Design sources used

`bunny-addendum-r12-2026-11-21.md` (supersedes every older hosting line), `plan.md` r4.1 (row "B2
purchase"), r4.3 test 2 and r4.5, `design.md` R4.4, R4.7 and R4.14.1 to R4.14.9, `approvals.md` (what is
not allowed), and ruling SD1 (Coach Ed, ClickUp 604765): the gate is the first confirmed subscription
payment of any plan, never a trial; an early first payment counts. **The year-2 renewal charge is not
built** (it needs Coach Ed's go).

## What B2 contains (apps/domains)

* `providers/cloudflare_registrar.py`: the Registrar adapter, four allowlisted calls (check, register,
  registration-status, get registration). Register body exactly `{"domain_name": <name>}` (plus
  `auto_renew: true` only while `DOMAINS_REGISTER_AUTO_RENEW` is on). It refuses a name that is not the
  name of a row step 1 already marked `sent`, refuses inside a web request (`context.py`, the
  request_started/finished thread flag) and without the worker-only token. Status and get only for names
  of our own registration rows. `providers/fake.py` gains `FakeRegistrar`, a transport under the real
  adapter (the B1 pattern); `Providers` gains `registrar` and the existing `registrar_read` is the same
  adapter, so the daily registration read now works.
* `gate.py`: the payment gate (position state ACTIVE at the instant, a PAID cycle, Santé, not deleted, not
  on hold, not exempt; unknown fails closed). Read at Check, Buy, Retry, in step 1 under the lock, and in
  the worker before every setup step (`machine._site_ok`), so a re-attach on a suspended, cancelled or held
  site waits.
* `purchase.py`: the sequence of R4.14.3: Check, step 1 (ledger lock then row lock, every condition
  re-read, caps counted from `register_sent` events, one conditional UPDATE, the event, `send_count` 1),
  step 2 (the single POST, outside any transaction), step 3 (result), polling of a sent row, adoption only
  of this row's own sent POST, `conflict` for `action_required`, `blocked`, `failed`, a refusal to the POST
  and 30 minutes unknown. Also `release` (refused worker Check, cancel), `rename_intent`,
  `cancel_request`, `confirmed_registered` and the daily `sentinel` Check.
* `owner.py` and `api_owner.py`: `GET/POST /v1/sites/{site_id}/domain/` (status), `check/`, `buy/`,
  `retry/`. Buy only writes rows and never enqueues. A Buy of the name of a `succeeded` registration is a
  re-attach (claim at `registered`; no `register`, no caps, works with the purchase switch off).
* `staff.py`: `domains/list/`, `retry/`, `disconnect/` (the two B1 staff actions), `cancel-request/`,
  `confirmed-registered/`. All audited (an operator audit entry and a `DomainEvent` with the operator).
* `services.py`: `after_confirm` (the one hook line's target, a robust `on_commit`), `on_first_payment`
  (idempotent: the `eligible` event once, one notice), `safety_net` (run by the minute sweep),
  `offered_tlds`. `events.py`: the append-only event writer. `tasks.py`: the minute sweep also runs the
  purchase states and the safety net; new task `domains.check_name` (owner Check, read only, touches a
  `checking` claim and nothing else); the daily sweep runs the sentinel. `checks.py`: `domains.W004`.
* Switch `DOMAINS_PURCHASE_ENABLED` (default off). `docs/runbooks/custom-domains.md` extended.

## Files outside apps/domains (every one)

* `apps/billing/services.py`: `has_paid_cycle(subscription)` (public wrapper of `_ever_paid`) and one hook
  call in `confirm_transfer`, after `audit.record`, inside a `try/except` that logs (so even registering
  the callback can never fail a confirmation; the callback itself is a robust `on_commit`).
* `apps/operators/audit.py`: five declared actions (`domains_retry`, `domains_disconnect`,
  `domains_request_cancelled`, `domains_confirmed_registered`, `domains_confirm_refused`).
* `apps/operators/staff_views.py`: import and one `ROUTES.update` for the domains staff routes.
* `config/api.py`: import and one `add_router` for the owner routes.
* `config/settings.py` and `.env.example`: names only (`DOMAINS_PURCHASE_ENABLED`,
  `CLOUDFLARE_REGISTRAR_API_TOKEN`, `DOMAINS_REGISTER_AUTO_RENEW`, `DOMAINS_MAX_COST_USD`,
  `DOMAINS_DAILY_PURCHASE_CAP`, `DOMAINS_DAILY_SPEND_CAP_USD`, `DOMAINS_ALLOWED_TLDS`,
  `DOMAINS_CHECKS_PER_HOUR_SITE`, `DOMAINS_CHECKS_PER_HOUR_ACCOUNT`).
* `docs/runbooks/custom-domains.md` and this file.
* Tests outside apps/domains that pin lists or counts: `apps/common/tests/test_tenant_isolation/owner_base.py`
  (four `OWNER_ROUTES` entries, the `domains` prefix, `DOMAINS_ENABLED` in the two-owner setup, a body)
  and `test_owner_routes.py` (counts 146 to 150, `domains: 4`), `apps/billing/tests/test_staff_finance.py`
  (the staff route pin), `apps/billing/tests/test_tasks.py` (the task list gains `domains.check_name`),
  `apps/operators/tests/test_audit.py` (the audit scan now also walks apps/domains).

## Deviations and choices (smaller, fail-closed option)

1. **Money values are unset = 0 = nothing can be bought.** The plan names defaults (12.00, 3, 40); here an
   unset ceiling, cap, spend cap or extension list buys nothing and `check --deploy` warns (`domains.W004`,
   only while the purchase switch is on). The Manager sets them explicitly, as approvals B2 already says.
2. **Registrar paths and answer shapes are as the design describes them, not verified**: `POST
   /accounts/{id}/registrar/domain-check` (`{"domains": [...]}`, answer `result.domains[]` with `name`,
   `registrable`, `tier`, `pricing.{currency,registration_cost,renewal_cost}`), `POST .../registrations`,
   `GET .../registrations/{name}/registration-status`, `GET .../registrations/{name}`. Step 0 part A must
   confirm them; the adapter reduces every answer to whitelisted fields and reads prices as dollars rounded
   up to cents.
3. **"Confirmed registered" needs a process that holds the Registrar token.** The staff routes run in the
   `api` container, which must hold no token, so there the action is refused (`provider_unavailable`) and
   audited. See the open question.
4. Buy while the registration is `sent` or `conflict` is refused outright ("your domain is being set up")
   and starts no claim, instead of a claim that waits (same effect: nothing registers, nothing past
   `registered`).
5. Buy for a different name while the request is `confirmed` or `awaiting_gate` and nothing was sent renames
   it in place (claim first, then the registration, `check_*` cleared, under the lock, the explicit
   `allow_name_release` argument); the worker re-Checks before any send, so a rename needs no new owner Check.
6. A Check of the name of the site's own `succeeded` registration writes a `quoted` claim with no provider
   call; Buy then re-attaches.
7. The gate reads the billing position at the instant (ACTIVE), not only the stored state, so a due but
   unwritten transition is seen.
8. The worker refuses the purchase inside a web request before step 1 as well (not only inside the adapter),
   so an eager task cannot spend the one send and then be refused.
9. A reset of a sent row (queryset update or `save()`) cannot send again: the unique `register_sent` event
   per registration makes step 1 fail; the worker treats that as `second_send_refused` and sends nothing.
10. The sentinel stores a reading per allowed extension; a stale reading (36 hours) or a non-USD reading
    withdraws the extension. The owner Check and Buy use the offered list; a worker Check always re-reads the
    live price. The registration read of the daily sweep now tolerates a paused or unconfigured adapter.
11. `machine._site_ok` is now the full gate. The B1 tests were adapted (their sites are paid, BILLING_ENABLED
    on) and `test_tasks` expects the registrar and the `prices` key.
12. Helper named `events.log` (not `record`) so the audit-literal scan in `test_audit` does not read event kinds
    as audit actions.
13. The notice text ("Choose your domain") is a placeholder in the style of the other Subscriber emails; it says
    nothing about renewal or price. It needs Coach Ed's wording approval before staging.

## Not done (out of B2)

The year-2 renewal charge; Resend email (B3); every frontend; the Public Suffix List file; any provider
call, token, variable or staging change.

## Suites

Run locally on code commit cdf9113 (before this notes commit), serially (`--parallel` needs `tblib`, which
is not a project dependency):

* `apps.domains`: 260 tests, OK on SQLite (3 PostgreSQL-only lock tests skipped there); the same 260 on
  PostgreSQL, 0 skipped.
* Full suite SQLite: 5636 tests, OK (74 skipped, the PostgreSQL-only ones).
* Full suite PostgreSQL 14 (own local instance, UTF8, fresh port 55432) + Redis 7 (own instance, fresh
  port 56379), non-debug settings: 5636 tests, OK (1 skipped). Both instances were stopped and their
  data removed afterwards. CI uses postgres 16; not run locally.
* `manage.py check --deploy` (with `REDIS_URL` and a long secret key): only `domains.W001` (public suffix
  list missing, intended) plus the known HSTS warnings (W005, W021). `makemigrations --check`: no changes.
* The first full SQLite run found one pinned staff-route count in `apps/inbox/tests/test_staff.py`; it was
  updated (one more test file outside apps/domains) and the full runs above were repeated on the final code.

## Open questions for the Supervisor

1. "Confirmed registered" cannot run in the `api` container (no Registrar token there by design). Which
   process runs it: give `api` a read-only Registrar token and `DOMAINS_PROVIDER=real`, or a worker-side
   management command? Until then the action is refused and audited.
2. The Registrar paths and answer shapes (deviation 2) must be confirmed by step 0 part A before any real run.
3. The Subscriber notice text and the screen wording need Coach Ed's approval (deviation 13); SD1's
   "first year included, then billed" is not stated anywhere in what Subscribers see.
4. Money settings default to 0 (deviation 1): confirm that is wanted instead of the plan's 12.00, 3 and 40.
