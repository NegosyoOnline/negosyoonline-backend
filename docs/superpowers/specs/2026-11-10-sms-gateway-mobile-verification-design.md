# SMS gateway and mobile verification design (2026-11-10)

ClickUp task: z8uq9md33v "NPC Build 1 — SMS Carrier & Data-Flow Controls" (https://app.clickup.com/t/z8uq9md33v), parent z8uq9mbreu. This is slice 8 "Mobile OTP" of `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md`.

Status: **design proposed, partly ruled. Nothing is built.** No code, migration, variable or Railway change exists. Section 2.1 records the rulings given so far; section 3 lists the decisions still open. Until Coach Ed rules on those, the rest of this document assumes the recommended answer. The plan is `docs/superpowers/plans/2026-11-10-backend-npc-build1-sms-gateway.md`.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9md33v, description (no comments, no attachments as of 2026-11-10) |
| Parent | ClickUp z8uq9mbreu: the wording rules (admin, subscribers, the subscriber's customers), the retention summary ("SMS records 1 year") and the list of public web apps ("No mobile app") |
| Build list | Legacy repository, read only: `.claude/docs/superpowers/specs/2026-11-07-negosyoonline-npc-launch-build-list.md`, items A1 to A4 |
| Scope doc | `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md` at `e85e727` |
| Code | `apps/notifications`, `apps/accounts`, `apps/common`, `apps/operators`, `config/` at `e85e727` |
| Dashboard | Frontend repository at `ab852d3`, read only: `apps/dash/public/assets/js/account.js`, `earnings.js`, `app.js` |
| Legacy behaviour | Legacy repository, read only: `app/sms_gateway/`, `app/negosyoonline_platform/sms.py`, `sms_transport.py`, `mobile.py`, and `2026-09-04-native-sms-gateway-design.md`. Read to understand behaviour. No code is copied |
| Third parties | Each project's own documentation, fetched on 2026-11-10 (section 12) |

## 2. Rulings this design follows (not reopened)

- NegosyoOnline builds its own gateway: its own phone and SIM, no send endpoint in the legacy system, no legacy code copied (Coach Ed, 2026-11-10, in the Task).
- The phone has a SIM on an ordinary Globe mobile plan. Only sign-in codes and order-status texts are sent. Only the recipient number and the message text leave. No marketing. Globe has no access to our database.
- The text of a message is never stored in the database. Send records are kept 1 year, then deleted.
- The paid Dedicated SMS Line add-on (z8uq9mb19f) is not designed here. Section 6.6 shows it is not blocked.
- Each migration and each Railway variable needs Coach Ed's own approval. This document lists them; it creates none.

### 2.1 Rulings on this design

- **2026-11-10, Coach Ed, in the coordinator session:** "let's build our own small app, but include reading sms since in our CMS inbox, we will include SMS."
  - D1 is (a): our own small Android app. He chose it after seeing the legacy handset app compared with the other options; that app is not reused.
  - D2 is (a): the phone asks the server. No Firebase. The question put to him was "our own small Android app that polls the API".
  - D12: a new repository is needed. Creating it remains Coach Ed's own action or approval.
  - New requirement: the app must be able to read incoming texts, because the SMS inbox planned in the Dedicated SMS Line add-on (z8uq9mb19f) will show them. How this is written here (section 5.6) was the coordinator's interpretation, confirmed by Coach Ed on 2026-11-11 (D13, below).
- **2026-11-11, Coach Ed, in the coordinator session:** "yes, agree with your recommendation", answering the coordinator's recommendation: reading built into the app behind a per-phone server switch, off by default, kept off for the system phone until the inbox is designed in the add-on task (z8uq9mb19f).
  - D13 is (a).
- **2026-11-10, the coordinator, as reversible details inside the approved scope:** D3 is (a), the API host under `/gateway/`; D9, the `SmsMessage` field names as proposed; D11, two accounts may verify the same number.

## 3. Decisions that need Coach Ed

Rows marked **Settled** are ruled (section 2.1) and are kept for the record. Every other row **needs Coach Ed**. The detail and the evidence are in the section named.

| # | Decision | Options | Recommended | Consequence of the recommendation |
|---|---|---|---|---|
| D1 | Which phone app (section 4) — **Settled**: (a), Coach Ed, 2026-11-10 | (a) a small Android app of our own; (b) the open-source "SMS Gateway for Android" with its private server; (c) the same app in local-server mode; (d) httpSMS or textbee | (a) | No third party sees anything. No Firebase. No new Railway service. We own and maintain a small Android app in a new repository, and the phone installs it from a file, not from Google Play |
| D2 | How the phone learns a text is waiting (section 5.3) — **Settled**: (a), Coach Ed, 2026-11-10 | (a) the phone asks every 5 seconds; (b) a push nudge through Firebase | (a) | A code leaves the phone within about 5 seconds of being queued. No Firebase project, no Google in the path. About 17,000 small requests a day to the API |
| D3 | Which hostname the phone uses (section 5.2) — **Settled**: (a), the coordinator, 2026-11-10 | (a) the API host, under `/gateway/`; (b) a dedicated host such as `sms.negosyoonline.com` | (a) | No new domain, DNS record or Railway custom domain. The phone's address is not in the published API description |
| D4 | Where the text waits for the phone (section 6.3) | (a) the cache, encrypted, for at most 10 minutes; (b) written only at pickup, from the record | (a) | The text exists only in Redis, as ciphertext, until the phone takes it or it expires. It is never in PostgreSQL |
| D5 | What starts the retention job before slice 4 adds beat (section 9.3) | (a) the web service, once a day; (b) a Railway cron service; (c) by hand | (a) | No new Railway service. If the web service gets no gateway traffic for a day, that day's run is skipped and the next run catches up. Build 7 can reuse it or choose (b) |
| D6 | The daily limits and where they are kept (section 8.1) | Numbers: carried from legacy, 500 a day for the platform and 100 a day per site, or Coach Ed's own. Kept in: (a) one settings row the admin edits in the Django admin; (b) Railway variables | 500 and 100, in (a) | The admin can change a limit or stop all sending in seconds, without a deploy. Each change is audited. One more small table |
| D7 | The notice when a subscriber changes mobile (section 7.5) | (a) email only; (b) also a text to the old number | (a) | Stays inside the ruling "only sign-in codes and order-status texts". The dashboard sentence "we send a notice to your old number and your email" must change, in a frontend task |
| D8 | `GET /v1/account/mobile/` (section 7.4) | (a) serve it; (b) leave it out | Reported, not decided | `earnings.js` calls it; the Task does not list it. See 7.4 |
| D9 | `SmsMessage` field names (section 6.1) — **Settled**: as recommended, the coordinator, 2026-11-10 | The Task says number, purpose, status, time. The scope doc says recipient, category, status, provider reference | `recipient`, `category`, `purpose`, `status`, `created_at`; a link to the sending phone instead of a provider reference | Both documents are satisfied. The scope doc line is amended |
| D10 | The wording of the code text and the error messages (sections 7.2, 7.3) | Proposed text is in 7.6 | As proposed | The words subscribers read |
| D11 | May two accounts verify the same mobile number? (section 7.1) — **Settled**: (a), the coordinator, 2026-11-10 | (a) yes; (b) no | (a) | Matches legacy and the scope doc, which says "one each" per account only. A number is proof of reach, not an identity |
| D12 | The Android app's repository (section 4.3) — **Settled**: a new repository is needed (Coach Ed, 2026-11-10); creating it is still his action or approval | A new private repository, for example `negosyoonline-sms-gateway-android` | As stated | Needed only if D1 is (a). The repository does not exist; creating it is Coach Ed's action or approval |
| D13 | Reading incoming texts (section 5.6) — **Settled**: (a), Coach Ed, 2026-11-11 | (a) the app can receive texts, behind a per-phone switch that is off for the system phone until the add-on's inbox is designed; (b) reading is switched on for the system phone in this task | (a) | (a): this task stores no incoming text, and "no message text stored" and the NPC wording stand; the add-on designs where incoming text is kept, who sees it and for how long. (b): this task must also design inbound storage, retention and admin masking, and the filing wording must be checked before it is switched on |

What Coach Ed supplies in any case: the phone, the Globe SIM and its plan, a charger and a stable internet connection for the phone, and the approvals in the plan. With the recommended answers he supplies **no Firebase project and no new hostname**.

## 4. The phone app (D1)

### 4.1 What the phone app has to do

Ask the server for waiting texts, send each one through the SIM, and tell the server the result. By Coach Ed's ruling of 2026-11-10 it can also pass texts that arrive on the SIM to the server, behind a switch that is off unless the server turns it on for that phone (section 5.6). It reads nothing else: not the texts already stored on the phone, not the contact list.

### 4.2 Options

Facts about third-party apps come from their documentation on 2026-11-10 (section 12). "The NPC wording" means: only the recipient number and the message text leave, and only to Globe as the carrier.

**(a) A small Android app of our own.** The phone talks only to the gateway endpoints in `apps/notifications`, over HTTPS.

- Third parties that see the number or the text: none. Globe carries the text, as the NPC wording says.
- Other third parties in the path: none with polling (D2).
- Licence: ours. No third-party code beyond the Android platform and its standard libraries.
- Maintenance: ours. The app is small (one background service, one settings screen), but it is a second codebase with its own toolchain, and Android updates can change background rules.
- Coach Ed supplies: the phone and SIM, and installs the app from a file we build. An app whose main job is sending texts is installed from a file rather than through Google Play (from memory: Google Play restricts the SMS permission to default messaging apps; not re-verified today).
- Fit with the Task: A1 asks for "the gateway the phone talks to" in `apps/notifications`. This is the only option where the phone talks to that gateway directly.

**(b) "SMS Gateway for Android" (capcom6) with its private server.** Apache-2.0. The app is installed unchanged from the project's releases. Its private mode needs the project's own server, which we would run.

- What it needs: the project's Go server, its background worker and a MySQL or MariaDB database, behind HTTPS. On Railway that is **three new services** next to ours. Django would call that server's API; the phone would talk to that server, not to `apps/notifications`.
- Third parties in the path: in private mode the push nudge still goes from our private server to the developer's public server at `api.sms-gate.app`, then to Google's Firebase Cloud Messaging, then to the phone. The project's privacy policy says the nudge "Contains only FCM token" with "No message content/recipients". So neither sees a number or a text, but two outside services are in the path for every send. The documentation shows a second path (a held-open connection from the app to the private server) without describing it.
- Where the text is stored: the private server keeps messages in its own database. The documented example hashes message content on a weekly worker run and removes old records after 30 days. That conflicts with "the text is never stored in the database" unless the app's end-to-end encryption is switched on, in which case the database holds ciphertext.
- Maintenance: the app and server are maintained by the project (about 6,000 stars on GitHub; a third-party listing reports version 1.72.0 in August 2026, not confirmed from the releases page). We would maintain three more services and the upgrade path between app and server.
- Coach Ed supplies: the phone and SIM, approval of three Railway services and their cost, and a hostname for the private server (the app requires a valid HTTPS certificate and the path `/api/mobile/v1`).

**(c) The same app in local-server mode.** The phone runs a small web server on port 8080 with basic authentication, and our server would call the phone. The documentation says this is for a local network: the phone's public address works only if the mobile or home internet provider gives one, and "many ISPs use Carrier-Grade NAT". Reaching the phone from Railway would need a tunnel service, which adds a third party to the path of the number and text. Not recommended.

**(d) httpSMS or textbee.** Both are AGPL-3.0 (from each repository's README; some listings call textbee MIT, the repository says AGPL). Both need their own server stack (httpSMS: a Go API with CockroachDB or PostgreSQL and Redis; textbee: MongoDB and Redis), **both require our own Firebase project and a rebuilt Android app**, and both keep messages in their server's database. More services than (b), plus Firebase, plus a stricter licence. Not recommended.

**Not offered: our Django speaking another app's private device protocol.** Legacy did this with a commercial app. For the capcom6 app the device-side routes are not in the published documentation, so an app update could break sending without notice.

### 4.3 Recommendation (ruled: (a), Coach Ed, 2026-11-10)

**(a), a small app of our own**, in a new private repository (D12). It is the only option that keeps every third party except Globe out of the path, adds no Railway service, keeps the text out of every database, and matches A1. Its cost is real: a second small codebase. If Coach Ed does not want an Android codebase, (b) with end-to-end encryption on is the workable alternative; sections 5 and 6.3 would then be replaced by a client for that server, and the plan's PR 2 and PR 5 change.

The parent task's filing list says "No mobile app". That line is about apps the public uses. This app runs on one phone that NegosyoOnline owns and is not distributed. It is flagged here so the filing text can be checked against it; this design changes no filing text.

## 5. How the phone and the server talk

### 5.1 The calls

Two calls carry outgoing texts; a third, for incoming texts, is in section 5.6. All are `POST`, JSON, over HTTPS, and carry the phone's token. They are plain Django views in `apps/notifications`, mounted outside `/v1/` so they are not in the published API description.

| Call | The phone sends | The server answers |
|---|---|---|
| `POST /gateway/v1/poll` | App version, battery level, charging or not | Up to 5 waiting texts, each with an id, the number, the text and its expiry; how many seconds to wait before asking again; and whether this phone forwards incoming texts (section 5.6) |
| `POST /gateway/v1/report` | For each text: its id and `sent` or `failed` with a short reason | Nothing (204). Repeating a report changes nothing |

Rules:

- A text is handed out **once**. At handout the record moves from `queued` to `dispatched` and the text is removed from the cache. If the phone fails before sending, the text is not sent again; the subscriber asks for a new code. A sign-in code is never sent twice.
- The server never hands out a text after its expiry, and the phone never sends one after its expiry.
- The phone waits at least 2 seconds between two texts, so the SIM's traffic looks like a person's and not a broadcast.
- The "seconds to wait" in each answer lets the server slow the phone down (for example to 30 seconds while sending is stopped) without an app update.
- A `dispatched` record with no report after 10 minutes becomes `failed` with reason `no_report`.
- The app asks Android for permission to send texts, to receive texts as they arrive (section 5.6), to use the network, to run in the background and to start after a restart. It does not ask for permission to read the texts stored on the phone.

### 5.2 Hostname (D3; settled: (a), the coordinator, 2026-11-10)

**(a) The API host, under `/gateway/`** (staging: `https://api-staging.negosyonline.com/gateway/v1/...`; production: `https://api.negosyoonline.com/gateway/v1/...`). Recommended. Nothing new to register. The browser rules of the API do not apply: the phone sends no `Origin` header, and `ApiOriginMiddleware` acts only under `/v1/`.

**(b) A dedicated host**, for example `sms.negosyoonline.com`, routed by host name the way hq is. It separates the phone's traffic from the API's, at the cost of a DNS record, a Railway custom domain, a new variable, and one more public host to list in the filing. If chosen, the label must also be reserved: `apps/sites/subdomains.py` does not reserve `sms` or `gateway` today, so a subscriber could take it as a store name.

Whichever is chosen, the labels `sms` and `gateway` should be added to the reserved list, so no store can look like part of the gateway. That is a two-word change in `apps/sites`, which another session owns; the plan lists it for the coordinator.

### 5.3 How the phone learns a text is waiting (D2; ruled: (a), Coach Ed, 2026-11-10)

**(a) The phone asks every 5 seconds.** Recommended. Each call is a few hundred bytes and one or two cache reads on the server. At 5 seconds that is 17,280 calls a day. A code is on its way within about 5 seconds, which is well inside its 10-minute life. The phone stays on its charger, so battery is not a constraint.

**(b) A push nudge through Firebase Cloud Messaging.** The server tells Google to wake the app, and the app then calls `poll`. The nudge carries no number and no text. It needs: a Firebase project in NegosyoOnline's name (Coach Ed), Google's configuration file built into the app, a service-account secret as a Railway variable, and Google as one more outside service in the path of every send. Firebase's documentation also says high-priority nudges that do not show the user a notification can be given lower priority after 7 days of that pattern, which is exactly this app's pattern. It would still need (a) as the fallback.

Holding one request open until a text arrives (long polling) is not offered: `railway.toml` starts Gunicorn with its default single synchronous worker, and a held request would block every other request.

### 5.4 How the phone proves who it is

- Each phone is one row, `SmsDevice`, created only with a terminal command (`manage.py sms_device add --name ... --actor USERNAME`), the same way admin identities are. There is no browser path.
- The command prints a random token **once**. Only its SHA-256 is stored. Coach Ed types or scans it into the app, which keeps it in Android's encrypted storage.
- Every call sends the token in the `Authorization` header over TLS. The server looks the phone up by the token's hash. An unknown, rotated or disabled token gets the same 401 answer and counts against a per-IP limit of failed tries.
- `sms_device rotate` replaces the token; `sms_device disable` stops the phone at once; `sms_device incoming on|off` sets the switch in section 5.6; `sms_device list` shows each phone's name, state and last contact. Every action except `list` writes an `OperatorAuditEntry`.
- The token gives the right to fetch waiting texts and report results, nothing else. It cannot read records, accounts or settings.

### 5.5 When the phone is offline

- The server records the time of each `poll`. The gateway counts as **online** when a phone polled in the last 60 seconds.
- **Offline at the moment of sending:** nothing is queued. A mobile verification request answers at once with 503 and "Text messages are not available right now. Please try again later." A text that has an email fallback (order-status texts, later) goes by email instead. The record is written with status `refused` and reason `gateway_offline`.
- **The phone drops out after a text was queued:** the text waits until its expiry (10 minutes for a code). If the phone returns in time, it is sent. If not, the record becomes `expired`, the text is gone from the cache, and a text with an email fallback is emailed then.
- An ERROR log line with a fixed name (`sms_gateway_offline`) is written at most once an hour while sends are being refused, so the outage is visible in the Railway log. There is no alert table yet; the hq health page that would show this is not part of this task.

### 5.6 Incoming texts (D13; ruled: (a), Coach Ed, 2026-11-11)

Coach Ed's ruling of 2026-11-10 asks for the app to be able to read incoming texts, for the SMS inbox of the Dedicated SMS Line add-on (z8uq9mb19f). What follows is option (a) of D13, which he confirmed on 2026-11-11 ("yes, agree with your recommendation").

**What is built in this task**

- **A per-phone switch, on the server.** `SmsDevice` gains one field, "forwards incoming texts", off by default. Only the `sms_device` terminal command can change it, and the change is audited. Each `poll` answer tells the phone its current value.
- **The app's receiving part.** While the switch is off, the app's receiver is disabled: it forwards nothing and keeps nothing, and an arriving text stays only in the phone's own messaging app, as on any phone. While it is on, the app passes each arriving text to the server with the third call and keeps it only until the server has answered.
- **The third call, `POST /gateway/v1/received`.** The phone sends, for each text: the sender's number, the text, the time it arrived and the SIM slot. The server answers 204.
- **For this task the system phone's switch stays off, and the server stores no incoming text.** While a phone's switch is off, the call answers **204 and discards** what was sent, without reading it into any record or log. Proposed over answering 404: a phone that has not yet learned its switch was turned off would treat 404 as a fault and hold the text to try again, which is exactly the keeping this design rules out; a 204 lets it drop the text at once.

**What is not built in this task**

Where incoming text is stored, who may see it, how long it is kept, how numbers are masked, and what happens to texts that are not for a subscriber (the carrier's own notices, wrong numbers, one-time codes from other services sent to that SIM). Those belong to the add-on's design; its checklist already says the inbox needs its own storage design. This task's rule "no message text stored" and the NPC wording therefore stand unchanged.

**Consequences of the wider permission, stated plainly**

- The app now holds Android's permission to receive texts. It asks only for that one: it sees a text at the moment it arrives, and never the texts already stored on the phone.
- Android treats this as a sensitive permission. For an app installed from a file rather than from Google Play, recent Android versions can show the permission greyed out until the phone's owner allows restricted settings for that app by hand, and Google Play Protect can warn about or block the install. The documentation of the open-source app in section 4.2 reports both for Android 15; the exact steps on the chosen phone are to be proven when the phone is set up (section 12).
- A SIM receives texts that have nothing to do with NegosyoOnline. If forwarding is ever switched on for the **system** phone, every such text would reach the server. That is new personal data flowing in, which the NPC documents do not describe today: they say the phone sends, and that only the recipient number and the message text leave. **The filing and policy wording must be checked before the switch is turned on for the system number.**

**Option (b), reading switched on for the system phone in this task (not chosen).** This task would then also need: a table for incoming texts with their text, a retention period and job, masked admin lists, a rule for texts that are not for a subscriber, and a change to the rule "no message text stored" and to the NPC wording. It widens the task and was not recommended.

## 6. Sending (A1)

### 6.1 Records (D9; settled as proposed, the coordinator, 2026-11-10)

All in `apps/notifications`, in one additive migration.

**`SmsMessage`** — one row per text. No text column.

| Field | Meaning |
|---|---|
| `public_id` | Opaque id (`sms_…`), the one the phone sees |
| `recipient` | The number, `+639XXXXXXXXX`. The Task's "number" |
| `category` | `code` or `status`. Decides which limits and fallback apply. The scope doc's "category" |
| `purpose` | What the text is for, for example `mobile_verify`. The Task's "purpose" |
| `status` | `queued`, `dispatched`, `sent`, `failed`, `expired`, `refused` |
| `reason` | Why it was refused or failed: `site_limit`, `platform_limit`, `paused`, `gateway_off`, `gateway_offline`, `text_lost`, `no_report`, or the phone's own short reason |
| `emailed` | True when the email fallback went out instead |
| `site` | The site it belongs to, if any. Needed for the per-site limit. Null for account-level texts |
| `device` | The phone that took it. Replaces the scope doc's "provider reference": there is no provider, and the phone is the useful reference |
| `created_at`, `dispatched_at`, `sent_at`, `expires_at` | The Task's "time" |

Difference to report: the Task names the fields number, purpose, status, time; the scope doc, section 6, names them recipient, category, status, provider reference. Proposed resolution, above: keep `recipient` (the same name `EmailMessage` uses), keep both `category` and `purpose` because they do different jobs, and replace "provider reference" with the sending phone. The scope doc line is amended when this design is approved (section 13).

**`SmsDevice`** — one row per phone: name, token hash, active, forwards incoming texts (off by default; section 5.6), last contact, app version, battery, created and rotated times. It never holds the SIM's number.

**`SmsPolicy`** — one row: sending stopped (the kill switch), the platform daily limit, the per-site daily limit, updated time (section 8).

### 6.2 The one send function

`apps/notifications/services.py` gains `send_sms`, beside `send_email`. Every app that sends a text calls it; nothing else writes `SmsMessage` or touches the queue.

- It takes: the number, the purpose, the text, optionally the site, optionally an email fallback (address, subject, body), and how long the text is worth sending (10 minutes by default).
- It answers one of: `queued`; `emailed` (not sent, the fallback went out); `refused` with the reason. It raises only for a programming mistake.
- **Purposes are a closed list in code.** Each purpose is declared once with its category. `send_sms` refuses a purpose that is not declared. Today the list has one entry: `mobile_verify`, category `code`. An order-status purpose is added by the app that sends it, in a reviewed pull request. This is what keeps "only sign-in codes and order-status texts, no marketing" true in code: there is no way to send a text of any other kind without changing that list.
- A `status` text must name its site and must carry an email fallback. A `code` text need not.
- The text must fit one SMS (160 plain characters) and must contain no web link. Globe announced in September 2022 that it blocks person-to-person texts with clickable links, and repeated it in October 2023; whether that still holds today was not confirmed, so the function refuses links rather than risk the SIM.
- Order of checks: the number is normalised (`apps/common/phones.py`); the record is written; then the master switch, the kill switch, the limits and the phone's presence are checked (section 8); then the text is put in the cache and the record stays `queued`.

Order-status texts: the backend has no orders. Mobile verification is the only caller now. Nothing in `send_sms` is specific to it, and the tests call `send_sms` with a `status` purpose declared only in the tests, so the site limit and the email fallback are proven before the first real status text exists.

### 6.3 Where the text waits (D4)

**(a) In the cache, encrypted, until pickup or expiry.** Recommended.

- When a text is queued, the text is encrypted and put in the cache under the record's id, with a lifetime equal to the text's own (10 minutes for a code). The email fallback, when there is one, is kept the same way under a second key.
- The cache is Railway Redis (`REDIS_URL`), the same place the email sign-in codes are held. One difference from those codes: the cache holds only a keyed hash of an email code, but a text must be readable to be sent. So the text is encrypted before it is stored, with a key derived from `DJANGO_SECRET_KEY` (the `cryptography` package is already a dependency). Redis, and any copy Redis writes to its own disk, holds ciphertext only.
- At handout the text is read, decrypted, removed from the cache and given to the phone. If it is missing (Redis restarted, or the entry expired), the record becomes `failed` with reason `text_lost`.
- PostgreSQL never holds the text: the queue itself is the `SmsMessage` rows with status `queued`, which have no text column.
- The gateway refuses to run outside local development without `REDIS_URL`: with the per-process local cache, a text queued by one process could not be found by another.

**(b) Write the text only at pickup.** The record would hold a reference (for example the challenge it belongs to), and each purpose would register a small function that produces the text when the phone asks. For a code, the code itself would be generated at that moment. The text would then exist nowhere before the phone takes it. It is stricter, but every sender must supply such a function, the code's hash would be written during the phone's call rather than the subscriber's, and `send_sms` would no longer take the text as `send_email` does. Not recommended for the first build; the move from (a) to (b) later does not change the tables.

### 6.4 Where sending runs before slice 4

Nothing on the server pushes. The web service writes the record and the cache entry inside the subscriber's request, and the phone collects it with its next `poll`. **Sending needs no worker, no beat and no background thread.** The scope doc's line that SMS sending runs in the worker does not apply to this design; section 13 amends it.

Two things happen "later" and are done at the start of each `poll`, cheaply: queued records past their expiry become `expired`, and `dispatched` records with no report after 10 minutes become `failed`. An email fallback owed by one of those is sent with `apps/operators/background.py:run_later`, the existing web-service thread, so the phone's call is not delayed. If slice 4's worker arrives, `run_later` is the one place that changes, as its own comment says.

### 6.5 The master switch

One Railway variable, `SMS_GATEWAY_ENABLED`, off by default. While it is off: `send_sms` refuses with reason `gateway_off`, all three gateway calls answer 404, and mobile verification answers 503 with the "not available" message. The code can therefore be merged and deployed dark, the way hq was behind `HQ_HOST`.

### 6.6 The Dedicated SMS Line add-on is not blocked

The add-on (z8uq9mb19f) is not designed here. Four choices keep its door open: phones are rows, so a second phone is a second row; each record names the phone that took it; and one function picks the phone for a text, which today always returns the system phone; and the app can already forward incoming texts for a phone whose switch is on (section 5.6), so the add-on's inbox needs no new app. The add-on's own statement that all sign-in codes use the NegosyoOnline system number is already how `code` texts behave.

## 7. Mobile verification (A2)

### 7.1 Records

Both in `apps/accounts`, in one additive migration. Another session owns migrations in `apps/accounts`; the plan names this one for the coordinator and does not assume a number.

- **`AccountMobile`**: account (one each), the verified number, verified time, created and updated times. A row exists only for a verified number. Two accounts may hold the same number (D11; settled, the coordinator, 2026-11-10).
- **`MobileOtpChallenge`**: account, the number being verified, code hash, expires at (10 minutes), attempts (5 at most), used at, superseded at, the id of the `SmsMessage` that carried the code, created at. The scope doc lists account, code hash, expires_at and attempts; the number, the two end times and the message id are added because a challenge must remember which number it is for and must be usable once. The hash is a keyed hash of account, number and code, so a database copy does not reveal codes.

One open challenge per account: starting a new one supersedes the old. Challenges are personal data (a number that may never be verified), so they are deleted 24 hours after expiry (section 9).

### 7.2 `POST /v1/account/mobile/start/`

Signed-in account. Body: `{"mobile": "..."}`. The dashboard sends `+63` followed by the digits typed (`NOContact.phoneValue`); the server accepts any form `normalise_ph_mobile` accepts and works for every Philippine mobile number, on any network. There is no list of test numbers.

- Limits, through `apps/common/throttle.py`, carried from legacy: 3 starts per account per 15 minutes; 3 per number per 15 minutes; 10 per number per day; 20 per IP per hour.
- It creates the challenge, calls `send_sms` with purpose `mobile_verify` and no email fallback, and answers `200 {"queued": true}`. Both dashboard screens only check that the call succeeded.
- Errors, in the API's one error shape. The dashboard shows `fields.mobile` when present, else `message`:

| Case | Status and code | Shown |
|---|---|---|
| Not a Philippine mobile number | 400 `invalid_mobile`, `fields.mobile` | "Enter a valid Philippine mobile number." |
| Already this account's verified number | 400 `mobile_unchanged`, `fields.mobile` | "That is already your mobile number." |
| Too many requests | 429 `rate_limited` | The existing throttle message |
| Gateway off or offline | 503 `sms_unavailable` | "Text messages are not available right now. Please try again later." |
| Sending stopped or platform limit reached | 503 `sms_paused` | "Please try again shortly." |

A code is never offered by email instead: the point of the step is to prove the subscriber holds the phone. "SMS falls back to email" (section 8.3) applies to texts that have an email equivalent.

### 7.3 `POST /v1/account/mobile/verify/`

Signed-in account. Body: `{"code": "123456"}`. Limit: 10 tries per account per 15 minutes.

- Right code: the challenge is marked used; `AccountMobile` is created or updated; an `AccountAuditEvent` is written (`mobile_verified` or `mobile_changed`, holding no number); and the answer is `200 {"verified": true, "mobile_masked": "+63917***4567"}`. `account.js` puts that object straight into its `mobile` value, and shows `+63917***4567` as "+63 917 ••• 4567", which is exactly what `mask_mobile` produces.
- Errors, all 400 with `fields.code`: `code_expired` "This code has expired. Request a new one."; `wrong_code` "That code did not work. Please try again."; `code_burned` "Too many wrong codes. Request a new one." (after the fifth wrong try).

### 7.4 `GET /v1/account/` and `GET /v1/account/mobile/` (D8)

- `GET /v1/account/` returns `"mobile": {"verified": true, "mobile_masked": "+63917***4567"}`, or `{"verified": false, "mobile_masked": ""}` when no number is verified. Today it returns `null`. The dashboard reads both forms the same way (`hasMobile` in `account.js`). `PATCH /v1/account/` and the other calls that answer with the same shape carry it too. The full number is never returned.
- **`GET /v1/account/mobile/` is reported, not decided.** `earnings.js` (the withdrawals screen) reads it for `verified` and says so in its header comment. The Task lists only `start/` and `verify/`, and the scope doc, section 8, lists only those two. The withdrawals screen belongs to commerce, which the scope doc puts out of scope; the same file also reads `subscriber_mobile_verified` from an endpoint that does not exist here. If Coach Ed wants it, it is one read-only route answering the same object as above and adds no table. Without it, that one overview strip on the dashboard stays hidden, because the call fails quietly.

### 7.5 Changing a verified number (D7)

The dashboard says: "Changing your mobile sends a notice to your old number and your email", and legacy did text the old number. A notice to the old number is neither a sign-in code nor an order-status text, so it is outside the ruling.

- **(a) Email only.** Recommended. The account's email gets "The mobile number on your NegosyoOnline account was changed to the number ending in 4567." through `send_email`, as connecting and disconnecting a provider already do. The dashboard sentences that promise a text to the old number (`account.js`, the security note, the dialog note and the "Security alerts" line "Sent to your email and mobile") need a wording change in a frontend task.
- **(b) Also text the old number.** Needs Coach Ed to widen the ruling, a third declared purpose, and a check that the NPC wording ("sign-in codes and order-status texts") still holds.

### 7.6 Proposed wording (D10)

- The text: "Your NegosyoOnline code is 123456. It expires in 10 minutes. Do not share it." (79 characters, one SMS, no link).
- The change notice email. Subject: "Your NegosyoOnline mobile number changed". Body: "The mobile number on your NegosyoOnline account was changed to the number ending in 4567. If you did not make this change, sign in and update it."
- The error messages in 7.2 and 7.3.

## 8. Controls (A3)

### 8.1 Daily limits (D6)

- **Per site:** `status` texts for one site in one Manila day. Proposed: 100.
- **Platform:** every text in one Manila day. Proposed: 500. Both numbers are legacy's defaults; they need Coach Ed, because the right ceiling is whatever an ordinary Globe plan tolerates. Globe's fair-use terms for unlimited texts were looked for and not found (section 12).
- The counts are kept in the cache with an atomic add, so two requests cannot both take the last slot, and are started from a count of the day's records if the cache was emptied. The records stay the source of truth.
- A `code` text has no site, so it counts against the platform limit only. The per-site limit has no real sender until order-status texts exist; it is built and tested now.
- Risk accepted: a flood of code requests could use up the platform limit for everyone that day. The per-account, per-number and per-IP limits in 7.2 bound it, and only a signed-in account can ask.

**Where the numbers and the kill switch live.** (a) One `SmsPolicy` row, edited in the Django admin (which requires two-step sign-in), each change written to `OperatorAuditEntry` with the before and after values. Recommended: stopping all sending takes seconds and no deploy. (b) Railway variables: no table, but every change is a redeploy and a variable approval, which is slow for a kill switch.

### 8.2 Kill switch

`SmsPolicy.sending_stopped`. While on: `send_sms` queues nothing (reason `paused`), the phone is handed nothing and told to ask less often, and texts already queued are left to expire. It is separate from the master switch in 6.5, which is a deploy-time setting.

### 8.3 SMS falls back to email

When a text that carries an email fallback cannot be sent (site limit, platform limit, sending stopped, gateway off or offline, expired unsent, or the phone reports a failure), the fallback email is sent through `send_email`, and the record shows `emailed`. If no fallback was given, or the email also fails, the record keeps its `refused` or `failed` status and an ERROR log line is written. Mobile verification gives no fallback, by design (7.2).

### 8.4 Masked numbers in the admin

- `SmsMessage`: a read-only list built on `MaskedListAdmin`, showing the number through `mask_mobile` (`+63917***4567`), with category, purpose, status, reason and times. The record's detail page shows the masked number too, and the record's name never contains the number. Search matches a full number typed by the admin, as the email list does for addresses.
- `AccountMobile` and `MobileOtpChallenge`: read-only lists with the number masked and the account shown through `masked_account`. The challenge's code hash is not shown.
- `SmsDevice`: a read-only list (name, active, last contact, app version, battery). Tokens are not shown; phones are managed only with the terminal command.
- `SmsPolicy`: the one row can be changed, not added or deleted.
- Log lines never contain a number or a text.

## 9. Send records and retention (A4)

### 9.1 What is kept

`SmsMessage` as in 6.1: number, category, purpose, status, reason, times. No text, at any point. Records live in the PostgreSQL database on Railway, Singapore, as the NPC wording says. (That the staging and production databases are in Singapore is Railway configuration this session did not read; section 12.)

### 9.2 The rules

| Rule | What | When |
|---|---|---|
| `sms_messages` | Delete `SmsMessage` rows | 365 days after `created_at` |
| `mobile_challenges` | Delete `MobileOtpChallenge` rows | 24 hours after `expires_at` |

Rows are deleted, not blanked: the ruling for SMS records is "kept 1 year, then deleted".

### 9.3 One retention mechanism, shared with Build 7

Build 7 (z8uq9mdwdy, account-side retention, not started) will also need a retention job. To avoid two, this build creates the shared piece and uses it:

- **`apps/common/retention.py`**: a small registry. An app registers a rule with a name and a function that takes "now" and "dry run" and returns how many rows it removed (or would remove). `run_all` runs every rule and returns the counts.
- **`manage.py run_retention [--dry-run] [--only NAME]`**: runs the rules, prints the counts, and writes one `OperatorAuditEntry` (action `retention_run`, no operator, the counts as detail), which is the run log the build list asks for. The dry run changes nothing.
- This build registers the two rules in 9.2. **Build 7 adds its own rules to the same registry and gets the command, the dry run and the run log for free.** Flag for the coordinator: whichever of the two builds merges first creates `apps/common/retention.py` and the command; the other only registers rules.

**What starts it before slice 4 (D5).**

- **(a) The web service, once a day.** Recommended for now. One function asks the cache for a once-a-day lock and, if it gets it, runs the rules with `run_later`. It is called from the gateway's `poll`, which arrives every few seconds while a phone is connected. No new service. Weakness: a day with no call is a day with no run; the next run removes everything that is due, so nothing is kept longer than one extra day of downtime.
- **(b) A Railway cron service** running `manage.py run_retention` daily. Independent of traffic and the cleanest record, but it is a new Railway service and needs Coach Ed's approval.
- **(c) By hand**, by the admin or an approved session. Not recommended.

No `SmsMessage` row can be a year old before November 2027, and slice 4's beat service will exist long before that, so for this task the choice affects only the 24-hour challenge clean-up. For Build 7, whose rules start at 30 days, the choice matters more; that build can keep (a) or ask for (b). Slice 4 moves the trigger to beat in either case.

## 10. Variables, migrations and dependencies the build will need

Listed for approval in the plan. None is created by this document.

| Kind | Item | Notes |
|---|---|---|
| Railway variable | `SMS_GATEWAY_ENABLED` | `1` turns the gateway on. Off by default. The only new variable with the recommended answers |
| Railway variable (only if D2 is b) | A Firebase service-account secret | Not needed with polling |
| Railway variable (only if D3 is b) | A gateway host name | Not needed on the API host |
| Migration | `apps/notifications`: `SmsMessage`, `SmsDevice`, `SmsPolicy` | Additive. Next free number in that app (`0002` as of `e85e727`) |
| Migration | `apps/accounts`: `AccountMobile`, `MobileOtpChallenge` | Additive. Next free number in that app (`0003` as of `e85e727`); another session owns this app's migrations, so the number and the order are agreed through the coordinator. It does not depend on the notifications migration |
| Dependency | None | Encryption uses `cryptography`, already in `pyproject.toml` |
| Existing variable | `REDIS_URL` | Must be set wherever the gateway is on (6.3). Not checked on staging by this session |

## 11. Not in this design

The Dedicated SMS Line add-on; any text other than a code or an order status; storing, showing or acting on incoming texts (the app can forward them, switched off, section 5.6; everything after that is the add-on's); delivery receipts from the carrier; the hq health page and its SMS counts; an alert table; order-status texts themselves (no orders exist); per-site limit overrides; the dashboard wording change in 7.5; any filing or policy text.

## 12. What was verified, and what was not

Verified from each project's own pages on 2026-11-10:

- SMS Gateway for Android (docs.sms-gate.app and the GitHub README): Apache-2.0; local, cloud and private modes; private mode needs the project's server, a MySQL or MariaDB database, and HTTPS with a valid certificate; the app's address must end in `/api/mobile/v1`; private-server push goes through `api.sms-gate.app` to Firebase; the privacy policy says the push "Contains only FCM token" with "No message content/recipients"; the example private-server settings hash message content every 168 hours and clean up records after 720 hours; local mode listens on port 8080 with basic authentication and is limited by carrier-grade NAT; custom builds with one's own Firebase "are not officially supported"; the app is installed from GitHub release files; optional end-to-end encryption covers text and numbers.
- httpSMS (GitHub README): AGPL-3.0; self-hosting needs a Firebase project and a rebuilt app.
- textbee (GitHub README): AGPL-3.0; self-hosting needs MongoDB, Redis, a Firebase project and a rebuilt app.
- Firebase Cloud Messaging (Android message priority page): high-priority messages can wake a sleeping phone; messages that do not result in a user-facing notification may be given lower priority, judged over 7 days.
- Globe (its help page and 2022 and 2023 news reports): person-to-person texts with clickable links are blocked.

Not verified:

- Whether Globe's link blocking is still in force in 2026, and Globe's fair-use limit on unlimited texts. Coach Ed should check the plan's terms when choosing the SIM. The plan must include texts to all networks, or "every Philippine number" is not true.
- The current version and last release date of SMS Gateway for Android (only a third-party listing was found), the details of its held-open connection, and exactly which fields its private server stores.
- Android behaviour stated from memory, to be proven on the real phone before the build is called done: that a phone on its charger is not put to sleep in a way that stops a 5-second background service; that the SMS permission rules out Google Play for this app; and the exact steps Android asks for before an app installed from a file may receive texts (section 5.6).
- Railway facts this session did not read: whether staging has `REDIS_URL` set, how many Gunicorn workers run (the start command sets none, so the default of one is assumed), and the database region.
- Nothing was run. This stage wrote documents only.

## 13. Scope doc changes

Made in this pull request, because they record rulings already given: section 9 item 8, section 11 (the SMS provider decision is answered), and a dated entry in section 12.

To be made when this design is approved, and not before:

- Section 3, last bullet: SMS sending does not wait for the worker; the phone collects texts from the web service.
- Section 6, accounts: `AccountMobile` and `MobileOtpChallenge` as in 7.1.
- Section 6, notifications: `SmsMessage` as in 6.1, plus `SmsDevice` and `SmsPolicy`.
- Section 8: the three gateway calls, noted as outside `/v1/`; `GET account/mobile/` only if D8 is ruled in.
- Section 10: SMS send-record retention moves into scope.
