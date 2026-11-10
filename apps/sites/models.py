"""Sites and their web addresses. Every site-owned row in later slices points at Site."""
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower

from apps.common.ids import new_public_id
from apps.sites import templates


def new_site_id():
    return new_public_id("site")


class Site(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_site_id, editable=False)
    account = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="sites")
    template = models.CharField(max_length=30, choices=templates.CHOICES)
    display_name = models.CharField(max_length=80)
    contact_email = models.EmailField(blank=True)
    contact_mobile = models.CharField(max_length=13, blank=True)  # +639XXXXXXXXX
    accent_color = models.CharField(max_length=7, blank=True)  # #RRGGBB
    instagram_url = models.URLField(max_length=200, blank=True)
    tiktok_url = models.URLField(max_length=200, blank=True)
    operator_hold = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.display_name


class Subdomain(models.Model):
    """The ledger of every name ever issued. A name is taken until an operator releases it."""

    class State(models.TextChoices):
        ACTIVE = "active"
        RETIRED = "retired"
        OPERATOR_RESERVED = "operator_reserved"

    name = models.CharField(max_length=30, unique=True)
    site = models.ForeignKey(
        Site, null=True, blank=True, on_delete=models.PROTECT, related_name="subdomains"
    )
    state = models.CharField(max_length=20, choices=State.choices, default=State.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(name=Lower(F("name"))), name="sites_subdomain_lowercase"
            ),
            models.UniqueConstraint(
                fields=["site"], condition=Q(state="active"), name="sites_one_active_subdomain"
            ),
        ]

    def __str__(self):
        return self.name


class SiteBusinessProfile(models.Model):
    site = models.OneToOneField(Site, on_delete=models.PROTECT, related_name="business_profile")
    business_name = models.CharField(max_length=160, blank=True)
    registered_name = models.CharField(max_length=120, blank=True)
    tin = models.CharField(max_length=17, blank=True)
    dti_business_number = models.CharField(max_length=15, blank=True)
    bir_rsn = models.CharField(max_length=19, blank=True)
    business_address = models.CharField(max_length=255, blank=True)
    complaints_contact_name = models.CharField(max_length=120, blank=True)
    privacy_contact_email = models.EmailField(blank=True)
    shipping_processing_time = models.CharField(
        max_length=60, blank=True, default="1-2 business days"
    )
    shipping_metro_manila_estimate = models.CharField(
        max_length=60, blank=True, default="1-2 business days"
    )
    shipping_luzon_estimate = models.CharField(
        max_length=60, blank=True, default="2-5 business days"
    )
    shipping_visayas_estimate = models.CharField(
        max_length=60, blank=True, default="5-7 business days"
    )
    shipping_mindanao_estimate = models.CharField(
        max_length=60, blank=True, default="5-7 business days"
    )
    shipping_other_areas_estimate = models.CharField(max_length=60, blank=True)
    shipping_estimates_confirmed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Business profile of {self.site}"


class Onboarding(models.Model):
    """What onboarding persists per account. The state itself is derived; see onboarding.py."""

    account = models.OneToOneField(
        "accounts.Account", on_delete=models.PROTECT, related_name="onboarding"
    )
    first_site = models.OneToOneField(
        Site, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    started_at = models.DateTimeField(null=True, blank=True)
    minimum_completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Onboarding of {self.account}"
