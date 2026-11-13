"""The Membership Package order number as an identity-check item of the Subscriber's member recovery.
Applies to a member who has an order; the Subscriber sees the number from their own records."""
from apps.members import recovery
from apps.pack.models import PackOrder


def _latest(member):
    return PackOrder.objects.filter(site_id=member.site_id, member=member).order_by("-created_at", "-pk").first()


def register():
    recovery.register_checklist_item(
        "pack_order_number", lambda member: _latest(member) is not None,
        reference=lambda member: getattr(_latest(member), "order_number", ""))
