"""The retention rules of the operators app. Loaded by OperatorsConfig.ready()."""
from datetime import timedelta

from django.contrib.sessions.models import Session as DjangoSession
from django.db.models import Q

from apps.common import retention
from apps.operators import audit
from apps.operators.models import AdminSession, AdminSignInChallenge, OperatorAuditEntry

# The personal values of an admin audit entry are blanked this long after it was written (D4).
AUDIT_PERSONAL_KEPT = timedelta(days=365)


@retention.behind_switch
def expired_admin_sign_in_codes(now, dry_run):
    """Emailed hq sign-in codes, 30 days after they expired."""
    rows = AdminSignInChallenge.objects.filter(expires_at__lte=now - retention.CREDENTIAL_GRACE)
    return retention.remove(rows, dry_run)


@retention.behind_switch
def expired_admin_sessions(now, dry_run):
    """hq sessions, 30 days after they expired or were revoked."""
    cutoff = now - retention.CREDENTIAL_GRACE
    rows = AdminSession.objects.filter(Q(expires_at__lte=cutoff) | Q(revoked_at__lte=cutoff))
    return retention.remove(rows, dry_run)


@retention.behind_switch
def expired_django_sessions(now, dry_run):
    """Django's own session rows, 30 days after they expired. They carry the notices shown on
    the admin lists, never a sign-in."""
    rows = DjangoSession.objects.filter(expire_date__lte=now - retention.CREDENTIAL_GRACE)
    return retention.remove(rows, dry_run)


@retention.behind_switch
def admin_audit_personal_values(now, dry_run):
    """Admin audit entries declared PERSONAL, 1 year after they were written: the personal
    values are blanked and the row is stamped. No row is deleted and nothing else on it changes.
    The run log carries the count; the stamp on each row says when it was blanked."""
    personal_actions = [name for name, kind in audit.ACTIONS.items() if kind == audit.PERSONAL]
    rows = OperatorAuditEntry.objects.filter(
        action__in=personal_actions,
        at__lte=now - AUDIT_PERSONAL_KEPT,
        personal_blanked_at__isnull=True,
    )
    if dry_run:
        return rows.count()
    return rows.update(personal={}, personal_blanked_at=now)


retention.register("expired_admin_sign_in_codes", expired_admin_sign_in_codes)
retention.register("expired_admin_sessions", expired_admin_sessions)
retention.register("expired_django_sessions", expired_django_sessions)
retention.register("admin_audit_personal_values", admin_audit_personal_values)
