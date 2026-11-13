"""Export the Pack files of one ended site (orders, proof images, manifest) as one ZIP.

    manage.py export_pack_files ACCOUNT --site SITE --actor USERNAME --request-ref REF --out-dir DIR
    manage.py export_pack_files ACCOUNT --site SITE --actor USERNAME --request-ref REF --stdout-base64
    ... --basis request     (a person's own access or deletion request on a site still served)

Inside the 30 days after the service ended it runs on its own; outside them it refuses. It changes
nothing except its output and one `pack_files_exported` audit entry, written only after the whole
archive was built. See docs/runbooks/retention.md, "The Pack files of an ended site".
"""
import base64
import os
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts import retention as account_retention
from apps.accounts.management.commands import _shared
from apps.common import private_files
from apps.operators import audit
from apps.pack import export
from apps.pack.models import PackOrder
from apps.sites.models import Site


class Command(BaseCommand):
    help = "Export the Pack orders and proof images of one ended site as a ZIP."

    def add_arguments(self, parser):
        _shared.add_arguments(parser)
        parser.add_argument("--site", required=True, help="the site's public id (site_...)")
        parser.add_argument(
            "--basis", choices=export.BASES, default=export.WINDOW,
            help="'window' (default): only inside the 30 days after service ended. 'request': a "
            "person's own access or deletion request on a site that is still served",
        )
        where = parser.add_mutually_exclusive_group(required=True)
        where.add_argument("--out-dir", metavar="DIR", help="write the ZIP in this folder")
        where.add_argument(
            "--stdout-base64", dest="to_terminal", action="store_true",
            help="print the ZIP as base64 text",
        )

    def handle(self, *args, account, actor, request_ref, site, basis, out_dir, to_terminal, **options):
        if bool(out_dir) == bool(to_terminal):  # call_command skips the parser's own check
            raise CommandError("Give exactly one of --out-dir and --stdout-base64.")
        actor = _shared.staff_user(actor)
        account = _shared.account_for(account)
        try:
            request_ref = account_retention._checked_ref(request_ref)
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        if account_retention.is_disposed(account):
            raise CommandError("That account is disposed of. There is nothing to export.")
        if not settings.PACK_ENABLED:
            raise CommandError("Pack is off here. Nothing was written.")
        found = Site.objects.filter(account=account, public_id=site).first()
        if found is None:
            raise CommandError("No such site for this account.")
        if out_dir and not Path(out_dir).is_dir():
            raise CommandError(f"{out_dir} is not a folder.")
        now = timezone.now()
        reason = export.window_error(found, now, basis)
        if reason:
            raise CommandError(reason + " Nothing was written.")
        if basis == export.REQUEST and not PackOrder.objects.filter(site=found).exists():
            raise CommandError("This site has no Pack orders. Nothing was written.")
        target = None
        if out_dir:
            target = Path(out_dir) / f"pack-export-{found.public_id}-{request_ref}.zip"
            if target.exists():
                raise CommandError(f"{target} already exists. Nothing was written.")
        try:
            with export.build_site_archive(found, now, basis=basis) as (path, summary):
                with transaction.atomic():  # no audit entry unless the archive was handed over
                    audit.record(
                        actor, "pack_files_exported", target_type="site", target_id=found.public_id,
                        reason=request_ref,
                        detail={
                            "counts": summary["counts"], "bytes": summary["bytes"],
                            "archive_sha256": summary["archive_sha256"], "basis": basis,
                        },
                    )
                    if target is not None:
                        self._write(path, target)
                    else:
                        self._print(path)
        except export.ExportRefused as exc:
            raise CommandError(str(exc)) from None
        except private_files.StoreError:
            raise CommandError(
                "A proof image could not be read from the private store. Nothing was written; "
                "run it again."
            ) from None
        counts = summary["counts"]
        line = (
            f"Exported site {found.public_id}: {counts['orders']} orders, {counts['proofs']} proofs, "
            f"{counts['files']} files, {summary['archive_bytes']} bytes, "
            f"{summary['warnings']} warnings. archive sha256 {summary['archive_sha256']}"
        )
        if target is not None:
            self.stdout.write(f"{line}\nWritten to {target}")
        else:
            self.stderr.write(line)

    def _write(self, source, target):
        try:
            handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise CommandError(f"{target} already exists. Nothing was written.") from None
        try:
            with os.fdopen(handle, "wb") as out, open(source, "rb") as stream:
                shutil.copyfileobj(stream, out)
        except BaseException:
            os.unlink(target)
            raise

    def _print(self, source):
        with open(source, "rb") as stream:
            for block in iter(lambda: stream.read(57 * 1024), b""):
                self.stdout.write(base64.encodebytes(block).decode("ascii"), ending="")
        self.stdout.write("")
