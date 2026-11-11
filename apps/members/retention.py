"""What account disposal and account export do to the members of an account's sites, and the
rule for old sessions.

Called by apps.accounts.retention. While MEMBERS_ENABLED is off every function here answers
empty and reads no member table.
"""
from django.db.models import Q

from apps.common import retention
from apps.common.disposal import disposed_address
from apps.members import services
from apps.members.models import Member, MemberContactAudit, MemberOtpChallenge, MemberSession
from apps.sites.models import Site

MEMBERS, SESSIONS = "members", "member_sessions"
CHALLENGES, AUDITS = "member_otp_challenges", "member_contact_audits"
MEMBER_PERSONAL = ("full_name", "mobile", "email", "delivery_address")


def _iso(moment):
    return moment.isoformat() if moment else None


def _placeholder_mobile(member):
    """A mobile that passes the table's check and that no handset can have."""
    return f"+63900{member.pk % 10_000_000:07d}"


def blanked():
    """The field names a disposal blanks, for its audit entry."""
    if not services.enabled():
        return {}
    return {
        MEMBERS: list(MEMBER_PERSONAL), SESSIONS: ["all rows deleted"], CHALLENGES: ["all rows deleted"],
    }


def blank_members_of(account, dry_run=False):
    """Delete every session and every sign-in code row of the members of the account's sites and
    blank each member's name, mobile, email and delivery address. The rows stay: an order will
    point at its member, and the contact audit, recovery checks and holds keep only masked values.
    Returns the counts; a dry run only counts. Call it inside a transaction."""
    if not services.enabled():
        return {}
    members = Member.objects.filter(site__account=account)
    sessions = MemberSession.objects.filter(member__site__account=account)
    challenges = MemberOtpChallenge.objects.filter(site__account=account)
    counts = {MEMBERS: members.count(), SESSIONS: sessions.count(), CHALLENGES: challenges.count()}
    if not dry_run:
        sessions.delete()
        challenges.delete()
        for member in list(members.select_for_update()):
            Member.objects.filter(pk=member.pk).update(
                full_name="", delivery_address={}, email=disposed_address(f"member-{member.pk}"),
                mobile=_placeholder_mobile(member), mobile_state="pending", mobile_verified_at=None,
                email_state="pending", email_verified_at=None,
            )
    return counts


def export_members_of(account):
    """The members of the account's sites as plain values: their contacts and dates, never a
    token hash, an address of a session or a row number. Reads only."""
    if not services.enabled():
        return {}
    public_ids = dict(Site.objects.filter(account=account).values_list("pk", "public_id"))
    rows = Member.objects.filter(site__account=account).order_by("pk")
    sessions = MemberSession.objects.filter(member__site__account=account).select_related("member")
    audits = MemberContactAudit.objects.filter(site__account=account).select_related("member")
    return {
        MEMBERS: [
            {
                "site": public_ids[row.site_id],
                "id": row.public_id,
                "full_name": row.full_name,
                "mobile": row.mobile,
                "mobile_verified_at": _iso(row.mobile_verified_at),
                "email": row.email,
                "email_verified_at": _iso(row.email_verified_at),
                "delivery_address": row.delivery_address,
                "source": row.source,
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ],
        AUDITS: [
            {
                "member": row.member.public_id,
                "kind": row.kind,
                "field": row.field,
                "old": row.old_masked,
                "new": row.new_masked,
                "created_at": _iso(row.created_at),
            }
            for row in audits.order_by("pk")
        ],
        SESSIONS: [
            {
                "member": row.member.public_id,
                "created_at": _iso(row.created_at),
                "expires_at": _iso(row.expires_at),
                "revoked_at": _iso(row.revoked_at),
                "revoke_reason": row.revoke_reason,
            }
            for row in sessions.order_by("pk")
        ],
    }


@retention.behind_switch
def expired_member_sessions(now, dry_run):
    """Member sessions, 30 days after they expired or were revoked."""
    if not services.enabled():
        return 0
    cutoff = now - retention.CREDENTIAL_GRACE
    rows = MemberSession.objects.filter(Q(expires_at__lte=cutoff) | Q(revoked_at__lte=cutoff))
    return retention.remove(rows, dry_run)


@retention.behind_switch
def expired_member_challenges(now, dry_run):
    """Sign-in code rows (they hold the contact a code was sent to), 30 days after they expired."""
    if not services.enabled():
        return 0
    rows = MemberOtpChallenge.objects.filter(expires_at__lte=now - retention.CREDENTIAL_GRACE)
    return retention.remove(rows, dry_run)


retention.register("expired_member_sessions", expired_member_sessions)
retention.register("expired_member_challenges", expired_member_challenges)
