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
| `onboarding/details/` | `{token, dob_year, dob_month, dob_day, desired_username, alternate_username, terms}` |

`onboarding/completed/` `{token}` -> `{result: "issued"|"open"|"stale"|"spent", session_token}`. The POST alone issues the member session, once per checkout token; `session_token` is non-empty only when `result` is `issued`. `open` goes back to onboarding, `stale` to the closed-checkout page, `issued` and `spent` to the member dashboard (clear the checkout cookie).

`address/zip/` `{province, city, barangay, postal_code?}` -> `{ok, zip_code, zip_precision, error_field, error_message}` (the vendored dataset, server side).

## Secure order-access link (PK7)

The emailed URL is `https://<host>/funnel/membership-pack/access/<signed-id>/`. `link` below is `<signed-id>`. An unknown link is `404 not_found`.

| Route | Body | Answer |
|---|---|---|
| `access/read/` | `{link}` | `{link_open, renewable}`; changes nothing |
| `access/renew/` | `{link}` | `{sent, link_open, renewable}` (3 per hour per member) |
| `access/redeem/` | `{link}` | `{redeemed, token, expires_at}`; a fresh checkout token for the member's latest order, never a session |

## Member sign-in (PK2/PK2b, built) and the rest

`member/signin/*`, `member/me/`, `member/signout/` as `apps/members/api.py`. Still to come in this task, shapes will be added here and the coordinator told before the frontend relies on them: `member/pack/`, `member/pack/proof/{id}/`, `member/pack/resubmit/` (PK8), the policy read (PK12), the member Account and verify-email reads (PK13), and the Subscriber routes under `/v1/sites/{site_id}/pack/orders/` (PK8).
