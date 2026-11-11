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


class MembershipAgreement(models.Model):
    """The one row (pk 1) of the central Membership Agreement: it points at the version every
    Santé site shows its buyers. Only the sante_agreement_publish command moves the pointer."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    active_version = models.OneToOneField(
        "sante.MembershipAgreementVersion", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Membership Agreement"
        verbose_name_plural = "Membership Agreement"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="sante_agreement_singleton")
        ]

    def __str__(self):
        return "Membership Agreement"


class MembershipAgreementVersion(models.Model):
    """One uploaded PDF of the agreement. A draft can be published; a published version is kept
    for the record and stays readable after a newer one is published."""

    DRAFT, PUBLISHED = "draft", "published"
    STATES = [(DRAFT, "Draft"), (PUBLISHED, "Published")]

    agreement = models.ForeignKey(
        MembershipAgreement, on_delete=models.PROTECT, related_name="versions"
    )
    label = models.CharField(max_length=32)
    effective_date = models.DateField()
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    file_key = models.CharField(max_length=200, editable=False)
    state = models.CharField(max_length=10, choices=STATES, default=DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-pk"]
        constraints = [
            models.UniqueConstraint(fields=["agreement", "label"], name="sante_agreement_label"),
        ]

    def __str__(self):
        return f"Membership Agreement {self.label}"
