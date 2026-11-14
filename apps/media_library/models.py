"""Member media library: promotion images and short videos for a site template's members.

Port of TWCinc app/media_library/models.py (MediaAsset), trimmed: no Source (Santé only), no
funnel, no products link, no pro-only flag, no server-made renditions, no slug. Files are
public Vercel Blob keys (apps/catalog/media.py's store); the URLs are made on the way out. Content
belongs to a site template ("sante"), not to one Subscriber, and is written in HQ only. A row with
no published_at is a draft. Write this table through services.py.
"""
from django.db import models
from django.utils import timezone

from apps.common.ids import new_public_id

SANTE = "sante"


def new_asset_id():
    return new_public_id("mda")


class MediaAsset(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_asset_id, editable=False)
    template_key = models.CharField(max_length=20, default=SANTE)
    title = models.CharField(max_length=160)
    asset_type = models.CharField(max_length=10)  # image | video (services.ASSET_TYPES)
    category = models.CharField(max_length=10, default="promo")  # promo | ad | page (services.CATEGORIES)
    product_category = models.CharField(max_length=20, blank=True, default="")  # catalog categories
    crop_ratio = models.CharField(max_length=4, default="4:5")  # 4:5 | 1:1 | 9:16 | 16:9
    file_key = models.CharField(max_length=200)
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
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "pk"]
        indexes = [models.Index(fields=["template_key", "active", "published_at"], name="media_visible")]

    def __str__(self):
        return self.public_id
