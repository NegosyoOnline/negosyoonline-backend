"""Order placement: the consent gate, the saved browser identifiers and AddPaymentInfo."""
from unittest import mock

from apps.meta_ads.models import MetaDelivery, MetaOrderContext
from apps.meta_ads.tests.base import FakeMeta, configure, switched_on
from apps.pack.models import PackOrder
from apps.pack.tests.base import HOST, VISITOR, PackTestCase

UA = "Mozilla/5.0 Buyer"
FBP = "fb.1.1700000000000.123456789"
SOURCE = f"https://{HOST}/funnel/membership-pack/checkout"
CONSENT = {"meta_consent": "1", "fbp": FBP, "user_agent": UA, "source_url": SOURCE + "?email=a@b.co"}


@switched_on
class PlacementTests(PackTestCase):
    def setUp(self):
        super().setUp()
        configure(self.site)

    def test_with_consent_the_context_is_saved_and_add_payment_info_is_sent(self):
        with FakeMeta(200) as meta:
            self.placed(**CONSENT)
        order = PackOrder.objects.get()
        ctx = MetaOrderContext.objects.get()
        self.assertEqual((ctx.order_id, ctx.site_id, ctx.client_ip, ctx.fbp, ctx.user_agent), (order.pk, self.site.pk, VISITOR, FBP, UA))
        self.assertEqual(ctx.source_url, SOURCE)  # the query is gone
        delivery = MetaDelivery.objects.get()
        self.assertEqual((delivery.event_name, delivery.event_id, delivery.outcome), ("AddPaymentInfo", f"api_{order.public_id}", "sent"))
        event = meta.events()[0]
        self.assertEqual((event["custom_data"]["value"], event["custom_data"]["currency"]), (1488.0, "PHP"))
        self.assertEqual(event["event_time"], int(order.created_at.timestamp()))
        self.assertEqual(event["user_data"]["client_ip_address"], VISITOR)

    def test_without_consent_nothing_is_stored_or_sent(self):
        for fields in ({}, {**CONSENT, "meta_consent": "0"}, {**CONSENT, "meta_consent": ""}):
            with self.subTest(fields=fields), FakeMeta(200) as meta:
                MetaOrderContext.objects.all().delete()
                self.placed_fresh(fields)
                self.assertEqual((meta.requests, MetaOrderContext.objects.count(), MetaDelivery.objects.count()), ([], 0, 0))

    def placed_fresh(self, fields):
        n = PackOrder.objects.count() + 1
        token = self.start(email=f"buyer{n}@buyer.test", mobile=f"0917000{n:04d}")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place(token, submit_key=f"k{n}", **fields)
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_malformed_browser_id_is_not_kept(self):
        with FakeMeta(200):
            self.placed(**{**CONSENT, "fbp": "not-a-cookie", "source_url": "https://evil.test/x"})
        ctx = MetaOrderContext.objects.get()
        self.assertEqual((ctx.fbp, ctx.source_url), ("", ""))

    def test_a_site_without_a_pixel_or_token_keeps_nothing(self):
        configure(self.site, token="")
        with FakeMeta(200) as meta:
            self.placed(**CONSENT)
        self.assertEqual((meta.requests, MetaOrderContext.objects.count(), MetaDelivery.objects.count()), ([], 0, 0))

    def test_with_the_switch_off_placement_is_as_it_was(self):
        with self.settings(META_EVENTS_ENABLED=False), FakeMeta(200) as meta:
            self.placed(**CONSENT)
        self.assertEqual(PackOrder.objects.count(), 1)
        self.assertEqual((meta.requests, MetaOrderContext.objects.count(), MetaDelivery.objects.count()), ([], 0, 0))

    def test_a_failing_meta_hook_never_breaks_the_order(self):
        with mock.patch("apps.meta_ads.services.capture_order_context", side_effect=RuntimeError("x")):
            with self.assertLogs("apps.meta_ads", level="ERROR"):
                self.placed(**CONSENT)
        self.assertEqual(PackOrder.objects.count(), 1)

    def test_a_replay_adds_nothing(self):
        with FakeMeta(200) as meta:
            token, _ = self.placed(**CONSENT)
            with self.captureOnCommitCallbacks(execute=True):
                self.place(token, **CONSENT)
        self.assertEqual((len(meta.requests), MetaDelivery.objects.count(), MetaOrderContext.objects.count()), (1, 1, 1))

    def test_the_meta_fields_are_not_proof_data(self):
        # the proof rules are unchanged by the extra form fields: a normal order is still placed
        self.placed(**CONSENT)
        self.assertEqual(PackOrder.objects.get().payment_status, "pending_review")
