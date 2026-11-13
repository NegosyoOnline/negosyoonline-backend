"""List the ended sites due for erasure, and what an erasure would take. Changes nothing.

    manage.py erasure_dry_run                     as of now
    manage.py erasure_dry_run --as-of 2026-12-24  as of that day (00:00 Manila time)

Works with SITE_ERASURE_ENABLED off. The output is ids, dates, counts and codes only.
See docs/superpowers/plans/2026-11-13-site-deletion-executor-z8uq9mb22v.md, section 6.1.
"""
import json
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.billing.clock import MANILA
from apps.lifecycle.dry_run import erasure_dry_run


class Command(BaseCommand):
    help = "Dry run of the site deletion executor: reads and counts, writes nothing."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", metavar="DATE", help="YYYY-MM-DD, 00:00 Manila time")

    def handle(self, *args, as_of, **options):
        now = timezone.now()
        if as_of:
            try:
                now = datetime.strptime(as_of, "%Y-%m-%d").replace(tzinfo=MANILA)
            except ValueError:
                raise CommandError("--as-of must be a date such as 2026-12-24.") from None
        self.stdout.write(json.dumps(erasure_dry_run(now), indent=2, sort_keys=True))
