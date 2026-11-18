# Facebook comments: switches, order of turning on, caps (task z8uq9mg9pn)

Written from the C1 code on main (78b35b2, checked 2026-11-19) and the C2 task design (z8uq9mg9pj, draft contract on `feat/z8uq9mg9pj-comment-rules`). Anything marked **per C2 task, confirm at merge** is not in code yet. Code and behaviour of C1 are in `inquiries.md` ("Facebook comments") and `docs/contracts/comments-api-c1.md`. Nothing here sets a variable; each is set by Coach Ed or the manager, staging first (CM5, asked when the variables are set).

## Switches and variable

| Name | What it does | Needs | Off means |
|---|---|---|---|
| `INQUIRY_COMMENTS_ENABLED` | Comments view, `feed` webhook storage, manual public reply, private reply, hide | `INQUIRY_MESSENGER_ENABLED` and `INQUIRIES_ENABLED` (`check --deploy` reports `inquiries.E006` otherwise); the Meta app values (`META_MESSENGER_APP_ID`, `META_MESSENGER_APP_SECRET`) | Routes 404, `feed` events dropped, nothing stored, nothing sent to Meta |
| `INQUIRY_COMMENT_AUTOMATION_ENABLED` (per C2 task, confirm at merge) | Auto-reply rules: Celery evaluates rules and queues replies | `INQUIRY_COMMENTS_ENABLED` | No rule runs; each queued task re-checks the switch before sending, so it also stops queued replies |
| `META_COMMENTS_CONFIG_ID` | Second Facebook Login for Business configuration (six permissions), used only when the owner turns comments on | Created by Coach Ed in the Meta dashboard (step list: C0 notes, task z8uq9mg9pm); optional, not a secret | Unset: the login dialog asks for the six scopes (`scope`) instead; never the Messenger-only configuration `META_MESSENGER_CONFIG_ID` |

Off is safe: both switches default off (`config/settings.py`: on only when the value is exactly `1`); the Messenger inbox and its configuration `2209223909690313` are untouched by either.

## Order of turning on (staging)
1. Release with `inquiries.0006_comments` (C1) and C2's migration (per C2 task, confirm at merge); run them on staging first (additive), then `check --deploy`.
2. Coach Ed does the Meta dashboard steps (C0): second configuration, `feed` field, test post.
3. Set `META_COMMENTS_CONFIG_ID`, then `INQUIRY_COMMENTS_ENABLED=1`; owner clicks "Turn on comments" (re-connect with the extra permissions; `connect/` subscribes `feed`).
4. Check a comment on the test post reaches the Comments view, then manual replies.
5. Only then `INQUIRY_COMMENT_AUTOMATION_ENABLED=1` and one rule on the test Page.
Turn off in reverse; setting a switch off stops processing at once.

## Caps and back-off (per C2 task, confirm at merge)
- Per Page: 30 automatic replies an hour, 300 a day (each kind counted); over the cap the comment is left for a manual reply, "Not auto-answered (limit)".
- Per person: 1 automatic reply per post per 24 hours. One automatic public and one private reply per comment, ever (DB unique). Edits never retrigger.
- Only top-level customer comments, not older than 7 days, not in a thread the Page already answered.
- Meta rate-limit or spam error pauses a site's automation 1 hour; three in a day pause it until the owner turns it back on (banner).
- No links in automatic public replies; 500 characters public, 1000 private; spintax with at least 2 variants; random delay 30-120 s.
- Limits live in one settings module (tunable).

## Retention and migration
Comments are kept 180 days from the comment time (rule `inquiry_comments`, `retention.md`; the job runs only with `RETENTION_ENABLED`, whatever the comments switch says, and does nothing before `inquiries.0006` is applied); `remove` events, the data-deletion callback and site/account disposal delete earlier. Migration `inquiries.0006_comments` (C1, additive). Run on staging before the switch; production not covered. C2 adds `inquiries.0007` (additive: rules table, automation fields on the settings row, rule fields on the action row), per C2 task, confirm at merge; its Celery tasks need `REDIS_URL` in staging (without it Celery runs eagerly and ignores the delay). Every comment code path checks that the tables exist, so code may be released before the migration, but the switch must not be turned on until it has run.

## Privacy sentence (CM4, approved verbatim; tracked only)
Owned by HQ task z8uq9mg9pd (frontend branch `docs/z8uq9mg9pd-privacy-fb-comments`, ready and on hold until the C1/C3 release); not edited in this repository. "If a store connects its Facebook Page and turns on comments, we process the comments on that Page's posts and ads (the commenter's name, Facebook ID and comment) so the store can read and answer them, including automatic replies the store sets up. We keep them for up to 180 days." Handed to the HQ Supervisor through the Manager as an HQ docs task; this repo's privacy page is not edited here.
