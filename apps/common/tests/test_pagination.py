from django.test import TestCase

from apps.common.errors import ApiError
from apps.common.pagination import page
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site


class PageTests(TestCase):
    def setUp(self):
        self.account = make_account()
        self.sites = [
            make_site(self.account, name=f"store-{n}", first=False) for n in range(3)
        ]
        self.mine = Site.objects.filter(account=self.account)

    def test_pages_in_creation_order_with_a_cursor(self):
        items, cursor = page(self.mine, limit=2, cursor=None, prefix="site")

        self.assertEqual(items, self.sites[:2])
        self.assertEqual(cursor, self.sites[1].public_id)

        items, cursor = page(self.mine, limit=2, cursor=cursor, prefix="site")

        self.assertEqual(items, self.sites[2:])
        self.assertIsNone(cursor)

    def test_an_exact_page_has_no_next_cursor(self):
        items, cursor = page(self.mine, limit=3, cursor=None, prefix="site")

        self.assertEqual(len(items), 3)
        self.assertIsNone(cursor)

    def test_a_cursor_from_another_account_is_refused(self):
        other = make_site(make_account("ben@example.test"), name="bens-store")

        with self.assertRaises(ApiError) as raised:
            page(self.mine, limit=2, cursor=other.public_id, prefix="site")

        self.assertEqual(raised.exception.code, "invalid_cursor")
        self.assertEqual(raised.exception.status, 400)

    def test_a_malformed_cursor_is_refused(self):
        for cursor in ("1", "site_", "acc_" + "0" * 20, "' OR 1=1"):
            with self.subTest(cursor=cursor):
                with self.assertRaises(ApiError) as raised:
                    page(self.mine, limit=2, cursor=cursor, prefix="site")
                self.assertEqual(raised.exception.code, "invalid_cursor")

    def test_the_limit_is_clamped(self):
        self.assertEqual(len(page(self.mine, limit=0, cursor=None, prefix="site")[0]), 1)
        self.assertEqual(len(page(self.mine, limit=1000, cursor=None, prefix="site")[0]), 3)
        self.assertEqual(len(page(self.mine, limit=None, cursor=None, prefix="site")[0]), 3)
