# Database Migrations Runbook

Migrations are an explicit, reviewed release step (see `CLAUDE.md`). They never run automatically on deploy, and they do not run from CI.

## Why not CI

On 2026-11-10 we tested a Railway project token scoped to the staging environment. The token was valid (`railway status` resolved the project and environment), but Railway rejected it for `railway ssh` with "Unauthorized". Running migrations from CI would need an account-level credential that can reach every project in the workspace, so we decided against it. Migrations are run by Coach Ed, or by an agent session after Coach Ed approves that specific run.

## Identifiers (not secrets)

| Item | Value |
|---|---|
| Project | `negosyoonline-backend` (`f94bedb8-86c5-4c66-8ee5-5d9ce9dad172`) |
| Environments | `staging`, `production` |
| Service | `api` |

Access uses your logged-in Railway account and the SSH key registered to it. No token is stored anywhere.

## Preconditions

- The migration file is in a merged pull request. CI already fails when a model change has no migration (`makemigrations --check`).
- The code that expects the new schema is deployed to the target environment, or the migration is backward compatible with the code that is running (add first, remove in a later release).
- Coach Ed has approved running it against this environment.

## Procedure

Replace `<env>` with `staging` or `production`.

1. Show what would run. This changes nothing:

   ```bash
   railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py migrate --plan
   ```

2. Read the plan. Stop if it lists anything you did not review.
3. Apply:

   ```bash
   railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py migrate --noinput
   ```

4. Confirm nothing is pending. Expected output: `No planned migration operations.`

   ```bash
   railway ssh -p f94bedb8-86c5-4c66-8ee5-5d9ce9dad172 -e <env> -s api -- uv run python manage.py migrate --plan
   ```

5. Verify the environment:

   ```bash
   scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/
   ```

6. Record the run (date, environment, commit, plan output summary) in the pull request that introduced the migration.

## Notes

- Migrations do not roll back automatically. Keep each release backward compatible with the previous one.
- Production has not been migrated and has no verified health URL yet. Do not run this against `production` until that environment is set up and Coach Ed approves.

## Pending

- `production` has not been migrated at all (see Notes).
- `inbox.0003_bulk` (Bulk SMS: three new tables and `InboxAccess.can_bulk`) is written in the plan 8 build PR and applied nowhere. The coordinator runs it on staging after the merge; the History line is added after that run.
- `inquiries.0002_stored_channels` (website chat: `InquiryMessage` and `InquiryChannelSettings`, new `InquiryConversation` fields and constraints, `InquiryReply.message` and the `delivered`/`bounced` states; additive, no data change) was written in the G3 PR1 build PR and applied on `staging` on 2026-11-15 (see History); `production`: not run. Where it is not applied: the chat routes and the owner's stored-channel routes (channels, close, reopen, read) answer 404, the owner list is empty, disposal skips the new tables and the message retention rule does nothing. Nothing of the chat works, and the chat switch must stay off, until it is applied. After it is applied the new `NOT NULL` columns keep database defaults (`db_default`), so a deploy that lags or a rollback of the code still inserts rows.
- `inquiries.0003_remove_pancake` (Remove Pancake, task z8uq9mg1n9, Coach Ed's ruling of 2026-11-17) is applied on `staging` (see History). It needs `0002` applied first. A data step deletes every `InquiryConnection` row (all were Pancake connections, with their encrypted tokens), every `InquiryConversation` whose provider is `pancake`, and those conversations' `InquiryReply` and `InquiryMessage` rows; stored-channel rows (website chat, email) are not touched. Two `AlterField` steps then drop the `pancake` default and choices (the `InquiryConnection` table stays, with no provider choices until Messenger adds one). The data step is not reversible. Before it runs on staging, a dry run shows the counts (`InquiryConnection`, Pancake `InquiryConversation`, their `InquiryReply` rows) and they go to the Manager (comment 1100430000547238). The frontend removal ships in the same round pair. `production`: not run.
- `inquiries.0004_messenger` (the direct Messenger inbox, task z8uq9mg1qd) is applied on `staging` (see History). It needs `0003` applied first. Additive: the `messenger` provider choice, `InquiryConversation.psid` and `page_id`, `InquiryConnection.meta_user_id`, `InquiryMessage.attachment_types`, the `token_invalid` reply code, and two partial unique constraints (one Messenger Page per site; one conversation per connection and customer id). **Run it immediately after the api deploy that contains it.** `stored_tables_exist()` (`apps/inquiries/switch.py`) checks for these new columns, so until `0004` is applied website chat and the contact inbox answer as if their tables were missing, even with the Messenger switch off. `production`: not run.
- `sites.0004_sitecustomhost` (task z8uq9mg8gw, Live4More custom domain) is written and applied nowhere. One new table, additive, no data step; run it only after Coach Ed's separate approval (see `docs/runbooks/custom-domain-live4more.md`). `production`: not run.

## History

- 2026-11-10: the 18 built-in Django migrations (`contenttypes`, `auth`, `admin`, `sessions`) were applied to `staging`. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
- 2026-11-10: the built-in migrations were unapplied on `staging` and re-applied together with `operators.0001_initial`, because the custom user model (`operators.User`) must exist before `auth` and `admin` are migrated. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
- 2026-11-10: `accounts.0001_initial` and `notifications.0001_initial` were applied to `staging`. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
- 2026-11-10: `accounts.0002_externalidentity` was applied to `staging`. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
- 2026-11-10: `sites.0001_initial` was applied to `staging` by Coach Ed from his own terminal, at commit `23c9e32`. The plan read beforehand listed only that migration. Afterwards `migrate --plan` reported no pending operations.
- 2026-11-11: `notifications.0002_sms_gateway` was applied to `staging` by the coordinator session after Coach Ed approved that run, at commit `91aa0a8`. The plan read beforehand listed only that migration. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `SMS_GATEWAY_ENABLED` was not set.
- 2026-11-11: `accounts.0003_accountmobile_mobileotpchallenge` was applied to `staging` by the coordinator session after Coach Ed approved that run, at commit `100c66f`. The plan read beforehand listed only that migration. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `SMS_GATEWAY_ENABLED` was not set.
- 2026-11-11: `notifications.0003_sms_daily_limits_start_at_2000` was applied to `staging` by the coordinator session after Coach Ed approved that run ("Yes, run it"). It changes the starting value of the two SMS daily limits from 4,000 to 2,000 for a row created later; on PostgreSQL it runs no SQL (`sqlmigrate` prints `-- (no-op)` for both fields) and only records itself as applied. `production`: not run, not approved.
- 2026-11-11: `billing.0001_initial` was applied to `staging` by the coordinator session after Coach Ed approved that run ("Yes, run it"), at commit `ccd19da`. The plan read beforehand listed only that migration (three models, four constraints). Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `BILLING_ENABLED` was not set and no backfill was run. `production`: not run.
- 2026-11-11: `sante.0001_initial` was applied to `staging` by the coordinator session under Coach Ed's standing approval for a migration the approved plan specifies, at commit `f1c868b`. The plan read beforehand listed only that migration (two models). Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `SANTE_SETTINGS_ENABLED` was not set. `production`: not run.
- 2026-11-11: `catalog.0001_initial` was applied to `staging` by the coordinator session under Coach Ed's standing approval for a migration the approved plan specifies, at commit `f23c83a`. The plan read beforehand listed only that migration (three models, eleven constraints). Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `SANTE_CATALOG_ENABLED` was not set. `production`: not run.
- 2026-11-11: `billing.0002_bank_transfer` was applied to `staging` by the coordinator session under Coach Ed's standing approval for a migration the approved plan specifies, at commit `9a90649`. The plan read beforehand listed only that migration (three models, two constraints). Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `BILLING_BANK_TRANSFER_ENABLED` was not set. `production`: not run.
- 2026-11-11: `inbox.0001_initial` was applied to `staging` at commit `b6d37a4`. The plan read beforehand listed only that migration. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
- 2026-11-11: `inbox.0002_contacts` was applied to `staging` by the coordinator session under Coach Ed's standing approval for a migration the approved plan specifies, at commit `f224ade`. The plan read beforehand listed only that migration (two models). Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `SMS_CONTACTS_ENABLED` was not set. `production`: not run.
- 2026-11-11: `billing.0003_plan_snapshot` and `billing.0004_plan_snapshot_backfill` were applied to `staging` by the coordinator session after Coach Ed approved that run ("Yes, run it"; 0004 backfills existing rows), at commit `1fdbce6`. The plan read beforehand listed only those two (six added fields, one model, one data step). Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. No billing switch was set. `production`: not run.
- 2026-11-11: `billing.0005_paid_cycle_has_snapshot` was applied to `staging` by the coordinator session under Coach Ed's standing approval for a migration the approved plan specifies, at commit `34b0d10`. The plan read beforehand listed only that migration (one check constraint, no data change). Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. No billing switch was set. `production`: not run.

- 2026-11-12: `billing.0006_usage_ledger` was applied to `staging` by the coordinator session under Coach Ed's standing approval for a migration the approved plan specifies, at commit `5fe8d25`. The plan read beforehand listed only that migration (three new tables, five constraints, no data change). Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `BILLING_USAGE_ENABLED` was not set. `production`: not run.
- 2026-11-12: `billing.0007_transfer_usage` was applied to `staging` by the coordinator session under Coach Ed's standing approval for a migration the approved plan specifies, at commit `abf4c2d`. The plan read beforehand listed only that migration (two columns on `BankTransferPayment` and the replaced `billing_transfer_confirmed_in_full` constraint). Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `BILLING_USAGE_ENABLED` was not set. `production`: not run, not approved.
- 2026-11-12: `statements.0001_initial`, `invoices.0001_initial` and `statements.0002_immutability_triggers` were applied to `staging` by the coordinator session under Coach Ed's standing approval for migrations the approved plan specifies, at commit `4626c9c`. The plan read beforehand listed exactly those three. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `BILLING_INVOICE_REGISTER_ENABLED`, `BILLING_STATEMENTS_ENABLED` and `BILLING_LIVE_DOCUMENTS` were not set.
- 2026-11-13: `meta_ads.0001_initial` was applied to `staging` after PR #139 merged (merge commit `11b7bb3`). The plan read beforehand listed only that migration. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok.
- 2026-11-13: `catalog.0002_product_category_image` was applied to `staging` by the Supervisor session under Coach Ed's standing approval for a migration the approved plan specifies, after PR #141 merged (merge commit `c0dbc08`). The plan read beforehand listed only that migration (four added blank fields, no data change). Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. The catalog seed was not run. `production`: not run.
- 2026-11-13: `pack.0005_buyer_notice` was applied to `staging` by the BILLING Supervisor under Coach Ed's standing approval for a migration the approved plan specifies, after PR #152 merged (merge commit `e75c1a4`). The plan read beforehand listed only that migration (one new table). Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `PACK_PURGE_ENABLED` and `PACK_BUYER_NOTICE_ENABLED` were not set. `production`: not run.
- 2026-11-15: `inquiries.0002_stored_channels` was applied to `staging` at 18:03 UTC by the SANTE Supervisor under Coach Ed's standing approval for a migration the approved plan specifies, at commit `49dd122` (PR #168, round 20). The plan read beforehand listed only that migration. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `production`: not run.
- 2026-11-16: `media_library.0001_media_assets` and `meta_ads.0002_delivery_meta_receipt` were applied to `staging` by the SANTE Supervisor under Coach Ed's standing approval for a migration the approved plan specifies, at commit `a03ae10`. The plan read beforehand listed only those two migrations. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok. `production`: not run.
- 2026-11-17: `pack.0007_pack_visit` (one model, `PackVisit`) was applied to `staging` by the GATES Supervisor under Coach Ed's standing approval for a migration the approved plan specifies, at commit `04d6db2`. The plan read beforehand listed only that migration. Afterwards `migrate --plan` reported no pending operations, `showmigrations pack` showed 0001–0007 applied and `/healthz/` returned ok. `production`: not run.
- 2026-11-17: `inquiries.0003_remove_pancake` was applied to `staging` by the SANTE Supervisor under Coach Ed's standing approval for a migration the approved plan specifies, at commit `b273894` (PR #176, round 8). The plan read beforehand listed only that migration. The dry-run counts sent to the Manager first were 1 `InquiryConnection` row and 0 Pancake conversations, replies and messages. Afterwards `migrate --plan` reported no pending operations and connections numbered 0. `production`: not run.
- 2026-11-17: `inquiries.0004_messenger` was applied to `staging` at 07:35 UTC by the SANTE Supervisor under Coach Ed's standing approval for a migration the approved plan specifies, at commit `4d39c89` (PR #177, round 9), right after the api deploy. The plan read beforehand listed only that migration (schema only, no data step). The four inquiries tables held 0 rows before and after. Afterwards `migrate --plan` reported no pending operations, `showmigrations inquiries` showed 0001–0004 applied and `/healthz/` returned ok. `production`: not run.
- 2026-11-17: `inquiries.0005_message_seen_state` was applied to `staging` at 14:36 UTC by the SANTE Supervisor under Coach Ed's standing approval for a migration the approved plan specifies, at commit `fd7300b`. The plan read beforehand listed only `0005`. Afterwards `migrate --plan` reported no planned migration operations, `showmigrations inquiries` showed 0001–0005 applied and `/healthz/` returned ok. `production`: not run.
