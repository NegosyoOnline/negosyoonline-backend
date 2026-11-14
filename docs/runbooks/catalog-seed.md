# Santé catalog seed (CAT-1, ClickUp z8uq9mek5w)

The 18 approved Santé products are loaded into the shared catalog by a command, never by a
migration and never on deploy. This runbook covers the migration that adds category and image
fields, the seed command, the product photo store, and how to check each step. Plan:
revision 3, approved by Coach Ed on 2026-11-13.

Seed data in any shared environment needs Coach Ed's explicit word each time. The coordinator
runs it, on staging only, after he says so in the coordinator's session and has seen the file
hash. Production is never covered. Nothing here runs by itself.

## What ships

- Migration `catalog.0002_product_category_image`: `category` and `image_key` on products and
  on revisions. Additive, reversible, no data change.
- Seed file `apps/catalog/seed/sante_catalog_2026-11.json`: the 18 rows, in shop order, from
  TWCinc's 2026-10-28 extraction (the rows with `active=true` that are not the Membership
  Package). Codes are the TWC SKUs, prices whole centavos, `earnings_rate` the members
  discount (`0.4000`, two products `0.2000`), `weight_kg` from the source. The file's header
  names the source and Coach Ed's approval.
- Command `catalog_seed_sante`. Its default is a dry run.
- Owner routes `POST` and `DELETE /v1/sites/{site_id}/catalog/products/{product_id}/image/`,
  and the storefront reads `GET /storefront/v1/catalog/products/` and `/{public_id}/`.
- Arrange (migration `catalog.0003_product_display_order`, additive): the owner route
  `POST /v1/sites/{site_id}/catalog/products/order/` takes `{"product_ids": [...]}`, every
  product of the catalog (active and hidden) exactly once, and sets the shop order for every
  Santé store. Lists and the storefront read `(display_order, id)`; every row starts at 0, so
  the order stays today's creation order until the first Arrange, and a new product (create or
  seed) goes last. An Arrange writes no product revision and no operator audit; to undo one,
  Arrange again. The migration is run like `0002` (staging by the Supervisor, production
  never without Coach Ed's approval) and is reversible (`migrate catalog 0002` drops the
  column and the order falls back to creation order).
- Section images and category FAQs (migration `catalog.0007_product_section_images_category_faq`,
  additive only: three `*_image_key` columns on products and on revisions, and the table
  `CatalogCategoryFaq`; run like `0002`, staging by the Supervisor, production never without
  Coach Ed's approval). Owner routes, all owner-site only:
  - `POST /v1/sites/{site_id}/catalog/products/{product_id}/image/?section=features|advantages|benefits`
    (multipart `file` and `revision`) and `DELETE ...?revision=N&section=...` set, replace and
    clear the one optional image of What's Inside (`features`), Why It Stands Out
    (`advantages`) and What You Get From It (`benefits`). Same store, size and type rules as
    the photo slots, and each change is a product revision. `section` and `slot` are never sent
    together; with neither, slot 1 is used as before.
  - `GET` and `PUT /v1/sites/{site_id}/catalog/categories/{category}/faq/`, with `category` one
    of `barley`, `drinks`, `essentials`, `beauty` (else 404). `PUT {"faq": [{"q", "a"}]}`
    replaces the category's general FAQs (up to 10, question 1 to 150, answer 1 to 1000
    characters, as a product's faq; `[]` clears). General FAQs are shared by every Santé store,
    like the products, so they are keyed by category, not by site. Not a product revision.
  - Payloads: owner product and revisions carry `features_image_url`, `advantages_image_url`,
    `benefits_image_url`; the storefront product reads carry those and `category_faq` (the
    product's category FAQs, `[]` when none; shown after the product's own `faq`). A section
    image URL is `null` when unset and never falls back to a photo.

## Variables (read by `config/settings.py`; names only, values are Coach Ed's to set)

- `PUBLIC_BLOB_READ_WRITE_TOKEN`: read-write token of the public Vercel Blob store (Railway
  `api`).
- `PUBLIC_MEDIA_BASE_URL`: that store's public host, no trailing slash; not a secret. It must
  equal the host the dash and storefronts allow for images.

With either unset the code works and every test passes: reading products needs neither, a
product just has `image_url: null`, and the upload route answers 503
`image_store_not_configured` ("Product images cannot be stored yet: the image store is not
configured."). The seed does not need them unless `--images-dir` is given.

## Steps (staging)

1. Release the PR. Run the reviewed migration under the standing approval:
   `railway ssh -p <project-id> -e staging -s api -- uv run python manage.py migrate --plan`
   (check it lists only `catalog.0002`), then `migrate`. Record the run in `migrations.md`.
2. Dry run. It writes nothing and prints the file hash and one line per code:

   ```
   railway ssh -p <project-id> -e staging -s api -- uv run python manage.py catalog_seed_sante
   ```

   Expected on an empty catalog: `Seed file sha256: <hash>`, 18 lines `CODE: create`, and
   `Dry run, would create 18, skipped 0.` A line `skip (same)` means the product is already
   there; `skip (differs: name, ...)` means the owner changed it, and the seed leaves it.
3. Review the hash and the file against the PR. Show Coach Ed the hash.
4. Only on his word, write. The whole remote command is single-quoted (the remote shell
   re-parses it):

   ```
   railway ssh -p <project-id> -e staging -s api -- 'uv run python manage.py catalog_seed_sante --apply --operator <staff username> --expect-sha256 <hash>'
   ```

   It refuses, writing nothing, without `--apply`, `--operator` (an active staff user),
   `--expect-sha256` equal to the file's hash, or while `SANTE_CATALOG_ENABLED` and
   `SANTE_SETTINGS_ENABLED` are not both on. All creates are one transaction: all 18 or none.
   Each product starts at revision 1, active, with a revision of source `seed` that records the
   operator and the hash. The seed needs no catalog owner.
5. Photos (optional, same word): with the media store configured, add
   `--images-dir <folder>`. For each product just created, a file named `<CODE>.jpg`, `.png`
   or `.webp` in the folder (JPEG, PNG or WebP, 2 MB or less) becomes its image; other files
   are ignored. The folder lives on the machine running the command, never in the repo. Only
   products created by that run get an image; an existing product is not touched, so photos
   for products already seeded are added in the editor.
6. Verify: the owner's product list (`GET /v1/sites/{site_id}/catalog/products/`, signed in as
   the catalog owner) shows 18; the storefront read for a served Santé host shows 18. A second
   run of step 4 prints `Created 0, skipped 18.` and writes nothing.

## Main Santé site (catalog owner)

The main Santé site, whose account owns the shared Santé catalog and template, is the
Live4More site: public id `site_1tqtmvvpn6ksqv3b8b16`, display name "Sante Barley", account
`admin@live4moreonline.store`, staging host `live4more.storefronts-staging.negosyonline.com`.

On staging it is set as the catalog owner (`SanteCatalogOwner` pk 1). The operator sets the
owner in HQ; the seed does not (it needs no owner, as above). This replaces the old E3 launch
step O-E3-6 ("designate the main Santé Barley PH site as catalog owner"): at production launch
the staging database is moved once into production, so the same site stays the owner.

## Adding images to existing products

`catalog_set_images` gives images to products already in the catalog (the seed only images the
products it creates). Files are named `<CODE>.jpg|.jpeg|.png|.webp` (any letter case), each a
JPEG, PNG or WebP of 2 MB or less; the same check as the seed, and one bad file refuses the
whole run. It needs `PUBLIC_BLOB_READ_WRITE_TOKEN` and `PUBLIC_MEDIA_BASE_URL` on the service
for `--apply` only.

1. Copy the files into a folder the `api` service can read (for example `/tmp/product-images/`
   on the running container, copied in with `railway ssh`; the folder is not in the repo).
2. Dry run. It writes nothing and prints one line per file, then a summary:

   ```
   railway ssh -p <project-id> -e staging -s api -- uv run python manage.py catalog_set_images --images-dir /tmp/product-images
   ```

   `CODE: would set`, `CODE: skip (has image)` or `CODE: skip (no such product)`, then
   `Dry run, would set N, skipped M.` Check the codes against the catalog.
3. Only on Coach Ed's word, write (whole remote command single-quoted):

   ```
   railway ssh -p <project-id> -e staging -s api -- 'uv run python manage.py catalog_set_images --images-dir /tmp/product-images --apply --operator <staff username>'
   ```

   It refuses, writing nothing, without `--operator` (an active staff user), while
   `SANTE_CATALOG_ENABLED` and `SANTE_SETTINGS_ENABLED` are not both on, or with the media
   store not configured. All images are one transaction: all or none. Each product set gets a
   revision (source `seed`, naming the operator; the revision's file hash is the SHA-256 of the
   image). A product that already has an image is skipped unless `--replace` is added, and the
   replaced file is removed once the change is committed. Output never shows a key or token.
4. Verify: the owner list shows `image_url` for those products and the storefront read does too.

## Undo

There is none by deletion: products and revisions are never deleted. A mistaken seed row is
hidden with `is_active=false` through the editor (an owner write, with its own revision). An
out-of-stock product is hidden the same way: the catalog has no stock field.

## Local use

Local checks use SQLite. Set `SANTE_SETTINGS_ENABLED=1` and `SANTE_CATALOG_ENABLED=1`, run
`manage.py migrate`, then the dry run, then, to try the write on the local database only, the
`--apply` line with a local staff user. Never run it against a shared database without the
steps above.
