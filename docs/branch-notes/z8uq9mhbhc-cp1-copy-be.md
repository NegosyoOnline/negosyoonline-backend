# CP-1 BE (z8uq9mhbhc): copy shared Live4More courses and media

Branch `feat/z8uq9m92fv-cp1-copy-be`. Spec: plan sections C1-C4 and Storage counting. TWCinc source: none used.

## What was built
- `SanteContentSource` (pk 1 singleton, like the catalog owner), `services.set_content_source`, audit kind `sante_content_source_set` (KEPT), command
  `manage.py sante_content_source --operator STAFF (--site site_... | --clear)`. The source site is kept from disposal (`refuses_disposal`).
- `SanteContentCopy` (site, kind, source_public_id, state copying|copied|failed, target_public_id, requested_at, finished_at, error). The newest row per (site, kind, source id) is the item's state. A "copied" row whose target no longer exists reads `not_copied`. A row still `copying` after 30 minutes reads `failed`.
- `Course.shared`, `Course.copied_from`, `Lesson.needs_video`, `MediaAsset.shared`, `MediaAsset.copied_from`. Migrations: `sante/0003_cp1_copy_content`, `training/0002_cp1_copy_content`, `media_library/0002_cp1_copy_content` (additive only).
- `apps/sante/content_copy.py` (logic), `apps/sante/tasks.py` (Celery `sante.copy_content`), `apps/sante/api_shared.py` (routes), `media.copy(src_key, dest_key)` in `apps/catalog/media.py`
  (download the public file and put it under the new key; Vercel Blob server-side copy was not used because its API was not confirmed).
- Delete guard `apps/common/file_refs.py: discard_if_unreferenced(key)`: used by media_library `store.discard` and every discard in training services. Add a new key column to `FIELDS` there.

## API (owner session, same gates as training: 404 foreign/missing site, 403 billing-only, 404 while MEMBERS_ENABLED is off)

### GET /v1/sites/{site_id}/shared-content/ -> 200
```json
{"is_source": false, "source_name": "Live4More",
 "courses": [{"id": "crs_...", "title": "Ways to Earn", "image_url": "https://.../x.png", "module_count": 3,
              "lesson_count": 12, "bytes": 0, "copy_state": "not_copied", "copy_id": null}],
 "media":   [{"id": "mda_...", "title": "Promo", "asset_type": "video", "thumb_url": "https://...", "bytes": 1000,
              "copy_state": "copied", "copy_id": "mda_..."}]}
```
- `copy_state`: "not_copied" | "copying" | "copied" | "failed". `copy_id` is this store's public id of the copy when copied, else null.
- On the source store: `{"is_source": true, "source_name": "Live4More", "courses": [], "media": []}`.
- No source set, or a non-Santé site: `{"is_source": false, "source_name": "", "courses": [], "media": []}`.
- Offered: courses active + not archived + shared; media active + published (not in the future) + shared. Module and lesson counts are the active ones (what a copy brings).
- `bytes`: media = `file_size` of the file; a course reports 0 (course and lesson pictures store no size).

### POST /v1/sites/{site_id}/shared-content/copy/ -> 202
Body: `{"kind": "course"|"media", "ids": ["..."]}` or `{"kind": "course", "all": true}`, optional `"again": true`. Unknown keys are 400.
```json
{"queued":  [{"kind": "course", "id": "crs_<source id>", "copy_state": "copying"}],
 "skipped": [{"kind": "course", "id": "crs_<source id>", "reason": "copied"}]}
```
- `reason` is "copied" or "copying". `all` skips items already copied. Explicit ids already copied are skipped unless `again: true`, which makes a new separate copy. An item that is `copying` is always skipped.
- 400 `invalid_request` with `fields`: bad `kind`; no `ids` and no `all`; more than 100 ids; `ids` not offered (unknown, unshared, another store's, or the caller is the source store, or no source is set). Nothing is queued then.
- The files are copied later by the task. Poll GET: `copying`, then `copied` (with `copy_id`) or `failed`.
- A copy arrives **switched off** (course `active=false`) or **unpublished** (media `published_at` null, `active` true). Only active modules and lessons are copied.
- A lesson whose video is a Bunny link (iframe.mediadelivery.net) is copied with `video_url` empty and `needs_video` true. Setting a video on the lesson clears it. YouTube and Vimeo links are copied.

### Owner PATCH changes
- `PATCH .../training/courses/{id}/` and `PATCH .../media/{id}/` accept `{"shared": bool}`. Only the content source store may send it; any other store gets 400 `invalid_request` with `fields.shared`. An archived course cannot be shared (400, also when `archived: true` is sent with it).
- Read fields added: course (list, detail, create/patch) `shared`, `copied_from` (source public id or null); media owner out `shared`, `copied_from`; lesson owner out `needs_video`.

## Files and storage
Every stored file (media file, thumb, poster, course image, lesson images) gets a new key for the target (`media-library/<target site>/<new asset>/<random><ext>` or `training/<random><ext>`). `file_size` is copied as is; rows are written like an upload (own site, no exempt or platform marker). No billing code changed. A failed copy discards the files it stored and writes no row.

## Not done / notes
- Staging and production untouched. Migrations not run on staging.
- Live trainings, announcements and member progress are not copied.
