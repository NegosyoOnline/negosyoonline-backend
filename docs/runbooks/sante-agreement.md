# Santé Membership Agreement and Membership Pack reads (PK1)

One central agreement PDF serves every Santé site. It is published by an operator with a
command; there is no upload page. Switch: `SANTE_AGREEMENT_ENABLED` (`1` turns it on, off by
default). Off, the three storefront routes answer the same 404 as an unknown site, and the
command refuses. The migration is `sante.0002_membership_agreement` (two new tables, no
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

## Publishing

The PDF stays out of chat, logs and output. Run on the `api` service (see `railway-ssh-quoting`
note in the team memory: single-quote the whole remote command):

```bash
manage.py sante_agreement_publish draft --label 2026-11 --effective-date 2026-11-15 --file -  < agreement.pdf
manage.py sante_agreement_publish publish --label 2026-11 --reason "Authorized by <who>, filed at <where>"
```

`--file -` reads the PDF from standard input, so nothing is copied to the service's disk;
`--file <path>` reads a file on the machine running the command. `draft` prints the version
label and the SHA-256, never contents. `publish` writes the audit action
`sante_agreement_published` (kept whole) with the reason, the label, the SHA-256 and the
previous active version. A published version is never changed; publishing a newer one moves the
pointer and the older one stays readable. Limits: PDF only (checked by its first bytes), 20 MB.

## Data and retention

The agreement is a company document, not personal data of a Subscriber, site or buyer: nothing
to export, dispose of or register with retention. The audit row holds no personal value.
