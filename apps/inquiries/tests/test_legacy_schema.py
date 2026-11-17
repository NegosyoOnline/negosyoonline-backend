"""Code from before inquiries.0002 (a deploy that lags, or a rollback) inserts conversations without
the columns 0002 added. The database supplies their defaults."""
from django.db import connection
from django.test import TestCase

from apps.inquiries.models import InquiryConversation
from apps.sites.tests.base import make_account, make_site


class OlderCodeAfterMigration0002Tests(TestCase):
    def test_an_insert_naming_only_the_old_columns_works(self):
        site = make_site(make_account())
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO inquiries_inquiryconversation "
                "(site_id, provider, external_id, stores_history, display_name, provider_url, created_at) "
                "VALUES (%s, 'webchat', %s, %s, '', '', CURRENT_TIMESTAMP)",
                [site.pk, "wc_" + "1" * 24, True],
            )
        row = InquiryConversation.objects.get(site=site)
        self.assertEqual(
            (row.status, row.contact_name, row.contact_email, row.visitor_hash, row.thread_token, row.unread, row.auth_flag),
            ("open", "", "", "", "", False, ""),
        )
