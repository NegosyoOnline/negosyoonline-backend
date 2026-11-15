# Retention Runbook

Account-side retention (NPC Build 7, ClickUp z8uq9mdwdy, and NPC Build 10, ClickUp z8uq9mdzve). Plans: `docs/superpowers/plans/2026-11-11-backend-npc-build7-account-retention.md` and `docs/superpowers/plans/2026-11-11-backend-npc-build10-retention-gaps.md`.

## The schedule

Where the `worker` and `beat` services run (staging; production has none yet), the Celery task `retention.run` (`apps/common/tasks.py`) runs **every** registered rule once a day at 03:00 Asia/Manila (beat entry `retention-daily`, `CELERY_BEAT_SCHEDULE`). A per-Manila-day lock (`retention:all:<YYYYMMDD>`) means a duplicated or re-queued tick never runs it twice in a day, and a tick not picked up within an hour is dropped: the next day's run removes everything that is due. Each run writes the run log, one `retention_run` audit entry.

`RETENTION_ENABLED` decides what the run deletes. It is off unless set to `1`. With it off, the 12 rules behind it touch nothing and are named under `switched_off` in the entry; the four SMS rules (`sms_messages`, `mobile_challenges`, `inbox_messages`, `bulk_batches`) are not behind it and run as before. `pack_registration_details` has its own switch, below. The switch is read by the process that runs the task, so it must be set on `worker` as well as `api` before a scheduled run deletes anything (the variable is only read by name; see `docs/runbooks/billing.md`, "Worker and beat").

Where there is no beat, the phone's `poll` still starts the four SMS rules once a Manila day (`start_daily`); the other rules then run only by hand. Where both exist, the second run of the day finds nothing due.

The manual commands below stay as they are: `manage.py run_retention` for a count first or a run on demand, `dispose_account` for one account. The export (`manage.py export_account`) changes nothing and does not need the switch.

### The date-of-birth erase switch

`DOB_ERASE_ENABLED` (on only when set to `1`, off by default) is the switch of the rule `pack_registration_details` alone: the erase of the date of birth and the two Santé usernames of a Membership Pack registration (Coach Ed's ruling D3: the date-of-birth erase never depends on the Subscriber's subscription, so it has its own switch). The rule no longer answers to `RETENTION_ENABLED`: `RETENTION_ENABLED` on with `DOB_ERASE_ENABLED` off erases nothing, and `DOB_ERASE_ENABLED` on with `RETENTION_ENABLED` off erases. It also needs `PACK_ENABLED`; with that off the rule reads no pack table. With its switch off the rule is named under `switched_off` in the run log, and its dry run still counts. Set it on `worker` for the nightly run and on `api` for runs by hand. It needs Coach Ed's approval, each time and per environment, like `RETENTION_ENABLED`.

## What needs Coach Ed's approval, each time and per environment

- Setting `DOB_ERASE_ENABLED=1` (the date-of-birth erase, ruling D3), on `worker` and on `api`.
- Setting `PACK_PURGE_ENABLED=1` (the day-90 buyer purge) or `PACK_BUYER_NOTICE_ENABLED=1` (the day-76 buyer email), each on `worker` and `api`. See "The Membership Pack buyer purge and the day-76 notice" below.
- Setting `RETENTION_ENABLED=1`. From then on the daily `retention.run` deletes with it wherever `worker` has the switch; run the dry run first.
- Running any command below against staging or production data. This includes the dry run and the export.

## Before any command here

Migration `accounts.0003` (the mobile number tables) must be applied in an environment before any retention, disposal or export command runs there. Disposal and export read those tables directly, whether or not texts are switched on, so that a number is never left behind; without the tables the command fails and changes nothing.

## The rules

| Rule | Deletes or blanks | When |
|---|---|---|
| `expired_subscriber_sessions` | Subscriber sessions | 30 days after they expired or were revoked |
| `expired_admin_sign_in_codes` | Emailed hq sign-in codes | 30 days after they expired |
| `expired_admin_sessions` | hq sessions | 30 days after they expired or were revoked |
| `expired_django_sessions` | Django's session rows (never a sign-in) | 30 days after they expired |
| `email_messages` | Email send records | 2 years after they were written |
| `admin_audit_personal_values` | Deletes nothing: it blanks the personal values of admin sign-in audit entries (signed in, failed, locked, signed out) and of the SMS inbox's `sms_inbox_open`, `sms_inbox_reply` and `sms_inbox_compose` entries (the masked number of the conversation that was opened, or that the text was sent to), and stamps each row with the time. The row and everything else on it stay (ruling D4) | 1 year after the entry was written |
| `never_verified_accounts` | Nothing: it disposes of accounts whose email was never verified, as in "What disposal does" below | 30 days after sign-up |
| `expired_member_sessions` | Santé member sessions, where `MEMBERS_ENABLED` is on | 30 days after they expired or were revoked. Does nothing, and reads no member table, while `MEMBERS_ENABLED` is off |
| `expired_member_challenges` | Santé member sign-in code rows (they hold the contact a code went to), where `MEMBERS_ENABLED` is on | 30 days after they expired. Does nothing, and reads no member table, while `MEMBERS_ENABLED` is off |
| `account_audit_events` | An account's audit events (sign-ins, sign-outs of other devices, linked sign-in providers, email and mobile changes, and the account and site history events) | 1 year after they were written. An `email_changed` event is kept 2 years: see "Security records" below |
| `inbox_messages` | SMS inbox messages, received and sent, then any conversation left empty | 365 days after the message was stored. Not behind `RETENTION_ENABLED` and not behind `SMS_INBOX_ENABLED`: it runs wherever the inbox tables exist and does nothing where they do not. Started daily by the phone's `poll`, beside `sms_messages` (`docs/runbooks/sms-gateway.md`), and included in `manage.py run_retention` |
| `bulk_batches` | Bulk SMS batches (the text and counts), with any numbers still attached | 365 days after the batch was confirmed. Not behind `SMS_BULK_ENABLED`: it runs wherever the table exists and does nothing where it does not. Started daily by the phone's `poll` and included in `manage.py run_retention` |
| `inquiry_replies` | Reply audit rows of the customer inquiries inbox (a length and a code, no text) | 365 days after they were written. Behind `RETENTION_ENABLED` and `INQUIRIES_ENABLED` |
| `inquiry_messages` | Website chat messages, and the conversation's personal fields (`display_name`, `contact_name`, `contact_email`, `visitor_hash`, `thread_token`). A conversation no reply audit row points at is deleted; one that is pointed at is blanked and closed, and goes when its audit rows do | 180 days after the conversation's last message (`MESSAGE_RETENTION` in `apps/inquiries/retention.py`, a code value only: the Privacy wording for it, D1, is not yet approved). Behind `RETENTION_ENABLED` and `INQUIRIES_ENABLED`, and does nothing before `inquiries.0002` is applied. The dry run count equals the run count: messages plus conversations dealt with |
| Do-not-text list (`inbox_donottext`) | Nothing: no job and no command removes it | Kept until the person asks otherwise. A request to remove a number is an NPC and privacy decision, not built. `sms_inbox_delete` leaves it |
| Contacts and groups (`inbox_contact`, `inbox_contactgroup`) | Nothing: no job deletes them | Kept until HQ staff delete them. Not deleted by any job and not in the account export. A person's request is carried out with `sms_inbox_delete` (`docs/runbooks/sms-gateway.md`) |

Inquiry messages are not in the account export: they are the customer's words, not the Subscriber's data. A disposal or a site erasure deletes every `InquiryMessage` of the site, blanks the conversations' personal fields (including the visitor secret hash, so no conversation can be continued) and the site's chat settings (switches off, pause cleared, `email_route_token` blanked), through `blank_inquiries()`.

No rule touches a billing or tax record or a recovery code. The SMS send records have their own rules, in `docs/runbooks/sms-gateway.md`. No rule deletes an entry of the admin audit log: `admin_audit_personal_values` only blanks the personal values of the admin sign-in entries and of the `sms_inbox_open`, `sms_inbox_reply` and `sms_inbox_compose` entries, and every other entry is kept whole. An account's audit events are a different record, and `account_audit_events` deletes them. Only `never_verified_accounts` touches an account or a site.

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

The daily run's own log lines, on the service logs: `beat` shows `Scheduler: Sending due task retention-daily (retention.run)` at 03:00; `worker` shows `Task retention.run[...] received` and `succeeded ...` with the counts per rule, the `switched_off` names and any failed rule's name (never a personal value). `already ran today` means the day's lock was taken.

## What the output and the log hold

Rule names and counts only. Never an email address, a name or a number. Each real run writes one audit entry, action `retention_run`, with no operator and the count per rule, the rules held back by the switch, and the name of any rule that failed. A failed rule keeps none of its own changes and does not stop the others; the command then ends with an error.

The count of `admin_audit_personal_values` in that entry is the log of the blanking, and each blanked row carries the time it was blanked. A second run counts zero for rows already blanked.

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
| Santé site settings, where `SANTE_SETTINGS_ENABLED` is on | The sponsor ID | The row |
| Site payment accounts, where `PAYMENT_METHODS_ENABLED` is on | The account name and account number; every account is marked removed; each account's QR code file is deleted from the private store | The row, its kind, bank or e-wallet name and type, dates |
| Email send records to the account's address | Deleted, except hq sign-in codes and lockout notices | The records of the three payment emails (`billing_proof_received`, `billing_payment_approved`, `billing_payment_rejected`): billing records, kept 5 years (Privacy, section 8), then deleted by `email_messages` |
| Email send records to an address the account used before an email change | Deleted when written up to the moment of that change, plus the change notice sent to the old address; hq sign-in codes, lockout notices and the three payment-email records stay. Later records to that address are not touched: it may belong to another account by then | |

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

**Subscriptions.** Where `BILLING_ENABLED` is on, a disposal cancels the account's subscriptions first (`billing.services.prepare_disposal`; `docs/runbooks/billing.md`):

- On trial, needing payment attention or suspended: the subscription is cancelled, service ends at once, and the disposal goes on. A suspended one keeps the dates its suspension recorded. The counts gain `subscriptions_cancelled`.
- Inside a paid term: the subscription is cancelled to the end of that term, and the account is **not** disposed of. The command prints the cancellation, names the site and the date (`paid_term_running`), exits with an error and blanks nothing. The cancellation is kept. Run the command again after that date.
- Already ended, exempt from billing, or no subscription: nothing is cancelled and the disposal goes on.

The dry run cancels nothing. It prints `subscriptions_cancelled` and, when a real run would be refused, "A real run would be refused for now" with the site and the date.

No billing row is deleted or blanked by a disposal: a subscription, its cycles and its transitions hold the site, states and dates only, and stay.

What the billing step does not know yet, so check by hand before disposing: nothing can be owed until Billing Build B2 takes payments, so there is no final bill to wait for today (ruling R-3). B2 adds the refusal while a payment is open or held for review and the hold of the name and billing email until a final bill is settled; later builds add unpaid final usage and the wait for a delivered statement.

**Santé settings.** Where `SANTE_SETTINGS_ENABLED` is on (ClickUp z8uq9m92ez, port slice 1):

- An account that owns the Santé catalog owner site is **not** disposed of. This is checked before the subscriptions, so nothing is cancelled and nothing is blanked. The command names the site (`catalog_owner`) and exits with an error; the dry run prints "A real run would be refused for now". An operator first names another catalog owner site, or clears it, on the HQ Settings page; then run the command again.
- Otherwise the sponsor ID of each of the account's sites is blanked and the row is kept. The counts gain `sante_site_settings`, and the audit entry names the field, never the value.
- The export gains `sante_site_settings`: per Santé site its public id, the sponsor ID, when it was last changed and whether it is the catalog owner.

While `SANTE_SETTINGS_ENABLED` is off, disposal and export do not read the Santé tables at all, because the code is released before migration `sante.0001` is applied. So turn it on in an environment only after that migration is applied there. Once it has been on in an environment, it is not turned off again without Coach Ed's ruling: a disposal run while it is off would leave a saved sponsor ID in place and would not refuse the catalog owner's account.

**Shared Santé catalog.** Where `SANTE_CATALOG_ENABLED` is on (ClickUp z8uq9m92f1, port slice 2):

- The catalog belongs to no account. A disposal deletes and blanks nothing in it: every product and every revision stays as it was, including the revisions written from a disposed account's site. A revision names its writer by the account's and the site's row number only; it holds no name, email, number or address.
- The account that owns the catalog owner site is still refused, as above, until another owner site is named. The products then stay with the catalog and the new owner site edits them.
- The export holds no catalog row: products and revisions are not an account's data.
- A product's category and its image (a file in the public media store, named by a random key) belong to the catalog, not to an account: disposal leaves them, and a revision names a writer by row number only.

While `SANTE_CATALOG_ENABLED` is off the catalog tables are not read at all, because the code is released before migration `catalog.0001` is applied. Turn it on in an environment only after that migration is applied there and `SANTE_SETTINGS_ENABLED` is on.

**Payment methods.** Where `PAYMENT_METHODS_ENABLED` is on (ClickUp z8uq9me4dy, port slice 7):

- The account name and account number of every bank and e-wallet account of the account's sites are blanked, each account is marked removed, and the rows are kept. The counts gain `site_payment_accounts` and `site_payment_qr_files`, and the audit entry names the fields (name, number, QR file key), never a value.
- Each account's QR code image (ClickUp z8uq9me7aq) is a private file. Disposal deletes it from the store, removed accounts' files too, and blanks the key. A file that cannot be deleted stops the disposal and undoes it; run it again once the store answers. A file already gone is not an error. A removed account (the owner's Remove) keeps its file until disposal, as it keeps its row.
- The payment settings row (direct transfer on or off) holds nothing personal and is kept as it is.
- The export gains `site_payment_accounts` (every account of the account's sites, removed ones too, with the full name and number, and whether it has a QR code, never the file's key) and `site_payment_settings`.
- No Xendit key is stored (Xendit is hidden by Coach Ed's ruling of 2026-11-11), so there is none to blank or export.

While `PAYMENT_METHODS_ENABLED` is off, disposal and export do not read the payment tables at all, because the code is released before migration `payments.0001` is applied. Turn it on in an environment only after that migration is applied there. Once it has been on in an environment, it is not turned off again without Coach Ed's ruling: a disposal run while it is off would leave saved account names and numbers in place.

**Santé members.** Where `MEMBERS_ENABLED` is on (ClickUp z8uq9me7x8, Membership Package slice PK2):

- A member belongs to a site, so an account disposal reaches the members of the account's sites. Every member session and every sign-in code row (challenge) of those sites is deleted. Each member's name and delivery address are blanked, and the email and mobile are replaced by values nobody can use (`deleted-member-<n>@deleted.invalid`, and a `+63900` number that is no handset's). Both contacts go back to pending. The row and its public id stay, because an order will point at its member. The counts gain `members`, `member_sessions` and `member_otp_challenges`, and the audit entry names the fields (name, mobile, email, delivery address), never a value. The contact audit, recovery checks and security holds keep only masked values and stay.
- The replaced contacts are free again, so the real owner of an address can be registered on another store.
- The export gains `members` (every member of the account's sites with name, contacts, delivery address, verification dates and source), `member_sessions` (dates and revoke reason only) and `member_contact_audits`. It never holds a token hash or a visitor address.
- The members are the Subscriber's buyers. Decided by Coach Ed on 2026-11-13 (Manager session, relayed by the Manager; recorded on ClickUp z8uq9memg6): a Subscriber's own export includes its Santé members (members, member_sessions, member_contact_audits), as built. Members belong to the site, so disposal and export know them.

- **Order log** (ClickUp z8uq9meze0): a member's own notebook of Santé app orders (`member_order_logs`: order number, date, customer first name, city, payment, total, status). The rows name a third party, so disposal, the site erasure and `pack_remove_test_member` delete them (the count is `member_order_logs`, shown in the dry run) and the Subscriber's export lists them.

While `MEMBERS_ENABLED` is off, disposal and export do not read the member tables at all, because the code is released before migration `members.0001` is applied. Turn it on in an environment only after that migration is applied there. Once it has been on, it is not turned off again without Coach Ed's ruling: a disposal run while it is off would leave members' names and contacts in place.

**Membership Pack orders.** Where `PACK_ENABLED` is on (ClickUp z8uq9me9mr, Membership Package slice PK4):

- An order is never deleted and the Subscriber's books must still add up, so a disposal keeps the order rows, their numbers, status, frozen price, events and the agreement and enrollment records. What identifies a person goes: the buyer name and delivery address on each order, the sender name, sender account number, reference number and visitor address on each proof, every proof file in the private store, and every checkout token with its typed contact. Counts gain `pack_orders`, `pack_proof_submissions`, `pack_proof_files` and `pack_checkout_tokens`; the audit entry names fields, never values. A file that cannot be deleted undoes the disposal so it can be run again.
- Shipment records (ClickUp z8uq9mek5x): the courier, tracking number, shipped and delivered dates and the Subscriber's actor email label on an order are kept by a disposal, like the order's status and events. A tracking number is not personal data on its own; the buyer name and delivery address are still blanked.
- Rule `expired_checkout_tokens`: a token is deleted 30 days after it expired.
- Rule `pack_registration_details` (as TWCinc's sweep; its own switch is `DOB_ERASE_ENABLED`, not `RETENTION_ENABLED`, and it needs `PACK_ENABLED`): the date of birth and the two Santé usernames of a registration are erased 90 days after its last status change, whatever its status. A registration still in progress takes edge T24 to needs_buyer_action; one waiting on the customer loses its confirmation, so the buyer enters the details again. The registration row stays. A disposal blanks the same three fields (count `sante_registrations`), and the export lists registrations.
- The export gains `pack_orders` and `pack_proof_submissions` as plain values, with no file key, no token and no row number. The proof images and their checksums are not in `export_account`: they are in the Pack archive of `export_pack_files` ("The Pack files of an ended site", below).
- Same shape as TWCinc and as billing's transfer proofs (files deleted, rows and money kept). While `PACK_ENABLED` is off, disposal and export read no pack table, because the code is released before migration `pack.0001` is applied.

SMS send records are not touched by a disposal: they hold the number a text went to and follow the SMS build's own 1-year rule.

## An export request

The export holds the account (email, name, dates, referral code), its policy acceptances, linked sign-in providers, sessions (device, masked IP, dates), its verified mobile number and mobile verification challenges (never the code), account audit events, the send records for its address, and each site with its web addresses, business profile and onboarding dates. It never holds a session token, a provider's user id, or anything of another account. It writes one audit entry, action `account_exported`, with counts only.

The service's disk is lost on every restart, so print the JSON and save it on your own computer through the ssh call:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py export_account ACCOUNT --actor USERNAME --request-ref REF --stdout > account-export.json
```

`--out-dir DIR` writes `account-export-<public id>-<time>.json` in an existing folder instead, readable by its owner only; it never replaces an existing file.

The file is personal data on your computer. Send it to the requester from the DPO mailbox, only after confirming who is asking, then delete it. Sending is done by hand; nothing here sends it.

How a request arrives, its reference number and the support periods: `docs/runbooks/support-and-exit.md`.

## The Pack files of an ended site

For a Subscriber who sold Membership Packs, the store data includes the buyers' payment-proof images. `export_account` carries no image. `manage.py export_pack_files` builds one ZIP per site: `manifest.json` (every file with its size and sha256, the window, counts, warnings), `pack.json` (orders, proofs with sender and paid-to details, decisions, order events, agreement and enrollment records, registrations) and `proofs/<order number>-<seq>.<jpg|png|webp>`. It never holds a file key, a checkout token, a hash other than sha256, a visitor address, a row number, a staff reference or a full account number, and nothing of another site or account.

It runs only inside the 30 days after the service ended, and never at or after the deletion point (a site that never paid has its deletion point exactly at the end of the 30 days). Outside that it refuses and writes nothing; so does a served, trial, exempt or reactivated site. It also needs `PACK_ENABLED`, an active staff `--actor`, a request reference number (never an address), and a site that belongs to the account. It reads the private store once per image; if any image cannot be read it fails with no output and no audit entry, so run it again. An archive over 200 MiB is refused (no parts option is built; tell the Supervisor).

Run it after `export_account`, for each site that sold Packs. The disk is lost on restart, so print it as base64 and decode on your own computer:

```bash
railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py export_pack_files ACCOUNT --site SITE --actor USERNAME --request-ref REF --stdout-base64 > pack-export.txt
tr -d '\r' < pack-export.txt | base64 -d > pack-export.zip
shasum -a 256 pack-export.zip
```

The command prints a line to the error output with the counts and the archive sha256; the last command must show the same value. `--out-dir DIR` writes `pack-export-<site>-<REF>.zip` (0600, never replaces a file; a repeat of the same site and reference refuses and writes no second audit entry). Each successful hand-over writes one audit entry, `pack_files_exported`, with counts, bytes, the archive sha256 and the basis.

**A person's own request on a site still served** (`--basis request`): an access request, or a deletion request on a live site. It skips the window check and nothing else. Run it, send the archive, and only then run `dispose_account` for any account that sold Packs: `dispose_account` deletes the proof files of the account's sites without an export. The site must have Pack orders, and the audit entry records `"basis": "request"`.

Send the archive to the registered email only, from the DPO mailbox, then delete your copy. Same staging and production rule as every command here: Coach Ed's word each time.

## Billing statements and the invoice register

No rule touches the `statements_*` or `invoices_*` tables (Billing Build B5). They are billing and tax records kept intact for at least five years (L76), and disposal of an account keeps the statement snapshots.

## The Membership Pack buyer purge and the day-76 notice

ClickUp z8uq9mep8p (buyer portal z8uq9mbk0m, plan PR 3). Coach Ed approved the scope and the email text as written on 2026-11-13 ("Approved b c d and d as recommended", blocks B and C). Both jobs are Celery tasks in `apps/pack/tasks.py`, queued hourly by their own beat entries (`pack-purge`, `pack-buyer-notice`), and both are **off in every environment** until Coach Ed says otherwise, per environment.

| Switch | Default | Does |
|---|---|---|
| `PACK_PURGE_ENABLED` | off | The day-90 purge (task `pack.purge`). Needs `PACK_ENABLED`; `check --deploy` reports `pack.E004` otherwise. Off: the task reads nothing and answers `{"skipped": "off"}`. Also adds the deletion sentence to the portal banner. |
| `PACK_BUYER_NOTICE_ENABLED` | off | The day-76 email (task `pack.buyer_notice`). It is never sent unless `PACK_PURGE_ENABLED` is on too (`pack.E005`). Off: nothing is read or sent. |

Set them on `worker` (the process that runs the tasks) and on `api` (the banner). They are read by name only; see `docs/runbooks/billing.md`, "Worker and beat".

### The purge

At `billing.deletion_point(site)`, for every site whose subscription has ended and whose stored deletion date has passed, **whether the site is served or not and a removed site included**, one transaction per site under the site's row lock clears:

| Table | Cleared |
|---|---|
| `SanteRegistration` | date of birth, both usernames; `details_purged_at` set. Status and row stay |
| `SanteRegistrationEvent` | the note, and the actor label of a member's event (append-only table: the one sanctioned bypass, as account disposal) |
| `PackOrder` | `buyer_name` becomes `Deleted buyer`, `delivery_address` becomes `{}`. Money, number, status and shipment stay |
| `Member` | `delivery_address`. Name, mobile and email stay until the store-data deletion executor exists |
| `CheckoutToken` | every row of the site |
| `MemberSession`, `MemberContactAudit` | the visitor address |
| `PackProofSubmission` | sender name, sender account number, sender bank, visitor address, reference number, the account snapshot (public id, name, last four), and the transfer time (set to the submission time). Amount, seq, kind and bank name stay |

**Proof files.** A file is deleted only while the constant `PROOF_EXPORT_READY_FROM` (`apps/pack/purge.py`) is a date not later than the day the site's service ended, so the export held images for its whole 30-day window. It is `date(2026, 11, 14)`, the Manila day after the staging deploy of the Pack archive (`export_pack_files`, z8uq9mbu0p) was verified, so every site whose service ends on or after that day had the export for its whole window and its proof files may be deleted (the personal columns above clear as before). `None` would close the gate again. A site whose service ended before that day keeps its images (Finance-only, no route, no automatic deletion; L72). The constant deletes nothing by itself: deletion also needs `PACK_PURGE_ENABLED` or an approved executor batch. A store that is down on a deletion leaves the key and the next hourly pass retries it.

**Not covered by the purge.** The Subscriber's receiving accounts: the site erasure below covers them (it calls the per-site `blank_payments`). The purge honours an open `ErasureHold` (a held site is left alone) only while `SITE_ERASURE_ENABLED` is on. The Finance resolver, admin gates and anything Xendit are deferred (decision B2). The date-of-birth erase `pack_registration_details` (D3, its own switch `DOB_ERASE_ENABLED`) is unchanged and runs on its own age clock.

**Behaviour.** Idempotent: a second pass changes nothing, and rows made after a restore are cleared by the next pass. A reactivated site has no deletion date, so nothing runs for it. A payment in flight does not delay it. A site that fails is logged as `Pack purge failed for site <pk> (<exception class>)` and the loop goes on. From the instant of the point every registration action (details, delivery, the T8 move) is refused, whether or not the pass has run.

**Hold.** To hold one site for a legal or fraud reason, set an erasure hold on it (`manage.py erasure_hold SITE --actor USER --reason-code CODE`, or the HQ route `lifecycle/erasure/hold/`); the purge then skips it while `SITE_ERASURE_ENABLED` is on. To hold every site, turn `PACK_PURGE_ENABLED` off. Turn it on again when the hold ends; the next hourly pass clears what is due.

### The notice

14 x 24 hours before the deletion point, each member with a verified order on the site gets one email (category `pack_buyer_notice`): subject "Your {store name} order view closes on {date} at {time}.", and the body of Coach Ed's block C, word for word. The date and time are Manila time and equal the deletion point. Once per (site, member, deletion date): table `PackBuyerNotice` (migration `pack.0005_buyer_notice`). It also holds the number of tries, the last try, and when it was given up, never an address or the text.

- A failed send is tried again every 6 hours, at most 6 retries (7 tries in all), and only while the point has not been reached.
- Before each send the subscription is read again; the email goes only while the service has ended and the point is still the recorded one. A reactivated site sends nothing.
- A window missed (an outage past the point) sends nothing. A notice given up (point passed, retries used up, or a disposed address) is logged once: `Pack buyer notice given up (<reason>): site <pk>, member <pk>.` There is no alert table; watch the worker log for that line.
- No text message.

## An ended site's data (the deletion executor)

Plan: `docs/superpowers/plans/2026-11-13-site-deletion-executor-z8uq9mb22v.md` (ClickUp z8uq9mb22v). Code: `apps/lifecycle`. It anonymises in place the store data of a site whose `deletion_due_at` has passed, only after a person approved a frozen list. It ships dark: `SITE_ERASURE_ENABLED` is unset everywhere, and turning it on in an environment needs Coach Ed's word naming that environment. Production is never covered by an earlier approval. No rehearsal runs on staging data with real members; rehearse on a throwaway local database, and make the first run on staging a batch of one site that Coach Ed names after reading its dry run.

**What it erases, per site** (rows stay, personal fields blank; the feature switches `PAYMENT_METHODS_ENABLED`, `MEMBERS_ENABLED`, `SHOP_ENABLED`, `SANTE_SETTINGS_ENABLED` do not matter): the site's contact fields and business profile (seal file deleted), Santé sponsor id, receiving accounts (name, number, QR file deleted), members with their sessions and sign-in codes, Pack buyers (fields only; **pack proof files go** only for a site whose service ended on or after `PROOF_EXPORT_READY_FROM`, 2026-11-14), Santé registrations, shop orders and proofs (files deleted, `account_number_last4` blank), Meta settings and order contexts, and the site-owned SMS inbox (conversations, messages, contacts, groups; the do-not-text list stays). When no other live site remains the account is disposed of too (rule `site_erasure`; needs `RETENTION_ENABLED`; its pack proof files stay unless every site of the account ended on or after the ready day). Kept: every billing, statement, invoice, ledger and audit row.

**Procedure** (all need `SITE_ERASURE_ENABLED`; `erasure_dry_run` works without it and writes nothing):

1. `manage.py erasure_dry_run` (or HQ `lifecycle/erasure/list/`): eligible, near and refused sites with counts. Check the support mailbox for an open export or deletion request about any listed site; hold or exclude it.
2. `manage.py erasure_propose SITE [SITE ...] --actor USER` (1 to 25 sites; expires after 7 days). Deletes nothing.
3. `manage.py erasure_approve BATCH --actor USER --reason TEXT --confirm "erase batch BATCH N sites" [--exclude SITE]`. `--cancel` cancels instead. The reason is a note: no name, email or number.
4. `manage.py erasure_run BATCH`, or wait for the Celery task `lifecycle.run_approved` (every 5 minutes, approved batches only). Each item runs in its own transaction and re-checks eligibility under the locks; an item that no longer qualifies is `skipped` and nothing is erased. A failed item (for example `store_unavailable`) is retried up to 5 times, then the batch shows `needs_attention`.
5. Read the audit entries `site_data_erased` and `site_erasure_skipped` (counts and field names only).

**Holds.** `manage.py erasure_hold SITE --actor USER --reason-code CODE --reference REF`, `--release` to lift it. A held site is skipped by the executor and by the Pack purge.

**Restore.** Erasure cannot be undone from the application. Backups keep erased data for up to about 6 days (staging Postgres uses the Daily volume backup only, kept 6 days; Coach Ed, 2026-11-14). The private file backup is not in use (declined for now on cost, 2026-11-14). When it is turned on (`docs/runbooks/backups-and-restore.md`, section 7), the copy of a deleted private file is kept 35 days after the file is seen deleted, then removed; an urgent erasure can remove it sooner with `backup_private_files --purge-prefix`. After a restore, erase again the sites listed in the `site_data_erased` and `account_disposed` records kept outside the database (support mailbox, PR and ClickUp record of each batch).

## Emptying one TEST order (staging only)

`manage.py shop_remove_test_order --site SITE --order ORDER [--actor USER]` is a dry run that prints counts and writes nothing; read it first, then add `--apply`. SITE is the site's public id, ORDER the order's public id or number (`SO00001`). It refuses unless the receiver name starts with "TEST " and the order is on that site. It blanks the order's receiver and contact fields and its proof's personal fields, deletes the proof file and the order's reviews, and keeps the order, lines, totals and events. One audit entry `shop_test_order_removed`. Run again, it changes nothing. Never on production; the real run waits for Coach Ed's word after the dry-run counts. (ClickUp z8uq9mf15g; same style as `pack_remove_test_member`.)

## Turning it off

Unset `RETENTION_ENABLED`. Deleted rows cannot be brought back from the application; only a database backup holds them, until that backup expires.
