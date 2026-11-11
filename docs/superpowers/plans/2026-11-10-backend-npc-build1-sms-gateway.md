# NPC Build 1: SMS gateway and mobile verification (plan)

Date: 2026-11-10. ClickUp task: z8uq9md33v (https://app.clickup.com/t/z8uq9md33v), parent z8uq9mbreu.
Design: `docs/superpowers/specs/2026-11-10-sms-gateway-mobile-verification-design.md` ("the Design"; D1 to D14 are its decisions).
Status: **the Design and this delivery order are approved. Nothing is built.** Coach Ed, 2026-11-11, in the coordinator session: "Approve the design", answering "Approve the SMS gateway design so the build can start?" on pull request 42 at `50d4ec6`. The approval covers the Design and the five pull requests of section 3, in the order of section 11. It does **not** cover, and each needs his own approval when the build reaches it: creating migration M1, creating migration M2, the Railway variable `SMS_GATEWAY_ENABLED`, creating the Android app's repository, and turning incoming texts on for any phone (section 6). No code, migration, dependency, variable or repository exists yet. The Design's D1 to D13 are all settled; its section 2.1 holds every ruling with its source. D14 (how long inbox text is kept) is open, but belongs to the add-on and does not hold this build up.

## 1. Sources

The Design's section 1. The Design was written against backend `e85e727` and frontend `ab852d3`. As of 2026-11-11, backend `main` is `4adeeed`: pull request 43 merged, which changed `apps/sites` only and nothing this plan touches. Frontend `main` is `48eff85`; the dashboard files named in the Design were not read again at that commit, and the builder re-reads them before PR 3. Open pull requests on 2026-11-11: backend 42 (this plan's own); frontend none.

## 2. Scope, traced

### 2.1 Deliverables

| # | Task item | Design section | Delivered in |
|---|---|---|---|
| 1 | A1: one send function every app uses | 6.1, 6.2, 6.3, 6.5 | PR 1 |
| 2 | A1: the gateway the phone talks to | 5 | PR 2 |
| 3 | A1: the phone app | 4 | PR 5 (new repository) |
| 3a | Ruling of 2026-11-10: the app can read incoming texts (D13 (a), confirmed 2026-11-11) | 5.6 | PR 1 (the per-phone switch), PR 2 (the `received` call and the `incoming` command), PR 5 (the app's receiver). Switched off for the system phone; nothing is stored |
| 3b | Ruling of 2026-11-11: codes at once, inbox replies ahead of marketing, marketing paced at a random 18 to 25 seconds | 6.7 | PR 1 (the sending class on each purpose and record), PR 2 (handout order and the gap in the `poll` answer), PR 5 (the app obeys the gap). Only the `code` class is used; no reply and no marketing text can be sent |
| 3c | Ruling of 2026-11-11: inbox conversation text is stored, in its own table | 14 | **Designed only.** No pull request of this plan creates the table or stores a text. Section 12 lists what the add-on adds |
| 4 | A2: mobile verification for every Philippine number; `mobile` in `GET /v1/account/` | 7 | PR 3 |
| 5 | A3: daily limits per site and platform, kill switch, email fallback | 8.1 to 8.3 | PR 1 |
| 6 | A3: masked numbers in the admin | 8.4 | PR 1 (send records, phones, settings), PR 3 (account mobile) |
| 7 | A4: send record with no text | 6.1, 9.1 | PR 1 |
| 8 | A4: 1-year retention job | 9.2, 9.3 | PR 4 |

### 2.2 Acceptance criteria

| Task "Done when" | How it is met | Proved by |
|---|---|---|
| Texts reach real Globe numbers through the NegosyoOnline gateway | The phone app collects a queued text and sends it through the SIM | Stage 6 on staging, by Coach Ed, with his own numbers (one Globe, one other network) |
| Mobile verification works on Account Settings | `start/`, `verify/` and the `mobile` object match what `account.js` sends and reads | T-MOB tests; local check (section 9); Stage 6 |
| Caps, kill switch and masking exist | `SmsPolicy`, the counters, the admin lists | T-SEND, T-ADM tests; local check |
| No message text stored | `SmsMessage` has no text column; the cache holds ciphertext with a lifetime | T-SEND-9 to 11; T-BND-1 |
| Retention job removes send records after 1 year | The `sms_messages` rule and `run_retention` | T-RET tests |

### 2.3 Boundaries

Not built: the Design's section 11, which includes creating the inbox table and storing, showing or acting on incoming texts (the Design's section 14 designs the storage; the add-on z8uq9mb19f builds it), any reply or marketing text, and `GET /v1/account/mobile/` (D8). Also not touched: `apps/sites` (one reserved-label change is listed for its owner in section 7), billing, any frontend file, any filing or policy text, `railway.toml`, CI.

## 3. Pull requests

Each is small, has its own tests, and is opened only after the branch is updated from `origin/main` and the open pull requests are listed again. Branch names: `feat/z8uq9md33v-<topic>`. Nothing is reachable by a subscriber or a phone until `SMS_GATEWAY_ENABLED` is set, so each PR can merge and deploy dark.

### PR 1: send records, settings and the send function (no endpoint)

| File | Change |
|---|---|
| `apps/notifications/models.py` | `SmsMessage`, `SmsDevice`, `SmsPolicy` (Design 6.1). `SmsMessage` includes `priority`, the sending class (Design 6.7), and has no text column. `SmsDevice` includes the switch "forwards incoming texts", off by default (Design 5.6) |
| `apps/notifications/migrations/0002_…` | **Migration M1** (approval A1) |
| `apps/notifications/sms.py` (new) | The purposes list, each purpose with its category and sending class; limits and counters; the kill switch; holding and taking the encrypted text, as two functions keyed by the send record, so the add-on can give inbox text a second source (Design 6.3); picking the phone |
| `apps/notifications/services.py` | `send_sms`, beside `send_email` |
| `apps/notifications/admin.py` | Masked read-only lists for `SmsMessage` and `SmsDevice`; the one `SmsPolicy` row, audited on change |
| `apps/operators/audit.py` | Declares `sms_policy_change` (class `KEPT`). One line in the registry, as that file's comment asks |
| `config/settings.py` | `SMS_GATEWAY_ENABLED` (approval A3) |
| `.env.example`, `README.md` | The variable; one line on the gateway |
| `apps/notifications/tests/` | T-SEND, T-ADM (today's `tests.py` moves to `tests/test_email.py` unchanged) |

### PR 2: the gateway calls and the phone's identity

| File | Change |
|---|---|
| `apps/notifications/gateway.py` (new) | `poll`, `report` and `received` views; token check; expiry and no-report sweeps; presence. `poll` hands texts out by sending class, then oldest first, each with its gap (0 for a code); a full answer tells the phone to ask again at once; it also answers the phone's incoming switch (Design 6.7). `received` takes the final shape of Design 5.6 (a list of up to 20 texts, each with the app's id), checks the token, and calls one storing function, which in this task discards: it answers 204 and stores and logs nothing, whatever the switch says. The add-on replaces that one function's body |
| `config/urls.py` | Three paths under `gateway/v1/` (`poll`, `report`, `received`), before `v1/` |
| `apps/notifications/management/commands/sms_device.py` (new) | `add`, `rotate`, `disable`, `incoming on|off`, `list`; each but `list` needs `--actor` and is audited |
| `apps/notifications/management/commands/sms_fake_phone.py` (new) | Local development only: polls like a phone and prints each text in the terminal instead of sending it. Refuses to run unless `DEBUG` is on |
| `apps/operators/audit.py` | Declares `sms_device_add`, `sms_device_rotate`, `sms_device_disable`, `sms_device_incoming` (`KEPT`) |
| `docs/runbooks/sms-gateway.md` (new) | Adding a phone, rotating its token, stopping sending, what to do when the phone is offline, release order; that the incoming switch stays off for the system phone, and the filing check required before it is ever turned on |
| `apps/notifications/tests/` | T-GW, T-CMD |

No migration. No variable.

### PR 3: mobile verification

| File | Change |
|---|---|
| `apps/accounts/models.py` | `AccountMobile`, `MobileOtpChallenge` (Design 7.1) |
| `apps/accounts/migrations/<next>_…` | **Migration M2** (approval A2; number and order agreed through the coordinator first) |
| `apps/accounts/mobile.py` (new) | Start, verify, the `mobile` object, the change notice by email only (D7) |
| `apps/accounts/api.py` | `POST account/mobile/start/`, `POST account/mobile/verify/`; `settings_out` returns the `mobile` object; a typed schema replaces `dict | None`. No `GET account/mobile/` (D8) |
| `apps/accounts/admin.py` | Masked read-only lists for the two tables |
| `apps/accounts/tests/` | T-MOB |
| `docs/superpowers/specs/2026-11-10-backend-mvp-scope-design.md` | The Design's section 13 changes |

No variable. Depends on PR 1 (it calls `send_sms`). It does not depend on PR 2 to be tested: tests and the local check use the fake phone or read the queue directly.

### PR 4: retention

| File | Change |
|---|---|
| `apps/common/retention.py` (new) | The rule registry, `run_all`, and the once-a-day trigger (Design 9.3) |
| `apps/common/management/commands/run_retention.py` (new) | `--dry-run`, `--only NAME`; prints counts; writes the run log |
| `apps/notifications/apps.py`, `apps/accounts/apps.py` | Each registers its rule in `ready()` |
| `apps/notifications/gateway.py` | Calls the once-a-day trigger from `poll` (D5 (a), settled) |
| `apps/operators/audit.py` | Declares `retention_run` (`KEPT`) |
| `apps/common/tests/` | T-RET |

No migration. No variable. **Shared with Build 7 (z8uq9mdwdy):** if Build 7 merges first, it creates `apps/common/retention.py` and the command, and this PR shrinks to the two rule registrations.

### PR 5: the phone app (new repository, D12)

Ruled on 2026-11-10: our own small app (D1), which asks the server (D2, polling, no Firebase), in a new repository (D12). Kotlin, one module, no third-party service. Its own short plan is written once Coach Ed has created the repository; the outline:

- A foreground service that calls `poll` on the server's "seconds to wait", sends each text with the platform's SMS interface, one at a time, and calls `report`. It adds no wait of its own: a text with gap 0 goes at once, and a text with a gap goes no sooner than that many seconds after the last text that carried one (Design 6.7). When an answer is full it asks again at once.
- A receiver for arriving texts, **disabled unless the last `poll` answer said this phone's switch is on**. Disabled, it forwards nothing and keeps nothing. Enabled, it gives each arriving text an id, passes it to `received` in the final shape of the Design's 5.6, keeps it in encrypted storage only until the server answers 204, and tries again if no answer comes. It never reads the texts already stored on the phone (Design 5.6).
- One screen: server address, token (typed or scanned), a start and stop switch, last contact time, texts sent today, last error.
- Token kept in Android's encrypted storage. Starts again after a restart. Asks only for the permissions in the Design's 5.1: send texts, receive texts as they arrive, network, background, start after a restart.
- Unit tests for the protocol client, the send loop (order, the gap, a code never waiting), and the receiver's on and off states and its retry, against a fake server. A signed release file built on Coach Ed's computer or in CI, never committed.
- Phone set-up notes, kept in that repository: the app is installed from a file, not from Google Play; the SMS permissions are granted by hand, and on recent Android versions that can mean first allowing restricted settings for the app and answering a Google Play Protect warning. The exact steps are proven on the chosen phone when it is set up (Design 5.6 and 12), not assumed.

### Stage 6: staging, each step its own approval

1. Apply M1 and M2 on staging (`docs/runbooks/migrations.md`).
2. Confirm `REDIS_URL` is set on staging; set `SMS_GATEWAY_ENABLED=1`.
3. `sms_device add` on staging; Coach Ed enters the token on the phone. `sms_device list` shows the incoming switch off, and it stays off.
4. Coach Ed verifies his own number on `https://dash-staging.negosyonline.com/account/`, with a Globe number and a number on another network.
5. Check in the Django admin: the two send records, numbers masked, no text anywhere; stop sending with the kill switch and see the "try again shortly" message; start it again.
6. Turn the phone's internet off, ask for a code, and see the "not available" message within a minute.

Production is not built until the MVP is complete; nothing here touches it.

## 4. Migrations

| # | App | Creates | Changes existing tables | Depends on |
|---|---|---|---|---|
| M1 | `apps/notifications`, next free number (`0002` as of `e85e727`) | `SmsMessage` (with the sending class), `SmsDevice` (with the incoming switch, off by default), `SmsPolicy`. No table or column for any message text: the inbox tables are the add-on's own migration | No | `notifications.0001`; `sites.0001` for the optional site link |
| M2 | `apps/accounts`, next free number (`0003` as of `e85e727`) | `AccountMobile`, `MobileOtpChallenge` | No | `accounts.0002` only |

- **M2 is in the app whose migrations another session owns.** Before PR 3 is opened, the coordinator is told that an `apps/accounts` migration creating these two tables is coming and confirms no other open branch adds one; the number is taken then, not now.
- M2 holds no foreign key to `apps/notifications` (the challenge stores the send record's public id as text), so M1 and M2 can be applied in either order.
- Both are additive and safe to apply before the code that uses them is switched on.

## 5. Railway variables

| Name | Meaning | Default | Needed in |
|---|---|---|---|
| `SMS_GATEWAY_ENABLED` | `1` turns on the send function, the gateway calls and mobile verification | off | PR 1 (code reads it); set on staging in Stage 6 |

Already existing and required where the gateway is on: `REDIS_URL`, `DJANGO_SECRET_KEY`. No other variable: D2 (polling) and D3 (the API host) are ruled, so no Firebase secret and no host variable. The incoming switch is a field on the phone's row, not a variable.

## 6. Approvals

### 6.1 Before the code that needs each one is written (Coach Ed, relayed by the coordinator)

- **A0. The Design as a whole. Approved**, Coach Ed, 2026-11-11: "Approve the design".
- **A1. Migration M1**: creating the file, and applying it to the builder's local SQLite file for the local check.
- **A2. Migration M2**: the same, after the coordinator agrees the number and order with the session that owns `apps/accounts` migrations.
- **A3. Variable `SMS_GATEWAY_ENABLED`**: adding it to the code and `.env.example`, and setting it to `1` in the builder's ignored local `.env`.
- **A4. Creating the new private repository for the phone app.** That one is needed is ruled (D1, D12); creating it is Coach Ed's own action, and its name is his (the Design suggests `negosyoonline-sms-gateway-android`). PR 5 cannot start before it exists.
- **A5. Delivery in the five pull requests of section 3. Approved** with A0, the same ruling.

A1 to A4 are **not approved**. PR 1 does not start until A1 and A3 are given; PR 3 waits for A2; PR 5 waits for A4.

### 6.2 Later, each its own approval

Merging each PR; applying M1 and M2 on staging; setting `SMS_GATEWAY_ENABLED` on staging; running `sms_device` on staging; installing the app on the phone; the first real text; turning the incoming switch on for any phone (for the system phone, only after the add-on's design and the filing check in the Design's 5.6); everything for production.

### 6.3 What Coach Ed supplies

- An Android phone that can stay on a charger with a stable internet connection.
- A Globe SIM on a plan that includes texts to all networks. To check when choosing it: the plan's fair-use limit on texts, and whether Globe still blocks texts that contain links (the Design's section 12).
- Installing the app on that phone from a file and granting its SMS permissions by hand (PR 5's set-up notes).
- No Firebase project and no hostname: D2 and D3 are ruled.

## 7. Coordination

- **`apps/accounts` migration (M2):** named above; number and order through the coordinator.
- **Build 7 (z8uq9mdwdy):** shares `apps/common/retention.py`, `run_retention` and the `retention_run` audit action (PR 4). Whichever build merges first creates them.
  - **Order agreed, 2026-11-11 (the coordinator, with the NPC coordinator):** Build 7 creates the shared piece exactly as the Design's 9.3 names it: the `apps/common/retention.py` registry with `run_all`, `manage.py run_retention [--dry-run] [--only NAME]`, and the `retention_run` audit entry. PR 4 of this plan then only registers the two SMS rules and adds the once-a-day start from `poll`. PR 4 is held until Build 7's pull request is merged, and does not create those files. PR 2 and PR 3 do not wait for it.
- **`apps/operators/audit.py`:** PRs 1, 2 and 4 each add lines to the `ACTIONS` registry. Build 7 will add its own. A conflict there is resolved by keeping both sides' lines.
- **`apps/sites/subdomains.py`:** the labels `sms` and `gateway` should be reserved (Design 5.2). The file belongs to the session that owns slice 3; this build does not change it and asks the coordinator to route it.
- **Frontend, a separate task that needs its own ClickUp task (not part of this build):** D7 is ruled email only, so the sentences in `apps/dash/public/assets/js/account.js` that promise a text to the old number (the security note, the dialog note and the "Security alerts" line "Sent to your email and mobile") need a wording change. Until it is done the dashboard promises a text that is not sent. D8 is settled as not built, so `earnings.js` keeps calling a route that answers 404, which it already handles.
- **The add-on (z8uq9mb19f):** section 12. Its description differs from Coach Ed's ruling of 2026-11-11 on the pace and on replies; the Design's 6.7 lists the differences for the add-on's own design.
- **The scope doc, with Build 9 (z8uq9mdxvt):** that build's builder will later edit the scope doc's section 4 and lines 193 and 197 and append a dated entry to section 12. This plan's pull requests also append to section 12. Agreed rule: the second pull request to merge updates from `main` first and keeps both entries.
- **Shared files:** `config/settings.py`, `config/urls.py`, `README.md`, `.env.example`, the scope doc. Before each PR: fetch `origin/main`, update the branch, re-run the tests and checks, list open PRs with their files, and report any overlap to the coordinator first.

## 8. What changes if Coach Ed rules otherwise

No decision is open, so nothing here is pending. If he changes a daily limit, it is a default value in PR 1 and, after release, a field the admin edits; no code changes. The limits start at 2,000 a day per site and 2,000 a day for the platform (the Design's 8.1; changed from 4,000 on 2026-11-11 by migration `notifications.0003`).

## 9. Tests

Run with `uv run python manage.py test`, then `manage.py check`, `makemigrations --check --dry-run`, and `check --deploy` with `DJANGO_DEBUG=false`. Test first at each step.

- **T-SEND** 1 an undeclared purpose is refused; 2 a `status` text without a site or without an email fallback is a programming error; 3 a text over 160 characters or containing a link is refused; 4 gateway off: refused `gateway_off`, nothing queued; 5 sending stopped: refused `paused`; 6 platform limit reached: a `code` text is refused, a `status` text is emailed; 7 site limit reached: emailed, and another site is unaffected (the tests set small limits; the defaults are 2,000 and 2,000); 8 counts follow the Manila day and are rebuilt from the records when the cache is empty; 9 the record has no text in any field; 10 the cache value is not the plain text and expires with the text; 11 no log line holds a number or a text; 12 phone offline: `code` refused `gateway_offline`, `status` emailed; 13 the fallback email failing leaves the record refused and logs an error; 14 each record carries the sending class of its purpose, and `mobile_verify` is `code`; 15 outside the tests no purpose of class `reply` or `marketing` is declared.
- **T-GW** 1 no token, a wrong token, a rotated token and a disabled phone all get the same 401; 2 failed tries are limited per IP; 3 `poll` hands out at most 5, oldest first, and marks them `dispatched`; 4 a second `poll` does not hand the same text out again; 5 the text is gone from the cache after handout; 6 an expired text is never handed out and becomes `expired`; 7 a missing cache entry gives `failed` `text_lost`; 8 `report` sets `sent` or `failed`, and repeating it changes nothing; 9 a report for another phone's text is refused; 10 no report after 10 minutes gives `failed` `no_report`; 11 a failed or expired text with a fallback is emailed once; 12 gateway off: all three calls answer 404; 13 sending stopped: `poll` hands out nothing and asks the phone to wait longer; 14 `poll` updates last contact and presence; 15 the calls are not in `/v1/openapi.json`; 16 `poll` answers the phone's incoming switch, off for a new phone; 17 `received` answers 204 with the switch off and with it on, and writes no record, no cache entry and no log line holding a number or a text; 18 `received` with no token or a wrong one gets the same 401 as the other calls; 19 `poll` hands out a code before a reply, a reply before a status text, and a status text before a marketing text, whatever order they were queued in (classes declared only in the tests); 20 every text in a `poll` answer carries a gap, 0 for a code, and between 18 and 25 seconds for a marketing text; 21 a phone is handed one gap-carrying text at a time, and codes are still handed out while it is unreported; 22 a full `poll` answer tells the phone to ask again at once; 23 `received` sent twice with the same ids answers 204 both times.
- **T-CMD** 1 `add` prints the token once and stores only its hash; 2 `rotate` makes the old token fail; 3 `disable` stops the phone; 4 `list` writes no audit row; 5 every other action writes one audit row with the actor; 6 `sms_fake_phone` refuses to run without `DEBUG`; 7 `incoming on` and `incoming off` set the switch, each writes one `sms_device_incoming` audit row with the actor, and `list` shows the switch.
- **T-MOB** 1 every form `normalise_ph_mobile` accepts is accepted, including the dashboard's `+63` form; 2 a bad number answers `invalid_mobile` with `fields.mobile`; 3 the four start limits; 4 `start` answers `{"queued": true}` and queues one `mobile_verify` text; 5 a new `start` supersedes the old challenge; 6 the right code answers `{"verified": true, "mobile_masked": "+63917***4567"}`; 7 wrong, expired, used and sixth-try codes each give their message under `fields.code`; 8 `GET /v1/account/` shows the object, verified and not; 9 the full number is in no response; 10 changing a verified number emails the notice and texts nobody; `GET /v1/account/mobile/` answers 404; 11 gateway off or offline answers 503 `sms_unavailable`; stopped or at the limit answers 503 `sms_paused`; 12 signed-out calls answer 401; 13 the audit event holds no number.
- **T-ADM** 1 each list shows the number masked; 2 the detail page shows it masked; 3 nothing can be added, changed or deleted except the `SmsPolicy` row; 4 changing `SmsPolicy` writes an audit row with before and after; 5 no token or code hash is shown.
- **T-RET** 1 a rule that is not registered cannot run; 2 the dry run deletes nothing and reports the same counts; 3 `sms_messages` deletes a row of 366 days and keeps one of 364; 4 `mobile_challenges` deletes 24 hours after expiry; 5 the run writes one audit row with the counts; 6 the once-a-day trigger runs once however many calls arrive, and again the next day.
- **T-BND** 1 no model in `apps/notifications` or `apps/accounts` has a field that could hold a text body; 2 `apps/accounts` writes `SmsMessage` only through `send_sms`.

## 10. Local review checklist (Coach Ed), after the approvals and PRs 1 to 3

Environment: local, `http`, this repository's `runserver` and the dashboard's local server, SQLite, `SMS_GATEWAY_ENABLED=1` in the ignored `.env`, a local Redis or the local cache with one process. No phone is needed: `manage.py sms_fake_phone` plays the phone and prints each code in its terminal. The builder runs every row first and reports what was seen, with the exact ports.

| Step | Where | What to see |
|---|---|---|
| Add a mobile | `http://dash.negosyotest.com:<port>/account/`, "Add mobile" | The dialog; a bad number shows "Enter a valid Philippine mobile number."; a good one moves to the code step |
| Code | The fake phone's terminal | "Your NegosyoOnline code is ... It expires in 10 minutes. Do not share it." |
| Confirm | The dialog | A wrong code shows "That code did not work. Please try again."; the right one closes the dialog, shows the number as "+63 9XX ••• XXXX" with "Verified", and the "Mobile changed" toast |
| Change it | "Change mobile", a second number | The same steps; the change notice email appears in the `runserver` output; no second text |
| Phone offline | Stop the fake phone, wait a minute, ask for a code | "Text messages are not available right now. Please try again later." |
| Kill switch | `http://ggwp.negosyotest.com:<port>/admin/`, SMS settings, tick "sending stopped"; ask for a code | "Please try again shortly."; untick and it works again |
| Admin lists | Same admin: SMS messages, account mobiles | Numbers as `+63917***4567` in the list and on the detail page; no text anywhere |
| Retention | `manage.py run_retention --dry-run` | The two rules with their counts; nothing deleted |

Desktop and phone widths are the dashboard's own, already built; this build changes no page.

## 11. Build order after approval

PR 1, then PR 2 and PR 3 (either order; both need PR 1), then PR 4, then PR 5 in its own repository, then Stage 6. The phone and SIM are needed only for Stage 6; everything before it is built and checked with the fake phone.

## 12. What the add-on (z8uq9mb19f) adds later

From the Design's 14.3, so that nothing in PRs 1 to 5 is redone. None of it is in this plan, and each item needs its own approval in that task.

1. **The NPC wording check first** (Design 14.4): register row 6, filing-store-sms, filing-subscriber-accounts-billing, filing-subscriber-stores, PIA 5, privacy manual s8, and the Terms and Privacy provider list. They must be checked, and changed where needed by Coach Ed, before any inbox text is stored and before any reply or marketing text is sent. This build edits none of them.
2. **Its own migration**: the two inbox tables of the Design's 14.2 and the phone-to-site assignment. M1 and M2 are not changed.
3. **The storing step**: the body of the one function `received` calls, and a refusal to turn the switch on for a phone with no site.
4. **Turning the switch on**, per dedicated phone, with `sms_device incoming on`. Never for the system phone without a separate ruling.
5. **The `reply` and `marketing` purposes**, their text source, the page count and the pace.
6. **The retention rule for inbox text**, after D14 is ruled (the Parent states both "SMS records 1 year" and "message records and support email 2 years"; which applies is not settled).
7. The inbox screens, replies, broadcasts, billing and SIM custody.

Unchanged by all of it: the phone app, the three gateway calls, the phone's token, `SmsMessage`, `SmsPolicy` and mobile verification.
