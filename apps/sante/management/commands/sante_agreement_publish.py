"""Manage the central Membership Agreement, as TWCinc's operator screens did.

Every step names the operator (an active staff user) and is written to the operator audit log.
Output names versions and checksums, never file contents.

    manage.py sante_agreement_publish draft --operator NAME --title "..." --label 2026-11 \
        --effective-date 2026-11-15 --file agreement.pdf        # --file - reads standard input
    manage.py sante_agreement_publish publish --operator NAME --label 2026-11 \
        --reason "Authorized by X, filed at Y"
    manage.py sante_agreement_publish update|delete|status

Publishing is refused while no Santé catalog owner site is set. See docs/runbooks/sante-agreement.md.
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
        p.add_argument("--reason", required=True, help="who authorized it, where it is filed")
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
        if step == "draft":
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
            v = agreement.publish(user, o["label"], reason=o["reason"])
            out(f"Published {v.version_label} as the active version.")
