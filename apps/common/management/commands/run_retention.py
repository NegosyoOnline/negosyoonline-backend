"""Run the retention rules by hand.

    manage.py run_retention --dry-run           count what every rule would take; change nothing
    manage.py run_retention                     run every rule and write the run log
    manage.py run_retention --only NAME         one rule (repeat --only for more)

The output is rule names and counts only. See docs/runbooks/retention.md.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.common import retention


class Command(BaseCommand):
    help = "Run the retention rules, or count what they would take with --dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="count only; change nothing")
        parser.add_argument("--only", action="append", metavar="NAME", help="run only this rule")

    def handle(self, *args, dry_run, only, **options):
        try:
            report = retention.run_all(dry_run=dry_run, only=only)
        except ValueError as exc:
            known = ", ".join(retention.names())
            raise CommandError(f"{exc} The rules are: {known}.") from None
        self.stdout.write("Dry run: nothing was changed." if dry_run else "Retention run.")
        for name, count in report.counts.items():
            self.stdout.write(f"{name}: {count}")
        for name in report.switched_off:
            self.stdout.write(f"{name}: switched off")
        for name, error in report.errors.items():
            self.stdout.write(f"{name}: failed ({error})")
        if report.errors:
            raise CommandError("One or more rules failed. The others ran.")
