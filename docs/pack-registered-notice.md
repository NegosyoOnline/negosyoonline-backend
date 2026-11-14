# Santé registration work and the "account created" email

ClickUp z8uq9meyqj. Code: `apps/pack/registration.py` (`start`, `mark_registered`), `apps/pack/registered_notice.py`, `apps/pack/api_owner.py` (routes), migration `pack.0006_registered_notice`.

## The source event

The only trusted event is the Subscriber's own **Mark registered** in the dash. Nothing is read from Santé, there is no Santé API, no scraping and no webhook, and there is no test trigger: this is the real path. The Subscriber encodes the member in Santé by hand, then presses **Start** (T9, `ready_to_register` to `in_registration`) before encoding and **Mark registered** (T11+, `in_registration` to `registered`) after, typing the Santé member ID (letters, digits and hyphens, 1 to 32 characters). The ID is the only field the event carries. No screenshot is stored. The failure edges (T10, T12, T14, T16) are not in this build.

Both writes lock the registration row and compare `state_version` (stale: 409 `stale`). A registration in any other status answers 409 `wrong_status`, so a second Mark registered is refused and writes nothing.

## The auth boundary

Routes are under `/v1/sites/{site_id}/pack/registrations/`, on the Subscriber's signed-in session only (the same router as the pack orders). The site comes from the session's account through `site_for(account, site_id, template=SANTE)`; every query filters on that site, and a member is addressed by its public id. A foreign, missing or non-Santé site or member answers 404, a Billing-only site 403, and `PACK_ENABLED` off 404. Writes are rate limited like the order writes. No storefront secret and no member session reaches them. The deletion point closes both writes (404) like the rest of the registration.

Data minimisation: date of birth, both usernames, the delivery address and the mobile are returned only while the row is `ready_to_register` or `in_registration`. A `registered` row returns the name, Santé member ID, registered date and the email status. Fields the retention job (`DOB_ERASE_ENABLED`) erased stay empty: nothing here writes them back. There is no export of these fields.

## The email and its idempotency key

One `PackRegisteredNotice` per registration (a one-to-one key). Mark registered writes it in the same transaction as the move to `registered`; a repeat can neither pass the status check nor create a second row, and `send` does nothing for a row that is `sent` or `gave_up`. The email goes **after commit** (`transaction.on_commit`), so nothing is sent for a change that did not commit.

It is sent with the store's own sender helper (`send_store_email`) to the member's **verified** email only. It says the Santé account is created, that product orders are placed through the Santé Mobile App, and links the instruction video. It carries no password, sign-in code, token, Santé ID or other personal data (the member's first name and the store name only). The video is the setting `PACK_ORDERING_VIDEO_URL` (an http(s) link; empty by default): empty, the email says the video guide is coming soon and links nothing.

The row holds delivery evidence only: `status` (`pending`, `sent`, `failed`, `gave_up`), `attempts`, `last_attempt_at`, `last_error_code` (`no_verified_email`, `email_not_sent`, `bad_sender`, `site_closed`, `error`), `sent_at`, `gave_up_at`. No address, no text, no secret.

## Retry and give-up

A failed try (including a member whose email is not verified yet) leaves the row `failed` with the error code. The hourly task `pack.registered_notice` (beat entry `pack-registered-notice`, needs only `PACK_ENABLED`) tries a `failed` row again 6 hours after its last try (and a `pending` one that was never tried, for example after a crash). After 6 retries (7 tries) it gives up: the row becomes `gave_up` and one error line is logged with row numbers only. The owner sees the state in the list: `sending`, `sent`, `retrying` or `gave_up`, with the tries and last error code. A site past its deletion point gives up at once.

## Switches and migration

No new on/off switch: the routes follow `PACK_ENABLED`. Migration `pack.0006_registered_notice` adds `santeregistration.sante_member_id`, `santeregistration.registered_at` and the table `PackRegisteredNotice`. It is written, not run. Staging only, by the Supervisor, after the release merges; never production without Coach Ed.

## Retention

The account export carries `sante_member_id` and `registered_at` on each registration row. Account disposal blanks `sante_member_id` (and counts a row that holds only a member ID) and keeps `registered_at`, a date. The scheduled 90-day details purge does not touch the member ID: it is kept for the service until account disposal.
