"""The storefront events route, the site payload and the Purchase integrity of the order hooks."""
from datetime import timedelta
from unittest import mock

from django.db import transaction
from django.utils import timezone

from apps.meta_ads import events
from apps.meta_ads.models import MetaDelivery, MetaOrderContext
from apps.meta_ads.tests.base import PIXEL, TOKEN, FakeMeta, configure, switched_on
from apps.pack import constants as C, review
from apps.pack.models import PackOrder
from apps.pack.tests.base import HOST, NOT_FOUND, OTHER_HOST, PackTestCase
from apps.storefront.tests.base import SECRET

PATH = "/storefront/v1/meta/events/"
PV, VC = "pv_" + "a" * 16, "vc_" + "b" * 16
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
            response = self.events(("PageView", PV), ("ViewContent", VC))
        self.assertEqual(response.json(), {"status": "queued"})
        self.assertEqual(MetaDelivery.objects.filter(site=self.site).count(), 2)
        self.assertEqual(MetaDelivery.objects.filter(site=self.other_site).count(), 0)
        self.assertTrue(all(f"/{PIXEL}/events" in r.full_url for r, _ in meta.requests))
        self.assertEqual([e["event_id"] for e in meta.events()], [PV, VC])
        self.assertEqual(meta.events()[0]["user_data"]["client_user_agent"], UA)

    def test_without_consent_nothing_is_stored_or_sent(self):
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            response = self.events(("PageView", PV), consent=False)
            absent = self.events(("PageView", PV), consent=None)
        self.assertEqual(response.json(), {"status": "ignored"})
        self.assertEqual(absent.json(), {"status": "ignored"})
        self.assertEqual((meta.requests, MetaDelivery.objects.count()), ([], 0))

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
            response = self.events(("PageView", "pv_zz"), ("PageView", VC), ("PageView", "x" * 90))
        self.assertEqual(response.json(), {"status": "ignored"})
        self.assertEqual(meta.requests, [])

    def test_an_address_on_another_host_is_not_sent_and_the_query_is_dropped(self):
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            self.events(("PageView", PV), url="https://evil.test/p?x=1")
            self.events(("PageView", "pv_" + "c" * 16), url=f"https://{HOST}/funnel/x?token=SECRET#frag")
        urls = [e.get("event_source_url") for e in meta.events()]
        self.assertEqual(urls, [None, f"https://{HOST}/funnel/x"])

    def test_unknown_host_foreign_secret_and_switch_off(self):
        self.assertEqual(self.events(("PageView", PV), host="nobody.negosyotest.com").json(), NOT_FOUND)
        self.assertEqual(self.events(("PageView", PV), secret="wrong" * 8).status_code, 401)
        with self.settings(META_EVENTS_ENABLED=False):
            self.assertEqual(self.events(("PageView", PV)).json(), NOT_FOUND)
        self.assertEqual(MetaDelivery.objects.count(), 0)

    def test_a_site_without_a_pixel_or_token_queues_nothing(self):
        configure(self.site, token="")
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.events(("PageView", PV)).json(), {"status": "ignored"})
        self.assertEqual((meta.requests, MetaDelivery.objects.count()), ([], 0))

    def test_too_many_events_in_one_call_are_refused(self):
        items = [("PageView", f"pv_{i:016x}") for i in range(6)]
        self.assertEqual(self.events(*items).status_code, 422)

    def test_the_route_is_throttled_per_address(self):
        for _ in range(120):
            self.events(("PageView", PV), consent=False)
        self.assertEqual(self.events(("PageView", PV), consent=False).status_code, 429)


class PurchaseIntegrityTests(MetaStorefrontTestCase):
    """Purchase is built in Django from the order row; nothing from a browser reaches it."""

    def verified_order(self):
        from apps.pack.tests.owner_base import make_order

        order, _ = make_order(self.site)
        MetaOrderContext.objects.create(
            order=order, site=self.site, client_ip="203.0.113.77", user_agent=UA,
            fbp="fb.1.1700000000000.123456789", source_url=f"https://{HOST}/funnel/membership-pack/checkout",
        )
        return order

    def approve(self, order):
        from apps.accounts.models import Account

        account = Account.objects.get(pk=self.site.account_id)
        with self.captureOnCommitCallbacks(execute=True):
            return review.approve(account, self.site, order.pk, state_version=order.state_version)

    def test_purchase_uses_the_orders_amount_currency_and_id_and_hashed_contact(self):
        order = self.verified_order()
        with FakeMeta(200) as meta:
            self.approve(order)
        event = meta.events()[0]
        self.assertEqual((event["event_name"], event["event_id"]), ("Purchase", f"pur_{order.public_id}"))
        self.assertEqual((event["custom_data"]["value"], event["custom_data"]["currency"]), (1488.0, "PHP"))
        self.assertEqual(event["custom_data"]["order_id"], order.order_number)
        self.assertEqual(event["event_time"], int(PackOrder.objects.get(pk=order.pk).verified_at.timestamp()))
        user = event["user_data"]
        self.assertEqual(user["em"], [events.sha(order.member.email)])
        self.assertEqual(user["ph"], [events.sha(order.member.mobile.lstrip("+"))])
        self.assertEqual((user["client_ip_address"], user["client_user_agent"]), ("203.0.113.77", UA))
        self.assertNotIn(order.member.email, str(event))
        self.assertEqual(MetaDelivery.objects.get().outcome, "sent")
        self.assertFalse(MetaOrderContext.objects.exists())  # its job is done

    def test_a_second_approval_sends_nothing_more(self):
        order = self.verified_order()
        with FakeMeta(200) as meta:
            self.approve(order)
            from apps.common.errors import ApiError

            with self.assertRaises(ApiError):
                self.approve(order)
        self.assertEqual(len(meta.requests), 1)
        self.assertEqual(MetaDelivery.objects.count(), 1)

    def test_approval_without_a_pixel_token_or_consent_queues_nothing(self):
        order = self.verified_order()
        configure(self.site, token="")
        with FakeMeta(200) as meta:
            self.approve(order)
        self.assertEqual((meta.requests, MetaDelivery.objects.count()), ([], 0))
        # no saved identifiers means the visitor gave no consent: nothing is sent either
        configure(self.site)
        other = self.verified_order()
        MetaOrderContext.objects.filter(order=other).delete()
        with FakeMeta(200) as meta:
            self.approve(other)
        self.assertEqual((meta.requests, MetaDelivery.objects.count()), ([], 0))

    def test_a_rolled_back_approval_queues_nothing(self):
        order = self.verified_order()
        from apps.accounts.models import Account

        account = Account.objects.get(pk=self.site.account_id)
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    review.approve(account, self.site, order.pk, state_version=order.state_version)
                    raise RuntimeError("undo")
            except RuntimeError:
                pass
        self.assertEqual((callbacks, meta.requests), ([], []))
        self.assertEqual(PackOrder.objects.get(pk=order.pk).payment_status, C.PENDING_REVIEW)

    def test_with_the_switch_off_approval_is_as_it_was(self):
        order = self.verified_order()
        with self.settings(META_EVENTS_ENABLED=False), FakeMeta(200) as meta:
            done = self.approve(order)
        self.assertEqual(done.payment_status, C.VERIFIED)
        self.assertEqual((meta.requests, MetaDelivery.objects.count()), ([], 0))

    def test_a_failing_meta_hook_never_breaks_the_approval(self):
        order = self.verified_order()
        with mock.patch("apps.meta_ads.services._queue_order_event", side_effect=RuntimeError(TOKEN)):
            with self.assertLogs("apps.meta_ads", level="ERROR") as logs:
                done = self.approve(order)
        self.assertEqual(done.payment_status, C.VERIFIED)
        self.assertNotIn(TOKEN, "\n".join(logs.output))

    def test_an_order_event_older_than_seven_days_expires(self):
        order = self.verified_order()
        with FakeMeta(200) as meta:
            self.approve(order)
        MetaDelivery.objects.update(outcome="queued")
        PackOrder.objects.filter(pk=order.pk).update(verified_at=timezone.now() - timedelta(days=8))
        MetaOrderContext.objects.create(order=order, site=self.site, client_ip="203.0.113.77", user_agent=UA)
        from apps.meta_ads import services

        with FakeMeta(200) as again:
            self.assertEqual(services.deliver(MetaDelivery.objects.get().pk), "expired")
        self.assertEqual(again.requests, [])
