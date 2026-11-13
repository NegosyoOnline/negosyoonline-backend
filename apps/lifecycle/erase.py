"""The erasure of one ended site's store data (plan z8uq9mb22v, sections 3 and 6.4).

`erase_item` is the only code in the application that erases an ended site. It runs one
`ErasureItem` of an approved batch, in the caller's transaction, under the locks of section 6.4,
and either erases the site, skips it with a reason code (nothing erased) or raises, so the caller's
transaction rolls back and the item is marked failed.

Nothing here is a new rule: it calls the per-app blank functions that account disposal already
uses, with `ignore_switch=True` (plan D13) so a feature switch that is off never leaves a personal
value behind. It writes none of the tables the retention floor keeps (billing, statements, invoices,
ledger, audit) apart from the new audit entries and, for a suspended site's open usage lines, the
write-off that the billing sweep makes at this same date (`usage.write_off_unreturned`, R-4c).

Pack proof files are never deleted here (L67): the Pack purge's own gate `files_deletable` decides,
and it is open only for a site whose service ended on or after `PROOF_EXPORT_READY_FROM`.
"""
from apps.accounts import retention as account_retention
from apps.accounts.models import Account
from apps.billing import usage
from apps.billing.models import Subscription
from apps.common import retention
from apps.inbox import retention as inbox_retention
from apps.inquiries import retention as inquiries_retention
from apps.lifecycle import dry_run, eligibility
from apps.lifecycle.models import ErasureItem
from apps.members import retention as member_retention
from apps.meta_ads import retention as meta_retention
from apps.operators import audit
from apps.pack import purge
from apps.pack import retention as pack_retention
from apps.payments import retention as payment_retention
from apps.sante import retention as sante_retention
from apps.sante.models import SanteCatalogOwner
from apps.shop import retention as shop_retention
from apps.sites import retention as site_retention
from apps.sites.models import Onboarding, Site, SiteBusinessProfile

Status = ErasureItem.Status
RULE = "site_erasure"

# Skip codes the run adds to the eligibility codes.
RETENTION_OFF = dry_run.RETENTION_OFF
PACK_PURGE_REFUSED = "pack_purge_refused"
# Account kept, with the item still erased.
ALREADY_DISPOSED, NOT_DISPOSABLE = "already_disposed", "not_disposable"

# The names (never the values) of the fields an erasure blanks, for the audit entry. Pack proof
# files and their keys are not on this list: they stay (L67).
BLANKED = {
    **site_retention.BLANKED,
    "site_seal_file": ["deleted"],
    sante_retention.TABLE: ["sponsor_id"],
    payment_retention.ACCOUNTS: list(payment_retention.ACCOUNT_PERSONAL),
    member_retention.MEMBERS: list(member_retention.MEMBER_PERSONAL),
    member_retention.SESSIONS: ["all rows deleted"],
    member_retention.CHALLENGES: ["all rows deleted"],
    pack_retention.ORDERS: list(pack_retention.ORDER_PERSONAL),
    pack_retention.PROOFS: list(purge.PROOF_TEXT_FIELDS),
    pack_retention.TOKENS: ["all rows deleted"],
    pack_retention.REGISTRATIONS: list(pack_retention.REGISTRATION_PERSONAL),
    shop_retention.ORDERS: list(shop_retention.ORDER_PERSONAL),
    shop_retention.PROOFS: [*shop_retention.PROOF_PERSONAL, "account_number_last4"],
    meta_retention.SETTINGS: ["token_encrypted", "test_event_code"],
    meta_retention.CONTEXTS: ["all rows deleted"],
    inbox_retention.CONVERSATIONS: ["all rows deleted"],
    inbox_retention.MESSAGES: ["all rows deleted"],
    inbox_retention.CONTACTS: ["all rows deleted"],
    inbox_retention.GROUPS: ["all rows deleted"],
    **inquiries_retention.blanked(),
}


def _lock(item, subscription_id):
    """Account, Onboarding, Subscription, then the site row (the lock order everywhere)."""
    site = item.site
    Account.objects.select_for_update().get(pk=site.account_id)
    list(Onboarding.objects.select_for_update().filter(account_id=site.account_id))
    subscription = Subscription.objects.select_for_update().filter(pk=subscription_id).first()
    Site.objects.select_for_update().get(pk=site.pk)
    return subscription


def _other_sites_gone(account, site):
    """Whether every other site of the account was erased by this executor."""
    others = Site.objects.filter(account=account).exclude(pk=site.pk)
    erased = ErasureItem.objects.filter(site__in=others, status=Status.ERASED).values("site_id")
    return not others.exclude(pk__in=erased).exists()


def _pack_files_stay(account):
    """Whether the account step must keep pack proof files (plan D14): unless every site of the
    account has a subscription whose service ended when the export already carried images."""
    sites = set(Site.objects.filter(account=account).values_list("pk", flat=True))
    ends = dict(
        Subscription.objects.filter(site__account=account).values_list("site_id", "service_ended_at")
    )
    if set(ends) != sites:
        return True
    return not all(purge.files_deletable(end) for end in ends.values())


def _skip(item, batch, reason, now):
    item.status, item.reason_code = Status.SKIPPED, reason
    item.save(update_fields=["status", "reason_code"])
    audit.record(
        None, "site_erasure_skipped", target_type="site", target_id=item.site.public_id,
        reason=reason, detail={"batch": batch.public_id, "reason_code": reason},
    )
    return Status.SKIPPED


def _stop_reason(item, subscription, now):
    """Why this item must not run now, or None. Called under the locks."""
    if subscription is None or subscription.site_id != item.site_id:
        return eligibility.NO_LONGER_DUE
    reason = eligibility.stop_reason(subscription, now, planned_due=item.deletion_due_at)
    if reason is None and subscription.state == Subscription.State.SUSPENDED and (
        eligibility.has_open_charge(subscription)
    ):
        # R-4c: a suspended account that never came back owes nothing at its deletion date.
        usage.write_off_unreturned(now)
        if eligibility.has_open_charge(subscription):
            return eligibility.UNPAID_USAGE
    if reason is None:
        if not retention.enabled() and would_dispose_account(item.site):
            return RETENTION_OFF  # refused whole, never half done (plan section 3)
    return reason


def _account_step(batch, item, now):
    """('disposed' | kept reason | ''). The account goes with its last site (plan D7)."""
    account = Account.objects.get(pk=item.site.account_id)
    if account_retention.is_disposed(account):
        return False, ALREADY_DISPOSED
    if not _other_sites_gone(account, item.site):
        return False, dry_run.OTHER_LIVE_SITE
    try:
        done = account_retention.dispose_account(
            account, now=now, rule=RULE, actor=None, request_ref=batch.public_id,
            keep_pack_files=_pack_files_stay(account),
        )
    except account_retention.NotDisposable as refused:
        return False, refused.reasons[0]["reason"] if refused.reasons else NOT_DISPOSABLE
    return done is not None, ""


def erase_item(batch, item, now):
    """Run one pending or failed item. Call inside a transaction that holds the batch row.

    Returns the item's new status (erased or skipped). Any exception means nothing was erased
    and the caller's transaction must be rolled back; `private_files.StoreError` is the expected
    one (a file that cannot be deleted)."""
    subscription = _lock(item, item.subscription_id)
    reason = _stop_reason(item, subscription, now)
    if reason is not None:
        return _skip(item, batch, reason, now)
    site = item.site
    sites = Site.objects.filter(pk__in=[site.pk])  # a fixed list: the run changes the site rows
    seals = SiteBusinessProfile.objects.filter(site__in=sites).exclude(seal_key="").count()
    counts = {}
    # The Pack part first (plan section 3a): it clears Pack buyers through the purge's own
    # function, which keeps proof files while its gate is closed and honours holds.
    counts.update(pack_retention.blank_packs(sites, dry_run=True, ignore_switch=True, keep_files=True))
    if not purge.purge_site(site.pk, now):
        return _skip(item, batch, PACK_PURGE_REFUSED, now)
    counts.update(site_retention.blank_sites(sites, now))
    counts.update(sante_retention.blank_settings(sites, ignore_switch=True))
    counts.update(payment_retention.blank_payments(sites, now, ignore_switch=True))
    counts.update(member_retention.blank_members(sites, ignore_switch=True))
    counts.update(shop_retention.blank_shop(sites, ignore_switch=True, last4=True))
    counts.update(meta_retention.blank_meta(sites))
    counts.update(inbox_retention.erase_site_inbox(sites))
    counts.update(inquiries_retention.blank_inquiries(sites))
    if seals:
        counts[dry_run.SEAL_FILES] = seals
    files = seals + sum(counts.get(key, 0) for key in dry_run.FILE_KEYS)
    item.status, item.reason_code, item.counts, item.erased_at = Status.ERASED, "", counts, now
    item.save(update_fields=["status", "reason_code", "counts", "erased_at"])
    disposed, kept = _account_step(batch, item, now)
    item.account_disposed = disposed
    item.save(update_fields=["account_disposed"])
    audit.record(
        None, "site_data_erased", target_type="site", target_id=site.public_id,
        reason=batch.public_id,
        detail={
            "batch": batch.public_id,
            "approved_by": batch.approved_by_id,
            "deletion_due_at": item.deletion_due_at.isoformat(),
            "counts": counts,
            "files_deleted": files,
            "blanked": BLANKED,
            "account_disposed": disposed,
            **({"account_kept": kept} if kept else {}),
        },
    )
    return Status.ERASED


def would_dispose_account(site, selected_pks=()):
    """Whether erasing `site` ends with its account disposed of: every other site of the account
    is erased already or among `selected_pks`, the account is not disposed of and does not own
    the Santé catalog owner site. The proposal uses it to refuse a site while RETENTION_ENABLED is
    off; the run uses it with no selection."""
    account = site.account
    if account_retention.is_disposed(account):
        return False
    if SanteCatalogOwner.objects.filter(site__account=account).exists():
        return False
    others = Site.objects.filter(account=account).exclude(pk=site.pk)
    erased = ErasureItem.objects.filter(site__in=others, status=Status.ERASED).values("site_id")
    return all(pk in selected_pks for pk in others.exclude(pk__in=erased).values_list("pk", flat=True))
