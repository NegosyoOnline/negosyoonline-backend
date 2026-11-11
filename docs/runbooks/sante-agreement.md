# Santé Membership Agreement and Membership Pack reads (PK1)

One central agreement PDF serves every Santé site. It is published by an operator with a
command; there is no upload page. Switch: `SANTE_AGREEMENT_ENABLED` (`1` turns it on, off by
default). Off, the three storefront routes answer the same 404 as an unknown site, and the
command refuses. The migration is `sante.0002_membership_agreement` (three new tables, no
existing table changed); it is run by the reviewed release step, never by the build.

## Routes (storefront surface, server credential, `Cache-Control: no-store`)

- `GET /storefront/v1/pack/?host=` price (148800 centavos, PHP), `available`, the active
  agreement `{id, label, effective_date}`, the site's payment accounts (no file key).
  Available only with a payment account to pay to (direct transfer on) and a published version.
- `GET /storefront/v1/pack/agreement/{version_id}/pdf/?host=` a published version's PDF,
  inline, `nosniff`, SHA-256 re-checked on every read. A draft is never served.
- `GET /storefront/v1/payment-accounts/{account_id}/qr/?host=` the QR image of an active
  account of the host's site.

The surface wrapper sets `no-store` on every answer, which is stricter than TWCinc's
`private, no-cache` for the PDF; the storefronts server must not rely on browser caching of it.

## Publishing (as TWCinc: authorization first)

Only the Santé catalog owner site writes the agreement, and only an operator acts for it (an
active staff user named with `--operator`); there is no Subscriber path and no admin screen.
Every step is in the operator audit log. The PDF stays out of chat, logs and output
(`--file -` reads standard input; `--file <path>` a file on the machine running the command).
Run on the `api` service, single-quoting the whole remote command.

1. A catalog owner is set (`sante_catalog_owner_set`, existing).
2. `authorize --operator NAME --document-date D --note "Santé letter ref" --covers-pdf-hosting --covers-branding --covers-online-acceptance --file <letter>` stores Santé's written authorization (PDF, JPEG or PNG, 20 MB) in the private store.
3. `verify --operator NAME --id N` puts it in force (needs all three grants and an intact file).
4. `draft --operator NAME --title T --label L --effective-date D --file -` stores the PDF as a draft; prints label and SHA-256.
5. `publish --operator NAME --label L` makes it the active version. Refused with no verified, unrevoked authorization.

`update` and `delete` change or remove a draft only; a published version never changes.
`revoke --operator NAME --id N --reason R` withdraws the authorization: the pack becomes
unavailable and no version is served, the rows and files are kept as evidence. `status` lists
versions and authorizations without file contents. Audit actions: `sante_agreement_version_created|updated|deleted`, `sante_agreement_published`, `sante_agreement_authorization_recorded|verified|revoked`.

## Data and retention

The agreement and Santé's letter are company documents, not personal data of a Subscriber, site
or buyer: nothing to export, dispose of or register with retention. Operator names sit on the
authorization rows and in the audit log (kept whole, no personal value). The catalog owner site
cannot be disposed of while it is the owner (existing rule), so `master_site` never dangles.
