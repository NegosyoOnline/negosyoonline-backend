"""The inbox's one switch. Off, nothing in this app stores into or shows from a table. Only
the retention rule and the deletion command touch one while it is off, and they only delete."""
from django.conf import settings
from django.db import connection

SMS = "sms"
MESSAGE_TABLE = "inbox_conversationmessage"


def inbox_on():
    return bool(settings.SMS_INBOX_ENABLED)


def tables_exist():
    """True once inbox.0001 is applied to this database. The code is released before its
    tables are created, so what must work with the switch off asks this first."""
    return MESSAGE_TABLE in connection.introspection.table_names()


CONTACT_TABLE = "inbox_contact"


def contacts_on():
    return bool(settings.SMS_CONTACTS_ENABLED)


def contacts_tables_exist():
    """True once inbox.0002 is applied to this database. The code is released before it."""
    return CONTACT_TABLE in connection.introspection.table_names()


def store_contacts_on():
    """The store owners' contacts: its own switch and the tables, not the HQ inbox switches."""
    return bool(settings.STORE_CONTACTS_ENABLED) and contacts_tables_exist()


BATCH_TABLE, DNT_TABLE = "inbox_bulkbatch", "inbox_donottext"


def bulk_on():
    return bool(settings.SMS_BULK_ENABLED)


def bulk_tables_exist():
    """True once inbox.0003 is applied to this database. The code is released before it."""
    return BATCH_TABLE in connection.introspection.table_names()


def dnt_tables_exist():
    return DNT_TABLE in connection.introspection.table_names()
