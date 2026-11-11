# Billing Runbook

Billing Build B1 (ClickUp z8uq9mdwrv): one subscription and one trial per website, billing cycles, the states and their transitions. Plan: `docs/superpowers/plans/2026-11-11-backend-billing-b1-subscription-lifecycle.md`.

B1 takes no payment. Nothing here charges anyone: there is no payment provider, no payment method and no statement until Billing Build B2.

Every step below that touches staging or production needs Coach Ed's approval for that step, the dry runs included. Nothing here is approved by this document. Production is not part of B1.

## Variable

| Name | Meaning |
|---|---|
| `BILLING_ENABLED` | `1` turns billing on. Unset: site creation writes no subscription, the billing endpoints answer 404, an account disposal skips its billing step, and every command below says the switch is off and reads and writes nothing. |

It is not a secret. It exists so the code can be deployed before the tables are created.

## Order on staging

Each step is its own approval.

1. Merge and deploy. The code is dark while `BILLING_ENABLED` is unset.
2. Apply `billing.0001_initial` (`docs/runbooks/migrations.md`). It only adds three tables.
3. Set `BILLING_ENABLED=1` on `api`. Never before step 2: site creation and the billing endpoints would fail on the missing tables. From this moment every new site gets its subscription when it is created.
4. Dry-run the backfill and read the counts ("Backfill", below).
5. Run the backfill with `--apply`.
6. Check: `GET /v1/sites/{site_id}/billing/` for a test site, signed in as its owner.

To go back, unset `BILLING_ENABLED`. The tables and their rows stay; nothing reads them.

## What the switch does to existing sites

A site's trial counts from its account's creation date, not from the site's creation or from the day billing is switched on. So when the backfill is applied, every site whose account is older than 14 days is ended at once (`cancelled_no_payment_method`), and nothing brings it back until B2 takes payments, except an exemption (below). A site whose account is younger than 14 days stays on trial until its own day 14.

Until the availability part of B1 (the by-host lookup and the Billing-only rule) is deployed, an ended site shows the ended state only on `GET billing/`; nothing else is turned off yet.

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

Until the `worker` and `beat` services exist, the sweep runs only by hand.

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

## Not in this runbook yet

- The `worker` and `beat` services and the 5-minute schedule: they arrive with the queue part of B1, each on its own approval.
- Payments, payment methods, reminders and statements: B2 and later.
