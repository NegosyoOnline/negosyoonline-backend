"""Member sessions. The token is random and shown once; only its SHA-256 is stored, bound to
the site and the member. Every call re-reads the rows: nothing is cached between requests, and
use never extends the fixed 30 days."""
import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

from apps.members.models import CONTACT_CHANGE, SIGN_OUT, MemberSession

SESSION_LIFETIME = timedelta(days=30)


def token_hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def create_session(site, member, *, ip="", now=None):
    """(session, raw token). Call it inside the transaction that holds the member row lock."""
    now = now or timezone.now()
    raw = secrets.token_urlsafe(32)
    session = MemberSession.objects.create(
        site=site, member=member, token_hash=token_hash(raw), created_at=now,
        expires_at=now + SESSION_LIFETIME, ip_advisory=(ip or "")[:45],
    )
    return session, raw


def issue_session(site, member, *, ip="", now=None):
    """For the order slice: sign a new member in after checkout. (session, raw token); runs the
    session_issued hooks. The caller hands the raw token to the storefronts server."""
    from apps.members import hooks
    session, raw = create_session(site, member, ip=ip, now=now)
    hooks.run_session_issued_hooks(site, member)
    return session, raw


def recheck(session_pk, member_pk, *, now=None):
    """Inside the caller's transaction, after the member row lock: is this session still live."""
    now = now or timezone.now()
    return (
        MemberSession.objects.select_for_update()
        .filter(pk=session_pk, member_id=member_pk, revoked_at__isnull=True, expires_at__gt=now)
        .first()
    ) is not None


def find_session(site, raw):
    """The live session of this site for this token, or None. The member row is read with it."""
    if not raw or len(raw) > 200:
        return None
    return (
        MemberSession.objects.select_related("member")
        .filter(
            token_hash=token_hash(raw), site_id=site.pk, member__site_id=site.pk,
            revoked_at__isnull=True, expires_at__gt=timezone.now(),
        )
        .first()
    )


def revoke_session(session, reason=SIGN_OUT, *, now=None):
    MemberSession.objects.filter(pk=session.pk, revoked_at__isnull=True).update(
        revoked_at=now or timezone.now(), revoke_reason=reason
    )


def revoke_sessions(member, reason=CONTACT_CHANGE, *, now=None):
    """Revoke every live session of the member. Returns how many."""
    return MemberSession.objects.filter(member=member, revoked_at__isnull=True).update(
        revoked_at=now or timezone.now(), revoke_reason=reason
    )
