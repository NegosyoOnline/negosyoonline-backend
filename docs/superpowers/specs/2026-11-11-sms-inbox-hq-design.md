# SMS inbox, reply and compose for HQ staff: design (2026-11-11)

ClickUp task: z8uq9me128 "SMS inbox, reply and compose (HQ)" (https://app.clickup.com/t/z8uq9me128), a subtask of z8uq9md33v "NPC Build 1 — SMS Carrier & Data-Flow Controls".

Status: **design for Coach Ed's approval. Nothing is built.** This document creates no code, migration, setting, variable or switch. One decision is open and is his: where the screens are served (section 3). The mockup of the three screens is in `docs/superpowers/specs/mockups/2026-11-11-sms-inbox/` (section 13); visual acceptance is his. After approval comes a plan, also for approval, before any code.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9me128: description and its seven comments, read 2026-11-11 |
| Parent | ClickUp z8uq9md33v: description and its ten comments, read 2026-11-11 |
| Gateway design | `docs/superpowers/specs/2026-11-10-sms-gateway-mobile-verification-design.md`, sections 5.6, 6.2, 6.3, 6.7 and 14 |
| Runbook | `docs/runbooks/sms-gateway.md`, `docs/runbooks/retention.md` |
| Code | `apps/notifications`, `apps/operators`, `apps/common`, `apps/accounts/retention.py`, `config/settings.py` at `1ba9cc6` |
| Dashboard look | Frontend repository at `36d2ec4`, read only: `apps/dash/public/index.html`, `apps/dash/public/assets/css/dash.css`, `apps/dash/next.config.ts` |
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

The coordinator's readings recorded with those rulings, which this design confirms: R0 means records owned per Site, Subscriber or HQ, one access check, and a reusable API, with no add-on screen, billing or Subscriber access now. R6 is about how the screens look; it does not move the tool to the Subscriber dashboard or give a Subscriber access.

Not approved by any of them, and not done here: any build, any migration, any setting or Railway variable, turning the incoming switch on anywhere, anything on staging or production, and restyling the existing HQ pages.

## 3. The open decision for Coach Ed: where the three screens are served

R3 keeps the tool behind the HQ two-step sign-in on the HQ host. R6 asks for the Subscriber dashboard's look, and that dashboard lives in the frontend repository on another host. The two have to meet somewhere.

| Option | What it is | Security consequence | Cost |
|---|---|---|---|
| **A. Django pages on the HQ host, styled to match the dashboard (recommended)** | Three server-rendered pages under `/inbox/` in the HQ URL set (`apps/operators/hq_urls.py`), with their own base template that carries the dashboard's colours, type, cards, chips, buttons, dark sidebar and phone tab bar. The existing HQ pages are not touched | Nothing new to trust. The pages, the numbers and the message text are served only by the HQ host, only to a session that passed both steps. The session cookie stays as it is: host-only, `HttpOnly`, `SameSite=Strict`. No other origin is allowed to call HQ, so no CORS rule is added. Django's CSRF check covers the reply and compose forms. The pages load nothing from another host: the font is served by HQ itself | The look is a second copy of the dashboard's styles, kept in the backend. When the dashboard's design changes, this copy is updated by hand |
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

One row per owner, line and other party.

| Field | Meaning |
|---|---|
| `public_id` | Opaque id (`conv_…`), the only id a screen or the API shows |
| `owner_kind` | `hq`, `site` or `account` |
| `site`, `account` | The owning Site or Subscriber. Both empty for `hq`. A database constraint allows exactly the combination `owner_kind` names |
| `channel` | `sms`. Other channels can be added as new values later |
| `line` | The phone (`SmsDevice`) whose SIM the conversation is on |
| `contact` | The other party. A Philippine mobile is stored as `+639XXXXXXXXX`. Anything else (a carrier's short code, a sender name, a foreign number) is stored as received, at most 32 characters |
| `can_reply` | True only when `contact` is a Philippine mobile |
| `unread_count` | Incoming texts since the conversation was last opened by anyone with access |
| `last_message_at`, `created_at` | For ordering the inbox |

One conversation per owner, line and contact.

### 4.3 `ConversationMessage`

One row per text, with its text. This is the inbox record (direction `in`) and the staff-text record (direction `out`).

| Field | Meaning |
|---|---|
| `public_id` | Opaque id (`msg_…`) |
| `conversation` | The conversation, and through it the owner |
| `direction` | `in` or `out` |
| `body` | The text. The only column in the database that holds message text. At most 2,000 characters; a longer incoming text is cut and `clipped` is set |
| `gateway_ref` | `in`: the id the app gave the text. Unique per phone, which is what makes a repeated `received` call harmless. `out`: the `public_id` of the `SmsMessage` send record, kept as text with no foreign key, the way a mobile challenge refers to its send record |
| `device`, `sim_slot` | `in`: the phone and SIM slot it arrived on |
| `author` | `out`: the staff user who wrote it. Empty for `in` |
| `kind` | `out`: `reply` or `compose` |
| `occurred_at` | `in`: when it arrived, by the phone's clock (the server's time if the phone's value cannot be read) |
| `created_at` | When the server stored it. Retention counts from here |

An outgoing message has no status column. Its status is read from its send record when it is shown, so the gateway's `report` and `sweep` code does not change and two copies of a status cannot disagree.

For every staff text there are therefore two rows: the `ConversationMessage` (with the text, owned through its conversation) and the `SmsMessage` send record (number, purpose, status, times, no text), as for every other text the gateway sends.

### 4.4 `InboxAccess`

One row per staff user who may use the inbox (section 7).

| Field | Meaning |
|---|---|
| `operator` | The staff user (one row each) |
| `can_send` | May reply and compose. Without it the user can only read |
| `granted_by`, `granted_at` | Who ran the command, and when |

## 5. Storing an incoming text

### 5.1 The switches

An incoming text is stored only when all three are on. Each is off today.

| Switch | Where | Who turns it on |
|---|---|---|
| `SMS_GATEWAY_ENABLED` | Railway variable, exists | Already on for staging. Unchanged |
| `SMS_INBOX_ENABLED` | Railway variable, **new** | Coach Ed's approval, per environment. While off: the inbox tables are never read or written, the three pages answer the HQ "Page not found", `received` discards as it does today, and the retention rule does nothing. The code can be merged and deployed dark, the way the gateway was |
| "Forwards incoming texts" on the phone's row | `manage.py sms_device incoming on --name system --actor USERNAME`, exists, audited | Coach Ed's approval, each time, and **only after the NPC and privacy wording is amended** (R1) |

The `sms_device incoming on` command is changed to refuse while `SMS_INBOX_ENABLED` is off, so the phone is never told to forward texts that would be thrown away, and its closing line no longer says nothing is stored.

### 5.2 What `received` does

The call keeps its shape (`apps/notifications/gateway.py`): up to 20 texts, each with `id`, `from`, `text`, `received_at`, `sim_slot`; the answer is 204. The phone app does not change.

| Case | The server |
|---|---|
| `SMS_INBOX_ENABLED` off, or this phone's switch off | Answers 204 and discards the body unread, as today |
| Both on, the body is a valid list | Stores every text in one database transaction, then answers 204. A text whose `id` this phone already sent is skipped, so a repeat changes nothing |
| Both on, the body is not a valid list (not JSON, more than 20 items, an item without a text `id` and `from`) | Stores nothing and answers 400. The phone keeps the texts and tries again; it drops them after 7 days. One ERROR log line, `sms_incoming_rejected`, with the count only |

Storing one text: find or create the conversation for this owner, line and sender; write the `in` message; add one to `unread_count`; set `last_message_at`. The storing step is one function in `apps/inbox`, which replaces the body of `store_incoming`. Nothing else in the three gateway calls changes.

- The sender is normalised with `normalise_ph_mobile`. If it is not a Philippine mobile it is kept as received and the conversation cannot be replied to.
- No log line, cache entry or audit entry ever holds a number or a text.
- **Every text the SIM receives is stored**, including texts that have nothing to do with NegosyoOnline: the carrier's notices, wrong numbers, and one-time codes other services send to that SIM. There is no filter, because any filter would have to read the text to decide. The NPC wording must say so (section 14).
- The phone keeps a text it could not hand over, sealed, for up to 7 days and up to 500 texts, including while its own gateway switch is off (Parent, decision record 4). Those texts arrive later with their original arrival time.
- The phone's own Messages app keeps its copy of every arriving text, as on any phone. The runbook's auto-delete control covers it.

## 6. Replying and composing (R4, R5)

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
- **One text, one number.** The compose form takes one number. There is no list, no paste of many numbers, no upload and no "send to all". This, the audit record and a limit of 60 staff texts per staff user per hour are what stand between "any Philippine mobile" and bulk sending. Marketing stays forbidden; the compose page says so. Code cannot recognise a marketing text, so the audit record is the control.

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

- **Its name:** "SMS inbox". **Two levels:** read, and read with send (`can_send`). A staff user who only needs to look something up is given read.
- **It is explicit.** It is a row in `InboxAccess`, not a Django permission. A superuser has no access without a row, and the Django admin's user page cannot grant it.
- **Granting is a terminal command**, as admin identities and phones are managed: `manage.py sms_inbox_access grant --user USERNAME [--send] --actor USERNAME`, `revoke`, and `list`. `grant` and `revoke` write an audit entry. Who is granted access is Coach Ed's decision, per person.
- **Sign-in:** the existing HQ two-step sign-in. The pages are in the HQ URL set, so they see only the user of an HQ session that passed both steps.
- A user without access, or any visitor while `SMS_INBOX_ENABLED` is off, gets the same HQ "Page not found" as for a path that does not exist.

### 7.2 The one access check

One function in `apps/inbox`, `scope_for(actor)`, answers who is asking and what they may reach:

| Actor | Scope | Built now |
|---|---|---|
| A staff user with an `InboxAccess` row | Conversations owned by `hq`. Send only with `can_send` | Yes |
| A staff user without a row | None | Yes |
| A Subscriber account (later) | Conversations owned by that Subscriber's Site, when the add-on is active for it | No. The add-on adds this branch |

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

- The operator is the signed-in staff user. The target is the conversation's `public_id`.
- The three inbox actions are declared `PERSONAL`, because a masked number is still part of a person's number. The existing rule `admin_audit_personal_values` blanks `personal` one year after the entry is written, which matches R2. That rule runs only where `RETENTION_ENABLED` is on and, until slice 4, only by command (`docs/runbooks/retention.md`).
- `sms_inbox_access` is declared `KEPT`.
- Opening the inbox list writes no entry, because the list shows no message text (section 9).

## 9. Masking

- **The inbox list** shows, for each conversation: the masked number (`+63917***4567`, through `mask_mobile`), the time of the last text, whether the last text came in or went out, and an unread count. **It shows no message text**, not even a preview, so that reading a text always means opening a conversation, which is audited.
- **The conversation page** shows the masked number and the texts. The full number is not shown anywhere in the tool.
- **Search** matches a full number typed by the staff member, never a fragment, as the admin's SMS list does.
- **The compose form** is the one place a full number appears: the staff member types it. After sending, the conversation shows it masked.
- A sender that is not a mobile number (a short code or a sender name) is shown as received: it is not a person's number.
- The Django admin gets no list of conversations or messages. The existing `SmsMessage` admin list shows staff texts' send records like any other, masked, with no text.

## 10. The API shape the Subscriber dashboard can reuse

The pages are thin. They call four service functions in `apps/inbox`, each taking a scope (section 7.2) and returning plain values. The HQ pages render those values in templates. The Subscriber dashboard can later be given the same four functions as JSON under `/v1/inbox/`, behind the account session and the same scope check.

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
| `inbox_messages` (new) | Delete `ConversationMessage` rows, incoming and outgoing, then delete any conversation left with no message | 365 days after the message's `created_at` |
| `sms_messages` (exists) | Delete `SmsMessage` send records, staff texts included | 365 days after `created_at` |
| `admin_audit_personal_values` (exists) | Blank the masked number in the inbox audit entries | 1 year after the entry |

- `inbox_messages` is registered in the shared retention registry and added to the daily rules the phone's `poll` starts, beside `sms_messages`. It is not behind `RETENTION_ENABLED`; while `SMS_INBOX_ENABLED` is off it touches no table.
- Rows are deleted, not blanked.
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

Three pages, at these paths under option A: `/inbox/` (the list), `/inbox/c/<id>/` (a conversation, with the reply form), `/inbox/new/` (compose). The HQ home page gains one link to the inbox, shown only to a user with access; nothing else on the existing HQ pages changes.

They follow the Subscriber dashboard: the Manrope typeface, the blue `#173DB8` primary, the dark navy sidebar on desktop, the top bar and bottom tab bar on phones, the eyebrow and heading at the top of each view, cards with a 10-pixel radius, pill chips, 44-pixel buttons, and the dashboard's dark theme with its light theme when the device prefers light.

**Mockup:** `docs/superpowers/specs/mockups/2026-11-11-sms-inbox/`: `index.html` (inbox list), `conversation.html`, `compose.html`, one shared `mockup.css`. Static files, made-up data only: no real number and no real text. To open it:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory docs/superpowers/specs/mockups/2026-11-11-sms-inbox
```

Then `http://127.0.0.1:8765/` (http, port 8765, local only; nothing is deployed). The mockup loads the Manrope typeface from Google Fonts; the built pages would serve the font from HQ itself (section 3, option A). The "Theme" button switches between the dark and light themes.

Decisions the mockup shows, for Coach Ed's eye:

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
11. **The documents the Parent names:** register row 6; filing-store-sms; filing-subscriber-accounts-billing; filing-subscriber-stores; PIA 5; privacy manual s8; the Terms and Privacy provider list. This session did not read them.

## 15. Tests the build would write

- **Storing.** With either switch off, `received` answers 204 and writes nothing, and reads no inbox table. With both on: a valid list is stored; a repeat of the same ids stores nothing new; a bad list stores nothing and answers 400; a sender that is not a Philippine mobile is kept as received and cannot be replied to; a text over 2,000 characters is cut and marked; the owner is `hq` whatever the body says; no number or text appears in a log line.
- **Sending.** A reply and a composed text each make one send record with no text, one `out` message with the text, and one audit entry. A link, a 161st character and a non-SMS character are refused before anything is queued. A refused send (limit, kill switch, phone offline, gateway off) stores no message and shows its reason. A staff text takes one slot of the platform count.
- **Order and interval.** With a code and a staff text waiting, the code is handed out first. A staff text carries a gap of 18 to 25 seconds; a code carries 0. Only one paced text is with the phone at a time.
- **The number.** Every form `normalise_ph_mobile` accepts is accepted; a landline, a foreign number and a short number are refused. Composing to a number with a conversation adds to it.
- **Access.** A verified session without an `InboxAccess` row, a superuser without a row, a session that passed only the first step, and no session at all each get the HQ "Page not found" on all three pages. A read-only user sees no form and a posted reply is refused. A conversation owned by a Site or an account (made in the test) cannot be listed, opened or replied to by HQ staff. Every page answers "Page not found" while `SMS_INBOX_ENABLED` is off.
- **Audit.** Opening, replying and composing each write the declared entry; no entry contains the text or a full number; an undeclared action is refused.
- **Masking.** The list page's HTML contains no message text and no full number. The conversation page contains no full number.
- **Retention.** A message older than 365 days is deleted; a younger one stays; a conversation left empty is deleted; the dry run changes nothing; the rule does nothing while the switch is off.
- **The command.** `grant`, `revoke` and `list`; `sms_device incoming on` refuses while `SMS_INBOX_ENABLED` is off.
- **Forms.** The CSRF check; a refresh after a sent text does not send it again (the answer to a sent form is a redirect).
- Checks: `manage.py makemigrations --check`, `manage.py check`, `manage.py check --deploy`, the full test run.

## 16. Out of scope

- Any add-on screen, billing, SIM custody, dedicated line, broadcast or marketing text, and any Subscriber access. The `site` and `account` owners are fields and one branch point, nothing more.
- Mounting the JSON routes of section 10.
- Restyling the existing HQ pages (sign-in, code, verify, home), beyond the one link on the home page.
- Linking a sender's number to a Subscriber account, or showing account details beside a conversation.
- Showing a full number; deleting a conversation or a message by hand; exporting conversations.
- Fixed templates, saved replies, attachments, MMS, texts longer than one SMS, sending to more than one number.
- Delivery receipts; notifications to staff when a text arrives; automatic refresh.
- A second phone or line; order-status texts.
- The privacy page and the NPC documents themselves.
- Anything on staging or production.

## 17. What was verified, and what was not

Verified by reading, on 2026-11-11: the Task and Parent with every comment; the code named in section 1 at `1ba9cc6`; the dashboard's styles at frontend `36d2ec4`. The mockup's three pages were opened in headless Chrome over `http://127.0.0.1:8765/` at 1280 pixels wide (desktop) and 390 pixels wide (phone), in the dark theme, and the screenshots were read: no page scrolls sideways and nothing overlaps.

Not verified:

- The mockup's light theme, and any browser other than Chrome.

- Nothing was run against the backend. This stage wrote documents and a static mockup only.
- The phone app's behaviour with the switch on, on a real phone with a real SIM: the permission prompt, texts in several parts, two SIMs.
- That 18 to 25 seconds between staff texts is what Coach Ed means by "the sending interval" for replies (section 6.3).
- The NPC documents and the privacy page were not read by this session.
- Whether the HQ host sends a Content-Security-Policy header; none was found in `config/settings.py`. The built pages would carry their styles and the font from HQ itself in either case.
