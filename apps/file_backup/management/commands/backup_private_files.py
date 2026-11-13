"""Measure, inspect, prove and purge the private file backup. One mode per run.

    manage.py backup_private_files --measure          read-only: count and bytes of the live store
    manage.py backup_private_files --status           the last run's counts, from the bucket
    manage.py backup_private_files --selftest         backup + restore proof with 3 synthetic files
    manage.py backup_private_files --purge-prefix P --actor A --request-ref R [--apply]
                                                       remove the copies of already-erased files

The output is counts only, never a pathname. `--measure` and `--selftest` run with the switch off
(they need PRIVATE_BLOB_READ_WRITE_TOKEN; `--selftest` and `--status` also the bucket and key).
See docs/runbooks/backups-and-restore.md, section 7.
"""
import logging

from django.core.management.base import BaseCommand, CommandError

from apps.file_backup import clients, crypto, restore, service
from apps.file_backup import selftest as proof
from apps.file_backup.destination import DestinationError
from apps.file_backup.source import SourceError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Measure the private store, show the backup status, run the self-test, or purge copies."

    def add_arguments(self, parser):
        parser.add_argument("--measure", action="store_true")
        parser.add_argument("--status", action="store_true")
        parser.add_argument("--selftest", action="store_true")
        parser.add_argument("--purge-prefix", metavar="PREFIX")
        parser.add_argument("--actor", help="who asked for the purge")
        parser.add_argument("--request-ref", help="the request it answers")
        parser.add_argument("--apply", action="store_true", help="purge for real (default: count only)")

    def handle(self, *args, measure, status, selftest, purge_prefix, actor, request_ref, apply,
               **options):
        modes = [bool(measure), bool(status), bool(selftest), bool(purge_prefix)]
        if sum(modes) != 1:
            raise CommandError("Choose exactly one of --measure, --status, --selftest, --purge-prefix.")
        try:
            if measure:
                self._measure()
            elif status:
                self._status()
            elif selftest:
                self._selftest()
            else:
                self._purge(purge_prefix, actor, request_ref, apply)
        except (SourceError, DestinationError, crypto.CryptoError) as error:
            raise CommandError(str(error)) from None

    def _measure(self):
        total, size, folders = 0, 0, {}
        for obj in clients.build_source().list_objects():
            row = folders.setdefault(service.top_folder(obj["pathname"]), [0, 0])
            row[0] += 1
            row[1] += obj["size"]
            total, size = total + 1, size + obj["size"]
        self.stdout.write(f"Private store: {total} objects, {size} bytes ({size / 1024**3:.3f} GB).")
        for folder, (count, nbytes) in sorted(folders.items()):
            self.stdout.write(f"  {folder}: {count} objects, {nbytes} bytes")
        biggest = max((n for _, n in folders.values()), default=0)
        self.stdout.write(f"(Largest folder total: {biggest} bytes. Pathnames are never printed.)")

    def _status(self):
        cfg = clients.build_config()
        found = service.read_status(clients.build_destination(), cfg)
        if not found:
            raise CommandError("No backup status in the bucket yet.")
        for name, value in found.items():
            self.stdout.write(f"{name}: {value}")
        matches = found["source_count"] == found["backup_count"]
        self.stdout.write(f"source count equals backup count: {'yes' if matches else 'NO'}")

    def _selftest(self):
        results = proof.run(
            clients.build_source(), clients.build_destination(), clients.build_config().key
        )
        for name, passed in results:
            self.stdout.write(f"{'PASS' if passed else 'FAIL'}: {name}")
        if not all(passed for _, passed in results):
            raise CommandError("The self-test failed.")
        self.stdout.write("Self-test passed.")

    def _purge(self, prefix, actor, request_ref, apply):
        if apply and not (actor and request_ref):
            raise CommandError("--apply needs --actor and --request-ref.")
        cfg = clients.build_config()
        counts = restore.purge_prefix(
            clients.build_source(), clients.build_destination(), cfg, prefix, apply=apply
        )
        if apply:
            logger.warning(
                "File backup purge: actor=%s request=%s matched=%d purged=%d still_live=%d",
                actor, request_ref, counts["matched"], counts["purged"], counts["still_live"],
            )
        self.stdout.write(("Purged: " if apply else "Dry run, nothing removed: ") + str(counts))
