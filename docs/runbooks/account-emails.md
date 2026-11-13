# Account Emails Runbook

NegosyoOnline's own emails to Subscribers (sign-in code, Facebook email confirmation, email change code, and the security notices for an email change, a connected sign-in method and a mobile number change) are sent from `NegosyoOnline <no-reply@negosyoonline.com>` with Reply-To `support@negosyoonline.com`. Emails a store sends to its own members are a separate matter and are not covered here.

## Switch

`ACCOUNT_EMAILS_BRANDED` (Railway variable, default off). Off: plain text exactly as before. On (`1`): branded HTML plus a plain-text part. Turn it on in staging only after the preview has been approved and Resend shows the domain verified. Production is not changed by this task.

## Resend setup (actions for the owner, not code)

1. In Resend, add and verify the domain `negosyoonline.com` (SPF, DKIM and return-path DNS records).
2. Set on the `api` service: `EMAIL_HOST=smtp.resend.com`, `EMAIL_PORT=587`, `EMAIL_HOST_USER=resend`, `EMAIL_HOST_PASSWORD=<NegosyoOnline's own Resend API key>`, `DEFAULT_FROM_EMAIL=NegosyoOnline <no-reply@negosyoonline.com>`. Never paste the key anywhere else.
3. Make sure `support@negosyoonline.com` is a monitored mailbox.

With no `EMAIL_HOST` every email is printed to the service log instead of sent (local development).

## Preview without sending

`uv run python manage.py preview_account_email all --out-dir <folder>` writes each email as `.html` and `.txt` with a fixed sample code. Open the HTML in a browser at desktop and phone width. Nothing is sent.

## If Resend is down

The person sees "We could not send the email" (503) and can ask again at once: a failed send gives back the 60 second wait and the hourly count.

## Email and mobile change hardening

`ACCOUNT_EMAIL_CHANGE_HARDENED` (Railway variable, default off, `1` turns it on) governs the whole first gate. Off: nothing below exists and behaviour is as before.

On, an email change, a mobile change and the first mobile add each need a 6-digit code first, from either of the account's verified channels (Coach Ed, 2026-11-13: if the mailbox is no longer reachable, the verified mobile still works, and the other way round).

- `POST /v1/account/step-up/` with `{channel: "email"|"mobile", purpose: "email_change"|"mobile_change"}` sends the code: to the account's email, or by text to its verified mobile. The mobile channel exists only while texts are on (`sms_on()`) and the account holds a verified mobile; otherwise it answers 400 `step_up_channel_unavailable`. A first mobile add can only use the email channel. `POST /v1/account/email/step-up/` stays as an alias of `email` + `email_change`.
- A code is bound to its account, purpose and channel, lasts 10 minutes, and is refused for any other purpose or channel. Five wrong guesses kill it. It is cleared when the change completes.
- `POST /v1/account/email/start/` takes it as `step_up_code` (purpose `email_change`); `POST /v1/account/mobile/start/` takes it as `step_up_code` (purpose `mobile_change`). The new address or number is still verified by its own code. An address that already has an account is answered like a free one (nothing sent).
- One limit for both channels: 8 mails or texts an hour per account (plus the 60 second wait per purpose and channel).
- `GET /v1/account/` returns `email_change_step_up` and `step_up_channels` (for example `["email","mobile"]`; empty when off) so the dashboard knows what to offer.
- Audit events (`email_change_step_up`, `mobile_change_step_up`) record the channel and purpose, never an address, number or code.
- The text reads "Your NegosyoOnline code is NNNNNN. It expires in 10 minutes. Do not share it." and uses the existing `mobile_verify` text purpose. The email uses the `email_change_current` template, worded for an email or mobile change.

Turn it on in staging only after the dashboard step is deployed. The per-account limit is always on for email changes.
