from django.contrib import admin

from apps.accounts.models import (
    Account,
    AccountAuditEvent,
    ExternalIdentity,
    PolicyAcceptance,
    Session,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("email", "public_id", "name", "email_verified_at", "created_at", "closed_at")
    search_fields = ("email", "public_id", "name")
    readonly_fields = ("public_id", "referral_code", "created_at")


@admin.register(Session)
class SessionAdmin(ReadOnlyAdmin):
    list_display = ("account", "device_label", "masked_ip", "last_used_at", "expires_at", "revoked_at")
    exclude = ("token_hash",)


@admin.register(PolicyAcceptance)
class PolicyAcceptanceAdmin(ReadOnlyAdmin):
    list_display = ("account", "policy_version", "accepted_at")


@admin.register(AccountAuditEvent)
class AccountAuditEventAdmin(ReadOnlyAdmin):
    list_display = ("account", "event", "created_at")
    list_filter = ("event",)


@admin.register(ExternalIdentity)
class ExternalIdentityAdmin(ReadOnlyAdmin):
    list_display = ("account", "provider", "linked_at")
    list_filter = ("provider",)
