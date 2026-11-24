"""The only way to write an OperatorAuditEntry.

Every action name is declared here with its class. PERSONAL rows may hold personal values in
`personal` and are the ones the retention rule `admin_audit_personal_values` blanks after a year;
KEPT rows are kept whole and may not hold one. `record()` refuses a name that is not declared,
so a new action cannot skip this decision.
"""
from apps.operators.models import OperatorAuditEntry

PERSONAL, KEPT = "personal", "kept"

ACTIONS = {
    "admin_sign_in_ok": PERSONAL,
    "admin_sign_in_failed": PERSONAL,
    "admin_locked": PERSONAL,
    "admin_sign_out": PERSONAL,
    "admin_identity_add": KEPT,
    "admin_identity_enrol": KEPT,
    "admin_identity_reset": KEPT,
    "admin_identity_disable": KEPT,
    "sms_policy_change": KEPT,
    "sms_device_add": KEPT,
    "sms_device_rotate": KEPT,
    "sms_device_disable": KEPT,
    "sms_device_replace": KEPT,
    "sms_device_incoming": KEPT,
    # A refused fresh code on the HQ phone screens. Counts only: never a code.
    "sms_device_reauth_failed": KEPT,
    "sms_inbox_access": KEPT,
    "sms_inbox_open": PERSONAL,
    "sms_inbox_find": KEPT,
    "sms_inbox_delete": KEPT,
    "sms_inbox_reply": PERSONAL,
    "sms_inbox_compose": PERSONAL,
    "contact_open": KEPT,
    "contact_find": KEPT,
    "contact_add": KEPT,
    "contact_edit": KEPT,
    "contact_delete": KEPT,
    "contact_upload": KEPT,
    "contact_group_add": KEPT,
    "contact_group_rename": KEPT,
    "contact_group_remove": KEPT,
    "sms_inbox_bulk": KEPT,
    "sms_inbox_bulk_stop": KEPT,
    "sms_inbox_bulk_left_out": KEPT,
    "retention_run": KEPT,
    "account_disposed": KEPT,
    "account_exported": KEPT,
    "pack_files_exported": KEPT,
    "pack_test_member_removed": KEPT,
    "shop_test_order_removed": KEPT,
    "pack_test_order_deleted": KEPT,
    "shop_test_order_deleted": KEPT,
    "billing_exempt_set": KEPT,
    # The one-off emptying of the billing and ledger tables after the P7 production copy.
    # Counts per model and the database alias: never a value.
    "billing_reset_for_production": KEPT,
    # HQ -> Subscribers: one detail read of a subscriber site. Ids and counts only.
    "hq_subscriber_viewed": KEPT,
    # HQ -> Subscribers: Disable / Re-enable of a Site's own SMS line. Site id and reason only.
    "hq_sms_line_disabled": KEPT,
    "hq_sms_line_reenabled": KEPT,
    "hq_sms_line_reauth_failed": KEPT,
    # HQ -> Subscribers: Suspend / Resume of a store by hand (Site.operator_hold). Site id and
    # reason only; a refused fresh code is counts only.
    "hq_site_suspended": KEPT,
    "hq_site_resumed": KEPT,
    "hq_site_reauth_failed": KEPT,
    "billing_payment_confirmed": KEPT,
    "billing_payment_rejected": KEPT,
    "billing_usage_admitted": KEPT,
    "billing_usage_dismissed": KEPT,
    "billing_proof_viewed": KEPT,
    "billing_receiving_account_added": KEPT,
    "billing_receiving_account_edited": KEPT,
    "billing_receiving_account_activated": KEPT,
    "billing_receiving_account_deactivated": KEPT,
    "billing_issuer_set": KEPT,
    "billing_issuer_verified": KEPT,
    "billing_statement_adjusted": KEPT,
    "billing_statement_viewed": KEPT,
    "billing_statement_resent": KEPT,
    "billing_invoice_book_set": KEPT,
    "billing_invoice_recorded": KEPT,
    "billing_invoice_voided": KEPT,
    "billing_invoice_scan_uploaded": KEPT,
    "billing_invoice_scan_viewed": KEPT,
    "billing_invoice_verified": KEPT,
    "billing_invoice_scan_rejected": KEPT,
    # A refused edit or delete of an issued statement, its lines or an invoice item (B5 section 5.4).
    # Ids, the name of a field and counts: never a value.
    "billing_record_edit_refused": KEPT,
    "billing_record_delete_refused": KEPT,
    "sante_catalog_owner_set": KEPT,
    "sante_content_source_set": KEPT,
    # A site put on or taken off operator hold (manage.py site_hold). Ids and on/off only.
    "site_operator_hold_set": KEPT,
    "sante_agreement_version_created": KEPT,
    "sante_agreement_version_updated": KEPT,
    "sante_agreement_version_deleted": KEPT,
    "sante_agreement_published": KEPT,
    # The site deletion executor (plan z8uq9mb22v, section 7). Ids, codes, counts and the names of
    # fields: never a value.
    "erasure_batch_proposed": KEPT,
    "erasure_batch_approved": KEPT,
    "erasure_batch_cancelled": KEPT,
    "site_data_erased": KEPT,
    "site_erasure_skipped": KEPT,
    "erasure_hold_set": KEPT,
    "erasure_hold_released": KEPT,
    # Custom domains (apps/domains/staff.py). Ids and codes: never a value.
    "domains_retry": KEPT,
    "domains_disconnect": KEPT,
    "domains_request_cancelled": KEPT,
    "domains_confirm_requested": KEPT,
    "domains_confirmed_registered": KEPT,
    "domains_confirm_refused": KEPT,
}
SIGN_IN_ACTIONS = ("admin_sign_in_ok", "admin_sign_in_failed", "admin_locked", "admin_sign_out")
IDENTITY_TARGET = "admin_identity"


def record(
    operator, action, *, target_type="", target_id="", reason="", detail=None, personal=None,
    using=None,
):
    if action not in ACTIONS:
        raise ValueError(f"Audit action {action!r} is not declared in apps.operators.audit.ACTIONS.")
    if personal and ACTIONS[action] != PERSONAL:
        raise ValueError(f"Audit action {action!r} is kept whole: it cannot hold a personal value.")
    # `using` names a database alias, for a command run against a copy; every other caller omits it.
    manager = OperatorAuditEntry.objects if using is None else OperatorAuditEntry.objects.using(using)
    return manager.create(
        operator=operator,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        reason=reason,
        detail=detail or {},
        personal=personal or {},
    )


def recent_sign_in_events(identity, limit=10):
    """The newest sign-in events of one admin identity."""
    return list(
        OperatorAuditEntry.objects.filter(
            target_type=IDENTITY_TARGET, target_id=str(identity.pk), action__in=SIGN_IN_ACTIONS
        ).order_by("-at", "-pk")[:limit]
    )
