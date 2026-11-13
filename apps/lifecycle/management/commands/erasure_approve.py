"""Approve a proposed batch, or cancel it.

    manage.py erasure_approve BATCH --actor USERNAME --confirm "erase batch BATCH 3 sites" \
        --reason "weekly review 2026-12-28" [--exclude SITE ...]
    manage.py erasure_approve BATCH --actor USERNAME --cancel --reason "..."

The confirmation must be exactly `erase batch <id> <n> sites`, with n the number of sites that
remain after --exclude. The reason is a note of up to 300 characters with no email address in it.
Approving erases nothing by itself: the run does (`erasure_run` or the Celery task). Needs
SITE_ERASURE_ENABLED. See the plan, section 6.3.
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.management.commands import _shared
from apps.common.errors import ApiError
from apps.lifecycle import workflow
from apps.lifecycle.management.commands.erasure_propose import refusal


class Command(BaseCommand):
    help = "Approve (with the typed confirmation) or cancel a proposed batch."

    def add_arguments(self, parser):
        parser.add_argument("batch", metavar="BATCH")
        parser.add_argument("--actor", required=True, help="the staff user approving")
        parser.add_argument("--reason", required=True, help="a note, at most 300 characters")
        parser.add_argument("--confirm", default="", help='"erase batch <id> <n> sites"')
        parser.add_argument("--exclude", nargs="*", default=[], metavar="SITE")
        parser.add_argument("--cancel", action="store_true", help="cancel the batch instead")

    def handle(self, *args, batch, actor, reason, confirm, exclude, cancel, **options):
        operator = _shared.staff_user(actor)
        now = timezone.now()
        try:
            if cancel:
                done = workflow.cancel(operator, batch, reason=reason, now=now)
            else:
                if not confirm:
                    raise CommandError("--confirm is required to approve.")
                done = workflow.approve(
                    operator, batch, confirmation=confirm, exclude=exclude, reason=reason, now=now
                )
        except ApiError as error:
            raise refusal(error) from None
        self.stdout.write(json.dumps(workflow.batch_as_dict(done), indent=2, sort_keys=True))
