"""Copy one Vercel Blob store into another (P7: the staging stores into production's).

    manage.py copy_blob_store --access private|public --source-token-env NAME --dest-token-env NAME
                              [--prefix P] [--max-mb N] [--apply]

A dry run by default: counts only, nothing downloaded or written. With --apply every source object
missing at the destination is downloaded, put under the same pathname with the same content type,
read back and checked by sha256. An object already at the destination is never overwritten: the same
bytes are `skipped_existing`, different bytes are a `conflict` (reported, not changed). A second run
copies 0. Exit is non-zero when anything failed or conflicted.

Each token is read only from the environment variable named on the command line. It is never an
argument, never printed and never logged. The output is counts only, never a pathname or URL.
Run it once, as a one-off job with both variables set on that job only.
See docs/runbooks/backups-and-restore.md, "Copying a Blob store (P7)".
"""
import os

from django.core.management.base import BaseCommand, CommandError

from apps.file_backup import blobcopy
from apps.file_backup.source import BlobSource, SourceError


def build_store(token, access):
    return BlobSource(token, access)


class Command(BaseCommand):
    help = "Copy a Blob store into another store, sha256-checked, never overwriting. Dry run by default."

    def add_arguments(self, parser):
        parser.add_argument("--access", required=True, choices=("private", "public"),
                            help="the access of BOTH stores (a Blob store is one or the other)")
        parser.add_argument("--source-token-env", required=True, metavar="NAME",
                            help="name of the environment variable holding the source store's token")
        parser.add_argument("--dest-token-env", required=True, metavar="NAME",
                            help="name of the environment variable holding the destination's token")
        parser.add_argument("--prefix", default="", help="copy only pathnames starting with this")
        parser.add_argument("--max-mb", type=int, default=50,
                            help="objects larger than this are counted as failed (default 50)")
        parser.add_argument("--rate", type=float, default=blobcopy.DEFAULT_RATE,
                            help="requests a second (default 10)")
        parser.add_argument("--apply", action="store_true", help="copy for real (default: count only)")

    def handle(self, *args, access, source_token_env, dest_token_env, prefix, max_mb, rate,
               apply, **options):
        if source_token_env == dest_token_env:
            raise CommandError("Not done: the source and destination token variables must differ.")
        if max_mb < 1 or rate <= 0:
            raise CommandError("Not done: --max-mb must be at least 1 and --rate above 0.")
        tokens = []
        for name in (source_token_env, dest_token_env):
            value = os.environ.get(name, "").strip()
            if not value:
                raise CommandError(f"Not done: the environment variable {name} is not set.")
            tokens.append(value)
        try:
            source, dest = (build_store(token, access) for token in tokens)
            if source.store_id == dest.store_id:
                raise CommandError("Not done: the source and destination are the same store.")
            counts = blobcopy.copy_store(
                source, dest, prefix=prefix, apply=apply, max_bytes=max_mb * 1024 * 1024, rate=rate,
            )
        except SourceError as error:
            raise CommandError(f"Not done: {error}") from None

        self._report(counts, access, apply)
        if counts["failed"] or counts["conflict"] or counts["size_differs"]:
            raise CommandError(
                f"Not done: {counts['failed']} failed, "
                f"{counts['conflict'] + counts['size_differs']} conflict."
            )

    def _report(self, counts, access, apply):
        out = self.stdout.write
        if not apply:
            out(f"Dry run, nothing copied. {access.capitalize()} store:")
            out(f"  listed: {counts['listed']}")
            out(f"  bytes: {counts['listed_bytes']} ({counts['listed_bytes'] / 1024**3:.3f} GB)")
            out(f"  already at the destination: {counts['existing']}")
            out(f"  would copy: {counts['would_copy']}")
            out(f"  conflict (existing, size differs): {counts['size_differs']}")
            return
        out(f"{access.capitalize()} store copied:")
        for name in blobcopy.COUNT_NAMES:
            out(f"  {name}: {counts[name]}")
        out(f"  oversize (counted in failed): {counts['oversize']}")
