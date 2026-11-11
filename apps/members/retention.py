"""What account disposal and account export do to the members of an account's sites, and the
rule for old sessions.

Called by apps.accounts.retention. While MEMBERS_ENABLED is off every function here answers
empty and reads no member table.
"""
from django.db.models import Q

from apps.common import retention
from apps.common.disposal import disposed_address
from apps.members import services
from apps.members.models import Member, MemberSession
from apps.sites.models import Site

MEMBERS, SESSIONS = "members", "member_sessions"
MEMBER_PERSONAL = ("full_name", "mobile", "email")


def _iso(moment):
    return moment.isoformat() if moment else None


def _placeholder_mobile(member):
    """A mobile that passes the table's check and that no handset can have."""
    return f"+63900{member.pk % 10_000_000:07d}"


def blanked():
    """The field names a disposal blanks, for its audit entry."""
    if not services.enabled():
        return {}
    return {MEMBERS: list(MEMBER_PERSONAL), SESSIONS: ["all rows deleted"]}


def blank_members_of(account, dry_run=False):
    """Delete every session of the members of the account's sites and blank each member's name,
    mobile and email. The rows stay: an order will point at its member. Returns the counts; a
    dry run only counts. Call it inside a transaction."""
    if not services.enabled():
        return {}
    members = Member.objects.filter(site__account=account)
    sessions = MemberSession.objects.filter(member__site__account=account)
    counts = {MEMBERS: members.count(), SESSIONS: sessions.count()}
    if not dry_run:
        sessions.delete()
        for member in list(members.select_for_update()):
            Member.objects.filter(pk=member.pk).update(
                full_name="", email=disposed_address(f"member-{member.pk}"),
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
                "source": row.source,
                "created_at": _iso(row.created_at),
            }
            for row in rows
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


retention.register("expired_member_sessions", expired_member_sessions)
