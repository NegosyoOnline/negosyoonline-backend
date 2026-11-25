# Store bulk SMS: BE API contract (for the dash FE)

ClickUp tasks z8uq9mj4uh (bulk) and z8uq9mj9q4 (contacts and schedule). Matches the code on main (79bfc1a).
Plan and Coach Ed's D1-D4 ruling: `docs/plans/store-bulk-sms-plan-2026-11-23.md`.

## Switch and auth

- Every route is under `/v1/sites/{site_id}/inquiries/sms/bulk/` and uses the same sign-in and site checks as the
  Inbox SMS routes (404 for a site that is not the account's or not a Sante site; 403 `billing_only` for a
  billing-only site; 401 signed out).
- **404 `not_found` on every bulk route** unless `STORE_BULK_SMS_ENABLED` is on AND `INQUIRY_SMS_ENABLED`,
  `SMS_GATEWAY_ENABLED`, `SMS_SITE_LINES_ENABLED` are on AND the tables exist. Off, the FE hides the button
  (treat a 404 on `GET .../bulk/status/` as "feature off").
- Errors use the standard shape `{"error": {"code", "message", "fields"?}}`. A malformed body (wrong type,
  unknown key) is 400 `invalid_request`. Text problems and bad values are 422 `invalid`.
- Keys: `idempotency_key` matches `^[A-Za-z0-9_-]{8,64}$` (else 422 `invalid`, field `idempotency_key`).
- Nothing in any answer carries a full phone number. Numbers are always masked like `+63917***4567`.

## Rules (ruled by Coach Ed, 2026-11-23; the FE must not add anything on top)

- The audience is `all` ("All my members") or `pick` (chosen groups and contacts, see Audience below). There is no
  promo filter or promo label (Coach Ed, 2026-11-24). No consent warning, nudge or extra confirm wording.
- Always left out: unverified mobiles, test numbers (+63900...), numbers on the store's own STOP list, repeats.
- The text: one SMS of at most 160 plain characters, all typed by the owner. There is **no store-name prefix** and
  **nothing is added at the end** (no STOP footer) (Coach Ed, NO-SMS-PREFIX). No web link (422).
- Sent from the store's own paired phone only, one text every 18 to 25 seconds, only 08:00 to 20:00 Manila time
  (a batch waits overnight and goes on the next morning), at most `daily_limit` bulk texts a day per store
  (default 2,000, HQ-editable; a test text counts as one). A batch that meets the cap waits for the next day.
- One active batch (state `waiting` or `sending`) per store.
- No cost line from NegosyoOnline. The FE may show `cost_note` from the status.

## GET `.../bulk/status/`

```json
{
  "available": true,
  "blocked": "",                      // "" | "site_held" | "sms_off" | "replies_off" | "no_line"
  "blocked_message": "",              // fixed text for `blocked` ("" when not blocked); no_line = "Pair your phone in Settings → SMS Gateway to text many members."
  "phone_online": true,               // the store's own paired phone polled in the last minute
  "daily_limit": 2000, "used_today": 0, "left_today": 2000,
  "prefix": "", "max_length": 160,    // prefix is always "" (kept for old clients); max_length = the whole text
  "window": {"start": "08:00", "end": "20:00", "timezone": "Asia/Manila"},
  "in_window": true,                  // right now
  "seconds_between": {"min": 18, "max": 25},
  "cost_note": "Texts go out from your own phone and SIM. Your carrier's normal SMS rates apply.",
  "active_batch": null                // or a batch object (below)
}
```
`blocked` != "" means a new batch is refused with the same code (409). The FE should show `blocked_message`
and disable "Text many members" (for `no_line`, with the pairing hint).

## Audience (`audience`, `groups`, `contacts`)

Used by audience, confirm and the schedule routes. `audience` is required: `all` or `pick`. A missing, null or blank audience is refused (400 `invalid_audience`, `fields.audience`); nothing defaults to all.
For `pick`, send `groups` and/or `contacts`: lists of public ids (strings, at most 20,000 each). The members are the
contacts in the picked groups plus the picked contacts, of this store only, each counted once. At least one
group or contact is needed. For `all`, `groups` and `contacts` are ignored.
400 `invalid_audience` for: any other audience value, `pick` with nothing picked, an id that is not this store's
group or contact, a bad list, or the store-contacts switch being off.

## POST `.../bulk/audience/`  `{ "audience": "all" }` or `{ "audience": "pick", "groups": [...], "contacts": [...] }`

Reads only; never a number or a list.
```json
{
  "audience": "all",
  "total_members": 120,
  "will_send": 95,
  "left_out": {"unverified": 10, "invalid_number": 0, "opted_out": 5, "duplicates": 0}
}
```
Every member is counted once, under the first reason
that applies, in the order shown. `will_send + sum(left_out) = total_members` (for `pick`, `total_members` is the
number of picked contacts).

## POST `.../bulk/preview/`  `{ "text": "Sale this weekend, 20% off." }`

```json
{ "text": "Sale this weekend, 20% off.", "length": 27, "max_length": 160, "remaining": 133 }
```
The exact text as it will arrive (as typed, trimmed). 422 `invalid` (field `text`) for empty, too long, characters outside plain
SMS, or a web link.

## POST `.../bulk/test/`  `{ "text": "...", "idempotency_key": "..." }`

Sends the same text once to the owner's own verified account mobile, from the store's phone. Counts as one bulk
text. The same key sends once. Answer: `{"state": "queued", "repeated": false}` (`repeated` true for a repeat key).
Errors: 409 `site_held` | `sms_off` | `replies_off` | `no_line` | `site_phone_offline` | `bulk_limit` | `no_mobile`
(account has no verified mobile) | `paused`; 422 `invalid` for a bad text.

## POST `.../bulk/batches/`  (confirm)

```json
{ "text": "...", "audience": "all", "idempotency_key": "...",
  "confirm": true, "expected_count": 95 }
```
For `pick`, add `groups` and/or `contacts`. Unknown keys are a 400 `invalid_request`.
- `confirm` must be `true` (422 `invalid`, field `confirm`). `expected_count` is optional: the `will_send` the owner
  saw; if the count is different now, 409 `audience_changed` with `{"error": {"code", "message", "will_send": 97}}`.
- 201 with the batch object when created; **200 with the same batch** when the same `idempotency_key` is sent
  again (no second batch).
- Errors: 409 `site_held` | `sms_off` | `replies_off` | `no_line` | `batch_running` | `empty_audience` |
  `audience_changed`; 400 `invalid_audience`; 422 `invalid` (text, confirm, idempotency_key, or a negative
  `expected_count`); 403 `billing_only`.

## Batch object

```json
{
  "id": "sbk_...", "state": "waiting",       // waiting | sending | finished | stopped | ended | scheduled | cancelled
  "created_at": "2026-11-23T10:00:00+08:00",
  "scheduled_at": null,                        // ISO time for a scheduled batch (see Schedule SMS), else null
  "audience": "all",                           // all | pick
  "text": "Sale this weekend.",                // as it arrives; kept 180 days
  "size": 95, "sent": 0, "failed": 0, "not_sent": 0, "waiting": 95,
  "left_out": {"unverified": 10, "invalid_number": 0, "opted_out": 5, "duplicates": 0},
  "end_reason": "",                            // "" | "stopped" | "site_held" | "billing_only" | "line_disabled" | scheduled-start codes (see Schedule SMS)
  "end_message": "",                           // fixed text for end_reason
  "paused_reason": "",                         // active batches only: "" | "outside_window" | "daily_cap" | "phone_offline"
  "seconds_left": 2042,                        // estimate at the mean gap; 0 when not active
  "stopped_at": null, "finished_at": null
}
```
- `waiting` (the count) = `size - sent - failed - not_sent`; it includes a text the phone holds.
- `end_reason` is set only for `stopped` (`stopped`), `ended` (`site_held`, `billing_only`, `line_disabled`, or a
  scheduled-start code) and `cancelled` (`cancelled`); a `finished` batch has `end_reason: ""`. Not-sent texts of an ended or stopped batch carry that reason.
- A batch's state `waiting` means nothing has been sent yet; `sending` means at least one went out. A batch
  outside the window or at the cap stays in its state with `paused_reason` set.

## GET `.../bulk/batches/?cursor=&limit=`

`{ "items": [batch, ...], "next_cursor": "sbk_..." | null }`, newest first, `limit` 1 to 100 (default 20). 422
`invalid_cursor` for a bad cursor. `scheduled` and `cancelled` batches are left out (use `.../bulk/scheduled/`).
Each item (and the detail answer below) also carries the picked names, see Picked names.

## GET `.../bulk/batches/{id}/?cursor=&limit=`

The batch object plus a page of its recipients, in send order:
```json
{ "...batch fields...",
  "results": [{"name": "Maria Santos", "mobile": "+63917***4567", "state": "sent", "reason": "", "reason_text": ""}],
  "next": "20" | null }
```
Recipient `state`: `waiting` | `queued` (with the phone) | `sent` | `failed` | `not_sent`. `reason` (for `failed` and
`not_sent`): `stopped`, `site_held`, `billing_only`, `line_disabled`, `member_removed`, `number_changed`,
`unverified`, `opted_out`, `text_invalid`, `not_sent`, `expired`, or a short phone reason for `failed`;
`reason_text` is the fixed text where there is one. A picked contact removed later is `contact_removed`. `limit` 1
to 100 (default 20). 404 for another store's batch, and for a `scheduled` or `cancelled` batch.

## POST `.../bulk/batches/{id}/stop/`

No body. Answer: the batch object (`state: "stopped"`, `end_reason: "stopped"`). 409 `not_running` when the batch is
not waiting or sending. Texts the phone already took may still go out and are counted when the phone reports.

## Picked names

The batch list, the batch detail and the scheduled routes add two arrays to the batch object:
```json
"groups": [{"id": "...", "name": "Regulars"}],
"contacts": [{"id": "...", "first_name": "Maria", "last_name": "Santos", "mobile_masked": "+63917***4567"}]
```
Both are `[]` for an `all` batch. Of this store only, in the picked order; an id that is gone is skipped. Never a
full number. The POST answers (confirm, stop, schedule create, cancel, PATCH) and `active_batch` carry no names.

## Schedule SMS: `.../bulk/scheduled/`

Same switches, sign-in and errors as above. A scheduled batch holds the text, audience and `scheduled_at`; no
recipients yet. Its `size` and `left_out` are a count for display, redone at start.

- **POST** `.../bulk/scheduled/`: `{ "text", "audience", "groups"?, "contacts"?, "scheduled_at", "idempotency_key" }`.
  `scheduled_at` is an ISO datetime; without a zone it is Asia/Manila time. 201 with the batch (`state: "scheduled"`);
  200 with the same batch for a repeat `idempotency_key`.
- **GET** `.../bulk/scheduled/?cursor=&limit=`: `{ "items": [batch...], "next_cursor": "sbk_..." | null }`, soonest first
  (`limit` 1 to 100, default 20; 422 `invalid_cursor`). Lists every batch with a scheduled time, so it also shows
  `cancelled` ones and ones that have started or ended.
- **GET** `.../bulk/scheduled/{id}/`: the batch with picked names. 404 for a batch with no scheduled time.
- **PATCH** `.../bulk/scheduled/{id}/`: any of `text`, `audience`, `groups`, `contacts`, `scheduled_at`; same checks,
  and the count is redone. Answers the batch. 409 `already_started` unless `state` is `scheduled`.
- **POST** `.../bulk/scheduled/{id}/cancel/`: no body. `state: "cancelled"`, `end_reason: "cancelled"`. A second cancel
  answers the same; 409 `already_started` for a batch that has started.

`scheduled_at` rules (422, field `scheduled_at`): `invalid` (not a date and time); `too_soon` (under 10 minutes
ahead); `outside_window` (not 08:00 to 20:00 Manila time); `too_far` (over 30 days ahead). Other errors: 409
`schedule_limit` (20 scheduled already), 409 `empty_audience` (nobody to send to now), 400 `invalid_audience`, 422
`invalid` (text, `idempotency_key`).

Block checks (`site_held`, `no_line` ...), the daily cap and the phone are not checked at create, only at the start.
A due batch starts from the beat task, the phone's pull or a read of the store. It becomes an ordinary `waiting`
batch (recipients made, size recounted) and then follows the rules above. If it cannot start, it becomes `ended`
with all its texts `not_sent` and one `end_reason`: `site_held`, `billing_only`, `line_disabled`, `sms_off`,
`replies_off`, `no_line`, `outside_window`, `daily_cap`, `phone_offline`, `empty_audience` (nobody left, or a picked
group or contact is gone), or `batch_running` (another batch was still sending, and the window closed). While
another batch is sending inside the window it tries again on the next run. `end_message` carries the fixed text.

## HQ cap field

`GET/PUT /v1/staff/sms/...` (the existing SMS policy answer and body) carries `store_bulk_daily_limit` (integer,
default 2000, zero or more), beside `store_daily_limit` and `all_stores_daily_limit`. A body that leaves it out
keeps it. It is read and saved only once migration notifications.0008 is applied.

## What happens to a running batch

- The store's phone polls the gateway; the BE gives it the next text only (one at a time). Each member is checked
  again just before their text: still the store's member with the same verified number, and not on the STOP list.
  A member who is not goes `not_sent` with the reason.
- Store put on hold, billing-only, or its phone line switched off: the rest are `not_sent` with that reason and
  the batch is `ended`.
- After a batch ends, its recipients' numbers are replaced by the masked form.
- Audit (account events, no text or number): `store_bulk_confirmed`, `store_bulk_stopped`, `store_bulk_ended`,
  and for schedules `store_bulk_scheduled`, `store_bulk_schedule_edited`, `store_bulk_schedule_cancelled`,
  `store_bulk_schedule_started`.
