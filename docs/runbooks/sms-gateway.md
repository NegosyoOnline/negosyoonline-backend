# SMS Gateway Runbook

NegosyoOnline sends texts through its own phone: an Android phone with a SIM, running the NegosyoOnline gateway app. The server never pushes. The phone asks the server for waiting texts every few seconds (`POST /gateway/v1/poll`), sends them through its SIM, and reports each result (`POST /gateway/v1/report`). The calls are on the API host and are described at the top of `apps/notifications/gateway.py`.

Every step below that touches staging or production needs Coach Ed's approval for that step. Nothing here is approved by this document.

## Variables

| Name | Meaning |
|---|---|
| `SMS_GATEWAY_ENABLED` | `1` turns on the send function, the three phone calls and the admin's SMS lists. Unset: no text is queued, the calls answer 404, and the SMS tables are not read or written. |
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

Phones are managed only with `manage.py sms_device`, from a terminal. No browser page can add or change one. `--actor` is the staff user running the command; every action except `list` writes an operator audit row.

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

In the admin lists on hq: SMS settings, tick "Sending stopped", save. It takes effect on the next request and needs no deploy. While it is on:

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

  Each run asks for the number: type it at the prompt, then Enter. The first run only counts; read the count, then run the second, which deletes for good. For a Philippine mobile it also deletes the send records of the staff texts sent to that number and drops one still waiting for the phone, so it is never sent; it leaves the send record of a sign-in code to that number, which goes at 365 days. If a text to that number is with the phone right now it stops and deletes nothing: run it again in 10 minutes. **The number is typed at the prompt and nowhere else: never as an argument, and never fed in through `echo`, `printf` or a here-string, which would leave it in the shell's history.** A Philippine mobile may be typed in any usual form. Any other number (a foreign mobile, a landline) is matched by its digits, so spaces, brackets and dashes do not matter, but the prefix does: try the number as the person wrote it, with and without its country code. The command works with the variable on or off, and each run writes one audit entry that holds counts and no number. On a shared environment a run with `--confirm` is done by whoever Coach Ed has answer that person's request, per request. **What it does not remove:** the copy in the phone's Messages app, which is deleted on the phone; the masked number in the audit record of each time a conversation was opened, replied to or written to, which is blanked after a year; the send record of a sign-in code sent to that number, which is deleted at 365 days; and a text that arrives afterwards.
- A text the server cannot store (one with no sender, for one) is skipped and counted in one log line, `sms_incoming_skipped count=N`, and the phone drops it: it is then only in the phone's Messages app. `sms_incoming_rejected` means the phone sent something that is not a list of texts, which the app never does. Neither line holds a number or a text.
- The commands: `sms_device incoming on|off`, which refuses `on` while the variable is off; `sms_inbox_access grant|revoke|list`, which refuses while the variable is off; `sms_inbox_delete`. Who is granted access, and whether they may send, is Coach Ed's decision, per person.
- There is no screen yet: the pages are the next plan. The staff routes exist (see "Staff texts" below). No Django admin screen shows a conversation or a text.

## Staff texts

- A staff member with send access may reply in a conversation or write a new text to one Philippine mobile. Both go through the send function: one SMS of at most 160 plain characters, no web link, no marketing.
- A staff text waits up to 1 hour for the phone, is sent after every waiting code, and leaves no sooner than 18 to 25 seconds after the last staff text. One staff text is with the phone at a time.
- 300 of the day's texts are kept for codes: a staff text is refused once the day's count reaches the limit less 300. The 300 is `RESERVED_FOR_CODES` in `apps/notifications/sms.py`; changing it is Coach Ed's ruling and a reviewed change.
- One staff user may send 60 texts in an hour.
- What is kept: the text, with who wrote it, in the inbox for 365 days; a send record with the full number and no text for 365 days; an audit entry with the masked number, blanked after a year (`admin_audit_personal_values`, see `retention.md`).
- The six routes under `/staff/v1/inbox/`, each a `POST`, are closed while `SMS_INBOX_ENABLED` is off and to a staff user without access.
- There is still no screen: the pages are the next plan.

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
