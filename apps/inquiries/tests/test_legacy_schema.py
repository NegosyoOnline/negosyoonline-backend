"""The code is released before inquiries.0002 is applied (the api service never migrates on
start). Until then the Pancake thread and reply paths must work on the inquiries.0001 tables: no
column 0002 added is selected or inserted. The schema here is really migrated back to 0001."""
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from apps.inquiries import services
from apps.inquiries.models import InquiryConversation, InquiryReply
from apps.inquiries.switch import stored_tables_exist
from apps.inquiries.tests.base import (
    KEY, REF, REPLY_TEXT, THREAD_OPEN, FakePancake, configure, no_limiter,
)
from apps.sites.tests.base import make_account, make_site


@override_settings(INQUIRIES_ENABLED=True, INQUIRIES_CREDENTIALS_KEY=KEY)
class BeforeMigration0002Tests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        no_limiter(self)
        self.addCleanup(call_command, "migrate", "inquiries", verbosity=0)
        call_command("migrate", "inquiries", "0001", verbosity=0)
        self.assertFalse(stored_tables_exist())
        self.account = make_account()
        self.site = make_site(self.account)
        self.connection = configure(self.site)

    def columns(self):
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, "inquiries_inquiryconversation")
        return {c.name for c in description}

    def test_the_schema_really_lacks_the_new_columns(self):
        self.assertNotIn("status", self.columns())

    def test_the_thread_opens_and_makes_its_handle(self):
        with FakePancake():
            code, data = services.thread(self.connection, REF, None)
        self.assertEqual(code, "ok")
        self.assertEqual(len(data["messages"]), len(THREAD_OPEN["messages"]))
        handle = InquiryConversation.objects.only("external_id", "reply_open").get(site=self.site)
        self.assertEqual((handle.external_id, handle.reply_open), (REF, True))
        cache.clear()
        with FakePancake():  # a second read finds the handle instead of making another
            services.thread(self.connection, REF, None)
        self.assertEqual(InquiryConversation.objects.only("pk").filter(site=self.site).count(), 1)

    def test_the_reply_is_sent_and_audited_and_repeating_its_key_returns_the_same_row(self):
        with FakePancake() as pancake:
            code, data = services.reply(self.connection, self.account, REF, REPLY_TEXT, "key-00000001")
            again = services.reply(self.connection, self.account, REF, REPLY_TEXT, "key-00000001")
        self.assertEqual((code, data["state"]), ("ok", InquiryReply.SENT))
        self.assertEqual(again, (code, data))
        self.assertEqual(len(pancake.sent_bodies()), 1)
        self.assertEqual(InquiryReply.objects.only("pk").filter(site=self.site).count(), 1)
        cache.clear()
        with FakePancake():
            code, data = services.thread(self.connection, REF, None)
        self.assertEqual([r["state"] for r in data["replies"]], ["sent"])

    def test_a_key_used_for_another_conversation_is_a_conflict(self):
        with FakePancake():
            services.reply(self.connection, self.account, REF, REPLY_TEXT, "key-00000002")
            other = REF[:-1] + "9"
            self.assertEqual(
                services.reply(self.connection, self.account, other, REPLY_TEXT, "key-00000002")[0],
                "idempotency_conflict",
            )


class OlderCodeAfterMigration0002Tests(TestCase):
    """Code from before the release (a deploy that lags, or a rollback) inserts conversations
    without the columns 0002 added. The database supplies their defaults."""

    def test_an_insert_naming_only_the_old_columns_works(self):
        site = make_site(make_account())
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO inquiries_inquiryconversation "
                "(site_id, provider, external_id, stores_history, display_name, provider_url, created_at) "
                "VALUES (%s, 'pancake', %s, %s, '', '', CURRENT_TIMESTAMP)",
                [site.pk, REF, False],
            )
        row = InquiryConversation.objects.get(site=site)
        self.assertEqual(
            (row.status, row.contact_name, row.contact_email, row.visitor_hash, row.thread_token, row.unread, row.auth_flag),
            ("open", "", "", "", "", False, ""),
        )
