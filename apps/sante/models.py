"""What only a Santé site has. Write these tables through services.py, never directly."""
from django.db import models


class SanteSiteSettings(models.Model):
    """The Santé settings of one site. The sponsor ID is the Subscriber's own: it starts blank
    and is never filled from anywhere else."""

    site = models.OneToOneField(
        "sites.Site", on_delete=models.PROTECT, related_name="sante_settings"
    )
    sponsor_id = models.CharField(max_length=32, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Santé site settings"
        verbose_name_plural = "Santé site settings"

    def __str__(self):
        return f"Santé settings of {self.site}"


class SanteCatalogOwner(models.Model):
    """The one row (pk 1) that names the Santé catalog owner site. Only an operator sets it."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    site = models.OneToOneField(
        "sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Santé catalog owner"
        verbose_name_plural = "Santé catalog owner"

    def __str__(self):
        return "Santé catalog owner"
