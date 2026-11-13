"""Simultaneous placements. These need real row locks and threads that share a database, so they
run on PostgreSQL only (CI's "PostgreSQL and Redis" job)."""
from unittest import skipUnless

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TransactionTestCase, override_settings

from apps.billing.tests.base import make_subscription
from apps.common.errors import ApiError
from apps.members.tests.test_concurrency import run_together
from apps.pack import checkout, constants as C, fulfillment, placement, records, review
from apps.pack.models import PackOrder
from apps.pack.tests.base import PNG, VISITOR
from apps.pack.tests.owner_base import make_order
from apps.payments.tests.base import make_payment_account, use_local_private_store
from apps.sante.tests.agreement_helpers import make_master, make_operator, published
from apps.sites.models import Site
from django.utils import timezone


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(
    PACK_ENABLED=True, MEMBERS_ENABLED=True, SANTE_AGREEMENT_ENABLED=True, PAYMENT_METHODS_ENABLED=True,
    BILLING_ENABLED=True,  # make_subscription answers None with Billing off
)
class PackConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        use_local_private_store(self)
        self.site = make_subscription().site
        operator = make_operator()
        make_master(operator)
        self.version = published(operator)
        self.account = make_payment_account(self.site)

    def token(self, mobile, email):
        data = {"full_name": "Maria", "mobile": mobile, "email": email}
        extras = lambda token: {  # noqa: E731
            records.AGREEMENT_KEY: records.agreement_from_post(
                token, version_id=str(self.version.pk), accept="1"),
            records.ENROLLMENT_KEY: records.enrollment_from_post("1"),
        }
        token, raw = checkout.start_checkout(self.site, data, extras=extras, ip=VISITOR)
        return token, raw

    def data(self):
        return {
            "account_id": self.account.public_id, "reference_number": "R1", "amount_sent": "1488",
            "sender_bank": "BDO", "sender_first_name": "M", "sender_last_name": "S",
            "sender_account_number": "1", "transferred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        }

    def place(self, token, raw, key="k"):
        upload = SimpleUploadedFile("p.png", PNG, content_type="image/png")
        return placement.place_pack_order(
            Site.objects.get(pk=self.site.pk), token, raw, submit_key=key, data=self.data(), upload=upload
        )

    def test_one_token_and_key_posted_three_times_makes_one_order(self):
        token, raw = self.token("09171234567", "maria@buyer.test")
        results = run_together(*[lambda: self.place(token, raw)] * 3)
        placed = [r for r in results if isinstance(r, placement.Placed)]
        self.assertEqual(len(placed), 3, results)
        self.assertEqual(sorted(p.replay for p in placed), [False, True, True])
        self.assertEqual(PackOrder.objects.count(), 1)

    def test_two_buyers_at_once_get_two_different_numbers(self):
        a, raw_a = self.token("09171234567", "maria@buyer.test")
        b, raw_b = self.token("09170000002", "juan@buyer.test")
        results = run_together(lambda: self.place(a, raw_a), lambda: self.place(b, raw_b))
        self.assertTrue(all(isinstance(r, placement.Placed) for r in results), results)
        self.assertEqual(
            sorted(PackOrder.objects.values_list("order_number", flat=True)), ["SMP00001", "SMP00002"]
        )

    def test_two_tokens_for_one_contact_make_one_order(self):
        a, raw_a = self.token("09171234567", "maria@buyer.test")
        b, raw_b = self.token("09171234567", "maria@buyer.test")
        results = run_together(lambda: self.place(a, raw_a), lambda: self.place(b, raw_b, key="k2"))
        self.assertEqual(sum(isinstance(r, placement.Placed) for r in results), 1, results)
        self.assertTrue(all(isinstance(r, (placement.Placed, ApiError)) for r in results), results)
        self.assertEqual(PackOrder.objects.count(), 1)

    def test_shipping_races_approval_and_never_ships_an_unverified_order(self):
        """Ship and approve at the same state_version: the row lock settles it. Ship never wins on an
        order that was not verified when it ran, and the order ends verified and not shipped."""
        site = Site.objects.get(pk=self.site.pk)
        account = site.account
        order, _ = make_order(site, status=C.PENDING_REVIEW)
        version = order.state_version
        results = run_together(
            lambda: review.approve(account, site, order.pk, state_version=version),
            lambda: fulfillment.record_shipment(
                account, site, order.pk, state_version=version, courier="lbc", tracking_number="T1"),
        )
        self.assertIsInstance(results[1], ApiError, results)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)
        self.assertIsNone(order.shipment_recorded_at)

    def test_two_shipments_at_once_make_one(self):
        site = Site.objects.get(pk=self.site.pk)
        account = site.account
        order, _ = make_order(site, status=C.VERIFIED)
        version = order.state_version
        ship = lambda: fulfillment.record_shipment(  # noqa: E731
            account, site, order.pk, state_version=version, courier="lbc", tracking_number="T1")
        results = run_together(ship, ship)
        self.assertEqual(sum(isinstance(r, ApiError) for r in results), 1, results)
        self.assertEqual(order.events.filter(kind=C.EVENT_SHIPMENT_RECORDED).count(), 1)
