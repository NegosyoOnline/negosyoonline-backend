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
