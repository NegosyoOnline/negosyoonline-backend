# Facebook comments API, C1 (task z8uq9mg9pe)

Contract for the Dash Inbox "Comments" view (C3 builds against this). Owner routes only, under
`/v1/sites/{site_id}/inquiries/comments/`. Same session cookie, same site ownership and Santé-site
rule as the other inquiry routes (a missing, foreign or non-Santé site answers 404 before any body is
read). Errors use the API's single shape: `{"error": {"code", "message", "fields?"}}`.

Status: **contract v1, written before the code**. Anything that changes after this commit is announced
to the Supervisor and shown in the diff of this file.

## Switch

`INQUIRY_COMMENTS_ENABLED` (default off). It counts only while `INQUIRY_MESSENGER_ENABLED` and
`INQUIRIES_ENABLED` are on too, and the Meta app values are set. **Off, every route below answers
404 `not_found`** (the same 404 as an unknown site), the feed webhook is dropped, nothing is stored
or read, and nothing is sent to Meta. The dashboard treats a 404 from `status/` as "comments are not
available" and hides the Comments view.

## Common shapes

`ref`: the comment's server-made random id, `cm_` + 24 hex characters, unique per site. Meta's own
comment id is never sent to the browser. A ref that is not this site's is 404 `not_found`.

**Comment item** (list and detail):

```json
{
  "ref": "cm_0123456789abcdef01234567",
  "author_name": "Maria Santos",          // "Facebook user" when Meta sent none
  "text": "How much is the medium?",       // plain text only, never HTML; up to 2000 chars
  "created_at": "2026-11-18T03:00:00Z",    // when the person commented (Meta's created_time)
  "unread": true,
  "answered": false,                       // a public reply or a private reply exists (ours, or the Page's own on Facebook)
  "hidden": false,
  "is_reply": false,                       // true when it is a reply under another comment, false for a top-level comment
  "post": {
    "id": "111000111_222",                 // Meta post id (an ad runs as a Page post and comes the same way)
    "is_ad": false,
    "permalink": "https://www.facebook.com/111000111_222",  // or null; only a verified pattern, never a query
    "caption": "Fresh mangoes, 3 kilos for 500 pesos"       // short snippet (max 140), may be ""
  },
  "public_reply":  {"available": true,  "reason": "", "replied_at": null},
  "private_reply": {"available": true,  "reason": "", "replied_at": null, "expires_at": "2026-11-25T03:00:00Z"},
  "hide":          {"available": true,  "reason": ""}
}
```

`available: false` carries a fixed `reason` (never provider text), for the disabled button:

| reason | meaning | applies to |
|---|---|---|
| `not_connected` | no active Page connection | all three |
| `reconnect` | the Page token no longer works (connection invalid) | all three |
| `permission_missing` | comments were not turned on for this Page (the comments permissions were not granted / feed not subscribed) | all three |
| `removed` | the comment was deleted on Facebook | all three (such a comment is never listed; this appears on a stale detail only as 404) |
| `own_comment` | the comment is the Page's own | all three |
| `window_closed` | more than 7 days since the comment was made (Meta's private-reply limit) | `private_reply` |
| `already_sent` | a private reply was already sent for this comment (Meta: one per comment) | `private_reply` |
| `already_hidden` | the comment is already hidden | `hide` |

`private_reply.expires_at` is `created_at` + 7 days (null for an own comment). `replied_at` is the time
of our successful send, or of the Page's own reply seen on Facebook for `public_reply`.

## Routes

### GET `status/`
`200 {"enabled": true, "connected": true, "page_name": "Ana Store Page", "ready": true, "reason": ""}`.
`ready` is true only when the Page is connected, the token works and comments were turned on for it.
`reason` when not ready: `not_connected`, `reconnect`, or `permission_missing` (use the reconnect
flow below). A stored list may still be shown when not ready; sends are refused (see Errors).

### GET `/` (list)
Query (all optional):

| param | values | default | invalid |
|---|---|---|---|
| `scope` | `all`, `post` (posts only, not ads), `ad` (ads only) | `all` | 422 `invalid_scope` |
| `answered` | `all`, `answered`, `unanswered` | `all` | 422 `invalid_answered` |
| `unread` | `1` unread only, `0` read only | all | 422 `invalid_unread` |
| `q` | 2 to 80 characters; matches author name, comment text or the post caption, ignoring case | none | 422 `invalid_query` |
| `cursor` | the previous `next_cursor` (opaque) | first page | 422 `invalid_cursor` |

`200 {"items": [CommentItem], "next_cursor": "..."|null, "unread_total": 3}`. Newest first, 30 per page.
Deleted comments and the Page's own comments are not listed. `unread_total` counts all unread comments
of the site and is never filtered.

### GET `{ref}/` (detail)
`200` the Comment item plus:

```json
{
  "replies": [{"ref": "cm_...", "kind": "comment|private", "author_name": "Ana Store Page", "is_page": true, "text": "...", "created_at": "..."}],
  "actions": [{"kind": "public|private|hide", "source": "manual", "state": "sent|failed|sending", "error_code": "", "created_at": "..."}]
}
```
`replies` are stored comments directly under this one (oldest first, at most 50), the Page's own
included, merged with the owner's sent private messages (`kind: "private"`, `ref: ""`, `is_page: true`,
`author_name` the Page name) in time order. The owner's public reply is stored as a Page comment
(`kind: "comment"`) as soon as Meta returns its id, so it stays after a reload. Private message
text is kept with the action row until the comment is removed or erased. `actions` are the latest 20
outgoing attempts (outcome only, never text).

### POST `{ref}/read/`
No body. `200` the Comment item with `unread: false`.

### POST `{ref}/reply/` (public reply)
Body `{"text": "...", "idempotency_key": "8-64 chars [A-Za-z0-9_-]"}`; text 1 to 500 characters after trim
(else 422 `invalid`, with `fields.text`). Meta call: `POST /{comment-id}/comments`. A repeat of the same
key returns the first result and sends nothing. `200`:

```json
{"state": "sent", "error_code": "", "created_at": "...", "comment": CommentItem}
```
`state`: `sent`, `failed`, `blocked_rate`. A Meta refusal is **200 with `state: "failed"`** and a fixed
`error_code` (below), not an HTTP error. A sent reply sets `answered` and marks the comment read.

### POST `{ref}/private-reply/`
Body as above; text 1 to 1000 characters. Meta call: `POST /{page-id}/messages` with
`recipient: {comment_id}`. Only one private reply per comment, only within 7 days of the comment.
Same `200` shape. Preconditions fail before any call, as 409 (below). A successful send sets
`private_reply.available=false, reason="already_sent"` and marks the comment answered and read. The
customer's answer arrives in the normal Messenger inbox.

### POST `{ref}/hide/`
No body. Meta call: `POST /{comment-id}` with `is_hidden=true`. `200 {"state": "sent"|"failed", "error_code": "", "created_at": "...", "comment": CommentItem}`; a successful hide sets `hidden: true`. (Unhide is not in C1.)

## Errors

| HTTP | code | when |
|---|---|---|
| 404 | `not_found` | switch off, unknown/foreign site or ref, ref malformed |
| 409 | `comment_removed` | the comment was deleted on Facebook |
| 409 | `not_connected` / `reconnect` / `permission_missing` | the reasons above, on a send/hide |
| 409 | `own_comment` | acting on the Page's own comment |
| 409 | `window_closed` | private reply after 7 days |
| 409 | `already_sent` | second private reply for the same comment |
| 409 | `already_hidden` | hiding a hidden comment |
| 409 | `conflict` | the idempotency key was already used for another comment/action |
| 422 | `invalid`, `invalid_query`, `invalid_scope`, `invalid_answered`, `invalid_unread`, `invalid_cursor` | with `fields` |
| 429 | `rate_limited` | the per-account request cap (same caps as the other inquiry routes) |

Meta refusals come back as **200 + `state: "failed"`** with one fixed `error_code` (the existing
outcomes): `token_invalid` (reconnect; the connection is marked invalid), `forbidden_page`
(permission missing), `window_closed`, `rate_limited` (also `state: "blocked_rate"` when our own cap
stops it), `provider_down`, `not_found` (the comment no longer exists / cannot be replied to),
`bad_response`. No Meta text, body, token or URL is ever returned.

## Reconnect with the comments permissions (for C4, same flow as the Messenger connection)

`GET /v1/sites/{site_id}/inquiries/messenger/start/?comments=1` (existing route, new optional query):
302 to the Facebook dialog. With `META_COMMENTS_CONFIG_ID` set the dialog sends that
Login-for-Business configuration; otherwise it asks for the six scopes. The callback then requires
`pages_read_user_content` and `pages_manage_engagement` too (`?messenger=error&code=permission_missing`
when missing); the existing `pages/` and `connect/` routes pick the Page. `connect/` subscribes the
`feed` field **only** when the comments switch is on and the permissions were granted; otherwise the
Messenger fields only, as today. `GET messenger/connection/` gains `"comments_ready": true|false`.

## Not in C1

Unhide, rules and automation (C2), live (server-sent) push for comments (use Refresh), any storefront
route.

## Data and retention

Kept 180 days from the comment time (the existing retention job). Deleted by: a `remove` event, the
retention job, the Meta data-deletion callback (all comments of the requesting user's Pages) and site
disposal. A deleted comment's text and author fields are erased at once.
