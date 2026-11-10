from django.contrib import admin

from apps.notifications.models import EmailMessage


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ("recipient", "category", "status", "created_at", "sent_at")
    list_filter = ("category", "status")
    search_fields = ("recipient",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
