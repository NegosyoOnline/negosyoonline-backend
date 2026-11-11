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
