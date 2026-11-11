"""Run the lifecycle sweep once from a terminal.

    manage.py billing_sweep            catch every due subscription up with the time
    manage.py billing_sweep --dry-run  print what is due and write nothing

The worker runs the same function every 5 minutes. The output is counts only.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.billing import services


class Command(BaseCommand):
    help = "Catch every due subscription up with the time."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="print what is due; write nothing")

    def handle(self, *args, dry_run, **options):
        if not settings.BILLING_ENABLED:
            self.stdout.write("BILLING_ENABLED is off. Nothing was read or written.")
            return
        counts = services.sweep(dry_run=dry_run)
        self.stdout.write("Dry run. Nothing was written." if dry_run else "Sweep done.")
        if not counts:
            self.stdout.write("Nothing is due.")
        for code, count in counts.items():
            self.stdout.write(f"{code}: {count}")
