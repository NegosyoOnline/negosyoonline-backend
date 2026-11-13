# G2 Merchant media upload and asset ledger (plan only)

ClickUp z8uq9mb384 (parent z8uq9m8h46, BILLING Supervisor). Plan-only builder, 2026-11-13, from `origin/main` 9c5cd6a. Approved by the Manager as "the G2 z8uq9mb384 plan only (no build)" (rulings log comment 1100430000509255). Nothing here is built, migrated, switched or configured. `SITE_ERASURE_ENABLED`, `PACK_PURGE_ENABLED`, `STORAGE_METERED` and `BILLING_USAGE_ENABLED` are not touched. Not launch work (Manager comment 1100430000512512); storage is not charged at launch (Coach Ed, 2026-11-13: "there will be no extra charge during lunch. we will turn this on later").

## 0. The short version

- Today there is **no merchant upload, no byte ledger and no per-site prefix**. The only public-media write is the shared Santé catalog product photo (`apps/catalog/media.py`), owned by the catalog writer site, not by a merchant.
- G2 adds one small app, `apps/media_library`, with **two tables** (`MediaAsset`, `MediaObject`), **three owner routes** (list, upload, delete), one **default-off switch** `MEDIA_LIBRARY_ENABLED`, and a read function `ledger.bytes_at(site, cutoff)` that P6.2d calls and G2 never calls.
- The ledger is the one place that holds merchant business media, and only the media service writes it. D4/Q3 files are excluded **by construction** (they are written by other code into other folders and never get a ledger row), and tests prove it.
- Version 1 stores **originals only**. No rendition is created (no image library is installed; adding one is a decision). The schema and the sum already carry a `role` column, so renditions can arrive later without a migration and each would have its own row (acceptance: "no untracked derivative").
- Three branches: two backend, one frontend (section 12). One migration, `media_library.0001_initial`, for which Coach Ed's approval is asked (section 9).
- Three things are Coach Ed's before build: renditions vs the 5 GB allowance, the limits, and the store (section 14).

## 1. Sources read

- ClickUp: task z8uq9mb384 (description; its one comment 1100430000512512); parent z8uq9m8h46 (id only, not opened: unread); P6.1 task z8uq9mb22f (description and its one comment 1100430000400591: r24 approved 2026-11-04 as a contract only, citations to re-verify before planning, done here); rulings log z8uq9med6x (all 25 comments, newest first; nothing media-specific beyond the comments named above).
- Contract text, **read from the TWCinc checkout, read-only, for confirmed intent only**: `/Users/edgeronilla/Dev/twcinc/.claude/docs/superpowers/specs/2026-11-02-negosyoonline-billing-p6-1-metering-source-contract.md`, sections 2 (D4, D5, Q3, L13), 3 (Gate G2, lines 183-203), 5 (lines 534-548), 7, 8, 13. That file is r25 (drift amendment, not approved); the approved text is r24. Sections 3 and 5 of r24 were not separately opened: unread (the r25 text of the same sections is what is quoted here, and r25 says its amendment is drift only).
- Code on main 9c5cd6a: `apps/catalog/media.py`, `apps/catalog/services.py` (`set_image`, `clear_image`, `is_writer`), `apps/catalog/api.py`, `apps/common/private_files.py`, `apps/billing/plan.py`, `apps/billing/usage.py`, `apps/billing/clock.py`, `apps/sites/api.py` (`_owner_site`), `apps/sites/models.py`, `apps/lifecycle/erase.py`, `apps/pack/checks.py`, `apps/common/throttle.py`, `apps/common/ids.py`, `config/settings.py`, `config/settings.py` beat table, tenant-isolation tests.
- Docs: `docs/runbooks/storage.md`, `docs/runbooks/billing.md` (usage switch paragraph), `docs/runbooks/retention.md` (lines 162, 169, 282), `docs/superpowers/plans/2026-11-11-backend-billing-b4-usage-ledger-and-overage.md`, `docs/superpowers/plans/2026-11-13-site-deletion-executor-z8uq9mb22v.md`, `docs/superpowers/plans/2026-11-13-owner-emails-z8uq9mbbkj.md` (format only), `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md` (lines 17, 84, 126, 180, 280).
- Frontend, read only: `apps/dash/public/index.html:129-139` (the `media-library` view is a "not available yet" card), `apps/dash/public/assets/js/dashboard.js:6` (the view is in `VIEWS`), `apps/dash/tests/nav.test.mjs:156,174` (the route is in the hidden list).
- **Unread / not available:** the P6.1 contract's r24 file itself (only r25 found); the parent task z8uq9m8h46; Vercel Blob's own documentation for list and for deleting an absent key (behaviour assumed, marked below); any Cloudflare R2 account (there is no R2 in this backend: `docs/runbooks/storage.md:5`).

## 2. What exists today (verified in code)

| Fact | Where |
|---|---|
| Two stores only, both Vercel Blob: the private store and the public media store. **No R2.** The task and contract text say "scheduled R2 check"; that wording is legacy | `docs/runbooks/storage.md:5`; `apps/common/private_files.py:204-210`; `apps/catalog/media.py:1-18` |
| Public store client: one folder `catalog/products`, 2 MB cap, `save(content, content_type)` returns a random key `<folder>/<32 hex><ext>`, `delete`, `discard`, `url_for(key)` = `PUBLIC_MEDIA_BASE_URL` + key | `apps/catalog/media.py:30-31, 42-45, 72-93, 96-117` |
| The public store needs `PUBLIC_BLOB_READ_WRITE_TOKEN` and `PUBLIC_MEDIA_BASE_URL`; **neither is set on staging** | `config/settings.py:190-191`; `docs/runbooks/storage.md` "Staging facts" |
| The only caller is the catalog product image route; it is writable only by the catalog writer site (`sante.is_catalog_writer`), so a normal merchant has **no upload route** | `apps/catalog/services.py:106-107, 438-469`; `apps/catalog/api.py:149-168` |
| No ledger, no byte count, no per-site prefix for public media. The key holds no site | `apps/catalog/media.py` (key from `private_files.new_key(FOLDER, ...)`) |
| The type check, size check and key shape are shared: `check()` sniffs JPEG, PNG, WebP, PDF by first bytes and demands both the declared and the sniffed type be allowed; `new_key(folder, type)`; key regex `^[a-z0-9-]+(/[a-z0-9-]+)*/[0-9a-f]{32}\.(jpg\|png\|webp\|pdf)$` | `apps/common/private_files.py:32-41, 67-99, 101-106` |
| Private files that have a site carry `site-<row id>` in the folder (`sante/qr/site-<pk>/`, `sante/proofs/site-<pk>/`, `sites/seal/site-<pk>/`); billing, invoice scan, statement and agreement folders carry **no site** | `docs/runbooks/storage.md:13-22` |
| The agreement is `sante/agreement` in the **private** store. `store_bytes` (named in the task and contract) **does not exist** in this repo; `private_files.save` is its equivalent. The folder `sante-agreement-evidence` is **not found** anywhere in this repo | grep of `apps`, `docs` on main |
| `STORAGE_INCLUDED_GB = 5`, `GB_BYTES = 1_000_000_000`, overage PHP 30 per started GB, `STORAGE_METERED = False`; `usage_enabled()` needs the switch **and** `STORAGE_METERED` | `apps/billing/plan.py:152-171` |
| `record_storage_snapshot(site, *, source_reference, period_end_at, measured_bytes, measured_at, now=None)`: returns `disabled` / `not_metered` before reading anything else; needs `source_reference` 1-64 chars, int bytes >= 0, aware datetimes; `measured_at == period_end_at`; one snapshot per period ever; at or below 5 GB writes no row. Its docstring: "The producer passes merchant-uploaded business media only: billing cannot tell a receipt from a banner" | `apps/billing/usage.py:52-56, 104-160` |
| Allowance periods end at 3:00 PM Manila on the anchor day (`CUTOFF = time(15, 0)`); `clock.allowance_periods(...)` is the only source of period ends | `apps/billing/clock.py:8-9, 36-46` |
| Owner routes live under `/v1/sites/{site_id}/...`, reach their site through `site_for(...)` then `_owner_site` (a missing, removed, foreign site: the same 404; a Billing-only site: 403 `billing_only`), and answer 404 while their switch is off | `apps/sites/api.py:145-155`; `apps/catalog/api.py:1-47` |
| The tenant-isolation harness lists every owner route in `OWNER_ROUTES`, maps prefixes to apps in `APP_PREFIXES`, walks every route with a foreign site and every child id, and snapshots every row of every model (`row_snapshot`). A route not listed fails `test_every_route_under_a_site_has_a_cross_tenant_test` | `apps/common/tests/test_tenant_isolation/owner_base.py:43, 60, 140, 169-175`; `test_owner_routes.py:29-51` |
| **`negosyoonline_platform/isolation.py` does not exist** in this repo, and `test_isolation.py:46` is not a registry check (it is `body_for`). The nearest equivalents are the two harness files above and `apps/sites/tests/test_isolation.py`. There is no registry of "models with a FK to Site" to add to or leave out | grep and `ls` on main |
| Storage ledger doc test: every `*FOLDER = "..."` constant and every literal folder passed to `private_files.save`/`new_key` must have a row in `docs/runbooks/storage.md` | `apps/common/tests/test_storage_ledger.py:11-60` |
| Site erasure deletes private files per site (seal, QR, shop proofs; pack proofs kept until the export is ready) and anonymises rows in place; `PROTECT` foreign keys keep rows | `apps/lifecycle/erase.py:147-190`; `docs/runbooks/retention.md:282` |
| The dash `/media-library/` view is a placeholder card | `apps/dash/public/index.html:129-139` |
| No image library (Pillow) is a dependency | `pyproject.toml:5-15` |

## 3. Design

### 3.1 App and tables

New app `apps/media_library` (label `media_library`). Both tables carry a foreign key to `sites.Site` (`on_delete=PROTECT`, like every other site table), so both are covered by `row_snapshot()` and the isolation walk automatically; there is no registry to edit (section 2).

**`MediaAsset`**: what the merchant sees in the library.

| Field | Notes |
|---|---|
| `public_id` | `med_` + 20 characters, `apps/common/ids.new_public_id("med")`, unique. The only id in a route or a response |
| `site` | FK `sites.Site`, PROTECT, `related_name="media_assets"` |
| `label` | text, at most 80, may be empty. Typed by the merchant, shown escaped. The upload's file name is **not** stored |
| `created_at` | set on commit |
| `removed_at` | null while live |

**`MediaObject`**: the ledger. **One row per stored object**, original or rendition.

| Field | Notes |
|---|---|
| `site` | FK `sites.Site`, PROTECT, `related_name="media_objects"`. Copied from the asset so the sum and the prefix check never join |
| `asset` | FK `MediaAsset`, PROTECT, `related_name="objects"` |
| `role` | `original` or `rendition` (closed set). The L13 decision is expressed only as which roles are summed |
| `spec` | text, at most 20, empty for an original; a rendition's recipe, for example `w640` |
| `key` | text, at most 160, **unique**. The stored key, exactly as the store holds it |
| `content_type` | `image/jpeg`, `image/png` or `image/webp` |
| `bytes` | BigInteger, `>= 0`: the length of the bytes **as written to the store** (not the upload's declared size) |
| `added_at` | aware datetime, set once, in the same transaction that creates the row |
| `removed_at` | null, or the instant the object was confirmed gone from the store |

Constraints: `bytes >= 0`; `removed_at IS NULL OR removed_at >= added_at`; `role = 'original'` implies `spec = ''`; unique `(asset, role, spec)`. Indexes `(site, added_at)` and `(site, removed_at)`.

The five columns the task names (site, key, bytes, added_at, removed_at) are all present; `asset`, `role`, `spec`, `content_type` are the additions the contract allows ("the L13 decision may add to them, for example to mark a rendition or a kind").

**Rows are never deleted, and a row's `site`, `key`, `bytes` and `added_at` never change.** Only `removed_at` is written, once, by `ledger.remove()`. Site erasure keeps the rows (section 8). That is what makes a past cutoff reproducible forever.

### 3.2 The per-site key prefix

`merchant-media/site-<suffix>/<32 hex>.<ext>`, where `<suffix>` is the 20-character random part of the site's `public_id` (`site_` + suffix). It matches the existing key regex (`[a-z0-9-]` only; the id alphabet is `0-9a-hjkmnp-tv-z`), so `private_files.new_key` accepts it unchanged. **The row id is not used** because these URLs are public: a row number in a public URL would tell anyone how many sites exist and would be guessable; the existing private folders use `site-<pk>` only because they are never public.

- `constants.FOLDER = "merchant-media"`; the per-site folder is `f"{FOLDER}/site-{suffix}"`.
- The scheduled check lists the prefix `merchant-media/` and nothing else (section 7). The agreement and other no-site folders are in the **private** store, and the catalog photo is in `catalog/products`; neither is under that prefix, so "the prefix check allows folders with no site in the key" holds by never looking outside `merchant-media/`.
- The prefix is derived from the site's immutable `public_id`, so the same site always lands in the same prefix.

### 3.3 Upload (service `media_library.services.upload`)

1. Router sign-in step as the catalog's: switch on (else 404), `signed_in`, `site_for(account, site_id, template=SANTE)`, `_owner_site` (Billing-only: 403 `billing_only`). Body is never read for a site that is not the caller's.
2. Store configured (else 503 `media_store_not_configured`).
3. Throttle: `throttle.hit("media-upload", account.pk, limit=30, window=600)`.
4. `private_files.check(upload, allowed=IMAGE_TYPES, max_bytes=MAX_FILE_BYTES)` (size judged before the read; declared and sniffed type must both be allowed; the stored type is the sniffed one).
5. Pre-check the quota (section 5) so an obviously full library stores nothing.
6. Put the object: key from `private_files.new_key(site_folder, content_type)`, store write through the public store client. A store failure: 502 `media_not_saved`, nothing recorded.
7. In one transaction: lock the `Site` row (`select_for_update`), **re-check the quota under the lock**, create the `MediaAsset` and the `MediaObject` (`role=original`, `bytes=len(content)`, `added_at=now`). Any failure after step 6: discard the new object (`discard`, never raises) and re-raise.
8. A crash between 6 and 7 leaves an object with no ledger row. By the contract an orphan is **not billed**; the scheduled check reports it (section 7).

### 3.4 Delete (service `media_library.services.remove`)

The contract says an object counts until its `removed_at`, and a row whose object still exists counts until the object is confirmed gone. So the order is: **remove the object first, then set `removed_at`**.

1. Sign-in step as above (a foreign, missing or already removed asset: the same 404).
2. Lock the asset and its live objects (`select_for_update`).
3. For each live `MediaObject`: delete the key in the store. A store failure: 502 `media_not_removed`, **no `removed_at` is written**, the asset stays in the library, the merchant retries. An absent key is treated as removed (see assumption A1).
4. Set `removed_at = now` on every object of the asset and on the asset, in one transaction. Re-running changes nothing.

Rows stay forever. `removed_at` is written nowhere else (a test greps for it).

### 3.5 The cutoff sum

`apps/media_library/ledger.py`:

```
COUNTED_ROLES = ("original",)   # the L13 decision; section 14, decision 1

def bytes_at(site, cutoff, roles=None) -> int:
    # sum of bytes of this site's rows with added_at <= cutoff
    # and (removed_at is null or removed_at > cutoff), role in roles
```

It reads only `MediaObject`; it needs nothing from the store, from the asset table or from the clock. It returns an int, and 0 for a site with no rows. It is the only reader P6.2d needs. It has a second form `breakdown_at(site, cutoff)` returning bytes per role, for the HQ dry-run report P6.2d plans.

### 3.6 How G2's ledger feeds `record_storage_snapshot` (P6.2d, not G2)

G2 writes the ledger and offers `bytes_at`. It does **not** call billing, does not choose period ends, does not compute an overage and does not change `STORAGE_METERED`. P6.2d, per site and per allowance period it finds from `clock.allowance_periods`, will call:

```
usage.record_storage_snapshot(
    site,
    source_reference=f"storage:{site.public_id}:{period_end_at:%Y%m%dT%H%M%SZ}",  # 50 chars, limit 64
    period_end_at=period_end_at,       # from billing's clock, 3:00 PM Manila, aware
    measured_bytes=ledger.bytes_at(site, period_end_at),
    measured_at=period_end_at,         # billing demands measured_at == period_end_at
)
```

- `bytes_at` for the exact period end gives the same figure whenever the job runs (late runs up to 72 hours are admitted by billing, `usage.py` rule L2), because ledger rows are immutable except `removed_at`, and an object removed after the cutoff still counts at the cutoff.
- Billing's own switch order applies first: `disabled`, then `not_metered`. While `STORAGE_METERED` is False the call writes nothing, so even a wrongly scheduled producer cannot charge.
- Contract section 8 names the idempotency key `usage:<cycle pk>:storage:storage-ledger-v1`; the implemented intake takes a `source_reference` instead (`usage.py:104-116`), so P6.2d reconciles to the implemented signature, as the contract allows ("P6.2d reconciles to it").
- At or below 5 GB billing writes no row (`usage.py` L49), so a quiet library costs nothing.
- Orphans (objects with no row) are never in the sum; rows with an object not yet confirmed gone are (section 3.4).

## 4. API routes the dash needs

All under `/v1/sites/{site_id}/media/`, signed-in owner only, every id a public id, no key or row number in a response. Off (switch false): all three answer the usual 404.

| Method and path | Purpose | Success | Notable errors |
|---|---|---|---|
| `GET /media/` | The library, newest first, with `limit`/`cursor` paging as the other lists (`apps/common/pagination.py`). Body: `items[]` each `{id, label, url, content_type, bytes, created_at}`, and `usage: {used_bytes, cap_bytes, count, cap_count}`. `used_bytes` counts live rows of the **counted roles**; it is the figure the merchant sees, never a bill | 200 | 404 foreign/missing site; 403 `billing_only` |
| `POST /media/` | Multipart: `file` (required), `label` (optional, at most 80). Returns the new item | 201 | 400 `invalid_request` with `fields.file` ("Upload a JPEG, PNG or WebP image." / "The image is too large. The limit is 5 MB."); 409 `media_library_full` (count or bytes cap); 429; 503 `media_store_not_configured`; 502 `media_not_saved` |
| `DELETE /media/{media_id}/` | Remove the item and its stored objects | 200 with the removed item's id | 404 (foreign, missing, already removed: the same answer); 502 `media_not_removed` |

`url` is `url_for(key)`: the public URL of a public-by-design file (the catalog photo is the same). The key is never sent on its own. The dash must say plainly that uploads are public.

No rename route in version 1 (`label` is set at upload). No attach-to-product route: using media on a storefront is a later task and nothing in G2 reads an asset from a storefront.

Frontend (separate repo, section 12): replace the placeholder at `index.html:129-139` with the library page (grid, usage bar, upload, delete, copy URL); the view stays hidden in the nav list (`nav.test.mjs:156,174`) until Coach Ed switches it on. It needs a mockup Coach Ed has seen (a design he has not seen is his; marching instruction).

## 5. Validation and limits (recommended values; Coach Ed approves, section 14 decision 2)

| Limit | Recommended | Reason |
|---|---|---|
| Allowed types | JPEG, PNG, WebP, by first bytes and by declared type | Exactly what `private_files.IMAGE_TYPES` and the catalog already allow. **No SVG** (script risk on a public host), no GIF, no PDF, **no video** (D6/D9: none at launch) |
| Max file size | 5 MB | Catalog photo is 2 MB; a phone photo is 2-5 MB. Bounded request memory: `check()` reads at most `max_bytes + 1` |
| Max live assets per site | 1,000 | Bounds the list and the check |
| Max live bytes per site (hard stop) | 5 GB (5 x 10^9) | Equals the allowance. While `STORAGE_METERED` is False nothing is billed, so an unbounded library is an unbounded platform cost. The cap is a constant; raising or removing it is the metering decision's job (decision 1) |
| Upload rate | 30 per account per 10 minutes | `throttle.hit`; stops scripted filling |
| Label | at most 80 characters, control characters refused | Shown escaped |
| Renditions (when built) | none in version 1; later at most 3 per asset, longest edge 1600 px, each no larger than its original, spec from a fixed list | Keeps platform overhead bounded and every derivative in the ledger |
| Image dimensions | not read in version 1 | No image library; adds nothing the ledger needs |

The quota check runs twice (before the store write, and under the site-row lock before the ledger write) so two simultaneous uploads cannot both pass the cap.

## 6. D4/Q3 exclusions

Excluded kinds: BIR seal, Terms PDF, subscriber photo, buyer proofs, QR codes, registration files, receipt images, system and support files. Also excluded here: the shared catalog product photo (platform catalog, not a merchant's business media).

How it is guaranteed, not just promised:
- `MediaObject` is written by one function, `ledger.record()`, called only by `services.upload`. Every excluded kind is written by other code (`apps/sites/seal.py`, `apps/payments/services.py`, `apps/pack/*`, `apps/shop/*`, `apps/billing/proofs.py`, `apps/invoices/scans.py`, `apps/statements/documents.py`, `apps/sante/agreement.py`, `apps/catalog/media.py`) into other folders and other stores (`docs/runbooks/storage.md:13-22`). None of them imports the ledger.
- The measurement reads only `MediaObject`, so billing cannot count what the ledger does not hold.
- The scheduled check lists `merchant-media/` only; it does not look at the private store at all.
- `docs/runbooks/storage.md` gains one row for the new kind (section 11), and `test_storage_ledger.py` already forces a row for the new `FOLDER`.

## 7. The scheduled check (branch B)

Contract: "a scheduled listing compares G2's own prefix against the ledger. It alerts and never changes a bill."

- A Celery task `media_library.audit` (beat, once a day), doing nothing while `MEDIA_LIBRARY_ENABLED` is off, listing the store under `merchant-media/` and comparing keys with live and recently removed `MediaObject.key`.
- Findings, each counted and alerted, none changing a row: **orphan** (object in the store, no ledger row), **ghost** (a live row whose object is gone). A ghost is **not** auto-removed: an operator confirms and a later change sets `removed_at`; the bill is never altered by the check (contract).
- Alert path: the existing Sentry capture and a security-log line (`apps/common/security_log.py`), counts only, never a key or a site.
- Needs the store's list-by-prefix call, which the current client does not have. Vercel Blob's list API is assumed (A2), unread in its docs; if the dedicated store is not Vercel Blob, the call changes with it.
- The check is its own branch so version 1 of the upload can ship and be tested without it; until it exists, orphans are possible but unbilled and unseen.

## 8. Retention and deletion

- A merchant deleting an asset: section 3.4.
- Site erasure: the executor (`apps/lifecycle/erase.py:147-190`, `SITE_ERASURE_ENABLED`, plan z8uq9mb22v) deletes the site's private files at its deletion point. Merchant media is a new file store of the site's own data, so it needs the same rule: `media_library.services.purge_site(site, now)` deletes every live object of the site and sets `removed_at`, rows kept, idempotent, an absent key counts as gone, a store failure raises `StoreError` like the other private-file steps. **Branch B adds `purge_site` and hooks it into `erase_item`** (one call beside the other `blank_*` steps, count key `media_files`, added to the item's `files_deleted` total). The hook runs only where erasure already runs; `SITE_ERASURE_ENABLED` is not touched. z8uq9mb22v has the same Supervisor and no open branch touches `erase.py`.
- Account disposal and the 30-day export: media is public data the merchant uploaded; the export (`export_account`) carries no files, and G2 does not change it. A line is added to `docs/runbooks/retention.md` stating this.
- A Billing-only (suspended or ended) site cannot upload or delete (`_owner_site`); its objects stay until the deletion point.

## 9. Switch, migration, settings (approvals asked)

- **Switch:** `MEDIA_LIBRARY_ENABLED` (env, default off, in `config/settings.py` beside the others). Off: the three routes answer 404, the audit task returns at once, nothing is read or written. A deploy check (`apps/media_library/checks.py`, like `apps/pack/checks.py`) errors `media.E001` when it is on and the store is not configured. Turning it on, anywhere, is Coach Ed's separate approval.
- **Store variables:** depend on decision 3: either the existing `PUBLIC_BLOB_READ_WRITE_TOKEN` and `PUBLIC_MEDIA_BASE_URL`, or a dedicated pair `MERCHANT_MEDIA_BLOB_READ_WRITE_TOKEN` and `MERCHANT_MEDIA_BASE_URL`. No variable is set by G2.
- **Exact migration** (one, named for approval): `apps/media_library/migrations/0001_initial.py`, creating `MediaAsset` and `MediaObject` as in section 3.1, with the four constraints and two indexes, and depending only on `sites.0001_initial` (the latest sites migration at planning time is `0003_site_business_profile_seal_key`; the dependency is the first migration that creates `Site`). No data migration, no backfill (there is nothing to backfill: no merchant media exists), no change to any existing table. Reversible. Coach Ed's standing approval (2026-11-11) lets a migration an approved plan specifies be written and run on staging by its Supervisor; production is never covered. A second migration is only needed if a rendition column beyond section 3.1 is later wanted; none is planned.
- `plan.STORAGE_METERED` stays `False`. Changing it is the metering decision after decision 1, not G2.

## 10. Tenant isolation and tests

Tests are in the repo's existing style (`TwoOwnersTestCase`, Ana and Ben).

- **Route inventory:** add the three routes to `OWNER_ROUTES` (`owner_base.py:60`), add `("/sites/{site_id}/media/", "media_library")` to `APP_PREFIXES` (`:140`), add `media_id` to `MISSING_CHILD` (`:43`), and update the recorded per-app counts that `test_each_route_is_listed_once_and_counts_per_app_are_as_recorded` checks. The existing walks then cover: a foreign site and a missing site answer the same bytes, nothing changes, every row of every model is unchanged (`row_snapshot` includes the two new tables automatically), and a foreign child id under my own site is the same 404.
- **No cross-site read/list/delete:** Ana uploads; Ben's `GET` lists none of Ana's items and none of her URLs; Ben's `DELETE` of Ana's `media_id` (under Ben's site and under Ana's site id) is the same 404 as a missing id and Ana's item, objects and `removed_at` are unchanged.
- **Not billed for another site's objects:** `bytes_at(ben_site, cutoff)` is 0 when only Ana uploaded; Ana's bytes never appear in Ben's `usage.used_bytes`; the key of every row starts with its own site's prefix (as `test_private_files.py` does for seals and QR codes), and two sites never share a prefix.
- **Key shape:** a key supplied by a request is never used; the response holds no key and no row number; the prefix has no row id.
- **Switch:** off, all three routes 404 for every tenant and nothing is written or stored (as `test_off_every_tenant_gets_404_and_nothing_changes`).
- **Billing-only site:** upload and delete answer 403 `billing_only` after the 404 check.
- **Exclusions:** with the harness fixtures that already create a payment QR code, a seal, a Pack proof, a shop proof, a billing proof and a catalog photo, `MediaObject.objects.count()` stays 0 and `bytes_at` is 0 for both sites. A source scan fails if `MediaObject(` / `MediaObject.objects.create` / `bulk_create` appears outside `ledger.py` and tests, and if `removed_at` is assigned outside `ledger.py`.
- **Audit prefix test (branch B):** a fake store listing that holds `sante/agreement/<hex>.pdf`, `billing/proofs/...`, `sites/seal/site-1/...`, `catalog/products/...` and one `merchant-media/site-xxx/...` object: only the last is considered; the others are neither orphans nor billed.

## 11. Docs

- `docs/runbooks/storage.md`: one new row, "Merchant media (behind `MEDIA_LIBRARY_ENABLED`)", store per decision 3, folder `merchant-media/site-<id suffix>/`, readers (anyone, public URL; owner list/upload/delete routes), retention ("removed by the owner; removed at site erasure by `purge_site`"). Required by `test_storage_ledger.py`.
- `docs/runbooks/billing.md`: the usage paragraph gains one sentence naming `ledger.bytes_at` as the storage source.
- `docs/runbooks/retention.md`: the merchant-media rule (section 8).
- A short `docs/runbooks/media-library.md` is not planned; the three rows above are enough (no extra documentation files).

## 12. Branches (PR count)

Three branches, no PR opened by the builder; the release train combines them (CLAUDE.md, builder brief section 2). In the release rounds that is **at most two PRs** (one backend, one frontend), and one backend PR if A and B are ready together.

| Branch | Repo | Contents | Switchable alone? |
|---|---|---|---|
| A `feat/z8uq9mb384-media-ledger-upload` | backend | `apps/media_library` (models, `0001_initial`, `ledger`, `services` upload/remove, `api`, `constants`, `checks`), `folder` argument added to the public store client (a backward-compatible parameter on `apps/catalog/media.py`, default unchanged), `MEDIA_LIBRARY_ENABLED`, router mount in `config/api.py`, harness updates, storage and billing runbook rows | yes: off by default |
| B `feat/z8uq9mb384-media-audit-erase` | backend | list-by-prefix store call, `media_library.audit` task and beat entry, `purge_site` and its hook in `apps/lifecycle/erase.py` (`media_files`), retention runbook line, tests. Depends on A | yes: same switch, audit does nothing while off |
| C `feat/z8uq9mb384-dash-media-library` | frontend | the `/media-library/` page against the three routes, behind the existing hidden-nav state | yes: hidden until the switch is on |

More than 3 to 4 was avoided: the audit and the erasure share a branch because both need the store's list/remove calls and both are about objects not covered by a request; A is the smallest slice that stores, bills nothing and can be tested end to end.

## 13. Acceptance tests per branch

**A**
1. Upload a JPEG: one `MediaAsset`, one `MediaObject` (`role=original`, `bytes == len(file)`, key under `merchant-media/site-<suffix>/`, `added_at` set, `removed_at` null); the response has no key.
2. Wrong type (SVG, GIF, PDF, a PNG declared as JPEG), over 5 MB, no file: 400 with `fields.file`, nothing stored (fake store empty), no row.
3. Count cap and byte cap: the 1,001st asset and an upload that would pass 5 GB answer 409 and store nothing; two uploads racing for the last slot: exactly one wins.
4. Store put fails: 502, no row. Ledger write fails after the put: the object is discarded.
5. Delete: object removed from the fake store, `removed_at` set on object and asset, asset gone from the list; delete again: 404. Store delete fails: 502, **`removed_at` still null**, row still counts, a second try succeeds.
6. **Cutoff sum, reproducible from the ledger alone:** seed rows with chosen `added_at`/`removed_at` and assert `bytes_at` at an instant before an add, exactly at `added_at` (counted), between add and remove, exactly at `removed_at` (not counted, the contract's `removed_at > cutoff`), after remove; the same answer after the assets table is emptied of labels and after the store is wiped; unaffected by later uploads and removals; role filter: a `rendition` row is excluded under `COUNTED_ROLES = ("original",)` and included when roles are passed.
7. Feed: with `plan.STORAGE_METERED` patched True and `BILLING_USAGE_ENABLED` on, `record_storage_snapshot(site, source_reference=..., measured_bytes=bytes_at(...), ...)` is accepted with the reference at its 64-character limit; with `STORAGE_METERED = False` it answers `not_metered` and writes nothing. (A test of the call shape only; G2 contains no producer.)
8. Switch off: 404 on all routes; no store call. Deploy check `media.E001` when on and unconfigured.
9. Migration check: `makemigrations --check` clean; the migration applies on a fresh PostgreSQL and on SQLite; the four constraints refuse a negative size, a `removed_at` before `added_at`, a duplicate (asset, role, spec) and an original with a spec.
10. Isolation and exclusion tests of section 10; `test_storage_ledger.py` passes with the new row.
11. Existing catalog photo tests unchanged and green (the store client change is backward compatible).

**B**
1. Audit: an orphan and a ghost are each counted and alerted; no row changes; a ledger row with no object still counts in `bytes_at`; a prefix-only listing ignores keys outside `merchant-media/`.
2. Switch off: the task returns at once and calls the store zero times.
3. `purge_site`: removes every live object of the site and sets `removed_at`; another site's objects untouched; run twice, second counts zero; an absent key counts as gone; a store failure raises and leaves `removed_at` null for the objects not yet removed. The hook: an erased site's live media objects are removed and `removed_at` set, its `erase_item` counts include `media_files`, another site's objects are untouched, and `SITE_ERASURE_ENABLED` is not read or changed by the new code.
4. The beat entry is registered and expires like its neighbours.

**C** (frontend repo's own checks: install frozen, lint, tests, build for the dash; the page renders the list and usage, uploads and deletes against a stub, shows the 409 and 400 messages and the "uploads are public" notice, and stays hidden from the nav.)

## 14. Open decisions for Coach Ed

Each: the recommendation, the reason, the risk. None is decided here. Nothing is metered or charged meanwhile.

1. **How platform renditions count against the 5 GB allowance (L13).**
   - Recommendation: **platform renditions do not count; only the merchant's own uploaded originals count** (`COUNTED_ROLES = ("original",)`), and version 1 creates no renditions at all.
   - Reason: the merchant is charged for what they chose to store, not for platform-made copies they cannot see or delete; the ledger still records every rendition with its own row and `bytes`, so the decision can be reversed later by changing one constant and recomputing any future snapshot (a past one is never recomputed, by design).
   - Risk: the platform pays for uncounted bytes. It is bounded by the rendition limits in section 5 (at most 3 per asset, none larger than its original) and, in version 1, is zero. The alternative (count all) makes the merchant's bill depend on how many sizes the platform decides to make. Also decide, with the same answer: the hard stop cap while unmetered (section 5).
2. **The limits (section 5).**
   - Recommendation: JPEG, PNG, WebP only; 5 MB per file; 1,000 assets and 5 GB per site as hard stops while storage is unmetered; 30 uploads per 10 minutes per account; no video, no SVG, no GIF, no PDF.
   - Reason: matches existing upload rules, bounds the platform's cost when nothing is charged, and keeps public-host risk low.
   - Risk: a merchant with many large originals hits 5 GB before any overage can apply, so overage billing cannot happen until the cap is raised on the day storage is switched on; too low a file size forces merchants to shrink photos.
3. **The storage provider and bucket for merchant media.**
   - Recommendation: **a dedicated public Vercel Blob store** (new pair `MERCHANT_MEDIA_BLOB_READ_WRITE_TOKEN` and `MERCHANT_MEDIA_BASE_URL`), reusing the existing Blob client and key rules; keep the per-site prefix inside it. Not R2.
   - Reason: the backend has no R2 client and no R2 account in any code (`storage.md:5`); the public Vercel store is not configured on staging either, so a store must be created whichever way; a separate store keeps the shared catalog photo's token away from merchant data and makes "list the store" equal "list merchant media" for the audit and for any later bulk export or purge.
   - Risk: one more token for Coach Ed to create and set (a credential action, his), and one more Vercel Blob bill line. Choosing the existing public store instead saves the token but mixes catalog and merchant objects under one credential. R2 would need a new S3-style client dependency and Cloudflare setup, more code and review; the contract's "R2 listing" wording is legacy (TWCinc).
4. **Image library for renditions.** Recommendation: **do not add one now**; version 1 is originals only. Reason: Pillow is a new dependency that decodes untrusted uploads (a security surface) and nothing in the Santé MVP storefront yet needs a stored rendition. Risk: pages show full-size originals until a later task (a 5 MB cap bounds the worst case).
5. **The switch and the migration.** Recommendation: approve the name `MEDIA_LIBRARY_ENABLED` (default off) and the single migration `media_library.0001_initial`, on the standing migration approval path (written by the builder, run on staging by the Supervisor, production never). Risk: low; two new empty tables, reversible.

## 15. Choices made without asking (what, why, source)

- Public store and public URLs for merchant media, not the private store: the dash library exists to put images on a storefront, and the catalog photo is public by the same reasoning (`apps/catalog/media.py:1-18`).
- Key prefix from the site's random public-id suffix, not `site-<pk>`: the URL is public; the row number would leak and be guessable (`apps/common/ids.py:6-14`).
- Delete removes the object, then sets `removed_at`: the contract's orphan rule (row with an object counts until confirmed gone), contract section 5.
- Label optional and the file name not stored: less client data kept; nothing in the task needs it.
- No rename and no attach routes in version 1: not in the task's scope.
- Erasure hook in branch B; z8uq9mb22v has the same Supervisor, routine decision.
- Idempotency via `source_reference`, not the contract's `usage:<cycle pk>:storage:storage-ledger-v1`: the implemented intake takes `source_reference` (`apps/billing/usage.py:104-116`); the contract allows P6.2d to reconcile.
- The task's "`negosyoonline_platform/isolation.py`" and "`test_isolation.py:46`" and "`store_bytes`" and "R2" do not match this repo; the plan uses the real equivalents (section 2) and treats them as legacy wording.

## 16. Assumptions to verify at build (cannot be read here)

- A1: deleting an already-absent key from the Vercel Blob store answers success (the catalog's `delete` is only called for keys it stored). Verify on a throwaway store before branch A is reported ready; if it errors, map "not found" to success in the client.
- A2: Vercel Blob offers a list call by prefix with paging. Verify in its documentation before branch B.
- A3: P6.2d's task id z8uq9mb38y, and its plan, are not read here; section 3.6 states only what the code and contract require of a producer.

## 17. Out of scope

Storage measurement and the producer job (P6.2d), turning on `STORAGE_METERED` or `BILLING_USAGE_ENABLED`, any charge, video, SVG and GIF, product or storefront use of a library image, bulk export of media, image editing, a Django admin screen (none; staff view would go in the HQ dashboard if ever needed), production deployment.
