"""A record of each message sent. Bodies are never stored: they can hold sign-in codes."""
from django.db import models


class EmailMessage(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        SENT = "sent"
        FAILED = "failed"

    recipient = models.EmailField()
    category = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider_reference = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.category} to {self.recipient} ({self.status})"
