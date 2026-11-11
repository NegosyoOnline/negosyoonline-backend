# NPC Build 10: retention gaps left after Build 7 (plan)

Date: 2026-11-11. ClickUp task: z8uq9mdzve (https://app.clickup.com/t/z8uq9mdzve), parent z8uq9mbreu.
Branches: PR A on `feat/z8uq9mdzve-retention-gaps`, PR B on `feat/z8uq9mdzve-security-records`, each cut from `origin/main` at `2ca07eb` or later.
Status: approved by Coach Ed on 2026-11-11 with the recommended answers (2.5). Merges, `RETENTION_ENABLED`, commands against shared data and ClickUp writes still need his own approval.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9mdzve, description (no comments, attachments, checklists or custom fields as of 2026-11-11; status Open) |
| Parent | ClickUp z8uq9mbreu, description (no comments): rulings R-2 and D4, the retention summary, the wording rules |
| Brief | The coordinator's brief for this builder, 2026-11-11 (Coach Ed: "Start Build 10") |
| Build 7 | `docs/superpowers/plans/2026-11-11-backend-npc-build7-account-retention.md` (section 2.8 names the gaps) and `docs/runbooks/retention.md` |
| Code | `apps/common/retention.py`, `apps/accounts/{retention,services,models,sessions,codes}.py`, `apps/sites/{retention,services}.py`, `apps/notifications/{retention,services,models}.py`, `apps/operators/{audit,retention,models}.py` and their tests, at `2ca07eb` |

## 2. Items for Coach Ed

The recommended answer is what the rest of the plan assumes. Nothing here is a ruling until he gives it.

### 2.1 Which rows are "security records" (item 2)

- **Checked.** One table in this backend holds security records with no time rule: `accounts.AccountAuditEvent`, the per-account event log. Its events today: `signed_in` (method, session id), `sessions_revoked`, `identity_linked`, `identity_removed`, `email_changed` (old and new address), `mobile_verified`, `mobile_changed`, and also `account_created`, `profile_updated`, `site_created`, `site_updated`. Only one piece of code reads old rows: the 24-hour pause after an email change, which reads the newest `email_changed` event. Section 3.2 lists every other candidate and why it needs no new rule.
- **Options.**
  - (a) **Recommended.** Every row of the table is deleted 1 year after it was written, whatever its event (with the one exception in 2.2).
  - (b) Only the sign-in and credential events are deleted; `account_created`, `profile_updated`, `site_created` and `site_updated` are kept until the account is disposed of.
- **Consequence.** With (a), after a year the log no longer shows how an account was created or when a site was made; the account and site rows still hold their own dates. With (b), the rule needs a list of event names that every new event must be added to.

### 2.2 How long `email_changed` events are kept (items 2 and 3 meet here)

- **Checked.** The backend does record an account's earlier addresses: each `email_changed` event holds the old and the new address in its detail. Nothing else does. Item 3 reads them from there, so **item 3 needs no migration**. Email send records live 2 years. If `email_changed` events were deleted at 1 year, an account disposed of 13 months after an email change would leave the send records of its old address behind until the 2-year rule takes them.
- **Options.**
  - (a) **Recommended.** `email_changed` events are kept 2 years (730 days), the same as email send records, and every other event 1 year. After 2 years no send record of the old address can exist, so the event is no longer needed.
  - (b) All events are deleted at 1 year. Send records of an address changed more than a year before disposal stay until the 2-year rule deletes them.
  - (c) A new table of earlier addresses. Needs a migration in `apps/accounts`; not recommended, since (a) does the same without one.
- **Consequence.** With (a), one kind of security record (the address change, which holds two email addresses) is kept 2 years, not the 1 year the retention summary says. With (b), the summary's 1 year holds exactly, and disposal can miss old-address send records for up to a year.

### 2.3 The IP address on a policy acceptance

- **Checked.** `PolicyAcceptance.ip` is the address a person accepted the Terms and Privacy Policy from. Build 7's plan (2.8) listed it beside the security records. It is blanked at disposal and has no time rule.
- **Recommended.** Leave it as it is: it is proof of consent, not a security record, and it is useful for as long as the account exists. No rule is built for it.
- **Consequence.** If Coach Ed rules that it is a security record, one more rule blanks `ip` 1 year after `accepted_at` and keeps the row. No migration either way.

### 2.4 Found, not in the Task, not built unless ruled

- **The export and earlier addresses.** `export_account` lists the send records of the account's current address only. The Task asks for the earlier addresses at disposal, not in the export. Not built here.
- **Codes sent to an address that was never confirmed.** `start_email_change` emails a code to a new address; if the change is never confirmed, no event records that address. Its send record cannot be tied to the account and stays under the 2-year rule.

### 2.5 Coach Ed's ruling, 2026-11-11

- In the coordinator chat, relayed by the coordinator: "Approve as recommended". This settles: (2.1) every `AccountAuditEvent` row is deleted at 1 year, with the 2.2 exception; (2.2) `email_changed` events are kept 2 years, a deliberate exception to "security records 1 year"; (2.3) `PolicyAcceptance.ip` is left as it is; the `audit.record` guard in 4.1 is approved; (2.4) neither found item is built. No migration.

## 3. Scope, traced

### 3.1 Deliverables

| # | Task item | Built as | PR |
|---|---|---|---|
| 1 | Admin audit log: rows kept, personal values blanked 1 year after the row was written (D4) | Rule `admin_audit_personal_values` (4.1) | A |
| 2 | Security records, 1 year: a rule for each table that exists | Rule `account_audit_events` (4.2) | B |
| 3 | At disposal, also delete the send records of any address used before an email change; admin categories excluded | `dispose_account` reads the earlier addresses from the `email_changed` events before it blanks them (4.3) | B |
| 4 | `create_first_site` refuses an account whose `closed_at` is set | One check under the account's site lock (4.4) | A |

### 3.2 Item 2: every candidate table, and the finding

| Table or store | Holds | Finding |
|---|---|---|
| `accounts.AccountAuditEvent` | Sign-ins, sign-outs of other devices, linked providers, email and mobile changes | **No time rule today. Gets the new rule.** |
| `operators.OperatorAuditEntry` (`admin_sign_in_ok`, `admin_sign_in_failed`, `admin_locked`, `admin_sign_out`) | Admin sign-in events and lockouts | D4 says the rows are kept. Covered by item 1, not deleted |
| `accounts.Session`, `operators.AdminSession`, `operators.AdminSignInChallenge`, Django session rows | Device label, masked IP, code hashes | Already deleted 30 days after expiry (Build 7) |
| `accounts.MobileOtpChallenge`, `notifications.SmsMessage` | Verification challenges, SMS send records | Owned by the SMS build's two rules. Not touched |
| `accounts.PolicyAcceptance.ip` | IP at acceptance | See 2.3. No rule proposed |
| `operators.AdminIdentity.failed_attempts`, `locked_until` | The current lock state of one admin identity | State, not a record. Nothing to delete |
| Sign-in codes and rate limits | `apps/accounts/codes.py`, `apps/common/throttle.py` | Held in the cache with their own expiry. No rows |

There is no sign-in-attempt, lockout or IP-log table for subscribers in this backend.

### 3.3 Boundaries

Not built (Task, "Not in scope"): the "nothing owed" check; anything about SMS send records at disposal; turning `RETENTION_ENABLED` on anywhere; any command against staging or production data. Also not built: a schedule, a page on hq, any API change, the items in 2.4, any change to the rules `sms_messages` and `mobile_challenges` or to the daily start.

## 4. Design

Every new rule registers through `apps/common/retention.py` and is wrapped with `behind_switch`: with `RETENTION_ENABLED` off a real run touches nothing, and a dry run only counts. A row on the boundary is taken (`<=`), as in Build 7.

### 4.1 Item 1: the admin audit log (D4)

- **Checked.** `OperatorAuditEntry` already has `personal` and `personal_blanked_at` (migration `operators.0002`, on `main`). **No migration.** `audit.py` declares four actions as `PERSONAL` (the admin sign-in events); every other action is `KEPT`. No code passes `personal=` to `audit.record` today, so every existing row's `personal` is empty. The model refuses `save()` on an existing row; the rule uses a queryset update, which is how the existing test `test_personal_values_can_be_blanked_while_the_row_stays` does it.
- **Rule `admin_audit_personal_values`** in `apps/operators/retention.py`: rows whose action is declared `PERSONAL`, whose `at` is 365 days old or more, and whose `personal_blanked_at` is empty. A real run sets `personal` to empty and `personal_blanked_at` to the run time, and returns the count. Nothing else on the row changes; no row is deleted. A second run counts zero.
- **The log (R-2).** The `retention_run` entry carries the rule's count, and each blanked row carries its own `personal_blanked_at`. No separate audit entry is written per blanked row: that would be an audit entry about an audit entry, for a row that names no subscriber.
- **One guard added to `audit.record`.** It refuses a `personal` value on an action declared `KEPT`. Without it a personal value written to a `KEPT` row would never be blanked. No caller does this today, so no behaviour changes. This is the builder's choice inside item 1; strike it if unwanted.

### 4.2 Item 2: account audit events

- **Rule `account_audit_events`** in `apps/accounts/retention.py`: deletes `AccountAuditEvent` rows whose `created_at` is 365 days old or more; for `email_changed` events, 730 days old or more (2.2 (a)). With 2.1 (b) it filters on a list of event names; with 2.2 (b) there is one limit.
- The 24-hour email-change pause reads only an event younger than 24 hours, so it is not affected.
- Deleting expired rows is logged by the count in `retention_run`, as for every Build 7 time rule.

### 4.3 Item 3: send records of an earlier address

- **Where the addresses come from.** Inside `dispose_account`, under the account lock and before the `email_changed` details are blanked, read each event's `old` address and the event's time.
- **What is deleted.** For each earlier address: `EmailMessage` rows to that address (case-insensitive), admin categories excluded, written at or before the moment of the change, plus the `email_changed_notice` sent to it within 24 hours after the change. The time limit matters: after the change, the address can belong to another account that signed up with it, and that account's records must not be deleted.
- **Where the code goes.** A new function `delete_earlier_records_for(periods, dry_run)` in `apps/notifications/retention.py`, next to Build 7's `delete_records_for`; `apps/accounts` still never writes that table. No line of the SMS build in that file is changed (4.6).
- **Counts and the log.** The disposal's counts gain `earlier_address_send_records`; it appears in the dry run and in the `account_disposed` entry. No address is written to the entry.
- **Limit.** Earlier addresses are found only while their `email_changed` event exists (2.2).

### 4.4 Item 4: a closed account cannot create a site

- **Checked.** Over the API a closed account already cannot reach `create_first_site`: `find_session` refuses a session whose account is closed. The service itself has no check, so any other caller could create a site for a closed account.
- **Change.** In `apps/sites/services.py: create_first_site`, after the account's `Onboarding` row is locked, read `closed_at` again from the database and refuse with the existing error `account_closed` ("This account is closed.", 403). Disposal takes the same lock, so a site cannot be created while, or after, the account is disposed of.
- **Not provable locally.** Default tests use SQLite, where row locks are not exercised. The test proves the refusal; the ordering against a concurrent disposal rests on the lock and is not exercised.

### 4.5 Files

**PR A: items 1 and 4 (no migration, no open decision)**

| File | Change |
|---|---|
| `apps/operators/retention.py` | Rule `admin_audit_personal_values` and its registration |
| `apps/operators/audit.py` | `record` refuses `personal` on a `KEPT` action |
| `apps/sites/services.py` | The closed-account check in `create_first_site` |
| `docs/runbooks/retention.md` | The new rule in the rules table; the line "No rule touches ... the audit record" corrected |
| `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md` | Section 10 line 172 and one dated entry in section 12 name Build 10's rules |
| Tests | `apps/operators/tests/test_retention.py`, `test_audit.py`, `apps/sites/tests/test_services.py` |

**PR B: items 2 and 3 (no migration; waits for 2.1 and 2.2)**

| File | Change |
|---|---|
| `apps/accounts/retention.py` | Rule `account_audit_events`; `dispose_account` reads the earlier addresses and adds the count |
| `apps/notifications/retention.py` | New function `delete_earlier_records_for`, beside `delete_records_for` |
| `docs/runbooks/retention.md` | The new rule; the disposal table gains the earlier-address row and its limit |
| Tests | `apps/accounts/tests/test_retention.py`, `test_disposal.py`, `apps/notifications/tests/test_retention.py` |

### 4.6 Migrations and shared files

- **No migration in any app.** CI's `makemigrations --check` proves it. No new setting, variable or dependency.
- **Open PRs at `2ca07eb`, 2026-11-11:** none (`gh pr list --state open` returned nothing), so nothing overlaps today. The list is read again before each PR is opened and before it is merged.
- **`apps/notifications/retention.py`** is shared with the SMS Coordinator (task z8uq9md33v). PR B adds one function in the Build 7 part of the file and changes no line of `sms_messages`, `mobile_challenges`, `DAILY_RULES`, `run_daily_rules` or `start_daily`. The coordinator is told before PR B is opened.
- The new rules are not added to the SMS build's daily start. They run only by `manage.py run_retention`.

## 5. Tests, against the Task's "Accepted when"

Run with `uv run python manage.py test`, then `manage.py check`, `makemigrations --check --dry-run`, and `check --deploy` with `DJANGO_DEBUG=false`. Test first at each step.

| Accepted when | Tests |
|---|---|
| Item 1 has tests that pass in CI, including a dry run that changes nothing | **T-AUDIT** A `PERSONAL` row of 365 days with a personal value is blanked and stamped; one of 364 days and a `KEPT` row of 365 days are untouched; action, operator, target, reason, detail and `at` are equal to before; no row is deleted; a second run counts zero. The dry run reports the same count and every row is equal to before, with no `retention_run` entry. Switch off: nothing changes and the command prints "switched off". `audit.record` refuses `personal` on a `KEPT` action |
| Item 2, the same | **T-EVENTS** An event of 365 days is deleted and one of 364 days stays; an `email_changed` event of 365 days stays and one of 730 days is deleted (2.2 (a)); another account's young rows are untouched; the email-change pause still works. Dry run and switch-off as above |
| Item 3, the same | **T-EARLIER** An account that changed A to B to C is disposed of: the send records of A, B and C are gone, the notice sent to the old address included; admin-category records to those addresses stay; records of a second account that signed up with A after the change stay; the `account_disposed` entry holds the count and no address. The dry run reports the same counts and changes nothing. Existing disposal tests still pass |
| Item 4, the same | **T-CLOSED** `create_first_site` for an account with `closed_at` set raises `account_closed` and creates no site, web address, profile or event; an account closed after the object was loaded is also refused; an open account still succeeds. (Nothing to dry-run: this item deletes nothing) |
| The runbook lists the new rules | `docs/runbooks/retention.md` read after each PR: `admin_audit_personal_values` (A), `account_audit_events` and the earlier-address row (B) |
| Staging deploys and `/healthz/` returns 200, with retention still off | After each merge, with Coach Ed's approval to merge: the staging deploy is confirmed and `/healthz/` is read. `RETENTION_ENABLED` is not set by this build |

Nothing in this build is visible on a page, so there is no visual check.

## 6. PR split and release

1. **PR A** (items 1 and 4) can start as soon as the plan is approved. **PR B** (items 2 and 3) starts once 2.1 and 2.2 are answered. Each is cut from the latest `origin/main`, with its own CI run. Neither changes behaviour when deployed: the rules are behind the switch, which is off, and the site check only refuses an account that cannot sign in anyway.
2. **Separate approvals by Coach Ed, none given by this plan:** each merge; setting `RETENTION_ENABLED` anywhere; any command against staging or production data, the dry run included; any ClickUp write.

## 7. What was checked and what was not

- **Read:** the Task and Parent in ClickUp; the Build 7 plan and the runbook; every file in section 1's Code row; `gh pr list --state open` (no open PR).
- **Not run:** the test suite on this branch. No code has changed from `2ca07eb`.
- **Inferred, not proven:** that no row in a shared environment holds a value in `OperatorAuditEntry.personal`. The code never writes one; the data was not read (reading it needs Coach Ed's approval).
