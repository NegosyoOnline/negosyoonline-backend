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
    "sms_device_incoming": KEPT,
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
    "billing_exempt_set": KEPT,
    "billing_payment_confirmed": KEPT,
    "billing_payment_rejected": KEPT,
    "billing_usage_admitted": KEPT,
    "billing_usage_dismissed": KEPT,
    "billing_proof_viewed": KEPT,
    "sante_catalog_owner_set": KEPT,
    "sante_agreement_version_created": KEPT,
    "sante_agreement_version_updated": KEPT,
    "sante_agreement_version_deleted": KEPT,
    "sante_agreement_published": KEPT,
}
SIGN_IN_ACTIONS = ("admin_sign_in_ok", "admin_sign_in_failed", "admin_locked", "admin_sign_out")
IDENTITY_TARGET = "admin_identity"


def record(operator, action, *, target_type="", target_id="", reason="", detail=None, personal=None):
    if action not in ACTIONS:
        raise ValueError(f"Audit action {action!r} is not declared in apps.operators.audit.ACTIONS.")
    if personal and ACTIONS[action] != PERSONAL:
        raise ValueError(f"Audit action {action!r} is kept whole: it cannot hold a personal value.")
    return OperatorAuditEntry.objects.create(
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
