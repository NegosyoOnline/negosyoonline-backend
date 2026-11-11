# Billing Runbook

Billing Build B1 (ClickUp z8uq9mdwrv): one subscription and one trial per website, billing cycles, the states and their transitions. Plan: `docs/superpowers/plans/2026-11-11-backend-billing-b1-subscription-lifecycle.md`.

B1 takes no payment. Nothing here charges anyone: there is no payment provider, no payment method and no statement until Billing Build B2.

Every step below that touches staging or production needs Coach Ed's approval for that step, the dry runs included. Nothing here is approved by this document. Production is not part of B1.

## Variable

| Name | Meaning |
|---|---|
| `BILLING_ENABLED` | `1` turns billing on. Unset: site creation writes no subscription, the billing endpoints answer 404, the by-host lookup answers 404 for every host, no owner endpoint is limited to Billing, the "Santé Site Owner" label ignores subscriptions, an account disposal skips its billing step, and every command below says the switch is off and reads and writes nothing. |

It is not a secret. It exists so the code can be deployed before the tables are created.

### Bank transfer with proof (Billing Build B2a)

None of these is set anywhere. Setting any of them in a shared environment needs Coach Ed's approval for that variable. Production is not covered.

| Name | Meaning |
|---|---|
| `BILLING_BANK_TRANSFER_ENABLED` | `1` lets a Subscriber pay by bank transfer with proof, and only while `BILLING_ENABLED` is on, `BILLING_TRANSFER_ACCOUNTS` holds a complete account and a proof store is available. Unset, or with any of those missing: nothing can be submitted, confirmed or rejected, and the three tables of `billing.0002_bank_transfer` are not read or written. |
| `BILLING_LAUNCH_PROMO_ENABLED` | `1` offers the six-month Launch Promo (PHP 4,999.00 once, then PHP 2,499.00 monthly) beside Monthly, to a site that never had a paid cycle, and only where paying by bank transfer works. Unset: only Monthly is offered; a promo proof already sent can still be confirmed. The promo's end date is not named yet and none is in code: to end the promo, unset this. |
| `BILLING_ANNUAL_ENABLED` | `1` offers the Annual Plan (PHP 24,990.00 for twelve months, paid in advance) beside Monthly, only where paying by bank transfer works, and lists it on `GET /v1/plan/`. Unset: `plan=annual` is refused with 400 and the public plan list shows Monthly only; an annual proof already sent can still be confirmed. Not set anywhere; switching it on in any environment needs Coach Ed's separate approval. |
| `BILLING_USAGE_ENABLED` | `1` lets the storage usage ledger (Billing Build B4) decide snapshots, and only while `BILLING_ENABLED` is on. Unset: `usage.record_storage_snapshot` writes nothing and returns `disabled`. Even when set, no snapshot is charged until `plan.STORAGE_METERED` is changed by the media upload task (L13). NegosyoOnline never meters orders. Not set anywhere; switching it on in any environment needs Coach Ed's separate approval. |
| `BILLING_TRANSFER_ACCOUNTS` | The account or accounts Subscribers pay to, as a JSON list of objects with exactly `kind` (`bank` or `ewallet`), `provider_name`, `account_name` and `account_number`. Coach Ed supplies the values; none is in code. A value that is not such a list counts as empty and is logged as an error. |
| `BILLING_PROOF_DIR` | A local folder for proof files, used only while `DJANGO_DEBUG` is on. It is for a developer's machine. A shared environment needs the approved private store, which is not built yet, so the switch cannot work on staging or production today. |

`manage.py check` fails with `billing.E001` (no complete account) or `billing.E002` (no proof store) when the switch is on without them.

While the switch works, a signed-in Subscriber reaches five routes under `/v1/sites/{site_id}/billing/`: `payment/` (how to pay), `payments/` (list, and `POST` to send a proof), `payments/{payment_id}/` and `payments/{payment_id}/proof/`. Otherwise each answers 404. A proof file has no address of its own; it is sent only through the last route, to the Subscriber it belongs to.

Order on staging, each step its own approval: merge and deploy; the coordinator applies `billing.0002_bank_transfer`; the private store is approved and built; the receiving account is set; only then the switch.

The day the switch first works, the next sweep writes one `clocks_started` transition for each ended subscription that never paid and has no dates: its download and deletion dates become that sweep's time plus 30 days (R-4a). It happens once per subscription and turning the switch off does not take the dates back. Run `billing_sweep --dry-run` first to see the count.

## Order on staging

Each step is its own approval. All six steps are done on staging, each with Coach Ed's approval at the time. That covers staging only: in any other environment each step is again its own approval, and **none of this is done on production**, which has no services.

1. Merge and deploy. The code is dark while `BILLING_ENABLED` is unset.
2. Apply `billing.0001_initial` (`docs/runbooks/migrations.md`). It only adds three tables. **Done on staging on 2026-11-11.**
3. Set `BILLING_ENABLED=1` on `api`. Never before step 2: site creation and the billing endpoints would fail on the missing tables. From this moment every new site gets its subscription when it is created. **Done on staging on 2026-11-11**, set by Coach Ed himself.
4. Dry-run the backfill and read the counts ("Backfill", below). **Done on staging on 2026-11-11.**
5. Run the backfill with `--apply`. **Done on staging on 2026-11-11:** 1 site, its trial running (recorded on pull request 64).
6. Check: `GET /v1/sites/{site_id}/billing/` for a test site, signed in as its owner. Then `GET /v1/public/sites/by-host/?host=NAME.PARENT` with no sign-in, where `NAME` is that site's web address and `PARENT` is the value of `SITE_PARENT_HOST`: 200 with seven public fields for a site on trial, and the plain 404 for an ended one and for a name nobody holds. **Done on staging on 2026-11-11**, with Coach Ed signed in as the owner: the owner's billing read answered 200 (`state: trial_active`, `storefront_served: true`, `billing_only: false`, `clocks_started: false`) and 401 with no sign-in; the by-host lookup answered 200 with seven public fields for the site's host and 404 for a name nobody holds. **Not checked:** the 404 for an ended site, because staging has none.

To go back, unset `BILLING_ENABLED`. The tables and their rows stay; nothing reads them.

## What the switch does to existing sites

A site's trial counts from its account's creation date, not from the site's creation or from the day billing is switched on. So when the backfill is applied, every site whose account is older than 14 days is ended at once (`cancelled_no_payment_method`), and nothing brings it back until B2 takes payments, except an exemption (below). A site whose account is younger than 14 days stays on trial until its own day 14.

From the moment the switch is on, an ended or suspended site (one whose `GET billing/` answers `billing_only: true`):

- is not served: `GET /v1/public/sites/by-host/` answers 404 for its host, the same 404 as for a name nobody holds. A served site's answer may be cached for at most 60 seconds, and never past the moment it goes off; a 404 for 30 seconds. The lookup has no rate limit of its own.
- is limited to Billing for its owner: `PATCH sites/{site_id}/` and `GET` and `PATCH sites/{site_id}/business-profile/` answer 403 `billing_only`. Sign-in, the account endpoints, `GET sites/`, `GET sites/{site_id}/`, `GET account/setup/` and the billing endpoints answer as before.
- no longer counts for the "Santé Site Owner" label. A cancellation still inside its paid period is served and not limited, but does not count for the label either. An exempt site counts by its stored state, so one exempted after it ended is served and gets no label.

A site with no subscription (made before billing and not yet backfilled) is not served by the by-host lookup and gets no label, but its owner is not limited to Billing. So between step 3 and step 5 no older site is served.

A never-paid subscription that ends before payment collection is live has no download date, no deletion date and no last date to pay: `GET billing/` answers `clocks_started: false`. B2 starts those clocks on the day collection goes live.

## Backfill

Gives each site made before billing its subscription. It writes exactly what site creation writes. A removed site gets none. A site that already has a subscription is skipped, so it is safe to run again. The output is counts only.

Count first. This writes nothing:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py billing_backfill_subscriptions
```

It prints `sites_without_subscription`, `trial_still_running` and `trial_already_ended`. The last one is the number of sites that are ended when it is applied. An approved test site cannot be exempted beforehand, because an exemption needs the subscription to exist: apply first, then exempt it, and it is served again.

Apply:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py billing_backfill_subscriptions --apply
```

A second run answers "Every site already has a subscription."

## Exempting an approved test site

An approved test site is never billed. The exemption is set once and **cannot be undone**: no command, screen or endpoint clears it. An exempt site is always served, and nothing changes its subscription again: the sweep skips it, and a cancel, a reactivation and every payment function refuse it with `billing_exempt`.

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- 'uv run python manage.py billing_exempt SITE --actor USERNAME --reason "approved test site"'
```

`SITE` is the site's public id (`site_...`). `--actor` is your own staff username. The reason is written to the admin audit log (action `billing_exempt_set`, kept whole), so it must hold no personal value. A second run answers "was already exempt" and writes nothing.

## The sweep

The sweep catches the stored rows up with the time: a trial past its end, a payment needing attention past 7 days, a paid cycle past its end plus 7 days. A site's state never waits for it, because every read computes the state from the current time; a late sweep shuts nobody early and serves nobody late.

On staging the `worker` and `beat` services exist ("Worker and beat", below), so the sweep also runs every 5 minutes; running it by hand stays safe. Where they do not exist, the sweep runs only by hand.

Print what is due. This writes nothing:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py billing_sweep --dry-run
```

Run it:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py billing_sweep
```

The output is a count per transition. A second run answers "Nothing is due."

A proof of payment waiting for an operator holds a trial past its end for the 7 days and no longer. It does not hold a paid cycle past its due date plus 7 days. A payment confirmed after a suspension starts a new paid cycle at the confirmation time.

## The Finance routes (reviewing a proof)

An operator reviews proofs on the Finance page of the HQ staff app. The page calls five routes on the hq host, under `/staff/v1/finance/payments/`: `list/`, `detail/`, `proof/`, `confirm/` and `reject/`. Each is a `POST` with a small JSON body and needs the staff app's Origin and a session that passed both sign-in steps. There is no terminal command for confirming or rejecting a proof (Coach Ed, 2026-11-11).

- `confirm/` needs the amount received, typed by the operator, equal to the amount due. It settles exactly one cycle and writes `billing_payment_confirmed` to the admin audit log. Confirming the same payment again changes nothing.
- `reject/` needs a reason and changes nothing about the subscription. It writes `billing_payment_rejected`. The Subscriber sees the reason and the note and can send another proof.
- `proof/` answers the file itself, which has no address. Each read writes `billing_proof_viewed`.
- A proof the Subscriber replaced cannot be confirmed or rejected: the answer is `not_under_review`, and the newer proof is its own payment.

The routes do not exist while `HQ_STAFF_ORIGIN` is unset, and answer 404 and read no billing table while bank transfer is off. So they are closed wherever no proof store is available, which today is every shared environment. The page itself is a later pull request in the frontend.

## An account disposal

`manage.py dispose_account` cancels the account's subscriptions first. It is refused with `payment_under_review` while a site of the account has a proof of payment waiting for an operator; confirm or reject the proof first. See "A request to delete an account" in `docs/runbooks/retention.md`.

## Worker and beat

Two services run the sweep on a schedule: `worker` and `beat`. Both exist on staging, since 2026-11-11. **A new service, and any change to a service's Settings, needs Coach Ed's approval.** Staging only: production has no services.

Each service's settings are typed into its Settings in the Railway dashboard, project `negosyoonline-backend`, staging environment. No file in this repository sets them: Railway stops reading `railway.toml` files on 2026-12-01, and the three this repository had were removed in Build B1a. This table is the record of what each service's Settings must hold.

| Setting, with its label in Settings | `api` | `worker` | `beat` |
|---|---|---|---|
| What it does | Serves the API | Runs queued jobs | Queues the sweep every 5 minutes |
| Build command: "Custom Build Command" | `uv sync --frozen` | `uv sync --frozen` | `uv sync --frozen` |
| Start command: "Custom Start Command" | `uv run gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --access-logfile - --error-logfile -` | `uv run celery -A config worker --loglevel=info --concurrency=2` | `uv run celery -A config beat --loglevel=info --schedule=/tmp/celerybeat-schedule` |
| Health path: "Healthcheck Path" | `/healthz/` | empty | empty |
| Health timeout, in seconds: "Healthcheck Timeout" (shown once a path is set) | `120` | not used | not used |
| Restart policy: "Restart Policy" | `On Failure` | `On Failure` | `On Failure` |
| Restart retries: "Max restart retries" | `10` | `10` | `10` |
| Number of instances: "Regions & Replicas" | not set here | not set here | `1` |
| Public domain | yes | none | none |
| Config file path: "Railway Config File" (button "Add File Path") | empty | empty | empty |

No start command runs a migration. "Wait for CI" is off on all three.

`api` has the health path `/healthz/` with a timeout of 120 seconds, set on 2026-11-11 with Coach Ed's approval. Railway calls it once, at the start of each deployment, and switches traffic to the new deployment only after it answers 200; it is not continuous monitoring. The first deployment with the path set passed the check and succeeded (`df826a05`, commit `6725454`). The path was typed in the dashboard: the Railway CLI (`railway environment edit --service-config`) made no change.

The check works only because `/healthz/` is exempt from the HTTPS redirect. Railway's health check calls the service over plain HTTP, and the backend answers every other plain-HTTP request with a redirect to HTTPS (`SECURE_SSL_REDIRECT`). Before Build B1b that included `/healthz/`: with the path set, the deployment of 2026-11-11 failed (`33d647de`, HTTP 301 on `/healthz/`), and the path was removed until the exemption was merged (`9753aa68` succeeded without it). The exemption is `SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]` in `config/settings.py`: it matches only that exact path. Do not widen it, and do not remove it while the health path is set. From outside nothing changed: Railway's public edge answers plain HTTP with its own redirect before the request reaches the app, so only Railway's health check and services on the project's private network can call `/healthz/` over plain HTTP. The check sends the host `healthcheck.railway.app`, which must stay in `DJANGO_ALLOWED_HOSTS`; without it the check gets 400 and every deployment fails. `/healthz/` is also checked from outside over HTTPS after each deploy: `scripts/verify_deploy.sh` and the `verify-staging` workflow.

To create a service like these again:

1. Duplicate an existing service in the dashboard and, before its first deploy, change the name and the start command. Duplicating is how the variables are copied without anyone reading or retyping a value: `beat` was made by duplicating `worker`. The copy has the same repository and branch (`main`).
2. Check the service's values against the table in its Settings. Leave the config file path empty.
3. Check it has the variables it needs, by name only: `DATABASE_URL`, `REDIS_URL`, `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false` and `BILLING_ENABLED` at the least. `worker` and `beat` have the same variables. They do not have `SANTE_CATALOG_ENABLED` or `SANTE_SETTINGS_ENABLED`, which only `api` has.
4. No public domain and no health path. `beat` runs as exactly one instance: two would queue every sweep twice (harmless to the data, since a second sweep finds nothing due, but wasteful).

After changing a value: a change in the dashboard is staged. "Details" lists the staged changes and "Deploy Changes" applies them and redeploys the service. Read the value back from the service's Settings, and change this table in the same pull request or the next one, so the table and the dashboard never disagree. Every change to a service's Settings needs Coach Ed's approval.

The queue uses the Redis server of `REDIS_URL` on the database number after the cache's (cache on 0, queue on 1), so clearing the cache never drops queued work. It needs no variable of its own. Nothing is stored about a finished job.

While `BILLING_ENABLED` is unset on `worker`, every sweep it runs returns at once and reads nothing. So the services can be created before the switch is on. On staging `BILLING_ENABLED` is set on `worker`.

Check, in the service logs:

- `worker`: a line listing `billing.sweep` under `[tasks]`, then `ready`. After `beat` is up, every 5 minutes: `Task billing.sweep[...] received` and `succeeded ...: {...}`. The braces hold a count per transition, or nothing when nothing was due. No log line holds a personal value.
- `beat`: `beat: Starting...`, then `Scheduler: Sending due task billing-sweep (billing.sweep)` every 5 minutes. The first one comes 5 minutes after the start.

Checked on staging, 2026-11-11: `beat` deployment `a1f38b2a` succeeded. At 13:21:25 +08:00 the `beat` log showed `Scheduler: Sending due task billing-sweep (billing.sweep)`, and in the same second the `worker` log showed the task received, `Billing sweep: nothing due`, and succeeded.

If `worker` is down, queued sweeps are dropped after 5 minutes instead of piling up, and no site's state is wrong meanwhile: every read computes the state from the time. If `beat` is down, nothing is queued; run the sweep by hand.

To go back, remove the two services. Nothing else depends on them.

## Not in this runbook yet

- The Finance page itself, in the HQ staff app: a later pull request, in the frontend.
- The emails: a later pull request of B2a.
- Card payments, reminders by a gateway and statements: B2 and later.
