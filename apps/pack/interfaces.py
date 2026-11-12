"""Hand-off reads (port of TWCinc's negosyoonline_sante_pack/interfaces.py). The member
dashboard reads pack_dashboard_locked. Storefront buying and selling never call the lock
predicates ("Keep storefront buying and selling separate")."""
from apps.pack import constants as C
from apps.pack.models import PackOrder


def live_order(member):
    return PackOrder.objects.filter(member=member, payment_status__in=C.LIVE_STATUSES).first()


def benefits_active(member):
    order = live_order(member)
    return bool(order is not None and order.benefits_active_at)


def pack_payment_verified(member):
    order = live_order(member)
    return bool(order is not None and order.payment_status in C.PAYMENT_OK)


def pack_lock_active(member):
    order = live_order(member)
    return bool(order is not None and order.rejection_lock_active)


def pack_dashboard_locked(member):
    """pack_lock_active OR (member.source == 'onboarding' AND NOT benefits_active). Covers every
    onboarding member without benefits. Only a rejection locks a member who has benefits."""
    return pack_lock_active(member) or (member.source == "onboarding" and not benefits_active(member))
