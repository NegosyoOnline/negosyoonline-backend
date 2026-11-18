# Meta App Review pack: comments (DRAFT, task z8uq9mg9pn)

**FILE ONLY AFTER THE MESSENGER REVIEW IS APPROVED (CM3, Coach Ed 2026-11-18).** Nothing here is submitted. Separate submission; the Messenger request (four permissions, configuration 2209223909690313) is not changed. Use-case text below is a draft; Meta fact checks are in the C0 notes.

## Permissions (Advanced Access)
### pages_read_user_content
> A store owner connects their Facebook Page in the NegosyoOnline dashboard (Settings > Inbox > Turn on comments). We use pages_read_user_content to receive and read the comments customers write on that Page's posts and ads (commenter name, Facebook ID, comment text, post id) so they appear in the owner's Inbox. Stored for up to 180 days, shown only to that store's owner, deleted when the comment is removed.

### pages_manage_engagement
> From the Inbox the owner replies publicly to a comment as the Page, hides a comment, and may set automatic replies (limited per hour, day and person, with varied text). We use pages_manage_engagement only to create replies to and hide comments on the owner's own Page. Private replies use pages_messaging, already in the Messenger review.

## Screencast script (staging, about 3 min)
1. Settings > Inbox > Turn on comments; the login dialog lists the six permissions (pause).
2. Page already connected; show "Comments on".
3. From a second account, comment on a test post ("How much?").
4. Inbox > Comments: the comment appears; open it.
5. Public reply: type and send; show it under the comment on Facebook.
6. Private reply: Message privately; show it in the commenter's Messenger.
7. Settings > Comment auto-replies: rule on keyword "price"; comment again; show the automatic public (and private) reply after the short delay.
8. Hide a comment; show it hidden on Facebook.
Recording rules as the Messenger pack: test store, no tokens on screen.

## Reviewer steps (staging)
Hosts as in the Messenger pack: `https://www-staging.negosyonline.com` (sign-in), `https://dash-staging.negosyonline.com` (dashboard), `https://api-staging.negosyonline.com` (API, webhook). Same test store "Meta Review Test Store" and same reviewer mailbox login as the Messenger submission (below); the Page is already connected from that review.

1. Open https://www-staging.negosyonline.com/?signin=1 and sign in with the reviewer login (section "Reviewer login").
2. Open **Settings > Inbox** (https://dash-staging.negosyonline.com/settings/inbox/). Under the connected Page click **Turn on comments**. Log in with Facebook and allow the six permissions. The card shows "Comments on". *(pages_read_user_content, pages_manage_engagement are requested here; the other four are the Messenger ones already reviewed.)*
3. On Facebook, create a post on the test Page (or use the one prepared), then from a second Facebook account that has a role on the app, comment on it, for example "How much?".
4. Open **Inbox** (https://dash-staging.negosyonline.com/inbox/), choose **Comments**, press **Refresh**. The comment appears with the commenter's name, the post and the time. *(pages_read_user_content.)*
5. Open the comment, type a reply under "Reply in public" and press **Send**. The reply appears under the comment on Facebook, posted as the Page. *(pages_manage_engagement.)*
6. Press **Message privately**, type a message and send. It reaches the commenter in Messenger (one private reply per comment, within 7 days of the comment). *(pages_messaging, already reviewed.)*
7. Open **Settings > Inbox > Comment auto-replies** and add a rule: comment contains "price", public reply `{Hi|Hello} {name}! We sent you a message.` (rules need at least two variants and no links). Comment "price?" again from the second account; after a short delay (30 to 120 seconds) the automatic public reply appears under it. *(pages_manage_engagement.)* The panel shows today's automatic replies against the daily cap.
8. Press **Hide** on a comment: it is hidden on Facebook, not deleted. *(pages_manage_engagement.)*
9. Optional: **Settings > Inbox > Turn off comments** (or Disconnect): the `feed` subscription stops and nothing more is stored.

(CONFIRM at submission: whether reviewers bring their own test Page and user, as for the Messenger pack; if so steps 3 to 8 use theirs.) The button labels are the C4 labels "Turn on comments" and "Comment auto-replies"; check the rest of the wording in steps 2, 7 and 9 against the merged screens before filing.

## Reviewer login
Same method as the Messenger submission (R2, Coach Ed 2026-11-17): a reviewer mailbox of our own; the dashboard sends a 6-digit sign-in code to it.
- Mailbox address: ____ (Coach Ed fills in).
- **Password: left empty on purpose.** Coach Ed types it into the test-credentials box of Meta's App Review form himself. No session, file or message holds it.
- Text for the box (Coach Ed adds the password): "Sign in at https://www-staging.negosyonline.com/?signin=1 with the email below. We email a 6-digit code to it. Open the mailbox at <mailbox web-mail link> with the password below and enter the code; it works once and expires in 10 minutes. Email: ____. Mailbox password: (entered by Coach Ed)."

## Data handling (App Review and Data Use Checkup answers)
- **What data:** for each comment on the connected Page's posts and ads: Meta comment id, post id, the commenter's Page-scoped id and name, the comment text and time; a short caption snippet and permalink of the post or ad; for each reply or hide we make: the outcome code and Meta's id only (never the reply text in the log). The Page access token (encrypted at rest, never sent to a browser) is shared with the Messenger connection.
- **Purpose:** only to show the store owner the comments on their own Page, let them answer or hide them, and send the automatic replies they set up. No ads, profiling, resale or model training. We read nothing from people who did not comment.
- **Who can see it:** only the owner of the store the Page is connected to; NegosyoOnline staff for support only, logged. A Page connects to one store at a time.
- **Automatic replies:** only to top-level customer comments, only from rules the owner wrote. Limits (checked against the C2 code at c01d621): 30 an hour and 300 a day per Page (public and private each counted; the day is a Manila day), one per person per post per 24 hours, one public and one private reply per comment ever, only comments under 7 days old, up to 20 rules, random 30 to 120 s delay, no links in public replies, paused for an hour when Meta signals a rate limit or spam block (also on a manual reply) and switched off until the owner turns it on again after three in a Manila day. The pause never blocks the owner's manual replies.
- **Retention:** comments are deleted 180 days after the comment was made. A comment the person removes on Facebook (`remove` event) is erased at once and any queued reply is cancelled. On Disconnect the token is deleted.
- **Deletion requests:** Meta's data-deletion callback (already built for Messenger) deletes the comments, with their action rows, on the owner's connected Pages, and returns a status URL and confirmation code; the deauthorize callback invalidates the connection and deletes the token. When the store or account is erased, its comments are deleted. *(CONFIRM against merged code before answering: C1 runbook `inquiries.md`, section Facebook comments.)*
- **Processors:** hosting on Railway (database and API) and the dashboard on Vercel. (CONFIRM the hosting region before answering any question about where data is stored.)
- **Security:** webhook requests are verified by X-Hub-Signature-256; tokens are encrypted; HTTPS only; no Meta tokens, comment text or raw Meta bodies in logs (the webhook log holds counts only).
- **Sharing with third parties:** none, except the processors above.
- **Privacy Policy:** must contain the comments sentence before filing. The sentence (CM4, approved verbatim) is in `docs/runbooks/comments.md`; the page is HQ task z8uq9mg9pd (not edited here). Filing check: open the live Privacy Policy URL and confirm the sentence is there.
- **Contact:** the privacy contact in our Privacy Policy (Coach Ed to confirm the name or email used).

## Before filing (checklist for the Supervisor and Coach Ed)
1. Messenger review approved (CM3). 2. Privacy page with the comments sentence is live (z8uq9mg9pd). 3. C1, C2, C3, C4 on staging and the screencast recorded as above. 4. `META_COMMENTS_CONFIG_ID` created per the C0 steps (`c0-meta-comments-steps-2026-11-19.md` in the Manager notes) and the six permissions shown in the dialog. 5. Reviewer mailbox ready; Coach Ed enters the password himself. 6. Coach Ed submits in the Meta dashboard; no session does.
