"""Member announcements and promos (written by the Subscriber) and the member's bell.

Every function takes the site first and filters by it: a notice or a notification of another
site is never found. Write the two tables through here. A notice picture is stored in the
public media store (apps/catalog/media.py) in its own folder.

TWCinc source for the shape: app/notifications/models.py (Notification, read_at). The notice
list is new: TWCinc has one global announcement list, here each site has its own.
"""
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.catalog import media
from apps.catalog.services import IMAGE_MESSAGES
from apps.common import private_files
from apps.common.errors import ApiError

from .models import Member, MemberNotice, MemberNotification

IMAGE_FOLDER = "member-notices"
KINDS = ("announcement", "promo")
TITLE_MAX, BODY_MAX = 120, 5000
NOTIFICATION_TITLE_MAX = 160
DASHBOARD_PATH = "/dashboard/"
BATCH = 500
INVALID_MSG = "Some values are missing or invalid."
IMAGE_NOT_CONFIGURED_MSG = "Pictures cannot be stored yet: the image store is not configured."
IMAGE_NOT_SAVED_MSG = "The picture could not be stored. Try again."
FIELD_MESSAGES = {
    "kind": "Choose announcement or promo.",
    "title": f"Use 1 to {TITLE_MAX} characters.",
    "body": f"Use up to {BODY_MAX} characters.",
    "starts_at": "Enter a date and time with its time zone.",
    "ends_at": "Enter a date and time with its time zone, after the start.",
}


def _not_found():
    return ApiError("not_found", "Not found.", status=404, event="object_not_found", reason="missing")


def _invalid(fields):
    return ApiError("invalid_request", INVALID_MSG, fields={k: [v] for k, v in fields.items()})


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def clean(data, existing=None):
    """The checked values of a create or update body, or ApiError 400 with every field wrong."""
    out, errors = {}, {}
    if "kind" in data or existing is None:
        if data.get("kind") in KINDS:
            out["kind"] = data["kind"]
        else:
            errors["kind"] = FIELD_MESSAGES["kind"]
    if "title" in data or existing is None:
        title = data.get("title")
        if isinstance(title, str) and 1 <= len(title.strip()) <= TITLE_MAX:
            out["title"] = title.strip()
        else:
            errors["title"] = FIELD_MESSAGES["title"]
    if "body" in data:
        body = data["body"]
        if isinstance(body, str) and len(body) <= BODY_MAX:
            out["body"] = body.strip()
        else:
            errors["body"] = FIELD_MESSAGES["body"]
    if "active" in data:
        if isinstance(data["active"], bool):
            out["active"] = data["active"]
        else:
            errors["active"] = "Send true or false."
    for name in ("starts_at", "ends_at"):
        if name in data:
            value = data[name]
            if value is None or _aware(value):
                out[name] = value
            else:
                errors[name] = FIELD_MESSAGES[name]
    if not errors:
        start = out["starts_at"] if "starts_at" in out else (existing.starts_at if existing else None)
        end = out["ends_at"] if "ends_at" in out else (existing.ends_at if existing else None)
        if start is not None and end is not None and end <= start:
            errors["ends_at"] = FIELD_MESSAGES["ends_at"]
    if errors:
        raise _invalid(errors)
    return out


def notice_out(notice):
    """What the Subscriber sees of their notice."""
    return {
        "id": notice.public_id, "kind": notice.kind, "title": notice.title, "body": notice.body,
        "image_url": media.url_for(notice.image_key), "starts_at": notice.starts_at,
        "ends_at": notice.ends_at, "active": notice.active, "created_at": notice.created_at,
        "updated_at": notice.updated_at,
    }


def member_notice_out(notice):
    """What a member sees: no flags and no row number."""
    return {
        "id": notice.public_id, "kind": notice.kind, "title": notice.title, "body": notice.body,
        "image_url": media.url_for(notice.image_key), "starts_at": notice.starts_at,
        "ends_at": notice.ends_at,
    }


def visible_now(now):
    return Q(active=True) & (Q(starts_at__isnull=True) | Q(starts_at__lte=now)) & (
        Q(ends_at__isnull=True) | Q(ends_at__gt=now)
    )


def visible_to_members(site, now=None):
    """The notices a member of this site sees: active and inside their window, newest first."""
    return MemberNotice.objects.filter(visible_now(now or timezone.now()), site=site)


def list_for_owner(site):
    return MemberNotice.objects.filter(site=site)


def get_for_owner(site, notice_id):
    notice = MemberNotice.objects.filter(site=site, public_id=notice_id).first()
    if notice is None:
        raise _not_found()
    return notice


def notify_members(site, kind, title, url=DASHBOARD_PATH):
    """One bell entry for every member of the site. Returns how many were made."""
    now = timezone.now()
    rows = [
        MemberNotification(site=site, member_id=pk, kind=kind, title=title[:NOTIFICATION_TITLE_MAX],
                           url=url, created_at=now)
        for pk in Member.objects.filter(site=site).values_list("pk", flat=True)
    ]
    return len(MemberNotification.objects.bulk_create(rows, batch_size=BATCH))


@transaction.atomic
def create_notice(site, data):
    """A new notice. One that is already visible puts an entry in every member's bell; one that
    starts later does not (nothing runs at the start time)."""
    notice = MemberNotice.objects.create(site=site, **clean(data))
    if visible_to_members(site).filter(pk=notice.pk).exists():
        notify_members(site, "notice", notice.title)
    return notice


@transaction.atomic
def update_notice(site, notice_id, data):
    notice = MemberNotice.objects.select_for_update().filter(site=site, public_id=notice_id).first()
    if notice is None:
        raise _not_found()
    for name, value in clean(data, existing=notice).items():
        setattr(notice, name, value)
    notice.save()
    return notice


def delete_notice(site, notice_id):
    with transaction.atomic():
        notice = MemberNotice.objects.select_for_update().filter(site=site, public_id=notice_id).first()
        if notice is None:
            raise _not_found()
        old_key = notice.image_key
        notice.delete()
        transaction.on_commit(lambda: media.discard(old_key))


def set_image(site, notice_id, upload):
    """Store `upload` (JPEG, PNG or WebP, at most 2 MB) as the notice's picture, replacing any. A
    write that is refused stores nothing; the old file goes once the change is committed."""
    get_for_owner(site, notice_id)
    if not media.configured():
        raise ApiError("image_store_not_configured", IMAGE_NOT_CONFIGURED_MSG, status=503)
    try:
        content, content_type = private_files.check(
            upload, allowed=private_files.IMAGE_TYPES, max_bytes=media.MAX_BYTES
        )
    except private_files.FileRefused as refused:
        raise _invalid({"file": IMAGE_MESSAGES[refused.reason]}) from None
    try:
        key = media.save(content, content_type, folder=IMAGE_FOLDER)
    except media.StoreError:
        raise ApiError("image_not_saved", IMAGE_NOT_SAVED_MSG, status=502) from None
    try:
        with transaction.atomic():
            notice = MemberNotice.objects.select_for_update().filter(site=site, public_id=notice_id).first()
            if notice is None:
                raise _not_found()
            old_key = notice.image_key
            notice.image_key = key
            notice.save(update_fields=["image_key", "updated_at"])
            transaction.on_commit(lambda: media.discard(old_key))
    except BaseException:
        media.discard(key)
        raise
    return notice


@transaction.atomic
def clear_image(site, notice_id):
    notice = MemberNotice.objects.select_for_update().filter(site=site, public_id=notice_id).first()
    if notice is None:
        raise _not_found()
    if notice.image_key:
        old_key, notice.image_key = notice.image_key, ""
        notice.save(update_fields=["image_key", "updated_at"])
        transaction.on_commit(lambda: media.discard(old_key))
    return notice


# the bell -------------------------------------------------------------------

def notification_out(row):
    return {
        "id": row.public_id, "kind": row.kind, "title": row.title, "url": row.url,
        "created_at": row.created_at, "read_at": row.read_at,
    }


def notifications_of(member):
    """The member's own entries of their own site. Both are filtered, so a foreign row never shows."""
    return MemberNotification.objects.filter(site_id=member.site_id, member=member)


def mark_read(member, notification_id):
    row = notifications_of(member).filter(public_id=notification_id).first()
    if row is None:
        raise _not_found()
    if row.read_at is None:
        notifications_of(member).filter(pk=row.pk, read_at__isnull=True).update(read_at=timezone.now())
        row.refresh_from_db()
    return row


def mark_all_read(member):
    return notifications_of(member).filter(read_at__isnull=True).update(read_at=timezone.now())
