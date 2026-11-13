from django.conf import settings
from django.contrib import admin

from apps.common.admin import MaskedListAdmin
from apps.common.masking import mask_email, mask_mobile
from apps.notifications.models import EmailMessage, SmsDevice, SmsMessage, SmsPolicy
from apps.notifications import services


@admin.display(description="Recipient", ordering="recipient")
def masked_recipient(message):
    return mask_email(message.recipient)


@admin.register(EmailMessage)
class EmailMessageAdmin(MaskedListAdmin):
    list_display = (masked_recipient, "category", "status", "created_at", "sent_at")
    list_filter = ("category", "status")
    search_fields = ("recipient",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class GatewayAdmin(admin.ModelAdmin):
    """An SMS list. Closed while SMS_GATEWAY_ENABLED is off: the code is released before its
    tables are created, and a closed list never reads them. Nothing can be added or deleted."""

    def has_module_permission(self, request):
        return settings.SMS_GATEWAY_ENABLED and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return settings.SMS_GATEWAY_ENABLED and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.display(description="Number", ordering="recipient")
def masked_number(message):
    return mask_mobile(message.recipient)


@admin.register(SmsMessage)
class SmsMessageAdmin(GatewayAdmin, MaskedListAdmin):
    list_display = (
        masked_number, "category", "purpose", "status", "reason", "emailed", "created_at",
        "sent_at",
    )
    list_filter = ("category", "status", "reason")
    search_fields = ("=recipient",)  # a full number typed by the admin, never a fragment
    list_select_related = ("site", "device")
    # The detail page lists its fields by name, so the full number is never one of them.
    fields = (
        "public_id", masked_number, "category", "purpose", "priority", "status", "reason",
        "emailed", "site", "device", "created_at", "dispatched_at", "sent_at", "expires_at",
    )
    readonly_fields = (masked_number,)


@admin.register(SmsDevice)
class SmsDeviceAdmin(GatewayAdmin):
    """Phones are managed only from the terminal. The token's hash is never shown."""

    list_display = (
        "name", "is_active", "forwards_incoming", "last_contact_at", "app_version",
        "battery_percent",
    )
    fields = (
        "name", "is_active", "forwards_incoming", "last_contact_at", "app_version",
        "battery_percent", "is_charging", "created_at", "rotated_at",
    )


POLICY_FIELDS = services.POLICY_FIELDS


@admin.register(SmsPolicy)
class SmsPolicyAdmin(GatewayAdmin):
    """The one row of SMS settings. It can be changed, and every change is audited."""

    list_display = ("__str__", *POLICY_FIELDS, "updated_at")
    fields = POLICY_FIELDS

    def has_change_permission(self, request, obj=None):
        return settings.SMS_GATEWAY_ENABLED and admin.ModelAdmin.has_change_permission(
            self, request, obj
        )

    def changelist_view(self, request, extra_context=None):
        if self.has_view_permission(request):
            SmsPolicy.objects.get_or_create(pk=1)  # the row to change exists from the first visit
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        was = SmsPolicy.objects.get(pk=obj.pk)
        super().save_model(request, obj, form, change)
        services.record_policy_change(request.user, was, obj)
