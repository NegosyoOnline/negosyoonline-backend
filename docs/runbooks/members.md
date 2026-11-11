# Santé members runbook

ClickUp z8uq9me7x8 (Membership Package slice PK2). A Santé Member is the buyer of one Santé site: a new kind of identity, per site, never a NegosyoOnline Subscriber `Account`. App: `apps/members`. Routes: `/storefront/v1/member/…`, behind the storefront credential.

## The switch

| Where | Name | Meaning |
|---|---|---|
| Railway `api` | `MEMBERS_ENABLED` | `1` turns the four routes on. Unset: they answer the 404 of an unknown site and no member table is read or written. Needs `STOREFRONT_ENABLED=1` and the storefront secret too. |
| Railway `api` | `REDIS_URL` | Recommended before the switch is turned on. Sign-in codes are database rows and need no Redis. The request throttles and the per-site flood monitor use the Django cache; without `REDIS_URL` that cache is each process's own memory, so those limits are per Gunicorn worker. `manage.py check --deploy` reports `members.W001` when the switch is on and `REDIS_URL` is not set. |

Migration `members.0001` (tables for members, sessions, sign-in challenges, recovery checks, contact audits and security holds) must be applied in an environment before the switch is turned on there. It is run by the coordinator on staging after merge; never on production without Coach Ed.

## Routes

All are `POST`, JSON, `Authorization: Bearer <STOREFRONT_SERVER_SECRET>`, answers `Cache-Control: no-store`, and set no cookie. The storefronts server holds the visitor's cookie and the browser `binding` (16 to 200 characters, opaque, the same value for every step of one sign-in) and passes the raw session token as data. The Subscriber recovery routes (`/v1/sites/...`) are a later slice; `recovery.py` and `contact_change.py` hold the service logic only.

| Route | Body | Answer |
|---|---|---|
| `member/signin/request/` | `host`, `visitor_ip`, `binding`, `contact` (an email or a `+639` mobile) | `{"step", "message", "offer_email"}`; the same generic answer for a member, a stranger and a member of another site; 429 on a cooldown or limit; 400 `invalid_request` for a malformed contact or binding |
| `member/signin/resend/`, `member/signin/accept-email/` | `host`, `visitor_ip`, `binding` | the same answer shape |
| `member/signin/verify/` | `host`, `visitor_ip`, `binding`, `code` | `{"token", "member"}`; the token is shown once. 400 with a code such as `invalid_code` or `code_expired` |
| `member/me/` | `host`, `token` | `{"member"}`; 401 `member_session_invalid` |
| `member/signout/` | `host`, `token` | `{"status": "signed_out"}`, also for a token that is already gone |

An unknown host, a host that is not a served Santé site, and the switch off all answer `404 {"error": {"code": "not_found", "message": "Not found."}}`.

## Rules

- Ported from the approved TWCinc code (`otp.py`, `signin.py`, `sessions.py`, `holds.py`, `recovery.py`, `contact_change.py`). Codes are database rows (`MemberOtpChallenge`) bound to the browser binding; six digits, 10 minutes, 5 guesses, a resend inside the window reuses the code unless under 2 minutes is left, decoy rows make an unknown contact look like a member, and an advisory lock serialises one contact.
- Email is the code channel in this build. A mobile sign-in answers `sms_unavailable` until the SMS slice (PK2b) registers a transport with `hooks.register_sms_transport` (and owns the purposes in `apps/notifications/sms.py`). Other hooks: `register_contact_release`, `register_session_issued_hook`, `register_sign_in_sms_first`.
- `create_member` starts the email as pending, so email sign-in works at once; pass `email_state="unverified"` for TWCinc's behaviour. The first valid code marks it verified and writes a `first_verification` audit row.
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
