"""What only a Santé site has. Write these tables through services.py, never directly."""
from django.db import models
from django.utils import timezone


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



class ImmutableRecordError(Exception):
    """The row is kept for the record and cannot be changed or deleted."""


class MembershipAgreement(models.Model):
    """The one row (pk 1) of the central Membership Agreement: moving active_version is the
    whole distribution, every Santé site reads this one row. Only agreement.py moves it."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    active_version = models.ForeignKey(
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


class AgreementPublishAuthorization(models.Model):
    """Santé's written authorization, recorded and verified by an operator (Coach Ed
    2026-10-31). Publishing needs one verified, unrevoked row. Rows change only through
    agreement.py, with a filtered update; they are never deleted."""

    agreement = models.ForeignKey(
        MembershipAgreement, on_delete=models.PROTECT, related_name="authorizations"
    )
    document_date = models.DateField()
    note = models.CharField(max_length=255)
    covers_pdf_hosting = models.BooleanField(default=False)
    covers_branding = models.BooleanField(default=False)
    covers_online_acceptance = models.BooleanField(default=False)
    evidence_key = models.CharField(max_length=255, unique=True, editable=False)
    evidence_sha256 = models.CharField(max_length=64)
    evidence_size = models.PositiveIntegerField()
    recorded_by = models.ForeignKey(
        "operators.User", on_delete=models.PROTECT, related_name="+"
    )
    recorded_at = models.DateTimeField(default=timezone.now, editable=False)
    verified_by = models.ForeignKey(
        "operators.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        "operators.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-recorded_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(verified_at__isnull=True) | models.Q(
                    covers_pdf_hosting=True, covers_branding=True, covers_online_acceptance=True
                ),
                name="sante_authorization_verified_covers_all",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ImmutableRecordError("Authorization rows change only through the agreement service.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Authorization rows are never deleted.")


class MembershipAgreementVersion(models.Model):
    """One uploaded PDF. A published row is immutable and its file key is never reused."""

    DRAFT, PUBLISHED = "draft", "published"
    STATES = [(DRAFT, "Draft"), (PUBLISHED, "Published")]

    agreement = models.ForeignKey(
        MembershipAgreement, on_delete=models.PROTECT, related_name="versions"
    )
    master_site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="+")
    title = models.CharField(max_length=160)
    version_label = models.CharField(max_length=32)
    effective_date = models.DateField()  # document metadata only (Coach Ed 2026-10-31)
    storage_key = models.CharField(max_length=255, unique=True, editable=False)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    state = models.CharField(max_length=10, choices=STATES, default=DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["agreement", "version_label"], name="sante_agreement_label_uniq"
            ),
        ]

    def _is_published_row(self):
        return (
            self.pk is not None and not self._state.adding
            and type(self).objects.filter(pk=self.pk, state=self.PUBLISHED).exists()
        )

    def save(self, *args, **kwargs):
        if self._is_published_row():
            raise ImmutableRecordError("A published agreement version cannot be changed.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._is_published_row():
            raise ImmutableRecordError("A published agreement version cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"Membership Agreement {self.version_label}"
