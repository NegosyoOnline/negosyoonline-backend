# Meta App Review pack: comments (WIP, task z8uq9mg9pn)

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

## Reviewer steps, data handling
TODO (left for the new Supervisor): mirror sections 2 and 5 of the Messenger pack (side-session-notes messenger-app-review-pack-draft-2026-11-17.md), adding comment data and the 180-day retention.
