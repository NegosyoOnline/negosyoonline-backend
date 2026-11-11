# Admin Two-Step Sign-In Runbook

hq is the only admin door. Signing in to hq takes two steps: a code emailed to the admin address, then an authenticator code or one recovery code. The admin lists (the Django admin) are served only on the hq host, at `/admin/`, and open only for an hq session that passed both steps. There is no username-and-password sign-in, and no other host serves `/admin/`.

An admin identity holds the second step: an authenticator secret and ten recovery codes. Identities are managed only with `manage.py admin_identity`, from a terminal. No browser page can add, enrol, reset or disable one.

Every step below that touches staging or production needs Coach Ed's approval for that step. Nothing here is approved by this document.

## Variables

| Name | Meaning |
|---|---|
| `ADMIN_SECRETS_KEY` | Fernet key that encrypts authenticator secrets. Empty: nobody can be enrolled and no second step can succeed. Changing it makes every stored secret unreadable, so every identity must be reset and enrolled again. |
| `HQ_HOST` | The one host that serves hq and the admin lists. Empty: hq is off and the lists are served nowhere. |
| `HQ_STAFF_ORIGIN` | The one browser origin that may call the staff routes on the hq host (`/staff/v1/`): the staff app's address, written as `https://` and a host name with nothing after it. Empty: the routes do not exist and hq answers as it did before them. It must be a name directly beside `HQ_HOST` under the same parent, and must not be the dashboard, the public site, an API origin or a preview address. |

`ADMIN_TWO_STEP_REQUIRED` is no longer read by the code. See "Release order" for when it may be deleted.

Create a key on your own computer and put it straight into the variable. Never paste it into chat, a ticket or a commit:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Release order

For a new environment:

1. Deploy and migrate.
2. Set `ADMIN_SECRETS_KEY`. Add and enrol every staff user who needs the admin lists (below). `admin_identity list` names the staff users who are not covered yet.
3. Set `HQ_HOST`.
4. Check: hq signs in with both steps, "Open admin lists" opens the lists, and `/admin/` on the API host answers 404.

For an environment that ran the earlier build, where the Django admin had its own sign-in behind `ADMIN_TWO_STEP_REQUIRED=1`:

1. Before the release, confirm an admin can sign in to hq and has recovery codes at hand. Leave `ADMIN_TWO_STEP_REQUIRED=1` in place.
2. Deploy. One build removes the admin from the API host and opens the lists behind hq. No migration and no variable change is needed.
3. Check as in step 4 above.
4. To go back, redeploy the previous build. Because the variable is still `1`, the old admin returns with its second step, never with the password alone.
5. Only after the release is accepted, delete `ADMIN_TWO_STEP_REQUIRED`. Never unset it while the earlier build could still be redeployed.

## The staff routes

The staff app's pages call `/staff/v1/` on the hq host. Those routes check the same codes with the same limits as the hq pages and set the same cookie. They answer one origin only, `HQ_STAFF_ORIGIN`.

To stop every staff call at once, for example if the staff app's deploy is in doubt: delete `HQ_STAFF_ORIGIN` and redeploy. The hq pages keep working. To also end every live session of a person, run `admin_identity reset` for them.

Setting or deleting `HQ_STAFF_ORIGIN` on staging or production needs Coach Ed's approval each time.

On production it also waits for the NPC wording amendment: from the day it is set, Vercel serves the page where both admin codes are typed, and the NPC drafts describe one Django sign-in surface. Staging is not affected.

The audit record does not say which way a sign-in came in. A sign-in through the staff app's page and one through the hq pages are both written with the surface `hq`.

## Commands

`--actor` is the username of the active staff user running the command. It is written to the audit record. `--user` is the active staff user the identity belongs to. A user has one active identity.

```bash
uv run python manage.py admin_identity add EMAIL --user USERNAME --actor USERNAME
uv run python manage.py admin_identity enrol EMAIL --actor USERNAME
uv run python manage.py admin_identity reset EMAIL --actor USERNAME
uv run python manage.py admin_identity disable EMAIL --actor USERNAME
uv run python manage.py admin_identity list
```

- `enrol` prints the setup key, a setup link and ten recovery codes, once. It refuses to run without an interactive terminal, so run it from an interactive shell on the service, not as a one-off remote command whose output is captured. Before running it: have an authenticator app ready, and decide where the recovery codes will be kept away from the phone.
- In the authenticator app choose "enter a setup key", type the key, and keep the defaults (time-based, 6 digits).
- `reset` removes the secret and the recovery codes and ends that admin's sessions. Run `enrol` again afterwards.
- `disable` ends that admin's sessions and keeps the row. The same address cannot be added again.
- `list` changes nothing and writes no audit record. It names the staff users with no enrolled identity: they cannot open the admin lists.

Every action except `list` writes an `OperatorAuditEntry` and one error-level log line starting `admin-identity-change:`.

### Commands on staging

The interactive staging shell has no `uv` and no `python` on its path. These fail there:

```bash
uv run python manage.py admin_identity list
python manage.py admin_identity list
```

Use the service's own interpreter instead:

```bash
/app/.venv/bin/python manage.py admin_identity list
/app/.venv/bin/python manage.py admin_identity enrol EMAIL --actor USERNAME
```

The same holds for every other `manage.py` command in that shell.

## Lost phone, or hq sign-in does not work

There is no password to fall back on. The ways in are:

- Recovery codes at hand: sign in to hq with one, then run `reset` and `enrol` from the terminal.
- No recovery codes: run `reset` then `enrol` from the terminal.
- The sign-in email does not arrive: check the email service and the `EmailMessage` rows from the terminal. Until it is fixed, data can be read from the Django shell in the terminal.
- Straight after a release from the earlier build: redeploy the previous build (see "Release order").

## What the logs say

- `admin-secrets-key-empty:` the key is empty, invalid, or not the key the secrets were stored with. Logged at most once an hour. No second step can succeed until it is fixed.
- `admin-identity-change:` an identity was added, enrolled, reset or disabled.
- `hq-host-refused:` `HQ_HOST` is malformed or clashes with another host. hq and the admin lists stay off until it is fixed.
- `hq-staff-origin-refused:` `HQ_STAFF_ORIGIN` is malformed, clashes with another address, or is not beside `HQ_HOST`. The staff routes stay off until it is fixed; the hq pages are not affected.

Five wrong codes pause that admin's sign-in for 15 minutes, end their live sessions (the admin lists close with them) and send them an email. The pause ends by itself; `reset` also clears it.

Sign-ins, wrong codes, pauses and sign-outs are written to `OperatorAuditEntry`. "Sign out" on an admin list is the hq sign-out and is recorded the same way.
