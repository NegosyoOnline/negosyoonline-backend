"""Purchase at onboarding: queued once per order when the buyer first opens the verify page, and
the receipt (events_received, fbtrace_id) kept from Meta's answer."""
import json
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from apps.meta_ads import events, services
from apps.meta_ads.models import MetaDelivery, MetaOrderContext
from apps.meta_ads.tests.base import TOKEN, FakeMeta, configure, http_error, switched_on
from apps.meta_ads.tests.base import setUpModule, tearDownModule  # noqa: F401
from apps.pack import constants as C
from apps.pack.models import PackOrder
from apps.pack.tests.base import HOST, OTHER_HOST, PackTestCase
from apps.storefront.tests.base import SECRET

PATH = "/storefront/v1/meta/purchase/"
UA = "Mozilla/5.0 Buyer"
FBP = "fb.1.1700000000000.123456789"
CONSENT = {"meta_consent": "1", "fbp": FBP, "user_agent": UA, "source_url": f"https://{HOST}/funnel/membership-pack/checkout"}
TRACE = "AbCdEf123_xyz-9"


@switched_on
class OnboardingPurchaseTests(PackTestCase):
    def setUp(self):
        super().setUp()
        configure(self.site)

    def order_placed(self, **fields):
        with FakeMeta(200):
            token, _ = self.placed(**(CONSENT if fields.get("consent", True) else {}))
        return token, PackOrder.objects.get()

    def visit(self, token, *, host=HOST, consent=True, secret=SECRET, **fields):
        body = {"host": host, "visitor_ip": "203.0.113.50", "token": token, "consent": consent, **fields}
        headers = {"HTTP_AUTHORIZATION": f"Bearer {secret}"} if secret else {}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(PATH, body, content_type="application/json", secure=True, **headers)

    def purchases(self):
        return MetaDelivery.objects.filter(event_name="Purchase")

    # -- the trigger ---------------------------------------------------------------------

    def test_the_first_visit_queues_exactly_one_purchase_and_tells_the_browser_to_fire(self):
        token, order = self.order_placed()
        with FakeMeta(200) as meta:
            response = self.visit(token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {
            "status": "queued", "event_id": f"pur_{order.public_id}", "first": True, "value": 1488.0,
            "currency": "PHP", "content_ids": ["membership-pack"],
        })
        self.assertEqual(self.purchases().count(), 1)
        self.assertEqual([e["event_name"] for e in meta.events()], ["Purchase"])
        self.assertEqual(meta.events()[0]["event_id"], response.json()["event_id"])

    def test_repeat_visits_and_approval_queue_nothing_more(self):
        from apps.accounts.models import Account
        from apps.pack import review

        token, order = self.order_placed()
        with FakeMeta(200) as meta:
            self.visit(token)
            again = self.visit(token)
            self.assertEqual(again.json()["status"], "ignored")
            self.assertFalse(again.json()["first"])
            with self.captureOnCommitCallbacks(execute=True):
                review.approve(Account.objects.get(pk=self.site.account_id), self.site, order.pk, state_version=order.state_version)
            self.visit(token)
        self.assertEqual(self.purchases().count(), 1)
        self.assertEqual(len(meta.requests), 1)

    def test_approval_alone_sends_no_purchase(self):
        from apps.accounts.models import Account
        from apps.pack import review

        _, order = self.order_placed()
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            review.approve(Account.objects.get(pk=self.site.account_id), self.site, order.pk, state_version=order.state_version)
        self.assertEqual((meta.requests, self.purchases().count()), ([], 0))

    def test_a_repeat_after_the_context_is_gone_is_ignored_and_never_first(self):
        token, _ = self.order_placed()
        with FakeMeta(200):
            self.visit(token)
        self.assertFalse(MetaOrderContext.objects.exists())  # deleted once Purchase was sent
        with FakeMeta(200) as meta:
            again = self.visit(token)
        self.assertEqual((again.json()["first"], meta.requests), (False, []))

    def test_a_visit_without_consent_queues_nothing(self):
        token, _ = self.order_placed()
        with FakeMeta(200) as meta:
            response = self.visit(token, consent=False)
        self.assertEqual(response.json(), {"status": "ignored", "event_id": "", "first": False, "value": 0.0, "currency": "", "content_ids": []})
        self.assertEqual((meta.requests, self.purchases().count()), ([], 0))
        self.assertTrue(MetaOrderContext.objects.exists())  # a later visit with consent can still fire

    def test_an_order_placed_without_consent_gets_no_purchase(self):
        token, _ = self.order_placed(consent=False)
        self.assertFalse(MetaOrderContext.objects.exists())
        with FakeMeta(200) as meta:
            response = self.visit(token)
        self.assertEqual((response.json()["first"], meta.requests, self.purchases().count()), (False, [], 0))

    def test_no_pixel_or_token_gives_nothing(self):
        token, _ = self.order_placed()
        configure(self.site, token="")
        with FakeMeta(200) as meta:
            response = self.visit(token)
        self.assertEqual((response.json()["first"], meta.requests, self.purchases().count()), (False, [], 0))
        configure(self.site, pixel="")
        with FakeMeta(200) as meta:
            self.assertFalse(self.visit(token).json()["first"])
        self.assertEqual(self.purchases().count(), 0)

    def test_meta_off_answers_the_404_of_an_unknown_site_and_sends_nothing(self):
        token, _ = self.order_placed()
        with self.settings(META_EVENTS_ENABLED=False), FakeMeta(200) as meta:
            response = self.visit(token)
        self.assertEqual(response.status_code, 404)
        self.assertEqual((meta.requests, self.purchases().count()), ([], 0))

    # -- isolation and the token -------------------------------------------------------------

    def test_another_sites_host_cannot_use_this_sites_token(self):
        token, _ = self.order_placed()
        configure(self.other_site, pixel="555555555")
        with FakeMeta(200) as meta:
            response = self.visit(token, host=OTHER_HOST)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "checkout_closed")
        self.assertEqual((meta.requests, MetaDelivery.objects.filter(site=self.other_site).count()), ([], 0))
        self.assertEqual(self.purchases().count(), 0)

    def test_a_bad_token_or_missing_secret_is_refused(self):
        token, _ = self.order_placed()
        self.assertEqual(self.visit("nope").status_code, 409)
        self.assertEqual(self.visit("").status_code, 409)
        self.assertIn(self.visit(token, secret=None).status_code, (401, 403))
        self.assertEqual(self.purchases().count(), 0)

    def test_a_token_that_has_not_placed_an_order_reaches_nothing(self):
        token = self.start()
        with FakeMeta(200) as meta:
            response = self.visit(token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual((meta.requests, self.purchases().count()), ([], 0))

    def test_the_browser_cannot_choose_the_order_value_or_id(self):
        token, order = self.order_placed()
        response = self.visit(token, event_id="pur_forged", value=1, order_id=999)
        self.assertEqual(response.json()["event_id"], f"pur_{order.public_id}")
        self.assertEqual(response.json()["value"], 1488.0)

    def test_the_route_is_throttled_per_address(self):
        token, _ = self.order_placed()
        for _ in range(60):
            self.visit(token, consent=False)
        self.assertEqual(self.visit(token, consent=False).status_code, 429)

    # -- the event -----------------------------------------------------------------------

    def test_the_event_has_the_orders_value_currency_id_and_the_visit_time(self):
        token, order = self.order_placed()
        before = timezone.now().replace(microsecond=0)
        with FakeMeta(200) as meta:
            self.visit(token)
        event = meta.events()[0]
        self.assertEqual((event["event_name"], event["event_id"]), ("Purchase", f"pur_{order.public_id}"))
        self.assertEqual((event["custom_data"]["value"], event["custom_data"]["currency"]), (1488.0, "PHP"))
        self.assertEqual(event["custom_data"]["order_id"], order.order_number)
        delivery = self.purchases().get()
        self.assertEqual(event["event_time"], int(delivery.created_at.timestamp()))
        self.assertGreaterEqual(event["event_time"], int(before.timestamp()))
        self.assertIsNone(PackOrder.objects.get(pk=order.pk).verified_at)  # not the approval time
        user = event["user_data"]
        self.assertEqual(user["em"], [events.sha(order.member.email)])
        self.assertEqual(user["ph"], [events.sha(order.member.mobile.lstrip("+"))])
        self.assertEqual((user["client_user_agent"], user["fbp"]), (UA, FBP))
        self.assertNotIn(order.member.email, json.dumps(event))
        self.assertEqual(delivery.outcome, "sent")

    def test_an_unverified_order_is_enough(self):
        token, order = self.order_placed()
        self.assertEqual(PackOrder.objects.get(pk=order.pk).payment_status, C.PENDING_REVIEW)
        with FakeMeta(200) as meta:
            self.visit(token)
        self.assertEqual(len(meta.requests), 1)

    def test_a_failing_meta_hook_never_breaks_the_page_call(self):
        token, _ = self.order_placed()
        with mock.patch("apps.meta_ads.services._queue_order_event", side_effect=RuntimeError(TOKEN)):
            with self.assertLogs("apps.meta_ads", level="ERROR") as logs:
                response = self.visit(token)
        self.assertEqual((response.status_code, response.json()["first"]), (200, False))
        self.assertNotIn(TOKEN, "\n".join(logs.output))

    def test_a_purchase_older_than_seven_days_expires_unsent(self):
        token, _ = self.order_placed()
        with FakeMeta(200):
            self.visit(token)
        MetaDelivery.objects.update(outcome="queued", created_at=timezone.now() - timedelta(days=8))
        MetaOrderContext.objects.create(order=PackOrder.objects.get(), site=self.site, client_ip="203.0.113.77", user_agent=UA)
        with FakeMeta(200) as again:
            self.assertEqual(services.deliver(self.purchases().get().pk), "expired")
        self.assertEqual(again.requests, [])


@switched_on
class ReceiptTests(PackTestCase):
    """events_received and fbtrace_id come from Meta's answer; nothing else of it is kept."""

    def setUp(self):
        super().setUp()
        configure(self.site)

    def sent(self, answer):
        with FakeMeta(answer):
            self.placed(**CONSENT)
        return MetaDelivery.objects.get()

    def test_a_sent_answer_saves_the_count_and_the_trace_id(self):
        row = self.sent((200, {"events_received": 1, "messages": ["SECRET META TEXT"], "fbtrace_id": TRACE}))
        self.assertEqual((row.outcome, row.events_received, row.fbtrace_id), ("sent", 1, TRACE))
        self.assertNotIn("SECRET META TEXT", json.dumps(list(MetaDelivery.objects.values()), default=str))

    def test_an_answer_without_a_body_saves_nothing_extra(self):
        row = self.sent(200)
        self.assertEqual((row.outcome, row.events_received, row.fbtrace_id), ("sent", None, ""))

    def test_odd_values_are_dropped_not_stored(self):
        row = self.sent((200, {"events_received": "1", "fbtrace_id": "bad id with spaces & text"}))
        self.assertEqual((row.events_received, row.fbtrace_id), (None, ""))

    def test_an_error_answer_saves_the_trace_id_and_the_numeric_code_only(self):
        row = self.sent(http_error(400, 190, TRACE))
        self.assertEqual((row.outcome, row.meta_error_code, row.fbtrace_id, row.events_received), ("failed", 190, TRACE, None))

    def test_a_retried_then_sent_row_keeps_the_final_receipt(self):
        with FakeMeta(http_error(503, None, "RetryTrace1"), (200, {"events_received": 1, "fbtrace_id": TRACE})):
            self.placed(**CONSENT)
            row = MetaDelivery.objects.get()
            if row.outcome != "sent":
                services.deliver(row.pk)
        row.refresh_from_db()
        self.assertEqual((row.outcome, row.events_received, row.fbtrace_id), ("sent", 1, TRACE))
