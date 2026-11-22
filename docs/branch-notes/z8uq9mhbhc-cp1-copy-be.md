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

- Rebased onto origin/main cd34f47 (release #198, SMS-OWNER). Owner routes now 152 (inquiries 31, training 28, shared_content 2). Full suite 5480 tests: SQLite OK (skipped=71), PostgreSQL+Redis OK (skipped=1); migrations check clean; check --deploy shows only the two deferred HSTS warnings.

# R2 fix round 1: a lesson completes by watching (Coach Ed, 22 Nov: "the marking of complete is done by watching not manual", unlock after 90% watched)
TWCinc source paths (read only, adapted, nothing imported): `app/university/views/course.py` (`lesson_complete`: completion comes from the player's 90%/ended signal, re-checks the lesson is unlocked, returns next_unlocked), `app/university/services/progress.py` (`is_sequence_unlocked`, `build_unlock_map`: a lesson opens when its predecessor is complete), `app/static/dashboard/subscriber/js/lesson-watch.js` (`COMPLETE_AT = 0.90`, first crossing or `ended`), `app/university/templates/university/course/lesson.html` (FE wording). Migration: none.

## API changes
- `POST /storefront/v1/member/training/lessons/{lesson_id}/watch/` takes `{host, token, percent, ended?}` (`ended` is a strict boolean, default false; a non-boolean is 400). It answers `{percent, completed, next_unlocked, next_lesson_id}`. The stored percent is still max-only. In the same transaction, when the stored percent is >= 90 or `ended` is true, and the lesson completes by watching, the member's `completed_at` is set (once, never undone, a later lower post leaves it). `completed` is the lesson's completion. `next_unlocked` = completed and a next lesson exists; `next_lesson_id` is that lesson's id, else null. A locked lesson is still 409 `lesson_locked`.
- `POST .../lessons/{lesson_id}/complete/` is kept for old clients and refuses every lesson: 403 `complete_by_watching` (a foreign or missing lesson is still the usual 404). 403 because TWCinc answers 403 to a completion that is not allowed and has no manual path at all; the route shape stays so an old client gets a clean 4xx, never a 500. It writes nothing.
- Owner `GET .../courses/{course_id}/watch/` is unchanged in shape: `completed` reads the same `completed_at` record, so a member who reached 90 shows completed with their furthest percent.

## The one rule
`apps/training/video.py: completes_by_watching(lesson)` is the only place that says which lessons can complete: a lesson with a READY Stream video. A lesson with no video, a legacy YouTube/Vimeo link, or a Stream video still processing never completes, so the lessons after it stay locked (TWCinc literal; the Supervisor reports this to the Manager). Change that one function if Coach Ed rules otherwise.

## Tests
Watch post (89 no, 90 yes, ended below 90, never undone, last lesson, legacy/none/processing never complete, locked 409, strict `ended`), manual route 403 for every lesson and 404 foreign, owner view completed. The sequence tests write the completion record directly (the old manual route is gone). Tenant-isolation registry: no route added or removed, counts unchanged (owner 152).

# R2 fix round 2: video required (Coach Ed, 22 Nov: "all lessons has video... so it must be trackable. make the video required in our lessons")
TWCinc source: none used. Migration: none (no model change; `needs_video` the column is no longer read for output, it stays for the copy code).

## The rule
A course is shown to members (`Course.active`, the dash's "Show to members") only when every ACTIVE lesson of every ACTIVE module has a ready Stream video (`video.has_ready_stream`). Legacy links, pending, processing and failed videos do not count. Disabled lessons and modules are not counted. Where: `apps/training/video.py` (`lessons_missing_video`, `require_course_videos`, `video_required`).

## Refusal (every enforcing route)
HTTP 400, body (shape set by the Supervisor, 22 Nov):
`{"code": "video_required", "detail": "Every lesson needs a video before members can see this course.", "lessons": [{"id": "lsn_...", "title": "..."}], "error": {"code": "video_required", "message": "<same text>"}}`
The extra `error` envelope is only so a client reading the API's standard shape also works; `ApiError(body=...)` in `apps/common/errors.py` carries a ready body. Routes that enforce it (all owner, same auth):
- `PATCH /sites/{site}/training/courses/{id}/` with `active: true` on a course that is off (also the unarchive-by-switching-on path). Turning off, and any other edit, are never blocked. `POST .../courses/` creates an empty course, so `active: true` there is allowed (no lessons yet).
- `PATCH .../modules/{id}/` `active: true` on a module that is off, inside a published course: its active lessons must all have a video.
- `PATCH .../lessons/{id}/` `active: true` on a lesson that is off, inside a published course whose module is on: it must have a video.
- Copy-publish: a copy arrives Off and its only way on is the course PATCH above, so it is blocked until every lesson has a video. Copied lessons that only had a legacy link keep `video_url` untouched. Nothing else sets `Course.active` (checked: content_copy, services, API).

## Edge cases (choice and why)
- Adding a lesson/module to an already published course, or removing a video from one: allowed, the course is NOT unpublished and nothing is deleted. The lesson reads `needs_video: true` (lesson and `video.needs_video`) and the course tree reads `needs_video_count`, so the dash can flag it. Reason: auto-unpublishing would pull a live course from paying members mid-lesson; the member side already cannot complete a lesson without a video (R2 fix 1), so the lesson just waits. Re-publishing after turning it off is checked again.
- `needs_video` is now computed, not the stored column: true when there is no ready Stream video and none uploading/processing (so legacy-link, failed and empty lessons read true, a video on its way reads false).
- Turning a lesson/module back on is checked only in a published course (in an unpublished course the publish check covers it).

## Commands (read-only unless said; never run by the builder outside tests)
- `manage.py training_video_audit [--site site_.../subdomain/own domain]` writes nothing. Tab-separated columns: store, course, course_published, module, lesson, video_kind, library_id, video_guid. video_kind: stream-ready, stream-pending, bunny-embed-link, youtube, vimeo, other-link, none. Sample:
```
store	course	course_published	module	lesson	video_kind	library_id	video_guid
site_ab12 (Live4More)	Ways to Earn	yes	Module 1	Welcome	stream-ready	816560	3f0c...-guid
site_ab12 (Live4More)	Ways to Earn	yes	Module 1	Old intro	bunny-embed-link	816560	9a1b2c3d-1111-2222-3333-444455556666
site_ab12 (Live4More)	Starter	no	Basics	Intro	youtube		
site_ab12 (Live4More)	Starter	no	Basics	Draft	none		
```
- `manage.py training_stream_backfill --site <site> [--apply]`: dry run by default (prints "would set ..." lines, makes no Bunny call, writes nothing). Candidates: lessons with no stream or pending video whose `video_url` is a Bunny embed/play link whose library id equals the store's StreamLibrary `library_id` (other libraries, YouTube, other stores: untouched). `--apply` asks Bunny (Get Video) with the store's own key: ready -> sets stream library/video id, status `ready`, size, so the publish rule is met at once; Bunny says processing/uploading/failed -> that status; no answer or no key -> `processing`, and the dash's normal refresh (GET .../video/) settles it. `video_url` is never touched, nothing is deleted, the legacy link keeps playing until the Stream video is ready. No audit entry: the training app has no audit log (the printed lines are the record).

## Staging shell snippet (read-only, current staging schema = origin/main cd34f47, no Stream fields)
Run in `manage.py shell` on staging, before this release:
```python
import re
from apps.training.models import Lesson
rx = re.compile(r"mediadelivery\.net/(?:embed|play)/(\d+)/([0-9a-fA-F-]+)")
def kind(u):
    if not u: return "none"
    m = rx.search(u)
    if m: return "bunny-embed-link"
    if "youtu" in u: return "youtube"
    if "vimeo" in u: return "vimeo"
    return "other-link"
print("store\tcourse\tcourse_published\tmodule\tlesson\tvideo_kind\tlibrary_id\tvideo_guid")
for l in Lesson.objects.select_related("module__course__site").order_by("module__course__site__public_id", "module__course__order", "module__order", "order", "pk"):
    c = l.module.course
    m = rx.search(l.video_url or "")
    print("\t".join([c.site.public_id, c.title, "yes" if c.active and not c.archived_at else "no", l.module.title, l.title,
                     kind(l.video_url), m.group(1) if m else "", m.group(2) if m else ""]))
```

## Tests
`apps/training/tests/test_video_required.py` (refusal shape on every route, legacy/pending/failed do not count, disabled not counted, turn-off never blocked, edge cases, copy arrives Off and keeps its link, link parsing, audit columns and writes nothing, backfill dry run / apply / not-ready / already-set / other store / no library). Tenant-isolation registry: no route added or removed, counts unchanged (owner 152).
Full suite 5512 tests: SQLite OK (skipped=71), PostgreSQL 14 + Redis OK (skipped=1); `check --deploy` shows only the two deferred HSTS warnings; `makemigrations --check --dry-run` clean.
