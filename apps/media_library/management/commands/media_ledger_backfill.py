"""Write the ledger rows of media that was stored before the ledger existed.

    manage.py media_ledger_backfill            dry run: counts only, writes nothing, no store call
    manage.py media_ledger_backfill --apply    writes the rows

Every key an asset names and no row covers gets a row with added_at = the asset's created_at. A
file's bytes are the asset's file_size; a thumbnail's or poster's bytes come from a HEAD of its
public URL, and one whose HEAD fails is reported and gets no row (run it again). Idempotent on the
key: a second run writes nothing. Reports counts only, never a key.
"""
from django.core.management.base import BaseCommand

from apps.common import private_files
from apps.media_library import client_upload, ledger, store
from apps.media_library.models import MediaAsset, MediaObject

ROLE_FIELDS = (("file", "file_key"), ("thumb", "thumb_key"), ("poster", "poster_key"))
EXT_TYPES = {ext: content_type for content_type, ext in private_files.EXT.items()}


def _file_type(asset, key):
    if asset.asset_type == "video":
        return store.VIDEO_TYPE
    return EXT_TYPES.get(key[key.rfind("."):].lower(), "") if "." in key else ""


class Command(BaseCommand):
    help = "Backfill the media library byte ledger (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the rows (default: dry run).")

    def handle(self, *args, apply=False, **options):
        known = set(MediaObject.objects.values_list("key", flat=True))
        wanted = {"file": 0, "thumb": 0, "poster": 0}
        written = {"file": 0, "thumb": 0, "poster": 0}
        covered = failed = 0
        for asset in MediaAsset.objects.select_related("site").order_by("pk").iterator():
            for role, field in ROLE_FIELDS:
                key = getattr(asset, field)
                if not key:
                    continue
                if key in known:
                    covered += 1
                    continue
                wanted[role] += 1
                if not apply:
                    continue
                if role == "file":
                    content_type, size = _file_type(asset, key), asset.file_size
                else:
                    try:
                        content_type, size = client_upload.confirm_object(key)
                    except store.StoreError:
                        failed += 1
                        continue
                ledger.record(asset.site, asset=asset, role=role, key=key, content_type=content_type, size=size, added_at=asset.created_at)
                known.add(key)
                written[role] += 1
        mode = "APPLIED" if apply else "DRY RUN (nothing written; thumbnail and poster sizes are read at --apply)"
        self.stdout.write(f"media_ledger_backfill {mode}")
        self.stdout.write(f"  already in the ledger: {covered}")
        for role in wanted:
            self.stdout.write(f"  {role}: to write {wanted[role]}" + (f", written {written[role]}" if apply else ""))
        if apply:
            self.stdout.write(f"  size lookups failed (no row written, run again): {failed}")
