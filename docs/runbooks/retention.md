# Retention Runbook

Account-side retention (NPC Build 7, ClickUp z8uq9mdwdy). Plan: `docs/superpowers/plans/2026-11-11-backend-npc-build7-account-retention.md`.

Nothing here runs by itself today. Every rule is run by hand with `manage.py run_retention`, and every rule of this build is behind `RETENTION_ENABLED`, which is off unless it is set to `1`.

## What needs Coach Ed's approval, each time and per environment

- Setting `RETENTION_ENABLED=1`. Once the SMS build adds its once-a-day start, an environment with the switch on runs these rules every day without a command, so switching it on also approves that daily run.
- Running any command below against staging or production data. This includes the dry run.

## The rules

| Rule | Deletes | When |
|---|---|---|
| `expired_subscriber_sessions` | Subscriber sessions | 30 days after they expired or were revoked |
| `expired_admin_sign_in_codes` | Emailed hq sign-in codes | 30 days after they expired |
| `expired_admin_sessions` | hq sessions | 30 days after they expired or were revoked |
| `expired_django_sessions` | Django's session rows (admin list notices; never a sign-in) | 30 days after they expired |
| `email_messages` | Email send records | 2 years after they were written |

No rule touches a billing or tax record, an SMS record, a site, a recovery code or the audit record.

## Procedure

Replace `<env>` with `staging` or `production`. The identifiers are in `docs/runbooks/migrations.md`.

1. Count first. This changes nothing and writes no log row; it works with the switch off:

   ```bash
   railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py run_retention --dry-run
   ```

2. Read the counts. Stop if a count is not what the age of the environment explains.
3. With Coach Ed's approval, set `RETENTION_ENABLED=1` on the `api` service of that environment and wait for the redeploy.
4. Run:

   ```bash
   railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py run_retention
   ```

   `--only NAME` runs one rule; repeat it for more. A rule printed as `switched off` did nothing: the switch is not on.
5. Read the run log: on hq there is no list for it yet, so read the newest `retention_run` audit entry from the shell, or run step 1 again and see the counts at zero.

## What the output and the log hold

Rule names and counts only. Never an email address, a name or a number. Each real run writes one audit entry, action `retention_run`, with no operator and the count per rule, the rules held back by the switch, and the name of any rule that failed. A failed rule keeps none of its own changes and does not stop the others; the command then ends with an error.

## Turning it off

Unset `RETENTION_ENABLED`. Deleted rows cannot be brought back from the application; only a database backup holds them, until that backup expires.
