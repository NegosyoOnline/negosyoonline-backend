# Incident Runbook

For a suspected leak, a stolen secret, a compromised account or a site that must stop serving. ClickUp z8uq9m7ydg (security hardening). Names variables only: no value is written here, and none is ever pasted into chat, a ticket or a commit.

Every step that changes staging or production needs Coach Ed's approval for that step. This document approves nothing.

Staging is a live site since the Santé ads test (real members and real payments), so an incident on staging is a real incident and is handled the same way as one on production.

## 1. Who is told, in what order

1. **Coach Ed first**, as soon as an incident is suspected, with what was seen and when. Do not wait to be sure.
2. If personal data may have been exposed, the breach steps are HQ's NPC breach procedure. Follow it from there; this document does not repeat it.
3. Then the Supervisor of the affected area, through the manager.

Keep a dated note of what was seen, what was done and by whom. Do not copy personal data or secrets into the note.

## 2. Rotating each secret

Where "set" says Railway, it means the variable of the named service in the Railway dashboard (staging and production are separate). Check afterwards means a check that proves the new value is in use, not only that the service started.

| Variable | Set on | What breaks while it changes | Check afterwards |
|---|---|---|---|
| `DJANGO_SECRET_KEY` | Railway `api`, `worker` (the same value on both) | Pending sign-in codes and signed values made with the old key stop validating, so anyone mid sign-in asks for a new code. The service refuses to start if it is unset while `DJANGO_DEBUG` is false. | `/healthz/` answers 200; sign in with a test account. |
| `ADMIN_SECRETS_KEY` | Railway `api` | Every stored authenticator secret becomes unreadable: each HQ identity must be reset and enrolled again (`docs/runbooks/admin-two-step.md`). Rotate it only if it leaked. | A staff user enrols again and signs in to HQ. |
| `PAYMENT_CREDENTIALS_ENCRYPTION_KEY` | Railway `api`, `worker` | Credentials stored encrypted under it (a Subscriber's payment and Meta credentials) become unreadable until each is entered again. | Open a site with stored credentials; re-enter one and see it save and read back. |
| `STOREFRONT_SERVER_SECRET` | Railway `api`, then at once the Vercel storefronts project, then redeploy it | The surface refuses the storefronts server between the two changes; every storefront page shows its neutral "not found" page for that moment. Full steps: `docs/runbooks/storefront.md`, "Rotating the secret". | A served site opens. |
| `GOOGLE_CLIENT_SECRET` | Railway `api`; new secret made in the Google Cloud console | Google sign-in fails between the two changes. Email-code sign-in keeps working. | Sign in with Google using a test account. |
| `FACEBOOK_APP_SECRET` | Railway `api`; new secret made in the Meta app dashboard | Facebook sign-in fails, and Meta's data-deletion callbacks are refused (`invalid_signature`) until the new value is set. | Sign in with Facebook using a test account. |
| `EMAIL_HOST_PASSWORD` | Railway `api`, `worker`; new key made in the email provider | Sign-in codes and billing emails are not sent between the two changes. | Request a sign-in code and receive it. |
| `PRIVATE_BLOB_READ_WRITE_TOKEN` | Railway `api`; new token made on the private Vercel Blob store | Private files (payment QR codes, proofs) cannot be stored or read until it is set. Nothing is lost. | Open a private file as its owner. |
| `PUBLIC_BLOB_READ_WRITE_TOKEN` | Railway `api`; new token made on the public Vercel Blob store | Product photos cannot be uploaded until it is set. Existing photos still show. | Upload one photo. |
| SMS gateway key (the phone's token, not a variable) | `uv run python manage.py sms_device rotate --name system --actor USERNAME` | The old token fails at once. Texts stop until the new token is entered in the app (`docs/runbooks/sms-gateway.md`, "Rotating a token"). | `sms_device list` shows a fresh `last_contact`. |
| Database password (`DATABASE_URL`) | Railway: rotate the PostgreSQL service's password, then the services that reference it re-read `DATABASE_URL` and restart | Every service that reads the database fails until it has restarted on the new value. Plan it as a short outage. | `/healthz/` answers 200; one signed-in read works. |

Rotate at once any value that was shown in a log, a chat, a screenshot or a commit. A leaked value that is still live is the incident; rotating it comes before finding how it leaked.

## 3. Putting a site on hold

`Site.operator_hold` (`apps/sites/models.py`) is a flag on one site. While it is set, that site's public storefront answers 404 (`apps/sites/api_public.py`), exactly as for an unknown site. The Subscriber's own dashboard is not changed by it.

Today the flag can be set only by a developer with database access, and HQ has no control for it. This is a missing HQ control: report it to the manager so HQ gets a Hold button. Do not use Django admin for it. Until then, a hold is a request to the manager, who has it set with Coach Ed's approval.

## 4. Reading the audit record

| Record | What it holds | Where |
|---|---|---|
| `OperatorAuditEntry` | What HQ staff did: who, which action, which target, when. | `apps/operators/models.py`; read through HQ. |
| `AccountAuditEvent` | What happened to an account: sign-ins, identity and email changes, closures. | `apps/accounts/models.py`. |
| `MemberContactAudit` | Changes to a Santé member's contact details. | `apps/members/models.py`. |
| `security` log lines | One WARNING per refused access. In the Railway logs of `api`, search `denied event=`. |  `apps/common/security_log.py`. |

A `security` line reads `denied event=<event> route=<route> method=<method> reason=<reason> authenticated=<yes|no>`.

- `event`: `site_not_found` (a site the caller cannot reach), `object_not_found` (an order, subscription, group or other object that is not the caller's or does not exist), `code_refused` (a wrong or expired sign-in code), `code_locked` (a code killed by too many wrong guesses).
- `route`: the route pattern, never the URL with ids.
- `reason`: `foreign` (belongs to another account), `deleted`, or `missing` (not found, or not told apart from foreign).
- Never in a line: an email, mobile number, name, code, IP address, account id or object id.

Many `site_not_found` with `reason=foreign` on one route, or many `code_locked`, is someone probing; tell Coach Ed (section 1). Because a line holds no id, it cannot name the person: matching one to an account is done from the audit records above and the time of day, by someone allowed to read them.
