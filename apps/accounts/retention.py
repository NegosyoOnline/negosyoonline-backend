"""The retention rules of the accounts app. Loaded by AccountsConfig.ready()."""
from django.db.models import Q

from apps.accounts.models import Session
from apps.common import retention


@retention.behind_switch
def expired_subscriber_sessions(now, dry_run):
    """Subscriber sessions, 30 days after they expired or were revoked."""
    cutoff = now - retention.CREDENTIAL_GRACE
    rows = Session.objects.filter(Q(expires_at__lte=cutoff) | Q(revoked_at__lte=cutoff))
    return retention.remove(rows, dry_run)


retention.register("expired_subscriber_sessions", expired_subscriber_sessions)
