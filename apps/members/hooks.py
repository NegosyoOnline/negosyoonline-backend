"""Extension points that later slices fill. Nothing here knows about them.

- contact_release: a later slice says when a member no longer holds their contacts.
- session_issued: runs after a member session is created outside sign-in (the order slice).
- sign_in_sms_first: a template says its members are texted first when they sign in by mobile.
- the SMS channel: the SMS slice registers how a code reaches a handset. Until then no member
  code goes by SMS and a sign-in by mobile answers sms_unavailable.
"""
import logging

logger = logging.getLogger(__name__)

_contact_releases = []
_session_issued = []
_sign_in_sms_first = []
_sms_transport = {}


def register_contact_release(fn):
    """fn(member) returns True for a member who no longer holds their contacts."""
    if fn not in _contact_releases:
        _contact_releases.append(fn)


def contact_released(member):
    return any(fn(member) for fn in list(_contact_releases))


def register_session_issued_hook(fn):
    """fn(site=, member=) runs after issue_session. A failing hook never blocks the session."""
    if fn not in _session_issued:
        _session_issued.append(fn)


def run_session_issued_hooks(site, member):
    for fn in list(_session_issued):
        try:
            fn(site=site, member=member)
        except Exception:  # noqa: BLE001 - a later slice's hook never blocks a session issue
            logger.exception("member session-issued hook failed")


def register_sign_in_sms_first(fn):
    """fn(site) returns True for a site whose members are texted first."""
    if fn not in _sign_in_sms_first:
        _sign_in_sms_first.append(fn)


def sign_in_sms_first(site):
    for fn in list(_sign_in_sms_first):
        try:
            if fn(site):
                return True
        except Exception:  # noqa: BLE001 - a template never breaks sign-in
            logger.exception("member sign-in channel hook failed")
    return False


def register_sms_transport(*, available, deliver_code, send_notice=None, precheck=None):
    """The SMS slice (PK2b) calls this once.

    available() -> bool: can a text go out at all.
    deliver_code(challenge, code, sms_category) -> outcome string: "queued", "offer_email",
        "paused" or "refused_not_test_number". The slice supersedes a held challenge with
        otp.mark_held and records sms_message_id on the row.
    send_notice(site, mobile, body, idempotency_key) -> None: a security text.
    precheck(site, mobile) -> None: raises ApiError before any member lookup (a number that may not
        be texted, a daily ceiling), the same for every input.
    """
    _sms_transport.update(
        available=available, deliver_code=deliver_code, send_notice=send_notice, precheck=precheck
    )


def sms_transport():
    return _sms_transport or None
