"""Open the pending invoice item of each confirmed payment that has none.

    manage.py invoices_backfill_items             count only; write nothing
    manage.py invoices_backfill_items --dry-run   the same
    manage.py invoices_backfill_items --apply     open them

Covers payments confirmed before BILLING_INVOICE_REGISTER_ENABLED was on. Safe to run again:
a payment that has an item is skipped. The output is counts only. See docs/runbooks/billing.md.
"""
from django.core.management.base import BaseCommand

from apps.invoices import services
from apps.statements import switch


class Command(BaseCommand):
    help = "Open the invoice item of each confirmed payment that has none, or count them."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="open the items")
        parser.add_argument("--dry-run", action="store_true", help="count only (the default)")

    def handle(self, *args, apply, dry_run, **options):
        if apply and dry_run:
            self.stdout.write("Choose --apply or --dry-run, not both.")
            return
        if not switch.register_enabled():
            self.stdout.write("The invoice register is off. Nothing was read or written.")
            return
        if not apply:
            self.stdout.write(f"Dry run. Nothing was written. Payments without an item: {services.missing_items()}")
            return
        self.stdout.write(f"Backfill done. Items opened: {services.ensure_items()}")
