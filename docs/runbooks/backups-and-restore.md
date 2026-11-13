# Backups, Restore, Readiness and Monitoring Runbook

ClickUp z8uq9mekrk (gate z8uq9m7ydh). Plan approved by Coach Ed through the manager on 2026-11-13. Nothing here is a public uptime promise.

Production does not exist yet. Everything below names staging; production gets the same once it exists. A restore of any environment needs Coach Ed's word each time. No standing approval covers production.

## 1. What is backed up, and what is not

| Data | Backed up? | Where it lives |
|---|---|---|
| PostgreSQL | Railway's scheduled volume backups (below). | Railway Postgres service. |
| Private Blob files (billing proofs, statement and invoice PDFs, payment QR, pack files) | No. Vercel Blob keeps no versions; a deleted file is gone. | Private Vercel Blob store, `PRIVATE_BLOB_READ_WRITE_TOKEN`. |
| Public Blob files (catalog images) | No. Same as above. | Public Vercel Blob store, `PUBLIC_BLOB_READ_WRITE_TOKEN`. |
| Redis | No, by design. | Railway Redis. Holds sign-in and verification codes, throttle counters, the encrypted text of queued SMS, the Celery queue and the beat heartbeat. Losing it expires codes and drops queued SMS texts. |
| Variables | By name only, here and in `docs/runbooks/billing.md`. Values are in Railway (api, worker, beat) and Vercel and nowhere in the repository. | Railway service variables; Vercel project variables. |

### Railway database backups (read from Railway's documentation on 2026-11-13)

- Schedules a volume can use: daily (every 24 hours, kept 6 days), weekly (every 7 days, kept 27 days), monthly (every 30 days, kept 89 days). Several can be selected; manual backups are possible. Set under the service's Backups tab.
- What staging actually has set is read from the dashboard, not from this page. Record it here with the date it was read: `TODO(Coach Ed or the Supervisor): schedule and retention seen on staging, date` (screenshots belong to NPC Build 4, z8uq9md33y).
- **A volume backup can only be restored in place**, into the same project and environment. It cannot go to a separate service. Restoring mounts a new volume (named with the backup's date stamp) at the original path; the previous volume stays unmounted in the project; the change takes effect when you click Deploy, which redeploys the service. Newer backups stay with the previous volume.
- Point-in-time recovery (PITR) is different: it creates a sibling Postgres service at a chosen time and leaves the source untouched. It must be enabled first and only covers the time after it was enabled (a window of about four weeks). It is not enabled on staging as far as this runbook knows.
- Only a `pg_dump` taken to somewhere else survives the loss of the project or the volume. Wiping a volume deletes its backups too.

### After a restore: files and rows can disagree

Rows hold keys only; a file has a key and no row link in the other direction. After a restore:

- A row may point at a file that was deleted after the restore point.
- A file may exist with no row (uploaded after the restore point).

Keys are stored in these columns: `catalog` category and product `image_key`, `billing` `BankTransferPayment.proof_key`, `invoices` `store_key`, `statements` `pdf_key`, `pack` `file_key`, and the payment account's QR key (`apps/payments/models.py`). To find rows pointing at a missing file, list the store's keys (Vercel dashboard, or the store's list API with the read-write token on your own machine) and compare with `uv run python manage.py shell`:

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
4. Restore the backup in Railway (Backups tab, Restore, review the staged change under Details, Deploy). Record the backup's date and time.
5. Run the restore safety checks (section 3) with worker and beat still stopped.
6. Show what would migrate. This changes nothing: the `migrate --plan` command in `docs/runbooks/migrations.md` (Procedure, step 1). If it lists migrations, do not run them; take the list to the Supervisor (`docs/runbooks/migrations.md`).
7. Start the api. Check `https://<api host>/healthz/` and `https://<api host>/readyz/`. `/readyz/` will say `"beat": false` until step 8; that is expected.
8. Only then start the worker, then beat. After about two minutes `/readyz/` must answer 200 with every field `true`. Re-enable the uptime alert.

## 3. Restore safety checks

Each item below was re-read in the code on 2026-11-13 (main `f406bfb`). Run the queries in `manage.py shell` on the restored database. "Restore point" means the backup's time.

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

`GET /readyz/` (`apps/health/views.py`), unauthenticated, beside `/healthz/`, which is unchanged (liveness only). It answers 200 or 503 with booleans only, for example `{"ok": true, "db": true, "cache": true, "beat": true, "sms_queue": true}`: no versions, counts, hosts or errors.

| Field | True when |
|---|---|
| `db` | `SELECT 1` succeeds (PostgreSQL statement timeout 2 seconds). |
| `cache` | one Redis set and get succeeds within 2 seconds. |
| `beat` | the heartbeat is under 3 minutes old. The Celery task `health.heartbeat` (`apps/health/tasks.py`) runs every 60 seconds from the `health-heartbeat` beat entry and writes the time to the cache, so a fresh value proves beat, the broker and the worker. With no `REDIS_URL` (tasks run in-process) this is always true. |
| `sms_queue` | no SMS row has been `queued` for more than 15 minutes. Counted only while `SMS_GATEWAY_ENABLED` is on; otherwise true. |

It is limited to 30 calls a minute per client address (answers 429 `{"ok": false}`), using the last `X-Forwarded-For` entry. No migration, no variable, no new service.

## 5. Monitoring

Tools approved by Coach Ed on 2026-11-13: one external uptime check with an email alert, and Sentry's free tier with personal data scrubbed. Accounts, the `SENTRY_DSN` value and any cost are not approved yet and are his to create. Alerts go to Coach Ed's email.

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

- Errors only: `traces_sample_rate=0`, `send_default_pii=False`.
- `before_send` removes the request body, cookies, query string and headers (Authorization included) and the user, and masks anything that looks like an email address or a Philippine mobile number in messages, exception values and breadcrumbs.
- Covers the API (Django integration) and the Celery worker.
- Alert rule in Sentry: any new issue emails Coach Ed.
- No error tracking in the browser at launch: the dashboards are static pages and it would add a second processor's script (the Supervisor's routine decision, reversible).
- Proof once set: a deliberate test error appears in Sentry with no personal data.

### Silencing during a planned restore or maintenance

Pause the uptime monitors (or put them in the service's maintenance window) before step 2 of section 2, and resume them at the end. The Sentry alert needs no silencing. Never delete a monitor to silence it.

## 6. Restore test (staging only, written, not run)

Not run. The exact command goes to Coach Ed for his word after this runbook is merged. No production data, no change to staging itself.

**Finding from Railway's documentation (2026-11-13):** a volume backup restores only in place, into the same project and environment, over staging's own volume. It cannot be restored into a scratch service. So the test cannot restore Railway's backup into scratch. Unless point-in-time recovery has been enabled on staging (it then forks to a sibling service and leaves staging untouched), the test uses a `pg_dump` of staging restored into a scratch Railway Postgres service. **This tests the dump and the restore procedure, not Railway's backup.** Say so in the evidence.

Steps (a `pg_dump` restore):

1. In the staging project, create a scratch Postgres service (for example `postgres-restore-test`). Coach Ed's word covers this service and its cost; delete it at the end.
2. Dump staging, from your own machine through Railway's tunnel (the guide prefers this to the public proxy, which is billed as egress). In a directory linked with `railway link` to staging:
   `railway connect postgres --tunnel-only` (leave it running; it prints host, port, user, password and database).
   `pg_dump "postgresql://<user>:<password>@localhost:<port>/<database>" --format=custom --no-owner --file=staging.dump`
   Note the time the dump started: it is the "backup time".
3. Restore into the scratch service's database (create the database first if needed):
   `pg_restore --dbname="<scratch connection string>" --no-owner --exit-on-error staging.dump`
4. Compare table row counts on both sides:
   `SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;`
   (counts may differ slightly if staging changed after the dump; use `SELECT count(*)` on the tables that matter: accounts, sites, billing payments and notices, SMS messages.)
5. Run `uv run python manage.py migrate --plan` with `DATABASE_URL` set to the scratch database, on your machine, never against staging. It must list nothing to apply.
6. With the same `DATABASE_URL` and `DJANGO_DEBUG=false`, no Redis and the worker and beat not running, start the API locally and call `/readyz/`, or run the four checks by hand (`db` and `sms_queue`; `cache` and `beat` need Redis and are expected to pass only where Redis is set).
7. Record the evidence in the ClickUp task: backup time, restore duration, row counts side by side, the `migrate --plan` output, the `/readyz/` result. Then delete the scratch service.

Never put the scratch connection string or the dump in the repository or in chat. Delete `staging.dump` after the test: it holds real data. Staging holds live member data since the Santé ads test, so deleting the local `staging.dump` right after the check is a required privacy step; never copy it elsewhere.
