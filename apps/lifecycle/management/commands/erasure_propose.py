"""Freeze a list of eligible sites as a batch awaiting approval. Deletes nothing.

    manage.py erasure_propose SITE [SITE ...] --actor USERNAME

SITE is a site public id from `erasure_dry_run`. 1 to 25 sites. Needs SITE_ERASURE_ENABLED. The
output is the batch id, its expiry and the sites with their planned counts: ids and counts only.
See docs/superpowers/plans/2026-11-13-site-deletion-executor-z8uq9mb22v.md, section 6.2.
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.management.commands import _shared
from apps.common.errors import ApiError
from apps.lifecycle import workflow


def refusal(error):
    if error.status == 404 and error.message == "Not found.":
        return CommandError("SITE_ERASURE_ENABLED is off in this environment, so nothing was done.")
    detail = " ".join(f"{k}: {'; '.join(v)}" for k, v in (error.fields or {}).items())
    return CommandError(f"{error.message} {detail}".strip())


class Command(BaseCommand):
    help = "Propose a batch of eligible sites for erasure. Deletes nothing."

    def add_arguments(self, parser):
        parser.add_argument("sites", nargs="+", metavar="SITE")
        parser.add_argument("--actor", required=True, help="the staff user proposing the batch")

    def handle(self, *args, sites, actor, **options):
        operator = _shared.staff_user(actor)
        try:
            batch = workflow.propose(operator, sites, timezone.now())
        except ApiError as error:
            raise refusal(error) from None
        self.stdout.write(json.dumps(workflow.batch_as_dict(batch), indent=2, sort_keys=True))
