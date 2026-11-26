"""One-time copy of a store's Media Library videos from the public store into its own Bunny library.

    manage.py media_video_to_bunny --site site_...                dry run: counts only, writes nothing, no call
    manage.py media_video_to_bunny --site site_... --apply        copies (needs MEDIA_VIDEO_BUNNY_ENABLED)
    manage.py media_video_to_bunny --site site_... --release-old  dry run of step 2: counts the old copies
    manage.py media_video_to_bunny --site site_... --release-old --apply   removes them from the store

Step 1 (--apply): each video asset still on the public store is downloaded, uploaded to the store's
own Stream library, and waited for (--wait seconds, default 300). Only a video Bunny reports as
finished and with a size is switched over (the asset then names it and the ledger records it); one
that is not is removed from Bunny again and counted. The Blob copy is KEPT: its file stays in the
store and its ledger row stays live. Shared assets are left alone (a Bunny video cannot be shared yet).
Run it again to retry; an asset already switched is not touched.

Step 2 (--release-old --apply), only after the copied videos have been checked: removes the kept Blob
copies of assets that now play from Bunny and closes their ledger rows.

Output is counts only: no key, URL or title. Run by Coach Ed's staff on a server he has approved; the
app never runs it.
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.media_library import ledger, store
from apps.media_library.models import MediaAsset, MediaObject
from apps.training import stream
from apps.training import video as training_video
from apps.training.management.commands.training_video_audit import find_site
from apps.training.models import StreamLibrary


POLL_SECONDS = 5


def blob_videos(site):
    return [
        a for a in MediaAsset.objects.filter(site=site, asset_type="video").exclude(file_key="").order_by("pk")
        if not store.bunny_ref(a.file_key)
    ]


def old_copies(site):
    """The live ledger rows of file objects that assets which now play from Bunny used to name."""
    moved = MediaAsset.objects.filter(site=site, file_key__startswith=store.BUNNY_PREFIX)
    return list(
        MediaObject.objects.filter(site=site, asset__in=moved, role="file", removed_at__isnull=True)
        .exclude(key__startswith=store.BUNNY_PREFIX).order_by("pk")
    )


class Command(BaseCommand):
    help = "Copy a store's Media Library videos into its own Bunny library (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--site", required=True, help="the store's public id or address")
        parser.add_argument("--apply", action="store_true", help="do it (default: print counts only)")
        parser.add_argument("--release-old", action="store_true", help="step 2: remove the kept public-store copies")
        parser.add_argument("--wait", type=int, default=300, help="seconds to wait for Bunny to finish a video")

    def handle(self, *args, site, apply=False, release_old=False, wait=300, **options):
        site = find_site(site)
        mode = "APPLY" if apply else "DRY RUN"
        if release_old:
            return self._release(site, apply, mode)
        row = StreamLibrary.objects.filter(site=site).first()
        todo = blob_videos(site)
        shared = [a for a in todo if a.shared]
        todo = [a for a in todo if not a.shared]
        total = MediaAsset.objects.filter(site=site, asset_type="video").exclude(file_key="").count()
        self.stdout.write(
            f"{mode}: videos {total}, on the public store {len(todo) + len(shared)}, "
            f"already in the library {total - len(todo) - len(shared)}, shared (left alone) {len(shared)}, "
            f"to copy {len(todo)}"
        )
        if not apply:
            return
        if not settings.MEDIA_VIDEO_BUNNY_ENABLED:
            raise CommandError("MEDIA_VIDEO_BUNNY_ENABLED is off.")
        try:
            library = stream.library_from_row(row)
        except stream.NotConfigured:
            raise CommandError("This store has no usable Stream library (library row and key variable).") from None
        copied = not_ready = failed = 0
        for asset in todo:
            outcome = self._copy(library, asset, wait)
            copied += outcome == "copied"
            not_ready += outcome == "not_ready"
            failed += outcome == "failed"
        self.stdout.write(f"copied {copied}, not ready {not_ready}, failed {failed}")

    def _copy(self, library, asset, wait):
        guid, source = None, None
        try:
            source, size = stream.download_url(store.url_for(asset.file_key))
            guid = stream.create_video(library, asset.title)
            stream.upload_bytes(library, guid, source, size)
            found = self._wait_ready(library, guid, wait)
        except (stream.StreamError, OSError):
            found = None
        finally:
            if source is not None:
                source.close()
        if found is None or stream.status_name(found["status"]) != training_video.READY or found["storage_size"] <= 0:
            outcome = "failed" if found is None else "not_ready"
            if guid:
                try:
                    stream.delete_video(library, guid)
                except stream.StreamError:
                    pass
            return outcome
        key = store.bunny_key(library.id, guid)
        with transaction.atomic():
            fresh = MediaAsset.objects.select_for_update().get(pk=asset.pk)
            if fresh.file_key != asset.file_key or fresh.shared:  # changed meanwhile: leave it
                return "failed"
            fresh.file_key, fresh.file_size = key, found["storage_size"]
            update = ["file_key", "file_size", "updated_at"]
            if not fresh.duration_seconds and found["length"] > 0:
                fresh.duration_seconds = min(found["length"], 86400)
                update.append("duration_seconds")
            fresh.save(update_fields=update)
            ledger.record(fresh.site, asset=fresh, role="file", key=key, content_type=store.VIDEO_TYPE,
                          size=found["storage_size"])
        return "copied"

    def _wait_ready(self, library, guid, wait):
        deadline = time.monotonic() + max(wait, 0)
        while True:
            found = stream.get_video(library, guid)
            status = stream.status_name(found["status"])
            if status in (training_video.READY, training_video.FAILED) or time.monotonic() >= deadline:
                return found
            time.sleep(POLL_SECONDS)

    def _release(self, site, apply, mode):
        rows = old_copies(site)
        self.stdout.write(f"{mode}: old public-store copies kept after a copy {len(rows)}")
        if not apply:
            return
        released = sum(bool(ledger.release(row.key)) for row in rows)
        self.stdout.write(f"released {released}, still kept {len(rows) - released}")
