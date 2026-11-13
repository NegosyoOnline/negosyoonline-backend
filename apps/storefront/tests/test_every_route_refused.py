"""Every storefront route is refused on a host that is not served.

The test walks the surface's own `urlpatterns`, so a route added later is covered without anyone
listing it. For each route it asks a suspended host, a trial that ended unpaid and a cancellation
past its service end, and expects the same 404 `not_found` the site call gives. The same walk on
a served host must get something else from most routes: that keeps the walk from passing only
because a switch is off or the path is wrong.
"""
import re

from django.test import override_settings

from apps.billing import services
from apps.billing.tests.base import (
    CYCLE_1_END, TRIAL_END, days, make_active, make_attention, make_subscription,
    make_suspended,
)
from apps.storefront import api as storefront
from apps.storefront.tests.base import HOST, NOT_FOUND, SECRET, SurfaceTestCase

METHODS = ("get", "post", "put", "patch", "delete")


def routes():
    """(path, name) for every route of the surface except the catch-all for unknown paths."""
    found = []
    for pattern in storefront.urlpatterns[0]:
        route = str(pattern.pattern)
        if not route:
            continue
        found.append(("/storefront/v1/" + re.sub(r"<[^>]+>", "1", route), pattern.name))
    return found


@override_settings(
    PACK_ENABLED=True, MEMBERS_ENABLED=True, SHOP_ENABLED=True, META_EVENTS_ENABLED=True,
    SANTE_CATALOG_ENABLED=True, PAYMENT_METHODS_ENABLED=True, SANTE_AGREEMENT_ENABLED=True,
)
class EveryRouteRefusedTests(SurfaceTestCase):
    def ask(self, method, path, host):
        return getattr(self.client, method)(
            path + f"?host={host}", {"host": host}, content_type="application/json", secure=True,
            HTTP_AUTHORIZATION=f"Bearer {SECRET}", HTTP_X_REAL_IP="203.0.113.7",
        ) if method != "get" else self.client.get(
            path, {"host": host}, secure=True, HTTP_AUTHORIZATION=f"Bearer {SECRET}",
            HTTP_X_REAL_IP="203.0.113.7",
        )

    def refused(self, response):
        return response.status_code == 404 and response.json() == NOT_FOUND

    def assert_every_route_refused(self, host):
        for path, name in routes():
            answers = {m: self.ask(m, path, host) for m in METHODS}
            with self.subTest(route=name, host=host):
                # a method the route does not have answers 405: only the ones it has are judged
                judged = {m: r for m, r in answers.items() if r.status_code != 405}
                self.assertTrue(judged, f"{path} answers 405 to every method")
                for method, response in judged.items():
                    self.assertTrue(
                        self.refused(response),
                        f"{method.upper()} {path} answered {response.status_code} on {host}: {response.content[:200]}",
                    )

    def served_answers(self, host):
        """How many routes answer something other than the not-found refusal on a served host.
        Routes about data the test site does not have (a seal, a member, a product) also answer
        404 there, so the control asks for most routes, not all."""
        count = 0
        for path, _ in routes():
            answers = [self.ask(m, path, host) for m in METHODS]
            if any(r.status_code != 405 and not self.refused(r) for r in answers):
                count += 1
        return count

    def assert_walk_reaches_routes(self):
        self.assertGreater(self.served_answers(HOST), len(routes()) * 0.6)

    def test_the_walk_reaches_the_routes_on_a_trial(self):
        make_subscription()
        self.at(TRIAL_END - days(1))

        self.assert_walk_reaches_routes()

    def test_the_walk_reaches_the_routes_on_a_paid_cycle(self):
        make_active()
        self.at(TRIAL_END + days(1))

        self.assert_walk_reaches_routes()

    def test_the_walk_reaches_the_routes_on_payment_requires_attention(self):
        make_attention()
        self.at(CYCLE_1_END + days(3))

        self.assert_walk_reaches_routes()

    def test_a_suspended_host(self):
        make_suspended()
        self.at(CYCLE_1_END + days(8))

        self.assert_every_route_refused(HOST)

    def test_a_trial_that_ended_with_no_payment(self):
        make_subscription()
        self.at(TRIAL_END)

        self.assert_every_route_refused(HOST)

    def test_a_cancellation_past_its_service_end(self):
        subscription = make_active()
        services.cancel(subscription, now=TRIAL_END + days(3))
        self.at(CYCLE_1_END)

        self.assert_every_route_refused(HOST)
