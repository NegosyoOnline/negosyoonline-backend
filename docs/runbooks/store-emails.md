# Store emails (members and buyers)

What a store's own members and buyers are emailed: member sign-in and verification codes, contact-change notices, and the Membership Pack buyer emails (order received, payment rejected, payment verified, secure link). Everything to a Subscriber, an operator or an account holder stays on NegosyoOnline and is not covered here.

## Switches (Railway variables on `api`, both default off)

- `STORE_EMAILS_BRANDED` : `1` turns on branded HTML plus a plain-text part. Off: the plain text every email always was, byte for byte, from `DEFAULT_FROM_EMAIL` under the store's name. Turn on in staging only after the preview is approved and the sender domain shows Verified in Resend.
- `STORE_SENDERS` : comma-separated `key=address`, where `key` is the store's web-address name (for example `live4more`) or its site id, e.g. `live4more=orders@liveformore.com`. Set by an operator only; a store owner cannot change it. A store with no pair sends from `DEFAULT_FROM_EMAIL` with the store name as display name. A malformed pair is ignored and `manage.py check` warns (`notifications.W001`).
- Set `STORE_SENDERS` before `STORE_EMAILS_BRANDED`. The same Resend SMTP credentials send for every domain; the sender domain must be added and verified in Resend first (SPF, DKIM, return-path in that domain's DNS). If Resend needs a separate key per domain, stop and ask.

## What a store email looks like

From `"Store name" <store sender or NegosyoOnline address>`; Reply-To the store owner's verified account email (none while unverified). Header is the store name as text, accent colour only if `#RRGGBB`. Footer: store name, store contact email, contact mobile and business address, each only when present, and "You are receiving this because of your order or account with <store>." No NegosyoOnline or Santé line. A store name that reads as NegosyoOnline, a bank or an e-wallet is replaced by "Your store" in the From name only (list in `apps/notifications/store_email.py`).

## Preview (sends nothing)

`uv run python manage.py preview_store_email all --out-dir DIR` writes every kind for a full sample store and a bare one (`*-minimal`). `preview_store_email pack_verified --site <site id> --out FILE` renders one with a real store's brand (read only). Open the HTML in Chrome at desktop width and at 360px.

## Not built at launch

Branding settings page, logo, "send me a test email", per-store email log, automatic resend of a failed email (a failure is logged and the `EmailMessage` row shows `failed`), bounce webhook, sending limits. Monthly: look at the Resend usage page against the plan's included volume.
