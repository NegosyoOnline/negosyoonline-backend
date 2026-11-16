# Customer inquiries: switches, email replies and the contact form

The Inbox of a Santé store (`apps/inquiries`). This runbook covers the outbound email half (G3 PR2a). Website chat is in `docs/runbooks/storefront.md`; the Pancake connection is in the code docstrings of `apps/inquiries`. **Inbound email waits on D2** (Coach Ed's decision on the receiving domain): there is no webhook, no `c-`/`s-` routing address and no bounce state yet, and nothing here needs a Railway variable beyond the switches.

## The three switches

All are off by default. A switch is set on the `api` service only, by Coach Ed or the manager; no session changes one.

| Switch | Opens | Needs |
|---|---|---|
| `INQUIRIES_ENABLED` | The owner's routes under `/v1/sites/{id}/inquiries/`, including email replies on a stored `em_` conversation | `PAYMENT_CREDENTIALS_ENCRYPTION_KEY` (`inquiries.E001`) |
| `INQUIRY_WEBCHAT_ENABLED` | The two storefront chat routes | `INQUIRIES_ENABLED` and `STOREFRONT_ENABLED` (`inquiries.E002`) |
| `INQUIRY_CONTACT_INBOX_ENABLED` | A stored copy of each storefront contact-form message, as an `email` conversation | `INQUIRIES_ENABLED` and `STOREFRONT_ENABLED` (`inquiries.E003`) |

Email replies have no switch of their own: they need a conversation with an `em_` ref, and today only the contact form makes one. All three need `inquiries.0002` applied (staging: yes, 2026-11-15; production: not run). Run `manage.py check --deploy` after changing any of them.

## The contact form in the Inbox

With `INQUIRY_CONTACT_INBOX_ENABLED` on, `send_contact_message` first sends the usual email to the store (unchanged, byte for byte, whether the switch is on or off) and then stores:

- a NEW `email` conversation for every stored message. It is never merged by the typed address, because that address is only a claim. `auth_flag` is `unverified_sender`, the conversation is open and unread, `contact_name` and `display_name` come from the name, `contact_email` from the typed address and `external_id` is a server-made random `em_` ref;
- one incoming `InquiryMessage` whose body is `Subject: <subject>`, a blank line and the message, cut to 4,000 characters.

Each site also has its own switch, `email_enabled` on the channels API (`PUT .../inquiries/channels/`, Settings > Inbox, default off; `contact_inbox_available` in the same answer is the global switch): a copy is stored only when the global switch and the site's switch are both on, and the email goes either way.

Nothing is stored for the honeypot, with the switch off, or while the inquiries tables are absent (`stored_tables_exist()` is false; then no inquiries table is read). A failure while storing is caught and logged by class name only; the visitor still gets "sent". Caps, through `apps/common/throttle`, per day: 200 stored messages per site and 30 per typed address per site (the key holds a SHA-256 of the lowercased address, never the address). Over a cap the email still goes and nothing is stored; it is not a failure.

## Replying by email

`POST /v1/sites/{id}/inquiries/conversations/{ref}/reply/` on an `em_` ref sends one email.

- The recipient is the conversation's own `contact_email`, never a field of the request (the schema refuses any extra field). One recipient, no CC or BCC.
- The sender rule (D5): the store's own address from `STORE_SENDERS` (`sender_address(site)`) is required, whether `STORE_EMAILS_BRANDED` is on or off. An inquiry reply is always rendered with the branded layout. No sender for the store means nothing is sent, and the platform address is never used instead. Chat replies are unaffected.
- Subject: `A message from <store>` (there is no subject field to reply to). Body: the owner's plain text, at most 4,000 characters (the route allows 2,000); no link injected, no tracking.
- Reply-To is the store's usual one, the owner's verified account email, until a receiving domain exists (D2). `send_store_email` already takes a `reply_to` argument for the later `c-` address.
- Footer: `You are receiving this because you contacted <store>.` **D6 placeholder wording: awaiting Coach Ed.** It is the constant `FOOTER_REASON` in `apps/inquiries/stored.py`.
- An `unverified_sender` conversation can still be replied to; the thread shows `auth_flag` and the dash decides what to say.
- Send records: each reply leaves an `EmailMessage` row, category `inquiry_reply` (see `retention.md`).

### Limits

Per site, 20 inquiry emails an hour and 100 a day (keys carry the site: one store's flood never uses another's budget). Over a limit nothing is sent and the audit row is `blocked_rate`.

### Codes and the owner's fixed texts

The reply result is `{state, error_code, created_at}` plus, for an email reply that did not go, `message`. `InquiryReply.error_code` is a closed list and changing it needs a migration, so the audit row keeps an existing code and the specific one is kept on `InquiryMessage.error_code`; the owner's result shows the specific one.

| Specific code (shown) | Audit row | State | Text shown to the owner |
|---|---|---|---|
| `sender_not_ready` | `not_configured` | `failed` | Email replies are not set up for this store. |
| `no_recipient` | `not_found` | `failed` | This conversation has no email address to reply to. |
| `email_failed` | `provider_down` | `failed` | The email could not be sent. Please try again shortly. |
| `rate_limited` | `rate_limited` | `blocked_rate` | Too many emails have been sent from this store. Please wait and try again. |

No exception text, mail-server reply or credential is stored, logged or returned: a failed send logs the exception's class name only. A failed reply does not move the conversation. Sent means accepted by the mail server; delivered and bounced states wait on D2.

## Testing it locally

Use the console mail backend: leave `EMAIL_HOST` unset so every email is printed to the log, and set `DJANGO_DEBUG=true`. Run with `INQUIRIES_ENABLED=1 STOREFRONT_ENABLED=1 INQUIRY_CONTACT_INBOX_ENABLED=1`, `STORE_SENDERS=<web-address-name>=orders@example.test`, a Santé site and `STOREFRONT_SERVER_SECRET`. Post to `/storefront/v1/contact/` (the storefront test cases show the body), then list `GET .../conversations/`, then reply. The test suite uses the in-memory backend and sends nothing real.
