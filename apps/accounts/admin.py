from django.contrib import admin

from apps.accounts.models import (
    Account,
    AccountAuditEvent,
    ExternalIdentity,
    PolicyAcceptance,
    Session,
)
from apps.common.admin import MaskedListAdmin
from apps.common.masking import mask_email


@admin.display(description="Email", ordering="email")
def masked_email(account):
    return mask_email(account.email)


@admin.display(description="Account", ordering="account__email")
def masked_account(row):
    """Stands in for the `account` column, which would print the full email."""
    return mask_email(row.account.email)


class ReadOnlyAdmin(MaskedListAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Account)
class AccountAdmin(MaskedListAdmin):
    list_display = (masked_email, "public_id", "name", "email_verified_at", "created_at", "closed_at")
    search_fields = ("email", "public_id", "name")
    readonly_fields = ("public_id", "referral_code", "created_at")


@admin.register(Session)
class SessionAdmin(ReadOnlyAdmin):
    list_display = (masked_account, "device_label", "masked_ip", "last_used_at", "expires_at", "revoked_at")
    list_select_related = ("account",)
    exclude = ("token_hash",)


@admin.register(PolicyAcceptance)
class PolicyAcceptanceAdmin(ReadOnlyAdmin):
    list_display = (masked_account, "policy_version", "accepted_at")
    list_select_related = ("account",)


@admin.register(AccountAuditEvent)
class AccountAuditEventAdmin(ReadOnlyAdmin):
    list_display = (masked_account, "event", "created_at")
    list_select_related = ("account",)
    list_filter = ("event",)


@admin.register(ExternalIdentity)
class ExternalIdentityAdmin(ReadOnlyAdmin):
    list_display = (masked_account, "provider", "linked_at")
    list_select_related = ("account",)
    list_filter = ("provider",)
