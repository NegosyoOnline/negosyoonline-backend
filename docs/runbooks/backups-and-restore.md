# Backups, Restore, Readiness and Monitoring Runbook

ClickUp z8uq9mekrk (gate z8uq9m7ydh). Plan approved by Coach Ed through the manager on 2026-11-13. Nothing here is a public uptime promise.

Staging and production both exist and both take the nightly encrypted database copy (section 8): `/readyz/` reports `"db_backup": true` on both, read 2026-11-26. Railway is no longer the platform (ruling 1100430000597874); the database and Redis run on Bunny Magic Containers (`docs/runbooks/bunny-staging.md`). A restore of any environment needs Coach Ed's word each time. No standing approval covers production.

## 1. What is backed up, and what is not

**Scope: the database is backed up by the nightly encrypted dump in section 8 (switched on in staging and production, `/readyz/` `db_backup` true on both, read 2026-11-26); Bunny itself gives no database backup. The private Blob files have no backup for now.** The daily file backup in section 7 is built (ClickUp z8uq9mewtf, BE #161) but not in use: Coach Ed declined the bucket on cost on 2026-11-14 ("for storage bucket, let's not create a backup. this is expensivce for now for me"). Until he decides otherwise the private Blob files have no backup and are not part of any restore test. The business continuity note (NPC P5, outside this repository) says files are covered by the yearly restore test and that one private file is restored. That stays untrue while the file backup is not in use; the note must not be signed as written. The public store (catalog images) is not backed up.

| Data | Backed up? | Where it lives |
|---|---|---|
| PostgreSQL | Yes: nightly encrypted `pg_dump`, the newest copy of each of 14 days plus of 8 weeks (section 8). | Postgres in the Bunny data app (`docs/runbooks/bunny-staging.md`); the copies in the private Vercel Blob store under `db-backups/`. |
| Private Blob files (billing proofs, shop payment proofs, statement and invoice PDFs, payment QR, pack files, membership agreement files, business seals) | No for now. A daily backup to a Railway Storage Bucket is built (section 7) but not in use: Coach Ed declined the bucket on cost on 2026-11-14 ("for storage bucket, let's not create a backup. this is expensivce for now for me"), and `FILE_BACKUP_ENABLED` stays off. Vercel Blob itself keeps no versions; a deleted file is gone. | Private Vercel Blob store, `PRIVATE_BLOB_READ_WRITE_TOKEN`; the copy in the bucket named by `FILE_BACKUP_BUCKET`. |
| Public Blob files (catalog images) | No. Same as above. | Public Vercel Blob store, `PUBLIC_BLOB_READ_WRITE_TOKEN`. |
| Redis | No, by design. | Redis in the Bunny data app. Holds sign-in and verification codes, throttle counters, the encrypted text of queued SMS, the Celery queue and the beat heartbeat. Losing it expires codes and drops queued SMS texts. |
| Variables | By name only, here and in `docs/runbooks/billing.md`. Values are on Bunny (api, worker, beat; data app) and Vercel and nowhere in the repository. | Bunny app variables (`docs/runbooks/bunny-staging.md`); Vercel project variables. |

### Historical: Railway database backups (Railway era, before the move to Bunny; not in force)

The database no longer runs on Railway, and none of the Railway volume-backup facts below apply to it. They are kept only as the record of 2026-11-09 to 2026-11-14. The backup in force is section 8.

- Schedules a volume can use: daily (every 24 hours, kept 6 days), weekly (every 7 days, kept 27 days), monthly (every 30 days, kept 89 days). Several can be selected; manual backups are possible. Set under the service's Backups tab.
- Chosen: Daily only (Coach Ed, 2026-11-14, "no, lets just use daily."): up to 24 hours of data lost, restores reach back 6 days. Confirmed in Edit schedule 2026-11-14 (Daily ticked, Weekly and Monthly not).
- What staging has set, seen by Coach Ed in staging Postgres > Backups on 2026-11-13: at first there was no schedule and no backups at all, with PITR off. This was a gap. He then set a schedule (the screen showed "Next backup in 12 hours") and took an on-demand volume backup, 2026-11-13 19:33, 1.11 GB. Schedule boxes: not yet confirmed. A screenshot of 2026-11-14 shows volume backups Scheduled with the next backup in 6 hours and one kept backup (2026-11-13 19:33, 1.11 GB, locked), but the daily, weekly and monthly boxes are behind Edit schedule and not shown. Retention: 6 days for a daily backup; confirmed in the console on 2026-11-14 (Edit schedule: "Backed up every 24 hours, kept for 6 days").
- History: on 2026-11-09 a screenshot of Coach Ed's (NPC Build 4, z8uq9md33y, comment 1100430000451493) showed the staging backup schedule as Daily, with weekly and monthly not set. The 2026-11-13 read found no schedule. Why the two differ is not known: the schedule may have been removed between those dates, or the earlier view may have shown something else. The 2026-11-13 read above is the current state.
- PITR is left off on purpose: it costs more and redeploys the database. Revisit before production.
- **A volume backup can only be restored in place**, into the same project and environment. It cannot go to a separate service. Restoring mounts a new volume (named with the backup's date stamp) at the original path; the previous volume stays unmounted in the project; the change takes effect when you click Deploy, which redeploys the service. Newer backups stay with the previous volume.
- Point-in-time recovery (PITR) is different: it creates a sibling Postgres service at a chosen time and leaves the source untouched. It must be enabled first and only covers the time after it was enabled (a window of about four weeks). It is not enabled on staging as far as this runbook knows.
- Only a `pg_dump` taken to somewhere else survives the loss of the project or the volume. Wiping a volume deletes its backups too.

What still holds on Bunny: a nightly `pg_dump` taken to somewhere else is the only copy that survives the loss of the volume (section 8).

### After a restore: files and rows can disagree

Rows hold keys only; a file has a key and no row link in the other direction. After a restore:

- A row may point at a file that was deleted after the restore point.
- A file may exist with no row (uploaded after the restore point).

Keys are stored in these columns: `catalog` `CatalogProduct.image_key` and `CatalogProductRevision.image_key`, `billing` `BankTransferPayment.proof_key`, `invoices` `store_key`, `statements` `pdf_key`, `pack` `file_key`, `shop` `ShopProofSubmission.file_key`, `sante` `MembershipAgreementVersion.storage_key`, `sites` `SiteBusinessProfile.seal_key`, and the payment account's QR key (`apps/payments/models.py`). To find rows pointing at a missing file, list the store's keys (Vercel dashboard, or the store's list API with the read-write token on your own machine) and compare with `uv run python manage.py shell`:

```python
from apps.billing.models import BankTransferPayment
keys = set(open("store-keys.txt").read().split())   # one key per line, from the store listing
missing = [p.pk for p in BankTransferPayment.objects.exclude(proof_key="") if p.proof_key not in keys]
```

Repeat for each column above. Files with no row are listed the other way round (store keys not found in any column). Do not delete a file only because no row names it until Coach Ed has decided; it may be the only copy.

## 2. Restore order

1. Coach Ed gives his word for this environment and this backup, in the Manager session.
2. Silence the uptime alert (section 5) and tell the Supervisor.
3. **Stop the worker and beat** services first, and keep them stopped until step 8. The api may stay up read-only or be stopped; it does not send billing email or run sweeps.
4. Restore the chosen nightly copy into the data app's database with the restore job (`docs/runbooks/bunny-staging.md`, "Restore job (data app)"; list the copies first with `manage.py restore_database --list`; section 8). The target database must exist and be empty. Record the copy's name and time. (Railway-era steps, a volume backup restored in the Backups tab, no longer apply.)
5. Run the restore safety checks (section 3) with worker and beat still stopped. If the file backup is on, restore the private files too, before the checks that list missing files (section 7, "Restore").
6. Show what would migrate. This changes nothing: the `migrate --plan` command in `docs/runbooks/migrations.md` (Procedure, step 1). If it lists migrations, do not run them; take the list to the Supervisor (`docs/runbooks/migrations.md`).
7. Start the api. Check `https://<api host>/healthz/` and `https://<api host>/readyz/`. `/readyz/` will say `"beat": false` until step 8; that is expected.
8. Only then start the worker, then beat. After about two minutes `/readyz/` must answer 200 with every field `true`. Re-enable the uptime alert.

## 3. Restore safety checks

Each item below was re-read in the code on 2026-11-13 (main `f406bfb`). Run the queries in `manage.py shell` on the restored database. "Restore point" means the backup's time.

### Erased sites

Data erased by the site deletion executor after the restore point comes back with the restore. List the `site_data_erased` and `account_disposed` records kept outside the database (support mailbox, the PR and ClickUp record of each batch) and erase those sites again (`docs/runbooks/retention.md`, "An ended site's data"). Nothing re-applies them automatically.

### Billing emails

`apps/billing/notices.py` sends each email once per `BillingNotice.idempotency_key`, and looks only at events from the last 48 hours (`WINDOW`). The notice rows written after the restore point are gone, so for any event in the restored data whose email went out after the restore point, the next scan sends it again. Trial and renewal reminders can repeat the same way.

- Keep the worker (which runs `billing.notices`) stopped.
- List what could repeat and compare with the email provider's sent log (Resend) for the period from the restore point to now:

```python
from datetime import timedelta
from django.utils import timezone
from apps.billing.models import BankTransferPayment, SubscriptionTransition, BillingNotice
since = timezone.now() - timedelta(hours=48)
BankTransferPayment.objects.filter(submitted_at__gte=since).values("pk", "status", "submitted_at", "decided_at")
SubscriptionTransition.objects.filter(effective_at__gte=since, code__in=(
    "suspended_no_outcome", "suspended_after_attention", "trial_ended_no_method")).values("pk", "code", "effective_at")
BillingNotice.objects.order_by("-sent_at").values("code", "idempotency_key", "sent_at")[:50]   # the last notice the restored data knows of
```

- An email the provider shows as already sent, with no `BillingNotice` row, will be sent again. Write its `idempotency_key` into a `BillingNotice` row by hand only with Coach Ed's word, or let it go out once and say so to the Supervisor. Only then start beat.

### Bank-transfer confirmations made after the restore point are lost

`confirm_transfer` (`apps/billing/services.py`) settles a cycle in one transaction with its transition, event and audit entry; a restore removes all of them together. The money has already moved, so re-confirm from HQ's Finance records (the proof and amount received), never as a second charge. Find them: the payments still in a submitted state whose proofs the bank statement shows as received.

```python
BankTransferPayment.objects.filter(status="submitted").values("pk", "amount_centavos", "submitted_at")
```

### Meta Conversions events

`meta_ads.sweep` re-queues any Purchase or AddPaymentInfo delivery that is still `queued` or `retrying` after five minutes and under seven days old (`apps/meta_ads/services.py`). A delivery that was sent after the restore point is still open in the restored data, so the sweep would send it again. Keep beat stopped until you have looked:

```python
from apps.meta_ads.models import MetaDelivery
MetaDelivery.objects.filter(outcome__in=("queued", "retrying")).values("site_id", "event_name", "event_id", "created_at")
```

`/readyz/` now alerts on a stuck delivery: the `meta_queue` field goes false (503, so the uptime check emails) when any delivery has been `queued` or `retrying` for more than 30 minutes.

Meta de-duplicates by `event_id`, so a repeat is not a second count, but check the list before deciding. Events that happened after the restore point have no row and are not re-sent; they are lost.

### SMS

A text waits encrypted in Redis (not backed up) until the phone takes it, and a row says only that it exists (`apps/notifications/sms.py`).

- Rows that were `queued` at the restore point have no held text now; `sms.sweep` (run when the phone polls or a text is sent) marks them `expired`. `/readyz/` shows `"sms_queue": false` until then if any are older than 15 minutes.

```python
from apps.notifications.models import SmsMessage
SmsMessage.objects.filter(status__in=("queued", "dispatched")).values("public_id", "purpose", "status", "created_at")
```

- A text sent after the restore point leaves no row. Compare the gateway phone's own sent log from the restore point to now with the rows above. Nothing re-sends them.

### Retention deletions

`retention.run` now runs daily at 03:00 Manila time on the worker (`docs/runbooks/retention.md`; the plan called it manual, the code is newer). Rows deleted after the restore point come back. With the worker stopped nothing deletes them again. When the worker starts, the next 03:00 run removes what is due, if `RETENTION_ENABLED` (and, for the date-of-birth erase, `DOB_ERASE_ENABLED`) is on. To count first: `manage.py run_retention` (dry run, see the retention runbook). A person's data erased on request (`dispose_account`) comes back with the restore and must be erased again; check the audit log kept elsewhere for those requests.

## 4. Readiness and the heartbeat

`GET /readyz/` (`apps/health/views.py`), unauthenticated, beside `/healthz/`, which is unchanged (liveness only). It answers 200 or 503 with booleans only, for example `{"ok": true, "db": true, "cache": true, "beat": true, "sms_queue": true, "email": true, "files_backup": true, "meta_queue": true, "xendit_queue": true}`: no versions, counts, hosts or errors.

| Field | True when |
|---|---|
| `db` | `SELECT 1` succeeds (PostgreSQL statement timeout 2 seconds). |
| `cache` | one Redis set and get succeeds within 2 seconds. |
| `beat` | the heartbeat is under 3 minutes old. The Celery task `health.heartbeat` (`apps/health/tasks.py`) runs every 60 seconds from the `health-heartbeat` beat entry and writes the time to the cache, so a fresh value proves beat, the broker and the worker. With no `REDIS_URL` (tasks run in-process) this is always true. |
| `sms_queue` | no SMS row has been `queued` for more than 15 minutes. Counted only while `SMS_GATEWAY_ENABLED` is on; otherwise true. |
| `email` | not (at least `READYZ_EMAIL_FAILURES` of the newest emails sent in the last `READYZ_EMAIL_WINDOW_MINUTES` failed and none sent). Defaults 3 failures and 30 minutes, both environment variables read in `config/settings.py`; change them in the api's Bunny variables (`docs/runbooks/bunny-staging.md`) without a code change. It reads the newest 20 `EmailMessage` rows by primary key (one query), ignores `pending`, and is true when there are no recent rows. The body says only `true` or `false`, never an address. |
| `files_backup` | True while `FILE_BACKUP_ENABLED` is off (the field then builds and calls no client); it stays true on staging because the switch is off and the backup is not in use. Switched on, true while the last fully good backup run (no failure, no anomaly, every source file copied) is younger than `FILE_BACKUP_STALE_HOURS` (default 36). One cache read (`file_backup:last_ok`), no database query. Redis is not backed up, so after a Redis loss the field is false until the next daily run (at most a day; a false alarm, not a miss). A false field turns `/readyz/` into 503, which the uptime check emails. |
| `db_backup` | Reported only while `DB_BACKUP_ENABLED=1`; with the switch off the field is absent. True while the newest encrypted database copy (section 8) is younger than `DB_BACKUP_STALE_HOURS` (default 30); false when the newest is older or none exists. Read from the cache (`db_backup:last_ok`); after a Redis loss it is rebuilt from one listing of the private store. A false field turns `/readyz/` into 503. |
| `meta_queue` | no Meta Conversions delivery (`MetaDelivery`) has been `queued` or `retrying` for more than 30 minutes (`READYZ_META_QUEUE_MINUTES` in `apps/health/views.py`, well above the normal span of about 12 minutes). Always true while `META_EVENTS_ENABLED` is off. One `.exists()` query. |
| `xendit_queue` | no Membership Pack order is still `awaiting_xendit` after the sweep should have expired it (invoice expiry plus 1 hour, or 3 hours after placement with no invoice, plus one 15-minute reconcile tick and a 15-minute margin). True while `XENDIT_ENABLED` is off. One query. |

It is limited to 30 calls a minute per client address (answers 429 `{"ok": false}`), using the last `X-Forwarded-For` entry. No migration, no variable, no new service.

## 5. Monitoring

Tools approved by Coach Ed on 2026-11-13: one external uptime check with an email alert, and Sentry's free tier with personal data scrubbed. The account now exists (org "NS", project python-django, free plan, created 2026-11-13); the `SENTRY_DSN` value is his to create and is not set anywhere. Alerts go to Coach Ed's email.

Set up 2026-11-13: UptimeRobot free plan, Coach Ed's own account, 4 monitors at 5-minute intervals (the four URLs below), all green. The free plan sends HEAD and cannot pick the method or a keyword; `/readyz/` answers HEAD since BE #155 (8664aa5). The alert proof (stop the worker and beat, receive the Down and Up emails) was proved on 2026-11-13; see the ops B row in `docs/runbooks/launch-gates.md`.

### Uptime checks (one external service, email alert)

| URL | Expect |
|---|---|
| `https://api-staging.negosyonline.com/readyz/` | 200 and `"ok": true` in the body |
| `https://www-staging.negosyonline.com/` | 200 |
| `https://dash-staging.negosyonline.com/` | 200 |
| one Santé storefront staging address | 200 (the Supervisor names it when the check is set up) |

Check every 1 to 5 minutes, alert after two failures in a row. Production gets the same four once it exists.

### Sentry

`apps/common/sentry.py`, started from `config/settings.py` only when `SENTRY_DSN` is set. With no DSN it does nothing. Set it on `api` and `worker` (and `beat`) by name, value from the Sentry project, with Coach Ed's word.

Before SENTRY_DSN is set anywhere, staging included: Sentry is a new sub-processor, and the published Terms give Subscribers 30 days' notice. The Privacy page must first list "Sentry, error reports from our servers, in the United States;" (HQ's change; HQ records the date it goes live on ClickUp z8uq9mbreu). The DSN may be set no earlier than 30 days after that date. The Sentry account must be created in the US data region, or the Privacy line changes first.

Dated step (2026-11-21). The Privacy line "Sentry, error reports from our servers, in the United States" went live with frontend release #66 (commit 1fe7d6d) on 2026-11-13, so the earliest date `SENTRY_DSN` may be set on staging is Sunday 2026-12-13 (the 30-day notice in the published Terms still decides when). Setting it on staging is approved: Coach Ed, "GATES D1-D5: yes", Saturday 2026-11-21, relayed by the Manager. Production is not covered. On or after that date, in a quiet window (02:00-05:00 HKT or ads paused), announced to the SANTE Supervisor first:

1. Coach Ed creates a fresh client key named "staging" in the Sentry project.
2. Set `SENTRY_DSN` and `SENTRY_ENVIRONMENT=staging` on `api`, `worker` and `beat`. The value is never printed or committed.
3. Run the proof below.

- Errors only: `traces_sample_rate=0`, `send_default_pii=False`.
- `before_send` removes the request body, cookies, query string and headers (Authorization included) and the user, and masks anything that looks like an email address or a Philippine mobile number in messages, exception values and breadcrumbs.
- Covers the API (Django integration) and the Celery worker.
- Alert rule in Sentry: any new issue emails Coach Ed.
- No error tracking in the browser at launch: the dashboards are static pages and it would add a second processor's script (the Supervisor's routine decision, reversible).
- Proof once set: a deliberate test error appears in Sentry with no personal data.

### Silencing during a planned restore or maintenance

Pause the uptime monitors (or put them in the service's maintenance window) before step 2 of section 2, and resume them at the end. The Sentry alert needs no silencing. Never delete a monitor to silence it.

### Weekly check

Once a week the Manager checks, read-only, that `GET /readyz/` on staging and on production reports `"db_backup": true` (the newest nightly copy is younger than `DB_BACKUP_STALE_HOURS`, default 30; the field is reported only while `DB_BACKUP_ENABLED` is on). Record the date and the two readings in the ClickUp rulings or gates record. A false field, or a missing one, is raised with Coach Ed. The Railway-era reading of the Postgres Backups tab and volume usage no longer applies; no Bunny volume-usage reading is recorded in the repository, so none is required here until Coach Ed rules one.

With the file backup on, also run `manage.py backup_private_files --status` on the worker and record the last success time and the two counts (files in the store and files in the backup). The week fails if the last success is older than 36 hours or the two counts differ.

## 6. Restore test (staging only, run 2026-11-13; historical, Railway era)

**Historical.** This test ran on Railway before the move to Bunny and is kept as the record of 2026-11-13. Its steps (scratch Railway Postgres service, `railway ssh`) no longer apply. The current restore drill is in section 8 ("Restore drill").

**Scope: the 2026-11-13 test below covers the database only.** Private files in the Vercel Blob private store (payment proofs, uploaded seals, payment QR codes, billing proofs, statement PDFs, invoice scans, agreement and pack files; `apps/common/private_files.py`) were not part of it. The file restore test is `backup_private_files --selftest` (section 7): three synthetic test files, backed up and restored byte for byte, no real member file read or restored. It has not been run on staging, and will not be while the file backup is not in use (declined for now on cost, 2026-11-14). The NPC P5 note (outside this repository) saying files are covered by the yearly restore test, with one private file restored, is not true while there is no file backup; the note must be corrected, or reworded if the backup is later turned on and the `--selftest` passes, by its owner before it is signed.

Run on 2026-11-13. Evidence: the "Restore test" row in `docs/runbooks/launch-gates.md` (Gate 5). No production data, no change to staging itself.

**Limit:** a volume backup restores only in place, into the same project and environment, over staging's own volume. It cannot go into a scratch service, and PITR is off. So the test uses a `pg_dump` of staging restored into a scratch Railway Postgres service. **This tests the dump and the restore procedure, not Railway's backup.** Say so in the evidence.

**Privacy rule (Coach Ed's ruling 509745):** no data leaves Railway. The dump and the restore both run inside Railway, over the private network. Never dump to your own machine. Never put a connection string or a dump in the repository or in chat.

Steps:

1. In the staging project, create a scratch Postgres service (for example `postgres-restore-test`).
2. On the scratch service only, add a reference variable `SOURCE_DATABASE_URL` = `${{Postgres.DATABASE_URL}}`.
3. Check the versions inside the scratch container through `railway ssh` (single-quote the whole remote command): `pg_dump --version` and the server version. They must match.
4. Inside the container, run:
   `pg_dump "$SOURCE_DATABASE_URL" --format=custom --no-owner --no-acl | pg_restore --dbname="$DATABASE_URL" --no-owner --no-acl --exit-on-error`
   Note the start time and the finish time.
5. Compare row counts in psql on both URLs, table by table (`SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;`, and `SELECT count(*)` on the tables that matter).
6. Compare `django_migrations` on both sides (row count and max id). This replaces running `migrate --plan` locally.
7. Run the db check (`select 1`) and the sms_queue check (rows queued for 15 minutes or more) in psql. cache and beat need Redis and a worker, and are not tested on the copy.
8. Delete the scratch service and its volume, and confirm in the dashboard that both are gone.

Record in the ClickUp task: versions, start and finish times, row counts side by side, the `django_migrations` comparison, the db and sms_queue results, the deletion time.

## 7. Private file backup (ClickUp z8uq9mewtf)

**Not in use.** Built and merged (BE #161) but switched off: Coach Ed declined the Railway Storage Bucket on cost on 2026-11-14 ("for storage bucket, let's not create a backup. this is expensivce for now for me"). No bucket exists and `FILE_BACKUP_ENABLED` stays off on staging. Until he decides otherwise the private Blob files have no backup and are not part of any restore test. The NPC P5 warning in section 1 stands. The rest of this section describes the backup for when it is turned on.

Code: `apps/file_backup/`. No migration and no table: the bucket holds all state. It never changes how files are uploaded or served (`apps/common/private_files.py` is untouched).

**What it does.** Every day at 04:00 Manila time (after the 03:00 retention run) the `worker` lists the whole private Blob store (no folder filter, so a new feature's folder cannot be missed), copies each new object to a Railway Storage Bucket, and checks 20 stored copies. It runs only while `FILE_BACKUP_ENABLED=1`. Switched off, the task and the `/readyz/` field build and call no client. Once per Manila day, one run at a time; a run stops starting copies after 25 minutes and queues the next chunk itself (at most 12), so the first catch-up copy of a large store finishes over a few hours. Copies run at up to 10 a second, under Vercel's limit (Pro: 120 simple and 75 advanced operations a second; Hobby: 20 and 15; the plan on the Vercel dashboard was not read by the build, so check it before the first run).

**Layout in the bucket.** `data/<pathname>` the encrypted bytes; `index/<run time>.json.gz.enc` and `index/latest` the encrypted index (pathname to content type, size, etag, sha256, backup time, deleted-seen time; 30 kept); `status.json` the last run's counts, no pathnames.

**Encryption.** Railway encrypts the bucket at rest. The bytes are also encrypted before upload: AES-256-GCM, a random nonce per object, the pathname bound in as associated data, key `FILE_BACKUP_ENCRYPTION_KEY` (base64 of 32 random bytes; `openssl rand -base64 32`). **If the key is lost the backup cannot be read.** Coach Ed keeps a copy in his password manager. The bucket has public networking only, so its access key is the only other gate. It has no versioning and no object lock: a compromised `worker` could delete the backup. A copy that survives that needs a provider with versioning or lock, a decision for before production.

**Variables** (names only; set on `worker` only, never `api`; the bucket values by reference to the bucket's own variables; read in `config/settings.py` and nowhere else):

| Variable | Meaning |
|---|---|
| `FILE_BACKUP_ENABLED` | `1` turns the daily run and the `/readyz/` field on. Unset: off. |
| `FILE_BACKUP_BUCKET`, `FILE_BACKUP_ENDPOINT`, `FILE_BACKUP_REGION`, `FILE_BACKUP_ACCESS_KEY_ID`, `FILE_BACKUP_SECRET_ACCESS_KEY` | The Railway bucket. |
| `FILE_BACKUP_ENCRYPTION_KEY` | The key above. |
| `FILE_BACKUP_DELETED_KEEP_DAYS` | How long a copy outlives its source object. Default 35. |
| `FILE_BACKUP_STALE_HOURS` | How old the last good run may be in `/readyz/`. Default 36. |

`PRIVATE_BLOB_READ_WRITE_TOKEN` is reused (Vercel has no read-only token); the `worker` has it already. `manage.py check` warns (`file_backup.W001` to `W003`) when the switch is on and a value above is missing.

**Retention of deleted files (applies only once the backup is in use).** A file deleted at the source (an erasure, a retention rule) stays in the backup, marked deleted, for 35 days and is then removed. Database backups on staging are now Daily only, kept 6 days (`docs/runbooks/retention.md`), so this 35-day copy would outlast them; it adds no Terms or Privacy promise. A file never deleted at the source stays as long as the source has it. A restore skips files marked deleted unless `--include-deleted` is given.

**Safety brake.** If the store listing is empty while the index is not, or more than 20% (and more than 10) of the indexed files are missing at once, the run copies what it can, marks `anomaly`, deletes and purges nothing, and `/readyz/` goes red. This stops a wrong token or an emptied store from erasing the backup. A file over 50 MB is skipped and reported (also red) until the cap is raised in `apps/file_backup/service.py`.

**Commands** (on the `worker` through `railway ssh`, the whole remote command in single quotes; output is counts only, never a pathname):

- `manage.py backup_private_files --measure`: read-only, runs with the switch off. Prints the file count and bytes of the live store, in total and per top folder. First run on staging also proves the Vercel list call (the REST form is not in Vercel's docs). Cost: backup storage per month is the measured GB times $0.015 (Railway bucket); retained deleted copies add deleted GB a month times 35/30 times $0.015; the one-time first copy adds Vercel reads ($0.40 per million files), Vercel data transfer (about $0.05 a GB) and Railway service egress (rate not read).
- `manage.py backup_private_files --status`: the last run's counts from the bucket, and whether the store's file count equals the backup's.
- `manage.py backup_private_files --selftest`: the restore test below. Runs with the switch off.
- `manage.py backup_private_files --purge-prefix P --actor NAME --request-ref REF [--apply]`: removes the copies and index entries under `P` now (an urgent erasure; Coach Ed's word each time). Dry run unless `--apply`. A file the store still has is not touched. Logs counts, actor and reference only.
- `manage.py restore_private_files --list [--prefix P]`, `--missing`, `--prefix P`, `--key K`, `--include-deleted`, `--apply`: dry run unless `--apply`. It never overwrites a file the store has, checks each file's sha256 before writing it, and refuses a mismatch.

**Restore (after a data loss).** Coach Ed gives his word for the environment each time.

1. Restore the database first (section 2, steps 1 to 4), worker and beat still stopped.
2. `restore_private_files --missing` (dry run), then `--missing --apply`. Add `--include-deleted` only if the database restore point is older than some deletions, for example rows that point at files deleted after the restore point.
3. Run the "rows pointing at missing files" check (section 1) against the restored store. The list should be empty or explained.
4. If `--include-deleted` was used, or rows came back from before an erasure, run the "Erased sites" re-erasure (section 3), which deletes the files again.
5. Start api, then worker, then beat (section 2). Only then may the next 04:00 run mark anything deleted.

**Staging restore test (test files only).** `backup_private_files --selftest` creates three synthetic files (a tiny PNG, a tiny PDF, a 1 MB PNG-typed random file) under `backup-selftest/` in the staging private store, backs up only that prefix into the bucket under `selftest/`, checks the stored bytes are the plaintext plus 29 bytes and not the plaintext, deletes the live files, restores them, and compares byte for byte (and sha256). It then checks that a second restore changes nothing and that a tampered copy and a wrong key are refused, and removes the test files and the test copy. No real member file is read, copied or restored. It runs before the switch is turned on. Record: the PASS lines, the object counts and the times. The first full run after the switch is on copies real staging files from Vercel into the Railway bucket in the same staging project (the manager's reading of ruling 509745: Railway is already where staging's database lives).

**Not done until proved on staging:** `--measure`, `--selftest`, and the switch, all waiting for Coach Ed's word (and the bucket, its variables and the key, which are his).

## 8. Nightly encrypted database backup on Bunny (ClickUp z8uq9mhben)

**Switched on in staging and production** (`/readyz/` reports `"db_backup": true` on both, read 2026-11-26; staging commit a99c2bd). Code: `apps/db_backup/`. No migration and no table. This is the database backup, a copy that survives the loss of the Bunny volume; Bunny itself gives no database backup.

**What it does.** Every day at 04:30 Manila time (after the 04:00 file backup) the `worker` runs `pg_dump --format=custom --no-owner --no-acl` of the default database, encrypts the dump (AES-256-GCM, the copy's name bound in, the same code as section 7: `apps/file_backup/crypto.py`) and puts it in the private Vercel Blob store as `db-backups/<UTC time>.dump.enc`. Only after a good upload it applies retention. The password goes to the tools in environment variables, never on a command line; the tools' output is never logged. Logs hold counts and the exception type only. It runs once per Manila day and one at a time.

**Retention.** Counted from the newest copy: the newest copy of each of the 14 newest days that have a copy, plus the newest copy of each of the 8 newest weeks (ISO weeks, UTC). Everything else named `db-backups/<time>.dump.enc` is deleted by the task. Other files in the store are never touched. A stopped backup deletes nothing.

**Variables** (names only; on `worker`; the command also reads them wherever it runs):

| Variable | Meaning |
|---|---|
| `DB_BACKUP_ENABLED` | `1` turns on the nightly run and the `/readyz/` field `db_backup`. Unset: off, nothing runs, readyz does not report it. |
| `DB_BACKUP_ENCRYPTION_KEY` | Base64 of 32 random bytes (`openssl rand -base64 32`). New for each environment. **Lose it and the copies cannot be read**; Coach Ed keeps a copy in his password manager. |
| `DB_BACKUP_STALE_HOURS` | How old the newest copy may be in `/readyz/`. Default 30. |
| `DATABASE_URL` | The database that is dumped. |
| `PRIVATE_BLOB_READ_WRITE_TOKEN` | Reused: the private store that receives the copies. |

`manage.py check` warns (`db_backup.W001`, `W002`) when the switch is on and the token or the key is missing.

**Client version.** The worker image (`deploy/bunny/Dockerfile`, target `worker`) installs `postgresql-client-18` from the PostgreSQL project's apt repository and fails its build unless `pg_dump` is version 18. Keep it equal to the server's major version. The `api` and `beat` images do not carry it.

**Backup now (for example right before a cutover).** On the worker: `python manage.py backup_database`. It refuses, before dumping anything, when the key or the token is missing or bad. It runs with the switch off too. Output is the size and the number of old copies removed.

**Restore drill (the Wednesday drill, and the monthly one after go-live).** Coach Ed's word is needed for each environment. Use a temporary separate Bunny app (or any scratch PostgreSQL 18), never the running database:

1. Create the scratch PostgreSQL 18 database and an empty database in it. Take its URL (do not paste it anywhere but the command).
2. On a worker-image container that holds `DB_BACKUP_ENCRYPTION_KEY` and `PRIVATE_BLOB_READ_WRITE_TOKEN` for the environment being drilled, list the copies: `python manage.py restore_database --list`.
3. Restore a named copy: `python manage.py restore_database --name db-backups/<time>.dump.enc --target-url "$SCRATCH_DATABASE_URL"`. `--target-url` is required every time, and the command refuses a target that is the database the container itself runs on. The target must already exist and be empty; `pg_restore` stops at the first error. Output never shows the URL, the content or the tools' messages.
4. Against the scratch database only: run `python manage.py migrate --check` with `DATABASE_URL` set to the scratch URL, compare row counts table by table with the source (`SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;`, and `count(*)` on the tables that matter), and compare `django_migrations`.
5. Delete the scratch app or database and confirm in the dashboard that it is gone. Record the copy's time, the versions, the counts side by side and the deletion time in ClickUp.

A real restore of an environment follows section 2 (worker and beat stopped first) and the safety checks in section 3.

**Local proof (2026-11-21, task z8uq9mhben).** A throwaway PostgreSQL 18 container: migrated database `src` plus 250 probe rows (133 tables, 972 rows), `backup_database` with the worker image's `pg_dump` 18.7, then `restore_database` into empty database `dst`: 133 tables and 972 rows, every table count equal. The stored copy is not a readable dump, and a restore into the running database was refused.

## 9. Copying a Blob store (P7)

ClickUp z8uq9mhnzu (parent z8uq9mh7dt); production plan P7(a), GATES P7 run sheet step 3.5. Code: `apps/file_backup/blobcopy.py` and `manage.py copy_blob_store`. No migration, no table, no setting.

**What it does.** Copies every object of one Vercel Blob store into another so every stored file reference keeps working: the staging private store into production's private store, then the staging public (media) store into production's public store. One store pair per run (a Blob store is private or public, never both), named by `--access private` or `--access public`; the access is the store's own, so private stays private and public stays public. Each object keeps its pathname and content type. The put sends no allow-overwrite header, as the app's own uploads do (`x-vercel-blob-access` as the store, `x-add-random-suffix: 0`).

**How to run it: one job, once.** It runs as a one-off production job (a one-off container or `bunny` job, not the API, worker or beat), with the two token variables set on that job only. Name the variables anything that does not exist on the app, for example `COPY_SRC_PRIVATE_TOKEN` (staging store's read-write token) and `COPY_DEST_PRIVATE_TOKEN` (production store's), and the same with `PUBLIC` for the media stores, and pass the NAMES:

```text
manage.py copy_blob_store --access private --source-token-env COPY_SRC_PRIVATE_TOKEN --dest-token-env COPY_DEST_PRIVATE_TOKEN
manage.py copy_blob_store --access private --source-token-env COPY_SRC_PRIVATE_TOKEN --dest-token-env COPY_DEST_PRIVATE_TOKEN --apply
```

Then the same pair of commands with `--access public` and the two public stores' tokens. Remove both variables from the job when it ends; never set them on the API, worker or beat, and never write them in a file, a ticket or a log. A token is read only from its named variable: there is no token argument. Options: `--prefix P` (only pathnames under P), `--max-mb N` (default 50: a larger object is counted `failed` until the limit is raised), `--rate N` (requests a second, both stores together; default 10).

**Name rule (P7-BLOBGUARD, ClickUp z8uq9mjhhj).** `--access` is one flag for both stores, so both token variable NAMES must contain its word in upper case (`PRIVATE` or `PUBLIC`), for example `COPY_SRC_PRIVATE_TOKEN`. Anything else is refused before any network call: `Not done: --access private needs both token variable names to contain PRIVATE.` Names only, never values. 

**Kind probe (before any copy, in a dry run too).** The command proves each store is of the declared access, so a private store can never be copied into a public one by a wrong flag:

- Source: one listed object (under `--prefix`) is fetched through the declared-access host. A failure is `Not done: the source store is not a <access> store.` An empty source prints `nothing to copy` and exits 0 (no destination probe).
- Destination: one probe object is put at `ops/blobguard-probe/<random hex>` with the declared access and read back through the declared host. For `private` the public host (`<store id>.public.blob.vercel-storage.com`) must NOT serve it (a 200 there is a refusal); for `public` it must. A mismatch is `Not done: the destination store is not a <access> store.`; a refused probe put is `Not done: the destination store did not take the probe.`
- The probe is deleted in a `finally`, also on failure, and the output says `probe deleted: yes` or `no` (a flag only; a `no` means a stray `ops/blobguard-probe/` object to delete by hand). **The probe is the only thing a dry run writes.** Nothing prints a token, URL or pathname.

**Dry run first (the default).** Prints the kind check, then counts only: `listed`, `bytes` (and GB), `already at the destination` (same pathname), `would copy`, and `conflict (existing, size differs)`. It downloads and copies nothing (the probe above is its one write). Check `would copy` plus `already at the destination` equals `listed`.

**`--apply`.** For each source object missing at the destination: download, put at the same pathname with the same content type, read it back and compare sha256 with the source bytes. A mismatch counts the object `failed` and removes that fresh copy so a re-run can try again. A destination object that already exists is never overwritten: same bytes are `skipped_existing`, different bytes are `conflict` (reported, left as it is). Output: `listed`, `copied`, `skipped_existing`, `conflict`, `failed`, `bytes` (copied), and `oversize` (also in `failed`). **Fail fast:** the run stops at the first failed put or read-back at the destination (a store that refuses or mis-reads one object will not take the rest), prints the counts so far with `stopped early: yes`, and exits non-zero. A failed fetch from the source still counts `failed` and the run goes on. No pathname, URL or token is printed or logged. The exit is non-zero (`Not done: ...`) when `failed` or `conflict` is above 0.

**Success** is `failed: 0` and `conflict: 0`, and a second `--apply` run printing `copied: 0` with `skipped_existing` equal to `listed`. Safe to re-run any number of times; run it again after fixing a failure. Objects added at the source after the run need another run (or a final run in the cut-over window).

**Real Vercel behaviour not proved by the tests.** The tests use fakes only; the first real run is the proof of the list call (REST form, see section 7), of the public-store read host (`<store id>.public.blob.vercel-storage.com`) and of Vercel refusing a put onto an existing name (the command relies on that as a second guard; its dry-run listing is the first). Run the dry run on both pairs and read the counts before any `--apply`.
