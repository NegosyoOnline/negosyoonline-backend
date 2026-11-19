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


def bank_order_placed(member):
    """A live bank-transfer (direct-proof) order: the buyer is a member from placement, and the
    benefits come on only when the owner verifies the transfer (1100430000578068)."""
    order = live_order(member)
    return bool(order is not None and order.payment_method != C.XENDIT)


def pack_payment_verified(member):
    order = live_order(member)
    return bool(order is not None and order.payment_status in C.PAYMENT_OK)


def pack_lock_active(member):
    order = live_order(member)
    return bool(order is not None and order.rejection_lock_active)


def pack_dashboard_locked(member):
    """pack_lock_active OR (member.source == 'onboarding' AND NOT benefits_active). Covers every
    onboarding member without benefits, except a bank-transfer buyer whose order is placed (under
    review, rejected or verified): benefits wait for the verify, but sign-in and onboarding stay
    open. A rejection still locks."""
    return pack_lock_active(member) or (
        member.source == "onboarding" and not benefits_active(member) and not bank_order_placed(member)
    )
