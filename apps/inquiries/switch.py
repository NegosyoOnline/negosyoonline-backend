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


def comments_on():
    """INQUIRY_COMMENTS_ENABLED: Facebook Page comments. Only while the Messenger inbox is on too."""
    return bool(settings.INQUIRY_COMMENTS_ENABLED and messenger_on())


COMMENT_TABLES = ("inquiries_inquirypost", "inquiries_inquirycomment", "inquiries_inquirycommentaction",
                  "inquiries_inquirycommentsettings")


def comment_tables_exist():
    """True once inquiries.0006 (the comment tables) is applied. The code is released before its
    migration, so everything that touches those tables, even to delete, asks this first."""
    names = connection.introspection.table_names()
    return CONNECTION_TABLE in names and all(table in names for table in COMMENT_TABLES)


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


AUTOMATION_TABLES = ("inquiries_inquirycommentrule", "inquiries_inquirycommentautomation", "inquiries_inquiryautoreply")
_AUTOMATION_SEEN = False


def automation_tables_exist():
    """True once inquiries.0007 (the comment auto-reply tables) is applied. A positive answer is
    remembered for the life of the process."""
    global _AUTOMATION_SEEN
    if _AUTOMATION_SEEN:
        return True
    names = connection.introspection.table_names()
    _AUTOMATION_SEEN = all(table in names for table in AUTOMATION_TABLES)
    return _AUTOMATION_SEEN


def automation_on():
    """INQUIRY_COMMENT_AUTOMATION_ENABLED: comment auto-replies. Only while comments are on too."""
    return bool(settings.INQUIRY_COMMENT_AUTOMATION_ENABLED and comments_on())


SMS_TABLES = ("inquiries_inquirysmssettings", "inquiries_inquirysmsoptout")
_SMS_SEEN = False


def sms_tables_exist():
    """True once inquiries.0010 (the text-conversation tables) is applied. The code is released
    before its migration, so everything that touches those tables, even to delete, asks this first.
    A positive answer is remembered for the life of the process."""
    global _SMS_SEEN
    if _SMS_SEEN:
        return True
    names = connection.introspection.table_names()
    _SMS_SEEN = all(table in names for table in SMS_TABLES)
    return _SMS_SEEN


def sms_on():
    """INQUIRY_SMS_ENABLED: a store's text conversations with its members (SMS-OWNER). Only while
    INQUIRIES_ENABLED and SMS_GATEWAY_ENABLED are on too."""
    return bool(settings.INQUIRY_SMS_ENABLED and settings.INQUIRIES_ENABLED and settings.SMS_GATEWAY_ENABLED)


def sms_ready():
    """The switches are on and the tables are there: the only state in which a text route works."""
    return sms_on() and sms_tables_exist()


STORE_BULK_TABLES = ("notifications_storebulkbatch", "notifications_storebulkrecipient")
_BULK_SEEN = False


def store_bulk_tables_exist():
    """True once notifications.0008 (the store bulk tables) is applied. A positive answer is
    remembered for the life of the process."""
    global _BULK_SEEN
    if _BULK_SEEN:
        return True
    names = connection.introspection.table_names()
    _BULK_SEEN = all(table in names for table in STORE_BULK_TABLES)
    return _BULK_SEEN


def store_bulk_on():
    """STORE_BULK_SMS_ENABLED (STORE-BULK): a store's bulk texts to its own members. Only while the text
    conversations (INQUIRY_SMS_ENABLED, INQUIRIES_ENABLED, SMS_GATEWAY_ENABLED) and the store phone
    lines (SMS_SITE_LINES_ENABLED) are on too."""
    return bool(settings.STORE_BULK_SMS_ENABLED and sms_on() and settings.SMS_SITE_LINES_ENABLED)


def store_bulk_ready():
    """The switches are on and the tables are there: the only state in which a bulk route works."""
    return store_bulk_on() and sms_tables_exist() and store_bulk_tables_exist()
