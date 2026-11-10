"""The only way to write an OperatorAuditEntry.

Every action name is declared here with its class. PERSONAL rows may hold personal values in
`personal` and are the ones a retention job blanks; KEPT rows are kept whole. `record()` refuses
a name that is not declared, so a new action cannot skip this decision.
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
    "retention_run": KEPT,
}
SIGN_IN_ACTIONS = ("admin_sign_in_ok", "admin_sign_in_failed", "admin_locked", "admin_sign_out")
IDENTITY_TARGET = "admin_identity"


def record(operator, action, *, target_type="", target_id="", reason="", detail=None, personal=None):
    if action not in ACTIONS:
        raise ValueError(f"Audit action {action!r} is not declared in apps.operators.audit.ACTIONS.")
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
