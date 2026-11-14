# Santé members runbook

ClickUp z8uq9me7x8 (Membership Package slice PK2). A Santé Member is the buyer of one Santé site: a new kind of identity, per site, never a NegosyoOnline Subscriber `Account`. App: `apps/members`. Routes: `/storefront/v1/member/…`, behind the storefront credential.

## The switch

| Where | Name | Meaning |
|---|---|---|
| Railway `api` | `MEMBERS_ENABLED` | `1` turns the four routes on. Unset: they answer the 404 of an unknown site and no member table is read or written. Needs `STOREFRONT_ENABLED=1` and the storefront secret too. |
| Railway `api` | `REDIS_URL` | Recommended before the switch is turned on. Sign-in codes are database rows and need no Redis. The request throttles and the per-site flood monitor use the Django cache; without `REDIS_URL` that cache is each process's own memory, so those limits are per Gunicorn worker. `manage.py check --deploy` reports `members.W001` when the switch is on and `REDIS_URL` is not set. |

Migration `members.0001` (tables for members, sessions, sign-in challenges, recovery checks, contact audits and security holds) must be applied in an environment before the switch is turned on there. It is run by the coordinator on staging after merge; never on production without Coach Ed.

## Routes

All are `POST`, JSON, `Authorization: Bearer <STOREFRONT_SERVER_SECRET>`, answers `Cache-Control: no-store`, and set no cookie. The storefronts server holds the visitor's cookie and the browser `binding` (16 to 200 characters, opaque, the same value for every step of one sign-in) and passes the raw session token as data. Signed-in change-mobile and change-email are built (`apps/members/api.py`). The Subscriber recovery routes (`/v1/sites/...`) are listed under Subscriber recovery below.

| Route | Body | Answer |
|---|---|---|
| `member/signin/request/` | `host`, `visitor_ip`, `binding`, `contact` (an email or a `+639` mobile) | `{"step", "message", "offer_email"}`; the same generic answer for a member, a stranger and a member of another site; 429 on a cooldown or limit; 400 `invalid_request` for a malformed contact or binding |
| `member/signin/resend/`, `member/signin/accept-email/` | `host`, `visitor_ip`, `binding` | the same answer shape |
| `member/signin/verify/` | `host`, `visitor_ip`, `binding`, `code` | `{"token", "member"}`; the token is shown once. 400 with a code such as `invalid_code` or `code_expired` |
| `member/me/` | `host`, `token` | `{"member"}`; 401 `member_session_invalid` |
| `member/signout/` | `host`, `token` | `{"status": "signed_out"}`, also for a token that is already gone |

An unknown host, a host that is not a served Santé site, and the switch off all answer `404 {"error": {"code": "not_found", "message": "Not found."}}`.

## Subscriber recovery routes (ClickUp z8uq9mek5y)

Owner session (not the storefront secret), `apps/members/api_owner.py`, prefix `/v1/sites/{site_id}/members/`. Gated by `MEMBERS_ENABLED` (off: every route 404, signed in or not); a missing, foreign or non-Santé site is 404, a Billing-only site 403. Every answer is `Cache-Control: no-store` and carries masked contacts only. Ids are public ids; a member of another site is 404.

| Route | Purpose |
|---|---|
| `GET members/?q=` | Search by name, mobile or email (2+ characters, 20 rows). |
| `GET members/by-order/?ids=` | The member row (masked contacts, running hold) for each Membership Package order id. |
| `GET members/{id}/recovery/` | Member row, checklist items with what the store holds, hold, recent recoveries, failed checks, `locked`. |
| `POST members/{id}/recovery/check/` | `{answers: {item: matched\|not_matched}}` records the owner-attested check (15 minutes, single use). |
| `POST members/{id}/recovery/` | `{check_id, new_email, new_mobile?}` saves the new contact as pending, starts the 24-hour hold, revokes sessions, tells the old contacts. |

Limits per account: search 60 per 10 minutes, check 10 per hour, recover 5 per day (cache; per process without `REDIS_URL`). There is no per-member lock: failed checks are audited and limited only by the per-account check rate, and a recovery during a running hold restarts the 24-hour hold and notifies the old verified contacts again (the Subscriber owns the recovery of their members, Coach Ed 2026-11-13). A closed member is 409 `member_closed`. While texts are off (`sms.available()` false) `new_email` is required (422 `email_required`), because a mobile sign-in goes to the old email. The Membership Package order number is a checklist item when the member has an order (`apps/pack/recovery_item.py`). No migration.

## Training routes (ClickUp z8uq9mezdj)

The Subscriber writes the courses, modules, lessons and live trainings of their own site in the dash; there is no HQ authoring and no content shared between sites. Code: `apps/training/api_owner.py` (owner) and `apps/training/api.py` (member reads). Owner routes sit under `/v1/sites/{site_id}/training/`, behind the same gates as the notices (owner session, Santé site, Billing-only 403, 404 while `MEMBERS_ENABLED` is off) and answer `Cache-Control: no-store`. A foreign id is the same 404 as a missing one.

| Method and path | Body | Answer |
| --- | --- | --- |
| `GET courses/`, `POST courses/` | `title`, `slug?`, `description?`, `order?`, `active?` | `{courses}`; 201 course |
| `POST courses/reorder/` | `ids` (every course) | `{courses}` |
| `GET`, `PATCH`, `DELETE courses/{course_id}/` | PATCH: any of the create keys plus `archived` | course + `modules[].lessons[]`; course; `{status}` |
| `POST`, `DELETE courses/{course_id}/image/` | multipart `file` (JPEG, PNG, WebP, 2 MB) | course |
| `POST courses/{course_id}/modules/`, `POST .../modules/reorder/` | `title`, `order?`, `active?`; `ids` | 201 module; `{modules}` |
| `PATCH`, `DELETE modules/{module_id}/` | any of `title`, `order`, `active` | module; `{status}` |
| `POST modules/{module_id}/lessons/`, `POST .../lessons/reorder/` | `title`, `duration?`, `description?`, `video_url?`, `order?`, `active?`; `ids` | 201 lesson; `{lessons}` |
| `PATCH`, `DELETE lessons/{lesson_id}/` | any of the lesson keys | lesson; `{status}` |
| `POST`, `DELETE lessons/{lesson_id}/image/` | multipart `file` | lesson |
| `GET`, `POST live/` | `title`, `starts_at` (with time zone), `duration_minutes?`, `description?`, `join_url?`, `active?` | `{live}`; 201 live |
| `PATCH`, `DELETE live/{live_id}/` | any of the live keys | live; `{status}` |
| `POST`, `DELETE live/{live_id}/thumbnail/` | multipart `file` | live |

`video_url` must be an https embed link of YouTube, Vimeo or Bunny Stream; `join_url` an https link. Creating a live training that is active rings the bell of that site's members only. The member routes (`/storefront/v1/member/training/...`) read only the member's own site's content.

## Rules

- Ported from the approved TWCinc code (`otp.py`, `signin.py`, `sessions.py`, `holds.py`, `recovery.py`, `contact_change.py`). Codes are database rows (`MemberOtpChallenge`) bound to the browser binding; six digits, 10 minutes, 5 guesses, a resend inside the window reuses the code unless under 2 minutes is left, decoy rows make an unknown contact look like a member, and an advisory lock serialises one contact.
- Email is the code channel in this build. A mobile sign-in answers `sms_unavailable` until the SMS slice (PK2b) registers a transport with `hooks.register_sms_transport` (and owns the purposes in `apps/notifications/sms.py`). Other hooks: `register_contact_release`, `register_session_issued_hook`, `register_sign_in_sms_first`.
- `create_member` starts the email unverified, as in TWCinc, and the member's code goes by SMS (PK2b adds the transport; the switches stay off until then). Pass `email_state="pending"` for an email a code may verify. The first valid code marks it verified and writes a `first_verification` audit row.
- The answer to a request takes at least 1.5 s whoever asked. One visitor address may make 10 sends an hour and 30 verifications in ten minutes; the per-site monitor counts requests (300 an hour), logs one warning naming the site, and never refuses.
- A session is a random token; only its SHA-256 is stored with site and member. It runs 30 days and use never extends it. Every call re-reads the session and the member row, and a token only works on the site it was issued for. A contact change or recovery revokes the member's sessions.
- `create_member` refuses a contact that already belongs to a member of that site or of any other live site (the refusal never names the store).
- Nothing in a log line, an error or an answer carries a code, a token or an address beyond what the route returns.

## Retention

See `docs/runbooks/retention.md`: the `expired_member_sessions` and `expired_member_challenges` rules, and what disposal and export do to members.

## Turning it on (staging)

1. Merge and deploy. The routes are dark: with the credential, `POST /storefront/v1/member/me/` answers `404`.
2. The coordinator applies `members.0001` on staging (`docs/runbooks/migrations.md`).
3. Confirm `REDIS_URL` is set on Railway `api` (by name only) for shared throttles.
4. Set `MEMBERS_ENABLED=1`. The same call now answers `401 member_session_invalid` for a made-up token.

To go back, unset `MEMBERS_ENABLED`; the tables and their rows stay. See the warning in the retention runbook before leaving it off after real members exist.
