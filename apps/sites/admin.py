"""Read-only pages for operators. Changing a site or a name needs the operator audit trail,
which is a later task, so nothing here can be added, edited or deleted."""
from django.contrib import admin

from apps.common.admin import masked_account
from apps.sites import labels
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
        "display_name", "public_id", masked_account, "template", "operator_hold", "created_at",
        "deleted_at", "counts_for_sante_site_owner",
    )
    list_filter = ("template", "operator_hold")
    list_select_related = ("account",)  # the masked column reads the account's email
    search_fields = ("display_name", "public_id", "account__email", "subdomains__name")

    def get_queryset(self, request):
        return labels.with_counts_for_sante_site_owner(super().get_queryset(request))

    @admin.display(
        boolean=True, description=f"Counts for {labels.SANTE_SITE_OWNER}",
        ordering="counts_for_sante_site_owner",
    )
    def counts_for_sante_site_owner(self, site):
        """Derived on each read; shows why an account has or lacks the label."""
        return site.counts_for_sante_site_owner


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
    list_display = (masked_account, "first_site", "started_at", "minimum_completed_at")
    list_select_related = ("account", "first_site")
    search_fields = ("account__email",)
