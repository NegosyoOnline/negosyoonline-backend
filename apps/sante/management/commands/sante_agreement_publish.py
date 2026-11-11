"""Manage the central Membership Agreement, as TWCinc's operator screens did.

Every step names the operator (an active staff user) and is written to the operator audit log.
Output names versions and checksums, never file contents.

    manage.py sante_agreement_publish authorize --operator NAME --document-date 2026-10-31 \
        --note "Santé letter ref 123" --covers-pdf-hosting --covers-branding \
        --covers-online-acceptance --file letter.pdf
    manage.py sante_agreement_publish verify --operator NAME --id 1
    manage.py sante_agreement_publish draft --operator NAME --title "..." --label 2026-11 \
        --effective-date 2026-11-15 --file agreement.pdf        # --file - reads standard input
    manage.py sante_agreement_publish publish --operator NAME --label 2026-11
    manage.py sante_agreement_publish update|delete|revoke|status

Publishing is refused until an authorization is recorded and verified, and while no Santé catalog
owner site is set. See docs/runbooks/sante-agreement.md.
"""
import sys
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.sante import agreement


def _date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CommandError("Enter dates as YYYY-MM-DD.") from None


def _read(path):
    try:
        if path == "-":
            return sys.stdin.buffer.read(agreement.MAX_BYTES + 1)
        with open(path, "rb") as handle:
            return handle.read(agreement.MAX_BYTES + 1)
    except OSError:
        raise CommandError("The file could not be read.") from None


class Command(BaseCommand):
    help = "Manage the central Membership Agreement."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="step", required=True)

        def step(name, *, operator=True):
            p = sub.add_parser(name)
            if operator:
                p.add_argument("--operator", required=True, help="username of an active staff user")
            return p

        p = step("authorize")
        p.add_argument("--document-date", required=True)
        p.add_argument("--note", required=True)
        p.add_argument("--covers-pdf-hosting", action="store_true")
        p.add_argument("--covers-branding", action="store_true")
        p.add_argument("--covers-online-acceptance", action="store_true")
        p.add_argument("--file", required=True, help="evidence: PDF, JPEG or PNG; - for stdin")
        p = step("verify")
        p.add_argument("--id", type=int, required=True)
        p = step("revoke")
        p.add_argument("--id", type=int, required=True)
        p.add_argument("--reason", required=True)
        p = step("draft")
        p.add_argument("--title", required=True)
        p.add_argument("--label", required=True)
        p.add_argument("--effective-date", required=True)
        p.add_argument("--file", required=True, help="the agreement PDF; - for stdin")
        p = step("update")
        p.add_argument("--label", required=True, help="the draft to change")
        p.add_argument("--title", required=True)
        p.add_argument("--new-label", required=True)
        p.add_argument("--effective-date", required=True)
        p = step("delete")
        p.add_argument("--label", required=True)
        p = step("publish")
        p.add_argument("--label", required=True)
        step("status", operator=False)

    def handle(self, *args, step, **o):
        if not agreement.enabled():
            raise CommandError("SANTE_AGREEMENT_ENABLED is off.")
        try:
            self.run_step(step, o)
        except agreement.AgreementError as error:
            raise CommandError(str(error)) from None

    def run_step(self, step, o):
        out = self.stdout.write
        if step == "status":
            for line in agreement.status():
                out(line)
            return
        user = get_user_model().objects.filter(username=o["operator"]).first()
        if user is None:
            raise agreement.AgreementError(agreement.NOT_OPERATOR)
        if step == "authorize":
            row = agreement.record_authorization(
                user, content=_read(o["file"]), document_date=_date(o["document_date"]),
                note=o["note"], covers_pdf_hosting=o["covers_pdf_hosting"],
                covers_branding=o["covers_branding"],
                covers_online_acceptance=o["covers_online_acceptance"])
            out(f"Authorization {row.pk} recorded. It is not in force until verified.")
        elif step == "verify":
            agreement.verify_authorization(user, o["id"])
            out(f"Authorization {o['id']} verified.")
        elif step == "revoke":
            agreement.revoke_authorization(user, o["id"], reason=o["reason"])
            out(f"Authorization {o['id']} revoked.")
        elif step == "draft":
            v = agreement.create_draft(
                user, title=o["title"], version_label=o["label"],
                effective_date=_date(o["effective_date"]), content=_read(o["file"]))
            out(f"Draft {v.version_label} stored. sha256 {v.sha256}")
        elif step == "update":
            v = agreement.update_draft(
                user, o["label"], title=o["title"], new_version_label=o["new_label"],
                effective_date=_date(o["effective_date"]))
            out(f"Draft {v.version_label} updated.")
        elif step == "delete":
            agreement.delete_draft(user, o["label"])
            out(f"Draft {o['label']} deleted.")
        elif step == "publish":
            v = agreement.publish(user, o["label"])
            out(f"Published {v.version_label} as the active version.")
