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

# R2 (Bunny-only lesson video, per-store library, watch progress)
Contract: `docs/session-state/packets/cp1-copy-preview/r2-builder-contract.md` (v2). TWCinc source: none used. Migration: `training/0003_cp1_r2_bunny_stream` (additive).

## Built
- `apps/training/stream.py`: the one Stream client. Every call takes a `Library(id, cdn_host, key)`; global settings are only `BUNNY_STREAM_API_BASE` and `BUNNY_STREAM_EMBED_BASE` (plus `BUNNY_STREAM_CDN_SCHEME`, https, "http" only for the local stand-in). Create/Get/Delete video, PUT bytes, TUS signature `sha256(library_id + key + expires + video_id)` (endpoint `{API_BASE}/tusupload`), source download from the library's CDN host (`/{guid}/original`, else `/play_720p.mp4`, no key sent). The key goes only into the AccessKey header and the hash; errors carry a status only.
- `StreamLibrary` (one per site: `library_id` unique, `cdn_host`, `key_ref`). The key is read from env `BUNNY_STREAM_KEY_<KEY_REF>` (`key_ref` = `[A-Z0-9_]{1,40}`). Staff command `manage.py stream_library set --site site_.. --library-id N --cdn-host H --key-ref REF` / `show [--site ..]` (prints everything except the key, and whether the variable is present). No audit entry is written (the contract specifies none); add one if wanted.
- Lesson: `stream_library_id`, `stream_video_id`, `stream_status` ("" | uploading | processing | ready | failed), plus `stream_bytes` (Bunny storageSize once ready, for the copy list's bytes) and `stream_pending_*` (3 columns: a replacement upload waits there while the lesson keeps playing; it takes over when ready and the old Stream video goes through the guard). `LessonProgress.watched_percent` (0-100, CHECK) and `watched_at`.
- `video_url` can be cleared (null or "") but never set: any other value is 400 `fields.video_url = ["Upload your video instead."]` (create and patch). A ready Stream video wins over a legacy link; `video_url` is left untouched (Supervisor ruling, routine).
- Unconfigured store (no row, or its variable missing): upload answers 503 `video_not_available` "Video uploads are not available yet."; other stores are unaffected.

## API
Owner (same auth as the rest of training):
- `POST /sites/{site_id}/training/lessons/{lesson_id}/video/upload/` -> 201 `{upload: {endpoint, library_id, video_id, signature, expires (unix seconds)}, video}`
- `GET  .../lessons/{lesson_id}/video/` -> `{video}` (asks Bunny while uploading/processing: 4 ready, 5/6 failed, 1-3 processing)
- `DELETE .../lessons/{lesson_id}/video/` -> `{video}` (current and pending video removed through the guard)
- `GET  .../courses/{course_id}/watch/` -> `{course_id, lessons: [{lesson_id, title, module_id, module_title, members: [{member_id, name, percent, completed}]}]}` (the site's own members only)
- `video` = `{kind: "stream"|"legacy"|"none", status, embed_url, needs_video}`; `embed_url` = `{EMBED_BASE}/{lesson library id}/{guid}` only when ready. Also on every owner lesson read and on the member lesson read (member `video_url` is null once a Stream video is ready). Responses never carry the key or key_ref.
- Errors: 502 `video_failed` when Bunny does not answer.
Member: `POST /storefront/v1/member/training/lessons/{lesson_id}/watch/` `{host, token, percent}` -> 200 `{percent}` (stored maximum; 0-100 whole number else 400; locked lesson 409; 60/min per member then 429; never changes `completed_at`).

## Delete guard (training/video.py)
A Stream video is deleted only when no lesson of any site names the pair (current or pending), with the key of the library it lives in, after commit, never raising. Used by delete lesson/module/course, remove, replace, and the failed-copy clean-up.

## CP-1 copy change
A lesson with a ready Stream video in the source library: Create Video in the TARGET store's own library, the Celery task downloads the source from the source library's CDN host and PUTs it in (Fetch Video not used). The item stays Copying (`target_public_id` set on the row, course inactive) until `sante.poll_video_copy` (every 20 s) sees every new video ready -> Copied; a failed video or age over STALE_AFTER removes the course and its videos and reads failed. STALE_AFTER raised 30 min -> 2 h (download, upload and transcode). Target not configured: the course is refused (503 if nothing else is queued, else listed in `skipped` with `reason: "video_unavailable"`). Source library not configured: that lesson arrives needs_video. YouTube/Vimeo/744094/no video: needs_video, links no longer copied. The list's course `bytes` = sum of `stream_bytes` of ready lessons.

## Not covered / notes
- Disposal of a whole site cascades its lessons but does not delete their Stream videos (no hook added).
- Real Bunny behaviour (TUS headers, CDN `original`/MP4 availability, token-protected pull zones) is unverified; the staging check proves it.
- Local stand-in: `docs/session-state/packets/cp1-copy-preview/stream-standin.py` (per-library keys, TUS, CDN, embed with player.js).

## Plan only (item 10): automatic library creation (not built)
- **When:** the first video upload or copy of a store (not at sign-up), so stores that never use video cost nothing. Until Coach Ed approves, libraries are created by hand and set with `stream_library set`.
- **Name:** the store's username, as Ed did for Live4More ("Live4More").
- **How:** Bunny's account-level API, `POST https://api.bunny.net/videolibrary` with the account AccessKey, returns the library id, its API key and its pull zone (the CDN host). A staff-only job would write the StreamLibrary row (id, `<pull zone>.b-cdn.net`, `key_ref` derived from the username, uppercased).
- **Key storage without chat or docs:** the key returned by Bunny must be placed in a variable `BUNNY_STREAM_KEY_<REF>` by a path that does not print it. Options for Ed to choose: (a) a one-off script run by his own hands that calls the account API and writes the variable through the hosting provider's own variable API, never printing it; (b) an encrypted key column (Fernet, like the Meta/Xendit credential keys already used) read at use, which removes the per-store variable but puts a decryptable key in the database. Recommendation: (b) for automation, (a) while stores are few.
- **Open questions for Ed:** custody of the account-level key (it can create and delete every library: held only by the staff job, never by a store owner); per-library cost (is there a base fee per library, or only storage and bandwidth); replication regions per library; library settings "Keep original files" and the MP4 fallback (the copy needs `/original` or `/play_720p.mp4` on the CDN host); whether a library should be deleted when a store is closed. The account key and any cost go to Ed first.
