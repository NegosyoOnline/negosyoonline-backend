"""The Billing-only rule (L8, plan D11): once a site's subscription is suspended or ended, saving
the site and reading or saving its business profile answer 403 `billing_only`. The site list,
the site detail, the setup and the billing endpoints answer as always.

The billing clock is set by each test; none reads the real one.
"""
from unittest import mock

from django.test import override_settings

from apps.billing import services
from apps.billing.api import router as billing_router
from apps.billing.models import Subscription
from apps.billing.tests.base import days, method_on_file
from apps.catalog.api import router as catalog_router
from apps.meta_ads.api_owner import router as meta_router
from apps.meta_ads.tests.base import KEY as META_KEY, PIXEL as META_PIXEL
from apps.catalog.tests.base import catalog_call
from apps.pack.api_owner import router as pack_router
from apps.pack.tests.owner_base import pack_call
from apps.shop.api_owner import router as shop_router
from apps.shop.tests.owner_base import shop_call
from apps.payments.api import router as payments_router
from apps.accounts.tests.base import ORIGIN
from apps.common import private_files
from apps.payments.tests.base import PNG, payments_call, qr_file, use_local_private_store
from apps.sante.api import router as sante_router
from apps.sites.api import router as sites_router
from apps.sites.models import Site, SiteBusinessProfile
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.base import make_site
from apps.sites.tests.test_isolation import PROFILE_BODY, SITE_BODY, routes_under_a_site

# Every route under sites/{site_id}/ is in exactly one of these two lists.
TURNED_OFF = [
    ("PATCH", "/sites/{site_id}/"),
    ("GET", "/sites/{site_id}/business-profile/"),
    ("PATCH", "/sites/{site_id}/business-profile/"),
    ("GET", "/sites/{site_id}/business-profile/seal/"),
    ("POST", "/sites/{site_id}/business-profile/seal/"),
    ("DELETE", "/sites/{site_id}/business-profile/seal/"),
    ("GET", "/sites/{site_id}/sante/settings/"),
    ("PATCH", "/sites/{site_id}/sante/settings/"),
    ("GET", "/sites/{site_id}/catalog/products/"),
    ("POST", "/sites/{site_id}/catalog/products/"),
    ("PATCH", "/sites/{site_id}/catalog/products/{product_id}/"),
    ("GET", "/sites/{site_id}/catalog/products/{product_id}/revisions/"),
    ("POST", "/sites/{site_id}/catalog/products/{product_id}/image/"),
    ("DELETE", "/sites/{site_id}/catalog/products/{product_id}/image/"),
    ("GET", "/sites/{site_id}/payment-methods/"),
    ("PATCH", "/sites/{site_id}/payment-methods/"),
    ("POST", "/sites/{site_id}/payment-methods/accounts/"),
    ("POST", "/sites/{site_id}/payment-methods/accounts/{account_id}/"),
    ("DELETE", "/sites/{site_id}/payment-methods/accounts/{account_id}/"),
    ("GET", "/sites/{site_id}/pack/orders/"),
    ("GET", "/sites/{site_id}/pack/orders/{order_id}/"),
    ("GET", "/sites/{site_id}/pack/orders/{order_id}/proofs/{proof_id}/"),
    ("POST", "/sites/{site_id}/pack/orders/{order_id}/approve/"),
    ("POST", "/sites/{site_id}/pack/orders/{order_id}/reject/"),
    ("POST", "/sites/{site_id}/pack/orders/{order_id}/shipment/"),
    ("POST", "/sites/{site_id}/pack/orders/{order_id}/shipment/correct/"),
    ("POST", "/sites/{site_id}/pack/orders/{order_id}/delivery/"),
    ("GET", "/sites/{site_id}/shop/orders/"),
    ("GET", "/sites/{site_id}/shop/orders/{order_id}/"),
    ("GET", "/sites/{site_id}/shop/orders/{order_id}/proofs/{proof_id}/"),
    ("POST", "/sites/{site_id}/shop/orders/{order_id}/confirm-receipt/"),
    ("POST", "/sites/{site_id}/shop/orders/{order_id}/cancel/"),
    ("POST", "/sites/{site_id}/shop/orders/{order_id}/ship/"),
    ("POST", "/sites/{site_id}/shop/orders/{order_id}/deliver/"),
    ("GET", "/sites/{site_id}/meta/"),
    ("PUT", "/sites/{site_id}/meta/"),
    ("DELETE", "/sites/{site_id}/meta/token/"),
    ("POST", "/sites/{site_id}/meta/test/"),
]
# These answer 201 when they work.
CREATED = [
    ("POST", "/sites/{site_id}/catalog/products/"),
    ("POST", "/sites/{site_id}/payment-methods/accounts/"),
]
# A route that is let through but answers something else than 200 or 201 with the empty settings
# used here: sending the test event needs a saved Pixel, token and code first.
OTHER_WHEN_WORKING = {("POST", "/sites/{site_id}/meta/test/"): 409}
STAYS_ON = [
    ("GET", "/sites/{site_id}/"),
    ("GET", "/sites/{site_id}/billing/"),
    ("POST", "/sites/{site_id}/billing/cancel/"),
    ("POST", "/sites/{site_id}/billing/reactivate/"),
    # Paying by bank transfer (B2a): a suspended or ended site must still be able to pay.
    ("GET", "/sites/{site_id}/billing/payment/"),
    ("GET", "/sites/{site_id}/billing/usage/"),  # a closed site still sees what it owes
    ("GET", "/sites/{site_id}/billing/payments/"),
    ("POST", "/sites/{site_id}/billing/payments/"),
    ("GET", "/sites/{site_id}/billing/payments/{payment_id}/"),
    ("GET", "/sites/{site_id}/billing/payments/{payment_id}/proof/"),
    # The invoice copies (B5): billing records, like the payments, stay readable.
    ("GET", "/sites/{site_id}/billing/invoices/"),
    ("GET", "/sites/{site_id}/billing/invoices/{invoice_id}/copy/"),
]
MESSAGE = "This site is limited to Billing until its subscription continues."
SANTE_BODY = {"sponsor_id": "TAKEN-1"}


@override_settings(
    BILLING_ENABLED=True, SANTE_SETTINGS_ENABLED=True, SANTE_CATALOG_ENABLED=True,
    PAYMENT_METHODS_ENABLED=True, PACK_ENABLED=True, PACK_SHIPPING_ENABLED=True,
    SHOP_ENABLED=True, META_EVENTS_ENABLED=True, META_CREDENTIALS_KEY=META_KEY,
)
class BillingOnlyTestCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        use_local_private_store(self)
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        SiteBusinessProfile.objects.filter(site=self.site).update(  # a stored badge, so the seal GET has one to answer
            seal_key=private_files.save("sites/seal/site-1", PNG, "image/png"))
        self.subscription = Subscription.objects.get(site=self.site)
        self.start = self.subscription.trial_start_at
        self.trial_end = self.subscription.trial_end_at
        patcher = mock.patch("apps.billing.services.timezone")
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)
        self.at(self.start + days(1))

    def at(self, moment):
        self.clock.now.return_value = moment
        return moment

    def call(self, method, template, client=None, site_id=None):
        path = "/v1" + template.replace("{site_id}", site_id or self.site_id)
        if "/catalog/" in path:
            return catalog_call(self, method, path, client=client)
        if "/pack/" in path:
            return pack_call(self, method, path, client=client)
        if "/shop/" in path:
            return shop_call(self, method, path, client=client)
        if "/payment-methods/" in path:
            return payments_call(self, method, path, client=client)
        if path.endswith("/business-profile/seal/"):
            if method == "POST":
                return (client or self.client).post(
                    path, data={"seal": qr_file(name="seal.png")}, secure=True, HTTP_ORIGIN=ORIGIN)
            if method == "GET":  # the walk may have removed the badge just before
                SiteBusinessProfile.objects.filter(site__public_id=site_id or self.site_id, seal_key="").update(
                    seal_key=private_files.save("sites/seal/site-1", PNG, "image/png"))
        if method == "GET":
            return self.get(path, client=client)
        if "/meta/" in path:
            return self.send(method.lower(), path, {"pixel_id": META_PIXEL} if method == "PUT" else None, client=client)
        body = PROFILE_BODY if "business-profile" in path else SITE_BODY
        if "/sante/" in path:
            body = SANTE_BODY
        if "billing" in path:
            body = None
        return self.send(method.lower(), path, body, client=client)

    def assert_worked(self, method, template, response):
        expected = OTHER_WHEN_WORKING.get((method, template), 201 if (method, template) in CREATED else 200)
        self.assertEqual(response.status_code, expected, response.content)

    def pay_first_cycle(self):
        with method_on_file():
            services.first_payment_succeeded(
                self.subscription, paid_at=self.trial_end, reference="first-1"
            )
        return self.subscription.cycles.get(sequence=1)

    def suspend(self):
        """Paid once, the renewal failed, 7 days passed and the sweep wrote it."""
        cycle = self.pay_first_cycle()
        services.renewal_failed(self.subscription, at=cycle.end_at)
        services.sweep(now=cycle.end_at + days(7))
        return self.at(cycle.end_at + days(8))

    def rows(self):
        return (
            list(Site.objects.order_by("pk").values()),
            list(SiteBusinessProfile.objects.order_by("pk").values()),
        )

    def assert_turned_off(self, client=None, site_id=None):
        before = self.rows()
        for method, template in TURNED_OFF:
            with self.subTest(method=method, template=template):
                body = self.error(
                    self.call(method, template, client=client, site_id=site_id),
                    403, "billing_only",
                )
                self.assertEqual(body, {"code": "billing_only", "message": MESSAGE})
        self.assertEqual(self.rows(), before)

    def assert_working(self):
        for method, template in TURNED_OFF:
            with self.subTest(method=method, template=template):
                self.assert_worked(method, template, self.call(method, template))


class BillingOnlyTests(BillingOnlyTestCase):
    def test_a_suspended_site(self):
        self.suspend()

        self.assert_turned_off()

    def test_a_site_past_the_due_date_plus_7_days_even_if_the_sweep_never_ran(self):
        cycle = self.pay_first_cycle()
        self.at(cycle.end_at + days(7))

        self.assert_turned_off()
        self.assertEqual(Subscription.objects.get(pk=self.subscription.pk).state, "active")

    def test_a_trial_that_ended_with_no_payment_method_swept_or_not(self):
        self.at(self.trial_end)
        self.assert_turned_off()

        services.sweep(now=self.trial_end)
        self.at(self.trial_end + days(1))
        self.assert_turned_off()

    def test_a_cancelled_trial(self):
        services.cancel(self.subscription, now=self.start + days(2))
        self.at(self.start + days(2))

        self.assert_turned_off()

    def test_a_paid_cancellation_at_and_after_its_service_end(self):
        cycle = self.pay_first_cycle()
        services.cancel(self.subscription, now=self.trial_end + days(3))

        for moment in (cycle.end_at, cycle.end_at + days(30)):
            with self.subTest(moment=moment):
                self.at(moment)
                self.assert_turned_off()

    def test_everything_works_while_the_site_is_served(self):
        self.assert_working()  # on trial

        cycle = self.pay_first_cycle()
        self.at(self.trial_end + days(1))
        self.assert_working()  # paid
        self.at(cycle.end_at + days(7) - days(1))
        self.assert_working()  # past the cycle end, no outcome yet, inside the 7 days

        services.renewal_failed(self.subscription, at=cycle.end_at)
        self.assert_working()  # payment needs attention

    def test_everything_works_for_a_cancellation_inside_its_paid_period(self):
        cycle = self.pay_first_cycle()
        services.cancel(self.subscription, now=self.trial_end + days(3))
        self.at(cycle.end_at - days(1))

        self.assert_working()

    def test_an_exempt_site_is_never_billing_only(self):
        services.sweep(now=self.trial_end)
        Subscription.objects.filter(pk=self.subscription.pk).update(billing_exempt=True)
        self.at(self.trial_end + days(400))

        self.assert_working()

    def test_a_site_with_no_subscription_is_never_billing_only(self):
        self.subscription.transitions.all().delete()
        self.subscription.cycles.all().delete()
        self.subscription.delete()

        self.assert_working()

    def test_with_the_switch_off_nothing_is_refused(self):
        self.suspend()

        with override_settings(BILLING_ENABLED=False):
            self.assert_working()

    def test_another_accounts_site_still_answers_404_never_403(self):
        self.suspend()
        ben = self.other_client()

        for method, template in TURNED_OFF:
            with self.subTest(method=method, template=template):
                self.error(self.call(method, template, client=ben), 404, "not_found")

    def test_a_signed_out_request_still_answers_401(self):
        self.suspend()
        self.client.cookies.clear()

        for method, template in TURNED_OFF:
            with self.subTest(method=method, template=template):
                self.error(self.call(method, template), 401, "unauthenticated")

    def test_my_second_site_keeps_working_while_my_first_is_suspended(self):
        self.suspend()
        site = make_site(self.account, name="second-store", first=False)
        second = services.start_subscription(site, self.account)
        Subscription.objects.filter(pk=second.pk).update(billing_exempt=True)
        other = site.public_id

        self.assert_turned_off()
        for method, template in TURNED_OFF:
            with self.subTest(method=method, template=template):
                self.assert_worked(method, template, self.call(method, template, site_id=other))


class RouteInventoryTests(BillingOnlyTestCase):
    def routes(self):
        return (
            routes_under_a_site(sites_router)
            + routes_under_a_site(billing_router)
            + routes_under_a_site(sante_router)
            + routes_under_a_site(catalog_router)
            + routes_under_a_site(payments_router)
            + routes_under_a_site(pack_router)
            + routes_under_a_site(shop_router)
            + routes_under_a_site(meta_router)
        )

    def test_every_route_under_a_site_is_turned_off_or_allowed_by_name(self):
        self.assertEqual(
            sorted(self.routes()), sorted(TURNED_OFF + STAYS_ON),
            "A route under sites/{site_id}/ was added or removed: decide whether a suspended "
            "or ended site may use it, and list it in TURNED_OFF or STAYS_ON.",
        )
        self.assertFalse(set(TURNED_OFF) & set(STAYS_ON))

    def test_a_suspended_site_gets_403_on_the_first_list_and_never_on_the_second(self):
        self.suspend()

        for method, template in TURNED_OFF:
            with self.subTest(method=method, template=template):
                self.error(self.call(method, template), 403, "billing_only")
        for method, template in STAYS_ON:
            with self.subTest(method=method, template=template):
                response = self.call(method, template)
                self.assertNotEqual(response.status_code, 403, response.content)


class UnchangedEndpointsTests(BillingOnlyTestCase):
    """GET sites/, GET sites/{id}/ and GET account/setup/ answer for a suspended and for an
    ended site byte for byte what they answer for the same site on trial."""

    def answers(self):
        found = {}
        for path in ("/v1/sites/", f"/v1/sites/{self.site_id}/", "/v1/account/setup/"):
            response = self.get(path)
            self.assertEqual(response.status_code, 200, response.content)
            found[path] = response.content
        return found

    def test_a_suspended_site(self):
        on_trial = self.answers()

        self.suspend()

        self.assertEqual(self.answers(), on_trial)

    def test_an_ended_site(self):
        on_trial = self.answers()

        self.at(self.trial_end)
        self.assertEqual(self.answers(), on_trial)  # ended by the time, not yet swept

        services.sweep(now=self.trial_end)
        self.at(self.trial_end + days(1))
        self.assertEqual(self.answers(), on_trial)

    def test_a_cancelled_site_after_its_service_end(self):
        on_trial = self.answers()

        cycle = self.pay_first_cycle()
        services.cancel(self.subscription, now=self.trial_end + days(3))
        self.at(cycle.end_at)

        self.assertEqual(self.answers(), on_trial)

    def test_the_setup_carries_no_billing_state_or_new_blocked_reason(self):
        on_trial = self.get("/v1/account/setup/").json()

        self.suspend()
        suspended = self.get("/v1/account/setup/").json()

        self.assertEqual(suspended, on_trial)
        self.assertEqual(
            (suspended.get("state"), suspended.get("blocked_reason")),
            (on_trial.get("state"), on_trial.get("blocked_reason")),
        )
        self.assertNotIn("billing", str(suspended).lower())
