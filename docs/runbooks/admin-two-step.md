# Admin Two-Step Sign-In Runbook

Two-step sign-in protects hq and the Django admin. An admin identity holds the second step: an authenticator secret and ten recovery codes. Identities are managed only with `manage.py admin_identity`, from a terminal. No browser page can add, enrol, reset or disable one.

Every step below that touches staging or production needs Coach Ed's approval for that step. Nothing here is approved by this document.

## Variables

| Name | Meaning |
|---|---|
| `ADMIN_SECRETS_KEY` | Fernet key that encrypts authenticator secrets. Empty: nobody can be enrolled and no second step can succeed. Changing it makes every stored secret unreadable, so every identity must be reset and enrolled again. |
| `ADMIN_TWO_STEP_REQUIRED` | `1` makes the Django admin ask for the second step. Anything else is off. |
| `HQ_HOST` | The one host that serves hq. Empty: hq is off. |

Create a key on your own computer and put it straight into the variable. Never paste it into chat, a ticket or a commit:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Release order

1. Deploy and migrate with `ADMIN_TWO_STEP_REQUIRED` unset. Nothing changes for anyone.
2. Set `ADMIN_SECRETS_KEY`. Add and enrol every staff user who must keep using the Django admin (below). `admin_identity list` names the staff users who are not covered yet.
3. Set `ADMIN_TWO_STEP_REQUIRED=1`.
4. Check: the Django admin asks for the code after the password, and hq signs in.

To go back, unset `ADMIN_TWO_STEP_REQUIRED`. The Django admin stops asking at once and the identities stay. `admin_identity list` then warns that an identity is enrolled while the switch is off; that warning is expected.

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
- `list` changes nothing and writes no audit record.

Every action except `list` writes an `OperatorAuditEntry` and one error-level log line starting `admin-identity-change:`.

## Lost phone

- Recovery codes at hand: sign in with one, then run `reset` and `enrol` from the terminal.
- No recovery codes: run `reset` then `enrol` from the terminal.
- Nobody can reach a terminal with a working second step and the Django admin is needed now: unset `ADMIN_TWO_STEP_REQUIRED`, then reset and enrol.

## What the logs say

- `admin-secrets-key-empty:` the key is empty, invalid, or not the key the secrets were stored with. Logged at most once an hour. No second step can succeed until it is fixed.
- `admin-identity-change:` an identity was added, enrolled, reset or disabled.

Five wrong codes pause that admin's sign-in on hq and the Django admin for 15 minutes, end their live sessions and send them an email. The pause ends by itself; `reset` also clears it.
