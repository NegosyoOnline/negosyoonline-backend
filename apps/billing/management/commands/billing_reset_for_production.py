"""Empty the billing, ledger, invoice and statement rows of the restored production copy.

    manage.py billing_reset_for_production                       (dry run: counts only)
    manage.py billing_reset_for_production --apply --operator USERNAME [--database ALIAS]

The P7 copy restores everything except the billing and ledger rows (production plan P7c). This
command, run once after the restore and before the first real record, empties exactly the models
in RESET_MODELS and checks that they are empty. It refuses, touching nothing, unless
settings.APP_ENVIRONMENT is "production". The dry run (the default) prints the row count of each
model. `--apply` runs in one transaction: it counts, deletes, counts again, and rolls back and
fails if any count is above 0; it writes one audit entry, `billing_reset_for_production`, with
the counts. The output holds counts and model names only, no personal data. Plans and prices live
in code and are not touched. See docs/runbooks/billing.md, "Production reset after the P7 copy".

The rows are final by design (Django guards on the managers, PostgreSQL triggers on statements,
statement lines, invoice events and the ledger), so this command does not use the ORM delete.
On PostgreSQL it runs one TRUNCATE of all the listed tables, which the triggers do not cover;
the TRUNCATE has no CASCADE, so a table outside the list that points into one of these makes it
fail and roll back. On any other database (tests) it deletes table by table, children first.
"""
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections, transaction

from apps.operators import audit

# Children first: a row is deleted before any row it points to. One constant; a test checks the
# order against the foreign keys and the list against the four apps' models.
RESET_MODELS = (
    # billing: usage and charges
    "billing.UsageEvent",
    "billing.ChargeLine",
    "billing.UsageEntry",
    # invoices: the manual invoice register (items point at statements, so before them)
    "invoices.InvoiceEvent",
    "invoices.InvoiceScan",
    "invoices.ManualInvoice",
    "invoices.ManualInvoiceItem",
    "invoices.InvoiceBook",
    # statements
    "statements.StatementDelivery",
    "statements.StatementLine",
    "statements.Statement",
    "statements.StatementNumberSeries",
    # billing: payments, consent, notices, cycles, subscriptions, receiving accounts
    "billing.RenewalConsent",
    "billing.BankTransferEvent",
    "billing.BankTransferPayment",
    "billing.BillingNotice",
    "billing.BillingCycle",
    "billing.SubscriptionTransition",
    "billing.Subscription",
    "billing.ReceivingAccount",
    # ledger
    "ledger.LedgerEvent",
    "ledger.RecognitionSchedule",
    "ledger.JournalLine",
    "ledger.JournalEntry",
    "ledger.FiscalPeriod",
    "ledger.GLAccount",
    "ledger.LedgerAccess",
    "ledger.AccountingEntity",
)

# Models of the four apps that are deliberately kept, with the reason (also in the runbook).
KEPT_MODELS = {
    "statements.IssuerProfile": "the issuer's legal identity (COR), entered and verified by staff; not a record of a sale",
    "statements.BillingProfile": "a Subscriber's own business details, attached to the site, which is kept",
}


def reset_models():
    return [django_apps.get_model(label) for label in RESET_MODELS]


def count_rows(models, alias):
    # _base_manager: the plain manager, whatever guards the default one carries.
    return {m._meta.label: m._base_manager.using(alias).count() for m in models}


def empty_tables(models, alias):
    connection = connections[alias]
    quote = connection.ops.quote_name
    tables = [quote(m._meta.db_table) for m in models]
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY")
        else:
            for table in tables:
                cursor.execute(f"DELETE FROM {table}")


class Command(BaseCommand):
    help = (
        "Empty the billing, ledger, invoice and statement rows after the P7 copy. Production "
        "only. Dry run unless --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--database", default=DEFAULT_DB_ALIAS, help="the database alias to reset (default: default)"
        )
        parser.add_argument("--apply", action="store_true", help="delete the rows (default: count only)")
        parser.add_argument(
            "--operator", default="", help="with --apply: an active staff user, for the audit record"
        )

    def handle(self, *args, database, apply, operator, **options):
        if getattr(settings, "APP_ENVIRONMENT", "") != "production":
            raise CommandError(
                'This command runs only where APP_ENVIRONMENT is "production". Nothing was changed.'
            )
        if database not in connections:
            raise CommandError(f"No database alias {database!r}. Nothing was changed.")
        user = None
        if apply:
            user = get_user_model()._default_manager.using(database).filter(
                username=operator, is_active=True, is_staff=True
            ).first() if operator else None
            if user is None:
                raise CommandError("--apply needs --operator, an active staff user. Nothing was changed.")

        models = reset_models()
        if not apply:
            counts = count_rows(models, database)
            self._print(counts, "Dry run. Rows that --apply would delete:")
            self.stdout.write("Nothing was changed. Run again with --apply --operator USERNAME.")
            return

        try:
            with transaction.atomic(using=database):
                before = count_rows(models, database)
                empty_tables(models, database)
                after = count_rows(models, database)
                left = {label: n for label, n in after.items() if n}
                if left:
                    raise CommandError(
                        "Rows remain after the delete: "
                        + ", ".join(f"{label} {n}" for label, n in left.items())
                        + ". Rolled back; nothing was changed."
                    )
                audit.record(
                    user, "billing_reset_for_production", target_type="billing_reset", target_id=database,
                    detail={"database": database, "deleted": before}, using=database,
                )
        except CommandError:
            raise
        except Exception as exc:  # noqa: BLE001 - one clear failure; the transaction is already rolled back
            raise CommandError(
                f"The reset failed ({type(exc).__name__}). Rolled back; nothing was changed."
            ) from None
        self._print(before, "Deleted:")
        self.stdout.write("Every listed table is empty. One audit entry written.")

    def _print(self, counts, heading):
        self.stdout.write(heading)
        for label, n in counts.items():
            self.stdout.write(f"  {label}: {n}")
        self.stdout.write(f"  total: {sum(counts.values())}")
