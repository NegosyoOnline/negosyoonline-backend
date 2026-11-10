"""Read-only pages for operators. Changing a site or a name needs the operator audit trail,
which is a later task, so nothing here can be added, edited or deleted."""
from django.contrib import admin

from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Site)
class SiteAdmin(ReadOnlyAdmin):
    list_display = (
        "display_name", "public_id", "account", "template", "operator_hold", "created_at",
        "deleted_at",
    )
    list_filter = ("template", "operator_hold")
    search_fields = ("display_name", "public_id", "account__email", "subdomains__name")


@admin.register(Subdomain)
class SubdomainAdmin(ReadOnlyAdmin):
    list_display = ("name", "site", "state", "created_at", "retired_at", "released_at")
    list_filter = ("state",)
    search_fields = ("name",)


@admin.register(SiteBusinessProfile)
class SiteBusinessProfileAdmin(ReadOnlyAdmin):
    list_display = ("site", "business_name", "registered_name", "updated_at")
    search_fields = ("business_name", "registered_name", "site__display_name")


@admin.register(Onboarding)
class OnboardingAdmin(ReadOnlyAdmin):
    list_display = ("account", "first_site", "started_at", "minimum_completed_at")
    search_fields = ("account__email",)
