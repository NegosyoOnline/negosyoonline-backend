"""Draft and publish the central Membership Agreement.

    manage.py sante_agreement_publish draft --label 2026-11 --effective-date 2026-11-15 --file agreement.pdf
    manage.py sante_agreement_publish draft ... --file -        # the PDF on standard input
    manage.py sante_agreement_publish publish --label 2026-11 --reason "Authorized by X, filed at Y"

A draft is never shown to buyers. Publishing makes it the active version and writes the reason
to the operator audit log. The output names the version and its checksum, never the file's
contents. See docs/runbooks/sante-agreement.md.
"""
import sys
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.sante import agreement


class Command(BaseCommand):
    help = "Draft or publish the central Membership Agreement."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="step", required=True)
        draft = sub.add_parser("draft", help="store a PDF as a draft version")
        draft.add_argument("--label", required=True)
        draft.add_argument("--effective-date", required=True, help="YYYY-MM-DD")
        draft.add_argument("--file", required=True, help="path to the PDF, or - for stdin")
        publish = sub.add_parser("publish", help="make a draft the active version")
        publish.add_argument("--label", required=True)
        publish.add_argument("--reason", required=True, help="who authorized it, where it is filed")

    def handle(self, *args, step, **options):
        if not agreement.enabled():
            raise CommandError("SANTE_AGREEMENT_ENABLED is off.")
        try:
            if step == "draft":
                version = self._draft(options)
                self.stdout.write(f"Draft {version.label} stored. sha256 {version.sha256}")
            else:
                version = agreement.publish(options["label"], reason=options["reason"])
                self.stdout.write(f"Published {version.label} as the active version.")
        except agreement.AgreementError as error:
            raise CommandError(str(error)) from None

    def _draft(self, options):
        try:
            effective = date.fromisoformat(options["effective_date"])
        except ValueError:
            raise agreement.AgreementError("Enter the effective date as YYYY-MM-DD.") from None
        path = options["file"]
        try:
            if path == "-":
                content = sys.stdin.buffer.read(agreement.MAX_BYTES + 1)
            else:
                with open(path, "rb") as handle:
                    content = handle.read(agreement.MAX_BYTES + 1)
        except OSError:
            raise agreement.AgreementError("The file could not be read.") from None
        return agreement.create_draft(
            label=options["label"], effective_date=effective, content=content
        )
