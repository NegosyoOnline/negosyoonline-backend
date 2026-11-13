# Plan: back up the private Vercel Blob store (ClickUp z8uq9mewtf, parent z8uq9m7ydh)

Status: Plan for the Manager's approval (2026-11-14). PLAN ONLY: no code, no commit, no PR. Builder: Sonnet 5.5 sub-agent under the GATES Supervisor. Approval basis: Coach Ed, 2026-11-14, Manager session, relayed by the Manager: "Yes, as recommended" (about 1 day, plan first). Only the items in section 9a need his further word; 9b is decided by the Supervisor; 9c goes to the Manager.

Timing: the build starts from `origin/main`. It joins a release round before the Nov 20 freeze only if it is built, the local suite is green on SQLite and PostgreSQL, and the staging selftest has passed by then. Otherwise it goes in after the freeze.

Read for this plan (file:line against `origin/main` `88970c2661b1105cc401747926f808ddcb9fcc82`, 2026-11-14; round 11 and the former r11b branch are merged, so the `/readyz/` `email` field and the "Weekly check" are on main):
`apps/common/private_files.py` 1-18 (store rules), 44-48 (Blob API constants), 108-111 (`_checked`, key regex 41), 135-194 (`BlobStore`: `_send` 149-165, `put` 167-184, `get` 186-187, `delete` 189-194), 197-209 (`available`, `store`), 212-233 (`save`, `read`, `delete`, `discard`); `config/settings.py` 183-189 (private store settings, token 186), 373-458 (`CELERY_BEAT_SCHEDULE`, `retention-daily` 423-428), 485 (`TIME_ZONE`); `apps/catalog/media.py` 1-116 (the PUBLIC store client; same API version 13; not backed up, out of scope; not re-read at this SHA, unchanged since `71061a8` per `git diff --stat`); `apps/common/tasks.py` 15-30 (daily lock pattern with `cache.add`); `apps/health/views.py` 78-82 (`check_beat`), 100 (`check_email`), 117-133 (`readyz`, five fields today); `apps/health/tasks.py`; `apps/shop/checks.py` 11-40 (system check pattern); `pyproject.toml` 7 (`cryptography`); `docs/runbooks/backups-and-restore.md` 9, 14, 16, 29-44, 121-133 (section 4 table), 169-171 (Weekly check), 173-175 (section 6 scope); `docs/runbooks/launch-gates.md` 146 (gate 6 row), 150 and 159 (carried risk), 178 (timeline row), 194 (go-live check); `docs/runbooks/retention.md` 103, 169, 187, 214-216, 261, 282, 294, 298.

## 0. What the code and docs tell us (facts the plan rests on)

1. Who writes to the private store: all through `private_files.save()` (`private_files.py` 212-217) with a random key `<folder>/<32 hex>.<jpg|png|webp|pdf>` (`new_key`, 101-105). Folders in use today (grep of `FOLDER =`): `sante/agreement` (`apps/sante/agreement.py` 31), `sante/qr/site-<id>` (`apps/payments/services.py` 39), `billing/invoice-scans` (`apps/invoices/scans.py` 10), `billing/proofs` (`apps/billing/proofs.py` 13), `statements/pdf` (`apps/statements/documents.py` 15), `sante/shop-proofs/site-<id>` (`apps/shop/constants.py` 44), `sante/proofs/site-<id>` (`apps/pack/constants.py` 99), `sites/seal/site-<id>` (`apps/sites/seal.py` 13). `apps/catalog/media.py` `FOLDER = "catalog/products"` uses the PUBLIC store and is out of scope.
2. **A key is never overwritten.** Keys are random, `put` sends `x-add-random-suffix: 0` and no overwrite header (`private_files.py` 167-177). So a stored object's bytes never change under its pathname. This makes an incremental copy exact: copy once, never re-copy. (The plan still compares etag and size as a safety net.)
3. Deletes happen on purpose: erasure and retention call `private_files.delete`/`discard` (`apps/payments/retention.py` 49, `apps/sante/agreement.py` 184, etc.). `retention.md` 294 already says "Backups keep erased data for up to about 89 days" (the database backups). The file backup must stay inside that statement.
4. The store token is read-write only (`private_files.py` 135-144 parses `vercel_blob_rw_<store id>_<secret>`). Vercel has no read-only token, so the backup job uses the same token the app already uses. No new Vercel credential.
5. `BlobStore` can put, get and delete one key. It cannot list. It also rejects any pathname that does not match the key regex (`_checked`, 108-111, used by `_url`). A backup must copy every object, including any that predates or breaks that shape, so it needs its own listing and fetch code and must not use `_checked`. The plan adds a new module and **does not edit `private_files.py`**, so uploads and serving are untouched (out of scope per the task).
6. Vercel Blob facts (read 2026-11-14 at vercel.com/docs/vercel-blob, /using-blob-sdk, /private-storage, /usage-and-pricing):
   - `list()` returns up to 1000 per call (default), fields `pathname`, `size`, `uploadedAt`, `etag`, `url`, `downloadUrl`; **no contentType**; cursor pagination with `hasMore`; `prefix` filter; lexicographic order.
   - `get()`/`head()` return `contentType`, `size`, `uploadedAt`, `etag`. A GET of the private URL returns the content type header, so one GET gives bytes plus type (no extra `head`).
   - `copy()` exists but the docs say nothing about copying between stores, and it drops `contentType`. The plan does not depend on it: bytes are fetched and written by our code.
   - The docs do not document the REST list endpoint (only the SDK). The SDK's list is a `GET {api}/?limit=&cursor=&prefix=` with `x-api-version: 13` and the same bearer token; **this is from memory of the SDK, not read here, and must be proved by the first `--measure` run on staging** (read-only) before anything else is built on it. If it differs, the fix is local to the new `source.py`.
   - Prices (Pro): storage per GB-month (example in the page: $0.023/GB), advanced operations (upload, copy, list) $5.00 per million, simple operations (GET cache miss, head) $0.40 per million, delete free, Blob data transfer about $0.05/GB. Rate limits: Pro 120 simple/s and 75 advanced/s; Hobby 20 and 15 per second. Dashboard browsing counts as operations. Hobby plan stops Blob at its limits; the memory notes say the team is on a paid Team plan, to confirm.
   - Durability: S3-backed, 11 nines. Vercel keeps **no versions and no trash**: a deleted or overwritten object is gone (matches `backups-and-restore.md` 14).
7. Railway Storage Buckets (read 2026-11-14 at docs.railway.com/storage-buckets): S3-compatible (Put, Get, Head, Delete, List V1/V2, Copy, multipart, presigned URLs); $0.015 per GB-month; S3 operations free and unlimited; **bucket egress free; service egress to the bucket is not free**; "encrypted at rest"; **no versioning, no object lock, no lifecycle rules, no server-side-encryption feature**; **public networking only** (no private network); region fixed at creation; credentials from the bucket's Credentials tab as variables `BUCKET`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`, `REGION`, `ENDPOINT`.

## 1. Scope: what is backed up

Every object in the private store, found by listing the whole store (no prefix filter, no key-shape filter), not by walking database columns. Reason: `backups-and-restore.md` 36 lists ten key columns, and a new feature that stores a file but is missing from that list would silently go unbacked. Listing the store cannot miss a folder.

Per object, the backup keeps:

| Item | Where it comes from |
|---|---|
| bytes | GET of the private URL (`https://<store id>.private.blob.vercel-storage.com/<pathname>`, as `private_files.py` 146-147) |
| pathname | list |
| content type | the GET response header; if absent, `private_files.sniff()` on the first bytes (`private_files.py` 67-77) |
| size, uploadedAt, etag | list |
| sha256 of the plaintext | computed during the copy |
| backed_up_at; deleted_seen_at (empty while the source still has it) | the job |

Not backed up: the public store (catalog images, out of scope), Redis, variables, the database (Railway's own backups; this plan does not change them).

Hard limits to design for: objects are held in memory one at a time (existing pattern: `get` returns bytes). Upload limits today are small (QR 2 MB, `apps/payments/services.py`; others are image/PDF caps). The job skips and reports any object larger than a cap (default 50 MB, constant) instead of loading it, so one odd file cannot exhaust the worker. Size of all this is unknown until measured (section 3, "Measuring").

## 2. Destination: three options, one recommendation

Ruling 509745 (no real member data copied out of staging) and the preference for Railway or Vercel narrow the field. All options keep the data inside providers already used. The code writes to a small `Destination` interface, so the choice is a few lines, not a rewrite.

### Option A (recommended): a Railway Storage Bucket in the staging project

- Different provider from the source (source is Vercel; destination is Railway), so losing the Vercel account, the store or its token does not take the copy with it. Railway is already our processor (database, Redis); no new sub-processor, no new privacy line.
- $0.015/GB-month, operations free, bucket egress free. Worker-to-bucket upload is Railway service egress (not free; the docs give no rate in the page I read; the price is on Railway's billing page).
- Encrypted at rest by Railway. No versioning, no object lock: we implement retention ourselves (section 3). Public networking only, so the access key is the only gate; this is the reason for the client-side encryption in section 3.
- Needs a new dependency to speak S3: `boto3` (pinned range, with its `botocore`). Fallback if Coach Ed would rather add no dependency: a stdlib SigV4 client of about 120 lines in the style of `private_files.py` (more code and tests to own, same behavior).
- Needs from Coach Ed (each is his word): (1) create the bucket in the staging project (dashboard; region chosen at creation, same region as the worker, cannot be changed later); (2) approve the monthly cost; (3) approve the `boto3` dependency; (4) set the variables in section 3 on the `worker` service; (5) generate the encryption key and keep a copy. Section 9a lists these as the items for Coach Ed.

### Option B: a second private Vercel Blob store (new store, new token)

- Smallest code (reuses the same HTTP client and API version 13; no new dependency). Vercel allows unlimited stores on every plan. Same price per GB as the source ($0.023/GB-month example) and per-operation charges on both ends.
- Weakness: same vendor, same account, same dashboard and the same kind of token. An account problem, a mistaken "delete store", or a leaked Vercel credential hits both copies. It protects against a bad delete in the app, not against losing Vercel. That is a weaker answer to the risk that gate 6 names ("if the store or account were lost", `launch-gates.md` 150).
- Needs: Coach Ed creates the store (private, region chosen at creation) and a variable `FILE_BACKUP_VERCEL_TOKEN` on the worker.

### Option C (not recommended): an outside provider (AWS S3, Backblaze B2, Cloudflare R2, or Coach Ed's own disk)

- Best isolation and, for S3/B2, object versioning and lock. But it is a new account, a new sub-processor (the Privacy page and 30-day notice rule that `backups-and-restore.md` 155 describes for Sentry would apply), new credentials, and it moves real staging member files out of Railway/Vercel, which ruling 509745 says not to do. Listed only so the choice is visible. A disk copy on a laptop is the same problem and is not allowed by the ruling.

**Recommendation: Option A.** Option B is the fallback if Coach Ed wants zero dependency and zero new provider surface and accepts the weaker isolation.

Honest limit of A: no versioning or object lock, so a compromised worker (it holds the bucket key) could delete the backup. Mitigations in the plan: the job never deletes a data object before its retention end (section 3), the key is only on `worker`, and the integrity sample and status file (section 5) show surprises. If Coach Ed wants a copy a compromised worker cannot touch, that needs Option C (versioning/lock), a decision for before production.

## 3. Design

### Layout in the bucket

```
index/latest                       -> text: name of the newest index object
index/<UTC run time>.json.gz.enc   -> the index (encrypted): pathname -> {content_type, size, uploaded_at, etag, sha256, backed_up_at, deleted_seen_at}
data/<pathname>                    -> the encrypted bytes
status.json                        -> last run summary (counts only, no pathnames)
```

The bucket itself is the source of truth; **no database table, no migration**. Old index objects are kept 30 runs, then removed.

### Encryption at rest

1. Railway encrypts the bucket at rest (provider feature, as read above).
2. **Client-side**, before upload: AES-256-GCM with the `cryptography` package already in `pyproject.toml` (line 7, no new dependency for this), random 12-byte nonce per object, the pathname as associated data so an object cannot be swapped under another name. Stored as `version byte | nonce | ciphertext+tag`. The index is encrypted the same way. Key: one 32-byte key in a worker variable. Reason: the bucket is reachable on the public internet with one access key; a leaked key or a Railway-side exposure then yields only ciphertext (bank account images, statements). Cost: the key is now critical. **If the key is lost the backup is unreadable.** So Coach Ed keeps a copy of the key in his password manager, and the restore test proves a restore with that key. Decided by the Supervisor (section 9b); without client-side encryption the code is simpler and the backup is plaintext inside the Railway bucket.

### Variables and settings (names only; values are Coach Ed's, set on `worker`; the code reads them in `config/settings.py` and nowhere else)

| Setting / variable | Meaning | Default |
|---|---|---|
| `FILE_BACKUP_ENABLED` | feature switch, "1" turns the daily copy on | off |
| `FILE_BACKUP_BUCKET`, `FILE_BACKUP_ENDPOINT`, `FILE_BACKUP_REGION`, `FILE_BACKUP_ACCESS_KEY_ID`, `FILE_BACKUP_SECRET_ACCESS_KEY` | the Railway bucket, set by reference to the bucket's own variables | unset |
| `FILE_BACKUP_ENCRYPTION_KEY` | base64 of 32 random bytes | unset |
| `FILE_BACKUP_DELETED_KEEP_DAYS` | how long a copy outlives its source object | 35 |
| `FILE_BACKUP_STALE_HOURS` | how old `files_backup` in `/readyz/` may be before it fails | 36 |

`PRIVATE_BLOB_READ_WRITE_TOKEN` already exists (`config/settings.py` 186); the worker must have it (to confirm at build: it is needed for the PDF and notice tasks, so it most likely does). A Django system check (pattern of `apps/shop/checks.py` 11-40) warns when the switch is on and anything above is missing.

### Schedule (Celery beat)

- One beat entry `file-backup-daily`, task `file_backup.run`, `crontab(hour=4, minute=0)` Manila time, `expires` 3600, beside the 03:00 retention run (`config/settings.py` 423-428). 04:00 comes after retention, so what retention deletes at 03:00 is already gone from the source and is tombstoned the same morning rather than copied.
- Same pattern as `apps/common/tasks.py`: `cache.add("file-backup:day:<date>")` so a re-delivered task does nothing, plus a running lock (2 hours) so two runs never overlap. With the switch off, the task returns at once and calls nobody (no Blob or bucket request, same as the other switched-off tasks).
- Budget: a run stops starting new copies after 25 minutes and re-queues itself (`apply_async(countdown=60)`, at most 12 chained runs), so the first catch-up copy of a large store finishes over a few hours without a long-held worker slot. Copy rate is capped under the Vercel limit (default 10 per second).

### Incremental, idempotent copy

Per run:
1. List the source store fully (cursor loop, 1000 per page). List the bucket's `data/` prefix. Load the newest index.
2. For each source object: copy when the index has no entry, or the etag/size differs, or its `data/` object is missing in the bucket listing. Otherwise skip. Fetch (GET), compute sha256, encrypt, put to `data/<pathname>`, then record it. The index is saved every 500 objects and at the end, **after** the data object is stored, so a crash leaves at worst an orphan data object that the next run re-copies (harmless, same bytes, new nonce). Re-running a finished run changes nothing.
3. **Safety brake before any deletion work:** if the source listing is empty while the index is not, or more than 20% of indexed objects are missing from the source in one run, the run copies what it can, records `anomaly` in the status, **does not tombstone or purge**, and the check goes red. This stops a wrong token, an emptied or wrong store from erasing the backup.
4. Rolling integrity sample: download, decrypt and re-hash 20 objects per run, oldest-verified first. A mismatch is a failure.

### Deletes and retention (the privacy part)

- An object deleted at the source is **kept in the backup**, marked `deleted_seen_at`, and removed from the bucket once `deleted_seen_at + FILE_BACKUP_DELETED_KEEP_DAYS` has passed (35 days default). Reason: this is the recovery the gate wants (a wrongly deleted proof can be brought back), while a person's erasure is not defeated for long.
- 35 days sits inside the existing statement "Backups keep erased data for up to about 89 days" (`retention.md` 294) and is shorter than the database's monthly backups, so no new retention promise appears in the Terms or Privacy. The legal-wording rule ("bare compliance", memory) means no Privacy edit is planned; the Supervisor decided this reading (section 9b).
- A copy of a file that was never deleted at the source stays as long as the source has it (a billing record kept 5 years has its proof backed up 5 years).
- A restore must not resurrect erased people: by default the restore skips tombstoned objects; including them needs a flag and is followed by the "re-erase" step already in `backups-and-restore.md` 63 (section 4 below).
- Mid-window urgent erasure: `manage.py backup_private_files --purge-prefix sante/proofs/site-12/ --actor USER --request-ref REF` removes copies and index entries now (logs counts only; Coach Ed's word each time). Part of the plan because an erasure request should not wait 35 days when staff want it done sooner.

### Measuring the store size and cost (do not guess)

`manage.py backup_private_files --measure` is read-only and runs even with the switch off: it lists the private store (about one advanced operation per 1000 objects) and prints object count and total bytes in total and per top folder, never a pathname. The Vercel dashboard's Blob store usage (Latest value) is the cross-check; note that dashboard browsing counts as operations. Formulas for the Manager to fill in with the measured numbers:

- Backup storage per month = measured GB x $0.015 (Railway bucket) (encryption adds 29 bytes per object, nothing else).
- Retained deleted copies = deleted GB per month x 35/30 x $0.015.
- One-time first copy: Vercel reads (one simple operation per object, $0.40/million) + Blob data transfer (about $0.05/GB at the page's example rate) + Railway egress for the same bytes.
- Daily afterwards: only new bytes, plus about one list call per 1000 objects.
- Option B instead: GB x $0.023 plus about 1 advanced operation per object ($5.00/million).

## 4. Restore procedure

New command `manage.py restore_private_files` (run on the worker with `railway ssh`, single-quoted whole command per memory note; **dry run by default**):

- `--list [--prefix P]` prints counts and sizes per folder (no content).
- `--missing` copies every backed-up object that the live private store does not have (list the live store, diff with the index). Tombstoned objects are skipped unless `--include-deleted`.
- `--key K` (repeatable) or `--prefix P` restores specific objects.
- `--apply` actually writes. Without it, only prints what it would do.
- It never overwrites an existing live object (a live pathname that exists is skipped and counted), downloads, decrypts, verifies the stored sha256, then writes with `BlobStore.put` equivalent (same `x-vercel-blob-access: private`, same pathname, content type from the index). It prints counts and any mismatch, never a pathname list longer than the failures.

Order for a real incident (goes into `backups-and-restore.md` section 2, with Coach Ed's word for the environment each time, as now):
1. Restore the database first (existing steps 1-6).
2. With worker and beat still stopped, `restore_private_files --missing --apply` (add `--include-deleted` only if the database restore point is older than the deletions, e.g. rows that point at files deleted after the restore point).
3. Run the "rows pointing at missing files" check of section 1 (existing, `backups-and-restore.md` 36-44) against the restored store; the list should now be empty or explained.
4. If `--include-deleted` was used, or rows were restored from before an erasure, run the existing "Erased sites" re-erasure (`backups-and-restore.md` 61-63), which deletes the files again.
5. Start api, then worker, then beat (existing 7-8). Only then is the switch's next 04:00 run allowed to tombstone anything.

### Staging restore test (test files only)

No real member file is read or restored by the test. `manage.py backup_private_files --selftest` (or the same steps by hand) does exactly this and touches only the folder `backup-selftest/`:
1. Create 3 synthetic test files (a tiny PNG, a tiny PDF, a 1 MB random-bytes PNG-typed file) in the staging private store under the folder `backup-selftest/` (own random keys, via `private_files.save`; they are fake, generated by the command).
2. Back up **only that prefix** (`--prefix backup-selftest/`) to the bucket under a test prefix `selftest/`, so no real member object is read or copied in this step. Check: the 3 data objects and the index exist in the bucket; sizes are plaintext + 29 bytes; the bytes in the bucket are not the plaintext.
3. Delete the 3 test files from the live store (`private_files.delete`).
4. Restore the prefix with `restore_private_files --prefix backup-selftest/ --apply`.
5. Read the 3 files back from the live store and compare **byte-for-byte** with the generated originals (equality and sha256). Also: restoring again changes nothing (idempotent), and a tampered ciphertext (flip a byte in the test copy) is rejected by the authentication tag, and a wrong key fails.
6. Delete the 3 test files from the live store and the test prefix from the bucket. Record: object counts, sizes, sha256 equality, times, deletion.
This is the acceptance proof the task asks for ("restore brings back a test file byte-for-byte"). It runs **before** the switch is turned on. The first full run afterwards copies real staging files from Vercel into the Railway bucket in the same staging project: see section 9c.

## 5. Monitoring

Three layers, the first two cheap:

1. **`/readyz/` field `files_backup`** (extends the five fields on main: `db`, `cache`, `beat`, `sms_queue`, `email`; `apps/health/views.py` 124-131): true while the switch is off; with the switch on, true when the cache key `file_backup:last_ok` (set at the end of a run that finished with zero failures, no anomaly and every source object present in the backup) is younger than `FILE_BACKUP_STALE_HOURS` (36). One cache read, the same cost as the `beat` check (`apps/health/views.py` 78-82), no database query. `/healthz/` is not touched. Weakness, stated: Redis is not backed up (`backups-and-restore.md` 16); after a Redis loss the key is missing and the field is false until the next daily run (at most a day, a false alarm not a miss). A failed or stale backup turns `/readyz/` into 503 and the existing UptimeRobot check emails Coach Ed (same path as the `email` field). If Coach Ed would rather a backup problem never flips the overall `ok`, the field can be reported but left out of `ok` (section 9b records the decision: it flips `ok` at 36 hours).
2. **Durable status for the weekly check.** `status.json` in the bucket holds started/finished time, ok, source object count and bytes, copied, skipped, failed, tombstoned, purged, verified-sample result, anomaly flag (counts only). `manage.py backup_private_files --status` prints it and compares source count with backup count. The existing "Weekly check" (`backups-and-restore.md` 169-171, on main) gains two lines: run `--status` and record last success time and the two counts; fail the week if the last success is older than 36 hours or the counts differ.
3. A failed run logs an error (`logger.error`, counts only, no pathname), which Sentry picks up once `SENTRY_DSN` is set (`apps/common/sentry.py`).

## 6. Switch, tests, runbooks, migration

- **Feature switch:** `FILE_BACKUP_ENABLED`, off by default; with it off the task and `/readyz/` field do nothing and the commands `--measure`, `--selftest`, `--status`, `--list` still work for staging proof (they read, or touch only test files).
- **Migration: none.** No model, no table. State lives in the bucket and one cache key. (Said explicitly because the brief requires it.) The new app is added to `INSTALLED_APPS` only for task discovery and system checks.
- **Dependency:** `boto3` added to `pyproject.toml` and `uv.lock` (needs Coach Ed's word; fallback in Option A).
- **Tests (all offline, no network, fake in-memory source and destination classes):**
  - crypto round trip, tamper and wrong-key rejection, pathname-bound associated data;
  - listing pagination; copy of new, skip of unchanged, re-copy on changed etag, recover after a crash between data and index; idempotent second run;
  - deletion: tombstone, kept before the window, purged after; brake on empty source and on more than 20% missing; oversize object skipped and reported;
  - restore: `--missing` skips existing, skips tombstoned unless flagged, dry run writes nothing, sha256 mismatch refused;
  - switch off calls no client; the system check; `/readyz/` `files_backup` states (off, fresh, stale, missing key); the task's lock and chain limits;
  - the pathname/token never appear in logs or errors (pattern of the existing store tests).
  - **SQLite and PostgreSQL:** the app has no models, so the logic tests do not touch the database. They run in both modes anyway as part of the full suite (SQLite on PR CI, PostgreSQL locally with the builder's own PG on a fresh port per `local-test-services-isolation`). I will say so in the report rather than claim a database-specific test that does not exist. The `/readyz/` test already runs through the Django test client.
  - Local checks: targeted tests, the full suite, `manage.py check`, `manage.py check --deploy`, `makemigrations --check` (must report no changes), lint.
- **Runbook changes:** `docs/runbooks/backups-and-restore.md`: the "Scope" paragraph (line 9) and the table row (line 14) become "Yes, daily to a Railway bucket"; the section 6 scope sentence (line 175) states what the file restore test covers; new section 7 "Private file backup": layout, variables by name, schedule, retention 35 days, measure/status/selftest/restore commands, the restore order above, and the key-loss warning; section 4 table (121-133) gains `files_backup`; the Weekly check gains the `--status` lines; the NPC P5 note correction line stays until the staging proof passes, then is reworded. `docs/runbooks/launch-gates.md`: gate 6 row (line 146), the carried-risk lines (150, 159), the timeline row (178) and the go-live check (194) updated with the proof; a Gate 5 row "Private file backup and restore (z8uq9mewtf)". `docs/runbooks/retention.md`: one paragraph near line 294 that file backup copies of deleted files are kept 35 days. `docs/runbooks/billing.md`: the variable names. No change to uploads or serving.

## 7. Files touched (planned)

New: `apps/file_backup/` (`__init__.py`, `apps.py`, `source.py` listing/fetch store, `destination.py` S3 client, `crypto.py`, `service.py` the run, `tasks.py`, `checks.py`, `management/commands/backup_private_files.py` (`--measure`, `--status`, `--selftest`, `--purge-prefix`), `management/commands/restore_private_files.py`, `tests/`).
Edited: `config/settings.py` (settings, `INSTALLED_APPS`, one beat entry), `apps/health/views.py` and `apps/health/test_readyz.py` (`files_backup`, a sixth field beside `email`), `pyproject.toml`, `uv.lock`, the four runbooks above.
**Not edited:** `apps/common/private_files.py`, `apps/catalog/media.py`, any upload or serve path.

Overlap check (2026-11-14, from the backend repo, against `origin/main` `88970c2`): `gh pr list --json number,title,files` returned **0 open pull requests**, so no open PR touches any file above. The earlier `r11b` conflict is gone (merged). The build starts from `origin/main`; the check is repeated before the build is reported ready, because other GATES and release branches can still be pushed (branches without a PR are not visible to `gh pr list`; unverified).

## 8. Estimate

About 1 day of build as approved: source/destination/crypto and tests ~3.5 h, service run + retention + brake ~2.5 h, restore and selftest commands ~1.5 h, readyz, settings, beat, checks ~1 h, runbooks ~1 h, local checks on SQLite and PostgreSQL ~0.5 h. Plus Coach Ed-side steps the build waits on: create the bucket, set the variables, key copy. The staging proof (`--measure`, `--selftest`, then switch on) is run by the Supervisor on staging with his word; add about 1 hour. If `boto3` is refused and the stdlib SigV4 fallback is chosen, add about half a day. If the REST list call differs from the SDK, adding about 1 hour.

## 9. Decisions

### 9a. For Coach Ed (new accounts, credentials, variables and costs only; one recommendation each)

1. **Create a Railway Storage Bucket in the staging project, in the worker's region** (the region cannot be changed later). Recommend yes: a different provider from the source, no new sub-processor.
2. **Its monthly cost.** Formula: measured GB x $0.015 (bucket storage) + deleted-copy GB x 35/30 x $0.015; operations and bucket egress are free; one-time first copy adds Vercel reads and transfer (about $0.40 per million objects plus about $0.05 per GB) and Railway service egress (rate unknown, section 9c). **Rough range, an ESTIMATE until `--measure` runs (the store size is not known):** 1 to 50 GB gives about $0.015 to $0.75 a month for storage, and at most a few dollars once for the first copy. Recommend approve, and have the Supervisor report the measured figure before the switch goes on.
3. **The `boto3` dependency** (pinned range, with `botocore`, added through `uv`). Recommend yes; the alternative is a hand-written S3 signing client, which adds about half a day and more code to own.
4. **The `FILE_BACKUP_*` variables on `worker`**, by name: `FILE_BACKUP_ENABLED` (stays off), `FILE_BACKUP_BUCKET`, `FILE_BACKUP_ENDPOINT`, `FILE_BACKUP_REGION`, `FILE_BACKUP_ACCESS_KEY_ID`, `FILE_BACKUP_SECRET_ACCESS_KEY`, `FILE_BACKUP_ENCRYPTION_KEY`; optional with defaults `FILE_BACKUP_DELETED_KEEP_DAYS` (35) and `FILE_BACKUP_STALE_HOURS` (36). The four bucket values are set by reference to the bucket's own variables. Recommend yes; worker only, not `api`. No new Vercel credential: the existing `PRIVATE_BLOB_READ_WRITE_TOKEN` is reused.
5. **The encryption key** (32 random bytes, base64), with a copy kept in his password manager. Recommend yes. If the key is lost the backup cannot be read; the restore test proves a restore with that copy.
6. **Turn `FILE_BACKUP_ENABLED` on in staging after the selftest passes.** Recommend yes, after the selftest with test files only has passed (a staging switch needs a ruling; none exists for this one).

Production is not covered: no production bucket, no production variables. The public Blob store and PITR are out of scope.

### 9b. Decided by the Supervisor (2026-11-14, routine and reversible), taking the builder's recommendations

1. **Option A** (Railway bucket); Option B (second Vercel store) stays the documented fallback.
2. **Client-side AES-256-GCM** with the `cryptography` package already in `pyproject.toml`; no new dependency for it.
3. **35-day retention of deleted copies, with no Terms or Privacy edit.** It sits inside the 89 days in `retention.md` 294, and the legal wording rule is bare compliance.
4. **50 MB object cap** (skip and report above it); adjusted after `--measure` if needed.
5. **A stale or failed `files_backup` flips `/readyz/` `ok` at 36 hours**, like the `email` field.
6. **Runs on `worker` only** (not `api`); restore also on `worker` over `railway ssh`.

Build-time check (no longer a question): the Vercel plan (Pro or Hobby). The builder reads the Blob limits from the Vercel dashboard or the docs at build time and records them in the runbook. The 10 operations per second throttle works on both. Hobby stops Blob at its monthly limits, which the build notes in the runbook if the team is on Hobby.

### 9c. For the Manager

1. **Ruling 509745.** Does a Railway bucket in the same staging project count as "copied out of staging"? My reading: no (Railway already holds staging's database, and the restore test of 2026-11-13 stayed inside Railway on the same reading). Recommend yes (it does not count), before the first real run. The selftest runs first with test files only.
2. **The Railway service-egress rate**, still unknown (the Storage Buckets page says service egress to a bucket is not free and gives no rate). It sets the one-time first-copy cost and a small daily cost. Recommend the Manager read it from Railway's billing page or ask Coach Ed to screenshot it, so 9a item 2 can be firmed up. Unverified until then.

## 10. Plan-level notes

- The task's Deliverable 1 is answered as follows: what is backed up (section 1); where the copy goes and every new account, credential, variable and cost (sections 2, 3, 9a); schedule, incremental copy, retention, encryption (section 3); restore and the staging restore test with test files only (section 4); failed or stale backup shows up in `/readyz/` and the weekly check (section 5); switch, tests, runbooks (section 6).
- TWCinc code (ruling 520345): most likely does not apply here; this is a Vercel Blob to Railway bucket copy with nothing comparable to adapt, and none is copied.

## 11. Choices I made (what, why, source)

- No migration and no table: state in the bucket plus one cache key. Why: smallest and reversible; a table would need a migration approval (`CLAUDE.md`, working rules) and adds nothing the status file does not. Source: brief builder.md section 7.
- A new app and module, no edit to `private_files.py`. Why: the task says no change to how files are uploaded or served, and `_checked` would reject objects that do not match the key shape (`private_files.py` 108-111).
- List the store rather than the database columns. Why: cannot miss a new folder; `backups-and-restore.md` 36 already has to list columns by hand.
- 35-day deleted-copy retention. Why: inside the stated 89 days (`retention.md` 294) and shorter than the database monthly backup.
- Content type from the GET header, not `head`. Why: halves the per-object calls; list has no content type (Vercel docs, read).
- Brake on empty source / more than 20% missing. Why: a wrong token or emptied store must not erase the backup.
- `/readyz/` via one cache read, no database query. Why: the brief asks for one cheap query; Redis loss gives a one-day false alarm, which is safe.
- Everything about the Vercel list REST call is marked "prove with `--measure` first" because the docs describe only the SDK; I did not call any Vercel or Railway API and read no secret.
