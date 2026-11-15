# Meta Pixel and Conversions API (`apps/meta_ads`)

Backend half of ClickUp z8uq9md3n5. The browser Pixel and the dash tab are the frontend PR.

## Switch and variables (names only)

| Name | Where | Meaning |
|---|---|---|
| `META_EVENTS_ENABLED` | Railway `api` and `worker` | `1` turns everything on. Unset = off: every route answers the 404 of an unknown site, no table is read or written, nothing is sent, the site call answers `meta_pixel_id: ""`. |
| `PAYMENT_CREDENTIALS_ENCRYPTION_KEY` | `api` and `worker` | A Fernet key. Read by `config/settings.py` as `META_CREDENTIALS_KEY`; the Subscriber's Meta access token is stored encrypted under it. The worker decrypts it to send. A wrong or missing key gives `token_unreadable` and never a crash. |
| `STOREFRONT_ENABLED` | `api` | Required: the events route and the Pixel ID live on the storefront surface. |

`manage.py check --deploy` refuses a half-set switch (`meta.E001`).

## Routes

- Owner (signed in, Santé site only): `GET/PUT /v1/sites/{site_id}/meta/`, `DELETE .../meta/token/`, `POST .../meta/test/`. No route returns the token.
- Storefront (server secret): `POST /storefront/v1/meta/events/`; `GET /storefront/v1/site/` gains `meta_pixel_id`.
- `PlaceIn` of `POST /storefront/v1/checkout/place/` gains `meta_consent` ("1" = accepted), `fbp`, `fbc`, `user_agent`, `source_url`. They are read only when `meta_consent` is "1".

## Rules

- Nothing is stored or sent for a visitor without consent (`consent: true` on the events route, `meta_consent=1` at placement).
- `Purchase` is sent once per order when the buyer first opens the onboarding verify page (the storefront asks `/storefront/v1/meta/purchase/`; it needs consent on that visit and the order's saved context), `AddPaymentInfo` at placement; both are built in Django from the order. They are refused by name on the events route. Approval sends nothing. Each send records Meta's `events_received` and `fbtrace_id` on its `MetaDelivery` row (a number and a trace id only).
- Delivery evidence (`MetaDelivery`) holds the event name and id, outcome, attempts, HTTP status and Meta's numeric error code. No payload, no token, no message text.
- Browser identifiers kept for an order (`MetaOrderContext`) are deleted when its Purchase is sent, or after 30 days (retention rule `meta_order_contexts`).
- Beat entry `meta-ads-sweep` (300 s) re-queues waiting Purchase and AddPaymentInfo deliveries and expires any open delivery after 7 days.
- The Graph API version is the constant `GRAPH_VERSION` in `apps/meta_ads/send.py`.
