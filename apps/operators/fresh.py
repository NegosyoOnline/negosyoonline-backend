"""The fresh authenticator check HQ's sensitive staff writes share (SMS Gateway, SMS line,
Suspend / Resume): one check, one message, one audit detail. The caller records its own audit
action (a literal, as apps.operators.audit requires) and raises `refusal()`."""
from apps.common.errors import ApiError
from apps.operators import two_step

REAUTH_MESSAGE = "That code did not work. Enter the current code from your authenticator app."


def failure(body, operator):
    """None when the body holds a fresh 6-digit authenticator code. Otherwise the audit detail
    of the refusal (counts only, never a code): the code was wrong, stale, reused or locked, or
    it was a recovery code."""
    identity = operator.admin_identities.filter(is_active=True).first()
    result = (
        two_step.verify_fresh_totp(identity.pk, body.get("code"), surface=two_step.SURFACE_HQ)
        if identity is not None
        else two_step.Result(ok=False)
    )
    if result.ok:
        return None
    identity = operator.admin_identities.filter(is_active=True).first()
    return {
        "failed_attempts": identity.failed_attempts if identity else 0,
        "locked": bool(result.locked),
    }


def refusal():
    return ApiError("reauth_failed", REAUTH_MESSAGE, status=403)
