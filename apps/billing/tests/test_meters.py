"""`GET sites/{site_id}/billing/meters/`: the Plan usage meters, display only."""
from datetime import datetime
from unittest import mock

from django.test import override_settings

from apps.billing import clock, meters, services
from apps.billing.models import Subscription
from apps.billing.tests.base import days, method_on_file
from apps.media_library.models import MediaAsset
from apps.members.models import Member
from apps.pack import constants as pack_constants
from apps.pack.models import PackOrder
from apps.shop import constants as shop_constants
from apps.shop.models import ShopOrder
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.base import make_account, make_site

MANILA = clock.MANILA
_counter = [0]


def shop_order(site, created_at, status=shop_constants.VERIFIED):
    _counter[0] += 1
    n = _counter[0]
    order = ShopOrder.objects.create(
        site=site, order_number=f"SO8{n:04d}", buyer_kind="guest", ship_mode="guest",
        receiver_name="Maria Santos", receiver_mobile="+639171234567", contact_email="m@buyer.test",
        delivery_address={"line": "1 St"}, payment_status=status, subtotal_centavos=100,
        shipping_policy="zero", total_centavos=100, quote_fingerprint="f" * 64,
        idempotency_key=f"meter-key-{n}", view_token_hash="t" * 64,
        cancel_note="Out of stock" if status == shop_constants.CANCELLED else "",
    )
    ShopOrder.objects.filter(pk=order.pk).update(created_at=created_at)
    return order


def pack_order(site, created_at, status=pack_constants.VERIFIED):
    _counter[0] += 1
    n = _counter[0]
    member = Member.objects.create(
        site=site, full_name="Maria Santos", mobile=f"+6391712{n:05d}"[:13], email=f"m{n}@buyer.test",
        mobile_state="verified", email_state="verified",
    )
    order = PackOrder.objects.create(
        site=site, member=member, order_number=f"SMP8{n:04d}", price_centavos=pack_constants.PRICE_CENTAVOS,
        buyer_name="Maria Santos", payment_status=status, idempotency_key=f"meter-key-{n}",
    )
    PackOrder.objects.filter(pk=order.pk).update(created_at=created_at)
    return order


def media(site, size, **fields):
    _counter[0] += 1
    return MediaAsset.objects.create(
        site=site, title=f"m{_counter[0]}", asset_type=fields.pop("asset_type", "image"),
        file_size=size, **fields,
    )


@override_settings(BILLING_ENABLED=True)
class MetersTests(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.subscription = Subscription.objects.get(site=self.site)
        self.path = f"/v1/sites/{self.site_id}/billing/meters/"

    def pay(self):
        with method_on_file():
            services.first_payment_succeeded(
                self.subscription, paid_at=self.subscription.trial_end_at, reference="first"
            )
        self.subscription.refresh_from_db()
        return self.subscription.cycles.get(sequence=1)

    def at(self, moment):
        patcher = mock.patch("apps.billing.api.timezone")
        patcher.start().now.return_value = moment
        self.addCleanup(patcher.stop)

    def assertSameMoment(self, iso, moment):
        # The JSON keeps milliseconds only.
        self.assertLess(abs((datetime.fromisoformat(iso) - moment).total_seconds()), 0.001)

    def body(self):
        response = self.get(self.path)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_signed_out_switch_off_and_foreign_site(self):
        self.assertEqual(self.client_class().get(self.path, secure=True).status_code, 401)
        with override_settings(BILLING_ENABLED=False):
            self.error(self.get(self.path), 404, "not_found")
        self.error(self.get(self.path, client=self.other_client()), 404, "not_found")

    def test_owner_gets_the_sums_and_the_exact_shape(self):
        trial = self.subscription.trial_start_at
        self.at(trial + days(2))
        media(self.site, 1_200_000_000)
        media(self.site, 800_000_000, asset_type="video")
        shop_order(self.site, trial + days(1))
        shop_order(self.site, trial + days(1))
        pack_order(self.site, trial + days(1))
        body = self.body()
        self.assertEqual(set(body), {"storage", "orders"})
        self.assertEqual(body["storage"], {"used_bytes": 2_000_000_000, "limit_bytes": 5_000_000_000})
        self.assertEqual(
            set(body["orders"]), {"count", "limit", "period_start", "period_end", "period_kind"}
        )
        self.assertEqual((body["orders"]["count"], body["orders"]["limit"]), (3, 500))

    def test_an_empty_site_reads_zero(self):
        self.at(self.subscription.trial_start_at + days(1))
        body = self.body()
        self.assertEqual(body["storage"]["used_bytes"], 0)
        self.assertEqual(body["orders"]["count"], 0)

    def test_storage_counts_hidden_and_unpublished_rows_but_not_another_site(self):
        media(self.site, 100, active=False)
        media(self.site, 10, published_at=None)
        media(self.site, 1)
        media(self.other_site(), 999_999)
        self.at(self.subscription.trial_start_at + days(1))
        self.assertEqual(self.body()["storage"]["used_bytes"], 111)

    def other_site(self):
        return make_site(make_account("ben@example.test"), name="bens-store", first=False)

    def test_orders_of_another_site_are_not_counted(self):
        trial = self.subscription.trial_start_at
        shop_order(self.other_site(), trial + days(1))
        self.at(trial + days(2))
        self.assertEqual(self.body()["orders"]["count"], 0)

    def test_cancelled_shop_orders_are_left_out(self):
        trial = self.subscription.trial_start_at
        shop_order(self.site, trial + days(1))
        shop_order(self.site, trial + days(1), status=shop_constants.CANCELLED)
        self.at(trial + days(2))
        self.assertEqual(self.body()["orders"]["count"], 1)

    def test_orders_outside_the_period_are_left_out(self):
        trial = self.subscription.trial_start_at
        shop_order(self.site, trial - days(1))
        pack_order(self.site, self.subscription.trial_end_at)  # the end is exclusive
        shop_order(self.site, trial + days(1))
        self.at(trial + days(2))
        self.assertEqual(self.body()["orders"]["count"], 1)

    def test_the_trial_period(self):
        self.at(self.subscription.trial_start_at + days(2))
        orders = self.body()["orders"]
        self.assertEqual(orders["period_kind"], "trial")
        self.assertSameMoment(orders["period_start"], self.subscription.trial_start_at)
        self.assertSameMoment(orders["period_end"], self.subscription.trial_end_at)

    def test_a_site_that_paid_during_its_trial_stays_on_the_trial_window_until_the_paid_cycle(self):
        trial_end = self.subscription.trial_end_at
        with method_on_file():
            services.first_payment_succeeded(
                self.subscription, paid_at=self.subscription.trial_start_at + days(3), reference="early", early=True
            )
        self.subscription.refresh_from_db()
        self.assertNotEqual(self.subscription.state, "trial_active")
        self.at(self.subscription.trial_start_at + days(5))
        orders = self.body()["orders"]
        self.assertEqual(orders["period_kind"], "trial")
        self.assertSameMoment(orders["period_start"], self.subscription.trial_start_at)
        self.assertSameMoment(orders["period_end"], trial_end)
        self.at(trial_end + days(1))
        self.assertEqual(self.body()["orders"]["period_kind"], "billing_month")

    def test_the_billing_month_period(self):
        cycle = self.pay()
        moment = cycle.start_at + days(2)
        self.at(moment)
        shop_order(self.site, moment - days(1))
        shop_order(self.site, cycle.start_at - days(1))  # before the cycle: the trial's
        expected = services.allowance_period_at(self.subscription, moment)
        orders = self.body()["orders"]
        self.assertEqual(orders["period_kind"], "billing_month")
        self.assertSameMoment(orders["period_start"], expected["start_at"])
        self.assertSameMoment(orders["period_end"], expected["end_at"])
        self.assertEqual(orders["count"], 1)

    def test_the_calendar_month_period_between_cycles_and_for_an_exempt_site(self):
        moment = datetime(2030, 12, 15, 9, 0, tzinfo=MANILA)  # past the trial, no paid cycle
        shop_order(self.site, datetime(2030, 12, 1, 0, 0, tzinfo=MANILA))
        pack_order(self.site, datetime(2030, 12, 31, 23, 59, tzinfo=MANILA))
        shop_order(self.site, datetime(2030, 11, 30, 23, 59, tzinfo=MANILA))
        shop_order(self.site, datetime(2031, 1, 1, 0, 0, tzinfo=MANILA))
        self.at(moment)
        orders = self.body()["orders"]
        self.assertEqual(orders["period_kind"], "calendar_month")
        self.assertSameMoment(orders["period_start"], datetime(2030, 12, 1, tzinfo=MANILA))
        self.assertSameMoment(orders["period_end"], datetime(2031, 1, 1, tzinfo=MANILA))
        self.assertEqual(orders["count"], 2)
        Subscription.objects.filter(pk=self.subscription.pk).update(billing_exempt=True)
        self.assertEqual(self.body()["orders"]["period_kind"], "calendar_month")

    def test_shop_and_pack_orders_are_summed(self):
        trial = self.subscription.trial_start_at
        shop_order(self.site, trial + days(1))
        pack_order(self.site, trial + days(1))
        pack_order(self.site, trial + days(1), status=pack_constants.PENDING_REVIEW)
        self.at(trial + days(2))
        self.assertEqual(self.body()["orders"]["count"], 3)

    def test_rejected_and_expired_pack_orders_are_left_out(self):
        trial = self.subscription.trial_start_at
        pack_order(self.site, trial + days(1), status=pack_constants.REJECTED)
        pack_order(self.site, trial + days(1), status=pack_constants.XENDIT_EXPIRED)
        pack_order(self.site, trial + days(1), status=pack_constants.VERIFIED)
        pack_order(self.site, trial + days(1), status=pack_constants.PENDING_REVIEW)
        pack_order(self.site, trial + days(1), status=pack_constants.XENDIT_MISMATCH)
        self.at(trial + days(2))
        self.assertEqual(self.body()["orders"]["count"], 3)

    def test_it_writes_nothing(self):
        before = (Subscription.objects.count(), MediaAsset.objects.count(), ShopOrder.objects.count())
        self.at(self.subscription.trial_start_at + days(1))
        self.body()
        self.assertEqual(
            before, (Subscription.objects.count(), MediaAsset.objects.count(), ShopOrder.objects.count())
        )
        self.assertEqual(meters.ORDERS_INCLUDED_PER_MONTH, 500)
