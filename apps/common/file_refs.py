"""Delete guard for public media keys.

A stored file may be removed only when no row in any app still names its key. Every discard of
a public media key goes through discard_if_unreferenced, run after the row change has committed.
A copied item always has keys of its own (apps/sante/content_copy.py), so the guard is a second
line of defence: a copy's delete never removes the source's file, and the other way round.

Models are imported lazily so this module can be used from any app without a circular import.
When a model gains a column that holds a public media key, add it to FIELDS and to the test.
"""

FIELDS = (
    ("training", "Course", ("image_key",)),
    ("training", "Lesson", ("image_key",)),
    ("training", "LiveTraining", ("thumbnail_key",)),
    ("media_library", "MediaAsset", ("file_key", "thumb_key", "poster_key")),
    ("members", "MemberNotice", ("image_key",)),
    (
        "catalog", "CatalogProduct",
        ("image_key", "image_key_2", "image_key_3", "image_key_4", "image_key_5",
         "features_image_key", "advantages_image_key", "benefits_image_key"),
    ),
    (
        "catalog", "CatalogProductRevision",
        ("image_key", "image_key_2", "image_key_3", "image_key_4", "image_key_5",
         "features_image_key", "advantages_image_key", "benefits_image_key"),
    ),
)


def is_referenced(key):
    """True when any row still names this key."""
    from django.apps import apps
    from django.db.models import Q

    if not key:
        return False
    for app_label, model_name, fields in FIELDS:
        model = apps.get_model(app_label, model_name)
        query = Q()
        for field in fields:
            query |= Q(**{field: key})
        if model._base_manager.filter(query).exists():
            return True
    return False


def discard_if_unreferenced(key):
    """Remove the stored file unless a row still uses it. Never raises."""
    if not key:
        return
    from apps.catalog import media

    try:
        if is_referenced(key):
            return
    except Exception:  # noqa: BLE001 - when unsure, keep the file
        return
    media.discard(key)
