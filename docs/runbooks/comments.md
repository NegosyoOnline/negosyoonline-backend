# Facebook comments: switches, order of turning on, caps (WIP, task z8uq9mg9pn)

WIP: written from the C1 code on main (78b35b2) and the C2 task description (z8uq9mg9pj). Anything marked **per C2 task, confirm at merge** is not in code yet. Code and behaviour of C1 are in `inquiries.md` ("Facebook comments") and `docs/contracts/comments-api-c1.md`. Nothing here sets a variable; each is set by Coach Ed or the manager, staging first (CM5, asked when the variables are set).

## Switches and variable

| Name | What it does | Needs | Off means |
|---|---|---|---|
| `INQUIRY_COMMENTS_ENABLED` | Comments view, `feed` webhook storage, manual public reply, private reply, hide | `INQUIRY_MESSENGER_ENABLED` and `INQUIRIES_ENABLED`; Meta app values (`inquiries.E006` otherwise) | Routes 404, `feed` events dropped, nothing stored, nothing sent to Meta |
| `INQUIRY_COMMENT_AUTOMATION_ENABLED` (per C2 task, confirm at merge) | Auto-reply rules: Celery evaluates rules and queues replies | `INQUIRY_COMMENTS_ENABLED` | No rule runs; each queued task re-checks the switch before sending, so it also stops queued replies |
| `META_COMMENTS_CONFIG_ID` | Second Facebook Login for Business configuration (six permissions), used only when the owner turns comments on | Created by Coach Ed in the Meta dashboard (step list: C0 notes) | Unset: the dialog asks for the six scopes instead; never the Messenger-only configuration |

Off is safe: both switches default off; the Messenger inbox and its configuration `2209223909690313` are untouched by either.

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
Comments are kept 180 days from the comment time (rule `inquiry_comments`, `retention.md`); `remove` events, the data-deletion callback and site/account disposal delete earlier. Migration `inquiries.0006_comments` (C1, additive). Run on staging before the switch; production not covered.

## Privacy sentence (CM4, approved verbatim)
"If a store connects its Facebook Page and turns on comments, we process the comments on that Page's posts and ads (the commenter's name, Facebook ID and comment) so the store can read and answer them, including automatic replies the store sets up. We keep them for up to 180 days." Handed to the HQ Supervisor through the Manager as an HQ docs task; this repo's privacy page is not edited here.
