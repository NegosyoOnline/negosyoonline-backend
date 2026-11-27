# Monitoring: the public monitor list

The external uptime monitors for production and staging. Public URLs and expected results only: no account details, keys, tokens, email addresses or phone numbers belong in this file. Alert recipients are role names. The first monitors were set up on 2026-11-13 (see `docs/runbooks/backups-and-restore.md`, "Uptime checks", and the ops rows in `docs/runbooks/launch-gates.md`).

Every URL below was read with `curl -s --max-time 10 -o /dev/null -w '%{http_code} %{redirect_url}\n'` on 2026-11-28 and answered as stated.

## Production

| Monitor | URL | Method | Expected result | Interval | Alert recipient |
| --- | --- | --- | --- | --- | --- |
| api health | `https://api.negosyoonline.com/healthz/` | GET | 200; JSON `"status": "ok"` (the `commit` field is informational) | 5 min | Manager on duty |
| api readiness | `https://api.negosyoonline.com/readyz/` | GET | 200; JSON `"ok": true` and every check `true` (db, cache, beat, sms_queue, email, files_backup, meta_queue, xendit_queue, db_backup) | 5 min | Manager on duty |
| hq-api sign-in redirect | `https://hq-api.negosyoonline.com/` | GET, do not follow redirects | 303; `Location` path exactly `/sign-in/` (same check as `deploy/bunny/ci/verify-hq.sh`; the HQ host has no `/healthz/`) | 5 min | Manager on duty |
| www frontend | `https://www.negosyoonline.com/` | GET | 200 | 5 min | Owner |
| dash frontend | `https://dash.negosyoonline.com/` | GET | 200 | 5 min | Owner |
| hq frontend | `https://hq.negosyoonline.com/` | GET | 200 | 5 min | Owner |
| Santé store (custom domain) | `https://santebarleyph.com/` | GET | 200 | 5 min | Owner |

Notes:

- `https://www.santebarleyph.com/` answers 308 to the apex; monitor the apex only.
- `https://live4moreonline.store/` answered 404 on 2026-11-28 and is not live in production, so it has no monitor. Add one when the runbook `docs/runbooks/custom-domain-live4more.md` is done in production.
- Host mismatch to settle: `docs/runbooks/bunny-production.md` and `.github/workflows/bunny-production.yml` default the production API to `api.negosyonline.com` and `hq-api.negosyonline.com` (one `o`). Those names did not resolve on 2026-11-28 (curl code 000); the live production hosts use `negosyoonline.com` (two `o`), as in the table. The `www`, `dash` and `hq` production hosts are not named anywhere in the repo; they were taken from the live DNS answers above.

## Staging

| Monitor | URL | Method | Expected result | Interval | Alert recipient |
| --- | --- | --- | --- | --- | --- |
| api health | `https://api-staging.negosyonline.com/healthz/` | GET | 200; JSON `"status": "ok"` (the `commit` field is informational) | 5 min | Manager on duty |
| api readiness | `https://api-staging.negosyonline.com/readyz/` | GET | 200; JSON `"ok": true` and every check `true` | 5 min | Manager on duty |
| hq-api sign-in redirect | `https://hq-api-staging.negosyonline.com/` | GET, do not follow redirects | 303; `Location` path exactly `/sign-in/` (no `/healthz/` on this host) | 5 min | Manager on duty |
| www frontend | `https://www-staging.negosyonline.com/` | GET | 200 | 5 min | Manager on duty |
| dash frontend | `https://dash-staging.negosyonline.com/` | GET | 200 | 5 min | Manager on duty |
| hq frontend | `https://hq-staging.negosyonline.com/` | GET | 200 | 5 min | Manager on duty |

## How to set up

1. The Manager or the Owner adds each monitor by hand in the monitoring tool's dashboard, signed in to the Owner's own account. Nothing here is created by code or a workflow.
2. One monitor per row: name, URL, method and interval as in the tables. Where the tool offers a keyword or JSON check, use the expected result column (`"status":"ok"` for health; `"ok":true` for readiness). The free plan of the tool used so far sends HEAD and cannot pick the method or a keyword; `/readyz/` answers HEAD for that reason. On that plan the sign-in redirect check cannot be made exactly, so run `deploy/bunny/ci/verify-hq.sh <hq-api base URL>` by hand instead.
3. Send each alert to the role named in the row, using the contact held in the tool itself. Do not write contacts, keys or account ids in this repository.
4. Pause the monitors before planned maintenance and resume them after; never delete a monitor to silence it (`docs/runbooks/backups-and-restore.md`).
5. On any red monitor follow `docs/runbooks/incident.md`.
