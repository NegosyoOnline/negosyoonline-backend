from django.conf import settings
from django.contrib import admin

from apps.accounts.models import (
    Account,
    AccountAuditEvent,
    AccountMobile,
    ExternalIdentity,
    MobileOtpChallenge,
    PolicyAcceptance,
    Session,
)
from apps.common import masking
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


@admin.display(description="Number", ordering="number")
def masked_number(row):
    return masking.mask_mobile(row.number)


class MobileAdmin(ReadOnlyAdmin):
    """A mobile verification list. Closed while SMS_GATEWAY_ENABLED is off: the code is
    released before its tables are created, and a closed list never reads them."""

    list_select_related = ("account",)

    def has_module_permission(self, request):
        return settings.SMS_GATEWAY_ENABLED and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return settings.SMS_GATEWAY_ENABLED and super().has_view_permission(request, obj)


@admin.register(AccountMobile)
class AccountMobileAdmin(MobileAdmin):
    list_display = (masked_account, masked_number, "verified_at", "updated_at")
    search_fields = ("=number",)  # a full number typed by the admin, never a fragment
    # The detail page lists its fields by name, so the full number and email are never there.
    fields = (masked_account, masked_number, "verified_at", "created_at", "updated_at")
    readonly_fields = (masked_account, masked_number)


@admin.register(MobileOtpChallenge)
class MobileOtpChallengeAdmin(MobileAdmin):
    list_display = (
        masked_account, masked_number, "attempts", "created_at", "expires_at", "used_at",
        "superseded_at",
    )
    # The code hash is not one of them.
    fields = (
        masked_account, masked_number, "attempts", "sms_public_id", "created_at", "expires_at",
        "used_at", "superseded_at",
    )
    readonly_fields = (masked_account, masked_number)
