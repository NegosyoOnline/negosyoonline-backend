"""Member media library: promotion images and short videos for one Subscriber's members.

Port of TWCinc app/media_library/models.py (MediaAsset), trimmed: no Source (Santé only), no
funnel, no products link, no pro-only flag, no server-made renditions, no slug. Files are
public Vercel Blob keys (apps/catalog/media.py's store); the URLs are made on the way out. An
asset belongs to one Subscriber's site and is written by that owner in the dash. A row with no
published_at is a draft. Write this table through services.py.
"""
from django.db import models
from django.utils import timezone

from apps.common.ids import new_public_id

def new_asset_id():
    return new_public_id("mda")


class MediaAsset(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_asset_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.CASCADE, related_name="media_assets")
    title = models.CharField(max_length=160)
    asset_type = models.CharField(max_length=10)  # image | video (services.ASSET_TYPES)
    category = models.CharField(max_length=10, default="promo")  # promo | ad | page (services.CATEGORIES)
    product_category = models.CharField(max_length=20, blank=True, default="")  # catalog categories
    crop_ratio = models.CharField(max_length=4, default="4:5")  # 4:5 | 1:1 | 9:16 | 16:9
    file_key = models.CharField(max_length=200, blank=True, default="")  # empty until the file is stored
    thumb_key = models.CharField(max_length=200, blank=True, default="")
    poster_key = models.CharField(max_length=200, blank=True, default="")
    caption = models.TextField(blank=True)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    file_size = models.PositiveBigIntegerField(default=0)
    duration_seconds = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=1)
    published_at = models.DateTimeField(null=True, blank=True)  # empty = not published
    # shared: offered to other stores to copy (only the content source store may set it).
    # copied_from: public id of the source asset, a plain string, never a cross-store foreign key.
    shared = models.BooleanField(default=False)
    copied_from = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "pk"]
        indexes = [models.Index(fields=["site", "active", "published_at"], name="media_visible")]

    def __str__(self):
        return self.public_id


class MediaObject(models.Model):
    """One stored object of the media library: the byte ledger (docs/runbooks/storage.md).

    One row per key the library puts in the public store (an image or video file, a thumbnail, a
    poster). Rows are never deleted: only `removed_at` is written after the row is created, and
    only when the object is confirmed gone. The bytes held at an instant are the rows added at or
    before it and not removed by it (apps/media_library/ledger.py, the only code that writes this
    table). `asset` goes null when the asset row is deleted; the ledger row outlives it.
    """

    FILE, THUMB, POSTER = "file", "thumb", "poster"
    ROLES = ((FILE, "File"), (THUMB, "Thumbnail"), (POSTER, "Poster"))

    site = models.ForeignKey("sites.Site", on_delete=models.PROTECT, related_name="media_objects")
    asset = models.ForeignKey(MediaAsset, null=True, blank=True, on_delete=models.SET_NULL, related_name="ledger_rows")
    role = models.CharField(max_length=10, choices=ROLES)
    key = models.CharField(max_length=255, unique=True)
    content_type = models.CharField(max_length=100, blank=True, default="")
    bytes = models.BigIntegerField()
    added_at = models.DateTimeField(default=timezone.now)
    removed_at = models.DateTimeField(null=True, blank=True)  # empty while the object is stored

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(bytes__gte=0), name="media_object_bytes_gte_0"),
            models.CheckConstraint(
                condition=models.Q(removed_at__isnull=True) | models.Q(removed_at__gte=models.F("added_at")),
                name="media_object_removed_after_added",
            ),
        ]
        indexes = [
            models.Index(fields=["site", "added_at"], name="media_obj_site_added"),
            models.Index(fields=["site", "removed_at"], name="media_obj_site_removed"),
        ]

    def __str__(self):
        return f"{self.role} {self.key}"
