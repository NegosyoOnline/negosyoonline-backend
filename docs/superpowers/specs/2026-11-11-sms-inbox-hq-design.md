# SMS inbox, reply and compose for HQ staff: design (2026-11-11)

ClickUp task: z8uq9me128 "SMS inbox, reply and compose (HQ)" (https://app.clickup.com/t/z8uq9me128), a subtask of z8uq9md33v "NPC Build 1 — SMS Carrier & Data-Flow Controls".

Status: **the look and the design are approved by Coach Ed, both on 2026-11-11 ("approved the look", "approved the design"), on PR 75 at head `2d021f3`. Nothing is built.** The approval allows build plans to be written, each returning to him for approval before any code. It approves no build, no migration, no Railway or Vercel change, and no storing of incoming texts, which stays gated on the NPC and privacy wording (R1). The look approval does not cover the light theme, the recovery-code form, the "Sign-in paused" notice or the error page, which he was not shown (section 13). This document creates no code, migration, setting, variable or switch. Where the screens are served is settled (R23, section 3): the frontend on Vercel, with Django on Railway serving data and rules only. He then ruled that the existing HQ staff pages move to Vercel as part of this task (R24, "Yes, move them now") and, first, that the Django Admin stays on Django (R25, "Yes, Admin stays"). He then dropped the Django Admin from the staff area (R27, "just drop this django admin... dont show this"): it is not shown, linked or drawn anywhere in this design. Section 22 lays out the whole staff area on Vercel and the order in which the Django pages would be retired. How HQ staff sign in on Vercel is settled (R26, section 20) and kept for the staff area: the sign-in forms are Vercel pages, the backend checks the codes as today and sets today's cookie, with the cost of section 20.6 accepted. He then ruled that the Django Admin is switched off completely, not only hidden (R28, "Switch it off"): once the HQ move is complete the backend serves no Django Admin, and R26's sign-in opens the staff area only (sections 22.4 and 22.6). Still open and his (section 22.7): whether and when Vercel pages replace the Admin's lists, for which nothing is designed; and the host names, where the coordinator's reading is that `hq` is the staff app on Vercel and the backend address it calls gets a new name (a proposal, 22.7), with the exact names settled at build time. The reserve for sign-in codes is settled (R20, section 18.10). The mockup of the screens is in `docs/superpowers/specs/mockups/2026-11-11-sms-inbox/` (section 13); visual acceptance is his. Revised on 2026-11-11 after his feedback on the first mockup (R7, R8 and section 2.1): the menu item is "Inbox", with a horizontal menu of platforms inside it, and only SMS is designed and built. Revised again the same day after his feedback on that mockup (R9 and section 2.2): the heading block is removed, the status chips and "New SMS" share one line, and search and notification buttons are added at the top right. Bulk SMS (section 18) was then added to the design by five rulings, R10 to R14, for the launch announcement only. His feedback on the Bulk SMS page (R15) then added a CSV upload and a list of bulk sends with a stop button. Contacts and groups (section 19) were added by R16 to R19, so the scope is now three parts: the inbox, Bulk SMS and contacts. His feedback on that mockup (R21) then moved Contacts out of SMS: it is its own item on the HQ main menu, a kept list of the people NegosyoOnline deals with, and not only the recipients of a bulk text. He then ruled (R22) that Contacts holds only what HQ staff type in or upload, with no link to Subscriber accounts. Next come the plans, each for his approval, before any code.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9me128: description and its comments, read 2026-11-11; the feedback behind R9 and section 18 was relayed by the coordinator the same day |
| Parent | ClickUp z8uq9md33v: description and its ten comments, read 2026-11-11 |
| Gateway design | `docs/superpowers/specs/2026-11-10-sms-gateway-mobile-verification-design.md`, sections 5.6, 6.2, 6.3, 6.7 and 14 |
| Runbook | `docs/runbooks/sms-gateway.md`, `docs/runbooks/retention.md` |
| Code | `apps/notifications`, `apps/operators`, `apps/common`, `apps/accounts/retention.py`, `config/settings.py` at `1ba9cc6` |
| Dashboard look | Frontend repository at `36d2ec4`, read only: `apps/dash/public/index.html`, `apps/dash/public/assets/css/dash.css`, `apps/dash/next.config.ts`. For the platform menu: the Settings menu (`.set-tabs`) in the same two files at `origin/main` `5802934` |
| Frontend layout, for R23 | Frontend repository at `origin/main` `8e92124`, read only and not edited: `README.md`, `apps/dash/README.md`, `apps/dash/next.config.ts`. Backend at this branch: `config/settings.py`, `apps/common/middleware.py`, `apps/accounts/sessions.py`, `apps/operators/hq_views.py`, `hq_auth.py`, `hq_urls.py`, `middleware.py`, `checks.py` |
| HQ pages, for R24 and R25 | Backend at this branch (`b50e9a8`): `apps/operators/hq_urls.py`, `hq_views.py`, `hq_auth.py`, `two_step.py`, `middleware.py`, `admin_site.py`, `audit.py`, `checks.py`, `pages.py`, `templates/hq/`, `templates/admin/base_site.html`. The file lists of PRs 40, 41 and 78. `docs/superpowers/plans/2026-11-11-backend-npc-build9-hq-only-admin-door.md` (PR 45), sections 1 to 3.3. The Build 6 constraints and staging facts as relayed by the coordinator from the NPC coordinator. Frontend `origin/main` `1166182`, read only: `apps/dash/public/verify-email.html`, for the card a sign-in page uses |
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
| R20 | Of the 2,000 texts a day, 300 are kept for sign-in codes | "Hold back 300" |
| R21 | Contacts is its own item on the HQ main menu, not a button or page inside SMS. Staff open it directly to add and look up contacts. It is not only a recipient list for Bulk SMS | "As said, the contacts will be on the main menu, not inside the SMS. Okay, it must be on the main menu so that we will be able to add. We can check. It's not just for bulk sending. It is a kind of like a database of users of Negosyo Online." |
| R22 | Contacts holds only what HQ staff type in or upload from a CSV file. It does not show registered Subscriber accounts and has no link to account records | "No, hand-entered only" |
| R23 | The inbox, Bulk SMS and Contacts screens are served from the frontend on Vercel, not as Django pages on the HQ host. Django on Railway serves the data and rules only. This is option B of section 3, chosen against this design's recommendation after the consequences were put to him | "all the displays will be coming from Vercel, and all the backend codes will be from Railway using Django." Then: "No, you need to move it to Versal. We need to be um, uniform with our codes. So again, move all our front end to Versal. The railway or Django will be serving only the" (the message ended there) |
| R24 | The existing HQ staff pages (staff sign-in and its second step, the console, the lists), Django pages on the HQ host today, move to Vercel now, as part of this task, not later as a separate one. The sign-in question becomes how HQ staff sign in on Vercel with the two-step check kept | "Yes, move them now" |
| R25 | The Django Admin stays on Django on Railway, behind the two-step sign-in. Django generates it and it is not served from Vercel. Only the HQ staff pages we wrote move. **Replaced for the staff area by R27** | "Yes, Admin stays" |
| R26 | How HQ staff sign in on Vercel: the recommended column of section 20.4. The sign-in forms are Vercel pages; the backend checks the codes as today and sets today's HQ cookie; that one sign-in opens the staff area and the Django Admin; no token held by the page; no separate Admin sign-in; no migration. The cost of section 20.6 is accepted with it. He was shown the three columns and that cost first. **Kept for the staff area; the part about the Django Admin is overtaken by R27** | "Okay, just do all the recommended for the these 4 rows, how it works, Django admin, the security, and the build." |
| R27 | The Django Admin is dropped from the HQ staff area and not shown. The "Admin lists" card on the console and the "Admin lists" and "Lists" menu items come out of the design and the mockup. Said after: "all of our domain will be on Vercel. The only domain in railway is the API". Left open then, and settled by R28: whether the Django Admin is switched off on the backend altogether or only hidden | "just drop this django admin... dont show this.. you are confused because of this" |
| R28 | The Django Admin is switched off completely, not only hidden. Once the HQ move is complete the backend serves no Django Admin; R26's sign-in opens the staff area only; the backend serves no pages of ours to staff. He was told first that the Admin is today the only screen for the Subscriber, site and sent-email lists and for managing staff users, and that with it off there are only terminal commands until list pages are designed and built on Vercel. Switching it off on any environment is a build step with its own approved plan, reviewed by the NPC coordinator before merge. Not ruled: whether and when Vercel pages replace the lists | "Switch it off" |

R9 to R28 are each a comment on the Task, re-read there on 2026-11-11. Section 18 applies R10 to R15 and R20; section 19 applies R16 to R19, R21 and R22; sections 3, 10, 20 and 21 apply R23; sections 20 and 22 apply R24, R26, R27 and R28. Where the words recorded with R24 and R25 do not match the code (the "lists" are the Django Admin; the Admin has no sign-in pages of its own), section 22.2 reports both sides.

**A word used throughout.** "The HQ host" in this document means the backend address on Railway that `HQ_HOST` names today, where the staff routes and the staff session live. Under the coordinator's reading of R27, the name `hq` goes to the staff app on Vercel and that backend address gets a new name (a proposal, 22.7). Until he rules on the names, the sentences keep "the HQ host" for the backend address and "the staff host" for the app on Vercel.

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

## 3. Where the screens are served: settled, the frontend on Vercel (R23)

**Settled by Coach Ed: option B.** The inbox, Bulk SMS and Contacts screens are pages in the frontend repository, served by Vercel. Django on Railway serves the data and the rules through an API and renders none of these screens. His reason, in his words: "We need to be um, uniform with our codes."

R3 is unchanged in what it protects: HQ staff only, an explicit inbox permission, the two-step sign-in, every read and send audited. What R23 changes is the host that serves the screens. R3's "Host: HQ only" now means the data and the rules: every number, name and text is still read and written only by the backend, only for a session that passed both steps.

The table is kept as the record of what was weighed. This design recommended A; he ruled B after hearing the consequences in its row.

| Option | What it is | Security consequence | Cost |
|---|---|---|---|
| A. Django pages on the HQ host, styled to match the dashboard (recommended by this design, not chosen) | Server-rendered pages under `/inbox/` in the HQ URL set (`apps/operators/hq_urls.py`), with their own base template that carries the dashboard's look | Nothing new to trust. The pages, the numbers and the message text are served only by the HQ host, only to a session that passed both steps. The session cookie stays host-only, `HttpOnly`, `SameSite=Strict`. No other origin is allowed to call HQ | The look is a second copy of the dashboard's styles, kept in the backend and updated by hand |
| **B. A surface in the frontend repository (chosen, R23)** | A new app in the frontend repository, hosted on Vercel like the dashboard, that calls the backend for inbox data | The HQ session cookie cannot be read or sent by another origin, so one of three things must change: the HQ host must allow a second origin to make signed-in calls, or HQ must issue a token that the browser page holds, or the two-step sign-in must be built again in the frontend (section 20). In every case a second host and its deploy pipeline join the trust boundary of the staff tool: whoever can change that deploy can read every conversation (section 21). Message text and numbers are handled by script on a host that is not HQ | Shares the real dashboard styles. Needs new sign-in plumbing, a frontend task and repository, and new approvals |
| C. HQ pages that load the dashboard's stylesheet from the dashboard host (not chosen) | Option A's pages, but with a `<link>` to the dashboard's own CSS file | A stylesheet from another host can change what HQ staff see and can read parts of the page. HQ would depend on another host being up | Always matches the dashboard with no copy to maintain |

### 3.1 Where the screens live in the frontend repository

The frontend repository is a workspace of three apps, each its own Vercel project: `apps/site` (the public site), `apps/dash` (the Subscriber dashboard) and `apps/storefronts`. This design proposes a fourth, **`apps/hq`**, its own Vercel project on its own host, for the staff screens. It is a proposal from reading that repository, not from building in it; the frontend task's plan confirms it.

- **Why not inside `apps/dash`.** The dashboard's origin is where Subscribers are signed in. Staff screens on the same origin would share every script and every weakness of the Subscriber pages, and the backend could not tell a call made by a staff screen from one made by a dashboard page. A separate origin lets the backend allow exactly one origin for staff calls.
- **Built the way the dashboard is.** Static HTML, CSS and script in `public/`, served by Next.js, with `next.config.ts` giving the routing and the security headers. No server code on Vercel reads or writes inbox data: the browser calls the backend directly (section 21).
- **The look.** The pages use the dashboard's own stylesheet rules. How `apps/hq` gets them (a shared file in the workspace, or a copy) is for the frontend plan. The mockup's look stands.
- **The paths**, routed in the browser as the dashboard's are:

| Path on the staff host | Page |
|---|---|
| `/`, `/sign-in/`, `/code/`, `/verify/` | The console and the three sign-in pages (R24, section 22.3) |
| `/inbox/` | Goes to `/inbox/sms/` |
| `/inbox/sms/` | The SMS list |
| `/inbox/sms/c/<id>/` | A conversation, with the reply form. The id is the conversation's opaque `public_id` |
| `/inbox/sms/new/` | Compose |
| `/inbox/sms/bulk/` | Bulk SMS (section 18) |
| `/inbox/messenger/`, `/inbox/instagram/`, `/inbox/tiktok/`, `/inbox/email/`, `/inbox/webchat/` | The shared "not yet available" page (R8) |
| `/contacts/`, `/contacts/groups/`, `/contacts/new/`, `/contacts/<id>/`, `/contacts/upload/` | Contacts (section 19) |

**No number, name or text is ever part of a page address or a query string.** A page address is recorded by Vercel and kept in the browser's history. Finding a conversation by a full number sends the number in the body of a call to the backend (section 10), never in an address.

The records, the access check, the service functions, the audit record, masking, retention and the SMS rules (sections 4 to 9, 11, 18, 19) do not change with R23. What changes is in sections 7.1, 10, 12, 13, 15 and 16, and the new sections 20 and 21.

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
| `SMS_INBOX_ENABLED` | Railway variable, **new** | Coach Ed's approval, per environment. While off: the inbox tables are never read or written, every staff API route answers `not_found`, `received` discards as it does today, and the retention rule does nothing. The code can be merged and deployed dark, the way the gateway was |
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
5. If it answers `refused`: nothing is stored in the inbox. The API answers with the reason, and the page shows it and keeps what the staff member typed. The attempt is still audited (section 8).

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
- **The cap.** Each staff text takes one slot of the platform's 2,000 a day (Manila time), the same count codes use. Consequence, stated plainly: staff texts and codes share one allowance, so a day of heavy staff texting leaves fewer slots for codes. **300 of the 2,000 are kept for sign-in codes (R20):** a reply, a composed text and a bulk text are refused once 1,700 texts are used that day, with "Today's limit for staff texts is reached. 300 texts are kept for sign-in codes." Codes may use all 2,000. The admin can change the limit or stop all sending in seconds (`SmsPolicy`).
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
- **Sign-in:** the HQ two-step sign-in (R3). The screens are on Vercel (R23), so they hold no session of their own: every call they make to the backend must carry proof of an HQ session that passed both steps. It is carried by the HQ cookie, which the page cannot read (R26, section 20). The backend checks the session and the `InboxAccess` row on every call; the page is never trusted to have checked.
- A call with no session answers 401, and the page sends the staff member to sign in. A call by a signed-in user without access, or any call while `SMS_INBOX_ENABLED` is off, answers `not_found`, the same as for a path that does not exist, and the page shows "Page not found". The page files on Vercel hold no data, so serving them to anyone gives nothing away.

### 7.2 The one access check

One function in `apps/inbox`, `scope_for(actor, platform)`, answers who is asking and what they may reach on that platform. The platform is always named; there is no scope that spans platforms.

| Actor | Scope | Built now |
|---|---|---|
| A staff user with an `InboxAccess` row for `sms`, asking for `sms` | `sms` conversations owned by `hq`. Send only with `can_send` | Yes |
| Anyone asking for a platform other than `sms` | None | Yes. The answer is always none, because no other platform exists |
| A staff user without a row for `sms` | None | Yes |
| A Subscriber account (later) | `sms` conversations owned by that Subscriber's Site, when the add-on is active for it | No. The add-on adds this branch |

Every list, read, reply and compose starts from the scope's own query. No API route or service function reaches a conversation or message any other way, and a `public_id` outside the scope answers "not found", the same as one that does not exist. Admitting a Subscriber later is a change to this one function, not to the screens or the service functions.

HQ staff reach only `hq` conversations. A Site's conversations with its own customers are not HQ's to read, and the add-on decides whether HQ ever sees them.

## 8. The audit record (R3)

Written through `apps.operators.audit.record`, in the same transaction as the action. **No entry ever holds message text.**

| Action | When | `detail` (kept) | `personal` (blanked after 1 year) |
|---|---|---|---|
| `sms_inbox_open` | Each time a conversation's texts are returned by the API (a conversation page is opened) | The owner kind, how many messages were shown | The contact, masked |
| `sms_inbox_reply` | Each reply, sent or refused | The message's and the send record's ids, the outcome (`queued` or `refused`), the reason when refused, the length in characters | The contact, masked |
| `sms_inbox_compose` | Each composed text, sent or refused | The same, and whether it started a new conversation | The number, masked |
| `sms_inbox_access` | Each `grant` and `revoke` | The staff user it was for, and the level | Nothing |

- Every entry's `detail` names the platform (`platform: sms`), so the record says which platform was read or written on once there is more than one.
- The operator is the signed-in staff user. The target is the conversation's `public_id`.
- The three inbox actions are declared `PERSONAL`, because a masked number is still part of a person's number. The existing rule `admin_audit_personal_values` blanks `personal` one year after the entry is written, which matches R2. That rule runs only where `RETENTION_ENABLED` is on and, until slice 4, only by command (`docs/runbooks/retention.md`).
- `sms_inbox_access` is declared `KEPT`.
- The audit entry is written by the backend in the API call, never by the page, so a page that is changed or bypassed cannot read a text without an entry.
- Opening the inbox list writes no entry, because the list shows no message text (section 9). Opening the "not yet available" state of another platform writes none either: it shows no data.

## 9. Masking

- Masking is done by the backend. An API answer never carries a value the screen may not show: the page receives the masked number, not the full one.
- **The inbox list** shows, for each conversation: the masked number (`+63917***4567`, through `mask_mobile`), the time of the last text, whether the last text came in or went out, and an unread count. **It shows no message text**, not even a preview, so that reading a text always means opening a conversation, which is audited.
- **The conversation page** shows the masked number and the texts. The full number is not shown anywhere in the tool.
- **Search** matches a full number typed by the staff member, never a fragment, as the admin's SMS list does.
- **The compose form** is the one place a full number appears: the staff member types it. After sending, the conversation shows it masked.
- A sender that is not a mobile number (a short code or a sender name) is shown as received: it is not a person's number.
- The Django admin gets no list of conversations or messages. The existing `SmsMessage` admin list shows staff texts' send records like any other, masked, with no text, for as long as the Admin exists: R28 switches it off (section 22.4), and after that the send records have no screen.

## 10. The API the screens call (R23)

Under R23 the screens are pages on Vercel, so the API is no longer something left for later: it is the only way the screens reach the data, and it is mounted in this build. The service functions in `apps/inbox` stay as they were designed, each taking a scope (section 7.2) and returning plain values; each route is a thin wrapper that finds the scope, calls one function and writes the audit entry.

**Where it is mounted.** On the HQ host, under a staff prefix written here as `/staff/v1/` (the exact prefix is for the plan), in the HQ URL set. It is separate from the Subscriber API under `/v1/` on the API host: a different host, a different session, a different allowed origin. A Subscriber session never reaches a staff route, and a staff session never reaches a Subscriber route. The Subscriber dashboard can later be given the same service functions under `/v1/inbox/`, behind the account session and the same scope check (R0); that is the add-on's work.

**Rules every route keeps.**

- The session passed both steps, and `scope_for` allows the call (section 7). Otherwise 401 or `not_found`.
- A call that changes anything is refused unless its `Origin` is the one staff origin (section 20), as the Subscriber API already refuses a foreign origin.
- Every answer is sent with `Cache-Control: no-store`.
- No number, name or text is ever in a route's address or query string, so none reaches an access log. A value of that kind travels in the body. Ids in addresses are opaque `public_id` values.
- Errors use the API's one error shape, `{error: {code, message, fields}}`.

**Inbox.**

| Service function | Route | Returns |
|---|---|---|
| (the scope itself) | `GET /staff/v1/me/` | `{"user", "inbox": {"sms": {"read", "send", "bulk"}}, "bulk_enabled"}`: what the menu and the buttons may show. No personal value |
| (gateway status) | `GET /staff/v1/inbox/status/?platform=sms` | `{"phone_online", "used_today", "cap", "reserved_for_codes", "unread", "bulk_running"}` for the status line, the bell and the Bulk SMS chip |
| `list_conversations(scope, state, cursor)` | `GET /staff/v1/inbox/conversations/?platform=sms&state=unread&cursor=` | `{"results": [{"id", "platform", "contact_masked", "can_reply", "unread", "last_message_at", "last_direction", "last_status"}], "next"}`. No text |
| `list_conversations(scope, number=…)` | `POST /staff/v1/inbox/conversations/find/` with `{"platform", "number"}` | The same shape, for the one conversation with that full number, or an empty list. A POST so the number is in the body |
| `get_conversation(scope, id, cursor)` | `GET /staff/v1/inbox/conversations/{id}/` | `{"id", "platform", "contact_masked", "can_reply", "opted_out", "messages": [{"id", "direction", "body", "clipped", "kind", "status", "reason", "author", "occurred_at"}], "next"}`. Marks it read and writes `sms_inbox_open` |
| `reply(scope, id, text)` | `POST /staff/v1/inbox/conversations/{id}/messages/` with `{"text"}` | `201` with the message, or an error |
| `compose(scope, to, text)` | `POST /staff/v1/inbox/messages/` with `{"platform", "to", "text"}` | `201` with `{"conversation_id", "message"}`, or an error |

**Bulk SMS (section 18).** Every route needs `can_bulk` and `SMS_BULK_ENABLED`; otherwise `not_found`.

| Route | Does |
|---|---|
| `GET /staff/v1/inbox/bulk/` | The list of bulk sends (18.2.1): per batch its id, state, who, when, and counts. No number, no text |
| `POST /staff/v1/inbox/bulk/check/` with `{"text", "source", "groups"`, or `"numbers"`, or the CSV file`}` | Step 4, the check: lines read, duplicates, opted out, lines refused with their line numbers, texts left today, this batch's size, left for later, the time it will take. Stores nothing but a short-lived check held on the server, and answers its id |
| `POST /staff/v1/inbox/bulk/` with `{"check_id", "count"}` | Step 5, confirm: the typed count must equal the checked batch's size. Creates the batch and writes `sms_inbox_bulk` |
| `GET /staff/v1/inbox/bulk/{id}/` | Progress: the state, the counts, and a result per number, masked |
| `POST /staff/v1/inbox/bulk/{id}/stop/` | Stop. Writes `sms_inbox_bulk_stop` |
| `POST /staff/v1/inbox/bulk/{id}/left-out/` and `POST /staff/v1/inbox/bulk/check/{id}/refused/` | The numbers left out of a batch, and the refused lines of a check, as a CSV file for the staff member to save. Full numbers: answered only to the staff user who ran it, `no-store`, and audited with counts |

**Contacts (section 19).** Reading needs read access; every change needs `can_send` (19.6).

| Route | Does |
|---|---|
| `GET /staff/v1/contacts/?group=&cursor=` | The list: per contact its id, first and last name, masked number, groups, `opted_out` |
| `POST /staff/v1/contacts/find/` with `{"query"}` | Find by name or by a full number. A POST so neither is in an address |
| `POST /staff/v1/contacts/` with `{"first_name", "last_name", "mobile", "groups"}` | Add. `contact_add` |
| `GET /staff/v1/contacts/{id}/` | One contact with its full number, for the edit form. `contact_open` |
| `PATCH /staff/v1/contacts/{id}/`, `DELETE /staff/v1/contacts/{id}/` | Edit, delete. `contact_edit`, `contact_delete` |
| `GET /staff/v1/contacts/groups/`, `POST /staff/v1/contacts/groups/` | The groups with their counts; add one. `contact_group_add` |
| `PATCH /staff/v1/contacts/groups/{id}/`, `DELETE /staff/v1/contacts/groups/{id}/` | Rename, remove (its contacts stay). `contact_group_rename`, `contact_group_remove` |
| `POST /staff/v1/contacts/upload/check/` with the CSV file and `{"group"}` | The check of 19.2: lines read, new, already in contacts, refused with line numbers, opted out. Stores no contact |
| `POST /staff/v1/contacts/upload/` with `{"check_id"}` | Saves the checked upload. `contact_upload` |

Errors: `invalid_mobile` (`fields.to` or `fields.mobile`), `invalid_text` (`fields.text`), `duplicate_mobile` with the existing contact's id, `rate_limited` (429), `sms_paused` and `sms_unavailable` (503), `limit_reached`, `unauthenticated` (401), and `not_found` for anything outside the scope.

**Sign-in and the console (R24).** The sign-in, sign-out and console routes are in section 22.3. They are mounted in the same place and keep the same rules.

A CSV file is sent to the backend and read there, once, in memory (18.2.2, 19.3). The page does not parse it, so the rules for a number live in one place.

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
| Staff API | The routes of section 10 in the HQ URL set, and the sign-in routes of sections 20 and 22.3 | Code. Nothing of it exists today: the HQ host serves pages only |
| Access control | Replacing and later removing the Django HQ pages (section 22.6) | Its own plan, approved by Coach Ed and reviewed by the NPC coordinator before it merges. Edits to `config/settings.py` are announced to the NPC coordinator first (22.5) |
| Railway variable | The one staff origin the HQ host answers (written here as `HQ_STAFF_ORIGIN`) | Per environment, with Coach Ed's approval. Empty means the staff API answers no browser |
| Frontend repository | A task of its own for `apps/hq` (section 3.1): the pages, their tests, its `next.config.ts` headers | A different repository and a different builder. Not started by this document. Its own plan and approvals |
| Vercel | A new project for `apps/hq`, its host name, its environment variables (the staff API's address; nothing secret), and the settings of section 21 | Coach Ed's approval. Creating a project, a domain or a variable is his |
| Railway variable | `SMS_INBOX_ENABLED=1` | Per environment. Never before `inbox.0001` is applied there |
| Switch | `sms_device incoming on --name system` | Per environment, each time. **Only after the NPC and privacy wording is amended (R1)**. This is the step that starts storing people's texts |
| Access | `sms_inbox_access grant` for each named staff user | Coach Ed names who, and whether they may send |
| On the phone | Allow the app's "receive SMS" permission when it asks, after the switch is on | Unproven on the real phone: the restricted-settings and Play Protect steps (Runbook) |
| Documents | `docs/runbooks/sms-gateway.md` ("Incoming texts: the switch stays off" is rewritten), `docs/runbooks/retention.md`, the Gateway design's section 14 (superseded by this document), the scope document | With the build's pull requests |
| Outside this repository | The privacy page and the NPC documents (section 14) | Coach Ed's wording. Its own task |
| Dependency | None | |
| Phone app | No change | The app already forwards behind the server's switch |

Order on any environment: deploy the backend dark; apply the two migrations; set `SMS_INBOX_ENABLED`; deploy the staff app and set the staff origin on the backend; grant access; and, only after the wording is amended, turn the phone's switch on. To go back: turn the phone's switch off, then unset the variable. The tables stay and nothing reads them.

## 13. The screens and the mockup (R6)

**The menu (R7, R21).** The HQ side menu has two items of this design: "Inbox", with the unread count, and under it "Contacts" (section 19). On a phone both are in the bottom bar. Each is shown only to a user with access. The page title is "Inbox". "New SMS" is not a menu item: it is a button on the SMS list, as actions are on the dashboard. Below a separator is "Admin console", which is the staff area's first page (R24, section 22.3); on a phone it is the third item of the bottom bar, "Console". There is no "Admin lists" or "Lists" item: the Django Admin is not shown in the staff area (R27).

**The platform menu (R7).** At the top of every Inbox page is a horizontal menu: SMS, Facebook Messenger, Instagram, TikTok, Email, Web chat. It is the dashboard's Settings menu (the row that holds "Business Settings", "Plans", "Billing" and the rest; `.set-tabs` in `dash.css`), copied rule for rule: text links 15 pixels and semi-bold, 12 by 16 pixels of padding, 4 pixels apart, one thin line under the row, the current item in the brighter text colour with a 2-pixel blue underline, 24 pixels of space below. On a phone it behaves as the dashboard's does: the row stays on one line and is swiped sideways; the page itself does not scroll sideways.

**The top-right buttons (R9).** Three 44-pixel buttons, 8 pixels apart, copied from the dashboard's top bar in its order: search, theme, bell. On a phone they sit in the top bar before the account circle; as on the dashboard, the wordmark narrows below 420 pixels and the theme button is dropped below 380 pixels. What each does in this build, kept to what the design already has:

| Button | On the dashboard | In this build |
|---|---|---|
| Search | Opens a search of quick links | A link to the SMS list's "Find a full number" field. It finds a conversation by a full mobile number, never a fragment (section 9). No new search is built |
| Theme | Switches dark and light | The same |
| Bell | Opens a panel of notifications | A link to the SMS list, showing a dot when any conversation is unread (read from `unread_count`). There is no panel, no stored notification and no alert when a text arrives; those stay out of scope (section 16) |

So both new buttons work, as links, without any new record. The dashboard's own panels (the quick-links search and the notifications panel) are not copied, because each would be new scope.

**The pages (R23).** Pages of `apps/hq` in the frontend repository, at the paths of section 3.1. Each is a static page that loads its data from the API of section 10.

**The "not yet available" state (R8).** One page for the five platforms: the platform's name, "is not yet available", the sentence "Only SMS works in the Inbox today. Nothing is received or sent on this platform.", and a button back to SMS. It shows no conversation, made-up or real, and promises no date. It calls no route but `me`, reads and writes no table, and is shown only to a staff user who can open the Inbox. The five names are fixed text in the page: there is no platform table, setting or switch for them.

They follow the Subscriber dashboard: the Manrope typeface, the blue `#173DB8` primary, the dark navy sidebar on desktop, the top bar and bottom tab bar on phones, the eyebrow and heading at the top of each view, cards with a 10-pixel radius, pill chips, 44-pixel buttons, and the dashboard's dark theme with its light theme when the device prefers light.

**Mockup:** `docs/superpowers/specs/mockups/2026-11-11-sms-inbox/`: `index.html` (the SMS list), `conversation.html`, `compose.html`, `platform.html` (the "not yet available" state; `?p=instagram` and the like pick the name shown), `bulk.html` (Bulk SMS, section 18: the list of bulk sends and the three steps of a batch, drawn on one page), `contacts.html`, `contact.html` (add or edit a contact), `groups.html` and `import.html` (upload contacts from a CSV file), all section 19; `sign-in.html`, `code.html`, `verify.html` and `console.html` (the sign-in pages and the console, R24, section 22.3); one shared `mockup.css`. Static files, made-up data only: no real number and no real text. To open it:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory docs/superpowers/specs/mockups/2026-11-11-sms-inbox
```

Then `http://127.0.0.1:8765/` (http, port 8765, local only; nothing is deployed). The mockup loads the Manrope typeface from Google Fonts, as the dashboard does today. This design asks that the staff app serve the font from its own host, so a staff page loads nothing from a third host (section 21). The "Theme" button switches between the dark and light themes.

**Approved by Coach Ed on 2026-11-11: "approved the look".** Given on the screenshots sent to him of the mockup in PR 75: the four moved HQ pages (sign-in, code, second step, console), the console with the Django Admin removed, and the inbox, Bulk SMS and Contacts pages he saw in the earlier revisions. Dark theme, at 1280 and 390 pixels. **Not shown to him, and so not covered by the approval:** the light theme, the recovery-code form, the "Sign-in paused" notice, and the error page. Each of those returns to him for his eye when it is built.

Decisions the mockup shows, as he approved them:

- The side menu says "Inbox"; the platform menu sits at the top of the page, as the Settings menu does on the dashboard. There is no heading block under it (R9).
- The SMS list's "Bulk SMS" button is shown only to a staff user with the bulk permission (section 18.7).
- The list has no text preview (section 9).
- Numbers are masked everywhere except while typing one in compose.
- The compose page states the rules above the form: one SMS, no links, no marketing, one number at a time, and that the text is recorded.
- The reply box shows a character count out of 160.
- A conversation with a short code or sender name has no reply box, and says why.
- Like the dashboard, the pages are static files that load their data with script (R23). Without script they show nothing, and no data is in the page file itself. This replaces the earlier "the pages work without script", which was true of Django pages only.
- There is no automatic refresh. A "Refresh" button asks the API for the list again.
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
10. **No new recipient of a text.** Texts still travel only through the mobile carrier. See point 18 for the host that serves the staff screens.
11. **The launch announcement (R11).** One announcement by SMS to TWCinc subscribers is an exception to "no marketing". The wording must allow it before any such text is sent, and must keep every other marketing text forbidden.
12. **Where those numbers come from (R12).** A list of mobile numbers supplied by Coach Ed as a file, with his confirmation that those subscribers may be contacted. No system link to TWCinc. The wording names this source and the basis for texting those people.
13. **Opting out (R13).** Every bulk text says "Reply STOP to opt out". A number that replies STOP is kept on a do-not-text list and is skipped by later bulk texts. **That list is kept past 365 days**, until the person asks otherwise, because deleting it would start the texts again. This is a second retention period beside point 6.
14. **What a bulk send keeps.** The one text and its counts for 365 days; the list of numbers only until the batch ends; a send record per number for 365 days, as for every text.
15. **The documents the Parent names:** register row 6; filing-store-sms; filing-subscriber-accounts-billing; filing-subscriber-stores; PIA 5; privacy manual s8; the Terms and Privacy provider list. This session did not read them.
16. **Contacts (R16, R17, R21, R22).** For the first time a person's first and last name is stored together with their mobile number, with the groups staff put them in. **What the list is:** a kept list of the people NegosyoOnline deals with, which HQ staff add to and look up. It is not only the recipients of the launch announcement, so the purpose stated must be that wider one; choosing who gets a bulk text is one use of it. Where the names and numbers come from: typed by staff, or a CSV file Coach Ed supplies. No link to TWCinc, and no link to Subscriber account records. **A contact is kept until HQ staff delete it**, with no automatic deletion. This is a third retention period beside points 6 and 13. No contact is stored in any environment before this wording is amended.
17. **A contact's rights.** A person can be in Contacts without having an account, without having written to us and without knowing they are listed. How such a person is told, the basis for keeping them, and how they ask to see, correct or delete their contact record. Staff can edit and delete a contact on the screen; deleting it does not remove their number from the do-not-text list.
18. **The staff screens are served by Vercel (R23).** The screens that show numbers, names and message text are page files hosted by Vercel, a provider that until now served only the public site, the Subscriber dashboard and the storefronts. As designed (section 21), the data itself goes from the backend on Railway straight to the staff member's browser and is not sent to, stored by or logged by Vercel. The wording and the provider list should say what Vercel does for the staff tool (it serves the page code) and what it does not (it holds no number, name or text). Under R24 the staff sign-in forms are served by Vercel too: the codes a staff member types go from their browser to the backend, not to Vercel, but the page they are typed into is Vercel's (section 20.6). If the build ever passes that data through Vercel's servers, the wording must change first.

## 15. Tests the build would write

- **Storing.** With either switch off, `received` answers 204 and writes nothing, and reads no inbox table. With both on: a valid list is stored; a repeat of the same ids stores nothing new; a bad list stores nothing and answers 400; a sender that is not a Philippine mobile is kept as received and cannot be replied to; a text over 2,000 characters is cut and marked; the owner is `hq` whatever the body says; no number or text appears in a log line.
- **Sending.** A reply and a composed text each make one send record with no text, one `out` message with the text, and one audit entry. A link, a 161st character and a non-SMS character are refused before anything is queued. A refused send (limit, kill switch, phone offline, gateway off) stores no message and shows its reason. A staff text takes one slot of the platform count.
- **Order and interval.** With a code and a staff text waiting, the code is handed out first. A staff text carries a gap of 18 to 25 seconds; a code carries 0. Only one paced text is with the phone at a time.
- **The number.** Every form `normalise_ph_mobile` accepts is accepted; a landline, a foreign number and a short number are refused. Composing to a number with a conversation adds to it.
- **Platform.** Every stored conversation and message has `platform` `sms`, and a message's platform always equals its conversation's. `scope_for` answers none for any platform other than `sms`, with or without an `InboxAccess` row. A call for any of the five other platforms, or for a platform name that is not one of the six, answers `not_found` and reads and writes no inbox table.
- **Access.** A verified session without an `InboxAccess` row, a superuser without a row, a session that passed only the first step, and no session at all each get 401 or `not_found` on every route of section 10, and no data. A read-only user's reply is refused. A conversation owned by a Site or an account (made in the test) cannot be listed, opened or replied to by HQ staff. Every route answers `not_found` while `SMS_INBOX_ENABLED` is off.
- **Audit.** Opening, replying and composing each write the declared entry, which names the platform; no entry contains the text or a full number; an undeclared action is refused.
- **Masking.** The list answer contains no message text and no full number. The conversation answer contains no full number.
- **Retention.** A message older than 365 days is deleted; a younger one stays; a conversation left empty is deleted; the dry run changes nothing; the rule does nothing while the switch is off.
- **The command.** `grant`, `revoke` and `list`; `sms_device incoming on` refuses while `SMS_INBOX_ENABLED` is off.
- **Calls from the page (R23).** A changing call without the staff origin is refused; a call from the dashboard's origin, the public site's origin or a preview address is refused; every answer carries `no-store`; no route takes a number, name or text in its address; a repeated send with the same request id does not send twice. The tests of what section 20 settles (R26): a session that passed only the first step reaches nothing, sign-out ends access at once, an expired session answers 401.
- **The frontend task's own tests**, in the frontend repository: the pages show only what the API answers, keep no data in browser storage, and put no value in an address. For that task's plan.
- Checks: `manage.py makemigrations --check`, `manage.py check`, `manage.py check --deploy`, the full test run.

## 16. Out of scope

- **Facebook Messenger, Instagram, TikTok, Email and Web chat (R8).** No screen beyond the shared "not yet available" state, no record, no connection to any of those services, no credential, no setting, no NPC or privacy wording. Each is its own task with its own design, plan and approvals. This design only leaves room for them: the `platform` value on the records, the platform named in the access check and the audit entry, and the menu.
- Any add-on screen, billing, SIM custody, dedicated line, any broadcast or marketing text other than the launch announcement of section 18, and any Subscriber access. The `site` and `account` owners are fields and one branch point, nothing more.
- **The Django Admin, in any form (R27).** It is not shown, linked, served from Vercel, restyled or rebuilt as staff pages. No page of this design lists Subscribers, sites or sent emails. It is switched off on the backend as the last step of the move (R28, sections 22.4 and 22.6). Pages on Vercel to replace its lists are not designed: whether and when is his (section 22.7).
- **A plan for moving or removing the Django HQ pages.** Section 22 designs the move (R24) and gives the order only. The plan is its own approval (22.5).
- A passkey or security key as the second step (20.6), and any change to how admin identities are enrolled or reset.
- Any change in the frontend repository. This document is a design; the frontend work is its own task.
- Mounting the Subscriber routes under `/v1/inbox/` (section 10): the add-on's.
- Any change to the Django HQ pages while they still exist. Their Vercel counterparts (section 22.3) take the dashboard's look; the Django pages are not restyled before they are removed.
- Linking a sender's number to a Subscriber account, or showing account details beside a conversation.
- Showing a full number; deleting a conversation or a message by hand; exporting conversations.
- Fixed templates, saved replies, attachments, MMS, texts longer than one SMS, sending to more than one number outside Bulk SMS (section 18).
- Delivery receipts; notifications to staff when a text arrives; automatic refresh.
- For contacts (section 19): any link to Subscriber account records, and showing registered accounts in Contacts (R22); any import from or sync with TWCinc, custom fields on a contact, putting a contact's name into a text (name merge), merging two contacts, exporting contacts, and showing a contact's name in the inbox beside a conversation.
- A second phone, SIM or line, for bulk texts included (R14); adding or removing a do-not-text number by hand; a link of any kind to TWCinc; order-status texts.
- The privacy page and the NPC documents themselves.
- Anything on staging or production.

## 17. What was verified, and what was not

Verified by reading, on 2026-11-11: the Task and Parent with every comment; the code named in section 1 at `1ba9cc6`; the dashboard's styles at frontend `36d2ec4`, and its Settings menu and top bar (search, theme and bell buttons) at frontend `origin/main` `5802934`.

Verified by opening the mockup's five pages (list, conversation, compose, "not yet available", Bulk SMS) in headless Chrome over `http://127.0.0.1:8765/`, with the DevTools protocol's device emulation (`Emulation.setDeviceMetricsOverride`; `mobile` true at 390 pixels), at 1280 and at 390 pixels wide, in the dark theme and in the light theme: on every page the document's `scrollWidth` equals its `clientWidth` (1280 and 390), so no page scrolls sideways. Every dark-theme screenshot was read at both widths and nothing overlaps. At 390 pixels the platform menu is 619 pixels of content in a 358-pixel row: it is swiped sideways inside its own row, as the dashboard's Settings menu is. The first attempt at the phone top bar with three buttons was 18 pixels too wide; it was fixed the way the dashboard does it (a narrower wordmark below 420 pixels) and measured again.

After R21 (Contacts on the main menu), all nine pages were opened the same way at 1280 and 390 pixels in the dark theme: the side menu and the phone's bottom bar show "Inbox" and "Contacts" on every page; the four Contacts pages have no platform menu and mark "Contacts" as current; the SMS list's buttons stay on one line at 1280 pixels; no page scrolls sideways. The 1280 captures of the SMS list, Contacts and the edit form, and the 390 capture of Contacts, were read; the rest were measured. The light theme was not captured for this revision.

For R23, read on 2026-11-11 and not run: the frontend repository's layout and the dashboard's headers and session notes at `origin/main` `8e92124`; the backend's API origin check (`apps/common/middleware.py`), the account cookie (`apps/accounts/sessions.py`) and the HQ session cookie (`apps/operators/hq_views.py`, `hq_auth.py`). The frontend repository was not edited.

For R24 and R25, read on 2026-11-11 and not run: the code in the "HQ pages" row of section 1. The four new mockup pages (`sign-in.html`, `code.html`, `verify.html`, `console.html`) were opened in headless Chrome over `http://127.0.0.1:8765/` at 1280 and 390 pixels in the dark theme; what was measured and read is in the pull request's report.

For R27: the ten pages that carry the menu were opened the same way at 1280 and 390 pixels in the dark theme. On each, the side menu reads Inbox, Contacts, Admin console; the phone's bottom bar reads Inbox, Contacts, Console in three equal parts; no page scrolls sideways; and no page shows the words "Admin lists" or "Django Admin". Looked at: the console at both sizes, the SMS list at both sizes, Contacts at 390. The rest were measured only. The proposed host name of 22.7 was not run against the start-up check, and nothing about the Django Admin on the backend was changed or tested.

For R28: the mockup did not change, so nothing was recaptured. What the Admin registers was read from the `admin.py` files of `accounts`, `sites`, `notifications`, `catalog`, `sante` and `operators` for their registered models and permission methods, not run and not read line by line; which fields of an account a staff user's permissions let them change was not checked. The management commands were listed by file name only. The runbook lines named in 22.4 were found by searching `docs/runbooks/` for "admin"; other documents were not searched.

For the approvals of 2026-11-11: both were read as comments on the Task. The look was approved on dark-theme screenshots at 1280 and 390 pixels. The light theme has not been captured since the sixth revision of the mockup, and the recovery-code form, the "Sign-in paused" notice and the error page are described in 22.3 and were never drawn; none of these is covered. What section 17 lists as reasoned and not tested is still not tested: the approval of the design does not change that, and the first plan that depends on the sign-in cookie between the staff host and the HQ host must test it before anything is built on it.

Not verified:

- For R24: that a browser accepts the HQ cookie set by an answer to a call from the staff host, was reasoned from the cookie's attributes and not tested. The staging facts in 22.5 were relayed, not checked. The recovery-code form, the "Sign-in paused" state and "Page not found" are described in 22.3 and not drawn.
- Everything in sections 20 and 21 is reasoning from that reading. No cross-origin call to the HQ host was made, and how a browser treats the HQ cookie on such a call was not tested. The Vercel projects' settings (who may deploy, preview protection, log retention) were not looked at.

- The light-theme screenshots were measured for overflow but not read.
- Any browser other than Chrome, and a real phone. The swipe of the platform menu was measured, not performed by hand. The theme button being dropped below 380 pixels was written, not measured.
- The platform menu and the top-right buttons against the running dashboard side by side. Their rules were copied from the dashboard's stylesheet and the mockup was read; the two were not opened next to each other.
- Section 18's numbers (time to send, the size of a batch) are arithmetic on the existing cap and gap. The carrier's behaviour toward a SIM that sends one text to many numbers is not known; that risk is accepted (R14).
- Whether the phone's poll can feed a batch to the queue without a new worker (section 18.3) was not checked against the code.
- The CSV uploads (Bulk SMS and contacts) are drawn as an upload area around the browser's own file button, which shows no chosen file in the mockup; reading a real file was not tried.
- `bulk.html` draws one batch at three moments on one page and says so in a note: steps 1 and 2 before it is confirmed (214 texts used), the list and step 3 while it sends (610 used, as on the SMS list).
- The "Send to" choice is drawn with all three options open at once so a screenshot shows each; switching between them was not built.

- Nothing was run against the backend. This stage wrote documents and a static mockup only.
- The phone app's behaviour with the switch on, on a real phone with a real SIM: the permission prompt, texts in several parts, two SIMs.
- That 18 to 25 seconds between staff texts is what Coach Ed means by "the sending interval" for replies (section 6.3).
- The NPC documents and the privacy page were not read by this session.
- Whether the HQ host sends a Content-Security-Policy header; none was found in `config/settings.py`. The built pages would carry their styles and the font from HQ itself in either case.

## 18. Bulk SMS (R10 to R15)

Part of the design since rulings R10 to R14 (section 2), with the CSV upload and the list of bulk sends from R15. Like the rest of this document it was approved on 2026-11-11 and nothing is built. The mockup page is `bulk.html`. Where this section and an earlier one disagree about bulk sending, this section is the design; the table in 18.9 lists every such place.

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
- While a batch is waiting or sending, the "Bulk SMS" button on the SMS list carries a small chip, "Sending", so staff with the bulk permission see it without opening the page. It sits on the button so the status chips and the buttons stay on one line (R9).
- The counts are read from the API when the page is loaded. Whether the page asks again by itself is for the plan.
- A row stays as long as its batch record does: 365 days (18.4).

#### 18.2.2 The CSV file (R15)

- One mobile number per row, in the first column. A first row that is not a number is taken as a header and skipped.
- Other columns are ignored: they are not read into any record, not shown and not kept. So a file exported with names beside the numbers can be used as it is, and the names go nowhere.
- At most 1 MB. A larger file, or one that is not text, is refused with a plain message and nothing is read.
- The file is read once, in memory, and is never stored as a file (18.4). Only the numbers that pass the check become the batch's list.
- A batch has one source: groups, a file, or a pasted list (18.2 step 3). They are not combined.

### 18.3 Batches (R10)

- One bulk send is one batch. A batch never goes over the texts left for bulk today: 1,700 less what is already used, because 300 of the 2,000 a day are kept for sign-in codes (R20, 18.10). The cap and the 18 to 25 second gap are the existing ones; nothing raises them.
- When the list is larger than what is left, the batch is **cut to fit**: it takes the first numbers in the list up to what is left, says so with both counts, and offers the numbers it left out as a download for a later batch. When none are left for bulk today, the batch is refused.
- One batch at a time. A second cannot start while one is sending.
- A batch belongs to the day it was confirmed (Manila time). Whatever has not been sent when the day ends is marked "Not sent" and can be downloaded for a later batch.
- Codes are always first (section 6.3): bulk texts use the paced class behind every waiting code, one at a time. A code asked for while a batch runs takes a slot of the same 2,000, so the last texts of a batch that was cut to the very limit can be refused by the cap; they show "Not sent: today's limit reached".
- A staff text waits for the phone at most 1 hour (section 6.1), and at this gap the phone sends about 145 to 200 texts an hour. So a batch is not queued all at once: its texts are handed to the queue a few at a time. How that is driven without a new worker (the phone's own poll is the proposal) is for the plan to confirm.
- The 60-an-hour limit per staff user (section 6.3) applies to replies and composed texts only. A batch is limited by the cap, the gap and one batch at a time.

In numbers: a full day's 1,700 bulk texts take about 8.5 to 12 hours of sending. The mockup's example is a list of 2,293 numbers on a day when 214 texts are used: the batch is cut to 1,486, takes about 7 to 10 hours, and leaves 807 for a later batch. A list of more than 1,700 numbers takes at least two days.

### 18.4 The numbers and where they come from (R12)

- Coach Ed supplies the numbers, as a file or a pasted list, for each batch, and confirms that those subscribers may be contacted. That confirmation is his and is not something the tool checks.
- There is no link to TWCinc: no integration, no access to a TWCinc database, no TWCinc code and no TWCinc secret. The tool sees only what a staff user pastes or uploads.
- An uploaded file is read and never stored as a file. Columns other than the first are dropped as it is read (18.2.2).
- The batch keeps its list of numbers only while it needs them: the rows are deleted when the batch has finished or was stopped and its last text has a final result. After that the screen shows the batch's counts, not its numbers.
- What stays, as for every text the gateway sends: one send record per text (the number, the purpose, the result, no text), deleted at 365 days by the existing rule and shown masked in the admin's SMS list while the Admin exists (R28, section 22.4).
- The batch keeps its one text and its counts for 365 days, as staff texts are kept (R2).
- A bulk text does not open a conversation in the inbox, so 1,486 announcements do not bury the list. If a recipient writes back, their text arrives as an ordinary conversation.

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
- **A switch**, `SMS_BULK_ENABLED`, a new Railway variable, off. While off, the bulk routes answer `not_found` and the page shows no button. Creating the variable in a Railway environment is Coach Ed's approval at build time; this design creates none. It is turned on per environment with Coach Ed's approval, and only after the NPC and privacy wording covers the announcement (R11).
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

Tests the build would add to section 15: the opt-out line is always present and counted; a text over 138 characters is refused; the list is checked, de-duplicated and stripped of opted-out numbers; a batch larger than the texts left for bulk is cut and says so; a batch with nothing left is refused; a staff text and a bulk text are refused at 1,700 used while a code is still sent; a second batch is refused while one runs; a code is handed out before a waiting bulk text; stop sends nothing more, from the list and from the progress; a CSV with a header row and extra columns yields only its first column's numbers; a file over 1 MB is refused; the list of bulk sends shows each state and holds no number and no text; STOP in any case with spaces lists the number, and "STOP please" does not; a listed number can still be replied to; a batch is refused while incoming texts are not stored, while `SMS_BULK_ENABLED` is off, and for a user without `can_bulk`; the recipient rows are gone after the batch ends; no audit entry, log line or page after the check holds a full number, and none holds the text.

### 18.9 What this changes in the earlier sections

| Earlier | Now |
|---|---|
| R4 and section 14 point 4: no marketing | One stated exception, the launch announcement, after the wording is amended (R11) |
| Section 6.3: "There is no list, no paste of many numbers, no upload and no 'send to all'" | True of reply and compose. Bulk SMS is the one path with a list |
| Section 6.3: the one-number form, the audit record and the hourly limit stand between "any Philippine mobile" and bulk sending | For bulk, the controls are the bulk permission, the switch, the confirm step, the cap and the audit record |
| Section 7.1: two levels, read and send | A third, bulk |
| Section 11: everything goes at 365 days | The do-not-text list does not |
| Section 16: no broadcast or marketing text; no sending to more than one number | Bulk SMS for the launch announcement is in scope. Everything else on those lines stays out |

### 18.10 Texts kept for sign-in codes (R20)

Settled by Coach Ed: "Hold back 300".

- Of the 2,000 texts a day, 300 are for sign-in codes only.
- Bulk SMS and single staff texts (a reply, a composed text) stop once 1,700 texts are used that day. The 1,700 counts every text sent that day, codes included. Applying the reserve to single staff texts is the coordinator's routine choice, so the reserve is truly for codes.
- A batch is at most 1,700 less what is already used. A batch that was cut to that limit can still lose its last texts if codes are sent while it runs: they show "Not sent: today's limit reached" (18.3).
- The 300 is a backend setting. Changing its value is Coach Ed's ruling: it is an estimate made before any real sign-up volume, and no one else changes it. It is changed on the backend as a setting, not on any screen; the Django Admin is switched off (R28) and is not where it is changed. Where the setting is stored is for the plan.
- The Bulk SMS page and the New SMS page say so in words.

## 19. Contacts and groups (R16 to R19)

Added by R16, with R17 (kept until staff delete), R18 (the same inbox staff) and R19 (CSV upload into a group), and moved to the main menu by R21. Like the rest of this document it was approved on 2026-11-11 and nothing is built. The mockup pages are `contacts.html`, `contact.html`, `groups.html` and `import.html`. With this the Task has three parts: the inbox, Bulk SMS and contacts.

### 19.1 What it is for

A kept list of the people NegosyoOnline deals with, which HQ staff add to and look up (R21: "It's not just for bulk sending. It is a kind of like a database of users of Negosyo Online"). Contacts are sorted into groups, and Bulk SMS reads its groups from this list, so a batch is sent to a group instead of a file each time (R16). Sending is one use of the list, not its purpose.

A contact is hand-entered or uploaded (19.3). It has no link to a Subscriber account record: Contacts does not read, show or match registered accounts. Coach Ed ruled so (R22): "No, hand-entered only".

**Contacts are the first place in this tool, and in the SMS records, where a name is stored together with a mobile number.** The NPC and privacy wording must cover that, with the wider purpose, before any contact is stored in any environment (section 14 points 16 and 17), the same "amend first" order as R1 and R11.

### 19.2 The screens

"Contacts" is its own item in the HQ side menu, under "Inbox", and in the phone's bottom bar (R21). It is not a button or a page inside SMS: the SMS list keeps "Bulk SMS" and "New SMS" only. The Contacts pages do not carry the Inbox's platform menu; "Contacts" is the current menu item on each, and the add, edit and upload pages link back to Contacts. The pages sit at `/contacts/` beside `/inbox/` on the staff host (section 3.1); the exact paths are for the plan. The Contacts page has two views, Contacts and Groups.

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

The same HQ staff who hold the SMS inbox permission, behind the two-step HQ sign-in, through the one access check of section 7.2. There is no separate contacts permission (R18 stands after R21): the "Contacts" menu item is shown to those staff and to no one else. This design reads R18 with the two existing levels: a staff user with read access sees contacts and groups; adding, editing, deleting and uploading need `can_send`, as every other change does. Sending to a group still needs `can_bulk` (18.7). Whether read-only staff should also edit is the coordinator's to confirm.

### 19.7 Retention (R17)

- A contact is kept until HQ staff delete it. No job deletes contacts, and the 365-day rule does not apply to them.
- Texts, send records and batches stay on the 365-day rule (R2). The do-not-text list keeps its own rule (18.5).
- Deleting a contact removes its name, number and group memberships at once. It does not remove send records or a conversation with that number, which go at 365 days.

### 19.8 The audit record

Through the operator audit record. An entry holds who, when, ids and counts. It never holds a first or last name, a group's name, or a full number.

| Action | When | `detail` (kept) |
|---|---|---|
| `contact_open` | A contact's form is opened (the full number is shown) | The contact's id |
| `contact_add`, `contact_edit`, `contact_delete` | A contact is added, changed or deleted | The contact's id, which fields changed (their names, not their values), how many groups it is in |
| `contact_upload` | An upload is saved | The group's id, lines read, contacts added, already in contacts, lines refused, opted out |
| `contact_group_add`, `contact_group_rename`, `contact_group_remove` | A group is added, renamed or removed | The group's id, how many contacts it held |

All are declared `KEPT`: they hold no personal value.

### 19.9 Records and what the build would need

To the lists in sections 12 and 18.8, in the same new app, each needing the same approvals:

| Item | Notes |
|---|---|
| `Contact` | Owner `hq` (R0: a Site or Subscriber later), first name, last name, the normalised mobile number (one per owner), who added it, the times. No platform field, because Contacts is not part of SMS (R21), and no field that points at a Subscriber account |
| `ContactGroup` | Owner `hq`, the name (one per owner), the times. A contact belongs to any number of groups |
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
| Section 13: one menu item, "Inbox"; the first contacts draft: a "Contacts" button on the SMS list | Two menu items, "Inbox" and "Contacts" (R21); no Contacts button on the SMS list; the Contacts pages have no platform menu |
| The first contacts draft: a list of people to send a bulk text to | A kept list of the people NegosyoOnline deals with; Bulk SMS reads its groups from it (R21) |

### 19.11 Left out

No link to Subscriber account records and no registered accounts shown in Contacts (R22). No import from or sync with TWCinc. No custom fields: a contact is a first name, a last name, a number and groups. No name merge: a text never carries a contact's name. Also not here: merging two contacts, exporting contacts, and showing a contact's name beside a conversation in the inbox.

### 19.12 Settled: Contacts and registered accounts (R22)

R21 raised one question: should Contacts also show registered Subscriber accounts, or stay a list staff enter and upload by hand? Coach Ed ruled: "No, hand-entered only". Contacts holds only what HQ staff type in or upload from a CSV file. It does not show registered Subscriber accounts and has no link to account records.

Nothing in this section is open for Coach Ed. R17, R18 and R19 stand unchanged, and 18.10 is settled by R20. How staff sign in on Vercel is settled by R26 (section 20). What is left for him is section 22.7. The look and the design are approved (2026-11-11).

## 20. How HQ staff sign in on Vercel with the two-step check kept: settled by Coach Ed (R23, R24, R26)

**Settled by Coach Ed (R26): the first column of 20.4, with the cost of 20.6 accepted.** His words: "Okay, just do all the recommended for the these 4 rows, how it works, Django admin, the security, and the build." It was a security decision and it was his; he was shown the three columns and the cost before ruling. The rest of this section is kept as the record of what was weighed.

**Changed by R27.** R26 stands for the staff area: the forms on Vercel, the backend checking the codes as today, today's cookie, no token held by the page, no migration, the cost of 20.6. What R27 takes away is the Django Admin: it is not shown in the staff area, so this design no longer has the staff sign-in lead to it. **Then R28:** the Admin is switched off on the backend altogether (22.4). Once the move is complete, the staff sign-in opens the staff area and nothing else.

This section was rewritten after R24. Before R24 it recommended leaving the two sign-in pages on Django and letting the Vercel screens use the session made there (then called way 1 alone). R24 moves the sign-in pages themselves, so that answer is no longer available: staff will type their email code and their authenticator code into a page served by Vercel. What was left to decide was how the session made by that sign-in is carried, and whether the same sign-in opens the Django Admin. This section lays out the ways that were put to him.

### 20.1 What exists today

- HQ staff sign in on the HQ host in two steps: an emailed 6-digit code, then an authenticator code or a recovery code (`apps/operators`). These are Django pages (section 22.1).
- A sign-in that passed both steps sets one cookie on the HQ host: a random token, of which the database keeps only a hash, good for 12 hours from the emailed-code step. The cookie is host-only, `HttpOnly`, `Secure` and `SameSite=Strict`. The identity is read again on every request, and signing out ends it at once.
- **The Django Admin has no sign-in of its own.** Since PR 45 (z8uq9mdxvt, "hq is the only admin door") it is mounted only on the HQ host and opens only for that same HQ session. Its own login page sends the browser to the HQ sign-in, and its password pages answer "not found".
- The HQ host answers pages only. It has no API and allows no other origin to call it. A check at start-up refuses a configuration where the HQ host is also the dashboard's, the public site's or an allowed API origin.
- The Subscriber dashboard, for comparison, is a page on Vercel that calls the API host with the Subscriber's own `HttpOnly` cookie. The API allows a short list of origins to make signed-in calls and refuses a changing call from any other origin.

### 20.2 What the earlier rulings fixed, and what R26 then settled

Fixed:

- **The forms are pages on Vercel (R24).** The email form, the code form, the authenticator and recovery form, and the "sign-in paused" notice are pages of the staff app.
- **The checking stays on the backend (R23).** The pages only send what the staff member types to sign-in routes on the HQ host (section 22.3). The emailed code, the authenticator check, the recovery codes, the limits, the 15-minute pause, the session and the audit entries are the same functions that run today (`hq_auth`, `two_step`), called from a route instead of from a Django page. Nothing about how a person proves who they are is rewritten.
- **R3 stands:** two steps, the explicit permission, every read and send audited.

Settled by R26:

- How the session is carried after sign-in: the HQ cookie as it is (way 1), not a token the page holds (way 2).
- As ruled then, that the same sign-in also opened the Django Admin. R27 and R28 overtake this: the Admin is not shown and is switched off (22.4).

### 20.3 What must hold (R3)

- Only a session that passed both steps reaches any data, checked by the backend on every call.
- The explicit inbox permission is checked by the backend on every call (section 7).
- Every sign-in, wrong code, pause and sign-out, every read of a conversation and every send is audited by the backend, never by the page (sections 8 and 22.5).
- Signing out, revoking access, or disabling the staff user ends access at once, not at the end of some token's life.
- Exactly one origin, the staff app's host for that environment, may call the staff routes. Not the dashboard, not the public site, not a preview address.
- The sign-in routes give the same answer for an address that is an admin and one that is not, and keep today's limits: 5 submits an hour for an address and for an IP address, a code good for 10 minutes and 5 tries, 5 wrong second-step codes pausing sign-in for 15 minutes, a 12-hour session.
- The key that protects the authenticator secrets (`ADMIN_SECRETS_KEY`) and the secrets themselves stay on Railway. Nothing of them is ever a Vercel variable or in a page.

### 20.4 The ways that were weighed

In every column the sign-in forms are Vercel pages (the earlier "way 3"), because R24 rules it. The columns differ in what carries the session. The second and third columns are kept as they were put to him, before R27; where they speak of the Django Admin, read them with 22.4.

| | **Way 3 on way 1. The HQ cookie (chosen, R26)** | Way 3 on way 2. A token the page holds (considered, not chosen) | Way 3 on way 1, and the Admin keeps a Django sign-in of its own (considered, not chosen) |
|---|---|---|---|
| **What it is** | The sign-in routes on the HQ host set the same HQ cookie that the Django pages set today. The browser sends it with every staff call; the page never sees it. As put to him, this column also had that session open the Django Admin; R27 and R28 drop the Admin, so the session opens the staff area only (22.4) | The second-step route answers with a token. The page keeps it and sends it in a header with every call. The Django Admin cannot use a header, so it needs a sign-in of its own as well | As the first column for the staff area. A session made through the Vercel pages is marked and does not open the Django Admin. Today's Django sign-in pages stay on the HQ host as the Admin's door, and staff sign in a second time there |
| **What changes in the cookie** | Nothing, if the staff host and the HQ host are under the same registered domain, as the dashboard and API hosts are. `SameSite=Strict` then still lets the browser send it. On an unrelated domain it would have to become `SameSite=None`, which is weaker; this design asks that it never is | Nothing. A new secret, the token, exists beside it | Nothing in the cookie. The session row gains a mark of where it was made, which is a migration |
| **Security cost** | The codes are typed into a page served by Vercel (20.6). Script on the staff page can act as the signed-in person while they are on the page. It cannot carry the session away: the cookie is unreadable to script | The same, and more: the token is readable by script, so script that reads it can use it from any computer until it expires. Ending access at once needs a list of revoked tokens checked on every call, which is a session by another name | The same as the first column for the staff area. It looks as if it keeps a compromised Vercel deploy away from the Admin, but it does not: see 20.6, last paragraph |
| **Django pages that remain** | None of ours, other than the plain error pages. The Admin is switched off (22.4) | The sign-in pages, for the Admin | The sign-in pages, for the Admin. R24 is then only partly carried out |
| **Build size** | Smallest. The sign-in routes are thin wrappers around today's functions. No migration | Larger: issuing, storing, refreshing, expiring and revoking tokens, each with tests, and still a sign-in for the Admin | Middle: the first column, plus the mark, the check on it, a migration, and two sets of sign-in pages to keep in step |

A fourth arrangement, sending the staff page's calls through Vercel's own servers so the browser sees one host, is not offered: every code, number, name and text would then pass through Vercel, which section 21 forbids.

### 20.5 Chosen (R26): way 3 on way 1, for the staff area

**The sign-in forms are Vercel pages; the backend checks the codes exactly as today and sets the HQ cookie exactly as today; that session opens the staff screens.** Reasons:

1. The session secret never reaches script. In way 2 it must.
2. The checking code is not rewritten. The routes call the functions that are built, tested and running on staging; the limits, the pause, the recovery codes and the audit entries are untouched.
3. Sign-out, the 12 hours and revocation keep working on the backend as they do today, with nothing new to keep in step, and need no migration.
4. It is how the Subscriber dashboard works, which is the uniformity R23 asks for: a page on Vercel, a cookie the page cannot read, an API that names the one origin it answers.
5. It carries out R24 fully: no sign-in page of ours stays on Django.

What it needs, each stated so it can be checked:

- The staff host and the HQ host are under the same registered domain in every environment, so the cookie stays `SameSite=Strict`.
- One allowed staff origin per environment, set on the backend (`HQ_STAFF_ORIGIN`). Preview addresses are never allowed.
- A changing call, sign-in included, is refused unless its `Origin` is that origin. Django's form token cannot be used by a page on another host, so this check replaces it, as it does on the Subscriber API.
- Where the backend sends a browser to a sign-in page, it sends it to the staff app's sign-in address, taken from that one setting and never from the request.
- The staff routes are the only thing on the HQ host that answers another origin.

### 20.6 Its security cost, plainly (accepted by R26)

**From the day the sign-in pages are on Vercel, the Vercel deploy of the staff app can capture a staff member's email code and authenticator code as they type them, and sign in as that person from somewhere else.** That is a session of the attacker's own, good for up to 12 hours each time, reaching everything that person reaches in the staff area: the inbox, Bulk SMS and Contacts. When R26 accepted this cost it included the Django Admin with that person's permissions there. With the Admin switched off (R28) the cost no longer includes it: there is no Admin for a captured sign-in to reach. That holds from the step of 22.6 that switches it off. Until that step, the Admin on the HQ host still opens for the same session.

Before R24 this could not happen: the codes were typed only into pages the backend served, and the worst a compromised staff page could do was act through a signed-in person's browser while they were on the page. That narrower limit is what R24 gives up, in every column of 20.4. R26 accepts this cost.

What still limits it: the deploy rules of section 21 (who can change the staff app, no third-party script, no preview with access); the audit record, which shows every sign-in with its time; signing in again or a `reset` from the terminal, which ends every live session of that person; and removing the staff origin on the backend, which stops every staff call at once.

Weighed before R27 and kept as the record: keeping a separate Django sign-in for the Admin (the third column) does not remove this cost. A page that can capture the codes can also pass them on as they are typed: it can ask the Admin's own sign-in for a code, show the staff member the form they expect, and use what they type at the Admin's door. An authenticator code and an emailed code are not tied to the page they are typed into. Only a second step that is tied to the site's address (a passkey or security key) would resist this; that is not part of this design and would be its own task and ruling.

### 20.7 What Coach Ed ruled

**R26: the first column of 20.4.** The sign-in forms are Vercel pages; the backend checks the codes as today and sets today's HQ cookie; the page holds no token; no migration is needed. The cost of 20.6 is accepted with it. The second and third columns were considered and not chosen.

**R27, after it:** the Django Admin is dropped from the staff area and not shown. The words of R26 that had one sign-in open the Admin, and gave the Admin no separate sign-in, no longer describe anything in this design. R26 is kept for the staff area as ruled.

**R28:** the Django Admin is switched off completely. R26's sign-in opens the staff area only.

This is a design ruling. It approves no plan, build, migration or Railway variable.

## 21. The Vercel deploy inside the trust boundary (R23)

Before R23, one host served the staff tool, and only the backend's deploy could change what staff saw. Now the page code comes from a second place. **Whoever can change the staff app's deploy on Vercel can change the script that runs in a signed-in staff member's browser, and through it read every conversation and contact that person can reach, and send texts as them.** With the sign-in forms on Vercel (R24) the same person can also capture the codes a staff member types and sign in as them (section 20.6). This holds under every way of section 20. These are the rules that keep that exposure as small as it can be. Each is a requirement on the build or on the Vercel project; none is done by this document.

### 21.1 The data never rests on Vercel

- **Straight from the backend to the browser.** The page calls the staff API on the HQ host directly. No Vercel function, route handler, middleware or rewrite reads, forwards or stores a number, a name or a text. The staff app has no server code that touches inbox data.
- **Nothing private is cached.** Every API answer is `no-store`. The pages are sent `no-store`, as the dashboard's are. The page files hold no data, so a cached file gives nothing away.
- **Nothing private is logged.** No number, name or text is in a page address, a query string or a route's address (sections 3.1 and 10), so none can enter Vercel's request logs or the backend's access log. The page writes no value to the browser console. No error-reporting, analytics or session-recording script is added to the staff app.
- **Nothing private is kept in the browser.** No number, name or text in local storage, session storage, a cookie or the page address. What is on screen is gone when the tab closes. Only the theme is remembered, as on the dashboard.
- **Nothing is loaded from a third host.** The staff app's content security policy allows script and styles from itself only and calls to the one staff API only. The font is served by the app itself. It may not be framed by any page.

### 21.2 What must be true of the deploy

| About | Must be true |
|---|---|
| Who can change it | The staff app deploys only from the protected `main` branch of the frontend repository, through a reviewed pull request, as the backend does. The people who can merge there, and the people with a role in the Vercel team that lets them deploy, change settings or read logs, are now people who can reach staff data. Coach Ed names them, and the list is kept as short as the backend's |
| Preview deployments | A preview address is a different origin. The backend never allows one, so a preview of the staff app can show its pages but can reach no data and no session. Previews of the staff app are either off or behind Vercel's own sign-in. No preview is ever given the production staff address |
| Environment variables | The staff app's variables hold addresses only: the staff API's address and its own. **No secret, token or key is ever put in a staff app variable**, because anything a page can read is public. Changing a variable changes where the page sends staff data, so it is a production change with Coach Ed's approval |
| Its own project and host | A Vercel project of its own, on a host of its own, sharing no origin with the dashboard, the public site or a storefront (section 3.1) |
| Third parties | No integration, log drain, analytics or monitoring add-on is attached to the staff project without the same approval as a new data recipient |
| Staging first | The staff app's staging host talks only to the staging backend; production only to production. Neither origin is allowed by the other backend |
| If it is compromised | The staff origin is removed from the backend's setting, which stops every staff call at once; every HQ session is ended; the audit record shows what was opened and sent in the meantime |

### 21.3 What this changes for R3 and for the NPC points

- R3's controls all stay on the backend and none depends on the page behaving: the session, the permission, the audit entry and masking are enforced before an answer leaves Railway.
- What is new is who else is trusted: the frontend repository's reviewers and the Vercel team's members join the backend's operators as people whose access could expose staff data. Section 14 point 18 puts this to the NPC and privacy wording.
- None of this was looked at on the real Vercel projects (section 17). Before a build, the frontend task's plan checks each row of 21.2 against the project's actual settings and reports what it finds.

## 22. The whole HQ staff area on Vercel (R24, R27, R28)

R24 adds the existing HQ staff pages to this design: they move to the frontend on Vercel, with the Inbox, Bulk SMS and Contacts, as one staff area. R27 drops the Django Admin from that staff area: it is not shown. R28 switches it off on the backend. This section is the inventory of what the HQ host serves today, what each page becomes, what R28 switches off and what is lost with it, how the standing rulings of the admin sign-in are kept, and the order in which the Django pages would be retired. It is a design. It plans and builds nothing.

### 22.1 What the HQ host serves today

Read on 2026-11-11 at this branch: `apps/operators/hq_urls.py`, `hq_views.py`, `hq_auth.py`, `two_step.py`, `middleware.py`, `admin_site.py`, `audit.py`, `checks.py`, `pages.py` and `templates/hq/`. Built by PRs 40 and 41 (z8uq9mdwdx) and changed by PR 45 (z8uq9mdxvt).

| Path on the HQ host | What it shows | What it reads | What it writes | How it is protected |
|---|---|---|---|---|
| `/sign-in/` | "Admin sign-in": one email field and "Send code" | The hourly counters for that address and that IP address | The counters. A signed cookie, good for 10 minutes, that remembers the address asked for. After the answer is sent, and only if the address is an enrolled admin within the limits: a new code row (replacing any live one) and the email carrying the code | Django's form token. 5 submits an hour for an address and for an IP address. Every address gets the same answer, so the page does not say who is an admin. Sends a signed-in person to the console |
| `/code/` | "Enter your code": the 6-digit emailed code, and a link to start again | The 10-minute cookie; the code row | The tries count; the code marked used; a session row at the first stage; the session cookie; an audit entry `admin_sign_in_failed` for a wrong code | The form token. The code is good for 10 minutes and 5 tries, and only in the browser that asked for it. The page never says why a code failed |
| `/verify/` and `/verify/?method=recovery` | "Two-step sign-in": the authenticator code, or one of 10 recovery codes | The session at the first stage; the admin identity; its authenticator secret, opened with `ADMIN_SECRETS_KEY` | The last authenticator step used (so a code works once); a recovery code marked used; the wrong-code count; the session moved to "verified" with a new token; audit entries `admin_sign_in_ok`, `admin_sign_in_failed`, `admin_locked` | The form token. Needs a first-stage session. 5 wrong codes pause sign-in for 15 minutes, end every live session of that person and send them an email |
| (shown by `/verify/`) | "Sign-in paused" | Nothing | Both cookies are dropped | Shown once, when the fifth wrong code is entered |
| `/` | "Admin console": the signed-in address, "Two-step sign-in: Verified", when it was verified and when the session ends, a card "Admin lists" with one link, and the person's last 10 sign-in events | The session, the identity, the audit record | Nothing | A session that passed both steps. Without one, the browser is sent to `/sign-in/` or `/verify/` |
| `/sign-out/` (a button, no page) | Nothing; returns to `/sign-in/` | The session | The session ended; audit entry `admin_sign_out`; both cookies dropped | The form token; POST only |
| `/admin/…` | The "Admin lists": the Django Admin, generated by Django from the registered models (accounts, sessions, sites, sent emails, SMS records and the rest) | Those tables | Whatever each list allows; most are read-only | A session that passed both steps, an active staff user, and that user's Django permissions per list. No password sign-in exists: its login goes to `/`, its sign-out is the HQ sign-out, its password pages answer "not found" |
| `/static/admin/…` | The Django Admin's own CSS and script | Files shipped with Django | Nothing | A session that passed both steps; otherwise "not found" |
| Anything else, and every error | "Page not found", or "This page is not available right now." | Nothing | Nothing | The same plain page for 400, 403, 404 and 500, so an error says nothing about what exists |

Rules that cover every row:

- These pages exist only on the host named by `HQ_HOST`. On any other host none of them is routed, and the API prefix answers "not found" on the HQ host. If `HQ_HOST` is empty or unusable, HQ is off.
- Every answer on the HQ host carries `Cache-Control: no-store` and `X-Robots-Tag: noindex`.
- On the HQ host the user is the one the HQ session names and nobody else. A Django password session opens nothing.
- The session cookie is host-only, `HttpOnly`, `Secure`, `SameSite=Strict`, and holds a random token whose hash is in the database.
- Admin identities are added, enrolled, reset and disabled only from the terminal (`manage.py admin_identity`). No page does it.

### 22.2 Where the rulings and the code differ

Three things in the rulings and the brief do not match what the code shows. Each is reported here with both sides, and how this design reads it. The reading is this design's; confirming it is Coach Ed's (22.7).

| What was said | What the code shows | How this design reads it |
|---|---|---|
| R24 names the pages that move as "staff sign-in, console, lists" | The "lists" are the Django Admin itself. The console's "Admin lists" card is a link to `/admin/`. There is no list page that we wrote | Under R27 the lists are not shown in the staff area, and no list page is designed on Vercel. What moves is the sign-in, the code page, the second step, the "paused" notice, the console and sign-out. The Admin is today the only place staff see the Subscriber, site and sent-email lists; R28 switches it off, so those lists have no screen until he rules on Vercel list pages (22.7) |
| R25 says the Django Admin stays "behind its own two-step sign-in pages" | The Django Admin has had no sign-in pages of its own since PR 45. The pages R24 moves are its only door | Overtaken by R27 and R28: the Admin is not shown and is switched off, so it needs no door |
| The brief lists "the enforcement switch from z8uq9mdwdx" among what the HQ host serves | The switch (`ADMIN_TWO_STEP_REQUIRED`) and the Django Admin's own two-step pages were built by PR 41 and removed by PR 45. The code no longer reads the setting | Nothing to move. The two-step check cannot be switched off |

### 22.3 The staff area on Vercel, page by page

One app (`apps/hq`, section 3.1), one host, one look: the Subscriber dashboard's (R6). The signed-in pages share the side menu; the sign-in pages are a single card with no menu, because nothing is known about the person yet.

| Page on the staff host | Replaces | What it shows | What it needs from the backend |
|---|---|---|---|
| `/sign-in/` | Django `/sign-in/` | The email field and "Send code" | `POST /staff/v1/sign-in/` with `{"email"}`. Answers 204 for every well-formed address, known or not, and sets the 10-minute cookie. `invalid_email` only for something that is not an address |
| `/code/` | Django `/code/` | The 6-digit field, "Continue", and the link to start again | `POST /staff/v1/sign-in/code/` with `{"code"}`. 204 and the first-stage session cookie, or `invalid_code` with no reason given |
| `/verify/` | Django `/verify/`, both forms | The authenticator field, or the recovery field | `POST /staff/v1/sign-in/verify/` with `{"code"}`. 204 and the verified session cookie; `invalid_code`; or `sign_in_paused` |
| (a state of `/verify/`) | The "Sign-in paused" page | "Too many wrong codes. Sign-in is paused for 15 minutes." and a link back | The `sign_in_paused` answer above |
| `/` | The Django console | The signed-in address, "Two-step sign-in: Verified", the two times, and the last 10 sign-in events. The Django page's "Admin lists" card is left out (R27) | `GET /staff/v1/console/`: `{"email", "verified_at", "expires_at", "events": [{"action", "surface", "step", "at"}]}`. The person's own events only. Writes nothing, as today |
| "Sign out" in the side menu | Django `/sign-out/` | Nothing; returns to `/sign-in/` | `POST /staff/v1/sign-out/`. Ends the session and writes `admin_sign_out` |
| `/inbox/…`, `/contacts/…` | Nothing; they are new | Sections 13, 18 and 19 | Section 10 |
| "Page not found" | The plain error page | The same words | Nothing |

- **Which page a person lands on.** `GET /staff/v1/me/` answers 401 with `unauthenticated` (go to `/sign-in/`) or `second_step_required` (go to `/verify/`). The page decides nothing about access: it only follows the answer.
- **The route names are written here for the plan to confirm.** Each is a thin wrapper around a function that exists: `hq_auth.within_limits` and `issue_email_code`, `hq_auth.check_email_code`, `two_step.verify_second_step`, `hq_auth.mark_verified`, `hq_auth.sign_out`, `audit.recent_sign_in_events`. No model, limit or audit action changes, so the way chosen (R26) needs no migration.
- **The address typed on `/sign-in/`** is shown back on `/code/` from the page's memory only. It is not put in an address, a cookie the page can read, or browser storage. After a reload the page says "Code sent to your email".
- **The side menu** (signed-in pages): Inbox, Contacts, a separator, Admin console. The phone's bottom bar: Inbox, Contacts, Console. No item leads to the Django Admin (R27).
- **What the pages no longer have:** Django's form token (the origin check replaces it, 20.5), and working without script (section 13).

### 22.4 The Django Admin is not shown, and is switched off (R27, R28)

Ruled in two steps, in his words. R27: "just drop this django admin... dont show this.. you are confused because of this", said after "all of our domain will be on Vercel. The only domain in railway is the API". R28, on whether it is switched off or only hidden: "Switch it off". Together they replace R25 ("Yes, Admin stays").

In the staff area (R27):

- The console has no "Admin lists" card. The side menu and the phone's bottom bar have no "Admin lists" or "Lists" item. No page of the staff area links to, frames or names the Django Admin.
- No staff route under `/staff/v1/` serves anything of the Admin.

On the backend (R28). **Once the HQ move is complete, the backend serves no Django Admin.** What is gone, as the last step of 22.6:

- The Admin's pages at `/admin/…` on the HQ host. They are mounted nowhere else today, so after this they answer "not found" on every host.
- The Admin's own CSS and script at `/static/admin/…`.
- The redirect from the Admin's login to the sign-in page, and its "Admin console" and "Sign out" links, which exist only inside the Admin.

After that the backend serves no pages of ours to staff: only the staff routes the Vercel pages call, and the plain error pages. R26's sign-in opens the staff area and nothing else.

**What is lost, plainly.** He was told the first two before ruling and accepted them. The list is from the code's `admin.py` files, read for what each registers and not run:

- **No screen for the lists.** Subscriber accounts, their sessions, policy acceptances, account audit events, linked sign-ins, mobile numbers and mobile codes; sites, web addresses, business profiles and onboarding; sent emails; SMS send records and the SMS phones; catalog products and their revisions; the Santé site settings.
- **No screen for managing staff users.** The Django user list is where a staff user is added, changed or disabled in a browser today.
- **No screen for the three things the Admin changes today,** besides staff users: the SMS daily cap (`SmsPolicy`, and with it the 300 of section 18.10); the Santé catalog owner; and a Subscriber account's record, whose list is the one account list that is not read-only.
- **Terminal commands are what remains.** The repository has commands for admin identities (`admin_identity`), SMS phones (`sms_device`), billing, account export and disposal, and retention. **No command was found for the SMS daily cap, the Santé catalog owner, changing an account's record, or listing accounts, sites and sent emails.** Until one exists, or a Vercel page does, those can be read or changed only by a person with database or shell access. The plan that switches the Admin off must say, for each, what replaces it or that nothing does.
- **Runbook checks that name the Admin stop working** and are rewritten by that plan, not by this design:
  - `docs/runbooks/sms-gateway.md`, twice: "the admin's SMS phones list shows the phone". The check becomes `sms_device list` only.
  - `docs/runbooks/admin-two-step.md`: "Open admin lists" opens the lists, in the release check; and the line that `HQ_HOST` is the one host that serves hq and the admin lists.
  - `docs/runbooks/retention.md`: the row for Django's session rows, described as admin list notices.

What this design does not do: it designs no Vercel page for any of these lists or settings. Whether and when such pages replace them is his (22.7).

Switching the Admin off on any environment changes access control. It is a build step with its own approved plan, reviewed by the NPC coordinator before merge (22.5). Nothing is changed on staging or production by this document.

### 22.5 The standing rulings of the admin sign-in, and how each is kept

These are rulings of z8uq9mdwdx (NPC Build 6) and z8uq9mdxvt, relayed by the NPC coordinator as not lifted. Staging facts relayed with them, not checked by this session: `HQ_HOST` is `hq-staging.negosyonline.com`, `ADMIN_SECRETS_KEY` is set, migration `operators.0002` is applied.

| Standing ruling | How the staff area on Vercel keeps it | What cannot stay as it is |
|---|---|---|
| **The staff two-step check** | Kept whole. The same functions check the emailed code and the authenticator or recovery code, with the same limits, the same pause and the same 12-hour session, on the backend. The Vercel pages hold no secret and check nothing. `ADMIN_SECRETS_KEY` stays a Railway variable only. A session that passed only the first step reaches nothing | Where the codes are typed: a page served by Vercel instead of one served by the backend. Its cost is 20.6 |
| **The operator audit record** | Kept whole. `admin_sign_in_ok`, `admin_sign_in_failed`, `admin_locked` and `admin_sign_out` are written by the same backend functions in the same transaction as the check, with the surface recorded as `hq` as today. The browser never writes or sends an audit entry, so a changed page cannot sign in without one. The inbox, bulk and contact entries are section 8, 18.7 and 19.8. No new table: `operators.0002` already holds what is needed | Nothing |
| **hq is the only admin door** | Kept in what it protects. The API host still has no staff sign-in. A staff session is still made, checked and ended only by the backend, on one address that answers nothing else. The start-up check that keeps that address apart from the dashboard, the public site and the API origins stays, and gains the same rule for the staff origin. The Django Admin, which that ruling put behind hq, is switched off altogether (R28, 22.4), so no admin is served on any host | **The door is no longer one host.** Today the HQ host serves the forms and does the checking. After the move the staff app on Vercel serves the forms, and a backend address answers that one origin. The coordinator's reading of R27, which Coach Ed may overrule: `hq` is the staff app on Vercel, the address staff type; the Railway address the staff pages call is behind the scenes, under a new name staff never type. The proposed name and what it changes are in 22.7. This design does not decide it |

What a future plan must carry, from the NPC coordinator:

- Removing or replacing the Django HQ pages changes access control. It needs a plan approved by Coach Ed; this design is not that plan.
- Any such plan is reviewed by the NPC coordinator (owner of z8uq9mdwdx) before it merges.
- Any edit to `config/settings.py` is announced to the NPC coordinator before a merge. Its draft PR 78 touches that file.

### 22.6 Retiring the Django HQ pages: the order, so that no one is locked out

A sequence only. Each step is for an approved plan to detail, on staging first.

1. **The backend gains the staff routes, switched off.** The sign-in routes and the console route are added to the HQ host. While no staff origin is set they answer no browser. The Django pages are untouched and remain the way in.
2. **The staff app is deployed** to its own host, and the staff origin is set on that environment's backend. From here **both ways in work, and both make the same session**: a person can sign in on the Django pages or on the Vercel pages.
3. **Every staff member signs in through the Vercel pages** at least once: the emailed code, the authenticator code, one recovery code, and one deliberate pause after five wrong codes. The audit record is read to confirm each entry was written. Coach Ed accepts the pages.
4. **The links are turned.** The Django `/` sends the browser to the staff host. The Django sign-in pages still answer if their address is typed. The Admin's own login and its "Admin console" link are left as they are until step 6 removes them with the Admin.
5. **The Django pages are removed** in a later release, after the Vercel pages have been the way in for a period Coach Ed names: the five templates, the five views and their tests. The plain error pages and the staff routes stay. This step does not touch the Django Admin.
6. **The Django Admin is switched off (R28), last.** Its routes at `/admin/…`, its CSS and script at `/static/admin/…`, and the redirect from its login to the sign-in page are removed from the backend. From this step the backend serves no Django Admin on any host, and what 22.4 lists as lost is lost: the lists, staff user management and the three things it changes have no screen; the runbook's "admin's SMS phones list" check becomes `sms_device list` only. Before this step the plan must have settled, for each item of 22.4, what replaces it or that nothing does, and the runbooks must already say so. It is last so that every earlier step can still be checked in the Admin, and so that it is not switched off while it is the only way to see something a step depends on.

**If the names change as proposed in 22.7**, the order above gains a step the plan must set out: `HQ_HOST` names one host today (22.1), so the day the name `hq` is pointed at Vercel is the day the Django pages stop answering at that name. They answer at the backend's new name for as long as they exist. Whether the backend can answer both names for a time is for the plan; it was not looked into here.

At every step up to 5, going back is unsetting the staff origin: the Django pages are still there. After 5, and again after 6, going back is redeploying the previous release. Throughout, the terminal command (`manage.py admin_identity reset`, `enrol`) is unchanged and is the way to recover a person who cannot sign in.

### 22.7 What is settled, and what Coach Ed is still asked

Settled:

- **How HQ staff sign in on Vercel: R26** (section 20), kept for the staff area. The forms on Vercel, the backend checking the codes as today, today's cookie, no token held by the page, no migration, with the cost of 20.6 accepted.
- **The Django Admin is not shown in the staff area: R27** (22.4). No card, no menu item, no link.
- **The Django Admin is switched off on the backend: R28** (22.4, and step 6 of 22.6). What is lost with it is listed in 22.4.

Still his, one at a time, in this order. None is made by this design.

1. **The host names.** The coordinator's reading of R27, his to overrule: `hq` is the staff app on Vercel, and the Railway address the staff pages call is behind the scenes with a new name. **Proposal, not a decision:**
   - The staff app on Vercel takes the name the HQ host has today. On staging that is `hq-staging.negosyonline.com` (relayed, 22.5), so staff keep the address they know.
   - The backend address the staff pages call is named `hq-api`, written the way the other hosts of that environment are: `hq-api-staging.negosyonline.com` on staging. Staff never type it. It must stay under the same registered domain as the staff app, so the cookie stays `SameSite=Strict` (20.5).
   - What that changes at build time, each needing his approval and none approved here: the Railway variable `HQ_HOST` on that environment takes the new name; the new variable `HQ_STAFF_ORIGIN` is set to the staff app's address; the new name is added to the Railway service and to DNS; the name `hq` is pointed at the Vercel project. The new name must pass the start-up check of `HQ_HOST` (`apps/operators/checks.py`); whether `hq-api-staging` passes it was not run.
   - No production name is proposed beyond the same pattern: production is not built yet.
   - The staff routes stay on a second Railway address, apart from the Subscriber API host. His words were "The only domain in railway is the API"; the coordinator decided this as a routine detail under the standing rule that staff sign-in stays off the API host (22.5). It is the coordinator's decision, recorded as such, and his to overrule.
2. **Whether and when Vercel pages replace the lists.** With the Admin switched off there is no screen for the Subscriber, site and sent-email lists, for staff users, or for the things named in 22.4 that the Admin changes. Nothing is designed for them here. If he wants such pages, that is new scope with its own ruling, design and plan.

**Approved by Coach Ed on 2026-11-11.** The look: "approved the look" (section 13, with what it does not cover). The design: "approved the design", given on PR 75 at head `2d021f3`, after a summary put to him of the staff area on Vercel as its own app, sign-in per R26, the inbox, Bulk SMS and Contacts per R1 to R22, and the Django Admin removed and switched off last per R27 and R28. The approval allows build plans to be written, each returning to him for approval before any code. It approves no build, no migration, no Railway or Vercel change, and no storing of incoming texts, which stays gated on the NPC and privacy wording (R1).

The two points above stay open after the approval: the exact host names, settled at build time, and whether Vercel pages replace the lists. Not verified in the design, and said to him: how the sign-in cookie behaves between the Vercel address and the Railway address (section 17).
