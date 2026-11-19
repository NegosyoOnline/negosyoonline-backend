# Xendit for the Membership Pack runbook

ClickUp z8uq9mgkk2 (subtask z8uq9mgknz, backend). Coach Ed, 2026-11-18: "we will activate xendit in our sante membership funnel, then lets deactivate the bank transfer"; plan approved ("xendit plan approved"). This supersedes, for the Santé Membership Pack only, the 2026-11-11 ruling that hid Xendit and the 2026-11-17 ruling that deleted it. Subscriber SaaS billing stays bank transfer. Out of scope: the monthly CSV reconciliation, refunds, per-order metering.

Apps: `apps/payments` (the owner's key, the client, the webhook, the reconcile task) and `apps/pack` (`xendit.py`, placement, the payment-status route). Every call uses the site owner's OWN Xendit key, entered on the dash Payments page; no platform or TWC key exists anywhere.

## Source (TWCinc, read only, flow A: per-site NegosyoOnline invoices; adapted, never pasted, nothing imported)

All under `/Users/edgeronilla/Dev/twcinc/app/`:

| Piece | TWCinc path | Here |
|---|---|---|
| HTTP client, error map, find by reference, parse | `negosyoonline_orders/xendit_client.py` | `apps/payments/xendit_client.py` (stdlib `urllib.request`, no new dependency) |
| Credential and event tables, Fernet | `negosyoonline_orders/models.py` 77-127, `secrets.py` | `apps/payments/models.py`, `crypto.py` |
| Save and clear the key, live/test guard, key failed | `negosyoonline_orders/payment_methods.py` | `apps/payments/services.py` |
| PUT/DELETE routes | `negosyoonline_orders/api.py` 85-98 | `apps/payments/api_xendit.py` |
| Webhook | `negosyoonline_orders/webhooks.py`, `xendit_registry.py` | `apps/payments/webhooks.py` |
| Reconcile | `negosyoonline_orders/reconcile.py`, `tasks.py` | `apps/payments/reconcile.py`, `tasks.py`, `CELERY_BEAT_SCHEDULE` |
| Placement, invoice, continue, retry | `negosyoonline_sante_pack/placement.py` | `apps/pack/placement.py`, `apps/pack/xendit.py` |
| Paid, expired, mismatch, invoice-less, window | `negosyoonline_sante_pack/payments.py` | `apps/pack/xendit.py` (metering removed) |
| Return page verify | `negosyoonline_sante_pack/site_views.py` `status_view` | `POST /storefront/v1/checkout/payment-status/` |

Not ported: the legacy TWC adapter (non-constant-time token), platform billing, payouts, metering.

## The switches

| Where | Name | Meaning |
|---|---|---|
| Railway `api` and `worker` | `XENDIT_ENABLED` | `1` turns on the owner's key routes, the Xendit option at pack checkout, the webhook and the reconcile task. Unset (the default): exactly as before Xendit. The worker reads only this switch (it has no `PAYMENT_METHODS_ENABLED`) and reads each site's credential directly. Needs the key below; `check --deploy` reports `payments.E001` otherwise. |
| Railway `api` and `worker` | `PAYMENT_CREDENTIALS_ENCRYPTION_KEY` | Already set (a Fernet key). The site's Xendit key and webhook token are encrypted under it (setting `PAYMENT_CREDENTIALS_KEY`). No new variable. |
| Dash Payments page | per-site Xendit switch | `xendit_enabled`: the owner's on/off, allowed only with a working key. |
| Dash Payments page | per-site "Bank or e-wallet transfer" | The existing `direct_enabled` switch. Turning it off is the later "bank transfer off" step: it needs no code and hides the option from NEW checkouts only. |

A Santé site only: a site of another template cannot save a key (404) and reads as before. Xendit is available to buyers only while the platform switch is on, the owner switch is on, a working key is saved and it has not failed.

## Staging, production and test keys

`RAILWAY_ENVIRONMENT_NAME` decides: `production` accepts live keys (`xnd_production_...`) only; any other Railway environment (staging) accepts test keys (`xnd_development_...`) only; a local run takes either. A key is checked with one `GET /balance` at Xendit before it is stored.

`XENDIT_LIVE_KEY_SITES` (staging only; default empty = exactly the rule above): comma-separated site public ids (the `site_...` id; spaces and blank entries are ignored). On a non-production Railway environment a listed site may also save a live key (`xnd_production_...`); test keys stay allowed for it and every other site stays test-only with the same error. Production (live only) and local runs ignore it. It is a Railway variable on staging that the Manager sets after Coach Ed approves; never set it on production. The key itself is never logged.

## Owner routes (contract: `xendit-contract-z8uq9mgkk2-v2.md` in the SANTE Supervisor worktree)

- `GET/PATCH /v1/sites/{id}/payment-methods/`: the read carries the `xendit` block with `offered: true` only while Xendit is offered; otherwise the body is exactly what it was. The PATCH accepts `xendit_enabled`.
- `PUT /v1/sites/{id}/payment-methods/xendit/` `{api_key, webhook_token}`, `DELETE` the same path (answers `awaiting_xendit_orders`). Throttled with the other payment writes. The webhook URL is `https://<api host>/v1/webhooks/xendit/<webhook_key>/`; `webhook_key` is random, unique, made on the first save and kept after a remove.
- Nothing returns, logs or audits a secret. The audit events carry the site and the key mode only.

## The webhook (`POST /v1/webhooks/xendit/{webhook_key}/`, no sign-in)

1. 404 while `XENDIT_ENABLED` is off, for an unknown key, a deleted site, a non-Santé site or a cleared token.
2. The token header `x-callback-token` is compared with that site's own decrypted token with `hmac.compare_digest`: 401 `invalid_token` on any mismatch. Then a 64 KB cap (413), then the JSON.
3. One transaction: the site row is locked, then the order row; the order is matched by (site, our reference) only, so a webhook verified for one site never touches another site's order. The event row `XenditEvent` (unique per site and event id: the `webhook-id` header, else `invoice_id:status`) is inserted in a savepoint; a duplicate rolls the whole transaction back and answers 200 with no effect. Any other failure answers 500 so Xendit redelivers.
4. `PAID` or `SETTLED` with exactly PHP 1,488.00: the order becomes `verified` (`payment_method` xendit, `paid_at` = Xendit's time), benefits on, the Santé registration starts and moves to ready as for an approved proof, and the buyer gets the existing "payment verified" email after commit. A different amount or currency: `xendit_mismatch`, no benefits, kept live. `EXPIRED`: `xendit_expired`. Anything else is only recorded. A second invoice paid after the first, or a payment on an order that is no longer live while the member has a newer live order or released contacts, is recorded as an event and never applied.
5. A browser redirect, a return page poll or anything the buyer sends can never mark an order paid. The return page asks `POST /storefront/v1/checkout/payment-status/`, which at most once every 5 seconds reads the invoice at Xendit with the site's own key and applies it through the same handler.

## Order lifecycle

`awaiting_xendit` (live, benefits off, no registration) then `verified`; or `xendit_expired` (not live: the buyer can start a new checkout with the same contacts); or `xendit_mismatch` (live: the store checks it). The order is made with no proof; the invoice (24 hours, the server amount, our reference `np-{site row id}-{24 hex}`) is created after commit with the site's key, adopting the newest unexpired invoice found by that reference first, outside the order lock with a short cache claim. If Xendit cannot be reached the order stays `awaiting_xendit` with no invoice and the return page offers Try again (`retry: true`, 5 an hour per order). Onboarding routes answer 409 `payment_pending` until it is paid.

### The member is made on payment (hotfix z8uq9mgpt5, Coach Ed 2026-11-19)

Quote: "the creation of account must be after successful payment". Placing a Xendit order creates **no Member**: `PackOrder.member` is null, and the order keeps the buyer's typed `buyer_mobile` and `buyer_email` and `onboarding_challenge_id` (the used onboarding challenge that proved the mobile, still unbound). When Xendit confirms the payment (webhook or the server-side read, same transaction, same locks as before) `apps/pack/buyer.attach_member` makes the member from those, consumes the challenge, attaches it, clears the kept copies, and only then turns benefits on and starts the registration and the buyer email. The matching rules are the checkout's own (`checkout.match_existing`): a contact held by someone with benefits or a live order refuses.

- Contact taken before the payment, or the proof of a returning buyer gone: the order goes to `xendit_mismatch` ("Under review") with an `xendit_mismatch` order event whose `new` says `{"reason": "contact_in_use" | "mobile_not_verified", "field": ...}`, the `XenditEvent` outcome is `contact_conflict`, and benefits, registration and the buyer email wait for a person. Nothing is merged and no member is created. Nothing in the dash is customer-facing new: the status shows as before.
- A failed, expired, abandoned or under-review Xendit order holds no contact, so the same mobile and email can check out again at once (the next order is a new order; the old one stays as the record). The same token's Try again works as before.
- Orders placed before the hotfix have their member from placement. A member whose every order is an unpaid Xendit order with no benefits is now "abandoned" (`checkout.abandoned`): the next checkout with those contacts releases them through `release_abandoned` / `release_contacts` (the row and its orders stay; nothing is deleted). A paid member, or one with a bank order, is never touched. A payment arriving later on such an order whose contacts were released goes to `xendit_mismatch` with reason `contacts_released` (a non-live one stays recorded as superseded, as before).
- A bank-transfer order follows the same rule since task z8uq9mgpzd (see `docs/runbooks/pack.md`, "Bank transfer: the member is made on approval"); `attach_member` is called by the owner's approve.
- Readers that may see an order with no member: the status route and onboarding (409 `payment_pending`), the owner order list and detail (mobile masked from the kept number), the journey, the prospects list, the buyer notices (no email without a member), the Meta event (hashed from the kept contact), the members by-order route (the order is left out), the export (member null, kept contact included) and disposal and the details purge (kept contact blanked).
- Operator check on staging: after a failed or abandoned Xendit checkout `Member.objects.count()` does not grow; after the payment it grows by one.

## Reconcile (`payments.xendit_reconcile`, every 15 minutes, beat and worker)

Reads each awaiting order older than 15 minutes with its site's key and applies it as the webhook does. A 401 sets `key_failed_at` and emails the owner once; Xendit then is not offered until a new key is saved. An order with no invoice two hours after placement gets one last lookup, then expires. The window sweep expires an order one hour after its invoice expired (or three hours after placement with none), with or without a key; a late authenticated PAID still applies.

`/readyz/` field `xendit_queue` goes false when an order stays awaiting past this window, so the uptime check alerts.

## Retention

Account disposal wipes the key and token ciphertext (the row and its webhook key stay). Exports carry whether a key is saved, its mode and failure time, the order's provider ids and times; never a key, token, ciphertext, webhook key or invoice link. `XenditEvent` holds no personal data (ids, a body hash, an outcome) and is kept as the receipt.

## Local preview with a fake Xendit (no real call)

Local only: it needs `DJANGO_DEBUG=true`, so it cannot run on Railway.

```
export DJANGO_DEBUG=true DJANGO_SECRET_KEY=local-preview-only
export XENDIT_ENABLED=1 XENDIT_FAKE=1 PAYMENT_METHODS_ENABLED=1 PACK_ENABLED=1
export XENDIT_FAKE_RETURN_ORIGIN=http://localhost:3002   # optional, see below
export PAYMENT_CREDENTIALS_ENCRYPTION_KEY=$(uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
uv run python manage.py runserver 127.0.0.1:8000
```

(plus the other local switches the pack checkout already needs.) In the dash Payments page enter any key that starts `xnd_development_` (one containing `rejected` is refused, one containing `down` is "not reachable") and any token, switch Xendit on, then buy the pack with "Pay online". The buyer lands on `http://127.0.0.1:8000/fake-xendit/invoice/<id>/`, a page that plays Xendit: Pay (signed callback to the real webhook), Pay a different amount (mismatch), Let it expire. The invoice's return address is the storefront's `/funnel/membership-pack/checkout/return/` on the site's public host. The fake also needs no `RAILWAY_ENVIRONMENT_NAME` (it refuses to run on any Railway environment, and `check --deploy` errors with `payments.E002` if `XENDIT_FAKE` is set there). `XENDIT_FAKE_RETURN_ORIGIN` is a local environment variable only (never a Railway variable): when it is set and the fake is on, the fake page sends the buyer back to that origin (the storefront run locally, for example `http://localhost:3002`) with the same path, instead of the site's public host. It is ignored otherwise and never touches a real invoice. The fake lives in memory: restarting the server forgets its invoices.

## Verifying on an environment (Supervisor, after the migrations and `XENDIT_ENABLED=1`)

1. Dash Payments shows the Xendit card; Coach Ed saves his Xendit TEST key and token and copies the webhook URL into his Xendit test dashboard.
2. A pack order by Xendit: the pay page opens the invoice; paying it in Xendit's test mode makes the webhook mark the order paid and the return page goes to onboarding.
3. `migrate --plan` shows no pending operations; `/healthz/` ok.
