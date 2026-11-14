"""The shared catalog: the only data every Santé site reads. Write these tables through
services.py, never directly.

No model here has a foreign key to a Site or to an Account: a revision names its writer by
plain row numbers. Products are deactivated, never deleted, and revisions are append-only.
Money is whole centavos. The earnings rate is not money and stays a 4-decimal value.
"""
from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.db.models.functions import Upper
from django.utils import timezone

from apps.common.ids import new_public_id


def new_product_id():
    return new_public_id("prod")


class Catalog(models.Model):
    """One shared catalog per template key ("sante"). Created by the first write, never by a
    read. Its row lock serialises product creation."""

    template_key = models.CharField(max_length=30, unique=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    def __str__(self):
        return self.template_key


class CatalogProduct(models.Model):
    """A centrally controlled product. The members price is worked out (pricing.py), never
    stored."""

    class DeleteRefused(Exception):
        pass

    public_id = models.CharField(
        max_length=40, unique=True, default=new_product_id, editable=False
    )
    catalog = models.ForeignKey(Catalog, on_delete=models.PROTECT, related_name="products")
    code = models.CharField(max_length=64)  # upper-case, fixed after creation
    name = models.CharField(max_length=120)
    short_description = models.TextField(blank=True)  # at most 500, checked in services
    long_description = models.TextField(blank=True)  # at most 20000, checked in services
    # One of barley, drinks, essentials, beauty, or blank (checked in services, not a DB enum).
    category = models.CharField(max_length=20, blank=True, default="")
    # Storage key of the one product image in the public media store; blank means no image.
    # The public URL is worked out on output (media.py), never stored.
    image_key = models.CharField(max_length=200, blank=True, default="")
    # Extra photo slots 2 to 5 (slot 1 is image_key above), same kind of key; blank means empty.
    image_key_2 = models.CharField(max_length=200, blank=True, default="")
    image_key_3 = models.CharField(max_length=200, blank=True, default="")
    image_key_4 = models.CharField(max_length=200, blank=True, default="")
    image_key_5 = models.CharField(max_length=200, blank=True, default="")
    # Longer product details, at most 5000 characters each (checked in services). Field source:
    # TWCinc app/shops/models.py:178-183 (feature, specification, advantage, benefit).
    features = models.TextField(blank=True, default="")
    specifications = models.TextField(blank=True, default="")
    advantages = models.TextField(blank=True, default="")
    benefits = models.TextField(blank=True, default="")
    # Servings in one pack (1 to 999) and the product FAQ: a list of {"q", "a"}, at most 10,
    # checked in services.
    servings_per_pack = models.PositiveSmallIntegerField(null=True, blank=True)
    faq = models.JSONField(default=list, blank=True)
    # What's inside: a list of {"name", "line", "photo_slot"}, at most 8, checked in services.
    ingredients = models.JSONField(default=list, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    # Shipping inputs: null means not set.
    volume_slot = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    shipping_qty = models.PositiveIntegerField(null=True, blank=True)
    # Packed size in cm: all three or none, each above 0.
    length_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    width_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    srp_centavos = models.PositiveBigIntegerField()
    earnings_rate = models.DecimalField(max_digits=5, decimal_places=4)
    is_active = models.BooleanField(default=True)
    # The shop position set by the owner's Arrange (services.reorder_products). Products are
    # listed by (display_order, pk): every row starts at 0, so until the first reorder the order
    # is creation order. A new product is put after the last (services._next_display_order).
    # Not part of a revision: a reorder is a presentation change, not a product change.
    display_order = models.PositiveIntegerField(default=0)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pk"]
        verbose_name = "catalog product"
        constraints = [
            models.UniqueConstraint(fields=["catalog", "code"], name="catalog_product_code_uniq"),
            models.CheckConstraint(
                condition=Q(srp_centavos__gt=0), name="catalog_product_srp_positive"
            ),
            models.CheckConstraint(
                condition=Q(
                    earnings_rate__gte=Decimal("0.1000"), earnings_rate__lte=Decimal("0.5000")
                ),
                name="catalog_product_rate_bounds",
            ),
            models.CheckConstraint(
                condition=Q(weight_kg__gte=0), name="catalog_product_weight_nonneg"
            ),
            models.CheckConstraint(
                condition=Q(volume_slot__isnull=True) | Q(volume_slot__gt=0),
                name="catalog_product_slot_positive",
            ),
            models.CheckConstraint(
                condition=Q(shipping_qty__isnull=True) | Q(shipping_qty__gte=1),
                name="catalog_product_shipping_qty_min",
            ),
            models.CheckConstraint(
                condition=(
                    Q(length_cm__isnull=True, width_cm__isnull=True, height_cm__isnull=True)
                    | (
                        Q(length_cm__isnull=False, width_cm__isnull=False, height_cm__isnull=False)
                        & Q(length_cm__gt=0, width_cm__gt=0, height_cm__gt=0)
                    )
                ),
                name="catalog_product_dims_all_or_none_positive",
            ),
            models.CheckConstraint(condition=Q(revision__gte=1), name="catalog_product_revision_min"),
            models.CheckConstraint(condition=Q(code=Upper("code")), name="catalog_product_code_upper"),
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    def delete(self, *args, **kwargs):
        raise self.DeleteRefused("Catalog products are deactivated, never deleted.")


class CatalogProductRevision(models.Model):
    """A full copy of the product at each change. Append-only: this table is the record of
    who changed the catalog."""

    class ImmutableRevisionError(Exception):
        pass

    OWNER, SEED = "owner", "seed"
    SOURCE_CHOICES = [(SEED, "Seed"), (OWNER, "Owner")]

    product = models.ForeignKey(CatalogProduct, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=120)
    short_description = models.TextField(blank=True)
    long_description = models.TextField(blank=True)
    category = models.CharField(max_length=20, blank=True, default="")
    image_key = models.CharField(max_length=200, blank=True, default="")
    # Extra photo slots 2 to 5 (slot 1 is image_key above), same kind of key; blank means empty.
    image_key_2 = models.CharField(max_length=200, blank=True, default="")
    image_key_3 = models.CharField(max_length=200, blank=True, default="")
    image_key_4 = models.CharField(max_length=200, blank=True, default="")
    image_key_5 = models.CharField(max_length=200, blank=True, default="")
    # Longer product details, at most 5000 characters each (checked in services). Field source:
    # TWCinc app/shops/models.py:178-183 (feature, specification, advantage, benefit).
    features = models.TextField(blank=True, default="")
    specifications = models.TextField(blank=True, default="")
    advantages = models.TextField(blank=True, default="")
    benefits = models.TextField(blank=True, default="")
    # Servings in one pack (1 to 999) and the product FAQ: a list of {"q", "a"}, at most 10,
    # checked in services.
    servings_per_pack = models.PositiveSmallIntegerField(null=True, blank=True)
    faq = models.JSONField(default=list, blank=True)
    # What's inside: a list of {"name", "line", "photo_slot"}, at most 8, checked in services.
    ingredients = models.JSONField(default=list, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=2)
    volume_slot = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    shipping_qty = models.PositiveIntegerField(null=True, blank=True)
    length_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    width_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    srp_centavos = models.PositiveBigIntegerField()
    earnings_rate = models.DecimalField(max_digits=5, decimal_places=4)
    is_active = models.BooleanField()
    # Who wrote it. An owner write names the account and the site by row number; a seed
    # names the operator and the seed file (the catalog_seed_sante command).
    source = models.CharField(max_length=8, choices=SOURCE_CHOICES)
    changed_by_account_id = models.PositiveBigIntegerField(null=True, blank=True)
    changed_by_site_id = models.PositiveBigIntegerField(null=True, blank=True)
    changed_by_operator_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    changed_by_operator_username = models.CharField(max_length=150, blank=True)
    seed_file_sha256 = models.CharField(max_length=64, blank=True)
    changed_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["product_id", "-revision"]
        verbose_name = "catalog product revision"
        constraints = [
            models.UniqueConstraint(fields=["product", "revision"], name="catalog_revision_uniq"),
            models.CheckConstraint(
                condition=(
                    Q(
                        source="owner",
                        changed_by_account_id__isnull=False,
                        changed_by_site_id__isnull=False,
                    )
                    | (
                        Q(source="seed")
                        & ~Q(changed_by_operator_username="")
                        & ~Q(seed_file_sha256="")
                    )
                ),
                name="catalog_revision_writer",
            ),
        ]

    def __str__(self):
        return f"{self.code} revision {self.revision}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise self.ImmutableRevisionError("Catalog revisions are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise self.ImmutableRevisionError("Catalog revisions are append-only.")
