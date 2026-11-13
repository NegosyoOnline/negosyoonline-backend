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

## Rules

- **Token.** Only the SHA-256 is stored. Created by the first valid contact call, never by a read. Every use slides its expiry two hours on. Spent by placement.
- **Place.** Idempotent on `sha256(token:submit_key)`: a replay returns the first order and writes nothing. The proof file is stored before the transaction and discarded on any rollback. One transaction under the Site row lock; the agreement version is checked before the file is stored and again under the lock. The member is created (mobile `pending`, email `unverified`) or the returning buyer matched. Order `pending_review`, benefits on from placement, number `SMP%05d` of count+1 per site, price 148800 centavos frozen and CHECKed. After commit: buyer "received" email, and the Subscriber "review needed" email only if the Subscriber's email is verified.
- **Returning buyer.** Same mobile and same email, no benefits, no live order: must hold a used onboarding challenge for the number, else 409 "Verify your mobile number first." The code send/verify routes are PK2b/PK7, not here.
- **Proof.** JPEG, PNG, WebP or PDF judged by the first bytes. Amount above 0 and up to 99,999.99 with two decimals; it need not equal 1,488, the Subscriber judges. Transfer time not in the future (5 minutes of clock slack).
- **Size cap: 4 MB** (TWCinc allows 10 MB). The proof travels browser, storefronts route handler on Vercel, Django, and a Vercel Function request body is capped at 4.5 MB (413 `FUNCTION_PAYLOAD_TOO_LARGE`, https://vercel.com/docs/errors/FUNCTION_PAYLOAD_TOO_LARGE; streaming does not lift it). Constant `PROOF_MAX_BYTES` in `apps/pack/constants.py`.
- **Evidence tables** (proof submissions, order events, agreement acceptances, enrollment authorizations) are append-only, orders cannot be deleted, one live order per member, one fixed amount.
- Support contacts in notices come from the Subscriber's own site profile, never TWC's.

## Local check

With the switches on, the local private store and a published agreement, `curl` `checkout/contact/` (JSON) then `checkout/place/` (`-F proof=@file.png;type=image/png`): the file lands under `sante/proofs/site-<pk>/` in the local private folder, the order row exists, and both emails print in the service log.
