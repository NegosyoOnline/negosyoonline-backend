"""The ledger's three switches. Off, nothing in this app reads or writes a table."""
from django.conf import settings
from django.db import connection

ENTITY_TABLE = "ledger_entity"


def ledger_on():
    return bool(settings.LEDGER_ENABLED)


def post_billing_on():
    """A half-set pair counts as off; `ledger.E001` reports it at startup."""
    return ledger_on() and bool(settings.LEDGER_POST_BILLING_ENABLED)


def live_books_on():
    """A half-set pair counts as off; only where the real books live is this set."""
    return ledger_on() and bool(settings.LEDGER_LIVE_BOOKS)


def tables_exist():
    """True once ledger.0001 is applied to this database. The code is released before its
    migration, so what must work with the switch off asks this first."""
    return ENTITY_TABLE in connection.introspection.table_names()
