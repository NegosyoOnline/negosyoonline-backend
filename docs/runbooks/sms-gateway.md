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
4. Set up the phone's own controls ("Before a phone is added", below), then add the phone and enter its token in the app.
5. Check: `sms_device list` shows a recent `last_contact`, and the admin's SMS phones list shows the phone.

To go back, unset `SMS_GATEWAY_ENABLED`. The tables stay; nothing reads them.

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

The phone's own Messages app keeps a copy of every text the gateway app sends, with the number and the code. Android does this itself and the gateway app cannot turn it off. Coach Ed accepted this on 2026-11-11 ("Approve plan") with the two controls below in the set-up notes. Both are done on the phone, by hand, before `sms_device add`.

**Status: to be verified on the real phone. Neither step has been done; no phone exists yet.**

1. **Screen lock.** Set a screen lock on the phone and record its type (PIN, password, pattern or fingerprint) in the table below. Never record the PIN or password itself.
2. **Auto-delete of old texts.** In the phone's Messages app, turn on the setting that deletes old texts by itself, and record its period in the table below. **If the phone's Messages app has no auto-delete, stop and tell the coordinator before adding the phone:** the NPC filing wording depends on this control.

| Control | Value | Verified on the real phone |
|---|---|---|
| Phone model and Android version | not known yet | no |
| Screen lock type | not set yet | no |
| Messages app auto-delete period | not set yet | no |

A phone with a screen lock starts the gateway app again after a restart only once it has been unlocked. Until then the server sees it as offline.

### Setting up the phone and its app

The app is in its own repository, `NegosyoOnline/negosyoonline-sms-gateway`. Its checklist, `docs/phone-setup.md` there, covers the phone side: the signing key, the release build, the install, the permissions and the app's screen. This section says where the server steps fall in that checklist. No server address and no token is written in either repository.

**Status: not yet done on a real phone.** The app has run only on an Android 17 emulator against a local server. Android 8.0, a real SIM, 24 hours on the charger, and the restricted-settings and Play Protect steps of the install are unproven. The checklist's taps for the install and the permissions are written after the first real phone.

1. **The phone itself.** One SIM, no SIM PIN, kept on its charger, and the two controls in "Before a phone is added" above.
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

## Incoming texts: the switch stays off

The app can forward the texts the SIM receives, behind a per-phone switch that is **off** for every new phone:

```bash
uv run python manage.py sms_device incoming off --name system --actor USERNAME
```

- **Leave it off for the system phone.** A SIM receives texts that have nothing to do with NegosyoOnline, and the NPC filing describes a phone that only sends.
- Whatever the switch says, the server stores nothing a phone forwards: `POST /gateway/v1/received` answers 204 and drops the body unread. There is no inbox table.
- Before the switch is ever turned on for any phone, the filing and policy wording must be checked first (register row 6, filing-store-sms, filing-subscriber-accounts-billing, filing-subscriber-stores, PIA 5, privacy manual s8, the Terms and Privacy provider list), and the Dedicated SMS Line add-on must have built the inbox. Turning it on is Coach Ed's own approval.

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
- To run or count by hand, see `docs/runbooks/retention.md`.
