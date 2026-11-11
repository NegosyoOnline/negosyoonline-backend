"""The Membership Agreement rule and the enrollment authorization at checkout (port of TWCinc's
acceptance.py and consents.py). The browser's tick is never trusted alone: the server compares the
posted version with the active one, stamps its own time on the token, and checks again inside
placement under the locks. The version recorded is the one the buyer's popup carried (the posted
id, which must still be the active one). Accepting the terms is not Santé registration."""
from datetime import datetime

from django.utils import timezone

from apps.common.errors import ApiError
from apps.pack import constants as C
from apps.pack.models import PackAgreementAcceptance, PackEnrollmentAuthorization
from apps.sante import agreement
from apps.sante.models import MembershipAgreement, MembershipAgreementVersion

AGREEMENT_KEY, ENROLLMENT_KEY = "agreement", "enrollment"
ENROLLMENT_FIELD = "enrollment_authorized"


def _unavailable():
    return ApiError("agreement_unavailable", C.MSG_AGREEMENT_UNAVAILABLE, status=409)


def _changed():
    return ApiError("agreement_changed", C.MSG_AGREEMENT_CHANGED, status=409)


def _required():
    return ApiError("agreement_required", C.MSG_AGREEMENT_REQUIRED, status=422)


def held(token):
    return ((token.pending_contact if token else None) or {}).get(AGREEMENT_KEY) or {}


def agreement_from_post(token, *, version_id, accept):
    """The acceptance to keep on the token after a contact call. A token already holding the
    active version keeps it, so changing a typed field does not ask again. A posted id that is
    real but not active means this buyer saw an older popup: the "changed" branch."""
    active = agreement.active_version()
    if active is None:
        raise _unavailable()
    mine = held(token)
    if mine.get("version_id") == active.pk:
        return mine
    posted = str(version_id or "")
    if accept != "1" or not posted:
        raise _changed() if mine else _required()
    if posted != str(active.pk):
        known = MembershipAgreementVersion.objects.filter(pk=posted if posted.isdigit() else 0).exists()
        raise _changed() if known else _required()
    return {"version_id": active.pk, "accepted_at": timezone.now().isoformat()}


def require_current(token, *, lock=False):
    """Called before a proof file is stored and again under the placement locks. Returns
    (version, accepted_at). With lock the one MembershipAgreement row is held until the order
    commits, so the version recorded is the version active at that instant."""
    if lock:
        MembershipAgreement.objects.select_for_update().filter(pk=1).first()
    active = agreement.active_version()
    if active is None:
        raise _unavailable()
    mine = held(token)
    if not mine:
        raise _required()
    if mine.get("version_id") != active.pk:
        raise _changed()
    try:
        return active, datetime.fromisoformat(mine["accepted_at"])
    except (KeyError, TypeError, ValueError):
        raise _required() from None


def write_acceptance(order, version, accepted_at):
    return PackAgreementAcceptance.objects.create(
        site_id=order.site_id, order=order, agreement_id=version.agreement_id, version=version,
        sha256=version.sha256, accepted_at=accepted_at,
    )


def _enrollment_required():
    return ApiError(
        "enrollment_required", C.MSG_ENROLLMENT_REQUIRED, status=422,
        fields={ENROLLMENT_FIELD: [C.MSG_ENROLLMENT_REQUIRED]},
    )


def enrollment_from_post(authorized):
    """Asked on every contact call: the box is the buyer's own tick; the server stamps the time."""
    if authorized != "1":
        raise _enrollment_required()
    return {"text_version": C.ENROLLMENT_TEXT_VERSION, "accepted_at": timezone.now().isoformat()}


def require_enrollment(token):
    mine = ((token.pending_contact if token else None) or {}).get(ENROLLMENT_KEY) or {}
    if mine.get("text_version") != C.ENROLLMENT_TEXT_VERSION:
        raise _enrollment_required()
    try:
        return datetime.fromisoformat(mine["accepted_at"])
    except (KeyError, TypeError, ValueError):
        raise _enrollment_required() from None


def write_enrollment(order, accepted_at):
    return PackEnrollmentAuthorization.objects.create(
        site_id=order.site_id, order=order, text_version=C.ENROLLMENT_TEXT_VERSION,
        text=C.ENROLLMENT_AUTHORIZATION, accepted_at=accepted_at,
    )
