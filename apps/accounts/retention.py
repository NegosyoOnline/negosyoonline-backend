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
    AccountMobile,
    ExternalIdentity,
    MobileOtpChallenge,
    PolicyAcceptance,
    Session,
)
from apps.billing import services as billing
from apps.common import retention
from apps.common.disposal import DISPOSED_DOMAIN, disposed_address, is_disposed_address
from apps.members import retention as member_retention
from apps.inquiries import retention as inquiries_retention
from apps.meta_ads import retention as meta_retention
from apps.pack import retention as pack_retention
from apps.notifications import retention as email_records
from apps.operators import audit
from apps.payments import retention as payment_retention
from apps.sante import retention as sante_retention
from apps.shop import retention as shop_retention
from apps.sites import retention as site_retention

# An account whose email was never verified is disposed of this long after sign-up.
NEVER_VERIFIED_AFTER = timedelta(days=30)
# Security records: an account's audit events are deleted this long after they were written.
EVENTS_KEPT = timedelta(days=365)
# The exception: an email change names the old address, and a disposal needs it for as long as
# a send record of that address can exist.
EMAIL_CHANGE_KEPT = email_records.EMAIL_RECORD_LIFETIME
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


class NotDisposable(Exception):
    """The account cannot be disposed of yet. Its subscriptions were cancelled; nothing was
    blanked. `reasons` says why and until when, and `cancelled` which sites were cancelled.
    An account that owns the Santé catalog owner site is refused before anything is cancelled."""

    def __init__(self, step):
        super().__init__("The account cannot be disposed of yet.")
        self.reasons = step.reasons
        self.cancelled = step.cancelled


@retention.behind_switch
def expired_subscriber_sessions(now, dry_run):
    """Subscriber sessions, 30 days after they expired or were revoked."""
    cutoff = now - retention.CREDENTIAL_GRACE
    rows = Session.objects.filter(Q(expires_at__lte=cutoff) | Q(revoked_at__lte=cutoff))
    return retention.remove(rows, dry_run)


@retention.behind_switch
def account_audit_events(now, dry_run):
    """Account audit events, 1 year after they were written; email changes, 2 years."""
    is_email_change = Q(event="email_changed")
    rows = AccountAuditEvent.objects.filter(
        (~is_email_change & Q(created_at__lte=now - EVENTS_KEPT))
        | (is_email_change & Q(created_at__lte=now - EMAIL_CHANGE_KEPT))
    )
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
    the row. Its mobile number and verification challenges are deleted, its sites are blanked
    and closed, and its email send records deleted with it, those of the addresses it used
    before an email change included.

    Returns the count per table, or None when the account is already disposed of. A dry run
    returns the same counts and changes nothing. A real run needs RETENTION_ENABLED and writes
    one `account_disposed` audit entry in the same transaction as the blanking.

    The account's subscriptions are cancelled first. While a paid term is still running a real
    run keeps that cancellation, blanks nothing and raises NotDisposable; a dry run adds
    `refused_for_now` to the counts instead.
    """
    result = _dispose(account, now, rule, actor, request_ref, dry_run)
    if isinstance(result, NotDisposable):
        raise result  # raised outside the transaction, so the cancellation is kept
    return result


def _dispose(account, now, rule, actor, request_ref, dry_run):
    request_ref = _checked_ref(request_ref)
    if not dry_run and not retention.enabled():
        raise SwitchedOff()
    with transaction.atomic():
        rows = Account.objects.all() if dry_run else Account.objects.select_for_update()
        account = rows.get(pk=account.pk)
        if is_disposed(account):  # checked again under the lock
            return None
        # Before the subscriptions: a disposal refused for this cancels nothing.
        kept_sites = sante_retention.disposal_refusals(account, dry_run)
        if kept_sites and not dry_run:
            return NotDisposable(billing.DisposalStep(reasons=tuple(kept_sites)))
        step = billing.prepare_disposal(account, now, dry_run=dry_run)
        if step.reasons and not dry_run:
            return NotDisposable(step)
        address = account.email
        sessions = Session.objects.filter(account=account)
        identities = ExternalIdentity.objects.filter(account=account)
        acceptances = PolicyAcceptance.objects.filter(account=account, ip__isnull=False)
        email_changes = AccountAuditEvent.objects.filter(account=account, event="email_changed")
        # Read before the details are blanked: the addresses the account used before.
        earlier = [
            (detail["old"], changed_at)
            for detail, changed_at in email_changes.values_list("detail", "created_at")
            if detail.get("old") and detail["old"].lower() != address.lower()
        ]
        # Read directly, never behind sms_on(): a skipped table would leave the number behind.
        mobiles = AccountMobile.objects.filter(account=account)
        mobile_challenges = MobileOtpChallenge.objects.filter(account=account)
        counts = {
            "sessions": sessions.count(),
            "sign_in_identities": identities.count(),
            "policy_acceptances": acceptances.count(),
            "account_audit_events": email_changes.count(),
            "mobile_numbers": mobiles.count(),
            "mobile_challenges": mobile_challenges.count(),
            # Takes the lock every site write of this account takes, then blanks its sites.
            **site_retention.blank_sites_of(account, now, dry_run),
            **sante_retention.blank_settings_of(account, dry_run),
            **payment_retention.blank_payments_of(account, now, dry_run),
            **member_retention.blank_members_of(account, dry_run),
            **pack_retention.blank_packs_of(account, dry_run),
            **shop_retention.blank_shop_of(account, dry_run),
            **meta_retention.blank_meta_of(account, dry_run),
            **inquiries_retention.blank_inquiries_of(account, dry_run),
            "email_send_records": email_records.delete_records_for(address, dry_run),
            "earlier_address_send_records": email_records.delete_earlier_records_for(
                earlier, dry_run
            ),
        }
        if step.cancelled:
            counts["subscriptions_cancelled"] = len(step.cancelled)
        if dry_run:
            if kept_sites or step.reasons:
                counts["refused_for_now"] = [*kept_sites, *step.reasons]
            return counts
        sessions.delete()
        identities.delete()
        mobiles.delete()
        mobile_challenges.delete()
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
            detail={
                "counts": counts,
                "blanked": {
                    **BLANKED, **sante_retention.blanked(), **payment_retention.blanked(),
                    **member_retention.blanked(), **pack_retention.blanked(), **shop_retention.blanked(),
                    **meta_retention.blanked(), **inquiries_retention.blanked(),
                },
            },
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
        try:
            if dispose_account(account, now=now, rule=NEVER_VERIFIED) is not None:
                count += 1
        except NotDisposable:
            continue  # a paid term is running; the next run tries again
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
    mobile = AccountMobile.objects.filter(account=account).first()  # never behind sms_on()
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
        "mobile": None
        if mobile is None
        else {"number": mobile.number, "verified_at": _iso(mobile.verified_at)},
        "mobile_challenges": [
            {
                "number": row.number,
                "expires_at": _iso(row.expires_at),
                "used_at": _iso(row.used_at),
                "superseded_at": _iso(row.superseded_at),
                "created_at": _iso(row.created_at),
            }
            for row in account.mobile_challenges.order_by("pk")
        ],
        "email_send_records": email_records.export_records_for(account.email),
        **site_retention.export_sites_of(account),
    }
    sante = sante_retention.export_settings_of(account)
    data.update(sante)
    payments = payment_retention.export_payments_of(account)
    data.update(payments)
    members = member_retention.export_members_of(account)
    data.update(members)
    packs = pack_retention.export_packs_of(account)
    data.update(packs)
    transfers = billing.export_transfers_of(account)
    data.update(transfers)
    shops = shop_retention.export_shop_of(account)
    data.update(shops)
    lists = (
        "policy_acceptances", "sign_in_providers", "sessions", "audit_events",
        "mobile_challenges", "email_send_records", "sites",
    )
    counts = {name: len(data[name]) for name in lists}
    counts["mobile_numbers"] = 0 if mobile is None else 1
    counts.update({name: len(rows) for name, rows in sante.items()})
    counts.update({name: len(rows) for name, rows in payments.items()})
    counts.update({name: len(rows) for name, rows in members.items()})
    counts.update({name: len(rows) for name, rows in packs.items()})
    counts.update({name: len(rows) for name, rows in transfers.items()})
    counts.update({name: len(rows) for name, rows in shops.items()})
    audit.record(
        actor,
        "account_exported",
        target_type=TARGET,
        target_id=account.public_id,
        reason=request_ref,
        detail={"counts": counts},
    )
    return data


retention.register("expired_subscriber_sessions", expired_subscriber_sessions)
retention.register("never_verified_accounts", never_verified_accounts)
retention.register("account_audit_events", account_audit_events)
