# Branch notes: z8uq9mj85j SMS-MENU BE

Branch `feat/z8uq9mj85j-sms-channel-filter` (from origin/main b2cc56f). Subtask of z8uq9m92fv. No TWCinc code.
No migration, model, data, new endpoint or setting; sending, comments, access and rate limits untouched.

Ruling SMS-MENU (Coach Ed, 2026-11-24): "for the New SMS and Bulk SMS, we need to create another Menu for this.
can you suggest what will be the best Menu name for this? Then remove the SMS in the Inbox so it will have its own
page.. I have a lot of plans for SMS marketing later." Authority for this backend part: "SMS-MENU BE: go"
(Manager, 2026-11-24).

## Contract

`GET /v1/sites/{site_id}/inquiries/conversations/?channel=` and `GET .../inquiries/unread/?channel=`:
- missing or empty: today's behaviour, unchanged.
- `sms`: only SMS conversations. `nosms`: every channel except SMS.
- anything else (case-sensitive; `SMS`, `all`, `email`...): HTTP 400, same error shape as the other filters:
  `{"error": {"code": "invalid_channel", "message": "Channel must be sms or nosms.", "fields": {"channel": [...]}}}`.
  NOTE: the existing status/unread/answered filters answer 422; this one answers 400 as the task specified.
- `unread_total` on the list follows the `channel` filter (it still ignores q, status, unread, answered and paging).
- Works with the cursor (`stored_cursor`): the filter is applied before ordering and paging.

Code: `stored.by_channel`, `stored.list_items(channel=None)`, `stored.unread_count(site, channel=None)`;
`api_owner._channel` validates; `has_any` unchanged. Tests: `ChannelFilterTests` in `apps/inquiries/tests/test_inbox_ui.py`.
