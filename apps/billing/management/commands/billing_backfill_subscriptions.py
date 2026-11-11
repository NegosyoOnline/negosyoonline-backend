"""Give every site made before billing its subscription.

    manage.py billing_backfill_subscriptions           count only; write nothing
    manage.py billing_backfill_subscriptions --apply   write them

Each site gets exactly what site creation writes: a trial counted from its account's creation
date, already ended when those 14 days have passed. A removed site gets none. It is safe to
run again: a site that has a subscription is skipped. The output is counts only. See
docs/runbooks/billing.md.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.billing import services


class Command(BaseCommand):
    help = "Create the subscription of each site that has none, or count them without --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="write the subscriptions")

    def handle(self, *args, apply, **options):
        if not settings.BILLING_ENABLED:
            self.stdout.write("BILLING_ENABLED is off. Nothing was read or written.")
            return
        counts = services.backfill(apply=apply)
        self.stdout.write("Backfill done." if apply else "Dry run. Nothing was written.")
        if not counts:
            self.stdout.write("Every site already has a subscription.")
        for name, count in counts.items():
            self.stdout.write(f"{name}: {count}")
