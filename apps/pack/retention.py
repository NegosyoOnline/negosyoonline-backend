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
from django.db import transaction
from django.db.models import Q

from apps.common import private_files, retention
from apps.pack import registration
from apps.pack.models import CheckoutToken, PackOrder, PackProofSubmission, SanteRegistration
from apps.sites.models import Site

ORDERS, PROOFS, PROOF_FILES, TOKENS, REGISTRATIONS = (
    "pack_orders", "pack_proof_submissions", "pack_proof_files", "pack_checkout_tokens",
    "sante_registrations",
)
REGISTRATION_PERSONAL = ("date_of_birth", "desired_username", "alternate_username")
# TWCinc sweep.py: details are kept 90 days after the last status change, whatever the status.
DETAILS_RETENTION = timedelta(days=90)
HOLDS_DETAILS = (
    Q(date_of_birth__isnull=False) | ~Q(desired_username="") | ~Q(alternate_username="")
)
ERASE_T24 = ("waiting_for_accept", "ready_to_register", "in_registration", "retry_wait")
CLEAR_CONFIRMED = ("awaiting_buyer_details", "needs_buyer_action", "possible_existing_member")
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
        REGISTRATIONS: list(REGISTRATION_PERSONAL),
    }


def blank_packs_of(account, dry_run=False, *, keep_files=False):
    """Delete every proof file and every checkout token of the account's sites and blank the
    personal fields of its orders and proofs. The rows stay. Returns the counts; a dry run only
    counts. Call it inside a transaction.

    A file that cannot be deleted raises private_files.StoreError, so the disposal is undone and
    can be run again; deleting a file that is already gone succeeds."""
    return blank_packs(Site.objects.filter(account=account), dry_run, keep_files=keep_files)


def blank_packs(sites, dry_run=False, *, ignore_switch=False, keep_files=False):
    """The same for the sites in `sites`. `ignore_switch` reads the tables whatever PACK_ENABLED
    says (plan D13). `keep_files` leaves every proof file and its key where they are and counts
    no file (plan D14: the proof images stay until the export carries them). Defaults unchanged."""
    if not ignore_switch and not enabled():
        return {}
    orders = PackOrder.objects.filter(site__in=sites)
    proofs = PackProofSubmission._base_manager.filter(site__in=sites)
    tokens = CheckoutToken.objects.filter(site__in=sites)
    keys = [] if keep_files else list(proofs.exclude(file_key="").values_list("file_key", flat=True))
    registrations = SanteRegistration.objects.filter(site__in=sites).filter(HOLDS_DETAILS)
    counts = {
        ORDERS: orders.count(), PROOFS: proofs.count(), PROOF_FILES: len(keys), TOKENS: tokens.count(),
        REGISTRATIONS: registrations.count(),
    }
    if not dry_run:
        for key in keys:
            private_files.delete(key)
        tokens.delete()
        orders.update(buyer_name="", delivery_address={})
        wiped = {"sender_name": "", "sender_account_number": "", "reference_number": "", "ip_advisory": ""}
        proofs.update(**wiped, **({} if keep_files else {"file_key": ""}))
        registrations.update(date_of_birth=None, desired_username="", alternate_username="")
    return counts


def export_packs_of(account):
    """The pack orders and proofs of the account's sites as plain values: no file key, no token,
    no row number. Reads only."""
    if not enabled():
        return {}
    return export_packs(Site.objects.filter(account=account))


def export_packs(sites):
    """The same values for the sites in `sites` (a queryset of Site). The account export and the
    Pack files archive (apps.pack.export) both build from this, so they cannot drift."""
    public_ids = dict(sites.values_list("pk", "public_id"))
    orders = PackOrder.objects.filter(site__in=sites).select_related("member").order_by("pk")
    proofs = PackProofSubmission.objects.filter(site__in=sites).select_related("order").order_by("pk")
    registrations = SanteRegistration.objects.filter(site__in=sites).select_related("member").order_by("pk")
    return {
        REGISTRATIONS: [
            {
                "site": public_ids[row.site_id], "member": row.member.public_id, "status": row.status,
                "date_of_birth": row.date_of_birth.isoformat() if row.date_of_birth else None,
                "desired_username": row.desired_username, "alternate_username": row.alternate_username,
                "details_confirmed_at": _iso(row.details_confirmed_at),
            }
            for row in registrations
        ],
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


@retention.behind_setting("DOB_ERASE_ENABLED")
def registration_details(now, dry_run):
    """Date of birth and the two Santé usernames, 90 days after the registration's last status
    change (TWCinc sweep._erase, Coach Ed's erase rule): each row under its own lock. In-progress
    rows take T24 to needs_buyer_action; rows waiting on the customer lose details_confirmed_at, so
    the buyer enters them again. The registration row itself stays.

    Ruling D3: this erase never depends on the Subscriber's subscription, so it has its own
    switch, DOB_ERASE_ENABLED, and not RETENTION_ENABLED. It also needs PACK_ENABLED."""
    if not enabled():
        return 0
    due = SanteRegistration.objects.filter(HOLDS_DETAILS, status_entered_at__lte=now - DETAILS_RETENTION)
    if dry_run:
        return due.count()
    erased = 0
    for pk in due.values_list("pk", flat=True):
        with transaction.atomic():
            reg = SanteRegistration.objects.select_for_update().select_related("pack_order").get(pk=pk)
            if reg.status_entered_at > now - DETAILS_RETENTION or not (
                reg.date_of_birth or reg.desired_username or reg.alternate_username
            ):
                continue
            reg.date_of_birth, reg.desired_username, reg.alternate_username = None, "", ""
            reg.details_purged_at = now
            if reg.status in ERASE_T24:
                reg.details_confirmed_at = None
                registration._transition(
                    reg, "T24", actor=registration.SYSTEM, reason_code="details_expired", now=now
                )
            else:
                if reg.status in CLEAR_CONFIRMED:
                    reg.details_confirmed_at = None
                reg.state_version += 1
                reg.save()
            erased += 1
    return erased


retention.register("expired_checkout_tokens", expired_checkout_tokens)
retention.register("pack_registration_details", registration_details)
