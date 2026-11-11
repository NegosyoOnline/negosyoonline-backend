# Membership Package: storefront route shapes

Contract between the backend task (z8uq9me9qv) and the frontend task (z8uq9me9qw). Every route is `POST`, JSON unless noted, under `/storefront/v1/` of the storefront surface, and carries the storefronts server's credential. Django sets no cookie: the storefronts server keeps the checkout token (`__Host-no_checkout`, 2 h sliding) and the member session token (`__Host-no_member`, 30 d) and passes them as data. Every body carries `host` (the site host being served) and `visitor_ip`. While `PACK_ENABLED` is off, and for an unknown, foreign or non-Santé host, every route answers `404 not_found`.

Errors have the repository shape `{"error": {"code", "message", "fields"?}}`; `fields` maps a field name to a list of messages. A 429 is `rate_limited`. Strings are TWCinc's, character for character.

## Checkout (PK4, built)

| Route | Body | Answer |
|---|---|---|
| `checkout/contact/` | `token?, full_name, mobile, email, delivery_address?, agreement_version_id, agreement_accept, enrollment_authorized` | `{token, expires_at}`: `token` is the raw checkout token, answered once on the first call, `""` afterwards |
| `checkout/check/` | `mobile, email` | `{errors: {field: [msg]}}` (marks a contact as the buyer finishes typing) |
| `checkout/state/` | `token` | `{needs_code, mobile, verified, expires_at}`; 409 `checkout_expired` when the token is gone |
| `checkout/code/send/` | `token` | `{status}`; the SMS code of a returning buyer (only when `needs_code`) |
| `checkout/code/verify/` | `token, code` | `{status: "verified"}` |
| `checkout/place/` | multipart: `token, submit_key, account_id, reference_number, amount_sent, sender_bank, sender_first_name, sender_last_name, sender_account_number, transferred_at, proof` (file, 4 MB) | `{order_number, replay}` |

Shapes found by the scripted flow: `delivery_address` is an object (omit it when empty), `agreement_version_id` is the active version's id as a string, `account_id` is the payment account's public id (`pay_...`), `transferred_at` is `YYYY-MM-DDTHH:MM` (Manila).

Codes: `invalid_contact` 422 (with `fields`), `contact_in_use`, `checkout_expired` 409, `checkout_used` 409, `no_contact` 409, `mobile_not_verified` 409, `method_unavailable` 409, `store_unavailable` 503, agreement and enrollment refusals as the sub-spec.

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
| `member/registration/details/` | `dob_year, dob_month, dob_day, desired_username, alternate_username, terms` |

## Subscriber order review (PK8)

On the owner API, `https://<api>/v1/sites/{site_id}/pack/orders/...`, signed in as the Subscriber (the dash session). Foreign, missing or non-Santé site: `404 not_found`; a suspended or ended site: `403 billing_only`; `PACK_ENABLED` off: `404`. Order and proof ids are public ids; money is centavos.

| Route | Body | Answer |
|---|---|---|
| `GET .../pack/orders/?filter=to_review\|rejected\|all` | | `{orders: [row]}` newest 200 |
| `GET .../pack/orders/{order_id}/` | | detail |
| `GET .../pack/orders/{order_id}/proofs/{proof_id}/` | | the file, `Cache-Control: private, no-store`, `nosniff` |
| `POST .../pack/orders/{order_id}/approve/` | `{state_version}` | detail |
| `POST .../pack/orders/{order_id}/reject/` | `{state_version, reason_code, other_text?}` | detail |

`row`: `{id, order_number, buyer_name, mobile_masked, method, payment_status, created_at, state_version}`. `detail` is `row` plus `{price_centavos, currency, delivery_address, rejection_lock_active, submissions, events}`; a submission is `{id, seq, account_kind, account_provider_name, account_name, account_number_last4, reference_number, amount_sent_centavos, sender_bank, sender_name, sender_account_number, transferred_at, submitted_at, content_type, decisions: [{decision, reason_code, reason_label, other_text, actor_label, decided_at}]}`.

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
