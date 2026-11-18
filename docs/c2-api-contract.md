# Facebook comment auto-reply rules API, C2 (task z8uq9mg9pj)

Contract for Settings > Inbox > "Comment auto-replies" (C4 builds against this) and for the automatic-reply
marker in the Comments view (C3). Owner routes only, under `/v1/sites/{site_id}/inquiries/comment-rules/`
(not under `comments/`, because `comments/{ref}/` would swallow `comments/rules/`). Same session cookie,
site ownership and Sante-site rule as the C1 comment routes. Errors use the single API shape
`{"error": {"code", "message", "fields?"}}` (`fields` = `{field: [message]}`).

Status: **contract v1, written before the code**. Any later change is announced to the Supervisor and
shows in the diff of this file. Sources: Coach Ed 2026-11-18 01:00 HKT (CM1-CM6), plan sections 1.5, 2.4-2.5, 3, 6.

## 1. Availability: how the API says "not available"

| Situation | What the API does | What C4 shows |
|---|---|---|
| `INQUIRY_COMMENT_AUTOMATION_ENABLED` off (default), or `INQUIRY_COMMENTS_ENABLED` / Messenger switch off, or the Meta app values or the rule tables missing | **Every route below answers 404 `not_found`**, signed in or not; nothing is stored, evaluated or sent | Treat a 404 from `GET comment-rules/` as "auto-replies are not available" and hide the editor |
| Switches on, but the Page is not connected / token invalid / comments permission not granted ("comments not configured") | Routes work (rules can be edited). `automation.ready=false`, `automation.reason` is one of `not_connected`, `reconnect`, `permission_missing` (the same fixed reasons as C1 `comments/status/`), `automation.active=false` | Editor visible, with the "Turn on comments" prompt driven by C1 `status/` |
| Switches on and ready | `ready=true`, `reason=""`; `active` is true only while the owner's master is on and no pause is running | Normal |

A rule or the site master can be saved while not ready; nothing sends until `active`.

## 2. Shapes

**automation**

```json
{
  "enabled": false,            // owner's site master (default false; never turns itself on)
  "active": false,             // enabled && ready && no pause: automation is really running
  "ready": true,
  "reason": "",                // "" | not_connected | reconnect | permission_missing
  "banner": null,              // null | {"code", "message", "paused_until"}, see section 4
  "today": {"count": 12, "cap": 300, "date": "2026-11-18"},   // date in Asia/Manila
  "hour":  {"count": 3,  "cap": 30},                           // rolling last 60 minutes
  "limits": {
    "per_person_hours": 24, "delay_min_seconds": 30, "delay_max_seconds": 120,
    "max_rules": 20, "max_keywords": 20, "keyword_max_chars": 40, "max_posts": 50,
    "public_max_chars": 500, "private_max_chars": 1000, "max_age_days": 7
  }
}
```

`count` counts automatic replies (public and private each) queued, sending or sent; a cancelled, limited or
failed one is not counted. `limits` come from the tunable settings module, so C4 never hard-codes them.

**rule**

```json
{
  "ref": "cr_0123456789abcdef01234567",
  "enabled": true,
  "trigger": "keywords",                 // "any" | "keywords"
  "keywords": ["price", "magkano"],      // [] when trigger = any
  "scope": "selected",                   // "all" | "selected"
  "post_ids": ["111000111_222"],         // Meta post ids of this site's stored posts; [] when scope = all
  "posts": [{"id": "111000111_222", "is_ad": false, "caption": "Fresh mangoes", "permalink": "https://www.facebook.com/111000111_222"}],
  "public_text": "{Hi|Hello} {name}! {Check your inbox|We sent you a message}",
  "private_text": "{Hi|Hello} {name}, here are the details: ...",   // "" = no private reply
  "public_variants": 4,
  "private_variants": 2,                 // 0 when private_text is ""
  "created_at": "2026-11-18T03:00:00Z",
  "updated_at": "2026-11-18T03:00:00Z"
}
```

Rules are evaluated in list order (oldest first); the **first enabled rule that matches** answers the comment.
Keyword match: case-insensitive, whole word or phrase (so `hm` does not match `ahmed`).

**Spintax**: `{a|b|c}` picks one option. `{name}` is the commenter's first name (blank name becomes "there").
No nesting, no empty option, a group needs 2+ options, any other `{...}` is an error. Option text may hold
emoji and any characters except `{ } |`.

## 3. Endpoints

All need the session cookie; no `Idempotency-Key`. Reads are rate-limited like C1 reads, writes like C1 replies (429 `rate_limited`).

| Method and path | Request | Response |
|---|---|---|
| `GET comment-rules/` | none | `200 {"automation": automation, "rules": [rule]}` |
| `PUT comment-rules/automation/` | `{"enabled": bool}` | `200 automation`. Turning **on** clears any pause and the daily strike count (the "owner re-enables" step). Turning **off** also cancels every queued automatic reply of the site |
| `GET comment-rules/posts/?q=&cursor=` | `q` optional, 2-80 chars, searches caption | `200 {"items": [{"id","is_ad","caption","permalink"}], "next_cursor"}`: this site's stored posts and ads, newest first, 30 per page, for the post picker |
| `POST comment-rules/preview/` | `{"template": str, "kind": "public"\|"private"}` | `200 {"valid": bool, "error": null\|{"code","message"}, "variant_count": int, "samples": [str]}`: always 200 so the UI can validate while typing; up to 3 random samples rendered with the name "Maria" |
| `POST comment-rules/` | `enabled` (default true), `trigger`, `keywords`, `scope`, `post_ids`, `public_text`, `private_text` (default "") | `201 rule` |
| `GET comment-rules/{ref}/` | none | `200 rule` |
| `PUT comment-rules/{ref}/` | any subset of the writable fields (omitted = unchanged; the merged rule is validated). A toggle is `{"enabled": false}` | `200 rule`. Disabling a rule, or narrowing it, cancels its queued automatic replies |
| `DELETE comment-rules/{ref}/` | none | `200 {"deleted": true}`. Cancels its queued replies; replies already sent keep their history |

Unknown or other-site `ref`: 404 `not_found`. Another site's id: 404 before any body is read.
Unknown body fields: 400 `invalid_request` (the API's standard).

### Validation errors (422, `fields` names the field)

| code | field | message | when |
|---|---|---|---|
| `public_required` | `public_text` | Write the public reply. | blank public text |
| `spintax_variants_required` | `public_text` | Add at least 2 variants, for example {Hi\|Hello} {name}. | public text with fewer than 2 variants |
| `invalid_spintax` | `public_text` / `private_text` | Check the { } and \| in your text: each group needs 2 or more options, with no nesting and no empty option. | bad syntax, empty option, nesting, single-option group, unknown `{x}` |
| `link_in_public_reply` | `public_text` | Links cannot go in a public reply. Put the link in the private message. | URL-like text in any option (`http(s)://`, `www.`, or a domain with a common TLD). Private text may hold links |
| `public_too_long` | `public_text` | A public reply is at most 500 characters. | the **longest possible** variant (first name counted as 30 characters) is over 500 |
| `private_too_long` | `private_text` | A private message is at most 1000 characters. | same rule, over 1000 |
| `keywords_required` | `keywords` | Add at least one word to look for. | trigger `keywords` with an empty list |
| `invalid_keywords` | `keywords` | Keywords are at most 20, each 1 to 40 characters. | too many / too long (blank ones dropped; duplicates ignored case-insensitively) |
| `posts_required` | `post_ids` | Pick at least one post or ad. | scope `selected` with an empty list |
| `unknown_post` | `post_ids` | One of those posts is not one of your Page's posts. | an id that is not a stored post of this site |
| `invalid_posts` | `post_ids` | You can pick up to 50 posts. | more than 50 ids |
| `invalid_trigger` / `invalid_scope` | `trigger` / `scope` | Trigger must be one of: any, keywords. / Scope must be one of: all, selected. | bad enum |

`409 too_many_rules`: "You can have up to 20 auto-reply rules." (POST when 20 exist).
Keywords sent with trigger `any`, and post ids sent with scope `all`, are ignored and stored empty.

## 4. Pause and banner

`automation.banner` is `null` or:

| code | message | `paused_until` | `automation.active` | Owner action |
|---|---|---|---|---|
| `paused_rate_limit` | Facebook asked us to slow down, so auto-replies are paused for 1 hour. | ISO time | false until then, then resumes by itself | none |
| `needs_reenable` | Auto-replies were switched off after Facebook slowed us down 3 times today. Turn them back on when you are ready. | null | false; `enabled` is false | `PUT automation/ {"enabled": true}` |

Triggers: a Meta rate-limit (codes 4, 9, 17, 32, 613, HTTP 429) or spam-block (code 368) error on an
**automatic or a manual** send is one strike and pauses automation for 1 hour; the third strike in the same Manila day
sets `enabled=false` and shows `needs_reenable`. The pause itself never blocks manual replies. While paused, new
comments are not queued (no marker), and replies already queued are cancelled, not retried.

## 5. What C3 sees on a comment (Comments view)

The C1 **comment item** gains one key, `auto_reply` (always present; `null` for a comment with no automatic
action, and always `null` while automation is off):

```json
"auto_reply": {
  "state": "limit",                 // queued | sent | failed | limit | cancelled
  "label": "Not auto-answered (limit)",
  "reason": "hour_cap",             // "" | hour_cap | day_cap  (only for state limit)
  "public": "limit", "private": null   // per-kind state: queued|sent|failed|limit|cancelled|null
}
```

`state` summarises the comment's automatic rows: `queued` if any is queued, else `limit` if any was refused by
a cap, else `failed`, `cancelled`, or `sent`. Labels: `queued` "Auto-reply queued", `sent` "Auto-replied",
`failed` "Auto-reply failed", `cancelled` "Auto-reply cancelled", `limit` "Not auto-answered (limit)".
A `limit` comment is **left for a manual reply**: its `public_reply` / `private_reply` buttons stay available
as in C1. Only the two caps produce `limit`; skipped comments (per-person 24 h, Page's own, answered thread,
older than 7 days, no matching rule, paused) have no marker.

The C1 action objects in comment detail (`actions[]`) gain `skip_reason` ("" | `hour_cap` | `day_cap`); their
`state` may now be `queued`, `cancelled`, `blocked_rate` or `limit`, and `source` is `rule` for automatic ones.
`answered` in the list stays "a public or private reply exists", true once an automatic reply is **sent**.

## 6. Behaviour the API guarantees (tested)

Automation never runs in the webhook request; a task evaluates a new comment and queues rows with a random 30-120 s delay;
each send task re-checks platform switch, site master, pause, rule enabled and comment state; max one automatic public and
one automatic private reply per comment ever (DB unique); edits never retrigger; top-level customer comments only, not the
Page's own, not in a thread the Page already answered, not older than 7 days; one automatic reply per person per post per
24 h; caps 30 per hour and 300 per day per Page (both kinds counted; reserved when queued, in one transaction); the same
public variant is not used twice in a row on one post (same for private); `remove` and rule delete/disable cancel queued
rows; no token, raw Meta body or URL with a query goes to logs or audit rows.
