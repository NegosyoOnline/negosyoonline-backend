"""The storefront events route and the site payload."""
from apps.meta_ads.models import MetaDelivery
from apps.meta_ads.tests.base import PIXEL, FakeMeta, configure, switched_on
from apps.meta_ads.tests.base import setUpModule, tearDownModule  # noqa: F401
from apps.pack.tests.base import HOST, NOT_FOUND, OTHER_HOST, PackTestCase
from apps.storefront.tests.base import SECRET

PATH = "/storefront/v1/meta/events/"
PV, VC, IC = "pv_" + "a" * 16, "vc_" + "b" * 16, "ic_" + "c" * 16
UA = "Mozilla/5.0 Visitor"


@switched_on
class MetaStorefrontTestCase(PackTestCase):
    def setUp(self):
        super().setUp()
        self.mine = configure(self.site)

    def events(self, *items, host=HOST, secret=SECRET, **fields):
        body = {
            "host": host, "events": [{"name": n, "id": i} for n, i in items],
            "url": f"https://{host}/funnel/membership-pack", "ip": "203.0.113.9", "user_agent": UA,
            "consent": True, **fields,
        }
        body = {k: v for k, v in body.items() if v is not None}  # None leaves the key out
        headers = {"HTTP_AUTHORIZATION": f"Bearer {secret}"} if secret else {}
        return self.client.post(PATH, body, content_type="application/json", secure=True, **headers)


class SitePayloadTests(MetaStorefrontTestCase):
    def test_the_site_payload_carries_the_pixel_id_or_nothing(self):
        self.assertEqual(self.call().json()["meta_pixel_id"], PIXEL)
        with self.settings(META_EVENTS_ENABLED=False):
            self.assertEqual(self.call().json()["meta_pixel_id"], "")
        self.assertEqual(self.call(host=OTHER_HOST).json()["meta_pixel_id"], "")  # another site: its own, none


class EventsRouteTests(MetaStorefrontTestCase):
    def test_consent_queues_the_event_for_this_site_only(self):
        configure(self.other_site, pixel="555555555")
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            response = self.events(("InitiateCheckout", IC), ("ViewContent", VC))
        self.assertEqual(response.json(), {"status": "queued"})
        self.assertEqual(MetaDelivery.objects.filter(site=self.site).count(), 2)
        self.assertEqual(MetaDelivery.objects.filter(site=self.other_site).count(), 0)
        self.assertTrue(all(f"/{PIXEL}/events" in r.full_url for r, _ in meta.requests))
        self.assertEqual([e["event_id"] for e in meta.events()], [IC, VC])
        self.assertEqual(meta.events()[0]["user_data"]["client_user_agent"], UA)

    def test_without_consent_nothing_is_stored_or_sent(self):
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            response = self.events(("ViewContent", VC), consent=False)
            absent = self.events(("ViewContent", VC), consent=None)
        self.assertEqual(response.json(), {"status": "ignored"})
        self.assertEqual(absent.json(), {"status": "ignored"})
        self.assertEqual((meta.requests, MetaDelivery.objects.count()), ([], 0))

    def test_a_page_view_is_never_queued_or_sent_from_the_server(self):
        """PageView is browser-only (Coach Ed, 2026-11-15): the route drops it without a row, a send or an
        error, so a storefront that still lists it (deploy-order skew) keeps its other events."""
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            alone = self.events(("PageView", PV))
            mixed = self.events(("PageView", PV), ("ViewContent", VC))
        self.assertEqual((alone.status_code, alone.json()), (200, {"status": "ignored"}))
        self.assertEqual(mixed.json(), {"status": "queued"})
        self.assertEqual([e["event_name"] for e in meta.events()], ["ViewContent"])
        self.assertEqual(list(MetaDelivery.objects.values_list("event_name", flat=True)), ["ViewContent"])
        self.assertFalse(MetaDelivery.objects.filter(event_name="PageView").exists())

    def test_purchase_and_add_payment_info_are_refused_by_name(self):
        for name in ("Purchase", "AddPaymentInfo", "Lead", "AddToCart", ""):
            with self.subTest(name=name), FakeMeta(200) as meta:
                response = self.events((name, "pur_x"))
                self.assertEqual(response.status_code, 422, response.content)
                self.assertEqual(response.json()["error"]["code"], "event_refused")
                self.assertEqual(meta.requests, [])
        self.assertEqual(MetaDelivery.objects.count(), 0)

    def test_a_caller_cannot_supply_value_currency_or_custom_data(self):
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            self.events(("ViewContent", VC), value=1, currency="USD", custom_data={"value": 1}, content_ids=["x"])
        custom = meta.events()[0]["custom_data"]
        self.assertEqual((custom["value"], custom["currency"], custom["content_ids"]), (1488.0, "PHP", ["membership-pack"]))

    def test_a_malformed_id_or_a_mismatched_prefix_is_dropped(self):
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            response = self.events(("ViewContent", "vc_zz"), ("ViewContent", IC), ("ViewContent", "x" * 90))
        self.assertEqual(response.json(), {"status": "ignored"})
        self.assertEqual(meta.requests, [])

    def test_an_address_on_another_host_is_not_sent_and_the_query_is_dropped(self):
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            self.events(("ViewContent", VC), url="https://evil.test/p?x=1")
            self.events(("ViewContent", "vc_" + "c" * 16), url=f"https://{HOST}/funnel/x?token=SECRET#frag")
        urls = [e.get("event_source_url") for e in meta.events()]
        self.assertEqual(urls, [None, f"https://{HOST}/funnel/x"])

    def test_unknown_host_foreign_secret_and_switch_off(self):
        self.assertEqual(self.events(("ViewContent", VC), host="nobody.negosyotest.com").json(), NOT_FOUND)
        self.assertEqual(self.events(("ViewContent", VC), secret="wrong" * 8).status_code, 401)
        with self.settings(META_EVENTS_ENABLED=False):
            self.assertEqual(self.events(("ViewContent", VC)).json(), NOT_FOUND)
        self.assertEqual(MetaDelivery.objects.count(), 0)

    def test_a_site_without_a_pixel_or_token_queues_nothing(self):
        configure(self.site, token="")
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.events(("ViewContent", VC)).json(), {"status": "ignored"})
        self.assertEqual((meta.requests, MetaDelivery.objects.count()), ([], 0))

    def test_too_many_events_in_one_call_are_refused(self):
        items = [("ViewContent", f"vc_{i:016x}") for i in range(6)]
        self.assertEqual(self.events(*items).status_code, 422)

    def test_the_route_is_throttled_per_address(self):
        for _ in range(120):
            self.events(("ViewContent", VC), consent=False)
        self.assertEqual(self.events(("ViewContent", VC), consent=False).status_code, 429)
