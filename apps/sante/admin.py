"""The Santé lists in the hq admin. Closed while SANTE_SETTINGS_ENABLED is off: the code is
released before its tables are created, and a closed list never reads them.

Site settings are read-only. The catalog owner is the one operator action: it goes through
services.set_catalog_owner, which writes the operator audit entry.
"""
from django import forms
from django.contrib import admin

from apps.sante import services
from apps.sante.models import SanteCatalogOwner, SanteSiteSettings
from apps.sites import templates
from apps.sites.models import Site


class SwitchedAdmin(admin.ModelAdmin):
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


@admin.register(SanteSiteSettings)
class SanteSiteSettingsAdmin(SwitchedAdmin):
    list_display = ("site", "sponsor_id", "updated_at")
    list_select_related = ("site",)
    search_fields = ("site__display_name", "site__public_id", "=sponsor_id")


class SiteChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, site):
        return f"{site.display_name} ({site.public_id})"


class CatalogOwnerForm(forms.ModelForm):
    site = SiteChoiceField(
        queryset=Site.objects.none(), required=False,
        help_text="Only live Santé sites are listed. Leave empty to clear the catalog owner.",
    )

    class Meta:
        model = SanteCatalogOwner
        fields = ["site"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["site"].queryset = Site.objects.filter(
            template=templates.SANTE, deleted_at__isnull=True
        ).order_by("display_name", "pk")


@admin.register(SanteCatalogOwner)
class SanteCatalogOwnerAdmin(SwitchedAdmin):
    form = CatalogOwnerForm
    list_display = ("__str__", "site", "updated_at")
    list_select_related = ("site",)

    def has_add_permission(self, request):
        return (
            services.enabled()
            and admin.ModelAdmin.has_add_permission(self, request)
            and not SanteCatalogOwner.objects.exists()
        )

    def has_change_permission(self, request, obj=None):
        return services.enabled() and admin.ModelAdmin.has_change_permission(self, request, obj)

    def save_model(self, request, obj, form, change):
        services.set_catalog_owner(form.cleaned_data["site"], operator=request.user)
