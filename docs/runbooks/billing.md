# Billing Runbook

Billing Build B1 (ClickUp z8uq9mdwrv): one subscription and one trial per website, billing cycles, the states and their transitions. Plan: `docs/superpowers/plans/2026-11-11-backend-billing-b1-subscription-lifecycle.md`.

B1 takes no payment. Nothing here charges anyone: there is no payment provider, no payment method and no statement until Billing Build B2.

Every step below that touches staging or production needs Coach Ed's approval for that step, the dry runs included. Nothing here is approved by this document. Production is not part of B1.

## Variable

| Name | Meaning |
|---|---|
| `BILLING_ENABLED` | `1` turns billing on. Unset: site creation writes no subscription, the billing endpoints answer 404, the by-host lookup answers 404 for every host, no owner endpoint is limited to Billing, the "Santé Site Owner" label ignores subscriptions, an account disposal skips its billing step, and every command below says the switch is off and reads and writes nothing. |

It is not a secret. It exists so the code can be deployed before the tables are created.

## Order on staging

Each step is its own approval.

1. Merge and deploy. The code is dark while `BILLING_ENABLED` is unset.
2. Apply `billing.0001_initial` (`docs/runbooks/migrations.md`). It only adds three tables.
3. Set `BILLING_ENABLED=1` on `api`. Never before step 2: site creation and the billing endpoints would fail on the missing tables. From this moment every new site gets its subscription when it is created.
4. Dry-run the backfill and read the counts ("Backfill", below).
5. Run the backfill with `--apply`.
6. Check: `GET /v1/sites/{site_id}/billing/` for a test site, signed in as its owner. Then `GET /v1/public/sites/by-host/?host=NAME.PARENT` with no sign-in, where `NAME` is that site's web address and `PARENT` is the value of `SITE_PARENT_HOST`: 200 with seven public fields for a site on trial, and the plain 404 for an ended one and for a name nobody holds.

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

Until the `worker` and `beat` services exist ("Worker and beat", below), the sweep runs only by hand. Once they exist it also runs every 5 minutes; running it by hand stays safe.

Print what is due. This writes nothing:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py billing_sweep --dry-run
```

Run it:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e staging -s api -- uv run python manage.py billing_sweep
```

The output is a count per transition. A second run answers "Nothing is due."

## An account disposal

`manage.py dispose_account` cancels the account's subscriptions first. See "A request to delete an account" in `docs/runbooks/retention.md`.

## Worker and beat

Two services run the sweep on a schedule. The code for them is merged; **neither service exists until Coach Ed approves creating it**, and each is its own approval: `worker` first, then `beat`. Staging only.

| Service | What it does | Config file | Start command |
|---|---|---|---|
| `worker` | Runs queued jobs | `railway.worker.toml` | `uv run celery -A config worker --loglevel=info --concurrency=2` |
| `beat` | Queues the sweep every 5 minutes | `railway.beat.toml` | `uv run celery -A config beat --loglevel=info --schedule=/tmp/celerybeat-schedule` |

For each service, in the `negosyoonline-backend` project, staging environment:

1. Create the service from this repository, the same branch as `api`.
2. In its settings, set the config file path to the file in the table. `railway.toml` stays the `api` service's file and is not changed.
3. Give it the same variables as `api`. It needs `DATABASE_URL`, `REDIS_URL`, `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false` and `BILLING_ENABLED` at the least.
4. No public domain and no health path. `beat` runs as exactly one instance: two would queue every sweep twice (harmless to the data, since a second sweep finds nothing due, but wasteful).

The queue uses the Redis server of `REDIS_URL` on the database number after the cache's (cache on 0, queue on 1), so clearing the cache never drops queued work. It needs no variable of its own. Nothing is stored about a finished job.

While `BILLING_ENABLED` is unset on `worker`, every sweep it runs returns at once and reads nothing. So the services can be created before the switch is on.

Check, in the service logs:

- `worker`: a line listing `billing.sweep` under `[tasks]`, then `ready`. After `beat` is up, every 5 minutes: `Task billing.sweep[...] received` and `succeeded ...: {...}`. The braces hold a count per transition, or nothing when nothing was due. No log line holds a personal value.
- `beat`: `beat: Starting...`, then `Scheduler: Sending due task billing-sweep (billing.sweep)` every 5 minutes. The first one comes 5 minutes after the start.

If `worker` is down, queued sweeps are dropped after 5 minutes instead of piling up, and no site's state is wrong meanwhile: every read computes the state from the time. If `beat` is down, nothing is queued; run the sweep by hand.

To go back, remove the two services. Nothing else depends on them.

## Not in this runbook yet

- Payments, payment methods, reminders and statements: B2 and later.
