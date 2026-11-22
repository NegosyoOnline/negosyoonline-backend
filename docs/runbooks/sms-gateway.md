# SMS Gateway Runbook

NegosyoOnline sends texts through its own phone: an Android phone with a SIM, running the NegosyoOnline gateway app. The server never pushes. The phone asks the server for waiting texts every few seconds (`POST /gateway/v1/poll`), sends them through its SIM, and reports each result (`POST /gateway/v1/report`). The calls are on the API host and are described at the top of `apps/notifications/gateway.py`.

Every step below that touches staging or production needs Coach Ed's approval for that step. Nothing here is approved by this document.

## Variables

| Name | Meaning |
|---|---|
| `SMS_GATEWAY_ENABLED` | `1` turns on the send function, the three phone calls and the admin's SMS lists. Unset: no text is queued, the calls answer 404, and the SMS tables are not read or written. |
| `SMS_GATEWAY_MGMT_ENABLED` | `1` turns on the HQ screens' phone routes (`/staff/v1/sms/device/...`). Needs `SMS_GATEWAY_ENABLED` as well. Unset: those routes answer 404 and the CLI below is the only way. |
| `SMS_GATEWAY_PUBLIC_URL` | The address HQ shows beside a new token: an origin only, such as this environment's API host. Unset: HQ shows "not configured" (only local `DEBUG` falls back to `http://127.0.0.1:8000`). Never set a default in code. |
| `SMS_SITE_LINES_ENABLED` | `1` lets a Site's own paired phone carry that Site's texts (see Site phones below). Unset: dark, every text goes from the NegosyoOnline phone as before. Needs `notifications.0005` and `0007` applied first. |
| `REDIS_URL` | Must be set wherever the gateway is on. A text waits encrypted in Redis until the phone takes it. Without it `manage.py check` reports `notifications.E001` and the gateway stays off. |

## Release order

1. Deploy. The code is dark while `SMS_GATEWAY_ENABLED` is unset.
2. Apply the `apps/notifications` SMS migration and the `apps/accounts` mobile migration, `accounts.0003_accountmobile_mobileotpchallenge` (`docs/runbooks/migrations.md`). Each run is its own approval. Until `accounts.0003` is applied, deleting a subscriber account in the admin fails, because the delete looks for that account's rows in the two mobile tables. The Subscribers list allows it in two places: the "Delete selected" action on the list and the Delete button on an account's page.
3. Confirm `REDIS_URL` is set, then set `SMS_GATEWAY_ENABLED=1`. Never before step 2: the send function, mobile verification and the lists would fail on the missing tables.
4. Read "Before a phone is added" below and record the phone in its table, then add the phone and enter its token in the app.
5. Check: `sms_device list` shows a recent `last_contact`, and the admin's SMS phones list shows the phone.

To go back, unset `SMS_GATEWAY_ENABLED`. The tables stay; nothing reads them.

`notifications.0004_alter_smsmessage_category` changes the allowed values of a send record's category only (it adds `inbox`); it is applied before `SMS_INBOX_ENABLED` is set. The code can be released before it is applied: no staff text can be sent until `SMS_INBOX_ENABLED` is set and access is granted.

## Phones

The platform phone is managed from HQ, Settings, SMS Gateway, while `SMS_GATEWAY_MGMT_ENABLED` is on, or with `manage.py sms_device` from a terminal (the fallback, and the only way to switch incoming forwarding). Both call the same functions in `apps/notifications/devices.py`, so they cannot diverge. `--actor` is the staff user running the command; every action except `list` writes an operator audit row.

**HQ screens** (staff routes, all POST, all `Cache-Control: no-store`):

- `sms/device/get/` shows the phone, its connection and the server address; never a token or hash. Connection: *Connected* (an authenticated poll in the last 60 s), *Waiting for phone* (registered or rotated, no poll since), *Offline*, *Disabled*, *No phone*.
- `sms/device/register/` adds the platform phone and returns its token once. It answers 409 while an active platform phone exists.
- `sms/device/rotate/` and `sms/device/disable/` need `confirm: true` and a fresh 6-digit authenticator code (a recovery code is refused). A wrong, stale, reused or locked code gets one message, and the failure is audited as `sms_device_reauth_failed` with counts only. A disabled phone cannot be rotated or re-enabled; register a new one.
- Audit rows are `sms_device_add`, `sms_device_rotate`, `sms_device_disable` with `{"device": id, "name": ..., "via": "hq"}` (`"cli"` from the terminal). No row, log line or later answer holds a token or hash.
- These routes never change incoming forwarding; that stays `sms_device incoming on|off`.

```bash
uv run python manage.py sms_device add --name system --actor USERNAME
```

```bash
uv run python manage.py sms_device list
```

- `add` prints the phone's token **once**. Enter it in the app straight away. Only its SHA-256 is stored, so a lost token cannot be shown again: rotate it. Never paste a token into chat, a ticket or a commit.
- **The first phone added that is still active is the system phone, and it is the only one that is handed texts.** `list` marks it `sends=True`. A second phone can connect but sends nothing.
- A phone's row never holds its SIM's number.

### Before a phone is added

The phone's own Messages app keeps a copy of every text the gateway app sends, with the number and the code. Android does this itself and the gateway app cannot turn it off.

Coach Ed accepted this on 2026-11-11 ("Approve plan") with two controls in the set-up notes: a screen lock, and the Messages app's own auto-delete of old texts. The first real phone has neither, and he ruled on both the same day:

- **Auto-delete.** The realme C55's Messages app has no such setting. His ruling on what replaces it: "Just put there the same ruling that it will be deleted after 1 year. You don't need to be very strict with this."
- **Screen lock.** None is set. He was told that with no lock anyone holding the phone can read the copies, and that with no lock the gateway comes back by itself after a restart or a power cut. His ruling: "no lock, leave it as is."

So the controls on the phone's copies are now these two:

1. **A clear by hand, at least once a year.** Whoever keeps the phone deletes the sent texts in its Messages app and writes the date in the table below. This follows the 365-day rule for SMS send records ("Retention", below). Who does it, how often and where the date is noted were decided by the coordinator, not by Coach Ed; he may overrule them.
2. **Physical custody of the phone.** With no screen lock, whoever holds the phone can read the copies.

A phone with no auto-delete or no screen lock is no longer a reason to stop before `sms_device add`. Record what the phone has in the table instead.

This differs from the approved Android plan (`docs/plans/2026-11-11-gateway-app-plan.md` in the gateway repository, section 11 and question Q2), whose set-up notes still name the screen lock and the auto-delete. The two rulings above are later and stand. The NPC drafts also name the screen lock as a control; their wording belongs to the NPC coordinator and is not changed by this runbook.

The gateway phone. This table never holds the SIM's number or a token.

| | |
|---|---|
| Phone | realme C55 (RMX3710) |
| Android version | 15 |
| App | 0.1.0, signed release built from the gateway repository's `main` at `0e7ae88` |
| Name on the server | `system` |
| Environment | staging only. Production is not approved. |
| Incoming texts | off |
| First text sent | 2026-11-11, about 16:08 Manila |
| Screen lock | none (ruling above) |
| Messages app auto-delete | none: the app has no such setting |
| Sent texts last cleared by hand | not yet cleared |

Not verified on the real phone:

- **24 hours and 7 days on the charger with the screen off.**
- **A restart and a power cut.** With no screen lock the gateway app should come back by itself; this has not been watched on the phone.
- **The battery setting.** That the app is set to unrestricted is Coach Ed's report only.

If a screen lock is ever set, the phone starts the gateway app again after a restart only once it has been unlocked. Until then the server sees it as offline.

### Setting up the phone and its app

The app is in its own repository, `NegosyoOnline/negosyoonline-sms-gateway`. Its checklist, `docs/phone-setup.md` there, covers the phone side: the signing key, the release build, the install, the permissions and the app's screen. This section says where the server steps fall in that checklist. No server address and no token is written in either repository.

**Status: done once, on staging, on 2026-11-11.** The phone in the table above sent its first text at about 16:08 Manila: a mobile verification code Coach Ed asked for on the staging dashboard for his own number. By his report the text arrived and the code verified. On the server there was one send record with status `sent`, and `sms_device list` showed the phone active with a current `last_contact` and app 0.1.0. Before that the app had run only on an Android 17 emulator against a local server. Still unproven: the three points listed under the table above, what Android reports with no signal or no load, and the restricted-settings and Play Protect steps of the install. The Android 8.0 emulator run was skipped by Coach Ed's ruling ("Yes, skip it"), so nothing below Android 15 has been tested: a phone older than that owes an emulator run for its version first. Production is not approved.

1. **The phone itself.** One SIM, no SIM PIN, kept on its charger. Read "Before a phone is added" above and record the phone in its table.
2. **Build and install the app** (checklist steps 1 to 4). Nothing on the server changes.
3. **Add the phone on the server** with `sms_device add` (above). **On staging or production this is Coach Ed's approval, each time.** The token is printed once: type it into the app straight away, with the server's address, and tap Save.
4. **Check, then start.** In the app tap Check, which should say "Connected.", then turn "Gateway on" on. A Check takes any text that is waiting and reports it not sent, so tap it only when nobody is asking for a code on that server. The app refuses Check and Save while the gateway is on.
5. **Confirm on the server.** `sms_device list` shows a recent `last_contact` and the app's version, and the admin's SMS phones list shows the phone.
6. **Turn USB debugging off** on the phone if it was used for the install.

After an app update the gateway comes back by itself if its switch was on; `sms_device list` should show a fresh `last_contact` within a minute. If the app was reinstalled rather than updated, its saved token is gone: rotate the token (below) and type the new one.

Incoming texts stay off (see "Incoming texts" below). Turning that switch on for any phone is Coach Ed's own approval, each time.

### Rotating a token

Use this when the token may have been seen by anyone else, or when the phone is replaced.

```bash
uv run python manage.py sms_device rotate --name system --actor USERNAME
```

The old token fails at once. Sending stops until the new token is entered in the app; codes asked for meanwhile are refused with "Text messages are not available right now."

### Stopping one phone

```bash
uv run python manage.py sms_device disable --name system --actor USERNAME
```

The phone is refused from its next call. There is no command to enable it again: add a phone under a new name. A disabled phone is no longer the system phone, so the next active phone, if there is one, becomes it.

## Stopping all sending

On the HQ Settings page, SMS settings: tick "Sending stopped", save. It takes effect on the next request and needs no deploy. While it is on:

- nothing new is queued: a code request answers "Please try again shortly.", and a text that has an email fallback is emailed instead;
- the phone is handed nothing and is told to ask every 30 seconds instead of every 5;
- texts already waiting are left to expire.

Untick it to start again. Every change to SMS settings is written to the operator audit log with the values before and after. The two daily limits (they start at 2,000 for the platform and 2,000 for one site, counted by the Manila day) are on the same page.

## When the phone is offline

The gateway counts as online while the system phone asked for texts in the last 60 seconds.

- Offline when a text is asked for: nothing is queued. A code request answers "Text messages are not available right now. Please try again later." A text with an email fallback is emailed. The record is `refused`, reason `gateway_offline`.
- The phone drops out after a text was queued: the text waits until its expiry (10 minutes for a code), then the record becomes `expired` and a text with an email fallback is emailed.
- The phone took a text and never reported: after 10 minutes the record becomes `failed`, reason `no_report`. A text is handed out once and is never sent a second time.
- The Railway log shows `sms_gateway_offline` at most once an hour while sends are being refused, and `sms_not_sent` for each text that neither went out nor was emailed. Neither line holds a number or a text.

To find out why: `sms_device list` shows the phone's last contact. On the phone, check power, the internet connection, and that the app's service is running and holds its SMS permission.

## Incoming texts: the three switches

The app can forward the texts the SIM receives. A phone's own switch is **off** for every new phone.

- **A text a phone receives is stored only when three switches are all on:** `SMS_GATEWAY_ENABLED`, `SMS_INBOX_ENABLED`, and the phone's own switch. Each variable is Coach Ed's approval per environment. The phone's switch is his approval each time, **and only after the NPC and privacy wording is amended**.
- Before the phone's switch is turned on in any shared environment, these are checked and amended first, because the NPC filing today describes a phone that only sends: register row 6, filing-store-sms, filing-subscriber-accounts-billing, filing-subscriber-stores, PIA 5, privacy manual s8, and the Terms and Privacy pages (the provider list, and the sentence that says the text of a message is not kept).
- With any of them off, `POST /gateway/v1/received` answers 204 and drops the body unread, and `poll` tells the phone not to forward.
- What is stored when all are on: the sender's number, the full text, the arrival time and the SIM slot, for 365 days. **Every text the SIM receives is stored**, including the carrier's notices, wrong numbers and one-time codes other services send to that SIM. Nothing filters them.
- The order to turn it on in an environment: the `inbox.0001` migration applied, `SMS_INBOX_ENABLED=1` set, access granted, then the phone's switch.
- **To stop taking texts in, turn the phone's switch off.** It is audited:

  ```bash
  uv run python manage.py sms_device incoming off --name system --actor USERNAME
  ```

  **Intake is stopped by the phone's switch, not by unsetting the variable.** Unsetting `SMS_INBOX_ENABLED` hides the inbox from staff and does also stop storing, but it leaves the phone's switch recorded as on and is written to no audit record. Either way the phone deletes at once whatever it had not yet handed over.
- **What is already stored stays stored when the variable is unset, and is still deleted at 365 days.** The retention rule `inbox_messages` runs wherever the inbox tables exist, whatever the variable says. It is started by the phone's `poll`; in an environment where `SMS_GATEWAY_ENABLED` is off no phone polls, so run `manage.py run_retention --only inbox_messages` there by hand (`docs/runbooks/retention.md`), or the texts stay past 365 days.
- **A person asks for their texts to be deleted.** Count first, then delete:

  ```bash
  uv run python manage.py sms_inbox_delete --actor USERNAME
  ```

  ```bash
  uv run python manage.py sms_inbox_delete --actor USERNAME --confirm
  ```

  Each run asks for the number: type it at the prompt, then Enter. The first run only counts; read the count, then run the second, which deletes for good. For a Philippine mobile it also deletes the send records of the staff texts sent to that number and drops one still waiting for the phone, so it is never sent, and deletes the contact held under that number (see "Contacts" below; the groups it was in stay), and removes the number from a Bulk SMS batch still waiting (it counts "bulk recipients"); it does not touch the do-not-text list; it leaves the send record of a sign-in code to that number, which goes at 365 days. If a text to that number is with the phone right now it stops and deletes nothing: run it again in 10 minutes. **The number is typed at the prompt and nowhere else: never as an argument, and never fed in through `echo`, `printf` or a here-string, which would leave it in the shell's history.** A Philippine mobile may be typed in any usual form. Any other number (a foreign mobile, a landline) is matched by its digits, so spaces, brackets and dashes do not matter, but the prefix does: try the number as the person wrote it, with and without its country code. The command works with the variable on or off, and each run writes one audit entry that holds counts and no number. On a shared environment a run with `--confirm` is done by whoever Coach Ed has answer that person's request, per request. **What it does not remove:** the copy in the phone's Messages app, which is deleted on the phone; the masked number in the audit record of each time a conversation was opened, replied to or written to, which is blanked after a year; the send record of a sign-in code sent to that number, which is deleted at 365 days; and a text that arrives afterwards.
- A text the server cannot store (one with no sender, for one) is skipped and counted in one log line, `sms_incoming_skipped count=N`, and the phone drops it: it is then only in the phone's Messages app. `sms_incoming_rejected` means the phone sent something that is not a list of texts, which the app never does. Neither line holds a number or a text.
- The commands: `sms_device incoming on|off`, which refuses `on` while the variable is off; `sms_inbox_access grant|revoke|list`, which refuses while the variable is off; `sms_inbox_delete`. Who is granted access, and whether they may send, is Coach Ed's decision, per person.
- There is no screen yet: the pages are the next plan. The staff routes exist (see "Staff texts" below). No Django admin screen shows a conversation or a text.

## Contacts

- **A second switch, `SMS_CONTACTS_ENABLED`, opens the twelve routes under `/staff/v1/contacts/`.** It is off and set nowhere. They also need `SMS_INBOX_ENABLED` and a staff user with an inbox access row. Each variable is Coach Ed's approval per environment. **Before `SMS_CONTACTS_ENABLED` is set in a shared environment, the list of filings and policy pages above is checked and amended first, with contacts' names named** (a name is stored with a number).
- Order: the `inbox.0002` migration applied, `SMS_INBOX_ENABLED=1`, access granted, then `SMS_CONTACTS_ENABLED=1`. The code can be released before the migration; until it is applied every contacts route answers 404 and `sms_inbox_delete` still works.
- What is stored: a first name, a last name, a Philippine mobile number and the groups, who created it and when. A contact is kept until a staff member deletes it. **No job removes a contact**, and contacts are not in the account export.
- **A person asks for their name and number to be deleted:** `sms_inbox_delete`, as above. For a Philippine mobile it also deletes the contact held under that number and counts it ("1 contact"); the groups stay. Its audit entry holds the count, never the number or the name. With the switch off or `inbox.0002` not applied it still works.
- `Contact.created_by` is PROTECT: a staff user who created contacts cannot be deleted until their contacts are reassigned or deleted.
- Staff can add, edit and delete contacts and groups and upload a CSV file (up to about 2.4 MB, checked, then saved). Whether a body that large passes Railway's proxy is not verified.
- A contact whose number is on the do-not-text list shows `opted_out` (see "Bulk SMS" below).
- There is no screen yet: the pages are the next plan.

## Bulk SMS

- **A third switch, `SMS_BULK_ENABLED`, opens the seven routes under `/staff/v1/bulk/`.** It is off and set nowhere. They also need `SMS_INBOX_ENABLED` and a staff user with an inbox access row granted `--send --bulk`. Each variable is Coach Ed's approval per environment, **and only after the NPC and privacy wording covers the announcement (R11)** and the list of filings and policy pages under "Incoming texts" is amended.
- Order: the `inbox.0003` migration applied, `SMS_INBOX_ENABLED=1`, the phone's incoming switch on (so a STOP reply is seen), access granted with `sms_inbox_access grant --user USERNAME --send --bulk --actor USERNAME`, then `SMS_BULK_ENABLED=1`. The code can be released before the migration; until it is applied every bulk route answers 404, the access command refuses `--bulk`, and `sms_inbox_delete` and retention still work.
- **One batch at a time.** A second confirm while one is waiting or sending is refused (`batch_running`).
- **Room:** the day's limit less the 300 kept for codes, shared with every other staff text; a longer list is cut and the rest is offered as the left-out file.
- **How a batch is driven:** by the phone's `poll`, one text at a time, each ending "Reply STOP to opt out", 18 to 25 seconds after the last staff text. In practice about 145 to 200 an hour; that pace is not measured on a real phone.
- **What is kept:** the batch's text and counts for 365 days; each number only while the batch runs (deleted as it is sent, left out or stopped); a send record per number for 365 days; the do-not-text list with no end date. A person who texts "stop" (whitespace and edge punctuation ignored, so "stop." and "STOP!" count) is added to it and is never texted in bulk again.
- **No removal path for the do-not-text list.** No job and no command removes a number from it, and `sms_inbox_delete` leaves it. A request to remove a number is an NPC and privacy decision, not built.
- **To stop:** the Stop button on the page, which sends nothing more and drops the waiting numbers; or unset `SMS_BULK_ENABLED`, which freezes the batch where it is (nothing is sent; the waiting numbers stay until the variable is set again, Stop is used, or `sms_inbox_delete` removes one).
- **To continue a list the next day:** the check offers the left-out file; upload it as the next day's source. For a groups source the file hands staff the full numbers.
- **Blocked-SIM note.** If the carrier blocks the SIM for mass texting, many texts turn "Not sent" with `phone_failed` or `no_report`, or the phone reports failures; codes stop with them. Stop the batch, check the SIM with the carrier, run `sms_policy` stop if needed, and remember that codes go by email meanwhile only where a fallback exists. Coach Ed accepted this risk (R14).

## Staff texts

- A staff member with send access may reply in a conversation or write a new text to one Philippine mobile. Both go through the send function: one SMS of at most 160 plain characters, no web link, no marketing.
- A staff text waits up to 1 hour for the phone, is sent after every waiting code, and leaves no sooner than 18 to 25 seconds after the last staff text. One staff text is with the phone at a time.
- 300 of the day's texts are kept for codes: a staff text is refused once the day's count reaches the limit less 300. The 300 is `RESERVED_FOR_CODES` in `apps/notifications/sms.py`; changing it is Coach Ed's ruling and a reviewed change.
- One staff user may send 60 texts in an hour.
- What is kept: the text, with who wrote it, in the inbox for 365 days; a send record with the full number and no text for 365 days; an audit entry with the masked number, blanked after a year (`admin_audit_personal_values`, see `retention.md`).
- The six routes under `/staff/v1/inbox/`, each a `POST`, are closed while `SMS_INBOX_ENABLED` is off and to a staff user without access.
- There is still no screen: the pages are the next plan.

## Store texts (SMS-OWNER, task z8uq9mhbf4)

A Santé store writes to its own **verified** members from its dash Inbox, one member at a time. Built dark behind `INQUIRY_SMS_ENABLED` (needs `INQUIRIES_ENABLED` and `SMS_GATEWAY_ENABLED`, `inquiries.E008`); migrations `notifications.0006_store_sms` and `inquiries.0010_sms_channel` first (see `migrations.md`). Each store also turns it on itself (`sms_enabled` on the channels route); the default is off.

- **Purposes** `store_compose` (a new conversation) and `store_reply`: category `store`, the same paced lane and priority as a staff text, one plain SMS of at most 160 characters, no web link, no email fallback. The text always starts with the store's name, `"Name: "` (the name cut to 30 characters; a name that cannot be texted gives "Your store: "), so the owner's room is `160 - len(prefix)`.
- **Limits, in the order a store text is refused:** staff stop (`paused`); the store's day (`site_limit`, `SmsPolicy.store_daily_limit`, default 50); the store's clock hour in Manila time (`site_hour_limit`, `STORE_HOURLY_LIMIT` = 20 in `apps/notifications/sms.py`); all stores' day (`stores_limit`, `all_stores_daily_limit`, default 1000); the platform day less the 300 kept for codes (`staff_limit`); the phone offline (`gateway_offline`). A refusal gives back the slots already taken. The two store caps are changed in the staff SMS policy (`change_policy`, audited) like the other caps; a body that leaves them out keeps them. Codes are never counted against a store cap.
- **Incoming.** With `SMS_INBOX_ENABLED` on, a text from a number goes to the store that texted that number last in the past 7 days (store or HQ texts of the purposes `store_reply`, `store_compose`, `inbox_reply`, `inbox_compose`, `inbox_bulk`; codes are ignored), when that last text was a store's, went out (queued, with the phone or sent), and the store is not closed and has texts on. Otherwise it goes to HQ exactly as before. Each text is stored in exactly one place; a repeated delivery is stored once.
- **STOP.** A STOP routed to a store is that store's own opt-out (`InquirySmsOptOut`); the store can send the number no more texts, other stores and codes are unaffected. A STOP to HQ stays the global do-not-text list.
- **Data.** The number is held in the conversation (blanked with its other personal fields 180 days after the last message, like the other stored channels) and in the opt-out row (deleted with the store's data). The thread shows the number masked.
- **Deleting a person's texts:** `sms_inbox_delete` also deletes every store's text conversation with the number (with its reply audit rows) and the stores' send records to it, listing the store public ids; `--site SITE_PUBLIC_ID` deletes only that store's and touches nothing of HQ's. The store's opt-out of the number is kept.
- **Removing a test member** (`pack_remove_test_member`) deletes that member's text conversation first.
- **Turning on** (staging, Manager's go): apply the two migrations, set `INQUIRY_SMS_ENABLED=1` with `SMS_INBOX_ENABLED=1` and the phone passing texts on, run `check --deploy`, then switch a store on in its Inbox.

## Site phones (SMS-INCLUDED, task z8uq9mhbpp)

Built dark behind `SMS_SITE_LINES_ENABLED`. Texts are included in the website subscription, so a Site may pair its own phone while it is **serving**; nothing is granted by hand.

- **Who has a line** (`sms.site_has_line`): the Site's billing position is serving (`billing.position(site).storefront_served`: trial, active, payment needing attention, billing-exempt) and HQ has no active `SmsLineDisable` for it. It fails closed: with `BILLING_ENABLED` off, no subscription, or an unreadable position, there is no line. Suspended or cancelled-after-service-ended Sites lose it at once, with no job; reactivated, the same phone works again.
- **`SmsLineDisable`** (`notifications.0007`): one row per Disable (site, disabled_by, reason, disabled_at, `reenabled_at` empty while it holds). At most one active row per Site. No row, or only re-enabled rows, means not disabled. The HQ Disable and Re-enable screens come later; until then rows are written only from the shell, with the audit entry written by hand.
- **Routing:** a Site's order-status and store texts go to its own phone; a Site with no line (switch off, billing ended, disabled, or no active phone of its own) uses the NegosyoOnline phone. A Site phone is handed only its own Site's texts, and nothing while its Site has no line. **Codes never go to a Site phone (D1):** a member's sign-in or mobile-confirm code names its Site but is always sent from, and handed only to, the NegosyoOnline phone.
- **Offline (D2):** a store text from a Site whose own paired phone is offline is refused with `site_phone_offline` ("Your phone is offline right now. Nothing was sent."), never handed to the NegosyoOnline phone. A Site with no line of its own meets the NegosyoOnline phone's `gateway_offline`. The refusal comes last in the store text order, after the caps, and gives the slots back.
- **Before turning it on** (staging, Manager's go): apply `notifications.0005` and `0007` (see `migrations.md`), set `SMS_SITE_LINES_ENABLED=1`, run `check --deploy`, then prove with two clearly named test Sites that each phone gets only its own Site's texts.

## Owner SMS Gateway API and the two-Site isolation proof (SMS-INCLUDED, task z8uq9mhg8w)

Built dark. A store's owner pairs their own phone under `/v1/sites/{site_id}/sms-gateway/` (`apps/notifications/api_owner.py`); the dash tab that calls it is a later task.

| Route | Does |
|---|---|
| `GET .../sms-gateway/` | `{server_address, device}`; `device` is the Site's own phone (name, enabled, last poll, battery, `connection`: waiting, connected, offline, disabled) or `null`. Never a token or a hash. |
| `POST .../register/` | Pairs a phone. 201 with `token`, once. 409 `phone_exists` while the Site has an active phone. |
| `POST .../rotate/` `{step_up_code}` | A new token; the old one gets the 401 at once. The code comes from `POST /v1/account/step-up/` with `purpose: "sms_line_rotate"` (email, or the verified mobile) and is used up. 409 `no_phone` / `phone_disabled`. |
| `POST .../disable/` | Switches the Site's phone off for good (pair a new one to start again). |

- **Always 404** (never 403) while `SMS_GATEWAY_ENABLED` or `SMS_SITE_LINES_ENABLED` is off, for a missing, removed, foreign or non-Santé Site, and for a Site that has no line now (`sms.site_has_line`: billing serving and not disabled by HQ; a Billing-only Site is therefore 404 too). Signed out is 401 once the switches are on.
- No route takes a device id or name: the phone is always found through the Site. Every answer is `Cache-Control: no-store`. Only the register and rotate answers hold the token; only its hash is stored.
- **Audit** (the account's history, `AccountAuditEvent`): `sms_line_register`, `sms_line_rotate`, `sms_line_disable`, each `{site, device}`; the step-up request is `sms_line_rotate_step_up`. No token or hash is written to the audit, a log or an error body.
- Incoming forwarding is never touched. Rotate needs `ACCOUNT_EMAIL_CHANGE_HARDENED` on, the same as the step-up for an email or mobile change.

### Two-Site isolation proof

Run before any subscriber sees a token control (task 598401). The steps below are the **local** proof. Do not run them as written on staging: see "On staging" after step 9. Locally nothing is texted: the number in step 5 is a fake, local-only value and no phone exists that could send it.

1. **Local set-up.** Your own Redis on a fresh port (`redis-server --port 6391 --save '' --daemonize yes`; a text is held in the cache, so the server and the shell must share it) and a SQLite file. In the shell that runs the server and the steps: `DJANGO_DEBUG=true DJANGO_SECRET_KEY=<any> REDIS_URL=redis://127.0.0.1:6391/0 SMS_GATEWAY_ENABLED=1 SMS_SITE_LINES_ENABLED=1 BILLING_ENABLED=1 ACCOUNT_EMAIL_CHANGE_HARDENED=1 SITE_PARENT_HOST=negosyotest.com API_ALLOWED_ORIGINS=http://localhost:3000 DATABASE_URL=sqlite:///proof.sqlite3`. Then `manage.py migrate` and `manage.py runserver 127.0.0.1:8765 --noreload`. Stop only the Redis you started (`redis-cli -p 6391 shutdown nosave`).
2. **Two Sites, two owners.** Sign each owner in with the email code (local: the code is printed in the server log) and `POST /v1/sites/` `{"template":"sante","subdomain":"test-isolation-a","display_name":"Test Isolation A"}`, then the same for B. A new Site starts its trial, so it has a line.
3. **Platform phone.** `manage.py sms_device add --name proof-platform --actor STAFF_USER` (prints its token once).
4. **Pair.** Each owner: `POST /v1/sites/{site_id}/sms-gateway/register/`; keep the `token`. Check `GET` shows `connection: waiting` and the other owner's `GET` of this Site is 404.
5. **Queue texts, send nothing.** In `manage.py shell` (same environment): for A, B and the platform create an `SmsMessage(category="status", priority=STATUS, site=<site or None>, expires_at=now+30 min, recipient="+639171234567" (a fake number, local only: never use it on staging), purpose="proof")` and `sms.hold_text(message, "TEXT-FOR-SITE-A", 1800)` (B, PLATFORM likewise). Nothing leaves: the text is only handed to the polling phone.
6. **Poll as each phone.** `curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:8765/gateway/v1/poll`, once with each token, then `report` the ids as `sent`.
7. **Expect:** the A phone is handed only `TEXT-FOR-SITE-A`; the B phone only `TEXT-FOR-SITE-B`; the platform phone only `TEXT-FOR-PLATFORM`, and none of the Site texts. A second text queued for B goes to B's phone only. B's `GET` and `disable` of A's phone answer 404 and A's phone stays enabled.
8. **Rotate.** Owner A: `POST /v1/account/step-up/` `{"channel":"email","purpose":"sms_line_rotate"}`, then `POST .../rotate/` `{"step_up_code":"<code>"}`. The old A token polls 401, the new one 200, B's still 200; a wrong code is 400 `step_up_required`.
9. **Audit and logs.** `AccountAuditEvent` rows `sms_line_register` (twice) and `sms_line_rotate`, each `{site, device}`; no token in the server log.

Locally, leave nothing behind: delete the SQLite file and stop only the Redis you started.

#### On staging

Needs the Manager's go. Staging already has the live NegosyoOnline phone polling, and a text it claims is really sent. So:

- **Skip step 3.** Never add a "proof-platform" phone, and no session holds or uses the live phone's token.
- **Queue Site A and Site B texts only, never a platform text.** A platform text would be claimed by the live phone and sent to the recipient. Queue the two Site texts as in step 5, but with a non-routable test recipient agreed with the Manager (not the number in step 5) and a short expiry (for example 5 minutes), so a text that is not handed out simply expires.
- **Simulate only the two Site phones**, with `curl` as in step 6, using the two owner-paired tokens. Do not poll with any other token.
- **The platform-side proof is read-only.** Read the two `SmsMessage` rows: each is `queued` or handed to its own Site's phone (`device` is that Site's phone, `dispatched` or `sent` after the simulated report), and neither ever shows the live phone as its `device`. Also check that each simulated phone received only its own Site's text (step 7, without the platform text).
- Steps 2, 4, 8 and 9 run as written, with test Sites named "Test Isolation A" and "Test Isolation B".
- **Afterwards:** `POST .../disable/` for both test phones, then remove the two test Sites with the existing `dispose_account` for each test owner account (dry run first; see `docs/runbooks/retention.md`), under the Manager's go. Never in production.

## Member one-time codes

A Santé member's code goes by SMS through `send_sms` with no email fallback: `member_signin_code` (signing in by mobile) and `member_mobile_verify` (confirming the mobile after the first order). Both are codes: they count against the platform limit only, never a site limit, and are never paced. The code is sent only while `MEMBERS_ENABLED` and `SMS_GATEWAY_ENABLED` are both on; with the gateway off a sign-in by mobile answers 503 `sms_unavailable` for every input.

- Paused or the platform limit reached: 503 `sms_paused`, checked before any member lookup, so it is the same for every input.
- Phone offline: a member with a verified email is offered the email (sign-in only; the onboarding mobile never is, because it proves the phone). Otherwise the answer is the neutral one for a signed-out request and 503 `sms_unavailable` for an onboarding code.
- The text names the store, never NegosyoOnline. A store name that is not plain GSM-7, holds a web address or pushes the text past 160 characters is replaced by "Your store".
- A disposed member's number is never texted. The code rows are deleted 30 days after they expire (TWCinc's R-CRED rule, `expired_member_challenges`); the send records follow the 365-day rule below.
- Local test: the fake phone below prints the exact text.

## Local development

No phone is needed. With `DJANGO_DEBUG=true` and `SMS_GATEWAY_ENABLED=1` in the ignored `.env`, and the server running in one process:

```bash
uv run python manage.py sms_device add --name system --actor USERNAME
```

```bash
uv run python manage.py sms_fake_phone --token TOKEN --url http://127.0.0.1:8000
```

`sms_fake_phone` polls like the phone, prints each text in its terminal instead of sending it, and reports it sent. It refuses to run unless `DEBUG` is on.

The app's debug build can take the fake phone's place on an Android emulator: `adb reverse tcp:8000 tcp:8000`, then `http://127.0.0.1:8000` as the address in the app. Only the debug build accepts an `http` address, and only that one. The emulator has no real SIM, so no text leaves the computer. Run one or the other, not both: a text is handed out once, and only to the system phone.

## Retention

- `sms_messages` deletes SMS send records 365 days after they were written. `mobile_challenges` deletes mobile verification challenges 24 hours after they expired. Both are in `apps/notifications/retention.py`.
- Neither rule is behind `RETENTION_ENABLED`. While `SMS_GATEWAY_ENABLED` is off, both do nothing and read no table.
- Until a beat service exists, the phone's `poll` starts the run: the first `poll` of each Manila day runs these two rules once, in the background, and writes one `retention_run` audit entry. A day with no `poll` has no run; the next run removes everything that is due. The daily start covers these two rules only: every other retention rule stays manual until slice 4.
- The same daily start also runs `inbox_messages` (`apps/inbox/retention.py`), which deletes SMS inbox messages 365 days after they were stored and runs wherever the inbox tables exist: see "Incoming texts" above.
- To run or count by hand, see `docs/runbooks/retention.md`.
