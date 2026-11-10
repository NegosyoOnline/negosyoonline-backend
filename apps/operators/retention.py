"""The retention rules of the operators app. Loaded by OperatorsConfig.ready()."""
from django.contrib.sessions.models import Session as DjangoSession
from django.db.models import Q

from apps.common import retention
from apps.operators.models import AdminSession, AdminSignInChallenge


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


retention.register("expired_admin_sign_in_codes", expired_admin_sign_in_codes)
retention.register("expired_admin_sessions", expired_admin_sessions)
retention.register("expired_django_sessions", expired_django_sessions)
