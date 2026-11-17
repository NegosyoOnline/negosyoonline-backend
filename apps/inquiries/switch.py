"""The inquiries app's switches. Off, every route answers the 404 of an unknown site, no
inquiries table is read or written and nothing is sent to a provider. Only the disposal hook and
the retention rules touch a table while a switch is off, and they only blank or delete.

INQUIRIES_ENABLED opens the owner's routes. INQUIRY_WEBCHAT_ENABLED opens the two storefront
chat routes, and only while INQUIRIES_ENABLED is on too."""
from django.conf import settings
from django.db import connection

CONNECTION_TABLE = "inquiries_inquiryconnection"
STORED_TABLES = ("inquiries_inquirymessage", "inquiries_inquirychannelsettings")


def inquiries_on():
    return bool(settings.INQUIRIES_ENABLED)


def webchat_on():
    return bool(settings.INQUIRY_WEBCHAT_ENABLED and settings.INQUIRIES_ENABLED)


def contact_inbox_on():
    """INQUIRY_CONTACT_INBOX_ENABLED: the storefront contact form's messages are also stored as
    email conversations. Only while INQUIRIES_ENABLED is on too. Replying by email needs no switch
    of its own: it is part of the owner's routes."""
    return bool(settings.INQUIRY_CONTACT_INBOX_ENABLED and settings.INQUIRIES_ENABLED)


def messenger_on():
    """INQUIRY_MESSENGER_ENABLED: the direct Messenger inbox. Only while INQUIRIES_ENABLED is on too."""
    return bool(settings.INQUIRY_MESSENGER_ENABLED and settings.INQUIRIES_ENABLED)


def tables_exist():
    """True once inquiries.0001 is applied to this database. The code is released before its
    tables are created, so everything that must work with the switch off asks this first."""
    return CONNECTION_TABLE in connection.introspection.table_names()


_COLUMNS_SEEN = False


def _messenger_columns_exist():
    """True once inquiries.0004 (the Messenger columns) is applied. Every query on the
    conversation and message tables reads those columns, so the stored channels wait for it.
    A positive answer is remembered for the life of the process."""
    global _COLUMNS_SEEN
    if _COLUMNS_SEEN:
        return True
    with connection.cursor() as cursor:
        found = {c.name for c in connection.introspection.get_table_description(cursor, "inquiries_inquiryconversation")}
    _COLUMNS_SEEN = "psid" in found
    return _COLUMNS_SEEN


def stored_tables_exist():
    """True once inquiries.0002 (the stored channels) and 0004 (Messenger) are applied. Everything
    that reads the message or channel-settings tables, or the columns they added, asks this first."""
    names = connection.introspection.table_names()
    return CONNECTION_TABLE in names and all(table in names for table in STORED_TABLES) and _messenger_columns_exist()
