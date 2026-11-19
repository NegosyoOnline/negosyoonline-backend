# Membership Package: storefront route shapes

Contract between the backend task (z8uq9me9qv) and the frontend task (z8uq9me9qw). Every route is `POST`, JSON unless noted, under `/storefront/v1/` of the storefront surface, and carries the storefronts server's credential. Django sets no cookie: the storefronts server keeps the checkout token (`__Host-no_checkout`, 2 h sliding) and the member session token (`__Host-no_member`, 30 d) and passes them as data. Every body carries `host` (the site host being served) and `visitor_ip`. While `PACK_ENABLED` is off, and for an unknown, foreign or non-Santé host, every route answers `404 not_found`.

Errors have the repository shape `{"error": {"code", "message", "fields"?}}`; `fields` maps a field name to a list of messages. A 429 is `rate_limited`. Strings are TWCinc's, character for character.

## Checkout (PK4, built)

| Route | Body | Answer |
|---|---|---|
| `checkout/contact/` | `token?, full_name, mobile, email, delivery_address?, enrollment_authorized` (agreement_version_id and agreement_accept are optional, from an older storefront; the terms are accepted at `onboarding/details/`, which takes `agreement_version_id` with `terms`) | `{token, expires_at}`: `token` is the raw checkout token, answered once on the first call, `""` afterwards |
| `checkout/check/` | `mobile, email` | `{errors: {field: [msg]}}` (marks a contact as the buyer finishes typing) |
| `checkout/state/` | `token` | `{needs_code, mobile, verified, expires_at}`; 409 `checkout_expired` when the token is gone |
| `checkout/code/send/` | `token` | `{status}`; the SMS code of a returning buyer (only when `needs_code`) |
| `checkout/code/verify/` | `token, code` | `{status: "verified"}` |
| `checkout/place/` | multipart: `token, submit_key, account_id, reference_number, amount_sent, sender_bank, sender_first_name, sender_last_name, sender_account_number, transferred_at, proof` (file, 4 MB) | `{order_number, replay}` |

Shapes found by the scripted flow: `delivery_address` is an object (omit it when empty), `agreement_version_id` is the active version's id as a string, `account_id` is the payment account's public id (`pay_...`), `transferred_at` is `YYYY-MM-DDTHH:MM` (Manila).

Codes: `invalid_contact` 422 (with `fields`), `contact_in_use`, `order_on_file` 409 (a memberless bank order holds the contact), `checkout_expired` 409, `checkout_used` 409, `no_contact` 409, `mobile_not_verified` 409, `method_unavailable` 409, `store_unavailable` 503, agreement and enrollment refusals as the sub-spec.

## Onboarding after payment (PK7)

The page is bound to the checkout token that placed the order. A token with no placed order answers `409 checkout_closed` (send the buyer to sign-in). A token that began before a recovery or a contact change answers `409 checkout_stale` (`checkout_closed` page). Every call slides the token two hours; the answer carries `expires_at` so the storefronts server re-sets the cookie.

`onboarding/state/` `{token}` -> state:

```
{step: "verify"|"address"|"details"|"done", order_number, buyer_first_name, mobile_masked,
 mobile: {pending, mobile?, locked?, code_sent_to, resend_in, attempts_left},
 email: {verified, email, masked, code_sent, resend_in, attempts_left},
 address: {country, receiver_name, receiver_mobile, line?, barangay?, city?, province?, zip?, landmark?},
 details_erased, registration_held: {label, line} | null, expires_at}
```

Each of these answers the same state, so the page re-renders from it:

| Route | Body |
|---|---|
| `onboarding/mobile/send/` | `{token, mobile?}` (a typo fix; ignored once the emailed link was used) |
| `onboarding/mobile/verify/` | `{token, code}` |
| `onboarding/email/send/` | `{token}` |
| `onboarding/email/verify/` | `{token, code}` |
| `onboarding/address/` | `{token, receiver_name, receiver_mobile, line, province, city, barangay, landmark, postal_code?}` |
| `onboarding/details/` | `{token, dob_year, dob_month, dob_day (strings), desired_username, alternate_username, terms}` |

`onboarding/completed/` `{token}` -> `{result: "issued"|"open"|"stale"|"spent", session_token}`. The POST alone issues the member session, once per checkout token; `session_token` is non-empty only when `result` is `issued`. `open` goes back to onboarding, `stale` to the closed-checkout page, `issued` and `spent` to the member dashboard (clear the checkout cookie).

`address/zip/` `{province, city, barangay, postal_code?}` -> `{ok, zip_code, zip_precision, error_field, error_message}` (the vendored dataset, server side).

## Secure order-access link (PK7)

The emailed URL is `https://<host>/funnel/membership-pack/access/<signed-id>/`. `link` below is `<signed-id>`. An unknown link is `404 not_found`.

| Route | Body | Answer |
|---|---|---|
| `access/read/` | `{link}` | `{link_open, renewable}`; changes nothing |
| `access/renew/` | `{link}` | `{sent, link_open, renewable}` (3 per hour per member) |
| `access/redeem/` | `{link}` | `{redeemed, token, expires_at}`; a fresh checkout token for the member's latest order, never a session |

## Member sign-in (PK2/PK2b, built)

`member/signin/*`, `member/me/`, `member/signout/` as `apps/members/api.py`.

Channel rule (Coach Ed, 2026-11-12): the backend picks the channel, never the browser. A member whose email is verified gets the sign-in code by email on the first `member/signin/request/`, whether they typed the email or the mobile (no second "Email me the code" step). Without a verified email: an email entry still goes by email (verified or pending), a mobile entry goes by SMS (and a typed, still-unconfirmed onboarding mobile gets no code). A template that texts first keeps SMS.

The answer to `signin/request/` and `signin/resend/` is always `{step: "code", message: "If this matches a member of this store, we sent a code.", offer_email: false}`, identical for members and unknown contacts, so it carries no channel and no masked address (that would reveal membership). No new field. The code step may show a hint built only from what the visitor typed: for a typed email, "We emailed a code to f***@example.test" (masked from the typed value); for a typed mobile, the neutral `message`. `signin/accept-email/` stays as the fallback for the `offer_email: true` path (an SMS that fell back); with a verified email it is rarely reached.

## Member payment card, proof and new proof (PK8)

All under `/storefront/v1/`, body `{host, token}` where `token` is the member session. No session or an old one: `401 member_session_invalid`. The other errors use the usual `{error: {code, message, fields?}}`.

| Route | Body | Answer |
|---|---|---|
| `member/pack/` | `{host, token}` | `{pack, registration}` |
| `member/pack/proof/` | `{host, token, proof_id}` | the member's own file (an image or PDF); another member's id is `404 not_found` |
| `member/pack/resubmit/` | multipart `host, token, visitor_ip, account_id, reference_number, amount_sent, sender_bank, sender_first_name, sender_last_name (or sender_name), sender_account_number, transferred_at, proof` | as `member/pack/` |

`pack` is `null` with no order, else `{order_number, payment_status: pending_review|rejected|verified, payment_label, method_label, price_centavos, tone, benefits_off, lock, proofs}`. `lock` is `null` unless the order is rejected, else `{reason_label, other_text, resubmitted, accounts}`: the reason shows only here, after sign-in (the email names none); `resubmitted` is true while a new proof waits; `accounts` is the payment accounts (as `pack/` of the Santé reads). `proofs` is `[{id, seq, submitted_at, reference_number, amount_sent_centavos, content_type}]`. `registration` is the Santé registration card (`{status, label, line, tone, details_form, details_erased, state_version}`) while benefits are on, else `null` (the resubmit answer leaves it `null`).

Resubmit errors: `409 not_rejected` (only from rejected), `422 invalid_proof` with `fields` (same strings as checkout), `429`. The lock stays until the Subscriber approves.

Registration card by session (the dashboard's dialog and Home card), body `{host, token, visitor_ip, ...}`, answer `{registration, mobile}` (`mobile` as the onboarding state's mobile check):

| Route | Extra body |
|---|---|
| `member/registration/` | none |
| `member/registration/mobile/send/` | `mobile?` |
| `member/registration/mobile/verify/` | `code` |
| `member/registration/details/` | `date_of_birth` (`YYYY-MM-DD`; `dob_year/month/day` still accepted when it is empty), `desired_username, alternate_username, delivery_address?` (`{line, barangay, city, province, zip}`) |

## Subscriber order review (PK8)

On the owner API, `https://<api>/v1/sites/{site_id}/pack/orders/...`, signed in as the Subscriber (the dash session). Foreign, missing or non-Santé site: `404 not_found`; a suspended or ended site: `403 billing_only`; `PACK_ENABLED` off: `404`. Order and proof ids are public ids; money is centavos.

| Route | Body | Answer |
|---|---|---|
| `GET .../pack/orders/?filter=to_review\|rejected\|ready_to_ship\|shipped\|delivered\|all` | | `{orders: [row]}` newest 200 |
| `GET .../pack/orders/{order_id}/` | | detail |
| `GET .../pack/orders/{order_id}/proofs/{proof_id}/` | | the file, `Cache-Control: private, no-store`, `nosniff` |
| `POST .../pack/orders/{order_id}/approve/` | `{state_version}` | detail |
| `POST .../pack/orders/{order_id}/reject/` | `{state_version, reason_code, other_text?}` | detail |
| `POST .../pack/orders/{order_id}/shipment/` | `{state_version, courier, courier_other_name?, tracking_number, shipped_on?}` | detail. Only from `verified`, not yet shipped. `409 wrong_status`, `already_shipped`; `422 invalid_shipment` with field messages. Switch `PACK_SHIPPING_ENABLED` off: `404`. Sends the shipped email after commit |
| `POST .../pack/orders/{order_id}/shipment/correct/` | same body | detail. Shipped and not delivered only; `409 not_shipped`, `delivered_final`. No email |
| `POST .../pack/orders/{order_id}/delivery/` | `{state_version, delivered_on?}` | detail. Shipped only; date from the shipped date to today (Manila); final. `409 not_shipped`, `delivered_final`; `422 invalid_delivery`. Sends the delivered email |
| `GET .../pack/registrations/?filter=to_register\|registered\|all` | | `{registrations: [row]}` newest 200. Date of birth, usernames, delivery address and mobile only on `ready_to_register` / `in_registration` rows. See `docs/pack-registered-notice.md` |
| `POST .../pack/registrations/{member_id}/start/` | `{state_version}` | row. T9. `409 wrong_status`, `stale` |
| `POST .../pack/registrations/{member_id}/registered/` | `{state_version, sante_member_id}` | row. T11+; one email to the member after commit. `409 wrong_status`, `stale`; `422 invalid_success` |

`row`: `{id, order_number, buyer_name, mobile_masked, method, payment_status, created_at, state_version, price_centavos}`. `detail` is `row` plus `{currency, delivery_address, rejection_lock_active, submissions, events}`; a submission is `{id, seq, account_kind, account_provider_name, account_name, account_number_last4, reference_number, amount_sent_centavos, sender_bank, sender_name, sender_account_number, transferred_at, submitted_at, content_type, decisions: [{decision, reason_code, reason_label, other_text, actor_label, decided_at}]}`.

`reason_code`: `amount_mismatch, payment_not_found, proof_unreadable, reference_mismatch, wrong_account, other` (`other` needs `other_text` of 1 to 500 characters: `422 reason_text_required`; any other code `422 invalid_reason`). Approve works from `pending_review` or `rejected`; reject only from `pending_review` (`409 wrong_status`). An old `state_version`: `409 stale`; reload the detail. Unknown body keys: `400`.

## Store policy pages (PK12)

`GET /storefront/v1/policies/{slug}/?host=...`, `slug` in `terms-and-conditions, privacy-policy, shipping-policy, return-refund-policy, payment-policy`. Answer `{slug, title, version, effective_date, business: {name, address, email, phone, website_url, privacy_email, complaints_contact, registration_details}, shipping, privacy, links}`; `effective_date` is `null` until launch day; the TIN is never sent. A page is live only when every value it prints is filled; otherwise (and for an unknown slug or host, or `PACK_ENABLED` off) the one `404 not_found`. `GET site/` now fills `policy_links` with `[{label, path}]` of the live pages. An order records `policy_version` "1" when at least one page is live.

## Member Account (PK13)

TWCinc routes carried (negosyoonline_members `urls.py`): `dashboard/account/`, `account/verify-email/`, `account/change-mobile/`, `account/change-email/`, and the registration routes above. Body `{host, token, visitor_ip}` under `member/`, the raw session token binds every code. A contact change revokes every session of the member, this one included.

| Route | Extra body | Answer |
|---|---|---|
| `member/account/` | | `{member_name, mobile_masked, mobile_state, email_masked, email_state, default_channel, hold_banner}` |
| `member/account/verify-email/` | | `{step: send\|code\|done, email_masked, message}` |
| `member/account/verify-email/send/` | | same; `400 already_verified` |
| `member/account/verify-email/verify/` | `code` | same, `step: done`, `message: "Your email is verified."` |
| `member/account/change/{mobile\|email}/` | | `{field, current_masked, proof_channel: sms\|email, proof_masked, step: current\|current_code\|replacement\|replacement_code}` |
| `member/account/change/{mobile\|email}/act/` | `action` = `send_current`, `verify_current` (`code`), `send_replacement` (`new_value`), `verify_replacement` (`code`) | `{state, changed}`; `changed: true` means the contact was replaced and the session is revoked: clear the cookie and show the sign-in page |

## Member Dashboard and My Orders (home and orders reads)

Both are `POST`, body `{host, token}`, built from the member's pack order only (no product orders yet: their counts are zero, and shipment data comes from the Subscriber's manual entry: courier and tracking stay empty and `Shipped`/`Delivered` stay incomplete until the Subscriber records the shipment and the delivery, which needs `PACK_SHIPPING_ENABLED`). Money is PESOS as a number (`1488`, as TWCinc's templates format it: add the peso sign and thousands separators), with `*_centavos` (integer) and `*_text` (`"1,488.00"`) beside it. `h` is 0 to 100 in steps of 5. Dates are ISO strings; show them in Manila time.

`member/home/` -> `{summary, order_monitor}`:

```
summary: {total_spent, total_spent_text, total_spent_centavos, total_orders, delivered, needs_attention}
order_monitor: {awaiting_approval, for_booking, for_shipping, delivered, delta, has_orders,
                series: [{label: "Sun".."Sat", count, h}]}
```

Same meaning as TWCinc: `total_spent` counts verified orders; `needs_attention` counts rejected; `awaiting_approval` is "Under Review", `for_booking` "Preparing" (verified, not shipped), `for_shipping` "Shipping", `delivered` "Delivered"; `delta` is orders this month minus last month (up arrow `+n vs last month`, down arrow `n vs last month`, zero `No change yet`); `series` is this Sunday to Saturday (Manila) with `h` the bar height percent in steps of 5 (a floor of 4 orders on the axis). `has_orders` false shows "No orders yet. Your order activity will appear here." The tiles are labelled Total Spent, Orders, Delivered, Needs Attention; the monitor stats Under Review, Preparing, Shipping, Delivered; "View all orders" links to My Orders. The lock, hold banner and verify-email prompt come from `member/pack/` and `member/account/`.

`member/orders/` -> `{cards: [card]}`, newest first (empty: "No orders on your dashboard yet."):

```
card: {id, order: {order_number, created_at, total}, order_number, created_at, total, total_text, total_centavos, payment_status, payment_label,
       chip_label, chip_class, problem, problem_line, awaiting_payment, under_review,
       steps: [{num, label, state: done|current|todo, date|null}], courier, tracking, note,
       shipping_label, headline, item_count, item_lines: [{qty, name}],
       receiver_name, receiver_mobile, address,
       membership: null | {label, line, tone, registered, sante_member_id, act}}
```

`chip_class` is `""` or `sf-status-expired` (rejected; `problem` true and `problem_line` "We could not verify your payment."). TWCinc's card shows, in this order: the problem banner, else the note, else "We received your payment. The store is checking it, and your order starts processing once it is verified." while `under_review`, else the four-step tracker (and the courier line when set). `membership` appears on the newest order only, once its payment is verified ("Membership Status": `label`, `line`, the Member / Reference ID row only when `registered` and `sante_member_id`, "Review your details" link to the dashboard when `act`). View Details: Waybill (courier "To be assigned", tracking "Not yet assigned", Shipping fee `shipping_label` "FREE"), Items, Deliver to (when `address`).

## Parity answers to the frontend builder's four notes

1. `sante_member_id`: the registration card now carries `sante_member_id`, always `""` for now. TWCinc fills it when the Subscriber's registration workflow records the Santé ID on a `registered` registration. That workflow is out of this milestone (registration beyond T1, T3, T4, T8), so no registration can reach `registered`, and there is no column for it yet (a later slice adds it with its own migration). The card's `status`, `label`, `line` are unchanged.
2. `member/pack/resubmit/`: accepted fields are `host, visitor_ip, token, account_id, reference_number, amount_sent, sender_bank, sender_first_name, sender_last_name (or sender_name), sender_account_number, transferred_at, proof`. `submit_key` is accepted and ignored: TWCinc's resubmit has none. Double-submit protection is the status rule: the first call moves the order from `rejected` to `pending_review`, so a second call answers `409 not_rejected` ("A new proof can be sent only after the store asks for one.").
3. `member/registration/details/` now reproduces TWCinc's dashboard form: ONE `date_of_birth` (`YYYY-MM-DD`), `terms` and `agreement_version_id` only while the registration read says `terms_required: true` (the order has no Membership Terms acceptance; the same refusals and the same single acceptance row as `onboarding/details/`; ignored when `terms_required` is false), the registration note names the version on record, the two usernames, and a delivery address block (`delivery_address` `{line, barangay, city, province, zip}`). The address is required while the member has no address on file (`registration.needs_address` true: show the block) and is also saved when sent; the ZIP is derived on the server. Errors: `422 invalid_details` with `fields` (`date_of_birth`, `desired_username`, `alternate_username`), `400 invalid_address` with `fields.delivery_address` (a missing line, city or province, from the members address check) and `422 invalid_address` with `fields.province` or `zip` (the ZIP derivation), `409 mobile_not_verified`, `409 wrong_status`. The three-part date of the funnel is only on `onboarding/details/`.
4. Own-proof link: TWCinc's payment panel and rejected dialog show no link to the member's own proof (its proof route exists but nothing links it). Nothing to build; `member/pack/proof/` stays available.

## BIR registration badge (owner-uploaded seal)

Coach Ed's ruling 2026-11-12: the Subscriber uploads the BIR registration badge (Registered shield, BIR, QR, RSN) in the dash; the storefront footer registration block shows it. Migration `sites.0003` adds `SiteBusinessProfile.seal_key` (written; run on scratch only so far).

Owner (cookie session, allowed `Origin`, owner of the site; another account's or a missing site is `404`; a Billing-only site is `403 billing_only`):

| Route | Body | Answer |
|---|---|---|
| `POST /v1/sites/{site_id}/business-profile/seal/` | multipart form, one file field `seal` (JPEG, PNG or WebP, at most 2 MB; the type is checked from the bytes) | `200` the business profile (as `GET .../business-profile/`), now with `has_seal: true` |
| `DELETE /v1/sites/{site_id}/business-profile/seal/` | none | `200` the business profile, `has_seal: false` (harmless when none) |
| `GET /v1/sites/{site_id}/business-profile/seal/` | none | the image itself for the dash preview (`Cache-Control: private, no-cache`), `404 not_found` when none |

`GET /v1/sites/{site_id}/business-profile/` now also answers `has_seal: boolean`. A bad file is `400 invalid_request` with `fields: {seal: ["Upload a JPEG, PNG or WebP image."]}` or `["The file is too large. The limit is 2 MB."]`, or `["Choose an image to upload."]` with no file. 20 writes per account per hour. The file key and any storage URL are never answered.

Storefront (server credential): `GET /storefront/v1/seal/?host=...` serves the image (`image/png|jpeg|webp`, `Cache-Control: private, no-cache`, `X-Content-Type-Options: nosniff`) and `404 not_found` when none is uploaded or the host is not a served Santé site. `GET /storefront/v1/site/` `business.has_seal` is true once one is uploaded. Account disposal deletes the file.
