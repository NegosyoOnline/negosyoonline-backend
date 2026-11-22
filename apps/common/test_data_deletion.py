"""Shared pieces of the staging test-order deletion commands (`pack_delete_test_order`, ClickUp
z8uq9mfztm, and `shop_delete_test_order`, z8uq9mfzzn): the environment gate, the one sanctioned
bypass of the append-only guards, and the member-side cleanup. Used by management commands only."""
from django.conf import settings
from django.db.models import Q

from apps.members.models import (
    Member, MemberAddressEvent, MemberContactAudit, MemberNotification, MemberOrderLog,
    MemberOtpChallenge, MemberRecoveryCheck, MemberSecurityHold, MemberSession,
)
from apps.pack.models import PackBuyerNotice, PackOrder, SanteRegistration
from apps.shop.models import ShopOrder
from apps.training.models import LessonProgress


def environment_allows():
    """True on a local run (DEBUG) or on the environment named "staging" (settings.APP_ENVIRONMENT).
    Unknown or "production" refuses."""
    name = (getattr(settings, "APP_ENVIRONMENT", "") or "").strip().lower()
    if name == "production":
        return False
    return bool(settings.DEBUG) or name == "staging"


def gone(queryset):
    """Delete the rows of a queryset through the plain base manager and report how many. The
    append-only models refuse this through `objects`; the deletion commands are the only callers
    allowed (the one named bypass)."""
    model = queryset.model
    scoped = model._base_manager.filter(pk__in=queryset.values("pk"))
    count = scoped.count()
    scoped.delete()
    return count


def member_in_use(member):
    """True when a Pack order, a registration or a shop order (buyer or credited) still uses the
    member. Call it after the named orders are gone."""
    return (
        PackOrder.objects.filter(member=member).exists()
        or SanteRegistration.objects.filter(member=member).exists()
        or ShopOrder.objects.filter(Q(member=member) | Q(credited_member=member)).exists()
    )


def delete_member(member):
    """The member's own rows, leaf to root, then the member. Returns the counts per table."""
    by_member = {"member": member}
    audits = MemberContactAudit.objects.filter(**by_member)
    counts = {}
    for name, queryset in [
        ("member_security_holds", MemberSecurityHold.objects.filter(Q(member=member) | Q(audit__in=audits))),
        ("member_contact_audits", audits),
        ("member_recovery_checks", MemberRecoveryCheck.objects.filter(**by_member)),
        ("member_sessions", MemberSession.objects.filter(**by_member)),
        ("member_otp_challenges", MemberOtpChallenge.objects.filter(**by_member)),
        ("member_notifications", MemberNotification.objects.filter(**by_member)),
        ("member_order_logs", MemberOrderLog.objects.filter(**by_member)),
        ("member_address_events", MemberAddressEvent.objects.filter(**by_member)),
        ("pack_buyer_notices", PackBuyerNotice.objects.filter(**by_member)),
        ("lesson_progress", LessonProgress.objects.filter(**by_member)),
    ]:
        counts[name] = gone(queryset)
    gone(Member.objects.filter(pk=member.pk))
    return counts
