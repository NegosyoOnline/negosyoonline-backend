# Billing Build B1b: `/healthz/` is exempt from the HTTPS redirect so Railway can health-check `api` (plan)

Date: 2026-11-11. ClickUp task: z8uq9me2g0 (https://app.clickup.com/t/z8uq9me2g0), parent z8uq9m8h46. Follows Build B1a (z8uq9me1un).
Branch: `claude/z8uq9me2g0-healthz-https-exempt-c81055`, cut from `origin/main` at `3842e9c`.
Status: approved by Coach Ed, 2026-11-11, in the coordinator chat ("Approve plan", plan at `c246285`). The approval covers sections 3, 5 and 7; the Railway step in section 6 is still his separate approval. The sections below are kept as written.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9me2g0, description. It has no comments. |
| B1a record | ClickUp z8uq9me1un, coordinator comment "api health check removed, PR 74 merged, staging verified", 2026-11-11 |
| Health checks | https://docs.railway.com/reference/healthchecks |
| Edge | https://docs.railway.com/networking/public-networking/specs-and-limits |
| Django | `django/middleware/security.py` and `django/middleware/common.py` in the installed Django 5.2.19 |
| Code | `config/settings.py`, `config/urls.py`, `apps/health/tests.py`, `apps/operators/middleware.py`, `scripts/verify_deploy.sh`, `.github/workflows/verify-staging.yml`, `.github/workflows/ci.yml`, `docs/runbooks/billing.md`, `README.md`, at `3842e9c` |
| Staging, read-only | Three `curl` header requests to `api-staging.negosyonline.com` on 2026-11-11, section 4 |

The Railway pages were read through a summarising fetch tool, not as raw text. The Railway dashboard and its variables were not opened. The parent task was searched for health-check and Railway rulings, not read in full; it has none on this subject.

## 2. Items for Coach Ed

1. **Approve this plan** (the one-line setting in section 3). Nothing is written before it.
2. **Later, after the merge and a verified staging deploy: approve the Railway step** in section 6, with its exact values. Asked at that time, not now.
3. **One thing to confirm, by name only, before item 2:** that the staging `api` variable `DJANGO_ALLOWED_HOSTS` still lists `healthcheck.railway.app`. The evidence in section 4.2 says it did this morning. If it is missing, adding it is a Railway variable change and his decision.

## 3. The change

One new line in `config/settings.py`, beside `SECURE_PROXY_SSL_HEADER`:

```python
# Railway's health check calls /healthz/ over plain HTTP. Only this exact path skips the redirect.
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
```

### What is exempt

Only requests whose path is exactly `/healthz/`.

Django's `SecurityMiddleware` strips the leading slash from the request path and then runs `re.search` with each pattern. So the pattern is tested against `healthz/`:

- `^` ties it to the start and `$` to the end, with only the literal `healthz/` between them. No wildcard, no optional part.
- `/healthz` (no final slash), `/healthz/x`, `/v1/healthz/`, `/xhealthz/` and `/HEALTHZ/` do not match and still redirect.
- The query string is not part of the path. `/healthz/?x=1` is the same view and is exempt; the view ignores the query.
- `$` in Python also matches before one final newline. A path cannot carry a raw newline, and an encoded one (`%0A`) would make the path `healthz/\n`, which no URL pattern serves: the answer is a 404 with no data.
- `//healthz/` loses both slashes in the middleware and so is not redirected, but the URL resolver does not serve it: 404 with no data.

### What is not changed

- `SECURE_SSL_REDIRECT` stays `True`: every other path still answers plain HTTP with a 301 to HTTPS.
- `SECURE_HSTS_SECONDS` stays `31_536_000`. Django sends the HSTS header only on HTTPS responses; that is how it works today and it stays so. `includeSubDomains` and preload stay deferred.
- `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` stay `True`. `/healthz/` sets no cookie.
- `SECURE_PROXY_SSL_HEADER` stays as it is.
- `ALLOWED_HOSTS` is not changed. A request with a host that is not allowed still gets 400, on `/healthz/` too (`CommonMiddleware` checks the host on every request).
- The `/healthz/` view, its URL and its answer (`{"status": "ok", "commit": "<12 characters>"}`) are not touched. It stays unauthenticated and does no database or cache call.
- The hq host still serves no `/healthz/` (404), as today.

### Where the line goes, and why

Outside the `if not DEBUG:` block. The setting does nothing while `SECURE_SSL_REDIRECT` is off, and one unconditional line lets the tests read the real pattern in both CI jobs (the SQLite job runs with `DJANGO_DEBUG=true`, the PostgreSQL job with `false`). Routine choice.

## 4. The two points the task asks to check

### 4.1 Does Railway's public edge forward plain HTTP to the container?

**No. The edge redirects it first, so nobody outside can reach `/healthz/` over plain HTTP, before or after this change.**

- Railway, "Edge": "Plain HTTP GET requests will be redirected to HTTPS with a `301` response", and "`X-Forwarded-Proto` always indicates `https`".
- Staging, 2026-11-11: `curl -sI http://api-staging.negosyonline.com/healthz/` and the same for `/v1/account` both answer `301` to the `https://` address with `content-length: 0` and none of Django's headers (no `x-content-type-options`, `x-frame-options`, `referrer-policy`). The HTTPS answer for `/healthz/` carries all of them, plus `server: railway-hikari`. So the 301 comes from the edge, not from Django.

What this means:

- From outside, `/healthz/` over plain HTTP shows exactly what it shows today: the edge's 301. Nothing more is exposed.
- The exemption is reachable only where plain HTTP reaches the container directly: Railway's own health check, and other services of the same Railway project over the private network. What they get is the same two fields that are already public over HTTPS.
- Django's own redirect stays as a second line of defence for every other path.

### 4.2 Which host name does the health check send, and is it allowed?

**It sends `healthcheck.railway.app`, and the evidence says staging accepts it. I could not read the variable to confirm.**

- Railway, "Health checks": "Railway uses the hostname `healthcheck.railway.app` when performing healthchecks on your service", and it says to add that name to the allowed hosts.
- `README.md` line 31 already instructs: `DJANGO_ALLOWED_HOSTS`: the generated Railway domain plus `healthcheck.railway.app`.
- The failed deployment `33d647de` got **301**, not 400 (B1a record). Django builds that redirect with `request.get_host()`, which answers 400 when the host is not allowed and `DEBUG` is false. A 301 therefore means the host was accepted at that time.
- After this change the host still has to be allowed: `CommonMiddleware` calls `request.get_host()` on every request.
- `healthcheck.railway.app` is not the hq host, so the request reaches the normal `/healthz/` view.

Not verified: the variable's present value. Reading Railway is outside the builder's part. If the name were missing, the smallest fix is to add `healthcheck.railway.app` to the staging `api` variable `DJANGO_ALLOWED_HOSTS`. **That is a Railway variable change and Coach Ed's decision**, not the builder's. No code change to `ALLOWED_HOSTS` is proposed.

Also from Railway's page: the check runs only at the start of a deployment, against the `PORT` the app listens on, until it gets a 2xx. It is not continuous monitoring. The page does not say whether the call is HTTP or HTTPS; the 301 of `33d647de` shows it is plain HTTP.

## 5. Tests

Written first, seen failing, then the line is added. In `apps/health/tests.py`, a new test class that turns the redirect on with `override_settings(SECURE_SSL_REDIRECT=True)` and leaves `SECURE_REDIRECT_EXEMPT` as the settings file has it. The test runner already runs with `DEBUG` false.

1. `/healthz/` over plain HTTP answers 200 with the same JSON.
2. `/healthz/` over HTTPS answers 200.
3. Other paths over plain HTTP still answer 301 to `https://`: `/healthz` (no final slash), `/healthz/x`, `/v1/healthz/`, `/v1/account`.
4. `/healthz/` over plain HTTP with the host `healthcheck.railway.app` answers 200 when that host is allowed, and 400 when it is not.
5. `settings.SECURE_REDIRECT_EXEMPT` equals `[r"^healthz/$"]` exactly, so a wider pattern cannot slip in unnoticed.

Checks, with their output in the report:

- `uv run python manage.py test apps.health`, then the whole suite.
- `DJANGO_DEBUG=false uv run python manage.py check --deploy`, run on `origin/main` and on the branch, and the two outputs compared: no new warning.
- `uv run python manage.py makemigrations --check --dry-run`: no migration. None is expected; the change has no model.
- CI on SQLite and PostgreSQL.

## 6. The Railway step, after the merge (Coach Ed's approval at that time)

Order:

1. PR merged by the coordinator; staging `api` deploys the merge commit; `scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <commit>` passes. The health path is still empty at this point, so this deploy cannot fail on it.
2. Coach Ed confirms section 2 item 3 (the host name is in `DJANGO_ALLOWED_HOSTS`, by name only).
3. With his approval, in the Railway dashboard, project `negosyoonline-backend`, **staging** environment, service **`api`** only, Settings:

   | Field | Value |
   |---|---|
   | Healthcheck Path | `/healthz/` |
   | Healthcheck Timeout | `120` |

   Then deploy the staged change.
4. Confirm: the new deployment shows the health check passing and ends SUCCESS; `/healthz/` over HTTPS reports the commit; one other path over plain HTTP still answers 301.
5. If the deployment fails on the health check: Railway keeps the previous deployment serving. Clear the Healthcheck Path again (the state of today) and report the status code the check got.

Not touched: `worker`, `beat`, any variable, any domain, production. The builder makes no Railway write.

## 7. Documents

In the same pull request as the code, `README.md` line 42 and the paragraph at `docs/runbooks/billing.md` line 119 are rewritten to say: `/healthz/` alone is exempt from the HTTPS redirect, why, and that the edge redirects plain HTTP from outside before it reaches the app.

The table row "Health path" for `api` and the sentence "`api` has no health path on purpose" describe Railway's Settings. They are changed only after step 6.3 is done, in a second small docs pull request, so the runbook never states a setting that is not there. The task allows "the same pull request or the next one". Routine choice.

`CLAUDE.md` is not changed.

## 8. Not in this task

Any other security setting; HSTS `includeSubDomains` or preload; health checks for `worker` or `beat`; a change to the `/healthz/` view; `ALLOWED_HOSTS` in code; any Railway variable; production.

## 9. Not known

- The present value of `DJANGO_ALLOWED_HOSTS` on staging `api` (section 4.2).
- Whether Railway's health check sends an `X-Forwarded-Proto` header. The 301 of `33d647de` says it does not send `https`. The exemption works either way.
