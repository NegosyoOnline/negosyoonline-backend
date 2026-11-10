# NPC Build 7: account-side retention in the new backend (plan)

Date: 2026-11-11. ClickUp task: z8uq9mdwdy (https://app.clickup.com/t/z8uq9mdwdy), parent z8uq9mbreu.
Plan branch: `claude/z8uq9mdwdy-account-retention-0a8a0b`, on `origin/main` at `63db0a4` (PR 46 is merged there). The build branches will be `feat/z8uq9mdwdy-retention-time-rules` and `feat/z8uq9mdwdy-account-disposal`, each cut from the latest `origin/main` when the plan is approved.
Status: approved by Coach Ed on 2026-11-11 with the recommended answers (2.9). PR A is being built on `feat/z8uq9mdwdy-retention-time-rules`.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9mdwdy, description (no comments, attachments, checklists or custom fields as of 2026-11-11) |
| Parent | ClickUp z8uq9mbreu, description (no comments): rulings R-2, R-4, D4, the retention summary and the wording rules |
| Brief | The coordinator's brief for this builder, 2026-11-11 (Coach Ed: "Start Build 7") |
| Legacy | Read for behaviour only, nothing copied: `twcinc/app/negosyoonline/retention.py` and the readiness spec r13, sections 2 and 3 (3.6 account disposal, 3.7 time rules, 3.9 command) |
| SMS design | `docs/superpowers/specs/2026-11-10-sms-gateway-mobile-verification-design.md` section 9.3 and the Build 1 plan, PR 4: one retention mechanism shared with this build |
| Scope doc | `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`. The out-of-scope line is line 172 at `63db0a4` (it was line 163 before PR 46) |
| Code | `apps/accounts`, `apps/notifications`, `apps/sites`, `apps/operators`, `apps/common`, `config/settings.py`, `docs/runbooks/migrations.md`, `.github/workflows/ci.yml` at `63db0a4` |

## 2. Items for Coach Ed

Each item is separate. The recommended answer is what the rest of the plan assumes. Nothing here is a ruling until he gives it.

### 2.1 What account disposal does to the account's site

- **Checked.** A `Site` belongs to an account (`on_delete=PROTECT`). Today a site is only its own row, its `Subdomain` rows, one `SiteBusinessProfile` and the account's `Onboarding` row. There are no orders, customers or media yet. The site rows hold personal details: contact email and mobile, social links, and on the business profile the registered name, TIN, DTI number, BIR number, address, complaints contact name and privacy contact email. No code sets `Site.deleted_at` today: a site cannot be closed by anyone.
- **The disagreement.** The Task lists "sites" among what the rules cover and asks for "account and site export". It also lists "store disposal" under "Not in this task". It does not say what happens to the site rows of a disposed account.
- **Options.**
  - (a) **Recommended.** Disposing of an account also blanks the personal fields of its sites (list in 4.3), marks each site deleted and retires its web address. The rows stay. This is not store disposal: no order, customer or media row exists to dispose of.
  - (b) Disposal is refused for any account that has a site, until store disposal has its own approved scope.
- **Consequence.** With (b), no account that finished onboarding can be disposed of, on request or otherwise, and a deletion request cannot be honoured from this backend. With (a), a disposed account leaves no TIN, address or contact detail behind; the web address name stays in the ledger and is never reissued (scope doc, section 5).

### 2.2 How a disposed account is marked: no migration (recommended) or a new column

- **Checked.** Every `Account` query reads every column. Staging deploys on merge, and migrations are a separate approved step (`docs/runbooks/migrations.md`). A new `Account` column would therefore break every account query on staging, sign-in included, between the deploy and the migration. The SMS build (Build 1, PR 3) has also planned the next `apps/accounts` migration number for its mobile tables.
- **Options.**
  - (a) **Recommended: no migration.** A disposed account is one whose `closed_at` is set and whose email is the placeholder `deleted-<row number>@deleted.invalid`. Both are written in one statement by the one disposal function. The date of disposal is in the audit record.
  - (b) A new nullable `Account.disposed_at`, migration `accounts.0003_account_disposed_at` (or the next free number). An explicit date on the row, at the cost of a migration approval, an order agreed with the session that owns `apps/accounts` migrations, and the migration applied at the same time as the deploy.
- **Consequence.** With (a), Build 7 needs **no migration in any app**. With (b), one migration in `apps/accounts`.

### 2.3 What happens to the email send records of a disposed address

- **Checked.** `EmailMessage` holds the full recipient address and no link to an account. The 2-year rule would keep those rows, with the address, for up to 2 years after the account is disposed of. Legacy deleted the account's own message records at disposal.
- **Recommended.** At disposal, delete the `EmailMessage` rows whose recipient is the account's address, except admin sign-in categories (`admin_signin_code`, `admin_lockout`), which belong to an admin identity and follow the 2-year rule.
- **Consequence.** If not approved, the address of a disposed subscriber stays readable in the send records for up to 2 years.

### 2.4 When on-request disposal may run

- **Checked.** The Task says each rule runs "behind a switch that is off by default" and "with the switch off, no rule deletes or blanks anything". It does not say whether one account disposed of on a person's request counts as a rule.
- **Recommended.** The switch covers everything that deletes or blanks, on-request disposal included. With the switch off, `dispose_account` refuses and only its dry run works. The export is not covered by the switch: it changes nothing.
- **Consequence.** A deletion request cannot be carried out in an environment until Coach Ed approves switching retention on there.
- **What the switch will mean later.** This build has no schedule, so today switching it on starts nothing. The SMS build will later add a once-a-day start that runs every registered rule (agreed between the two coordinators). From that release on, an environment with the switch on runs this build's rules every day without a command. Switching it on is therefore also the approval of that daily run.

### 2.5 The send guard

- **Checked.** After disposal the account's real address is gone from the row. Keeping it, or a fingerprint of it, to block future emails would keep an identifier of a disposed person, which R-2 does not allow.
- **Recommended.** `send_email` refuses any recipient at the placeholder domain `deleted.invalid`: no email, no send record, one log line naming the category only. A person who signs up again later with the same address is a new account and is emailed normally.
- **Consequence.** The guard protects the disposed row, not the old address. Emailing a person who came back is correct, not a leak.

### 2.6 How the export leaves the server

- **Checked.** The command runs inside the Railway service over `railway ssh`. A file written there is lost when the service restarts.
- **Recommended.** The command writes one JSON file to `--out-dir`, or prints the JSON with `--stdout` so the admin can save it on his own computer through the ssh call. How the file reaches the requester (from the DPO mailbox, after the admin has confirmed who is asking) is done by hand and is not built.
- **Consequence.** The export file holds personal data on the admin's computer until he sends and removes it. The runbook says so.

### 2.7 Two source disagreements, reported, not decided here

- **Billing and tax records.** The Task says they "are kept at least 5 years". The Parent's retention summary and R-6 say 10 years. This build never touches them, so nothing here depends on the answer; the policy text does.
- **"Deleted" and "disposed of".** R-4 says never-verified accounts are "deleted". The Task says "disposed of" and defines disposal as blanking with the row kept (R-2). The plan follows the Task: the row is kept.

### 2.8 Gaps found, outside the seven deliverables (not built here)

- **D4: the admin audit log's personal values are blanked after 1 year.** `OperatorAuditEntry` has the `personal` and `personal_blanked_at` fields for it, but no build blanks them, and the Task's seven items do not include it.
- **"Security records 1 year"** (Parent's summary): `AccountAuditEvent` rows and `PolicyAcceptance.ip` have no time rule in this Task. They are blanked only when the account is disposed of.
- Both are reported to the coordinator so a task can name them. They fit the same registry as one rule each.

### 2.9 Coach Ed's ruling, 2026-11-11

- On the plan, in the builder session: "Approve as recommended". This approved the recommended answer to 2.1 to 2.6: (2.1) disposal also blanks the site's personal fields; (2.2) no migration; (2.3) the send records of a disposed address are deleted at disposal; (2.4) the switch covers on-request disposal, and switching it on also approves the later daily run; (2.5) the guard refuses the placeholder domain; (2.6) the export is a file or printed JSON, sent by hand.
- Still needing his own approval: setting `RETENTION_ENABLED` in any shared environment, any command run against staging data, and any ClickUp write.

## 3. Scope, traced

### 3.1 Deliverables

| # | Task item | Built as | PR |
|---|---|---|---|
| 1 | Never-verified accounts disposed of 30 days after sign-up | Rule `never_verified_accounts` (4.2) | B |
| 2 | Account disposal: personal fields blanked, sign-in identities and sessions removed, the row kept; every blanking logged (R-2) | `apps/accounts/retention.py: dispose_account` (4.3), one audit row per account | B |
| 3 | Expired sessions, one-time codes and sign-in tokens deleted 30 days after they expire | Rules `expired_subscriber_sessions`, `expired_admin_sign_in_codes`, `expired_admin_sessions`, `expired_django_sessions` (4.2) | A |
| 4 | Email send records deleted after 2 years | Rule `email_messages` (4.2) | A |
| 5 | Account and site export on request, as a command | `manage.py export_account` (4.5) | B |
| 6 | Send guard: a disposed contact is never emailed | A check at the top of `send_email` (4.4) | B |
| 7 | Each rule as a manual command with a dry run and a run log with counts, behind a switch that is off by default | `manage.py run_retention`, `manage.py dispose_account`, the `retention_run` audit row, `RETENTION_ENABLED` checked inside every rule of this build (4.1) | A, B |

### 3.2 What the code has, where it differs from the Task's words

- **Never-verified accounts cannot be created by a subscriber today.** Every sign-up path creates the account only after the email is proven (`services._create_account` sets `email_verified_at`). An account without it can only come from the admin lists or the shell. The rule is built as written and will usually count zero.
- **Subscriber one-time codes are not rows.** They live in the cache for 10 minutes (`apps/accounts/codes.py`) and there is nothing to delete. The rows that do expire are subscriber sessions (`accounts.Session`), admin sign-in codes (`operators.AdminSignInChallenge`), hq sessions (`operators.AdminSession`) and Django's own session rows.
- **Nothing closes an account or a site today.** `Account.closed_at` and `Site.deleted_at` are read but never written by code.

### 3.3 Boundaries

Not built (Task, "Not in this task"): the hourly schedule or any other automatic start; the paid 90-day and never-paid 30-day rules and the "nothing owed" check; SMS send records; store disposal, one-customer erasure and the date-of-birth erase; anything that touches billing or tax records. Also not built: a page or button on hq (commands only), any API endpoint, the items in 2.8.

Later builds must join this one, and the plan leaves the place for them: slice 4 adds the "nothing owed" check to `dispose_account` and the schedule; SMS PR 3's mobile tables add themselves to `dispose_account` and to the export.

## 4. Design

### 4.1 The shared mechanism (SMS design, section 9.3; this build creates it, in that shape and with those names)

Agreed between the two coordinators, 2026-11-11: this build creates the shared piece, and the SMS build later only registers its own two rules and adds its once-a-day start. This build builds neither of those.

- **`apps/common/retention.py`**: a registry. A rule has a name and a function taking "now" and "dry run" and returning the count: the rows it removed or blanked, or would. `run_all(now, dry_run, only=None)` runs every registered rule, or only the named ones, each in its own transaction, so one failing rule never stops the others, and returns the count per rule and the name of any error. A name that is not registered is refused. Each app registers its rules from its own `retention.py`, loaded in its `AppConfig.ready()`.
- **The switch**: `RETENTION_ENABLED` in `config/settings.py`, read from the environment; only `1` turns it on; **off by default**.
- **Where the switch is checked: inside the rules.** Every rule this build registers goes through one wrapper. With the switch off, a real run of the rule touches nothing and returns 0, whoever called it: the command, or the SMS build's later daily start. The dry run still counts, because it changes nothing. The registry itself has no switch, so the SMS build's rules are not affected by this one.
- **`manage.py run_retention [--dry-run] [--only NAME]`**
  - `--dry-run` counts and prints; it writes nothing, not even a log row.
  - A real run prints the count per rule. For a rule held back by the switch it prints "switched off" instead of a count.
  - Output never holds an email, a name or a number.
- **The run log**: a real run writes one `OperatorAuditEntry`, action `retention_run`, no operator, `detail` = the count per rule, the rules that were switched off and any error names.
- **How R-2's blanking log relates to it.** `retention_run` says a run happened and how many rows each rule took. It does not name an account. Every blanking of an account writes its own `account_disposed` entry (4.3), inside the same transaction as the blanking, so a blanking can never exist without its entry. A run of `never_verified_accounts` that disposes of three accounts therefore leaves three `account_disposed` entries and one `retention_run` entry whose count for that rule is 3. Deleting expired rows is not a blanking and is logged by the count only.
- **No automatic start in this build.** Nothing here calls `run_all` except the command.

### 4.2 The time rules: exactly which rows each one deletes

`now` is the run time. A row on the boundary is deleted (`<=`).

| Rule | Rows | Deleted when |
|---|---|---|
| `expired_subscriber_sessions` | `accounts.Session` | `expires_at` or `revoked_at` is 30 days old or more |
| `expired_admin_sign_in_codes` | `operators.AdminSignInChallenge` | `expires_at` is 30 days old or more |
| `expired_admin_sessions` | `operators.AdminSession` | `expires_at` or `revoked_at` is 30 days old or more |
| `expired_django_sessions` | `django.contrib.sessions` rows (they carry notices on the admin lists, never a sign-in) | `expire_date` is 30 days old or more |
| `email_messages` | `notifications.EmailMessage` | `created_at` is 730 days old or more |
| `never_verified_accounts` | `accounts.Account` with no `email_verified_at`, not yet disposed of | `created_at` is 30 days old or more: disposed of as in 4.3, not deleted |

The count a rule returns is its rows; for `never_verified_accounts` it is the accounts disposed of. Every rule is behind the switch (4.1).

Not touched by any rule: `AdminRecoveryCode` (no expiry), `AdminIdentity`, `OperatorAuditEntry`, `PolicyAcceptance`, `AccountAuditEvent`, `Subdomain`, every SMS table.

### 4.3 Account disposal: exactly what is blanked, deleted and kept

One function, `apps/accounts/retention.py: dispose_account(account, *, now, rule, actor, request_ref)`, in one transaction. It locks the `Account` row, then the account's `Onboarding` row (the lock every site write takes), and checks again under the locks that the account is not already disposed of. The site and email steps are functions of `apps/sites` and `apps/notifications`; `apps/accounts` never writes their tables.

| Table | Blanked or deleted | Kept |
|---|---|---|
| `Account` | `email` becomes `deleted-<row number>@deleted.invalid`; `name` becomes empty; `closed_at` is set if empty | The row, `public_id`, `email_verified_at`, `created_at`, `referral_code`, `referred_by` |
| `Session` | Every row of the account is deleted | |
| `ExternalIdentity` | Every row of the account is deleted (Google and Facebook links) | |
| `PolicyAcceptance` | `ip` is emptied | The row, the policy version, the policies, `accepted_at` |
| `AccountAuditEvent` | `detail` of `email_changed` events is emptied (it holds the old and new address) | Every row, its event name and time, and the detail of every other event (method, provider, session id, field names, site id) |
| `Onboarding` | Nothing (dates only) | The row |
| `Site` (2.1) | `contact_email`, `contact_mobile`, `facebook_url`, `instagram_url`, `tiktok_url` become empty; `display_name` becomes `Deleted site #<row number>`; `deleted_at` is set if empty | The row, `public_id`, `template`, `accent_color`, `operator_hold`, `created_at` |
| `SiteBusinessProfile` (2.1) | `business_name`, `registered_name`, `tin`, `dti_business_number`, `bir_rsn`, `business_address`, `complaints_contact_name`, `privacy_contact_email` become empty | The row and the shipping estimates |
| `Subdomain` (2.1) | The active name becomes `retired` with `retired_at` | Every row; the name stays taken |
| `EmailMessage` (2.3) | Rows whose recipient is the account's address are deleted, except the two admin categories | |

- **The audit record (R-2).** In the same transaction, one `OperatorAuditEntry`, action `account_disposed`: the operator (the `--actor` of `dispose_account`; none when the never-verified rule did it), target the account's `public_id`, `reason` the rule (`never_verified` or `request`) and the request reference, `detail` the counts per table and the names of the blanked fields. No personal value is written to it.
- **On request.** `manage.py dispose_account ACCOUNT --actor USERNAME --request-ref REF [--dry-run]`, where `ACCOUNT` is the account's public id or its email. It needs the switch on (2.4). The dry run prints what would be blanked and deleted, as counts, and changes nothing. Output is counts and the public id only.
- **Afterwards.** Sign-in is refused (`account_closed`, existing check). A second run answers "already disposed of" and writes nothing.

### 4.4 The send guard

`apps/notifications/services.py: send_email` starts with one check: a recipient at `deleted.invalid` is refused. It sends nothing, writes no `EmailMessage`, logs the category, and raises the existing `EmailNotSent`, which every caller already handles. The placeholder domain is one constant in `apps/common`, used by the disposal and by the guard, so they cannot drift apart. `.invalid` is a reserved name that can never receive mail.

### 4.5 The export

`manage.py export_account ACCOUNT --actor USERNAME --request-ref REF (--out-dir DIR | --stdout)`. It reads only. It refuses a disposed account. It writes one audit row, action `account_exported` (actor, account public id, request reference, counts; no personal value).

The JSON holds, for that account: the account (public id, email, name, dates, referral code); policy acceptances; linked sign-in providers (provider and date); sessions (device label, masked IP, dates); account audit events; the send records for its address (category, status, dates); and each site with its web addresses, business profile and onboarding dates. It never holds a session token hash, an internal row number of another account, or anything about another account. A file is created readable by its owner only, and an existing file is never overwritten.

### 4.6 Files

**PR A: the mechanism and the time rules (no migration)**

| File | Change |
|---|---|
| `apps/common/retention.py` (new) | The registry, `run_all` and the switch wrapper |
| `apps/common/management/commands/run_retention.py` (new, with the two `__init__.py`) | The command |
| `apps/accounts/retention.py` (new), `apps/accounts/apps.py` | Rule `expired_subscriber_sessions`; `ready()` loads the rules |
| `apps/operators/retention.py` (new), `apps/operators/apps.py` | Rules `expired_admin_sign_in_codes`, `expired_admin_sessions`, `expired_django_sessions` |
| `apps/notifications/retention.py` (new), `apps/notifications/apps.py` | Rule `email_messages`; one added line in `ready()` |
| `apps/operators/audit.py` | Declares `retention_run` (`KEPT`) |
| `config/settings.py`, `.env.example`, `README.md` | `RETENTION_ENABLED`, off by default |
| `docs/runbooks/retention.md` (new) | The commands, the dry run first, the approvals each step needs |
| `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md` | The amendment in section 5 |
| Tests | `apps/common/tests/test_retention.py`, `apps/accounts/tests/test_retention.py`, `apps/operators/tests/test_retention.py`, `apps/notifications/tests/test_retention.py` |

**PR B: disposal, the never-verified rule, the guard and the export (no migration with 2.2 (a))**

| File | Change |
|---|---|
| `apps/common/disposal.py` (new) | The placeholder domain and `is_disposed_address` |
| `apps/accounts/retention.py` | `dispose_account`, rule `never_verified_accounts`, `export_account_data` |
| `apps/sites/retention.py` (new) | `blank_sites_of(account)`, `export_sites_of(account)` |
| `apps/notifications/retention.py` | `delete_records_for(address)`, `export_records_for(address)` |
| `apps/notifications/services.py` | The guard at the top of `send_email` |
| `apps/accounts/management/commands/dispose_account.py`, `export_account.py` (new, with the two `__init__.py`) | The two commands |
| `apps/operators/audit.py` | Declares `account_disposed` and `account_exported` (`KEPT`) |
| `docs/runbooks/retention.md` | The deletion-request and export steps |
| Tests | `apps/accounts/tests/test_disposal.py`, `test_export.py`, `apps/sites/tests/test_retention.py`, `apps/notifications/tests/test_send_guard.py` |

### 4.7 Migrations

| Answer to 2.2 | App | Number | Adds |
|---|---|---|---|
| (a) recommended | none | none | Nothing. CI's `makemigrations --check` proves it |
| (b) | `apps/accounts` | `0003_account_disposed_at`, or the next free number agreed through the coordinator | One nullable date column on `Account` |

No migration in `apps/notifications`, `apps/sites`, `apps/operators` or `apps/common` under either answer. No new dependency. One new variable, `RETENTION_ENABLED`, which stays unset everywhere until Coach Ed approves otherwise.

### 4.8 Files other sessions also edit

No pull request is open at `63db0a4`, and PR 46 is merged, so nothing overlaps today. `apps/notifications/migrations/0002_sms_gateway.py` is on `main`; this plan adds no `apps/notifications` migration and does not assume `0002` is applied on staging (no rule of this build reads an SMS table).

The SMS build's planned PRs, none open yet: PR 2 touches `apps/notifications`, a new action in `apps/operators/audit.py` and `config/urls.py`; PR 3 adds `apps/accounts` migration `0003` (`AccountMobile`, `MobileOtpChallenge`); PR 4 registers its two rules in the registry this build creates and adds the daily start. This build adds lines and never edits or removes that session's lines. Whichever PR merges second merges `main` first and keeps both. Before each PR the open PRs are listed again and any shared file is reported to the coordinator first.

Not built here: the SMS rules (send records after 365 days, the 24-hour challenge clean-up) and the daily start.

## 5. The amendment to the scope doc

In `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`:

**Section 10, the line that is line 172 today.** Now:

> - Data retention and disposal automation beyond the dates recorded on the subscription, except the SMS retention job, which is in scope with slice 8: send records are deleted after 1 year and mobile verification challenges 24 hours after expiry.

Proposed:

> - Data retention and disposal automation beyond the dates recorded on the subscription, with two exceptions. The SMS retention job is in scope with slice 8: send records are deleted after 1 year and mobile verification challenges 24 hours after expiry. The account-side retention rules of NPC Build 7 (z8uq9mdwdy) are in scope as manual commands behind `RETENTION_ENABLED`, which is off by default: accounts whose email was never verified are disposed of 30 days after sign-up; an account is disposed of on request; expired sessions and admin sign-in codes are deleted 30 days after they expire; email send records are deleted after 2 years; an account and its sites are exported on request. Still out of scope: their schedule (slice 4), the rules that need billing, store disposal, customer erasure, and anything that touches billing or tax records.

**Section 12, one new dated entry**, 2026-11-11, saying the same and naming this plan. If Coach Ed chooses 2.2 (b), section 6's `Account` line also gains `disposed_at`.

## 6. Tests, against the Task's "Accepted when"

Run with `uv run python manage.py test`, then `manage.py check`, `makemigrations --check --dry-run`, and `check --deploy` with `DJANGO_DEBUG=false`. Test first at each step.

| Accepted when | Tests |
|---|---|
| A dry run on test data reports the expected counts and changes nothing | **T-DRY** For every rule and for `dispose_account`: build rows on both sides of each limit, run the dry run, compare the printed counts with the expected ones, then compare every table's rows with a copy taken before (equal), and check that no audit row was written |
| With the switch off, no rule deletes or blanks anything | **T-OFF** With `RETENTION_ENABLED` unset: `run_all` called directly, as an automatic start would, returns 0 for every rule of this build and every table is unchanged; `run_retention` prints "switched off" for each; a test rule registered without the wrapper still runs, which proves the gate is in the rules and not in the registry; `dispose_account` without `--dry-run` stops with an error. The switch reads only `1` as on |
| With the switch on, each rule removes exactly its own rows, and the run log records the counts | **T-RULE**, one per rule: a row of 30 days is removed and one of 29 days stays (730 and 729 for email); a revoked session is counted from `revoked_at`; a live session, a verified account, a recovery code and every table of the other rules are untouched. `--only` runs one rule alone. **T-LOG** one `retention_run` row with no operator, the count per rule and no personal value; three never-verified accounts leave three `account_disposed` rows and a count of 3; a name that is not registered is refused; a rule that raises is recorded by name and the other rules still run. **T-DISP** after disposal each field in 4.3 is blank or placeholder, sessions and identities are gone, every kept column is equal to before, the site is closed and its name retired, the `account_disposed` row holds counts and field names only; a second run changes nothing; the never-verified rule disposes of an unverified account of 30 days and leaves one of 29 days and a verified one |
| A disposed contact cannot be emailed; a test proves it | **T-GUARD** after disposal, `send_email` to the account's address raises `EmailNotSent`, the outbox is empty and no `EmailMessage` row is written; sign-in with the old session is refused; asking for a sign-in code for the placeholder address sends nothing; a new sign-up with the original address is emailed normally |
| Tests pass in CI | The CI run on each PR; the plan adds no CI step |
| (deliverable 5) | **T-EXPORT** the JSON holds the account's rows and its site's, no token hash and nothing of a second account; the file is owner-only and is not overwritten; one `account_exported` row; a disposed account is refused |

Nothing in this build is visible on a page, so there is no visual check. The local check is the commands' dry run against the local test database.

## 7. PR split and release

1. **PR A**, then **PR B**, each from the latest `origin/main`, each with its own CI run. Neither changes behaviour when it is deployed: the switch is off, and the guard only refuses an address that cannot exist until an account is disposed of.
2. **Separate approvals by Coach Ed, none given by this plan:** setting `RETENTION_ENABLED` on staging; any command run against staging data, the dry run included; the migration, if 2.2 (b) is chosen; any ClickUp write.
3. **Staging order once approved:** dry run first and its counts reported; then the switch; then a real run; then the run log read back.
