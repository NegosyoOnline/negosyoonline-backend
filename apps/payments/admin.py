"""The payment lists in the hq admin. Closed while PAYMENT_METHODS_ENABLED is off: the code is
released before its tables are created, and a closed list never reads them.

Both are read-only. An account number shows its last four digits only.
"""
from django.contrib import admin

from apps.payments import services
from apps.payments.models import SitePaymentAccount, SitePaymentSettings


class SwitchedReadOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return services.enabled() and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return services.enabled() and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SitePaymentAccount)
class SitePaymentAccountAdmin(SwitchedReadOnlyAdmin):
    list_display = ("site", "kind", "provider_name", "account_type", "number_ending", "removed_at")
    list_filter = ("kind",)
    list_select_related = ("site",)
    search_fields = ("site__display_name", "site__public_id", "=public_id")
    fields = (
        "public_id", "site", "kind", "provider_name", "account_type", "number_ending",
        "created_at", "updated_at", "removed_at",
    )
    readonly_fields = ("number_ending",)

    @admin.display(description="Account number")
    def number_ending(self, row):
        return f"ending {row.account_number[-4:]}" if row.account_number else ""


@admin.register(SitePaymentSettings)
class SitePaymentSettingsAdmin(SwitchedReadOnlyAdmin):
    list_display = ("site", "direct_enabled", "updated_at")
    list_select_related = ("site",)
    search_fields = ("site__display_name", "site__public_id")
