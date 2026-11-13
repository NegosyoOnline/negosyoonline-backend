"""What account disposal and account export do to the shop rows of an account's sites.

Called by apps.accounts.retention. While SHOP_ENABLED is off every function here answers empty
and reads no shop table.

Disposal (the rule, as for the pack, apps/pack/retention.py): the orders, their numbers, status,
totals and lines stay, because an order is never deleted and the Subscriber's books must still
add up. What identifies a person goes: the receiver name, mobile, email and delivery address on
each order, the reference number, account name and IP of each proof, and every proof file in the
private store. The evidence tables refuse queryset writes; disposal is the one deliberate
exception and goes through the plain base manager, so the refusal stays in force elsewhere."""
from django.conf import settings

from apps.common import private_files
from apps.shop.models import ShopOrder, ShopProofSubmission
from apps.sites.models import Site

ORDERS, PROOFS, PROOF_FILES = "shop_orders", "shop_proof_submissions", "shop_proof_files"
ORDER_PERSONAL = ("receiver_name", "receiver_mobile", "contact_email", "delivery_address")
PROOF_PERSONAL = ("reference_number", "account_name", "ip_advisory", "file_key")


def enabled():
    return settings.SHOP_ENABLED


def _iso(moment):
    return moment.isoformat() if moment else None


def blanked():
    """The field names a disposal blanks, for its audit entry."""
    if not enabled():
        return {}
    return {ORDERS: list(ORDER_PERSONAL), PROOFS: list(PROOF_PERSONAL)}


def blank_shop_of(account, dry_run=False):
    """Delete every proof file of the account's sites and blank the personal fields of its orders
    and proofs. The rows stay. Returns the counts; a dry run only counts. Call it inside a
    transaction. A file that cannot be deleted raises private_files.StoreError, so the disposal
    is undone and can be run again; deleting a file that is already gone succeeds."""
    if not enabled():
        return {}
    orders = ShopOrder.objects.filter(site__account=account)
    proofs = ShopProofSubmission._base_manager.filter(order__site__account=account)
    keys = list(proofs.exclude(file_key="").values_list("file_key", flat=True))
    counts = {ORDERS: orders.count(), PROOFS: proofs.count(), PROOF_FILES: len(keys)}
    if not dry_run:
        for key in keys:
            private_files.delete(key)
        orders.update(receiver_name="", receiver_mobile="", contact_email="", delivery_address={})
        proofs.update(reference_number="", account_name="", ip_advisory="", file_key="")
    return counts


def export_shop_of(account):
    """The shop orders and proofs of the account's sites as plain values: no file key, no token
    hash, no row number. Reads only."""
    if not enabled():
        return {}
    public_ids = dict(Site.objects.filter(account=account).values_list("pk", "public_id"))
    orders = (
        ShopOrder.objects.filter(site__account=account).prefetch_related("lines").order_by("pk")
    )
    proofs = (
        ShopProofSubmission.objects.filter(order__site__account=account)
        .select_related("order").order_by("pk")
    )
    return {
        ORDERS: [
            {
                "site": public_ids[row.site_id], "id": row.public_id, "number": row.order_number,
                "buyer_kind": row.buyer_kind, "ship_mode": row.ship_mode,
                "receiver_name": row.receiver_name, "receiver_mobile": row.receiver_mobile,
                "contact_email": row.contact_email, "delivery_address": row.delivery_address,
                "status": row.payment_status, "subtotal_centavos": row.subtotal_centavos,
                "shipping_charged_centavos": row.shipping_charged_centavos,
                "total_centavos": row.total_centavos, "currency": row.currency,
                "courier": row.courier, "tracking_no": row.tracking_no,
                "shipped_on": row.shipped_on.isoformat() if row.shipped_on else None,
                "delivered_on": row.delivered_on.isoformat() if row.delivered_on else None,
                "verified_at": _iso(row.verified_at), "cancelled_at": _iso(row.cancelled_at),
                "created_at": _iso(row.created_at),
                "lines": [
                    {
                        "code": line.code, "name": line.name, "quantity": line.quantity,
                        "line_charged_centavos": line.line_charged_centavos,
                    }
                    for line in row.lines.all()
                ],
            }
            for row in orders
        ],
        PROOFS: [
            {
                "id": row.public_id, "order": row.order.public_id, "account_kind": row.account_kind,
                "account_provider": row.account_provider_name, "account_name": row.account_name,
                "account_last4": row.account_number_last4, "reference_number": row.reference_number,
                "amount_sent_centavos": row.amount_sent_centavos, "content_type": row.content_type,
                "size": row.size, "submitted_at": _iso(row.submitted_at),
            }
            for row in proofs
        ],
    }
