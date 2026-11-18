# Customer inquiries: switches, email replies and the contact form

The Inbox of a Santé store (`apps/inquiries`). This runbook covers the outbound email half (G3 PR2a). Website chat is in `docs/runbooks/storefront.md`; the owner routes are in the docstring of `apps/inquiries/api_owner.py`. There is no provider connection: the Pancake integration was removed (task z8uq9mg1n9, migration `inquiries.0003_remove_pancake`), and the `InquiryConnection` table is kept, empty, for the direct Messenger inbox that comes next. **Inbound email waits on D2** (Coach Ed's decision on the receiving domain): there is no webhook, no `c-`/`s-` routing address and no bounce state yet, and nothing here needs a Railway variable beyond the switches.

## The three switches

All are off by default. A switch is set on the `api` service only, by Coach Ed or the manager; no session changes one.

| Switch | Opens | Needs |
|---|---|---|
| `INQUIRIES_ENABLED` | The owner's routes under `/v1/sites/{id}/inquiries/`, including email replies on a stored `em_` conversation | `PAYMENT_CREDENTIALS_ENCRYPTION_KEY` (`inquiries.E001`) |
| `INQUIRY_WEBCHAT_ENABLED` | The two storefront chat routes | `INQUIRIES_ENABLED` and `STOREFRONT_ENABLED` (`inquiries.E002`) |
| `INQUIRY_CONTACT_INBOX_ENABLED` | A stored copy of each storefront contact-form message, as an `email` conversation | `INQUIRIES_ENABLED` and `STOREFRONT_ENABLED` (`inquiries.E003`) |

Email replies have no switch of their own: they need a conversation with an `em_` ref, and today only the contact form makes one. All three need `inquiries.0002` applied (staging: yes, 2026-11-15; production: not run). Run `manage.py check --deploy` after changing any of them.

## The contact form in the Inbox

With `INQUIRY_CONTACT_INBOX_ENABLED` on, `send_contact_message` first sends the usual email to the store (unchanged, byte for byte, whether the switch is on or off) and then stores:

- a NEW `email` conversation for every stored message. It is never merged by the typed address, because that address is only a claim. `auth_flag` is `unverified_sender`, the conversation is open and unread, `contact_name` and `display_name` come from the name, `contact_email` from the typed address and `external_id` is a server-made random `em_` ref;
- one incoming `InquiryMessage` whose body is `Subject: <subject>`, a blank line and the message, cut to 4,000 characters.

Each site also has its own switch, `email_enabled` on the channels API (`PUT .../inquiries/channels/`, Settings > Inbox, default off; `contact_inbox_available` in the same answer is the global switch): a copy is stored only when the global switch and the site's switch are both on, and the email goes either way.

Nothing is stored for the honeypot, with the switch off, or while the inquiries tables are absent (`stored_tables_exist()` is false; then no inquiries table is read). A failure while storing is caught and logged by class name only; the visitor still gets "sent". Caps, through `apps/common/throttle`, per day: 200 stored messages per site and 30 per typed address per site (the key holds a SHA-256 of the lowercased address, never the address). Over a cap the email still goes and nothing is stored; it is not a failure.

## Replying by email

`POST /v1/sites/{id}/inquiries/conversations/{ref}/reply/` on an `em_` ref sends one email.

- The recipient is the conversation's own `contact_email`, never a field of the request (the schema refuses any extra field). One recipient, no CC or BCC.
- The sender rule (D5): the store's own address from `STORE_SENDERS` (`sender_address(site)`) is required, whether `STORE_EMAILS_BRANDED` is on or off. An inquiry reply is always rendered with the branded layout. No sender for the store means nothing is sent, and the platform address is never used instead. Chat replies are unaffected.
- Subject: `A message from <store>` (there is no subject field to reply to). Body: the owner's plain text, at most 4,000 characters (the route allows 2,000); no link injected, no tracking.
- Reply-To is the store's usual one, the owner's verified account email, until a receiving domain exists (D2). `send_store_email` already takes a `reply_to` argument for the later `c-` address.
- Footer: `You are receiving this because you contacted <store>.` **D6 placeholder wording: awaiting Coach Ed.** It is the constant `FOOTER_REASON` in `apps/inquiries/stored.py`.
- An `unverified_sender` conversation can still be replied to; the thread shows `auth_flag` and the dash decides what to say.
- Send records: each reply leaves an `EmailMessage` row, category `inquiry_reply` (see `retention.md`).

### Limits

Per site, 20 inquiry emails an hour and 100 a day (keys carry the site: one store's flood never uses another's budget). Over a limit nothing is sent and the audit row is `blocked_rate`.

### Codes and the owner's fixed texts

The reply result is `{state, error_code, created_at}` plus, for an email reply that did not go, `message`. `InquiryReply.error_code` is a closed list and changing it needs a migration, so the audit row keeps an existing code and the specific one is kept on `InquiryMessage.error_code`; the owner's result shows the specific one.

| Specific code (shown) | Audit row | State | Text shown to the owner |
|---|---|---|---|
| `sender_not_ready` | `not_configured` | `failed` | Email replies are not set up for this store. |
| `no_recipient` | `not_found` | `failed` | This conversation has no email address to reply to. |
| `email_failed` | `provider_down` | `failed` | The email could not be sent. Please try again shortly. |
| `rate_limited` | `rate_limited` | `blocked_rate` | Too many emails have been sent from this store. Please wait and try again. |

No exception text, mail-server reply or credential is stored, logged or returned: a failed send logs the exception's class name only. A failed reply does not move the conversation. Sent means accepted by the mail server; delivered and bounced states wait on D2.

## Messenger (the direct Page inbox, task z8uq9mg1qd)

Code: `apps/inquiries/messenger.py` (every Graph call, one network function), `messenger_inbox.py` (storage, reply, callbacks), `api_messenger.py` (routes). Migration `inquiries.0004_messenger` (provider choice, `psid`, `attachment_types`, `meta_user_id`, one-Page-per-site constraint, `token_invalid` code). `psid` and `page_id` are on `InquiryConversation` (`page_id` is the Page the conversation arrived on; a reply is refused as `not_configured` once the site's connection points at another Page). **Release order: run `0004` immediately after the api deploy.** `stored_tables_exist()` gates website chat and the contact inbox on `0004`'s columns, so until it is applied (even with the Messenger switch off) the stored channels answer as if their tables were missing. Run it on staging before turning the switch on.

**Graph API version.** Pinned in one constant, `messenger.GRAPH_VERSION` (now `v26.0`, the latest on Meta's versions page, 2026-11-17). Change it there only, after reading Meta's changelog.

**Variables (api service, Railway).** `INQUIRY_MESSENGER_ENABLED=1` (needs `INQUIRIES_ENABLED=1`), `META_MESSENGER_APP_ID`, `META_MESSENGER_APP_SECRET` (straight from Coach Ed into Railway, never chat or code), which the **worker service needs too** (not only `api`; on Railway set them there as reference variables to the `api` service's values): the worker's `fetch_messenger_profile` task checks `messenger.configured()` and silently skips the name and photo fetch without them, `MESSENGER_VERIFY_TOKEN` (any long random string; the same value goes into the Meta webhook form), and optional `META_MESSENGER_CONFIG_ID` (Facebook Login for Business: when set, the dialog sends `config_id` instead of `scope`). `check --deploy` reports `inquiries.E004` (switch without INQUIRIES_ENABLED) and `inquiries.E005` (missing app id, secret or verify token).

**Meta app settings (P0), staging.** Valid OAuth redirect URI `https://api-staging.negosyonline.com/v1/inquiries/messenger/callback/`; webhook callback `https://api-staging.negosyonline.com/v1/webhooks/messenger/` with the verify token above, object `Page`, fields messages, messaging_postbacks, message_echoes, message_deliveries, message_reads (the Page subscription itself is made by our `connect/`); deauthorize callback `.../v1/webhooks/messenger/deauthorize/`; data deletion request URL `.../v1/webhooks/messenger/data-deletion/`. Permissions asked: exactly pages_show_list, pages_manage_metadata, pages_messaging and pages_read_engagement (the four App Review covers, task z8uq9mg6mm; an extra one would need its own review). pages_read_engagement is used to read the Page name and Page token to connect the Page (the business-portfolio fallback reads each granted Page directly and needs it).

**Behaviour.**
- Webhook GET verify answers even with the switch off. POST needs a valid `X-Hub-Signature-256` over the raw body (constant-time compare, else 403), body cap 1,000,000 bytes (413); a signed POST with the switch off is dropped with 200. Events route by `entry.id` to the one active connection; unknown pages are dropped and logged by code only; idempotent by Meta message id; attachments stored as type only.
- Connect: owner `start/` (signed 10-minute state, one use) -> Facebook -> fixed callback -> server-side pick list (10 minutes, in the cache, Page tokens Fernet-encrypted; the user token is used once and never stored) -> `connect/` stores the Page token Fernet-encrypted and subscribes the Page. D5 (one Page, one site) is checked and also enforced by a database constraint.
- Login for Business Page fallback (task z8uq9mg5ej): when `/me/accounts` returns no Page with a token (Pages in a business portfolio, no `business_management`), `messenger.list_pages` reads `/debug_token` (user token and app token as query parameters, never logged), takes the `granular_scopes` target ids for `pages_messaging` (else `pages_show_list`; digits only, deduped, at most 100), and reads each Page with the user token and `appsecret_proof`; any Graph failure on that path returns its mapped error, and an empty result is still `no_pages`.
- Reply: Send API `POST /{page-id}/messages` with `messaging_type=RESPONSE`, only within 24 hours of the customer's last message (checked before any call; outside it the answer is `window_closed` and Meta is not called), through `limiter.py` per connection. The audit row keeps the outcome code and Meta's message id, never the text.
- Error mapping (Meta's error-code table): 190 -> `token_invalid` (connection marked invalid, owner must reconnect); 10 with subcode 2534022/2018278/2018065 -> `window_closed`; other 10, 200-299 -> `forbidden_page`; 4, 9, 17, 32, 613, HTTP 429 -> `rate_limited`; 551, 100/2018001, 10/2018108 -> `not_found` (customer unreachable); 1, 2, HTTP 5xx, no answer -> `provider_down`; anything else -> `bad_response`. No Meta body, URL or token is logged or returned.
- Refused-call log (task z8uq9mg63r): one warning `Messenger call refused (<outcome>) <method> <path template> status= code= subcode= type= fbtrace= msg=`; the path template has digit-only segments as `{id}` and no query; `msg` is Graph `error.message` with token-like runs masked as `***`, whitespace collapsed, cut to 200 characters. Plain permission-style names (lowercase letters and underscores only, such as pages_read_engagement) are shown, not masked (task z8uq9mg6mm). Never params, form, tokens, proof, URL or body. The failed-call warning logs the method and path template.
- Webhook summary line (task z8uq9mg76y): every signed Page POST logs exactly one warning, found with `railway logs` (filter `Messenger webhook summary`): `Messenger webhook summary entries=messaging:1,standby:0,changes:0,other:0 events=message:1,echo:0,delivery:0,read:0,postback:0,pass_thread_control:0,take_thread_control:0,other:0 stored=1 dropped=no_connection:0,not_addressed:0,empty:0,duplicate:0`. `entries` counts the entries by the key they carry; `events` counts the events of `messaging` and `standby` by kind; `stored` is the messages saved; `dropped` says why events were not (`no_connection`: no single active connection for the Page, counted per event or 1 for an entry without events). A POST with `standby>0` and `stored=0` means another app still holds the thread: standby events are counted, never stored, and nothing is sent to Meta. The line has counts only, never an id or text.
- INBOX-UI (task z8uq9mg7c5): `apps/inquiries/profile.py` + `tasks.py` (`inquiries.fetch_messenger_profile`, queued after commit for a new Messenger customer or when the cached photo is gone; at most one try per hour per conversation). Graph `GET /{psid}?fields=first_name,last_name,profile_pic` with the Page token and a 3 s timeout; the call logs the fixed label `/{psid}` and codes only (never the id, Graph's message, a name or a URL). The name fills `display_name` only while it is blank or `Messenger customer`; the photo URL (https, a `fbsbx.com` or `fbcdn.net` host only) is cached 48 h under `inbox:avatar:{conversation pk}` and is in no table; deauthorize, data deletion, the 180-day rule and site/account disposal all delete that key. `delivery` and `read` webhook events move the Page's own outgoing Messenger messages forward only (`sent` -> `delivered` -> `seen`, never back, `failed` never changes), by mids and/or watermark, in that customer's conversation of that Page's site. Migration `inquiries.0005_message_seen_state` adds `seen` to the delivery-state choices (choices only, no SQL). The list (`avatar_url`, `unread_total`, `?q=` search, 2 to 80 characters else 422 `invalid_query`), the thread (`avatar_url`, `reply_window.closes_at`) and `GET .../inquiries/unread/` (`{"count": n}`) are described in the docstring of `api_owner.py`.
- Callbacks: deauthorize (signed_request) clears the token and marks that user's connections invalid. Data deletion (signed_request) clears the connection and deletes the Messenger messages and customer fields of that user's connections, returns `{url, confirmation_code}`, and `GET .../data-deletion/{code}/` answers `{"status":"completed"}`. Both need the app secret set and work with the switch off.
- Retention: Messenger conversations are stored conversations: messages and the customer's page-scoped id are deleted/blanked 180 days after the last message, and site/account disposal blanks the token, Page id and customer fields.
- Logging: the Messenger verify token (`hub.verify_token`) and the OAuth callback `code` and `state` (whole parameter names only, on every path) are masked as `***` in the gunicorn access log by `gunicorn.conf.py` (`config/access_log.py`); the Railway start command is unchanged.

## The live Inbox (server push, task z8uq9mg7ah)

- `GET /v1/sites/{site_id}/inquiries/stream/` is a server-sent-events stream for the signed-in owner's open Inbox (`apps/inquiries/stream.py`, an async view registered ahead of the ninja include). It answers with exactly the Inbox routes' rules and JSON errors before any stream starts (404 off or no tables or foreign site, 401, 403 Billing-only), then `503 stream_unavailable` without Redis and `429 too_many_streams` over 5 open streams per account (a Redis sorted set `nego:inbox:conn:{account_id}` whose members expire, so a crashed worker frees its slots).
- Wire format: `retry: 5000`, `event: ready`, a `: ping` comment every 15 s, then `conversation_updated` (`ref`, `last_message_at`, `kind` = `message` or `status`) and `message_status` (`ref`). Never text, a name, a PSID, a page id, an email or a token. No `id:` lines and no replay: the Inbox reads the list again on every `ready`. The stream ends after 10 minutes, and at any ping where the session or the site is no longer the owner's; the browser reconnects.
- Publish side: `apps/inquiries/live.py` publishes on `nego:inbox:site:{site_id}` with `transaction.on_commit` from a stored Messenger message, a stored reply and its delivery state, a web-chat message, a contact-form message, and read / close / reopen. A failure (no `REDIS_URL`, Redis down) is swallowed with the warning `Inbox live publish failed (ClassName)`; it never fails a request or a webhook. Blocked replies publish nothing.
- Needs ASGI workers: the `api` start command is `uv run gunicorn config.asgi:application -k uvicorn_worker.UvicornWorker ...` (the table and rollback line are in `docs/runbooks/billing.md`). Under it the Messenger verify token, `code` and `state` stay masked: `config/asgi.py` attaches `config.access_log.MaskingFilter` to the `uvicorn.access` logger (`gunicorn.conf.py`'s `MaskingLogger` still serves WSGI and the rollback).
- Known limits: Django's ASGI app has no shutdown hook that reaches open streams, so on a deploy they end when gunicorn's graceful timeout (30 s) closes the worker and the browser reconnects; an in-flight stream's slot is also freed by its 45 s expiry should a worker die.
- Locally: run `uv run gunicorn config.asgi:application -k uvicorn_worker.UvicornWorker --bind 127.0.0.1:<free port>` with `REDIS_URL` pointing at your own `redis-server` on a free port, signed in as the owner; `curl -N -b <cookie jar> -H 'Origin: <allowed origin>' <api>/v1/sites/<id>/inquiries/stream/`.

## Testing it locally

Use the console mail backend: leave `EMAIL_HOST` unset so every email is printed to the log, and set `DJANGO_DEBUG=true`. Run with `INQUIRIES_ENABLED=1 STOREFRONT_ENABLED=1 INQUIRY_CONTACT_INBOX_ENABLED=1`, `STORE_SENDERS=<web-address-name>=orders@example.test`, a Santé site and `STOREFRONT_SERVER_SECRET`. Post to `/storefront/v1/contact/` (the storefront test cases show the body), then list `GET .../conversations/`, then reply. The test suite uses the in-memory backend and sends nothing real.

## Facebook comments (task z8uq9mg9pe, C1)

Contract: `docs/contracts/comments-api-c1.md`. Code: `comments.py` (feed webhook storage, list, actions), `api_comments.py` (routes), the Graph calls in `messenger.py` (Meta sources and the unconfirmed points are recorded in the comment block above `COMMENT_PERMISSIONS`). Migration `inquiries.0006_comments` (additive: `InquiryPost`, `InquiryComment`, `InquiryCommentAction`, `InquiryCommentSettings`). The settings table is separate so code released before the migration still reads `InquiryConnection`; every comment code path asks `switch.comment_tables_exist()` first (webhook, routes, retention, erasure, disposal). **Run `0006` on staging before turning the switch on.**

**Switch.** `INQUIRY_COMMENTS_ENABLED=1` (needs the Messenger inbox on; `check --deploy` reports `inquiries.E006`). Off: routes 404, `feed` events dropped, nothing stored, nothing sent. Optional `META_COMMENTS_CONFIG_ID` (second Login for Business configuration; unset, the comments reconnect asks for the six scopes). No variable was set by this task.

**Behaviour.** `feed` events (item `comment`, verbs add, edited, remove, hide, unhide) arrive at the existing signed webhook; routing, signature, size cap and idempotency (comment id) are the Messenger ones. `remove` blanks text and author and cancels queued actions (a tombstone row stays so a late retry is not stored). `connect/` subscribes `feed` only with the switch on and both comment permissions granted. One summary warning per POST with comment changes: `Comments webhook summary stored:N,updated:N,removed:N,hidden:N,duplicate:N,dropped:N` (counts only). Outgoing actions write one `InquiryCommentAction` row (outcome code and Meta id only, never text). Retention: rule `inquiry_comments`, 180 days from the comment time; the data-deletion callback and site/account disposal delete the comments of the affected connections/sites.

Switches, order of turning on, caps and retention for comments: `docs/runbooks/comments.md`. The separate comments App Review pack (file only after the Messenger review is approved): `docs/app-review/comments-app-review-pack.md`.
