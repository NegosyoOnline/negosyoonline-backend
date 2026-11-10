from django.contrib import admin

from apps.common.admin import MaskedListAdmin
from apps.common.masking import mask_email
from apps.notifications.models import EmailMessage


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
