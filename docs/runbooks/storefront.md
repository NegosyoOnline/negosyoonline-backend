# Storefront Surface Runbook

The storefronts server (`apps/storefronts` in the frontend, on Vercel) draws a Subscriber's site on its server. To do that it calls this backend on a surface of its own, `/storefront/v1/`, on the API host. The surface is outside `/v1/`: it is not in the published API, no browser calls it, and storefront origins stay out of `API_ALLOWED_ORIGINS`. The calls are described at the top of `apps/storefront/api.py`, the credential check in `apps/storefront/auth.py`.

Every step below that touches staging needs Coach Ed's approval for that step. Nothing here is approved by this document. Production is not covered.

## Variables

| Where | Name | Meaning |
|---|---|---|
| Railway `api` | `STOREFRONT_ENABLED` | `1` turns the surface on, and only while the secret below is set. Unset: every path under `/storefront/v1/` answers 404. |
| Railway `api` | `STOREFRONT_SERVER_SECRET` | The one credential of the storefronts server. At least 32 characters; a shorter value counts as not set and the surface stays off. Read in `config/settings.py` and used only in `apps/storefront/auth.py`. |
| Vercel project `negosyoonline-storefronts` | `STOREFRONT_SERVER_SECRET` | The same value. Never prefixed `NEXT_PUBLIC_`, so it cannot reach browser code. |

Coach Ed approved the two secret variables for staging on 2026-11-11 (ClickUp z8uq9m92fv, comment 1100430000488789) and sets the values himself. No session reads, prints or sets them.

## How the surface answers

| Case | Answer |
|---|---|
| Switch off, or secret unset or shorter than 32 characters | 404 `not_found`, "No such endpoint.", on every path and method, before any credential is looked at |
| On, credential missing, malformed or wrong | 401 `unauthenticated`, one body for all three |
| On, more than 10 bad credentials from one address in 15 minutes | 429 `rate_limited` for further bad credentials from that address. A good credential is never limited |
| On, good credential, unknown path | 404 `not_found`, "No such endpoint." |
| On, good credential, host whose site is not served right now, or is not a Santé site | 404 `not_found`, "Not found." |
| On, good credential, served Santé site | 200 |

## The contact call

`POST /storefront/v1/contact/` emails a visitor's "Send a Message" form to the store. The body is JSON: `host`, `visitor_ip`, `name`, `email`, `subject`, `message` and `website` (the form's hidden field), all strings. The switch, the credential and the host rule above apply first, with the same answers.

| Case | Answer |
|---|---|
| Sent | 200 `{"status": "sent"}` |
| `website` is filled (the hidden field people never see) | 200 `{"status": "sent"}`, and nothing is sent, counted or recorded |
| More than 10 calls in an hour for one `visitor_ip`, whichever stores they were for | 429 `rate_limited` |
| No name, no message, or an email that is not one valid address | 400 `invalid_request`, with `fields` naming `name`, `email` or `message` |
| The store has no contact email | 409 `no_contact_email` |
| The mail server refused or could not be reached | 503 `email_not_sent` |

The checks run in that order, so a refused form counts toward the 10 and a trapped one does not. A missing or invalid `visitor_ip` is counted under one shared entry. `visitor_ip` is believed only because the call carries the server credential.

The email goes to the contact email the site call shows (`site.contact_email`, else the profile's privacy contact email), never to the owner's account email. Its From is the store's name beside the address of `DEFAULT_FROM_EMAIL`, and its Reply-To is the visitor's email. Name and subject are cut to one line; limits are 120, 254, 200 and 4,000 characters, cut silently.

Nothing of the message is stored or logged. A sent or failed email leaves the usual `EmailMessage` row: the store's contact email, the category `storefront_contact`, the status and the times. The visitor's address is held only as the name of its count in the cache, for one hour. On a service with no `EMAIL_HOST` every email is printed to the service log instead of sent (`config/settings.py`), this one included, so the surface must not be turned on for real visitors on such a service.

Every answer carries `Cache-Control: no-store`.

"Served" is decided by `served(host)` in `apps/sites/api_public.py`, the function `GET /v1/public/sites/by-host/` uses: the host is one label under `SITE_PARENT_HOST`, the name is active and not reserved, the site is not removed and not on operator hold, and its subscription serves the storefront at that moment. So a site shows only while `BILLING_ENABLED` is on.

## Making a secret without showing it

On a Mac, this puts a new 64-character value on the clipboard and prints nothing:

```bash
openssl rand -hex 32 | tr -d '\n' | pbcopy
```

Paste it into the Railway variable and the Vercel variable, then copy something else so it leaves the clipboard. Do not paste it into a chat, a task, a file or a terminal.

## Turning it on (staging)

1. Deploy. The surface is dark while `STOREFRONT_ENABLED` is unset: `curl -s -o /dev/null -w '%{http_code}\n' https://api-staging.negosyonline.com/storefront/v1/site/` prints `404`.
2. Set `STOREFRONT_SERVER_SECRET` on Railway `api` and on the Vercel project, same value. Check presence by name only.
3. Set `STOREFRONT_ENABLED=1` on Railway `api`. The same `curl` now prints `401`. `manage.py check --deploy` reports `storefront.W001` if the switch is on without a long enough secret.
4. The storefronts project then reaches a served site at `https://<address>.storefronts-staging.negosyonline.com/`, which needs `SITE_PARENT_HOST` to be `storefronts-staging.negosyonline.com`.

To go back, unset `STOREFRONT_ENABLED`. There is no table and no migration; nothing is left behind.

## Rotating the secret

There is one secret at a time, so the surface refuses the storefronts server between the two changes and every storefront page answers its neutral "not found" page for that moment.

1. Make a new value as above.
2. Set it on Railway `api`, then at once on the Vercel project, and redeploy the Vercel project so its server reads the new value.
3. Check a served site opens again.

Rotate at once if the value was ever shown in a log, a chat, a screenshot or a commit.
