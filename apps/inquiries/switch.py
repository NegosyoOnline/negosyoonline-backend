"""The inquiries app's one switch. Off, every route answers the 404 of an unknown site, no
inquiries table is read or written and nothing is sent to a provider. Only the disposal hook and
the retention rule touch a table while it is off, and they only blank or delete."""
from django.conf import settings
from django.db import connection

CONNECTION_TABLE = "inquiries_inquiryconnection"


def inquiries_on():
    return bool(settings.INQUIRIES_ENABLED)


def tables_exist():
    """True once inquiries.0001 is applied to this database. The code is released before its
    tables are created, so everything that must work with the switch off asks this first."""
    return CONNECTION_TABLE in connection.introspection.table_names()
