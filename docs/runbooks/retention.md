# Retention Runbook

Account-side retention (NPC Build 7, ClickUp z8uq9mdwdy). Plan: `docs/superpowers/plans/2026-11-11-backend-npc-build7-account-retention.md`.

Nothing here runs by itself today. Every rule is run by hand with `manage.py run_retention`, and one account is disposed of on request with `manage.py dispose_account`. Every rule of this build and the on-request disposal are behind `RETENTION_ENABLED`, which is off unless it is set to `1`. The export (`manage.py export_account`) changes nothing and does not need the switch.

## What needs Coach Ed's approval, each time and per environment

- Setting `RETENTION_ENABLED=1`. It starts nothing by itself: these rules run only by the commands below until slice 4 adds a schedule. The SMS build's once-a-day start runs only the SMS rules, never these.
- Running any command below against staging or production data. This includes the dry run and the export.

## Before any command here

Migration `accounts.0003` (the mobile number tables) must be applied in an environment before any retention, disposal or export command runs there. Disposal and export read those tables directly, whether or not texts are switched on, so that a number is never left behind; without the tables the command fails and changes nothing.

## The rules

| Rule | Deletes | When |
|---|---|---|
| `expired_subscriber_sessions` | Subscriber sessions | 30 days after they expired or were revoked |
| `expired_admin_sign_in_codes` | Emailed hq sign-in codes | 30 days after they expired |
| `expired_admin_sessions` | hq sessions | 30 days after they expired or were revoked |
| `expired_django_sessions` | Django's session rows (admin list notices; never a sign-in) | 30 days after they expired |
| `email_messages` | Email send records | 2 years after they were written |
| `never_verified_accounts` | Nothing: it disposes of accounts whose email was never verified, as in "What disposal does" below | 30 days after sign-up |
| `account_audit_events` | An account's audit events (sign-ins, sign-outs of other devices, linked sign-in providers, email and mobile changes, and the account and site history events) | 1 year after they were written. An `email_changed` event is kept 2 years: see "Security records" below |

No rule touches a billing or tax record, an SMS record, a recovery code or the audit record. Only `never_verified_accounts` touches an account or a site.

## Security records

Security records are kept 1 year. In this backend they are the account audit events, deleted by `account_audit_events`. The admin sign-in entries of the admin audit log are kept as rows (ruling D4).

One deliberate exception, approved by Coach Ed on 2026-11-11: an `email_changed` event is kept 2 years, not 1. It holds the old and the new address, and it is the only record of an address an account used before. A disposal reads it to delete the send records of that address, and those send records live 2 years. After 2 years no such send record can exist, and the event is deleted.

The IP address on a policy acceptance is proof of consent, not a security record. No rule touches it; a disposal blanks it.

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

## What disposal does

One account at a time, in one transaction. The rows stay; the personal values go.

| Table | Blanked or deleted | Kept |
|---|---|---|
| Account | Email becomes `deleted-<row number>@deleted.invalid`, name becomes empty, the closing date is set | The row, its public id, dates, referral code and referrer |
| Sessions, Google and Facebook links | Deleted | |
| Verified mobile number and mobile verification challenges | Deleted | |
| Policy acceptance | The IP address | The row, the version, the policies, the date |
| Account audit events | The detail of `email_changed` events (old and new address) | Every row and every other detail |
| Sites of the account | Store email and mobile, social links; the name becomes `Deleted site #<row number>`; the site is closed | The row, template, dates |
| Business profile | Business and registered name, TIN, DTI number, BIR number, address, complaints contact, privacy email | The row, the shipping estimates |
| Web address | Retired | The name stays taken and is never reissued |
| Email send records to the account's address | Deleted, except hq sign-in codes and lockout notices | |
| Email send records to an address the account used before an email change | Deleted when written up to the moment of that change, plus the change notice sent to the old address; hq sign-in codes and lockout notices stay. Later records to that address are not touched: it may belong to another account by then | |

Each disposal writes one audit entry, action `account_disposed`: the operator (none when the never-verified rule did it), the account's public id, the rule and the request reference, the count per table and the names of the blanked fields. Never a personal value.

An earlier address is found only while its `email_changed` event exists (2 years). A code sent to a new address that was never confirmed is not tied to the account; its send record follows the 2-year rule.

Afterwards the account cannot sign in, and `send_email` refuses its placeholder address: no email goes out and no send record is written. A person who signs up again with the same address is a new account.

Disposal cannot be undone from the application.

## A deletion request

The request reaches the DPO mailbox. Confirm who is asking before anything else. Give the request a reference number and use it as `--request-ref`: a number, never a name or an address (an address is refused), because it is written to the audit record.

`ACCOUNT` is the account's public id (`acc_...`) or its email. `--actor` is your own staff username.

1. Offer the export first (next section) if the person wants a copy: a disposed account cannot be exported.
2. Count first. This changes nothing, writes no audit entry and works with the switch off:

   ```bash
   railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py dispose_account ACCOUNT --actor USERNAME --request-ref REF --dry-run
   ```

3. Read the counts. The output is the public id and counts only.
4. Dispose. This needs `RETENTION_ENABLED=1` in that environment; without it the command stops and changes nothing:

   ```bash
   railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py dispose_account ACCOUNT --actor USERNAME --request-ref REF
   ```

5. Running it again answers "already disposed of" and changes nothing.

Not handled here yet, so check by hand before disposing: whether the subscriber still owes a final bill (ruling R-3; billing arrives with slice 4). SMS send records are not touched by a disposal: they hold the number a text went to and follow the SMS build's own 1-year rule.

## An export request

The export holds the account (email, name, dates, referral code), its policy acceptances, linked sign-in providers, sessions (device, masked IP, dates), its verified mobile number and mobile verification challenges (never the code), account audit events, the send records for its address, and each site with its web addresses, business profile and onboarding dates. It never holds a session token, a provider's user id, or anything of another account. It writes one audit entry, action `account_exported`, with counts only.

The service's disk is lost on every restart, so print the JSON and save it on your own computer through the ssh call:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py export_account ACCOUNT --actor USERNAME --request-ref REF --stdout > account-export.json
```

`--out-dir DIR` writes `account-export-<public id>-<time>.json` in an existing folder instead, readable by its owner only; it never replaces an existing file.

The file is personal data on your computer. Send it to the requester from the DPO mailbox, only after confirming who is asking, then delete it. Sending is done by hand; nothing here sends it.

## Turning it off

Unset `RETENTION_ENABLED`. Deleted rows cannot be brought back from the application; only a database backup holds them, until that backup expires.
