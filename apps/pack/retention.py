"""What account disposal and account export do to the Membership Pack rows of an account's
sites, and the rule for old checkout tokens.

Called by apps.accounts.retention. While PACK_ENABLED is off every function here answers empty
and reads no pack table.

Disposal (the rule, for Coach Ed's review in the PR): the order rows, their numbers, status, the
frozen price and the agreement and enrollment records stay, because an order is never deleted and
the Subscriber's books must still add up. What identifies a person goes: the buyer name and
delivery address on each order, the sender name, sender account number, reference and IP on each
proof, every proof file in the private store, and every live checkout token with its typed
contact. The evidence tables refuse queryset writes; disposal is the one deliberate exception
and goes through the plain manager, so the refusal stays in force everywhere else."""
from datetime import timedelta

from django.conf import settings

from apps.common import private_files, retention
from apps.pack.models import CheckoutToken, PackOrder, PackProofSubmission
from apps.sites.models import Site

ORDERS, PROOFS, PROOF_FILES, TOKENS = (
    "pack_orders", "pack_proof_submissions", "pack_proof_files", "pack_checkout_tokens",
)
ORDER_PERSONAL = ("buyer_name", "delivery_address")
PROOF_PERSONAL = ("sender_name", "sender_account_number", "reference_number", "ip_advisory", "file_key")
TOKEN_GRACE = retention.CREDENTIAL_GRACE  # an expired token is deleted this long after it expires


def enabled():
    return settings.PACK_ENABLED


def _iso(moment):
    return moment.isoformat() if moment else None


def blanked():
    """The field names a disposal blanks, for its audit entry."""
    if not enabled():
        return {}
    return {
        ORDERS: list(ORDER_PERSONAL), PROOFS: list(PROOF_PERSONAL), TOKENS: ["all rows deleted"],
    }


def blank_packs_of(account, dry_run=False):
    """Delete every proof file and every checkout token of the account's sites and blank the
    personal fields of its orders and proofs. The rows stay. Returns the counts; a dry run only
    counts. Call it inside a transaction.

    A file that cannot be deleted raises private_files.StoreError, so the disposal is undone and
    can be run again; deleting a file that is already gone succeeds."""
    if not enabled():
        return {}
    orders = PackOrder.objects.filter(site__account=account)
    proofs = PackProofSubmission._base_manager.filter(site__account=account)
    tokens = CheckoutToken.objects.filter(site__account=account)
    keys = list(proofs.exclude(file_key="").values_list("file_key", flat=True))
    counts = {ORDERS: orders.count(), PROOFS: proofs.count(), PROOF_FILES: len(keys), TOKENS: tokens.count()}
    if not dry_run:
        for key in keys:
            private_files.delete(key)
        tokens.delete()
        orders.update(buyer_name="", delivery_address={})
        proofs.update(sender_name="", sender_account_number="", reference_number="", ip_advisory="", file_key="")
    return counts


def export_packs_of(account):
    """The pack orders and proofs of the account's sites as plain values: no file key, no token,
    no row number. Reads only."""
    if not enabled():
        return {}
    public_ids = dict(Site.objects.filter(account=account).values_list("pk", "public_id"))
    orders = PackOrder.objects.filter(site__account=account).select_related("member").order_by("pk")
    proofs = (
        PackProofSubmission.objects.filter(site__account=account).select_related("order").order_by("pk")
    )
    return {
        ORDERS: [
            {
                "site": public_ids[row.site_id], "id": row.public_id, "number": row.order_number,
                "member": row.member.public_id, "buyer_name": row.buyer_name,
                "delivery_address": row.delivery_address, "price_centavos": row.price_centavos,
                "currency": row.currency, "status": row.payment_status,
                "benefits_active_at": _iso(row.benefits_active_at), "verified_at": _iso(row.verified_at),
                "policy_version": row.policy_version, "created_at": _iso(row.created_at),
            }
            for row in orders
        ],
        PROOFS: [
            {
                "id": row.public_id, "order": row.order.public_id, "seq": row.seq,
                "account_kind": row.account_kind, "account_provider": row.account_provider_name,
                "account_name": row.account_name, "account_last4": row.account_number_last4,
                "reference_number": row.reference_number, "amount_sent_centavos": row.amount_sent_centavos,
                "sender_bank": row.sender_bank, "sender_name": row.sender_name,
                "sender_account_number": row.sender_account_number,
                "transferred_at": _iso(row.transferred_at), "content_type": row.content_type,
                "size": row.size, "submitted_at": _iso(row.submitted_at),
            }
            for row in proofs
        ],
    }


@retention.behind_switch
def expired_checkout_tokens(now, dry_run):
    """Checkout tokens, 30 days after they expired: each holds the contact a buyer typed."""
    if not enabled():
        return 0
    rows = CheckoutToken.objects.filter(expires_at__lte=now - TOKEN_GRACE)
    return retention.remove(rows, dry_run)


retention.register("expired_checkout_tokens", expired_checkout_tokens)
