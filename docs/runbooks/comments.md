# Facebook comments: switches, order of turning on, caps (task z8uq9mg9pn)

Written from the C1 code on main (78b35b2) and checked against the C2 code on `feat/z8uq9mg9pj-comment-rules` at c01d621 (files `comment_limits.py`, `comment_automation.py`, `comment_rules.py`, `api_comment_rules.py`, `switch.py`, `checks.py`, `tasks.py`, `migrations/0007_comment_rules.py`, `docs/c2-api-contract.md`), 2026-11-19. C2 is not merged yet: re-check these lines against main when it merges. Code and behaviour of C1 are in `inquiries.md` ("Facebook comments") and `docs/contracts/comments-api-c1.md`. Nothing here sets a variable. The CM5 staging variables are set by the Manager only, approved conditionally: after C1 and C2 are merged and verified on staging.

## Switches and variable

| Name | What it does | Needs | Off means |
|---|---|---|---|
| `INQUIRY_COMMENTS_ENABLED` | Comments view, `feed` webhook storage, manual public reply, private reply, hide | `INQUIRY_MESSENGER_ENABLED` and `INQUIRIES_ENABLED` (`check --deploy` reports `inquiries.E006` otherwise); the Meta app values (`META_MESSENGER_APP_ID`, `META_MESSENGER_APP_SECRET`) | Routes 404, `feed` events dropped, nothing stored, nothing sent to Meta |
| `INQUIRY_COMMENT_AUTOMATION_ENABLED` | Auto-reply rules: the rules routes, and Celery evaluating new comments and sending the queued replies | `INQUIRY_COMMENTS_ENABLED` (so also the Messenger inbox and `INQUIRIES_ENABLED`), the Meta app values, the comment tables (`0006`) and the auto-reply tables (`0007`); `check --deploy` reports `inquiries.E007` if it is on without comments, the Messenger inbox and `INQUIRIES_ENABLED` | The rules routes answer 404; nothing is evaluated; every queued send re-checks the switch first and is cancelled, so it also stops replies already queued |
| `META_COMMENTS_CONFIG_ID` | Second Facebook Login for Business configuration (six permissions), used only when the owner turns comments on | Created by Coach Ed in the Meta dashboard (step list: C0 notes, task z8uq9mg9pm); optional, not a secret | Unset: the login dialog asks for the six scopes (`scope`) instead; never the Messenger-only configuration `META_MESSENGER_CONFIG_ID` |

Off is safe: both switches default off (`config/settings.py`: on only when the value is exactly `1`); the Messenger inbox and its configuration `2209223909690313` are untouched by either.

## Order of turning on (staging)
1. Release with `inquiries.0006_comments` (C1) and `inquiries.0007_comment_rules` (C2); run them on staging first (additive), then `check --deploy`.
2. Coach Ed does the Meta dashboard steps (C0): second configuration, `feed` field, test post.
3. Set `META_COMMENTS_CONFIG_ID`, then `INQUIRY_COMMENTS_ENABLED=1`; owner clicks "Turn on comments" (re-connect with the extra permissions; `connect/` subscribes `feed`).
4. Check a comment on the test post reaches the Comments view, then manual replies.
5. Only then `INQUIRY_COMMENT_AUTOMATION_ENABLED=1` (`0007` must already have run) and one rule on the test Page; the owner switches "Comment auto-replies" on.
Turn off in reverse; setting a switch off stops processing at once. The CM5 staging variables are set by the Manager only.

## Caps and back-off (`comment_limits.py`, `comment_automation.py`)
- Per site (one Page per site): 30 automatic replies in a rolling hour and 300 in a Manila calendar day; public and private each count, and only replies queued, sending or sent count. Over a cap the reply is not sent: the row is stored as state `limit` and the comment list shows "Not auto-answered (limit)", left for a manual reply.
- Per person: one automatic reply per person per post in 24 hours. One automatic public and one automatic private reply per comment, ever (database constraint `inquiry_comment_action_auto_once`). Only a newly stored comment is evaluated; an edit never retriggers.
- Only top-level customer comments (not the Page's own, not hidden, with an author id), not older than 7 days, not already answered (public or private reply, or a Page reply under it). A private reply is also refused when the comment is over 7 days old at send time.
- Up to 20 rules per site (the 21st is refused with 409 `too_many_rules`); at most 20 keywords of 40 characters and 50 selected posts per rule.
- Random delay of 30 to 120 s between queueing and sending (Celery countdown).
- Back-off: a send that Meta refuses with a rate-limit outcome or error code 4, 9, 17, 32, 613 or 368 (spam block) is a strike. A strike pauses the site's automation for 1 hour and cancels its queued replies; the third strike in one Manila day turns the site's master switch off until the owner turns it on again (turning it on clears the strikes). A Meta rate-limit or spam error on a manual reply counts as a strike too (only while the site's automation is on). The pause never blocks manual replies.
- Public replies: no links (checked in every spintax option), at least 2 variants, at most 500 characters; private messages at most 1000. The same variant is not chosen twice in a row on a post.
- Limits live in one module, `comment_limits.py` (tunable).

## Retention and migration
Comments are kept 180 days from the comment time (rule `inquiry_comments`, `retention.md`; the job runs only with `RETENTION_ENABLED`, whatever the comments switch says, and does nothing before `inquiries.0006` is applied); `remove` events, the data-deletion callback and site/account disposal delete earlier. Migration `inquiries.0006_comments` (C1, additive). Run on staging before the switch; production not covered. C2 adds `inquiries.0007_comment_rules` (additive: three new tables, `InquiryCommentRule`, `InquiryCommentAutomation` and `InquiryAutoReply`, plus a choices-only `limit` state on `InquiryCommentAction`, which changes no database column), so C1 code works before `0007`. Celery tasks: `inquiries.evaluate_comment_rules` and `inquiries.send_comment_auto_reply` (they take a table id only); they need `REDIS_URL` (without it Celery runs eagerly in the web process and ignores the delay, `config/settings.py`). Every comment code path checks that the tables exist, and the automation checks the `0007` tables (`switch.automation_tables_exist()`), so code may be released before the migrations, but the automation switch must stay off until `0007` has run (and the comments switch until `0006` has).

## Privacy sentence (CM4, approved verbatim; tracked only)
Owned by HQ task z8uq9mg9pd (frontend branch `docs/z8uq9mg9pd-privacy-fb-comments`, ready and on hold until the C1/C3 release); not edited in this repository. "If a store connects its Facebook Page and turns on comments, we process the comments on that Page's posts and ads (the commenter's name, Facebook ID and comment) so the store can read and answer them, including automatic replies the store sets up. We keep them for up to 180 days." Handed to the HQ Supervisor through the Manager as an HQ docs task; this repo's privacy page is not edited here.
