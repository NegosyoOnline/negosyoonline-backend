"""GET /v1/public/sites/by-host/: which site a storefront host serves. Unknown fails closed.

The billing clock is set by each test; none reads the real one.
"""
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing import services
from apps.billing.models import BillingCycle, Subscription, SubscriptionTransition
from apps.billing.tests.base import (
    CYCLE_1_END, SECOND, T0, TRIAL_END, days, make_active, make_attention, make_subscription,
    make_suspended,
)
from apps.sites.models import Site, Subdomain
from apps.sites.tests.base import make_account, make_site

PATH = "/v1/public/sites/by-host/"
HOST = "anas-store.negosyotest.com"
KEYS = {
    "id", "template", "display_name", "accent_color", "facebook_url", "instagram_url",
    "tiktok_url", "custom_host",
}
NOT_FOUND = {"error": {"code": "not_found", "message": "Not found."}}


@override_settings(BILLING_ENABLED=True, SITE_PARENT_HOST="negosyotest.com")
class ByHostTestCase(TestCase):
    def setUp(self):
        patcher = mock.patch("apps.billing.services.timezone")
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)
        self.at(T0 + days(1))

    def at(self, moment):
        self.clock.now.return_value = moment
        return moment

    def lookup(self, host=HOST):
        return self.client.get(PATH, {"host": host}, secure=True)

    def assert_served(self, response, max_age=60):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], f"public, max-age={max_age}")
        return response.json()

    def assert_not_found(self, response):
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(response.json(), NOT_FOUND)
        self.assertEqual(response["Cache-Control"], "public, max-age=30")
        return response.content


class ServedTests(ByHostTestCase):
    def test_a_site_on_trial_is_served_with_only_the_public_fields(self):
        subscription = make_subscription()
        Site.objects.filter(pk=subscription.site_id).update(
            accent_color="#1a7f37", facebook_url="https://facebook.com/anas",
            instagram_url="https://instagram.com/anas", tiktok_url="https://tiktok.com/@anas",
            contact_email="ana-store@example.test", contact_mobile="09171234567",
        )
        site = Site.objects.get(pk=subscription.site_id)

        body = self.assert_served(self.lookup())

        self.assertEqual(set(body), KEYS)
        self.assertEqual(body, {
            "id": site.public_id, "template": "sante", "display_name": "Ana's Store",
            "accent_color": "#1a7f37", "facebook_url": "https://facebook.com/anas",
            "instagram_url": "https://instagram.com/anas",
            "tiktok_url": "https://tiktok.com/@anas", "custom_host": "",
        })
        text = self.lookup().content.decode()
        for private in ("ana-store@example.test", "09171234567", "ana@example.test", "trial"):
            self.assertNotIn(private, text)

    def test_it_needs_no_sign_in_and_no_origin_and_writes_nothing(self):
        make_subscription()
        before = (
            list(Subscription.objects.values()), SubscriptionTransition.objects.count(),
            BillingCycle.objects.count(), list(Site.objects.values()),
        )

        self.assert_served(self.lookup())
        self.at(TRIAL_END)
        self.assert_not_found(self.lookup())

        self.assertEqual(before, (
            list(Subscription.objects.values()), SubscriptionTransition.objects.count(),
            BillingCycle.objects.count(), list(Site.objects.values()),
        ))

    def test_a_paid_site_is_served_through_its_cycle_and_the_7_days_after(self):
        make_active()

        for moment in (TRIAL_END + days(1), CYCLE_1_END, CYCLE_1_END + days(7) - days(1)):
            with self.subTest(moment=moment):
                self.at(moment)
                self.assert_served(self.lookup())

    def test_a_site_needing_payment_attention_is_served(self):
        make_attention()
        self.at(CYCLE_1_END + days(3))

        self.assert_served(self.lookup())

    def test_a_cancellation_is_served_inside_its_paid_period(self):
        subscription = make_active()
        services.cancel(subscription, now=TRIAL_END + days(3))
        self.at(CYCLE_1_END - days(1))

        self.assert_served(self.lookup())

    def test_an_exempt_site_is_served_whatever_its_rows_say(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END)
        Subscription.objects.filter(pk=subscription.pk).update(billing_exempt=True)
        self.at(TRIAL_END + days(400))

        self.assert_served(self.lookup())

    def test_a_port_a_trailing_dot_and_upper_case_are_accepted(self):
        subscription = make_subscription()

        for host in (
            HOST + ":443", HOST + ".", HOST + ".:8000", "Anas-Store.NegosyoTest.COM",
            "ANAS-STORE.NEGOSYOTEST.COM.:443",
        ):
            with self.subTest(host=host):
                self.assertEqual(
                    self.assert_served(self.lookup(host))["id"], subscription.site.public_id
                )

    def test_one_host_never_answers_with_another_site(self):
        ana = make_subscription()
        ben = make_subscription(email="ben@example.test", name="bens-store")

        self.assertEqual(self.lookup().json()["id"], ana.site.public_id)
        self.assertEqual(
            self.lookup("bens-store.negosyotest.com").json()["id"], ben.site.public_id
        )

    def test_the_path_is_in_the_openapi_description(self):
        paths = self.client.get("/v1/openapi.json", secure=True).json()["paths"]

        self.assertIn("get", paths["/v1" + PATH[3:]])


class MaxAgeTests(ByHostTestCase):
    """A cache may never keep a 200 past the moment the site would next go off."""

    def test_the_trial_end(self):
        make_subscription()

        for moment, expected in (
            (T0, 60), (TRIAL_END - days(1), 60), (TRIAL_END - 61 * SECOND, 60),
            (TRIAL_END - 60 * SECOND, 60), (TRIAL_END - 59 * SECOND, 59),
            (TRIAL_END - SECOND, 1),
        ):
            with self.subTest(moment=moment):
                self.at(moment)
                self.assert_served(self.lookup(), max_age=expected)

    def test_a_part_of_a_second_left_is_no_caching_at_all(self):
        make_subscription()
        self.at(TRIAL_END - SECOND / 2)

        self.assert_served(self.lookup(), max_age=0)

    def test_the_end_of_the_7_days_after_a_paid_cycle(self):
        make_active()
        self.at(CYCLE_1_END + days(7) - 10 * SECOND)

        self.assert_served(self.lookup(), max_age=10)

    def test_the_end_of_the_7_days_of_payment_attention(self):
        make_attention()
        self.at(CYCLE_1_END + days(7) - 25 * SECOND)

        self.assert_served(self.lookup(), max_age=25)

    def test_the_service_end_of_a_cancellation(self):
        subscription = make_active()
        services.cancel(subscription, now=TRIAL_END + days(3))
        self.at(CYCLE_1_END - 5 * SECOND)

        self.assert_served(self.lookup(), max_age=5)

    def test_an_exempt_site_has_no_boundary(self):
        subscription = make_subscription()
        Subscription.objects.filter(pk=subscription.pk).update(billing_exempt=True)
        self.at(TRIAL_END - SECOND)

        self.assert_served(self.lookup(), max_age=60)


class NotFoundTests(ByHostTestCase):
    def test_a_suspended_site(self):
        make_suspended()
        self.at(CYCLE_1_END + days(8))

        self.assert_not_found(self.lookup())

    def test_a_site_past_the_due_date_plus_7_days_even_if_the_sweep_never_ran(self):
        active = make_active()
        attention = make_attention(email="ben@example.test", name="bens-store")
        self.at(CYCLE_1_END + days(7))

        self.assert_not_found(self.lookup())
        self.assert_not_found(self.lookup("bens-store.negosyotest.com"))
        self.assertEqual(
            [Subscription.objects.get(pk=row.pk).state for row in (active, attention)],
            ["active", "payment_requires_attention"],
        )

    def test_a_cancellation_at_and_after_its_service_end(self):
        subscription = make_active()
        services.cancel(subscription, now=TRIAL_END + days(3))

        for moment in (CYCLE_1_END, CYCLE_1_END + days(30)):
            with self.subTest(moment=moment):
                self.at(moment)
                self.assert_not_found(self.lookup())

    def test_a_cancelled_trial(self):
        subscription = make_subscription()
        services.cancel(subscription, now=T0 + days(2))
        self.at(T0 + days(2))

        self.assert_not_found(self.lookup())

    def test_a_trial_that_ended_with_no_payment_method_swept_or_not(self):
        make_subscription()
        self.at(TRIAL_END)
        self.assert_not_found(self.lookup())

        services.sweep(now=TRIAL_END)
        self.at(TRIAL_END + days(1))
        self.assert_not_found(self.lookup())

    def test_a_removed_site(self):
        subscription = make_subscription()
        Site.objects.filter(pk=subscription.site_id).update(deleted_at=timezone.now())

        self.assert_not_found(self.lookup())

    def test_a_held_site(self):
        subscription = make_subscription()
        Site.objects.filter(pk=subscription.site_id).update(operator_hold=True)

        self.assert_not_found(self.lookup())

    def test_a_retired_name_and_an_operator_reserved_name(self):
        subscription = make_subscription()

        for state in (Subdomain.State.RETIRED, Subdomain.State.OPERATOR_RESERVED):
            with self.subTest(state=state):
                Subdomain.objects.filter(site=subscription.site).update(state=state)
                self.assert_not_found(self.lookup())

    def test_a_retired_name_does_not_serve_the_site_under_its_new_name_either_way(self):
        subscription = make_subscription()
        Subdomain.objects.filter(site=subscription.site).update(state=Subdomain.State.RETIRED)
        Subdomain.objects.create(name="anas-shop", site=subscription.site)

        self.assert_not_found(self.lookup())
        self.assert_served(self.lookup("anas-shop.negosyotest.com"))

    def test_a_reserved_name_even_when_a_serving_site_holds_it(self):
        for number, name in enumerate(("www", "api", "sante", "w-w-w")):
            with self.subTest(name=name):
                make_subscription(email=f"owner{number}@example.test", name=name)
                self.assert_not_found(self.lookup(f"{name}.negosyotest.com"))

    def test_an_unknown_name(self):
        make_subscription()

        self.assert_not_found(self.lookup("nobody-here.negosyotest.com"))

    def test_a_host_outside_the_parent(self):
        make_subscription()

        for host in (
            "anas-store.example.com", "anas-store.negosyotest.com.example.com",
            "anas-store.evilnegosyotest.com", "anas-storenegosyotest.com", "anas-store",
            "negosyotest.com", "anas-store.negosyotest.co", "anas-store.negosyonline.com",
        ):
            with self.subTest(host=host):
                self.assert_not_found(self.lookup(host))

    def test_a_malformed_host(self):
        make_subscription()

        for host in (
            HOST + ":80a", HOST + ":", HOST + ":443:443", HOST + ":-1", HOST + ":٤٤٣",
            "shop.anas-store.negosyotest.com", "anas-store.shop.negosyotest.com",
            "anas store.negosyotest.com", " " + HOST, HOST + " ", "anas-store .negosyotest.com",
            ".negosyotest.com", "..negosyotest.com", HOST + "..", "anas_store.negosyotest.com",
            "anas--store.negosyotest.com", "-anas-store.negosyotest.com",
            "http://" + HOST, HOST + "/", HOST + "?x=1", "anas-store%2enegosyotest.com",
            "anas-store.negosyotest.com\n", "ánas-store.negosyotest.com",
            "a" * 300 + ".negosyotest.com", "%", "*.negosyotest.com", "_",
        ):
            with self.subTest(host=host):
                self.assert_not_found(self.lookup(host))

    def test_a_missing_or_empty_host(self):
        make_subscription()

        self.assert_not_found(self.client.get(PATH, secure=True))
        self.assert_not_found(self.lookup(""))
        self.assert_not_found(self.client.get(PATH, {"hostname": HOST}, secure=True))

    def test_a_site_with_no_subscription(self):
        make_site(make_account())

        self.assert_not_found(self.lookup())

    def test_the_switch_off(self):
        make_subscription()

        with override_settings(BILLING_ENABLED=False):
            self.assert_not_found(self.lookup())

    def test_an_empty_parent_host(self):
        make_subscription()

        with override_settings(SITE_PARENT_HOST=""):
            for host in (HOST, "anas-store.", "anas-store"):
                with self.subTest(host=host):
                    self.assert_not_found(self.lookup(host))

    def test_an_error_while_reading_the_billing_position(self):
        make_subscription()

        with mock.patch.object(services, "_read_position", side_effect=RuntimeError("boom")):
            with self.assertLogs("apps.billing.services", level="ERROR"):
                self.assert_not_found(self.lookup())

    def test_every_refusal_is_byte_for_byte_the_same(self):
        make_subscription()
        suspended = make_suspended(email="ben@example.test", name="bens-store")
        removed = make_subscription(email="cora@example.test", name="coras-store")
        Site.objects.filter(pk=removed.site_id).update(deleted_at=timezone.now())
        held = make_subscription(email="dan@example.test", name="dans-store")
        Site.objects.filter(pk=held.site_id).update(operator_hold=True)
        make_site(make_account("eve@example.test"), name="eves-store")
        self.at(CYCLE_1_END + days(8))  # Ana's trial ended with no method; Ben is suspended

        answers = {
            self.assert_not_found(self.lookup(host))
            for host in (
                HOST, "bens-store.negosyotest.com", "coras-store.negosyotest.com",
                "dans-store.negosyotest.com", "eves-store.negosyotest.com",
                "nobody-here.negosyotest.com", "www.negosyotest.com", "anas-store.example.com",
                HOST + ":80a", "",
            )
        }
        answers.add(self.assert_not_found(self.client.get(PATH, secure=True)))
        with override_settings(BILLING_ENABLED=False):
            answers.add(self.assert_not_found(self.lookup()))
        with override_settings(SITE_PARENT_HOST=""):
            answers.add(self.assert_not_found(self.lookup()))

        self.assertEqual(len(answers), 1)
        self.assertEqual(Subscription.objects.get(pk=suspended.pk).state, "suspended")
