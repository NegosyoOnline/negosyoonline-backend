# Store bulk SMS: BE API contract (for the dash FE)

Parent ClickUp z8uq9mbreu (subtask pending). Branch `feat/z8uq9mbreu-store-bulk-sms-be`, stacked on
PROMO-CONSENT BE v1.2 (de123b6). Plan and Coach Ed's D1-D4 ruling: `docs/plans/store-bulk-sms-plan-2026-11-23.md`.

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

- Audience `all` ("All my members") is the default. `promo` ("Members who agreed to promo texts") is an optional
  filter. No consent warning, nudge or extra confirm wording.
- Always left out: unverified mobiles, test numbers (+63900...), numbers on the store's own STOP list, repeats.
- The text: one SMS (160 plain characters including the store's name in front, "Name: "), no web link (422), and
  **nothing is added at the end** (no STOP footer). `promotional` is a label stored with the batch, nothing more.
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
  "prefix": "Ana's Store: ", "max_length": 147,   // room left for the owner's words after the name
  "window": {"start": "08:00", "end": "20:00", "timezone": "Asia/Manila"},
  "in_window": true,                  // right now
  "seconds_between": {"min": 18, "max": 25},
  "cost_note": "Texts go out from your own phone and SIM. Your carrier's normal SMS rates apply.",
  "active_batch": null                // or a batch object (below)
}
```
`blocked` != "" means a new batch is refused with the same code (409). The FE should show `blocked_message`
and disable "Text many members" (for `no_line`, with the pairing hint).

## POST `.../bulk/audience/`  `{ "audience": "all" | "promo" }`

(`audience` defaults to `all`.) Reads only; never a number or a list.
```json
{
  "audience": "all",
  "total_members": 120,
  "will_send": 95,
  "left_out": {"unverified": 10, "invalid_number": 0, "opted_out": 5, "not_promo": 0, "duplicates": 0}
}
```
`not_promo` is non-zero only for `audience: "promo"`. Every member is counted once, under the first reason
that applies, in the order shown. `will_send + sum(left_out) = total_members`. 422 `invalid` for any other audience.

## POST `.../bulk/preview/`  `{ "text": "Sale this weekend, 20% off." }`

```json
{ "text": "Ana's Store: Sale this weekend, 20% off.", "length": 40, "max_length": 160, "remaining": 120 }
```
The exact text as it will arrive. 422 `invalid` (field `text`) for empty, too long, characters outside plain
SMS, or a web link.

## POST `.../bulk/test/`  `{ "text": "...", "idempotency_key": "..." }`

Sends the same text once to the owner's own verified account mobile, from the store's phone. Counts as one bulk
text. The same key sends once. Answer: `{"state": "queued", "repeated": false}` (`repeated` true for a repeat key).
Errors: 409 `site_held` | `sms_off` | `replies_off` | `no_line` | `site_phone_offline` | `bulk_limit` | `no_mobile`
(account has no verified mobile) | `paused`; 422 `invalid` for a bad text.

## POST `.../bulk/batches/`  (confirm)

```json
{ "text": "...", "audience": "all", "promotional": false, "idempotency_key": "...",
  "confirm": true, "expected_count": 95 }
```
- `confirm` must be `true` (422 `invalid`, field `confirm`). `expected_count` is optional: the `will_send` the owner
  saw; if the count is different now, 409 `audience_changed` with `{"error": {"code", "message", "will_send": 97}}`.
- 201 with the batch object when created; **200 with the same batch** when the same `idempotency_key` is sent
  again (no second batch).
- Errors: 409 `site_held` | `sms_off` | `replies_off` | `no_line` | `batch_running` | `empty_audience` |
  `audience_changed`; 422 `invalid` (text, audience, confirm, idempotency_key); 403 `billing_only`.

## Batch object

```json
{
  "id": "sbk_...", "state": "waiting",       // waiting | sending | finished | stopped | ended
  "created_at": "2026-11-23T10:00:00+08:00",
  "audience": "all", "promotional": false,
  "text": "Ana's Store: Sale this weekend.",   // as it arrives; kept 180 days
  "size": 95, "sent": 0, "failed": 0, "not_sent": 0, "waiting": 95,
  "left_out": {"unverified": 10, "invalid_number": 0, "opted_out": 5, "not_promo": 0, "duplicates": 0},
  "end_reason": "",                            // "" | "stopped" | "site_held" | "billing_only" | "line_disabled"
  "end_message": "",                           // fixed text for end_reason
  "paused_reason": "",                         // active batches only: "" | "outside_window" | "daily_cap" | "phone_offline"
  "seconds_left": 2042,                        // estimate at the mean gap; 0 when not active
  "stopped_at": null, "finished_at": null
}
```
- `waiting` (the count) = `size - sent - failed - not_sent`; it includes a text the phone holds.
- `end_reason` is set only for `stopped` (`stopped`) and `ended` (`site_held`, `billing_only`, `line_disabled`);
  a `finished` batch has `end_reason: ""`. Not-sent texts of an ended or stopped batch carry that reason.
- A batch's state `waiting` means nothing has been sent yet; `sending` means at least one went out. A batch
  outside the window or at the cap stays in its state with `paused_reason` set.

## GET `.../bulk/batches/?cursor=&limit=`

`{ "items": [batch, ...], "next_cursor": "sbk_..." | null }`, newest first, `limit` 1 to 100 (default 20). 422
`invalid_cursor` for a bad cursor.

## GET `.../bulk/batches/{id}/?cursor=&limit=`

The batch object plus a page of its recipients, in send order:
```json
{ "...batch fields...",
  "results": [{"name": "Maria Santos", "mobile": "+63917***4567", "state": "sent", "reason": "", "reason_text": ""}],
  "next": "20" | null }
```
Recipient `state`: `waiting` | `queued` (with the phone) | `sent` | `failed` | `not_sent`. `reason` (for `failed` and
`not_sent`): `stopped`, `site_held`, `billing_only`, `line_disabled`, `member_removed`, `number_changed`,
`unverified`, `not_promo`, `opted_out`, `text_invalid`, `not_sent`, `expired`, or a short phone reason for `failed`;
`reason_text` is the fixed text where there is one. `limit` 1 to 100 (default 20). 404 for another store's batch.

## POST `.../bulk/batches/{id}/stop/`

No body. Answer: the batch object (`state: "stopped"`, `end_reason: "stopped"`). 409 `not_running` when the batch is
not waiting or sending. Texts the phone already took may still go out and are counted when the phone reports.

## HQ cap field

`GET/PUT /v1/staff/sms/...` (the existing SMS policy answer and body) carries `store_bulk_daily_limit` (integer,
default 2000, zero or more), beside `store_daily_limit` and `all_stores_daily_limit`. A body that leaves it out
keeps it. It is read and saved only once migration notifications.0008 is applied.

## What happens to a running batch

- The store's phone polls the gateway; the BE gives it the next text only (one at a time). Each member is checked
  again just before their text: still the store's member with the same verified number, not on the STOP list, and
  (promo) still agreed. A member who is not goes `not_sent` with the reason.
- Store put on hold, billing-only, or its phone line switched off: the rest are `not_sent` with that reason and
  the batch is `ended`.
- After a batch ends, its recipients' numbers are replaced by the masked form.
- Audit (account events, no text or number): `store_bulk_confirmed`, `store_bulk_stopped`, `store_bulk_ended`.
