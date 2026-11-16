# Membership Pack checkout runbook

ClickUp z8uq9me9mr (Membership Package slice PK4). The guest checkout of the Santé Membership Pack: contact step, checkout token, and placing an order with a bank-transfer proof. App: `apps/pack`. Routes: `/storefront/v1/checkout/…`, behind the storefront credential. Ported from TWCinc `place_pack_order` steps 1 to 12 without Xendit and without a billing meter: launch payment is a bank or e-wallet transfer with proof, and an order is never metered or charged per sale.

## The switch

| Where | Name | Meaning |
|---|---|---|
| Railway `api` | `PACK_ENABLED` | `1` turns the three routes on. Unset: they answer the 404 of an unknown site and no pack table is read or written. |
| Railway `api` | `PACK_SHIPPING_ENABLED` | `1` turns on the Subscriber's shipment routes (record, correct, mark delivered) and the shipped and delivered buyer emails (ClickUp z8uq9mek5x). Unset: the three routes answer `404 not_found`, no shipping email is sent, and the owner reads say `shipping_enabled: false` so the dash shows no shipment buttons. Needs `PACK_ENABLED`; `check --deploy` reports `pack.E003` otherwise. Staging only until Coach Ed says otherwise. |

Needs `STOREFRONT_ENABLED=1` with the storefront secret, `MEMBERS_ENABLED=1`, `SANTE_AGREEMENT_ENABLED=1` (a published Membership Agreement version), `PAYMENT_METHODS_ENABLED=1` with at least one saved account, and the private file store (`PRIVATE_BLOB_READ_WRITE_TOKEN`). `manage.py check --deploy` reports `pack.E001`/`pack.E002` for the first two, `pack.W001` for no file store and `pack.W002` for no `REDIS_URL` (the throttles then count per Gunicorn worker).

Migration `pack.0004_shipment` (shipment and delivery columns on the order, two CHECK constraints, new event kinds) is written, not run; it is additive and safe while the old code runs. The Supervisor runs it on staging after merge, before `PACK_SHIPPING_ENABLED` is turned on there. Never on production without Coach Ed.

Migration `pack.0001` (six tables) is written, not run. The coordinator runs it on staging after merge; the switch is turned on in an environment only after it is applied there. Never run on production without Coach Ed. Once on, the switch is not turned off again without his ruling: a disposal run while it is off would leave buyers' names and proof files in place.

## Routes (route inventory)

All are `POST`, `Authorization: Bearer <STOREFRONT_SERVER_SECRET>`, `Cache-Control: no-store`, and set no cookie. The storefronts server keeps the raw checkout token in the buyer's cookie and sends it as data. An unknown host, a foreign or non-Santé site and the switch off all answer `404 {"error": {"code": "not_found", "message": "Not found."}}`.

| Route | Body | Answer |
|---|---|---|
| `checkout/contact/` | JSON: `host`, `visitor_ip`, `token` (after the first call), `full_name`, `mobile`, `email`, `delivery_address` (optional), `agreement_version_id`, `agreement_accept` (`"1"`), `enrollment_authorized` (`"1"`) | `{"token", "expires_at"}`: `token` is the raw token on the first call only, `""` after. 422 `invalid_contact` with field messages, `agreement_required` / `agreement_changed` / `enrollment_required`, 409 `contact_in_use`, 429 |
| `checkout/check/` | JSON: `host`, `visitor_ip`, `mobile`, `email` | `{"errors": {field: [message]}}`; saves nothing, makes no token. 429 after 60 an hour per IP |
| `checkout/place/` | multipart: `host`, `visitor_ip`, `token`, `submit_key` (1 to 64 characters), `account_id`, `reference_number`, `amount_sent`, `sender_bank`, `sender_first_name` + `sender_last_name` (or one `sender_name`), `sender_account_number`, `transferred_at` (`YYYY-MM-DDTHH:MM`, Manila), `proof` (the file) | `{"order_number", "replay"}`. 422 `invalid_proof` with field messages; 409 `checkout_expired`, `agreement_changed`, `mobile_not_verified`, `method_unavailable`; 503 `store_unavailable` |

Throttles are TWCinc's: contact 20 an hour per IP and 10 an hour per token, check 60 an hour per IP, answered before any judgement so a 429 reveals no membership.

## Buyer journey (owner read, ClickUp z8uq9meyqk)

`GET /v1/sites/{site_id}/pack/journey/?period=7d|30d|all` (default `30d`; anything else is 422 `invalid_period`). Signed-in owner of a Santé site, same gate as the other pack owner routes (404 for a foreign or non-Santé site or `PACK_ENABLED` off, 401 signed out, 403 `billing_only`). Read-only counts from existing rows (checkout tokens, orders, proofs, registrations, member sessions, Meta deliveries); nothing is stored, no buyer data is returned, and Meta figures are event counts, not people. Code: `apps/pack/journey.py`, `apps/pack/api_journey.py`.

## Prospect list (owner read, ClickUp z8uq9mfw14)

`GET /v1/sites/{site_id}/pack/journey/prospects/?stage=&q=&page=`, same gate as the journey (404 foreign or non-Sante site or `PACK_ENABLED` off, 401 signed out, 403 `billing_only`). Three kinds of row, newest activity first, 25 to a page: `order` (one per order of the site), `visit` (a Membership Pack page visit with no order) and `inquiry` (one per stored inquiry conversation of the site). Each row has `kind`, `name`, `order_number` (order rows), `order_id`, `source`, `stage`, `registration_status`, `last_activity`, `video_percent` (null for inquiries and for orders with no linked visit), `mobile` and `email`, and for an order row `placed_at`, `payment_status`, `price_centavos`, `currency` (null on other rows; what the Orders page shows, for the order summary popup).

Eight stages, one furthest stage per row: `new_lead`, `landing_page`, `start_watching`, `done_watching`, `added_to_cart`, `checked_out`, `account_setup`, `activated`. Mapping (`apps/pack/prospects.py`): an inquiry is `new_lead`; a visit is `landing_page`, then `start_watching` (video started or any percent) and `done_watching` (90 percent or more), then `added_to_cart` once the checkout step was opened or a contact saved; an order is `checked_out`, `account_setup` once the payment is approved (with `registration_status` beside it), and `activated` once the member signed in to the dashboard. `video_percent` on an order row comes from its linked visit; it is null when the order has no linked visit (unknown, never inferred as 0), and a linked visit's real value, including 0, is kept.

`mobile` and `email` are returned in full where already stored (Coach Ed, 2026-11-16, ruling D2): an order row the buyer's member mobile and email, a visit row the contact as typed, a stored (webchat or email) inquiry row its `contact_email` (no phone is stored there); `""` otherwise, and a Pancake conversation is never read for a contact. Search matches the contact on every row. Never an address, proof, date of birth or Meta data. Inquiry rows read the stored name live (blank stays blank), never call a provider, and are never matched with order rows by name. `stage_counts` (with `all`) are taken before search and stage narrowing. Unknown `stage` is 422 `invalid_stage`; a `page` that is not a whole number from 1 is 422 `invalid_page`. Code: `apps/pack/prospects.py`, `apps/pack/api_journey.py`.

### Membership Pack page visits (PackVisit, ClickUp z8uq9mfw14)

Storefront routes `POST /storefront/v1/visit/` (`{host, visitor_ip, ref?}` -> `{ref}`, a ref made here and kept by the browser in localStorage), `visit/event/` (`start`, `percent` 25/50/75/90/100 with the highest kept and `done_watching_at` from 90, `checkout`) and `visit/contact/` (name, mobile, email saved when a checkout field loses focus, kept only in the visit). Rate limited with `apps.common.throttle` (`VISIT_*` in `constants.py`). Order placement takes an optional `visitor_ref` and links the visit. Retention rule `pack_visits`: 90 days after the last activity the contact is blanked and a visit with no order is deleted; the day-90 site purge and account disposal delete the site's visits. Nothing goes to Meta or any third party. One migration (`0007_pack_visit`); apply on staging as any pack migration.

## Rules

- **Token.** Only the SHA-256 is stored. Created by the first valid contact call, never by a read. Every use slides its expiry two hours on. Spent by placement.
- **Place.** Idempotent on `sha256(token:submit_key)`: a replay returns the first order and writes nothing. The proof file is stored before the transaction and discarded on any rollback. One transaction under the Site row lock; the agreement version is checked before the file is stored and again under the lock. The member is created (mobile `pending`, email `unverified`) or the returning buyer matched. Order `pending_review`, benefits on from placement, number `SMP%05d` of count+1 per site, price 148800 centavos frozen and CHECKed. After commit: buyer "received" email, and the Subscriber "review needed" email only if the Subscriber's email is verified.
- **Returning buyer.** Same mobile and same email, no benefits, no live order: must hold a used onboarding challenge for the number, else 409 "Verify your mobile number first." The code send/verify routes are PK2b/PK7, not here.
- **Proof.** JPEG, PNG, WebP or PDF judged by the first bytes. Amount above 0 and up to 99,999.99 with two decimals; it need not equal 1,488, the Subscriber judges. Transfer time not in the future (5 minutes of clock slack).
- **Size cap: 4 MB** (TWCinc allows 10 MB). The proof travels browser, storefronts route handler on Vercel, Django, and a Vercel Function request body is capped at 4.5 MB (413 `FUNCTION_PAYLOAD_TOO_LARGE`, https://vercel.com/docs/errors/FUNCTION_PAYLOAD_TOO_LARGE; streaming does not lift it). Constant `PROOF_MAX_BYTES` in `apps/pack/constants.py`.
- **Evidence tables** (proof submissions, order events, agreement acceptances, enrollment authorizations) are append-only, orders cannot be deleted, one live order per member, one fixed amount.
- Support contacts in notices come from the Subscriber's own site profile, never TWC's.

## The buyer portal (a site that is off)

A paid Membership Pack buyer of a Santé site that is suspended, or cancelled past its service end or with no payment method, can still sign in and read their own order. The routes are a separate set under `/storefront/v1/portal/` (`apps/pack/api_portal.py`); `served()` and the member and pack routes still answer the neutral 404 for that site. Every call is `POST`, carries the server credential, sets no cookie and answers `Cache-Control: no-store`.

| Route | Answer |
|---|---|
| `portal/site/` | `{display_name, accent_color, closes_at, banner}` |
| `portal/signin/request/`, `resend/`, `accept-email/` | as `member/signin/*` |
| `portal/signin/verify/` | `{token}` only, never the buyer's contacts |
| `portal/me/` | `{first_name}`, only for a member with a verified order |
| `portal/signout/` | `{status}` |
| `portal/orders/` | `{cards, closes_at, banner}`, read-only cards |
| `portal/proof/` | the buyer's own proof file |

- **Opens** when `MEMBERS_ENABLED` and `PACK_ENABLED` are on, the site is a Santé site, not removed, not on operator hold, and its billing position is `billing_only`. A served, unknown, exempt or no-subscription site, or a billing read error, answers the neutral 404. There is no switch: the portal must not be turned off after sales.
- **Closes** at `billing.deletion_point(site)`: 90 days after service ends for a site that paid, 30 days for one that never paid (suspended and cancelled alike), read from the clock so the sweep does not have to have run. A site with no clock (never paid, collection not live) has no end yet. After the point the portal answers the neutral 404.
- **Shows** only the buyer's own verified order: number, amount, payment label, shipment, own proofs, registration label and line. Never the address, receiver, date of birth, usernames, contacts, billing facts or other buyers.
- **Banner.** "This store is not taking orders." and, from 14 days before the closing instant, when the page closes. It says nothing about deletion or why the store is off.
- **Sale gate (L45).** `BUYER_PORTAL_READY` in `apps/pack/portal.py` is a code constant, `True`. While it is `False`, `checkout/contact/` and `place_pack_order` answer the unserved-site 404; product orders are not touched. No variable, no migration.
- **Purge and day-76 email** (ClickUp z8uq9mep8p): behind `PACK_PURGE_ENABLED` and `PACK_BUYER_NOTICE_ENABLED`, both off by default; see `docs/runbooks/retention.md`. With the purge on, the banner's last-14-days text also carries the deletion sentence of the email. Migration `pack.0005_buyer_notice` (one new table) is written, not run; the Supervisor runs it on staging after merge, before `PACK_BUYER_NOTICE_ENABLED` is turned on there. Never on production without Coach Ed.

## Local check

With the switches on, the local private store and a published agreement, `curl` `checkout/contact/` (JSON) then `checkout/place/` (`-F proof=@file.png;type=image/png`): the file lands under `sante/proofs/site-<pk>/` in the local private folder, the order row exists, and both emails print in the service log.

- **Export of the Pack files** (ClickUp z8uq9mbu0p): support runs `export_pack_files` for an ended site; see `docs/runbooks/retention.md`, "The Pack files of an ended site", and `docs/runbooks/support-and-exit.md`, section 4.
