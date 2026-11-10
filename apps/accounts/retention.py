"""The retention rules of the accounts app, account disposal and the account export.

Loaded by AccountsConfig.ready(). `dispose_account` is the only code that disposes of an
account: the never-verified rule and the `dispose_account` command both call it.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q

from apps.accounts.models import (
    Account,
    AccountAuditEvent,
    ExternalIdentity,
    PolicyAcceptance,
    Session,
)
from apps.common import retention
from apps.common.disposal import DISPOSED_DOMAIN, disposed_address, is_disposed_address
from apps.notifications import retention as email_records
from apps.operators import audit
from apps.sites import retention as site_retention

# An account whose email was never verified is disposed of this long after sign-up.
NEVER_VERIFIED_AFTER = timedelta(days=30)
NEVER_VERIFIED, REQUEST = "never_verified", "request"
TARGET = "account"
# The field names a disposal blanks, for its audit entry. Never a value.
BLANKED = {
    "account": ["email", "name"],
    "policy_acceptance": ["ip"],
    "account_audit_event": ["detail of email_changed"],
    **site_retention.BLANKED,
}


class SwitchedOff(Exception):
    """RETENTION_ENABLED is off: nothing may be blanked or deleted."""


class AlreadyDisposed(Exception):
    """The account is disposed of: there is nothing left to export."""


@retention.behind_switch
def expired_subscriber_sessions(now, dry_run):
    """Subscriber sessions, 30 days after they expired or were revoked."""
    cutoff = now - retention.CREDENTIAL_GRACE
    rows = Session.objects.filter(Q(expires_at__lte=cutoff) | Q(revoked_at__lte=cutoff))
    return retention.remove(rows, dry_run)


def is_disposed(account):
    """Closed, with the placeholder address. Both are written together, only by a disposal."""
    return account.closed_at is not None and is_disposed_address(account.email)


def find_account(reference):
    """The account with this public id or this email, or None."""
    value = (reference or "").strip()
    if "@" in value:
        return Account.objects.filter(email=value.lower()).first()
    return Account.objects.filter(public_id=value).first()


def _checked_ref(request_ref):
    """The request reference goes into the audit record, which must hold no personal value."""
    value = (request_ref or "").strip()
    if "@" in value:
        raise ValueError("The request reference must be a reference number, not an address.")
    return value


def dispose_account(account, *, now, rule, actor=None, request_ref="", dry_run=False):
    """Blank the account's personal fields, remove its sign-in identities and sessions, and keep
    the row. Its sites are blanked and closed and its send records deleted with it.

    Returns the count per table, or None when the account is already disposed of. A dry run
    returns the same counts and changes nothing. A real run needs RETENTION_ENABLED and writes
    one `account_disposed` audit entry in the same transaction as the blanking.
    """
    request_ref = _checked_ref(request_ref)
    if not dry_run and not retention.enabled():
        raise SwitchedOff()
    with transaction.atomic():
        rows = Account.objects.all() if dry_run else Account.objects.select_for_update()
        account = rows.get(pk=account.pk)
        if is_disposed(account):  # checked again under the lock
            return None
        address = account.email
        sessions = Session.objects.filter(account=account)
        identities = ExternalIdentity.objects.filter(account=account)
        acceptances = PolicyAcceptance.objects.filter(account=account, ip__isnull=False)
        email_changes = AccountAuditEvent.objects.filter(account=account, event="email_changed")
        counts = {
            "sessions": sessions.count(),
            "sign_in_identities": identities.count(),
            "policy_acceptances": acceptances.count(),
            "account_audit_events": email_changes.count(),
            # Takes the lock every site write of this account takes, then blanks its sites.
            **site_retention.blank_sites_of(account, now, dry_run),
            "email_send_records": email_records.delete_records_for(address, dry_run),
        }
        if dry_run:
            return counts
        sessions.delete()
        identities.delete()
        acceptances.update(ip=None)
        email_changes.update(detail={})
        Account.objects.filter(pk=account.pk).update(
            email=disposed_address(account.pk), name="", closed_at=account.closed_at or now
        )
        audit.record(
            actor,
            "account_disposed",
            target_type=TARGET,
            target_id=account.public_id,
            reason=f"{rule}: {request_ref}" if request_ref else rule,
            detail={"counts": counts, "blanked": BLANKED},
        )
        return counts


@retention.behind_switch
def never_verified_accounts(now, dry_run):
    """Accounts whose email was never verified, 30 days after sign-up: disposed of, not deleted."""
    rows = Account.objects.filter(
        email_verified_at__isnull=True, created_at__lte=now - NEVER_VERIFIED_AFTER
    ).exclude(closed_at__isnull=False, email__endswith=f"@{DISPOSED_DOMAIN}")
    if dry_run:
        return rows.count()
    count = 0
    for account in list(rows):
        if dispose_account(account, now=now, rule=NEVER_VERIFIED) is not None:
            count += 1
    return count


def _iso(moment):
    return moment.isoformat() if moment else None


def export_account_data(account, *, actor, request_ref):
    """Everything kept about one account and its sites, as plain values. Reads only, and writes
    one `account_exported` audit entry. Never a token hash, a provider's user id or a row
    number, and nothing of another account."""
    request_ref = _checked_ref(request_ref)
    if is_disposed(account):
        raise AlreadyDisposed()
    data = {
        "account": {
            "public_id": account.public_id,
            "email": account.email,
            "name": account.name,
            "email_verified_at": _iso(account.email_verified_at),
            "created_at": _iso(account.created_at),
            "closed_at": _iso(account.closed_at),
            "referral_code": account.referral_code,
        },
        "policy_acceptances": [
            {
                "policy_version": row.policy_version,
                "policies": row.policies,
                "ip": row.ip,
                "accepted_at": _iso(row.accepted_at),
            }
            for row in account.policy_acceptances.order_by("pk")
        ],
        "sign_in_providers": [
            {"provider": row.provider, "linked_at": _iso(row.linked_at)}
            for row in account.identities.order_by("pk")
        ],
        "sessions": [
            {
                "device_label": row.device_label,
                "masked_ip": row.masked_ip,
                "created_at": _iso(row.created_at),
                "last_used_at": _iso(row.last_used_at),
                "expires_at": _iso(row.expires_at),
                "revoked_at": _iso(row.revoked_at),
            }
            for row in account.sessions.order_by("pk")
        ],
        "audit_events": [
            {"event": row.event, "detail": row.detail, "created_at": _iso(row.created_at)}
            for row in account.audit_events.order_by("pk")
        ],
        "email_send_records": email_records.export_records_for(account.email),
        **site_retention.export_sites_of(account),
    }
    lists = (
        "policy_acceptances", "sign_in_providers", "sessions", "audit_events",
        "email_send_records", "sites",
    )
    audit.record(
        actor,
        "account_exported",
        target_type=TARGET,
        target_id=account.public_id,
        reason=request_ref,
        detail={"counts": {name: len(data[name]) for name in lists}},
    )
    return data


retention.register("expired_subscriber_sessions", expired_subscriber_sessions)
retention.register("never_verified_accounts", never_verified_accounts)
