# Santé members runbook

ClickUp z8uq9me7x8 (Membership Package slice PK2). A Santé Member is the buyer of one Santé site: a new kind of identity, per site, never a NegosyoOnline Subscriber `Account`. App: `apps/members`. Routes: `/storefront/v1/member/…`, behind the storefront credential.

## The switch

| Where | Name | Meaning |
|---|---|---|
| Railway `api` | `MEMBERS_ENABLED` | `1` turns the four routes on. Unset: they answer the 404 of an unknown site and no member table is read or written. Needs `STOREFRONT_ENABLED=1` and the storefront secret too. |
| Railway `api` | `REDIS_URL` | Required before the switch is turned on. Sign-in codes, attempt counts and every limit live in the Django cache. Without it the cache is each process's own memory: a code requested from one Gunicorn worker is unknown to the next. `manage.py check --deploy` reports `members.W001` when the switch is on and `REDIS_URL` is not set. |

Migration `members.0001` (tables `members_member`, `members_membersession`) must be applied in an environment before the switch is turned on there. It is run by the coordinator on staging after merge; never on production without Coach Ed.

## Routes

All are `POST`, JSON, `Authorization: Bearer <STOREFRONT_SERVER_SECRET>`, answers `Cache-Control: no-store`, and set no cookie. The storefronts server holds the visitor's cookie and passes the raw session token as data.

| Route | Body | Answer |
|---|---|---|
| `member/signin/request/` | `host`, `visitor_ip`, `email` | `{"status": "sent"}`, the same for a member, a stranger and a member of another site; 429 on the cooldown or a limit; 400 `invalid_request` for a malformed address |
| `member/signin/verify/` | `host`, `visitor_ip`, `email`, `code` | `{"token", "member"}`; the token is shown once. 400 `invalid_code` for wrong, expired, used and unknown alike |
| `member/me/` | `host`, `token` | `{"member"}`; 401 `member_session_invalid` |
| `member/signout/` | `host`, `token` | `{"status": "signed_out"}`, also for a token that is already gone |

An unknown host, a host that is not a served Santé site, and the switch off all answer `404 {"error": {"code": "not_found", "message": "Not found."}}`.

## Rules

- Codes go by email only. Six digits, 600 s, 5 attempts, one request a minute and 5 an hour per address, 20 an hour per visitor address across purposes (`apps/accounts/codes.py`), purpose `member:<site pk>`. A member signs in by email only; the mobile number is stored and stays unverified (SMS codes are an open owner decision, D1).
- The first valid code marks a pending email verified. A new member's contacts are both pending.
- The answer to a request takes at least 1.5 s whoever asked. One visitor address may make 10 requests an hour and 30 verifications in ten minutes. The per-site flood monitor counts requests (300 an hour) and writes one log warning naming the site; it never refuses.
- A session is a random token; only its SHA-256 is stored with site and member. It runs 30 days from sign-in and use never extends it. Every call re-reads the session and the member row, and a token only works on the site it was issued for.
- `services.change_contacts` replaces a contact, makes it pending and revokes every session of the member. `services.create_member` refuses a contact that is already a member of that site, or of any other live site (the refusal never names the store).
- Nothing in a log line, an error or an answer carries a code, a token or an address beyond what the route returns.

## Retention

See `docs/runbooks/retention.md`: the `expired_member_sessions` rule, and what disposal and export do to members.

## Turning it on (staging)

1. Merge and deploy. The routes are dark: with the credential, `POST /storefront/v1/member/me/` answers `404`.
2. The coordinator applies `members.0001` on staging (`docs/runbooks/migrations.md`).
3. Confirm `REDIS_URL` is set on Railway `api` (by name only).
4. Set `MEMBERS_ENABLED=1`. The same call now answers `401 member_session_invalid` for a made-up token.

To go back, unset `MEMBERS_ENABLED`; the tables and their rows stay. See the warning in the retention runbook before leaving it off after real members exist.
