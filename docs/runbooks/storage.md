# Storage Runbook: the media ledger

ClickUp z8uq9md33w. One row for every upload kind in the code, built from every call to `apps.common.private_files.save` and every write in `apps/catalog/media.py` on main 92ed859. The test `apps/common/tests/test_storage_ledger.py` fails when a folder in the code has no row here, so a new upload kind cannot ship without being listed.

There is no R2 in this backend. Two stores exist: the private Vercel Blob store (`apps/common/private_files.py`) and the public Vercel Blob media store (`apps/catalog/media.py`).

## The ledger

A private file has a key and no URL. Its bytes leave the backend only through the route named in the row, which checks who is asking. Content types are checked from the file's first bytes (images: JPEG, PNG, WebP; PDF where noted), never from the file name or the browser's claim.

| Upload kind | Code | Store | Folder (key prefix) | Who may read it, through which route | Kept how long, and the rule that says so |
|---|---|---|---|---|---|
| Payment QR code of a site's bank or e-wallet account | `apps/payments/services.py` (`QR_FOLDER`) | private | `sante/qr/site-<row id>/` | The Subscriber's own dashboard never receives a key. A shopper sees an active account's QR through `GET /storefront/v1/payment-accounts/{account_id}/qr/?host=` (server credential), only while payment methods work for the site (`docs/runbooks/sante-agreement.md`) | Replaced or removed by the owner: the old file is discarded. Account disposal deletes it (`docs/runbooks/retention.md`, site payment accounts) |
| Santé membership agreement PDF versions | `apps/sante/agreement.py` (`FOLDER`) | private | `sante/agreement` | `GET /storefront/v1/pack/agreement/{version_id}/pdf/?host=` (server credential) serves a published version only, after a SHA-256 re-check. Written by an operator through the management command only (`docs/runbooks/sante-agreement.md`) | A published version never changes and no rule deletes it: no rule; kept. A draft can be deleted by the operator, which discards its file |
| Pack payment proof, at checkout (Pack proof) | `apps/pack/review.py` (`PROOF_FOLDER`) | private | `sante/proofs/site-<row id>/` | The owning Subscriber: `GET /v1/sites/{site_id}/pack/orders/{order_id}/proofs/{proof_id}/`. The member who sent it: `POST /member/pack/proof/` (own proofs only) | Account disposal deletes every proof file and keeps the rows (`docs/runbooks/retention.md`, Pack). No other rule: no rule; kept |
| Pack payment proof, at placement | `apps/pack/placement.py` (`PROOF_FOLDER`, same folder) | private | `sante/proofs/site-<row id>/` | Same two routes as above | Same as above |
| Shop order payment proof (behind `SHOP_ENABLED`, off by default) | `apps/shop/constants.py` (`PROOF_FOLDER`; written by `apps/shop/proofs.py`) | private | `sante/shop-proofs/site-<row id>/` | The owning Subscriber only: `GET /v1/sites/{site_id}/shop/orders/{order_id}/proofs/{proof_id}/`. Never sent to the browser as a key | Account disposal deletes every proof file and keeps the rows (`apps/shop/retention.py`). No other rule: no rule; kept |
| Site registration badge (seal) | `apps/sites/seal.py` (`SEAL_FOLDER`) | private | `sites/seal/site-<row id>/` | The owner's dashboard preview: `GET /v1/sites/{site_id}/business-profile/seal/`. The storefront footer: `GET /storefront/v1/seal/?host=` (server credential) (`docs/runbooks/membership-package-routes.md`) | Replaced or removed by the owner: the old file is discarded. Account disposal deletes it (`docs/runbooks/retention.md`) |
| Billing transfer proof | `apps/billing/proofs.py` (`FOLDER`) | private | `billing/proofs` | The owning Subscriber: `GET /v1/sites/{site_id}/billing/payments/{payment_id}/proof/`. Operators on the Finance page: `POST /staff/v1/finance/payments/proof/` on the hq host, each read audited as `billing_proof_viewed` (`docs/runbooks/billing.md`) | A billing record: kept five years and no route deletes it; no retention rule touches billing or proof records (`docs/runbooks/billing.md`, `docs/runbooks/retention.md`) |
| Billing invoice scan | `apps/invoices/scans.py` (`FOLDER`) | private | `billing/invoice-scans` | The owning Subscriber: `GET /v1/sites/{site_id}/billing/invoices/{invoice_id}/copy/`, only for a verified scan of a recorded invoice (every other case the same 404). Operators on the Finance page through the staff routes, each read audited as `billing_invoice_scan_viewed` | A billing record: kept with the invoice and no route deletes it; no retention rule touches billing records (`docs/runbooks/billing.md`, `docs/runbooks/retention.md`) |
| Product photo | `apps/catalog/media.py` (`FOLDER`) | public | `catalog/products` | Anyone: it is shown on every Santé storefront at `PUBLIC_MEDIA_BASE_URL` plus its key, read through the catalog routes' `image_url` (`GET /storefront/v1/catalog/products/`). Uploaded and removed by the owner: `POST` and `DELETE /v1/sites/{site_id}/catalog/products/{product_id}/image/` (`docs/runbooks/catalog-seed.md`) | A photo belongs to the catalog, not an account: disposal leaves it (`docs/runbooks/retention.md`). Replaced or removed by the owner: no further rule stated; kept otherwise |

## Rules that hold for every row

- No route lets a request choose a store or a folder. Each caller names its store and its folder in code; a key is made on the server (`<folder>/<32 random hex><extension>`), never from anything a person typed; a key a request supplies is refused unless it has that shape.
- No private store (no `PRIVATE_BLOB_READ_WRITE_TOKEN`) and `DJANGO_DEBUG` off means no private store at all: every private upload fails closed, and nothing is written to the server's own disk. With DEBUG on, a developer's machine uses the ignored local folder `PRIVATE_FILES_DIR`.
- The public store needs both `PUBLIC_BLOB_READ_WRITE_TOKEN` and `PUBLIC_MEDIA_BASE_URL`; with either missing, a photo upload answers "not configured". Reading products never needs the store.
- Tokens are sent as request headers only and appear in no key, URL, log line or error.

## Staging facts, 2026-11-13 (read-only check, names only, no value printed; from the ClickUp comment on z8uq9md33w)

- On the `api` service `PRIVATE_BLOB_READ_WRITE_TOKEN` is set.
- `PUBLIC_BLOB_READ_WRITE_TOKEN` and `PUBLIC_MEDIA_BASE_URL` are not set: the public photo store is not configured on staging, so a product photo could not be loaded.
- An anonymous fetch of a stored private file (a payment QR code) from the private store host answered HTTP 403.
- Production: no Railway production services exist yet; nothing was checked there.

## Adding an upload kind

Add its row here in the same pull request, with its store, folder, readers and retention (write "no rule; kept" where that is the truth). The test reads the folders from the source and fails until a row names the folder.
