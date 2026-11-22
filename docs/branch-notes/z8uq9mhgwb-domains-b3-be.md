# Branch notes: z8uq9mhgwb DOMAINS B3 BE (store emails on the custom domain)

Branch `feat/z8uq9mhgwb-domains-b3-be`, cut from the B2 head b48f1b0, so it carries B1 and B2 and is
released after them. No TWCinc code was ported; the design is NegosyoOnline's own (ClickUp z8uq9mcyu0).
No new migration (`domains.0001_initial` holds the email columns), no new package, no provider call, no
key, no variable, no staging change, no email sent. The Public Suffix List was not downloaded.

## Design sources used

`bunny-addendum-r12-2026-11-21.md` (supersedes every older hosting line), `plan.md` r4.1 row "B3 email",
r4.3 test 3 and r4.5, `design.md` R4.5, R4.14.7 and R4.14.8, `approvals.md` (R1 to R2, B1 to B3, D3).

## What B3 contains (apps/domains)

* `providers/resend_domains.py`: the Resend adapter, five allowlisted calls: create (body exactly name,
  fixed region, `capabilities` sending enabled and receiving disabled), get, verify, delete, list. Create
  takes the name only from the site's own `succeeded` registration, never a platform zone; an empty
  `DOMAINS_PLATFORM_ZONES` refuses everything. Delete takes only an id recorded on a claim of the site,
  GETs the domain first and refuses unless its name equals the registration name. Answers are reduced to
  whitelisted fields. There is no `/emails` and no key call. `providers/fake.py` gains `FakeResend`, a
  transport under the real adapter (B1 and B2 pattern); `Providers.resend` (None when not built).
* `email_track.py`: the track `email_none` / `email_waiting_capacity` to `email_creating`, `email_dns`,
  `email_verifying`, `email_verified`, `email_active` (and `email_failed`), run by the minute sweep after
  the website steps, only while `DOMAINS_EMAIL_ENABLED` is on. Records come from Resend's answer and go
  into the zone through the existing Cloudflare DNS adapter (DNS-only, recorded ids in `DomainDnsRecord`
  purpose `resend`, list before create). Verify polling every 5 minutes for 72 hours. Capacity: occupied
  Resend domains of ours against `DOMAINS_MAX_EMAIL_DOMAINS`; at capacity the claim waits with its time and
  one alert; the purchase module does not mention email (a test pins it). `email_failed` never touches the
  website state. Runs under the claim's single-flight lock, shared with the website steps, and re-reads the
  claim, the registration, the name binding and the payment gate every pass.
* `machine.py`: removal also deletes the Resend domain (after the Vercel entries, independent of the DNS
  deletions, which already cover both purposes; the claim ends only when Resend is done; a refusal keeps
  `removal_pending`); `machine.retry` also retries a failed email track (so the existing HQ retry route and
  audit serve both, no new route).
* `sweeps.resend_daily` (daily sweep, only while the switch is on): re-reads verified domains (promotes
  `email_verified` to `email_active`, records the status, alerts on `temporary_failure` / `failed`), and
  compares Resend's list with the recorded ids (alerts on unrecorded and missing; platform names ignored).
* `events.clean` + `DETAIL_KEYS`: `DomainEvent.detail` of the email track holds whitelisted keys only.
* `checks.py`: `domains.W005` (names only), only while `DOMAINS_EMAIL_ENABLED` is on.
* Docs: `docs/runbooks/custom-domains.md` ("Store email (B3)") and `docs/runbooks/store-emails.md`.

## Files outside apps/domains (every one)

* `apps/notifications/store_email.py`: new `domain_sender(site)` (the one lookup, keyed on the site's own
  claim row) and one changed expression in `send_store_email`: `from_address=sender_address(site) or
  domain_sender(site)`, so an explicit `STORE_SENDERS` pair still wins.
* `config/settings.py` and `.env.example`: names only (`DOMAINS_EMAIL_ENABLED`, `RESEND_DOMAINS_API_KEY`,
  `RESEND_REGION`, `DOMAINS_MAX_EMAIL_DOMAINS`).
* `docs/runbooks/custom-domains.md`, `docs/runbooks/store-emails.md`, this file.
No other test or file outside apps/domains changed.

## Deviations and choices (smaller, fail-closed option)

1. **The sender lookup needs `DOMAINS_ENABLED` and `STORE_EMAILS_BRANDED`, not `DOMAINS_EMAIL_ENABLED`.**
   Addendum row 11 puts the email switch on the worker only, while member emails send from the api.
2. **Holding states minus `removal_pending`**: the lookup uses `connecting`, `activating`, `live`,
   `attention`; a claim being removed falls back at once. Email can verify earlier (from `registered`) but
   is not used until the website is connecting.
3. **A reading older than 72 hours is "unknown"** and falls back (the design says re-read daily; a dead
   daily sweep must not keep a stale `verified`).
4. **Unset `DOMAINS_MAX_EMAIL_DOMAINS` is 0**: every email track waits (same rule as B2's money values).
   The operator sets it to the plan's free slots (the platform's own domain already uses one).
5. **`email_verified` to `email_active`** happens on the first daily read that confirms `verified`; both
   count for the sender. `email_active` is therefore "confirmed by a daily read".
6. **A create answered "exists" is `email_failed`, never adopted**; a retryable create error is simply
   tried again (Resend refuses a duplicate name, so there is no double create).
7. **A non-retryable provider refusal keeps the state** (alert and event each pass) and becomes
   `email_failed` after 72 hours, as the website track does.
8. **Removal with the email switch off, or a Resend refusal, keeps the claim in `removal_pending`** (alert)
   instead of ending it and leaving a Resend domain that takes a plan slot.
9. **Resend request and answer shapes are as the design and docs describe them, not verified**: `POST
   /domains` with `name`, `region`, `capabilities`; answer `id`, `name`, `status`, `region`, `records[]` with
   `record`, `name`, `type`, `value`, `priority`, `ttl`, `status` (relative names, so the track makes them
   whole); `POST /domains/{id}/verify`; list answer under `data`. Part A/C must confirm them. DMARC is not
   written (optional operator step after verified, per R4.14.7 (d)).
10. The platform's own Resend domain is ignored by the daily list comparison by `DOMAINS_PLATFORM_ZONES`.

## Not done (out of B3)

A staff route of its own for the email track (the HQ retry covers `email_failed`), the owner screen, any
frontend, DMARC, any provider call, key, variable or staging change.

## Suites

See the final lines of the Supervisor report (filled in when the runs finished).
