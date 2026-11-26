"""The surface serves a host exactly when GET /v1/public/sites/by-host/ does.

Each case sets up a state, then asks both for the same host at the same moment. The surface
adds one rule of its own: the site must be a Santé site.
"""
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from apps.billing import services
from apps.billing.models import Subscription
from apps.billing.tests.base import (
    CYCLE_1_END, SECOND, T0, TRIAL_END, days, make_active, make_attention, make_subscription,
    make_suspended,
)
from apps.sites.models import Site, Subdomain
from apps.sites.tests.base import make_account, make_site
from apps.storefront.tests.base import HOST, NOT_FOUND, SECRET, SurfaceTestCase

BY_HOST = "/v1/public/sites/by-host/"


class ServedParityTests(SurfaceTestCase):
    def both(self, host=HOST):
        """Whether by-host and the surface serve this host; the test fails if they differ."""
        public = self.client.get(BY_HOST, {"host": host}, secure=True)
        surface = self.call(host)
        self.assertIn(public.status_code, (200, 404))
        self.assertEqual(surface.status_code, public.status_code, f"{host!r} differs")
        if surface.status_code == 200:
            self.assertEqual(surface.json()["id"], public.json()["id"])
        else:
            self.assert_answer(surface, 404, NOT_FOUND)
        self.assertEqual(self.contact(host).status_code, public.status_code, f"{host!r} differs")
        return surface.status_code == 200

    def contact(self, host):
        """The contact call for this host, with the form's hidden field filled: it answers
        whether the host is served and sends nothing."""
        return self.client.post(
            "/storefront/v1/contact/", {"host": host, "website": "x"},
            content_type="application/json", secure=True, HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )

    def assert_served(self, host=HOST):
        self.assertTrue(self.both(host), f"{host!r} is not served")

    def assert_not_served(self, host=HOST):
        self.assertFalse(self.both(host), f"{host!r} is served")

    # --- served ----------------------------------------------------------------------------

    def test_a_trial_up_to_its_last_second(self):
        make_subscription()

        for moment in (T0, TRIAL_END - days(1), TRIAL_END - SECOND):
            with self.subTest(moment=moment):
                self.at(moment)
                self.assert_served()

    def test_a_paid_site_through_its_cycle_and_the_7_days_after(self):
        make_active()

        for moment in (TRIAL_END + days(1), CYCLE_1_END, CYCLE_1_END + days(7) - SECOND):
            with self.subTest(moment=moment):
                self.at(moment)
                self.assert_served()

    def test_payment_attention_and_a_cancellation_inside_its_paid_period(self):
        make_attention()
        self.at(CYCLE_1_END + days(3))
        self.assert_served()

        cancelled = make_active(email="ben@example.test", name="bens-store")
        services.cancel(cancelled, now=TRIAL_END + days(3))
        self.at(CYCLE_1_END - days(1))
        self.assert_served("bens-store.negosyotest.com")

    def test_an_exempt_site(self):
        subscription = make_subscription()
        services.sweep(now=TRIAL_END)
        Subscription.objects.filter(pk=subscription.pk).update(billing_exempt=True)
        self.at(TRIAL_END + days(400))

        self.assert_served()

    def test_a_port_a_trailing_dot_and_upper_case(self):
        make_subscription()

        for host in (
            HOST + ":443", HOST + ":3002", HOST + ".", HOST + ".:8000",
            "Anas-Store.NegosyoTest.COM", "ANAS-STORE.NEGOSYOTEST.COM.:443",
        ):
            with self.subTest(host=host):
                self.assert_served(host)

    # --- not served ------------------------------------------------------------------------

    def test_a_trial_that_ended_and_a_cancelled_trial(self):
        ended = make_subscription()
        cancelled = make_subscription(email="ben@example.test", name="bens-store")
        services.cancel(cancelled, now=T0 + days(2))

        self.at(T0 + days(2))
        self.assert_served()
        self.assert_not_served("bens-store.negosyotest.com")
        self.at(TRIAL_END)
        self.assert_not_served()
        self.assertEqual(Subscription.objects.get(pk=ended.pk).state, "trial_active")  # never swept

    def test_a_suspended_site_and_one_past_the_7_days_that_was_never_swept(self):
        make_suspended()
        make_attention(email="ben@example.test", name="bens-store")
        self.at(CYCLE_1_END + days(8))

        self.assert_not_served()
        self.assert_not_served("bens-store.negosyotest.com")

    def test_a_cancellation_at_its_service_end(self):
        subscription = make_active()
        services.cancel(subscription, now=TRIAL_END + days(3))
        self.at(CYCLE_1_END)

        self.assert_not_served()

    def test_a_removed_site_and_a_held_site(self):
        removed = make_subscription()
        held = make_subscription(email="ben@example.test", name="bens-store")
        self.assert_served()
        self.assert_served("bens-store.negosyotest.com")

        Site.objects.filter(pk=removed.site_id).update(deleted_at=timezone.now())
        Site.objects.filter(pk=held.site_id).update(operator_hold=True)

        self.assert_not_served()
        self.assert_not_served("bens-store.negosyotest.com")

    def test_a_retired_name_an_operator_reserved_name_and_the_new_name(self):
        subscription = make_subscription()

        for state in (Subdomain.State.RETIRED, Subdomain.State.OPERATOR_RESERVED):
            with self.subTest(state=state):
                Subdomain.objects.filter(site=subscription.site).update(state=state)
                self.assert_not_served()
        Subdomain.objects.create(name="anas-shop", site=subscription.site)
        self.assert_not_served()
        self.assert_served("anas-shop.negosyotest.com")

    def test_a_reserved_name_with_no_active_row_is_not_served(self):
        # LABEL-R2 (2026-11-26): an ACTIVE row on a reserved name is an operator assignment and is
        # served (next test); with no such row the name is still not served.
        make_subscription()
        for name in ("www", "api", "sante", "w-w-w"):
            with self.subTest(name=name):
                self.assert_not_served(f"{name}.negosyotest.com")

    def test_an_operator_assigned_reserved_name_is_served_on_every_surface(self):
        make_subscription(name="santebarley")
        self.assert_served("santebarley.negosyotest.com")

    def test_a_site_with_no_subscription_and_an_unknown_name(self):
        make_site(make_account())

        self.assert_not_served()
        self.assert_not_served("nobody-here.negosyotest.com")

    def test_a_host_outside_the_parent(self):
        make_subscription()

        for host in (
            "anas-store.example.com", "anas-store.negosyotest.com.example.com",
            "anas-store.evilnegosyotest.com", "anas-storenegosyotest.com", "anas-store",
            "negosyotest.com", "anas-store.negosyotest.co", "anas-store.negosyonline.com",
            "www.santetest.com",
        ):
            with self.subTest(host=host):
                self.assert_not_served(host)

    def test_a_malformed_host(self):
        make_subscription()

        for host in (
            HOST + ":80a", HOST + ":", HOST + ":443:443", HOST + ":-1", HOST + ":٤٤٣",
            "shop.anas-store.negosyotest.com", "anas-store.shop.negosyotest.com",
            "anas store.negosyotest.com", " " + HOST, HOST + " ", ".negosyotest.com",
            HOST + "..", "anas_store.negosyotest.com", "anas--store.negosyotest.com",
            "-anas-store.negosyotest.com", "http://" + HOST, HOST + "/", HOST + "?x=1",
            "anas-store%2enegosyotest.com", "anas-store.negosyotest.com\n",
            "ánas-store.negosyotest.com", "a" * 300 + ".negosyotest.com", "%",
            "*.negosyotest.com", "_", "",
        ):
            with self.subTest(host=host):
                self.assert_not_served(host)

    def test_billing_off_and_an_empty_parent_host(self):
        make_subscription()

        with override_settings(BILLING_ENABLED=False):
            self.assert_not_served()
        with override_settings(SITE_PARENT_HOST=""):
            self.assert_not_served()
        self.assert_served()

    def test_an_error_while_reading_the_billing_position(self):
        make_subscription()

        with mock.patch.object(services, "_read_position", side_effect=RuntimeError("boom")):
            with self.assertLogs("apps.billing.services", level="ERROR"):
                self.assert_not_served()

    def test_every_refusal_of_the_surface_is_byte_for_byte_the_same(self):
        make_subscription()
        make_suspended(email="ben@example.test", name="bens-store")
        removed = make_subscription(email="cora@example.test", name="coras-store")
        Site.objects.filter(pk=removed.site_id).update(deleted_at=timezone.now())
        held = make_subscription(email="dan@example.test", name="dans-store")
        Site.objects.filter(pk=held.site_id).update(operator_hold=True)
        other = make_subscription(email="eve@example.test", name="eves-store")
        Site.objects.filter(pk=other.site_id).update(template="other")
        self.at(CYCLE_1_END + days(8))

        answers = {
            self.assert_answer(self.call(host), 404, NOT_FOUND)
            for host in (
                HOST, "bens-store.negosyotest.com", "coras-store.negosyotest.com",
                "dans-store.negosyotest.com", "eves-store.negosyotest.com",
                "nobody-here.negosyotest.com", "www.negosyotest.com", "anas-store.example.com",
                HOST + ":80a", "", None,
            )
        }

        self.assertEqual(len(answers), 1)


class SanteOnlyTests(SurfaceTestCase):
    """The one place the surface is stricter than by-host."""

    def test_a_served_site_of_another_template_is_not_found_on_the_surface(self):
        subscription = make_subscription()
        Site.objects.filter(pk=subscription.site_id).update(template="other")

        public = self.client.get(BY_HOST, {"host": HOST}, secure=True)

        self.assertEqual(public.status_code, 200)
        self.assert_answer(self.call(), 404, NOT_FOUND)
