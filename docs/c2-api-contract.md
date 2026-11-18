# C2 comment auto-reply rules: DESIGN DRAFT (task z8uq9mg9pj) - NOT a final contract

Status: WIP draft written when ownership moved to a new COMMENTS Supervisor. No code is written yet.
Nothing here is approved by C4; the contract has NOT been posted as final. Treat it as the starting plan.

## Findings from reading origin/main (78b35b2, includes C1)
- Migration 0006 already exists, so C2's migration is `0007`. C1 already added: `InquiryCommentAction.source=rule`,
  `state=queued/cancelled/blocked_rate`, constraint `inquiry_comment_action_auto_once` (comment+kind, source=rule),
  `comments.cancel_queued()` (called on `remove`), `InquiryCommentSettings` (one row per site, no automation fields yet).
- Reusable: `comments.readiness/_reasons/_store_page_reply/find`, `messenger.reply_comment/private_reply`,
  `limiter.acquire`, `crypto.decrypt`, `outcomes`. Webhook hook point: `comments._store` after a NEW comment is created
  (edits/duplicates return earlier, so edits never retrigger).
- Meta rate-limit mapping: `messenger.map_error` -> `RATE_LIMITED` for codes 4, 9, 17, 32, 613 and HTTP 429. Spam code 368
  currently maps to `bad_response`; decide whether to map it to `rate_limited` (one line in messenger.py, overlaps the
  deauthorize fix branch) - OPEN QUESTION.
- Celery: `config/celery.py`, tasks in `apps/inquiries/tasks.py` (`@shared_task`), eager when no REDIS_URL (countdown ignored in eager mode).
- Timezone Asia/Manila (use for "today").

## Planned model/migration 0007 (additive)
- `InquiryCommentRule`: site, ref (`cr_`+24 hex), enabled, trigger (any|keywords), keywords (JSON list), scope (all|selected),
  post_ids (JSON list of Meta post ids of this site's InquiryPost), public_text, private_text (blank = none), created/updated.
- `InquiryCommentSettings` + fields: automation_enabled (default False), paused_until, pause_reason ("" | rate_limit | repeated_limits),
  strikes_date, strikes.
- `InquiryCommentAction` + fields: rule (FK null, SET_NULL), variant_hash (char 16), send_after (datetime null), skip_reason (char 24);
  new state `limit` ("Not auto-answered (limit)") (state choices change only).

## Planned modules (new files, hooks only in existing ones)
- `apps/inquiries/comment_rules.py` (spintax parse/validate/render, link check, keyword match, settings module constants),
  `comment_automation.py` (evaluate + send, caps, back-off), `api_comment_rules.py` (router), tasks appended to `tasks.py`,
  one hook line in `comments._store`, router registered in `config/api.py`.
- Caps in one settings module: 30/hour, 300/day per Page (each kind counted, reserved at queue time under a row lock on the
  settings row), 1 per person per post per 24 h, delay 30-120 s, pause 1 h, 3 strikes/day -> owner must re-enable,
  max 20 rules, 500/1000 chars, 7 days. Switch `INQUIRY_COMMENT_AUTOMATION_ENABLED` (default off; off => routes 404, no tasks send).

## Planned API (owner routes, under /v1/sites/{site_id}/inquiries/comment-rules/, same auth as comments; off => 404)
- GET  `/`            -> {automation, rules[]}
- PUT  `/automation/` {enabled: bool} -> automation (enabling clears a pause / needs-reenable)
- GET  `/posts/?q=`   -> {items:[{id,is_ad,caption,permalink}]} (picker for selected posts)
- POST `/preview/`    {template, kind} -> {valid, error?, variant_count, samples[]}
- POST `/` create, GET/PUT/DELETE `/{ref}/`; rule: {ref, enabled, trigger, keywords, scope, post_ids, posts[], public_text, private_text,
  public_variants, private_variants, created_at, updated_at}; errors 422 `invalid` with `fields`, 409 `too_many_rules`, 404.
- automation: {enabled, active, ready, reason, banner: null | {code: paused_rate_limit|needs_reenable, message, paused_until?},
  today:{count,cap}, hour:{count,cap}, limits{...}}.
- Spintax: `{a|b}` groups, `{name}` token allowed anywhere, no nesting, no empty option, no unknown `{x}`; public needs >= 2 variants,
  no links (urls, www., common TLDs); worst-case length (first name capped at 30 chars) <= 500 / 1000.
- Detail actions of a comment gain `skip_reason` for `limit` rows.

## Behaviour plan
Evaluate (Celery, never in webhook): switches on, site enabled and not paused, comment top-level/not Page/not hidden/removed/<7 days/
no Page reply yet/has author id, first matching enabled rule (by id), per-person-per-post 24 h, caps -> create QUEUED rows
(unique DB guard) and `apply_async(countdown=random 30-120)`. Send: re-check switches/pause/rule/comment state, claim queued->sending,
render (no same variant twice in a row per post per kind), send via existing messenger calls, rate-limit -> pause 1 h + strike,
3rd strike in a day -> master off with banner `needs_reenable`. Remove/rule delete or disable cancels queued rows.
