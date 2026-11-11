# SMS inbox, reply and compose for HQ staff: design (2026-11-11)

ClickUp task: z8uq9me128 "SMS inbox, reply and compose (HQ)" (https://app.clickup.com/t/z8uq9me128), a subtask of z8uq9md33v "NPC Build 1 — SMS Carrier & Data-Flow Controls".

Status: **design for Coach Ed's approval. Nothing is built.** This document creates no code, migration, setting, variable or switch. One decision is open and is his: where the screens are served (section 3). The mockup of the screens is in `docs/superpowers/specs/mockups/2026-11-11-sms-inbox/` (section 13); visual acceptance is his. Revised on 2026-11-11 after his feedback on the first mockup (R7, R8 and section 2.1): the menu item is "Inbox", with a horizontal menu of platforms inside it, and only SMS is designed and built. Revised again the same day after his feedback on that mockup (R9 and section 2.2): the heading block is removed, the status chips and "New SMS" share one line, and search and notification buttons are added at the top right. Bulk SMS (section 18) was then added to the design by five rulings, R10 to R14, for the launch announcement only. His feedback on the Bulk SMS page (R15) then added a CSV upload and a list of bulk sends with a stop button. Contacts and groups (section 19) were added by R16 to R19, so the scope is now three parts: the inbox, Bulk SMS and contacts. After approval comes a plan, also for approval, before any code.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9me128: description and its comments, read 2026-11-11; the feedback behind R9 and section 18 was relayed by the coordinator the same day |
| Parent | ClickUp z8uq9md33v: description and its ten comments, read 2026-11-11 |
| Gateway design | `docs/superpowers/specs/2026-11-10-sms-gateway-mobile-verification-design.md`, sections 5.6, 6.2, 6.3, 6.7 and 14 |
| Runbook | `docs/runbooks/sms-gateway.md`, `docs/runbooks/retention.md` |
| Code | `apps/notifications`, `apps/operators`, `apps/common`, `apps/accounts/retention.py`, `config/settings.py` at `1ba9cc6` |
| Dashboard look | Frontend repository at `36d2ec4`, read only: `apps/dash/public/index.html`, `apps/dash/public/assets/css/dash.css`, `apps/dash/next.config.ts`. For the platform menu: the Settings menu (`.set-tabs`) in the same two files at `origin/main` `5802934` |
| Phone app | `NegosyoOnline/negosyoonline-sms-gateway` at `0e7ae88`, read only: its manifest and plan, and decision record 4 on the Parent |

## 2. Rulings this design applies (not reopened)

All from Coach Ed, 2026-11-11, recorded as comments on the Task.

| # | Ruling | His words |
|---|---|---|
| R0 | A separate HQ tool now, made ready to become part of the Dedicated SMS Line add-on (z8uq9mb19f) later | "what we will be building is a separate HQ tool, but make it ready because soon, it will be part of the add-on" |
| R1 | Incoming texts may be stored, only after the NPC and privacy wording is amended | "Yes, amend first" |
| R2 | Inbox messages and staff texts are kept 365 days, the same as send records. A disposed account's messages follow the same rule | "Yes, 365 days" |
| R3 | Only HQ staff with an explicit SMS inbox permission, through the existing two-step HQ sign-in. Every opened conversation, reply and composed text is written to the operator audit record | "Yes, as proposed" |
| R4 | Staff write free text through the existing send function: link gate, 160 characters, counted in the 2,000-a-day cap, the sending interval kept, no marketing | "Yes, free text" |
| R5 | Staff may text any Philippine mobile | "Any PH mobile" |
| R6 | The screens use the Subscriber dashboard's visual design | "let's use the Subscribers Dashboard design... this current HQ design is not good" |
| R7 | The menu item is "Inbox". Inside it, a horizontal menu lists the platforms, laid out like the dashboard's Business settings page | "in the menu, make it Inbox not 'sms inbox'"; "this inbox menu, we will put here all the possible inboxes like fb messenger, insta, tiktok, email, sms, and webchat."; "the design is kindalike business settings where there are horizontal menu and there listed all the platforms" |
| R8 | Only SMS is designed and built in this task. The layout is prepared for the other platforms | "Yes, this is for SMS only. I'm just referring to the design so that it will be prepared for other platforms." |

The coordinator's readings recorded with those rulings, which this design confirms: R0 means records owned per Site, Subscriber or HQ, one access check, and a reusable API, with no add-on screen, billing or Subscriber access now. R6 is about how the screens look; it does not move the tool to the Subscriber dashboard or give a Subscriber access.
| R9 | Layout feedback on the revised mockup: no heading block; the status chips and the new-SMS button on one line, without the "kept 365 days" chip; search and notification buttons at the top right | On the heading block: "remove this". On the chips: "then align this and the New SMS, then remove the text are kept". "add search and notification at the top right" |
| R10 | Bulk sending is added, for the launch. More than 2,000 numbers, sent in batches; the cap and the gap are not raised | "add bulk sending, we need this during launch to send sms to twcinc subscribers"; "It's more than 2,000. Just worry, don't worry on this because I will do it by batch." |
| R11 | A launch announcement by SMS is allowed as an exception to the no-marketing rule, once the NPC and privacy wording is amended. Other marketing stays forbidden | "Yes, amend first" |
| R12 | He supplies the TWCinc numbers himself as a file, with no system link to TWCinc | "Yes, a file" |
| R13 | The announcement lets people opt out by replying STOP, kept on a do-not-text list | "Yes, add STOP" |
| R14 | Bulk texts use the same SIM as the codes; the risk of a carrier block is accepted | "Same SIM, accept risk" |
| R15 | Bulk SMS has an input for uploading a CSV file of mobile numbers, and a list of bulk sends that shows a batch is still sending and lets him stop it | "Number one is there a input where we can upload a CSV file for the mobile numbers? Then number two, for the list of the bulk SMS, there should be a list that is informing me that it is still sending, and I have the capability to stop it." |
| R16 | Contacts are added: a menu where staff enter a first name, a last name and a mobile number, with groups, and Bulk SMS can send by group | "I think we need to include also the kind of like contacts for this one. Where it can be selected during the bulk sending. So there should be a menu for contacts where you can input name, last name, and their mobile numbers. And during the bulk sending, we will be able to send it by group. So in the contacts, we can put grouping there. So that through through bulk sending, we will be able to send a SMS based on their grouping." |
| R17 | A contact is kept until HQ staff delete it. No automatic deletion. Texts stay on the 365-day rule | "Until staff delete" |
| R18 | Contacts and groups are seen and edited by the same HQ staff who hold the SMS inbox permission. No separate contacts permission | "Same inbox staff" |
| R19 | Staff may upload a CSV file of contacts (first name, last name, mobile number) into a chosen group | "Yes, allow CSV" |

R9 to R19 are each a comment on the Task, re-read there on 2026-11-11. Section 18 applies R10 to R15; section 19 applies R16 to R19.

### 2.1 What R7 and R8 change in this design

- **The menu.** The side menu item is "Inbox" and the page title is "Inbox". Inside it is a horizontal menu of six platforms: SMS, Facebook Messenger, Instagram, TikTok, Email, Web chat (section 13).
- **Only SMS works.** The other five show one plain "not yet available" state. They have no screen, no record, no setting, no switch and no date. Each is its own task with its own approvals (section 16).
- **Records carry their platform.** A conversation and a message each name their platform (sections 4.2 and 4.3), so another platform is added later as new rows and new code beside SMS, without rebuilding the inbox.
- **The access check and the audit entry name the platform** (sections 7 and 8). Access to SMS gives access to nothing else.
- **The SMS rules are SMS-only.** The link gate, the 160 characters, the 2,000-a-day cap, the sending interval and the Philippine mobile check (section 6) are rules of the SMS platform. They are not inbox-wide rules, and no rule is assumed for any other platform.
- R0 to R6 and the earlier choices in this document are unchanged.

### 2.2 What R9 changes in this design

- **No heading block.** The Inbox pages have no eyebrow, title or description. The side menu and the platform menu say where the user is. The conversation page keeps the masked number as its heading and the compose page keeps "New SMS", because those name the thing on the page; neither has an eyebrow or a description. Each page still has a heading for screen readers.
- **One status line.** On the SMS list, the status chips ("Gateway phone online", "610 of 2,000 texts used today") are on the left and the "New SMS" button on the right, on one line. The "Texts are kept 365 days" chip is removed from the screen. The 365-day rule itself (R2, section 11) is unchanged, and the compose page still tells staff the text is kept 365 days.
- **"New SMS".** The button, the compose page and its send button say SMS, as he called it.
- **Search and notification buttons** at the top right, beside the theme button, in the dashboard's order: search, theme, bell (section 13).

Not approved by any of them, and not done here: any build, any migration, any setting or Railway variable, turning the incoming switch on anywhere, anything on staging or production, restyling the existing HQ pages, and anything for a platform other than SMS.

## 3. The open decision for Coach Ed: where the screens are served

R3 keeps the tool behind the HQ two-step sign-in on the HQ host. R6 asks for the Subscriber dashboard's look, and that dashboard lives in the frontend repository on another host. The two have to meet somewhere.

| Option | What it is | Security consequence | Cost |
|---|---|---|---|
| **A. Django pages on the HQ host, styled to match the dashboard (recommended)** | Server-rendered pages under `/inbox/` in the HQ URL set (`apps/operators/hq_urls.py`), with their own base template that carries the dashboard's colours, type, cards, chips, buttons, dark sidebar and phone tab bar. The existing HQ pages are not touched | Nothing new to trust. The pages, the numbers and the message text are served only by the HQ host, only to a session that passed both steps. The session cookie stays as it is: host-only, `HttpOnly`, `SameSite=Strict`. No other origin is allowed to call HQ, so no CORS rule is added. Django's CSRF check covers the reply and compose forms. The pages load nothing from another host: the font is served by HQ itself | The look is a second copy of the dashboard's styles, kept in the backend. When the dashboard's design changes, this copy is updated by hand |
| B. A surface in the frontend repository | A new page or app in the frontend repository (hosted on Vercel like the dashboard) that calls the backend for inbox data | The HQ session cookie cannot be read or sent by another origin, so one of three things must change: the HQ host must allow a second origin to make signed-in calls (a CORS rule with credentials, and a weaker cookie), or HQ must issue a token that the browser page holds, or the two-step sign-in must be built again in the frontend. In every case a second host and its deploy pipeline join the trust boundary of the staff tool: whoever can change that deploy can read every conversation. Message text and numbers would be handled by script on a host that is not HQ | Shares the real dashboard styles. Needs new sign-in plumbing, a frontend task and repository, and new approvals |
| C. HQ pages that load the dashboard's stylesheet from the dashboard host | Option A's pages, but with a `<link>` to the dashboard's own CSS file | A stylesheet from another host can change what HQ staff see and can read parts of the page (CSS can send attribute values to a remote address). The dashboard host's deploy would then be able to act inside HQ pages. HQ would depend on another host being up | Always matches the dashboard with no copy to maintain |

**Recommendation: A.** It is the only option where R3 holds without changing how HQ signs staff in, and where no host other than HQ ever handles a number or a text. Its cost, a copied look, is small and visible. The mockup is drawn as option A would look.

Whichever is chosen, the rest of this design does not change: the records, the access check, the service functions and the API shape in section 10 are the same.

## 4. Records

A new app, `apps/inbox`, as section 14.2 of the Gateway design proposed. The send record in `apps/notifications` keeps having no text column.

### 4.1 Who owns a record (R0)

Every conversation has exactly one owner, and every message is reached only through its conversation.

| Owner | Meaning | Used in this build |
|---|---|---|
| `hq` | The conversation is on NegosyoOnline's own line (the system phone) | Yes. Every conversation in this build |
| `site` | The conversation is on a Site's dedicated line | No. The add-on assigns a phone to a Site |
| `account` | The conversation belongs to a Subscriber rather than to one Site | No. Reserved for the add-on |

The owner is always taken from the phone the text arrived on or leaves from, never from anything the phone or a browser sends. One function, `owner_of(device)`, answers it. Today it always answers `hq`, because no phone is assigned to a Site. The add-on changes that one function when it adds the assignment.

A sender's number is not matched to a Subscriber account. Two accounts may verify the same number (Gateway design D11), so a number is not an identity, and linking a text to an account would be a new use of the verified number that no ruling covers.

### 4.2 `Conversation`

One row per owner, platform, line and other party.

| Field | Meaning |
|---|---|
| `public_id` | Opaque id (`conv_…`), the only id a screen or the API shows |
| `owner_kind` | `hq`, `site` or `account` |
| `site`, `account` | The owning Site or Subscriber. Both empty for `hq`. A database constraint allows exactly the combination `owner_kind` names |
| `platform` | `sms`, the only value in this build. The platform the conversation is on. Another platform is a new value added by its own task |
| `line` | SMS: the phone (`SmsDevice`) whose SIM the conversation is on. What stands for the line on another platform is that platform's task to decide |
| `contact` | The other party. SMS: a Philippine mobile is stored as `+639XXXXXXXXX`. Anything else (a carrier's short code, a sender name, a foreign number) is stored as received, at most 32 characters |
| `can_reply` | SMS: true only when `contact` is a Philippine mobile |
| `unread_count` | Incoming texts since the conversation was last opened by anyone with access |
| `last_message_at`, `created_at` | For ordering the inbox |

One conversation per owner, platform, line and contact.

### 4.3 `ConversationMessage`

One row per text, with its text. This is the inbox record (direction `in`) and the staff-text record (direction `out`).

| Field | Meaning |
|---|---|
| `public_id` | Opaque id (`msg_…`) |
| `conversation` | The conversation, and through it the owner |
| `platform` | The same value as its conversation's, `sms` in this build. Kept on the message too, so a message always says which platform it travelled on and the retention rule can select by it. A database constraint and the one storing function keep the two equal |
| `direction` | `in` or `out` |
| `body` | The text. The only column in the database that holds message text. At most 2,000 characters; a longer incoming text is cut and `clipped` is set |
| `gateway_ref` | SMS. `in`: the id the app gave the text. Unique per phone, which is what makes a repeated `received` call harmless. `out`: the `public_id` of the `SmsMessage` send record, kept as text with no foreign key, the way a mobile challenge refers to its send record |
| `device`, `sim_slot` | SMS. `in`: the phone and SIM slot it arrived on |
| `author` | `out`: the staff user who wrote it. Empty for `in` |
| `kind` | `out`: `reply` or `compose` |
| `occurred_at` | `in`: when it arrived, by the phone's clock (the server's time if the phone's value cannot be read) |
| `created_at` | When the server stored it. Retention counts from here |

An outgoing message has no status column. Its status is read from its send record when it is shown, so the gateway's `report` and `sweep` code does not change and two copies of a status cannot disagree.

For every staff text there are therefore two rows: the `ConversationMessage` (with the text, owned through its conversation) and the `SmsMessage` send record (number, purpose, status, times, no text), as for every other text the gateway sends.

### 4.4 `InboxAccess`

One row per staff user and platform (section 7). In this build every row is for `sms`.

| Field | Meaning |
|---|---|
| `operator` | The staff user |
| `platform` | `sms`. The platform the row gives access to. One row per staff user and platform |
| `can_send` | May reply and compose. Without it the user can only read |
| `granted_by`, `granted_at` | Who ran the command, and when |

## 5. Storing an incoming text

### 5.1 The switches

An incoming text is stored only when all three are on. Each is off today.

| Switch | Where | Who turns it on |
|---|---|---|
| `SMS_GATEWAY_ENABLED` | Railway variable, exists | Already on for staging. Unchanged |
| `SMS_INBOX_ENABLED` | Railway variable, **new** | Coach Ed's approval, per environment. While off: the inbox tables are never read or written, every Inbox page answers the HQ "Page not found", `received` discards as it does today, and the retention rule does nothing. The code can be merged and deployed dark, the way the gateway was |
| "Forwards incoming texts" on the phone's row | `manage.py sms_device incoming on --name system --actor USERNAME`, exists, audited | Coach Ed's approval, each time, and **only after the NPC and privacy wording is amended** (R1) |

The `sms_device incoming on` command is changed to refuse while `SMS_INBOX_ENABLED` is off, so the phone is never told to forward texts that would be thrown away, and its closing line no longer says nothing is stored.

### 5.2 What `received` does

The call keeps its shape (`apps/notifications/gateway.py`): up to 20 texts, each with `id`, `from`, `text`, `received_at`, `sim_slot`; the answer is 204. The phone app does not change.

| Case | The server |
|---|---|
| `SMS_INBOX_ENABLED` off, or this phone's switch off | Answers 204 and discards the body unread, as today |
| Both on, the body is a valid list | Stores every text in one database transaction, then answers 204. A text whose `id` this phone already sent is skipped, so a repeat changes nothing |
| Both on, the body is not a valid list (not JSON, more than 20 items, an item without a text `id` and `from`) | Stores nothing and answers 400. The phone keeps the texts and tries again; it drops them after 7 days. One ERROR log line, `sms_incoming_rejected`, with the count only |

Storing one text: find or create the conversation for this owner, platform (`sms`), line and sender; write the `in` message; add one to `unread_count`; set `last_message_at`. The storing step is one function in `apps/inbox`, which replaces the body of `store_incoming`. Nothing else in the three gateway calls changes.

- The sender is normalised with `normalise_ph_mobile`. If it is not a Philippine mobile it is kept as received and the conversation cannot be replied to.
- No log line, cache entry or audit entry ever holds a number or a text.
- **Every text the SIM receives is stored**, including texts that have nothing to do with NegosyoOnline: the carrier's notices, wrong numbers, and one-time codes other services send to that SIM. There is no filter, because any filter would have to read the text to decide. The NPC wording must say so (section 14).
- The phone keeps a text it could not hand over, sealed, for up to 7 days and up to 500 texts, including while its own gateway switch is off (Parent, decision record 4). Those texts arrive later with their original arrival time.
- The phone's own Messages app keeps its copy of every arriving text, as on any phone. The runbook's auto-delete control covers it.

## 6. Replying and composing (R4, R5)

Everything in this section is a rule of the SMS platform: the send function, the link gate, the 160 characters, the cap, the sending interval and the Philippine mobile check. None of it is an inbox-wide rule. Another platform brings its own sending rules in its own task.

### 6.1 The path

Both go through the one send function, `send_sms` in `apps/notifications/services.py`. Nothing else queues a text.

1. The text is checked with the send function's own rule (`sms.text_problem`): not empty, at most 160 plain SMS characters, no web link in any form the link gate knows.
2. For a composed text, the number is checked with `normalise_ph_mobile` (section 6.4).
3. `send_sms` is called with a new declared purpose, `inbox_reply` or `inbox_compose`. It writes the text-free send record, takes one of the day's slots, checks the kill switch and that the phone is online, and holds the text encrypted in Redis until the phone takes it.
4. If it answers `queued`: the `out` message is written with the text and the send record's id, the conversation is created or updated, and the audit entry is written. All in one database transaction with step 3, so a text is never queued without its inbox row.
5. If it answers `refused`: nothing is stored in the inbox. The page shows why and keeps what the staff member typed. The attempt is still audited (section 8).

The staff text waits for the phone for up to 1 hour, then the send record becomes `expired` and the conversation shows "Not sent". A code's 10 minutes is too short when several staff texts wait their turn (section 6.3).

### 6.2 What changes in `apps/notifications`

| Change | Why |
|---|---|
| Two entries in the closed purpose list: `inbox_reply` and `inbox_compose` | R4. The list's comment ("no reply and no marketing text") is amended: replies are now allowed; marketing still has no purpose and cannot be sent |
| A third category, `inbox`, beside `code` and `status` | A staff text is neither. It has no Site in this build, so it counts against the platform limit only. It carries no email fallback |
| A purpose can be declared "paced" | Today only the `marketing` class carries the 18 to 25 second gap. Staff texts must carry it too (section 6.3) |

The link gate, the 160-character rule, the daily counts, the kill switch, the encrypted hold and the phone calls are used as they are.

### 6.3 Order, interval and the cap

- **A code is always first.** Staff texts use the existing `reply` sending class, which is handed to the phone after every waiting code. A code's gap stays 0, and the phone's gap clock ignores texts without a gap, so a staff text never delays a code.
- **The sending interval applies to staff texts.** R4 says staff texts "keep the sending interval", which is the random 18 to 25 seconds of Coach Ed's earlier ruling. A staff text therefore carries that gap: the phone sends it no sooner than 18 to 25 seconds after the last paced text it sent, and the server hands the phone one paced text at a time. The first text after a quiet spell leaves on the phone's next poll, about 5 seconds.
- **This differs from the Gateway design, and is reported, not hidden.** Its section 6.7 gave the `reply` class no gap, written for the add-on before R4 existed. R4 is later and explicit, so this design follows R4. If Coach Ed meant replies to leave at once, it is a one-word change to the two purposes.
- **The cap.** Each staff text takes one slot of the platform's 2,000 a day (Manila time), the same count codes use. Consequence, stated plainly: staff texts and codes share one allowance, so a day of heavy staff texting leaves fewer slots for codes. The admin can change the limit or stop all sending in seconds (`SmsPolicy`).
- **One text, one number.** (This paragraph is about reply and compose. Bulk SMS, section 18, is the one path with a list, with its own controls.) The compose form takes one number. There is no list, no paste of many numbers, no upload and no "send to all". This, the audit record and a limit of 60 staff texts per staff user per hour are what stand between "any Philippine mobile" and bulk sending. Marketing stays forbidden; the compose page says so. Code cannot recognise a marketing text, so the audit record is the control.

### 6.4 The Philippine mobile check

The number is accepted in any form `normalise_ph_mobile` accepts (`+639…`, `639…`, `09…`, `9…`, with spaces, dashes, brackets or dots) and is stored and sent as `+639XXXXXXXXX`. Anything else is refused on the form with "Enter a valid Philippine mobile number." It works for every Philippine network; there is no list of allowed numbers. A reply is offered only in a conversation whose contact is a Philippine mobile.

Composing to a number that already has a conversation adds to that conversation.

### 6.5 What staff see when a text is not sent

| Case | Shown on the form, text kept |
|---|---|
| Empty, too long or not plain SMS characters | "The text must fit one SMS: at most 160 plain characters." |
| A web link | "The text must not contain a web link." |
| Not a Philippine mobile | "Enter a valid Philippine mobile number." |
| Daily limit reached | "Today's text limit is reached. Nothing was sent." |
| Sending stopped (kill switch) | "Text sending is stopped. Nothing was sent." |
| Phone offline, or the gateway off | "The gateway phone is offline. Nothing was sent." |
| Too many texts by this staff user in an hour | "Too many texts in the last hour. Please wait and try again." |

After a text is queued, its line in the conversation shows "Waiting", then "Sent", or "Not sent" with the reason in words. "Sent" means the phone handed the text to its SIM. There are no delivery receipts.

## 7. Who may use it (R3)

### 7.1 The permission

- **Its name:** "SMS inbox", as R3 ruled. It is access to the SMS platform of the Inbox and to nothing else; another platform has its own permission, granted separately. **Two levels:** read, and read with send (`can_send`). A staff user who only needs to look something up is given read.
- **It is explicit.** It is a row in `InboxAccess`, not a Django permission. A superuser has no access without a row, and the Django admin's user page cannot grant it.
- **Granting is a terminal command**, as admin identities and phones are managed: `manage.py sms_inbox_access grant --user USERNAME [--send] --actor USERNAME`, `revoke`, and `list`. `grant` and `revoke` write an audit entry. Who is granted access is Coach Ed's decision, per person.
- **Sign-in:** the existing HQ two-step sign-in. The pages are in the HQ URL set, so they see only the user of an HQ session that passed both steps.
- A user without access, or any visitor while `SMS_INBOX_ENABLED` is off, gets the same HQ "Page not found" as for a path that does not exist.

### 7.2 The one access check

One function in `apps/inbox`, `scope_for(actor, platform)`, answers who is asking and what they may reach on that platform. The platform is always named; there is no scope that spans platforms.

| Actor | Scope | Built now |
|---|---|---|
| A staff user with an `InboxAccess` row for `sms`, asking for `sms` | `sms` conversations owned by `hq`. Send only with `can_send` | Yes |
| Anyone asking for a platform other than `sms` | None | Yes. The answer is always none, because no other platform exists |
| A staff user without a row for `sms` | None | Yes |
| A Subscriber account (later) | `sms` conversations owned by that Subscriber's Site, when the add-on is active for it | No. The add-on adds this branch |

Every list, read, reply and compose starts from the scope's own query. No view or service function reaches a conversation or message any other way, and a `public_id` outside the scope answers "not found", the same as one that does not exist. Admitting a Subscriber later is a change to this one function, not to the screens or the service functions.

HQ staff reach only `hq` conversations. A Site's conversations with its own customers are not HQ's to read, and the add-on decides whether HQ ever sees them.

## 8. The audit record (R3)

Written through `apps.operators.audit.record`, in the same transaction as the action. **No entry ever holds message text.**

| Action | When | `detail` (kept) | `personal` (blanked after 1 year) |
|---|---|---|---|
| `sms_inbox_open` | Each time a conversation page is opened | The owner kind, how many messages were shown | The contact, masked |
| `sms_inbox_reply` | Each reply, sent or refused | The message's and the send record's ids, the outcome (`queued` or `refused`), the reason when refused, the length in characters | The contact, masked |
| `sms_inbox_compose` | Each composed text, sent or refused | The same, and whether it started a new conversation | The number, masked |
| `sms_inbox_access` | Each `grant` and `revoke` | The staff user it was for, and the level | Nothing |

- Every entry's `detail` names the platform (`platform: sms`), so the record says which platform was read or written on once there is more than one.
- The operator is the signed-in staff user. The target is the conversation's `public_id`.
- The three inbox actions are declared `PERSONAL`, because a masked number is still part of a person's number. The existing rule `admin_audit_personal_values` blanks `personal` one year after the entry is written, which matches R2. That rule runs only where `RETENTION_ENABLED` is on and, until slice 4, only by command (`docs/runbooks/retention.md`).
- `sms_inbox_access` is declared `KEPT`.
- Opening the inbox list writes no entry, because the list shows no message text (section 9). Opening the "not yet available" state of another platform writes none either: it shows no data.

## 9. Masking

- **The inbox list** shows, for each conversation: the masked number (`+63917***4567`, through `mask_mobile`), the time of the last text, whether the last text came in or went out, and an unread count. **It shows no message text**, not even a preview, so that reading a text always means opening a conversation, which is audited.
- **The conversation page** shows the masked number and the texts. The full number is not shown anywhere in the tool.
- **Search** matches a full number typed by the staff member, never a fragment, as the admin's SMS list does.
- **The compose form** is the one place a full number appears: the staff member types it. After sending, the conversation shows it masked.
- A sender that is not a mobile number (a short code or a sender name) is shown as received: it is not a person's number.
- The Django admin gets no list of conversations or messages. The existing `SmsMessage` admin list shows staff texts' send records like any other, masked, with no text.

## 10. The API shape the Subscriber dashboard can reuse

The pages are thin. They call four service functions in `apps/inbox`, each taking a scope (section 7.2), which names the platform, and returning plain values. Each conversation and message returned carries `"platform": "sms"`, and the later routes take the platform as a `platform=sms` parameter. The HQ pages render those values in templates. The Subscriber dashboard can later be given the same four functions as JSON under `/v1/inbox/`, behind the account session and the same scope check.

| Service function | Later route | Returns |
|---|---|---|
| `list_conversations(scope, state, number, cursor)` | `GET /v1/inbox/conversations/?state=unread&number=&cursor=` | `{"results": [{"id", "contact_masked", "can_reply", "unread", "last_message_at", "last_direction"}], "next": "..."}` |
| `get_conversation(scope, id, cursor)` | `GET /v1/inbox/conversations/{id}/` | `{"id", "contact_masked", "can_reply", "messages": [{"id", "direction", "body", "clipped", "kind", "status", "reason", "author", "occurred_at"}], "next": "..."}`. Marks it read. The caller writes the audit entry |
| `reply(scope, id, text)` | `POST /v1/inbox/conversations/{id}/messages/` with `{"text"}` | `201` with the message, or an error |
| `compose(scope, to, text)` | `POST /v1/inbox/messages/` with `{"to", "text"}` | `201` with `{"conversation_id", "message"}`, or an error |

Errors use the API's one error shape: `invalid_mobile` (`fields.to`), `invalid_text` (`fields.text`), `rate_limited` (429), `sms_paused` and `sms_unavailable` (503), and `not_found` for anything outside the scope.

**No JSON route is mounted in this build.** The four functions and their return values are built and tested; the routes are a few lines each for the add-on to add, with its own sign-in and its own tests. A route nobody uses yet is an open door with no user, so it is left out.

## 11. Retention and disposed accounts (R2)

| Rule | What | When |
|---|---|---|
| `inbox_messages` (new) | Delete `ConversationMessage` rows of the `sms` platform, incoming and outgoing, then delete any conversation left with no message | 365 days after the message's `created_at` |
| `sms_messages` (exists) | Delete `SmsMessage` send records, staff texts included | 365 days after `created_at` |
| `admin_audit_personal_values` (exists) | Blank the masked number in the inbox audit entries | 1 year after the entry |

- `inbox_messages` is registered in the shared retention registry and added to the daily rules the phone's `poll` starts, beside `sms_messages`. It is not behind `RETENTION_ENABLED`; while `SMS_INBOX_ENABLED` is off it touches no table.
- Rows are deleted, not blanked.
- R2's 365 days is ruled for SMS. The rule selects `sms` rows only; how long another platform's messages are kept is decided in that platform's task.
- **A disposed account.** Nothing in the inbox is tied to an account in this build (section 4.1), so disposing of an account does not find or remove any inbox text, exactly as it does not remove SMS send records today. The texts go at 365 days. That is R2 as ruled.
- There is no screen to delete a conversation early. A person's request to have their texts removed before 365 days has no path in this build (section 14, point 9).
- The account export (`account_exported`) does not include inbox texts, for the same reason: they are not tied to an account.

## 12. What the build would need, each needing Coach Ed's approval

None of these is created by this document.

| Kind | Item | Notes |
|---|---|---|
| Migration | `inbox.0001_initial`: `Conversation`, `ConversationMessage`, `InboxAccess` | Additive, a new app. Creating it is covered once a plan that names it is approved; running it on staging is the coordinator's step under the standing approval; production is never covered |
| Migration | `apps/notifications`, next free number (`0004` as of `1ba9cc6`): the `category` choices gain `inbox` | Changes the allowed values only. No column or row changes |
| Setting | `apps.inbox` added to `INSTALLED_APPS`; `SMS_INBOX_ENABLED` read in `config/settings.py` | Code, reviewed in the pull request |
| Railway variable | `SMS_INBOX_ENABLED=1` | Per environment. Never before `inbox.0001` is applied there |
| Switch | `sms_device incoming on --name system` | Per environment, each time. **Only after the NPC and privacy wording is amended (R1)**. This is the step that starts storing people's texts |
| Access | `sms_inbox_access grant` for each named staff user | Coach Ed names who, and whether they may send |
| On the phone | Allow the app's "receive SMS" permission when it asks, after the switch is on | Unproven on the real phone: the restricted-settings and Play Protect steps (Runbook) |
| Documents | `docs/runbooks/sms-gateway.md` ("Incoming texts: the switch stays off" is rewritten), `docs/runbooks/retention.md`, the Gateway design's section 14 (superseded by this document), the scope document | With the build's pull requests |
| Outside this repository | The privacy page and the NPC documents (section 14) | Coach Ed's wording. Its own task |
| Dependency | None | |
| Phone app | No change | The app already forwards behind the server's switch |

Order on any environment: deploy dark; apply the two migrations; set `SMS_INBOX_ENABLED`; grant access; and, only after the wording is amended, turn the phone's switch on. To go back: turn the phone's switch off, then unset the variable. The tables stay and nothing reads them.

## 13. The screens and the mockup (R6)

**The menu (R7).** The HQ side menu has one item, "Inbox", with the unread count. The page title is "Inbox". "New SMS" is not a menu item: it is a button on the SMS list, as actions are on the dashboard. The HQ home page gains one link to the Inbox, shown only to a user with access; nothing else on the existing HQ pages changes.

**The platform menu (R7).** At the top of every Inbox page is a horizontal menu: SMS, Facebook Messenger, Instagram, TikTok, Email, Web chat. It is the dashboard's Settings menu (the row that holds "Business Settings", "Plans", "Billing" and the rest; `.set-tabs` in `dash.css`), copied rule for rule: text links 15 pixels and semi-bold, 12 by 16 pixels of padding, 4 pixels apart, one thin line under the row, the current item in the brighter text colour with a 2-pixel blue underline, 24 pixels of space below. On a phone it behaves as the dashboard's does: the row stays on one line and is swiped sideways; the page itself does not scroll sideways.

**The top-right buttons (R9).** Three 44-pixel buttons, 8 pixels apart, copied from the dashboard's top bar in its order: search, theme, bell. On a phone they sit in the top bar before the account circle; as on the dashboard, the wordmark narrows below 420 pixels and the theme button is dropped below 380 pixels. What each does in this build, kept to what the design already has:

| Button | On the dashboard | In this build |
|---|---|---|
| Search | Opens a search of quick links | A link to the SMS list's "Find a full number" field. It finds a conversation by a full mobile number, never a fragment (section 9). No new search is built |
| Theme | Switches dark and light | The same |
| Bell | Opens a panel of notifications | A link to the SMS list, showing a dot when any conversation is unread (read from `unread_count`). There is no panel, no stored notification and no alert when a text arrives; those stay out of scope (section 16) |

So both new buttons work, as links, without script and without any new record. The dashboard's own panels (the quick-links search and the notifications panel) are not copied, because each would be new scope.

**The pages, under option A.**

| Path | Page |
|---|---|
| `/inbox/` | Goes to `/inbox/sms/` |
| `/inbox/sms/` | The SMS list |
| `/inbox/sms/c/<id>/` | A conversation, with the reply form |
| `/inbox/sms/new/` | Compose |
| `/inbox/messenger/`, `/inbox/instagram/`, `/inbox/tiktok/`, `/inbox/email/`, `/inbox/webchat/` | One shared "not yet available" page (R8) |

**The "not yet available" state (R8).** One page for the five platforms: the platform's name, "is not yet available", the sentence "Only SMS works in the Inbox today. Nothing is received or sent on this platform.", and a button back to SMS. It shows no conversation, made-up or real, and promises no date. It reads and writes no table and is seen only by a staff user who can open the Inbox. The five names are fixed text in the template: there is no platform table, setting or switch for them.

They follow the Subscriber dashboard: the Manrope typeface, the blue `#173DB8` primary, the dark navy sidebar on desktop, the top bar and bottom tab bar on phones, the eyebrow and heading at the top of each view, cards with a 10-pixel radius, pill chips, 44-pixel buttons, and the dashboard's dark theme with its light theme when the device prefers light.

**Mockup:** `docs/superpowers/specs/mockups/2026-11-11-sms-inbox/`: `index.html` (the SMS list), `conversation.html`, `compose.html`, `platform.html` (the "not yet available" state; `?p=instagram` and the like pick the name shown), `bulk.html` (Bulk SMS, section 18: the list of bulk sends and the three steps of a batch, drawn on one page), `contacts.html`, `contact.html` (add or edit a contact), `groups.html` and `import.html` (upload contacts from a CSV file), all section 19, one shared `mockup.css`. Static files, made-up data only: no real number and no real text. To open it:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory docs/superpowers/specs/mockups/2026-11-11-sms-inbox
```

Then `http://127.0.0.1:8765/` (http, port 8765, local only; nothing is deployed). The mockup loads the Manrope typeface from Google Fonts; the built pages would serve the font from HQ itself (section 3, option A). The "Theme" button switches between the dark and light themes.

Decisions the mockup shows, for Coach Ed's eye:

- The side menu says "Inbox"; the platform menu sits at the top of the page, as the Settings menu does on the dashboard. There is no heading block under it (R9).
- The SMS list's "Bulk SMS" button is shown only to a staff user with the bulk permission (section 18.7).
- The list has no text preview (section 9).
- Numbers are masked everywhere except while typing one in compose.
- The compose page states the rules above the form: one SMS, no links, no marketing, one number at a time, and that the text is recorded.
- The reply box shows a character count out of 160.
- A conversation with a short code or sender name has no reply box, and says why.
- The pages work without script. Script only updates the character count.
- There is no automatic refresh. A "Refresh" link reloads the list.
- A status line shows whether the gateway phone is online and how many of the day's texts are used, read from what the gateway already knows.

## 14. NPC and privacy wording: points the amendment must cover

Input for Coach Ed's wording. This is not wording, and this design edits no filing or policy text. R1 requires the amendment **before any incoming text is kept in any environment**.

1. **Incoming texts are now received and stored.** The published privacy page says "We do not store the text of the message." twice (frontend `apps/site/public/privacy.html`), and the filing describes a phone that only sends. What is stored: the sender's number, the full text, the time it arrived and the SIM slot, in the PostgreSQL database on Railway.
2. **Texts not meant for us are stored too.** A SIM also receives the carrier's notices, wrong numbers, and one-time codes that other services send to that number. They are stored like any other text, because nothing filters them.
3. **Staff replies and composed texts are stored with their text.** "The text of a message is never stored" stays true for sign-in codes and status texts only.
4. **The kinds of text sent.** The filing wording says the phone "sends only sign-in codes and order-status texts". Staff replies and staff-written texts are a third kind. No marketing remains true.
5. **Texting a number whose owner has not written to us and has not verified it (R5).** The basis for doing so, what such a text may be about, how the person is told who is texting, and how they can ask us to stop.
6. **One period for SMS records: 365 days**, then deleted, for incoming texts, staff texts and send records alike. A disposed account's texts follow the same 365 days and are not removed earlier.
7. **Who can read them.** Named HQ staff with an explicit permission, through two-step sign-in. Every opened conversation, reply and composed text is logged with the staff user and a masked number, and never with the text; the masked number is blanked from the log after one year.
8. **Copies on the phone.** The phone's Messages app keeps every arriving and outgoing text until its auto-delete removes it. The gateway app can also hold an arriving text, sealed, for up to 7 days when it cannot reach the server.
9. **A person's rights.** How someone asks to see or delete the texts held under their number before 365 days. This build has no delete screen and does not include inbox texts in the account export.
10. **No new recipient.** Texts still travel only through the mobile carrier. No other outside party receives a number or a text.
11. **The launch announcement (R11).** One announcement by SMS to TWCinc subscribers is an exception to "no marketing". The wording must allow it before any such text is sent, and must keep every other marketing text forbidden.
12. **Where those numbers come from (R12).** A list of mobile numbers supplied by Coach Ed as a file, with his confirmation that those subscribers may be contacted. No system link to TWCinc. The wording names this source and the basis for texting those people.
13. **Opting out (R13).** Every bulk text says "Reply STOP to opt out". A number that replies STOP is kept on a do-not-text list and is skipped by later bulk texts. **That list is kept past 365 days**, until the person asks otherwise, because deleting it would start the texts again. This is a second retention period beside point 6.
14. **What a bulk send keeps.** The one text and its counts for 365 days; the list of numbers only until the batch ends; a send record per number for 365 days, as for every text.
15. **The documents the Parent names:** register row 6; filing-store-sms; filing-subscriber-accounts-billing; filing-subscriber-stores; PIA 5; privacy manual s8; the Terms and Privacy provider list. This session did not read them.
16. **Contacts (R16, R17).** For the first time a person's first and last name is stored together with their mobile number, with the groups staff put them in. Where the names and numbers come from (typed by staff, or a CSV file Coach Ed supplies; no link to TWCinc), what they are used for (choosing who gets a bulk text), and that **a contact is kept until HQ staff delete it**, with no automatic deletion. This is a third retention period beside points 6 and 13. No contact is stored in any environment before this wording is amended.
17. **A contact's rights.** How a person asks to see, correct or delete their contact record. Staff can edit and delete a contact on the screen; deleting it does not remove their number from the do-not-text list.

## 15. Tests the build would write

- **Storing.** With either switch off, `received` answers 204 and writes nothing, and reads no inbox table. With both on: a valid list is stored; a repeat of the same ids stores nothing new; a bad list stores nothing and answers 400; a sender that is not a Philippine mobile is kept as received and cannot be replied to; a text over 2,000 characters is cut and marked; the owner is `hq` whatever the body says; no number or text appears in a log line.
- **Sending.** A reply and a composed text each make one send record with no text, one `out` message with the text, and one audit entry. A link, a 161st character and a non-SMS character are refused before anything is queued. A refused send (limit, kill switch, phone offline, gateway off) stores no message and shows its reason. A staff text takes one slot of the platform count.
- **Order and interval.** With a code and a staff text waiting, the code is handed out first. A staff text carries a gap of 18 to 25 seconds; a code carries 0. Only one paced text is with the phone at a time.
- **The number.** Every form `normalise_ph_mobile` accepts is accepted; a landline, a foreign number and a short number are refused. Composing to a number with a conversation adds to it.
- **Platform.** Every stored conversation and message has `platform` `sms`, and a message's platform always equals its conversation's. `scope_for` answers none for any platform other than `sms`, with or without an `InboxAccess` row. Each of the five "not yet available" pages shows the state to a user with access, answers "Page not found" to anyone else, and reads and writes no inbox table. A platform name that is not one of the six answers "Page not found".
- **Access.** A verified session without an `InboxAccess` row, a superuser without a row, a session that passed only the first step, and no session at all each get the HQ "Page not found" on every Inbox page. A read-only user sees no form and a posted reply is refused. A conversation owned by a Site or an account (made in the test) cannot be listed, opened or replied to by HQ staff. Every page answers "Page not found" while `SMS_INBOX_ENABLED` is off.
- **Audit.** Opening, replying and composing each write the declared entry, which names the platform; no entry contains the text or a full number; an undeclared action is refused.
- **Masking.** The list page's HTML contains no message text and no full number. The conversation page contains no full number.
- **Retention.** A message older than 365 days is deleted; a younger one stays; a conversation left empty is deleted; the dry run changes nothing; the rule does nothing while the switch is off.
- **The command.** `grant`, `revoke` and `list`; `sms_device incoming on` refuses while `SMS_INBOX_ENABLED` is off.
- **Forms.** The CSRF check; a refresh after a sent text does not send it again (the answer to a sent form is a redirect).
- Checks: `manage.py makemigrations --check`, `manage.py check`, `manage.py check --deploy`, the full test run.

## 16. Out of scope

- **Facebook Messenger, Instagram, TikTok, Email and Web chat (R8).** No screen beyond the shared "not yet available" state, no record, no connection to any of those services, no credential, no setting, no NPC or privacy wording. Each is its own task with its own design, plan and approvals. This design only leaves room for them: the `platform` value on the records, the platform named in the access check and the audit entry, and the menu.
- Any add-on screen, billing, SIM custody, dedicated line, any broadcast or marketing text other than the launch announcement of section 18, and any Subscriber access. The `site` and `account` owners are fields and one branch point, nothing more.
- Mounting the JSON routes of section 10.
- Restyling the existing HQ pages (sign-in, code, verify, home), beyond the one link on the home page.
- Linking a sender's number to a Subscriber account, or showing account details beside a conversation.
- Showing a full number; deleting a conversation or a message by hand; exporting conversations.
- Fixed templates, saved replies, attachments, MMS, texts longer than one SMS, sending to more than one number outside Bulk SMS (section 18).
- Delivery receipts; notifications to staff when a text arrives; automatic refresh.
- For contacts (section 19): any import from or sync with TWCinc, custom fields on a contact, putting a contact's name into a text (name merge), merging two contacts, exporting contacts, and showing a contact's name in the inbox beside a conversation.
- A second phone, SIM or line, for bulk texts included (R14); adding or removing a do-not-text number by hand; a link of any kind to TWCinc; order-status texts.
- The privacy page and the NPC documents themselves.
- Anything on staging or production.

## 17. What was verified, and what was not

Verified by reading, on 2026-11-11: the Task and Parent with every comment; the code named in section 1 at `1ba9cc6`; the dashboard's styles at frontend `36d2ec4`, and its Settings menu and top bar (search, theme and bell buttons) at frontend `origin/main` `5802934`.

Verified by opening the mockup's five pages (list, conversation, compose, "not yet available", Bulk SMS) in headless Chrome over `http://127.0.0.1:8765/`, with the DevTools protocol's device emulation (`Emulation.setDeviceMetricsOverride`; `mobile` true at 390 pixels), at 1280 and at 390 pixels wide, in the dark theme and in the light theme: on every page the document's `scrollWidth` equals its `clientWidth` (1280 and 390), so no page scrolls sideways. Every dark-theme screenshot was read at both widths and nothing overlaps. At 390 pixels the platform menu is 619 pixels of content in a 358-pixel row: it is swiped sideways inside its own row, as the dashboard's Settings menu is. The first attempt at the phone top bar with three buttons was 18 pixels too wide; it was fixed the way the dashboard does it (a narrower wordmark below 420 pixels) and measured again.

Not verified:

- The light-theme screenshots were measured for overflow but not read.
- Any browser other than Chrome, and a real phone. The swipe of the platform menu was measured, not performed by hand. The theme button being dropped below 380 pixels was written, not measured.
- The platform menu and the top-right buttons against the running dashboard side by side. Their rules were copied from the dashboard's stylesheet and the mockup was read; the two were not opened next to each other.
- Section 18's numbers (time to send, the size of a batch) are arithmetic on the existing cap and gap. The carrier's behaviour toward a SIM that sends one text to many numbers is not known; that risk is accepted (R14).
- Whether the phone's poll can feed a batch to the queue without a new worker (section 18.3) was not checked against the code.
- The CSV uploads (Bulk SMS and contacts) are drawn as an upload area around the browser's own file button, which shows no chosen file in the mockup; reading a real file was not tried.
- The "Send to" choice is drawn with all three options open at once so a screenshot shows each; switching between them was not built.

- Nothing was run against the backend. This stage wrote documents and a static mockup only.
- The phone app's behaviour with the switch on, on a real phone with a real SIM: the permission prompt, texts in several parts, two SIMs.
- That 18 to 25 seconds between staff texts is what Coach Ed means by "the sending interval" for replies (section 6.3).
- The NPC documents and the privacy page were not read by this session.
- Whether the HQ host sends a Content-Security-Policy header; none was found in `config/settings.py`. The built pages would carry their styles and the font from HQ itself in either case.

## 18. Bulk SMS (R10 to R15)

Part of the design since rulings R10 to R14 (section 2), with the CSV upload and the list of bulk sends from R15. Like the rest of this document it is for Coach Ed's approval and nothing is built. The mockup page is `bulk.html`. Where this section and an earlier one disagree about bulk sending, this section is the design; the table in 18.9 lists every such place.

### 18.1 What it is for

One purpose: the launch announcement to TWCinc subscribers (R11). It is a stated exception to the no-marketing rule. Every other marketing text stays forbidden, in Bulk SMS as in a reply or a composed text. Code cannot tell an announcement from other marketing, so the controls are the ones in 18.7: a separate permission, a switch, the words on the screen, and the audit record.

### 18.2 The steps on the screen

A "Bulk SMS" button on the SMS list, beside "New SMS", shown only to a staff user with the bulk permission. The page opens on the list of bulk sends (18.2.1); the steps of a new batch are below it.

1. **One text.** The same text for every number, under the SMS rules: one SMS of at most 160 plain characters, no web link.
2. **The opt-out line (R13).** Every bulk text ends with "Reply STOP to opt out". The server adds it; the staff user cannot remove or change it. With the space before it, it takes 22 of the 160 characters, so 138 are left for the announcement, and the screen's counter counts it.
3. **Send to (R12, R15, R16).** One choice of three for a batch: groups from Contacts (one or more, section 19.5), a CSV file of mobile numbers, or pasted numbers one per line. The file's shape is in 18.2.2. Each line is checked with `normalise_ph_mobile`; duplicates are removed; lines that are not a Philippine mobile are set aside and can be downloaded; numbers on the do-not-text list are skipped and counted. Nothing is sent at this step.
4. **The check.** What was read (the contacts in the chosen groups, or the lines of the file or pasted list), the count after duplicates are removed, the count after opted-out numbers are removed, lines refused (a file or pasted list only), texts left today, the size of this batch, what is left for a later batch, and how long it will take at one text every 18 to 25 seconds.
5. **Confirm.** The staff user types the batch's count.
6. **Progress and results.** Sent, waiting and not sent, with a result per number, masked. No full number is shown after the check.
7. **Stop.** Stopping keeps what was sent and sends nothing more. The button is on the batch's row in the list and on its progress.

#### 18.2.1 The list of bulk sends (R15)

At the top of the Bulk SMS page, newest first. One row per batch: when it was confirmed, who started it, its state, and its counts. No number and no text is shown in the list.

| State | Means | The row shows |
|---|---|---|
| Waiting | Confirmed; no text has been handed to the phone yet (codes are ahead of it, or the phone is offline) | "Waiting", the batch's size, a Stop button |
| Still sending | Texts are going out | "Still sending", sent of total, not sent, waiting, the time left, a progress bar, a Stop button |
| Stopped | A staff user stopped it | "Stopped", at how many of the total, by whom, sent, not sent, never sent |
| Finished | Every text has a final result, or the batch's day ended (18.3) | "Finished", sent and not sent |

- Stop is offered only on a waiting or sending row, to a staff user with the bulk permission, and asks once before it stops. It is audited (`sms_inbox_bulk_stop`, 18.7).
- While a batch is waiting or sending, the "Bulk SMS" button on the SMS list carries a small chip, "Sending 396 of 1,786", so staff with the bulk permission see it without opening the page. It sits on the button so the status chips and the buttons stay on one line (R9).
- The counts are read when the page is loaded; the page does not need script to show them. Whether it refreshes itself is for the plan.
- A row stays as long as its batch record does: 365 days (18.4).

#### 18.2.2 The CSV file (R15)

- One mobile number per row, in the first column. A first row that is not a number is taken as a header and skipped.
- Other columns are ignored: they are not read into any record, not shown and not kept. So a file exported with names beside the numbers can be used as it is, and the names go nowhere.
- At most 1 MB. A larger file, or one that is not text, is refused with a plain message and nothing is read.
- The file is read once, in memory, and is never stored as a file (18.4). Only the numbers that pass the check become the batch's list.
- A batch has one source: groups, a file, or a pasted list (18.2 step 3). They are not combined.

### 18.3 Batches (R10)

- One bulk send is one batch. A batch never goes over the texts left today under the 2,000-a-day cap. The cap and the 18 to 25 second gap are the existing ones; nothing raises them.
- When the list is larger than what is left, the batch is **cut to fit**: it takes the first numbers in the list up to what is left, says so with both counts, and offers the numbers it left out as a download for a later batch. When no texts are left today, the batch is refused.
- One batch at a time. A second cannot start while one is sending.
- A batch belongs to the day it was confirmed (Manila time). Whatever has not been sent when the day ends is marked "Not sent" and can be downloaded for a later batch.
- Codes are always first (section 6.3): bulk texts use the paced class behind every waiting code, one at a time. A code asked for while a batch runs takes a slot of the same 2,000, so the last texts of a batch that was cut to the very limit can be refused by the cap; they show "Not sent: today's limit reached".
- A staff text waits for the phone at most 1 hour (section 6.1), and at this gap the phone sends about 145 to 200 texts an hour. So a batch is not queued all at once: its texts are handed to the queue a few at a time. How that is driven without a new worker (the phone's own poll is the proposal) is for the plan to confirm.
- The 60-an-hour limit per staff user (section 6.3) applies to replies and composed texts only. A batch is limited by the cap, the gap and one batch at a time.

In numbers: a full day's 2,000 texts take about 10 to 14 hours of sending. The mockup's example is a list of 2,293 numbers on a day when 214 texts are used: the batch is cut to 1,786, takes about 10 to 11 hours, and leaves 507 for a later batch. A list of more than 2,000 numbers takes at least two days.

### 18.4 The numbers and where they come from (R12)

- Coach Ed supplies the numbers, as a file or a pasted list, for each batch, and confirms that those subscribers may be contacted. That confirmation is his and is not something the tool checks.
- There is no link to TWCinc: no integration, no access to a TWCinc database, no TWCinc code and no TWCinc secret. The tool sees only what a staff user pastes or uploads.
- An uploaded file is read and never stored as a file. Columns other than the first are dropped as it is read (18.2.2).
- The batch keeps its list of numbers only while it needs them: the rows are deleted when the batch has finished or was stopped and its last text has a final result. After that the screen shows the batch's counts, not its numbers.
- What stays, as for every text the gateway sends: one send record per text (the number, the purpose, the result, no text), deleted at 365 days by the existing rule and shown masked in the admin's SMS list.
- The batch keeps its one text and its counts for 365 days, as staff texts are kept (R2).
- A bulk text does not open a conversation in the inbox, so 1,786 announcements do not bury the list. If a recipient writes back, their text arrives as an ordinary conversation.

### 18.5 STOP and the do-not-text list (R13)

- An incoming text from a Philippine mobile whose whole text is STOP, in any letter case and with any spaces around it, puts that number on the do-not-text list. The text is stored and shown like any other.
- The list blocks bulk only. A later batch skips a listed number and the check says how many were skipped. A reply or a composed text to that person is still allowed, and the conversation shows "Opted out of bulk SMS".
- A listed number is **not** removed by the 365-day rule: removing it would start texting the person again. It stays until the person asks otherwise. That is a new retention period and an NPC wording point (section 14).
- There is no screen to add or remove a listed number by hand in this build.
- **Opting out works only while incoming texts are stored.** With `SMS_INBOX_ENABLED` off or the phone's incoming switch off, a STOP reply is discarded. So a batch is refused unless both are on; otherwise the opt-out line would be a promise the system cannot keep. Turning the incoming switch on stays Coach Ed's approval, after the wording is amended (R1).

### 18.6 Same phone, same SIM (R14)

Bulk texts go through the one gateway phone and its SIM. A batch has no choice of phone. **The accepted risk, stated plainly:** a carrier may block a consumer SIM that sends the same text to many numbers, and if it does, sign-in codes stop for every Subscriber until the SIM is unblocked or replaced. Coach Ed was told this and a second SIM was recommended; his ruling is "Same SIM, accept risk".

What limits the risk without new scope: codes first, the cap, the gap, one batch at a time, and the stop button. The gateway runbook gains a note for a blocked SIM: how it shows (texts turn "Not sent" and the phone reports failures), stopping the batch, and what to do about codes meanwhile.

### 18.7 Who may send a batch, and the audit record

- **A separate permission**, above send: `can_bulk` on the staff user's `InboxAccess` row for `sms`, which requires `can_send`. Granted by `sms_inbox_access grant --bulk`, audited, and Coach Ed names who holds it.
- **A switch**, `SMS_BULK_ENABLED`, a new Railway variable, off. While off, the button and the page do not exist. It is turned on per environment with Coach Ed's approval, and only after the NPC and privacy wording covers the announcement (R11).
- **Audit**, through the operator audit record, never with the text and never with the list or any number:

| Action | When | `detail` (kept) |
|---|---|---|
| `sms_inbox_bulk` | A batch is confirmed | The platform, the batch's id, the source (groups, file or pasted) and for groups their ids and how many, never their names, lines read, duplicates, lines refused, numbers skipped as opted out, the batch's size, numbers left for a later batch, the text's length |
| `sms_inbox_bulk_stop` | A batch is stopped | The batch's id, how many were sent, waiting and not sent |

Both are declared `KEPT`: they hold no personal value.

### 18.8 Records and what the build would need

To the list in section 12, all in the new app and each needing the same approvals:

| Item | Notes |
|---|---|
| `BulkBatch` | Platform `sms`, owner `hq`, the staff user, the one text, the source and any chosen groups, the counts, the times, the state |
| `BulkRecipient` | The batch, the number, the send record's id. Deleted when the batch ends (18.4) |
| `DoNotText` | Platform `sms`, the number, when, and the incoming message that caused it. Not covered by the 365-day rule |
| `InboxAccess.can_bulk` | 18.7 |
| A purpose `inbox_bulk` in `apps/notifications` | Paced, in the `inbox` category, beside `inbox_reply` and `inbox_compose` |
| `SMS_BULK_ENABLED` | Read in `config/settings.py`; a Railway variable per environment |
| Runbook | The blocked-SIM note (18.6) and the bulk steps |

Whether these tables are in `inbox.0001_initial` or a second migration is for the plan.

Tests the build would add to section 15: the opt-out line is always present and counted; a text over 138 characters is refused; the list is checked, de-duplicated and stripped of opted-out numbers; a batch larger than the texts left is cut and says so; a batch with nothing left is refused; a second batch is refused while one runs; a code is handed out before a waiting bulk text; stop sends nothing more, from the list and from the progress; a CSV with a header row and extra columns yields only its first column's numbers; a file over 1 MB is refused; the list of bulk sends shows each state and holds no number and no text; STOP in any case with spaces lists the number, and "STOP please" does not; a listed number can still be replied to; a batch is refused while incoming texts are not stored, while `SMS_BULK_ENABLED` is off, and for a user without `can_bulk`; the recipient rows are gone after the batch ends; no audit entry, log line or page after the check holds a full number, and none holds the text.

### 18.9 What this changes in the earlier sections

| Earlier | Now |
|---|---|
| R4 and section 14 point 4: no marketing | One stated exception, the launch announcement, after the wording is amended (R11) |
| Section 6.3: "There is no list, no paste of many numbers, no upload and no 'send to all'" | True of reply and compose. Bulk SMS is the one path with a list |
| Section 6.3: the one-number form, the audit record and the hourly limit stand between "any Philippine mobile" and bulk sending | For bulk, the controls are the bulk permission, the switch, the confirm step, the cap and the audit record |
| Section 7.1: two levels, read and send | A third, bulk |
| Section 11: everything goes at 365 days | The do-not-text list does not |
| Section 16: no broadcast or marketing text; no sending to more than one number | Bulk SMS for the launch announcement is in scope. Everything else on those lines stays out |

### 18.10 Still open

One point, raised by this design and not ruled on: **how many texts a day are kept back for sign-in codes.** As drawn, a batch may use every text left that day, and a code asked for afterwards is refused until the next day. Should a batch stop short of the cap, and by how many?

## 19. Contacts and groups (R16 to R19)

Added by R16, with R17 (kept until staff delete), R18 (the same inbox staff) and R19 (CSV upload into a group). Like the rest of this document it is for Coach Ed's approval and nothing is built. The mockup pages are `contacts.html`, `contact.html`, `groups.html` and `import.html`. With this the Task has three parts: the inbox, Bulk SMS and contacts.

### 19.1 What it is for

A list of people HQ staff may send a bulk text to, sorted into groups, so a batch is sent to a group instead of a file each time. **Contacts are the first place in this tool, and in the SMS records, where a name is stored together with a mobile number.** The NPC and privacy wording must cover that before any contact is stored in any environment (section 14 points 16 and 17), the same "amend first" order as R1 and R11.

### 19.2 The screens

A "Contacts" button on the SMS list, beside "Bulk SMS" and "New SMS". The Contacts page has two views, Contacts and Groups.

- **Contacts.** One row per contact: first and last name, the mobile number masked as everywhere else (section 9), the groups, and "Opted out of bulk SMS" when the number is on the do-not-text list. Find by name or by a full number; filter by group. Buttons: "Add contact" and "Upload a CSV file".
- **Add or edit a contact.** First name, last name, mobile number, groups. The number is checked with `normalise_ph_mobile`; one contact per number, so a number already in contacts is refused with a link to that contact. The edit form shows the full number, because staff cannot correct what they cannot see; opening it is audited. "Delete contact" asks once.
- **Groups.** One row per group: its name and how many contacts it holds. Add a group, rename it, remove it. Removing a group keeps its contacts; a contact can be in several groups or none.
- **Upload contacts (R19).** Step 1: a CSV file and the group to add them to. Step 2, the check, before anything is saved: lines read, new contacts, numbers already in contacts, refused lines with their line numbers and the reason, and how many of the new numbers are on the do-not-text list. Then "Add 748 contacts".

### 19.3 The contacts CSV file (R19)

- Three columns in this order: first name, last name, mobile number. A first row whose third column is not a number is taken as a header and skipped. Other columns are ignored: not read into any record, not shown, not kept.
- At most 1 MB, as for the Bulk SMS file (18.2.2). The file is read once, in memory, and never stored as a file.
- A number already in contacts is not added again. That contact is put in the chosen group and keeps the name it has; the file's name for it is dropped.
- A line whose number is not a Philippine mobile, or that has no number, is refused and listed, and can be downloaded.
- A number on the do-not-text list is added and stays marked opted out.
- The file is Coach Ed's own. There is no link to TWCinc and no sync (R12 stands).

### 19.4 The do-not-text list applies to contacts

- The list is kept by number (18.5), so it covers a contact whether the STOP came before or after the contact was added.
- An opted-out contact is marked in the list and on its form, stays in its groups, and is skipped by every bulk batch.
- Deleting a contact does not remove its number from the do-not-text list. Adding the number again later brings back the mark.
- Staff cannot clear the mark (18.5: no screen to add or remove a listed number by hand).

### 19.5 Sending to groups

- On the Bulk SMS form, "Send to" is one of: groups from Contacts, a CSV file of mobile numbers, or pasted numbers (18.2 step 3).
- With groups, staff tick one or more. The check shows the contacts in the chosen groups, the count after a contact in two groups is counted once, and the count after opted-out contacts are removed. That last count is the list; the cut to today's texts (18.3) applies to it.
- The batch takes a copy of the numbers at the moment it is confirmed. A contact added, changed or deleted afterwards does not change a batch that is waiting or sending. The copy is the batch's recipient rows, deleted when the batch ends (18.4).
- The text is the same for everyone. A contact's name is never put into the text and never leaves the server.
- When the groups do not fit in today's texts, the numbers left out are offered as a CSV download for a later batch, as for a file (18.3). A way to continue the same groups the next day without the download is not designed here.

### 19.6 Who may use it (R18)

The same HQ staff who hold the SMS inbox permission, behind the two-step HQ sign-in, through the one access check of section 7.2. There is no separate contacts permission. This design reads R18 with the two existing levels: a staff user with read access sees contacts and groups; adding, editing, deleting and uploading need `can_send`, as every other change does. Sending to a group still needs `can_bulk` (18.7). Whether read-only staff should also edit is the coordinator's to confirm.

### 19.7 Retention (R17)

- A contact is kept until HQ staff delete it. No job deletes contacts, and the 365-day rule does not apply to them.
- Texts, send records and batches stay on the 365-day rule (R2). The do-not-text list keeps its own rule (18.5).
- Deleting a contact removes its name, number and group memberships at once. It does not remove send records or a conversation with that number, which go at 365 days.

### 19.8 The audit record

Through the operator audit record. An entry holds who, when, ids and counts. It never holds a first or last name, a group's name, or a full number.

| Action | When | `detail` (kept) |
|---|---|---|
| `sms_contact_open` | A contact's form is opened (the full number is shown) | The contact's id |
| `sms_contact_add`, `sms_contact_edit`, `sms_contact_delete` | A contact is added, changed or deleted | The contact's id, which fields changed (their names, not their values), how many groups it is in |
| `sms_contact_upload` | An upload is saved | The group's id, lines read, contacts added, already in contacts, lines refused, opted out |
| `sms_group_add`, `sms_group_rename`, `sms_group_remove` | A group is added, renamed or removed | The group's id, how many contacts it held |

All are declared `KEPT`: they hold no personal value.

### 19.9 Records and what the build would need

To the lists in sections 12 and 18.8, in the same new app, each needing the same approvals:

| Item | Notes |
|---|---|
| `Contact` | Platform `sms`, owner `hq` (R0: a Site or Subscriber later), first name, last name, the normalised number (one per owner), who added it, the times |
| `ContactGroup` | Platform `sms`, owner `hq`, the name (one per owner), the times. A contact belongs to any number of groups |
| `BulkBatch` | Gains its source and the chosen groups (18.8) |
| Masking | A contact's number is masked in every list; the full number appears only on the contact's own form |
| Export | Contacts are not in the account export in this build |

Tests the build would add: a contact needs a Philippine mobile and is unique by number; the list shows masked numbers only; an opted-out contact is marked and skipped by a batch to its group; a contact in two chosen groups gets one text; a batch keeps its numbers when a contact is deleted while it sends; the upload adds nothing before the check is confirmed, does not duplicate a number, drops other columns, and lists refused lines; removing a group keeps its contacts; deleting a contact keeps the do-not-text mark; a user without the inbox permission reaches none of it, and one with read only cannot change anything; no audit entry or log line holds a name or a full number.

### 19.10 What this changes in the earlier sections

| Earlier | Now |
|---|---|
| The whole design: no name is stored | A contact holds a first and last name with a number |
| Section 11 and 18.5: two retention periods (365 days, and the do-not-text list) | A third: a contact is kept until staff delete it (R17) |
| Section 14: points 1 to 15 | Points 16 and 17 |
| 18.2: the numbers come from a file or a pasted list | Or from groups of contacts; one source per batch |
| Section 16 | Lists what contacts leave out |

### 19.11 Left out

No import from or sync with TWCinc. No custom fields: a contact is a first name, a last name, a number and groups. No name merge: a text never carries a contact's name. Also not here: merging two contacts, exporting contacts, and showing a contact's name beside a conversation in the inbox.

### 19.12 Still open

Nothing in this section is open for Coach Ed: R17, R18 and R19 answered the three questions it raised. The one open point of the design is still 18.10, how many texts a day are kept back for sign-in codes.
