"""Run an approved batch: erase its sites, and retry its failed items (up to 5 attempts each).

    manage.py erasure_run BATCH

Needs SITE_ERASURE_ENABLED; the account step also needs RETENTION_ENABLED. Only a batch a person
approved can run. Running it again changes nothing for a site already erased. The output is the
batch id, its status and counts. See the plan, section 6.4.
"""
import json

from django.core.management.base import BaseCommand

from apps.common.errors import ApiError
from apps.lifecycle import workflow
from apps.lifecycle.management.commands.erasure_propose import refusal


class Command(BaseCommand):
    help = "Run an approved erasure batch."

    def add_arguments(self, parser):
        parser.add_argument("batch", metavar="BATCH")

    def handle(self, *args, batch, **options):
        try:
            result = workflow.run_batch(batch)
        except ApiError as error:
            raise refusal(error) from None
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
