from django.contrib import admin

from apps.accounts.models import (
    Account,
    AccountAuditEvent,
    ExternalIdentity,
    PolicyAcceptance,
    Session,
)
from apps.common.admin import MaskedListAdmin, masked_account
from apps.common.masking import mask_email
from apps.sites import labels


@admin.display(description="Email", ordering="email")
def masked_email(account):
    return mask_email(account.email)


class ReadOnlyAdmin(MaskedListAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SanteSiteOwnerFilter(admin.SimpleListFilter):
    title = labels.SANTE_SITE_OWNER
    parameter_name = "sante_site_owner"

    def lookups(self, request, model_admin):
        return (("yes", "Yes"), ("no", "No"))

    def queryset(self, request, queryset):
        if self.value() in ("yes", "no"):
            return queryset.filter(sante_site_owner=self.value() == "yes")
        return queryset


@admin.register(Account)
class AccountAdmin(MaskedListAdmin):
    list_display = (
        masked_email, "public_id", "name", "email_verified_at", "created_at", "closed_at",
        "sante_site_owner",
    )
    list_filter = (SanteSiteOwnerFilter,)
    search_fields = ("email", "public_id", "name")
    readonly_fields = ("public_id", "referral_code", "created_at")

    def get_queryset(self, request):
        return labels.with_sante_site_owner(super().get_queryset(request))

    @admin.display(
        boolean=True, description=labels.SANTE_SITE_OWNER, ordering="sante_site_owner"
    )
    def sante_site_owner(self, account):
        """Derived from the account's sites on each read. Never stored, never authorizes."""
        return account.sante_site_owner


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
