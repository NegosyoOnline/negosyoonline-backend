"""The shared catalog in the hq admin: two read-only lists. Only the catalog owner site
writes the catalog, through the API; naming that site stays with "Santé catalog owner".

Closed while the catalog is switched off: the code is released before its tables are
created, and a closed list never reads them.
"""
from django.contrib import admin

from apps.catalog import services
from apps.catalog.models import CatalogProduct, CatalogProductRevision


class ReadOnlySwitchedAdmin(admin.ModelAdmin):
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


@admin.register(CatalogProduct)
class CatalogProductAdmin(ReadOnlySwitchedAdmin):
    list_display = (
        "code", "name", "srp_centavos", "earnings_rate", "is_active", "revision", "updated_at",
    )
    search_fields = ("=code", "name", "=public_id")


@admin.register(CatalogProductRevision)
class CatalogProductRevisionAdmin(ReadOnlySwitchedAdmin):
    list_display = (
        "product", "revision", "source", "changed_by_site_id", "changed_by_operator_username",
        "changed_at",
    )
    list_select_related = ("product",)
