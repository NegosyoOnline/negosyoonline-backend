"""The sender: payload shape, hashing, retries, outcomes, idempotency, the sweep, the secret."""
import json
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.core.checks import run_checks
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing.tests.base import make_subscription
from apps.common import retention
from apps.meta_ads import crypto, events, send, services
from apps.meta_ads.models import MetaDelivery, MetaOrderContext, MetaSettings
from apps.payments.tests.base import use_local_private_store
from apps.meta_ads.tests.base import CODE, KEY, PIXEL, TOKEN, FakeMeta, configure, http_error, switched_on
from apps.meta_ads.tests.base import setUpModule, tearDownModule  # noqa: F401

EV = "pv_" + "a" * 16
CLIENT = {"ip": "203.0.113.9", "user_agent": "Mozilla/5.0 Visitor", "fbp": "", "fbc": "", "url": ""}


@switched_on
@override_settings(BILLING_ENABLED=True)
class DeliveryTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.site = make_subscription().site
        self.settings_row = configure(self.site)

    def queue(self, items=(("PageView", EV),), **client):
        with self.captureOnCommitCallbacks(execute=True):
            return services.queue_page_events(self.site, list(items), **{**CLIENT, **client})

    def delivery(self, name="PageView", event_id=EV, **fields):
        return MetaDelivery.objects.create(site=self.site, event_name=name, event_id=event_id, **fields)

    def page_event(self, **changes):
        return events.page_event("PageView", EV, now=timezone.now(), **{**CLIENT, **changes})


class PayloadTests(DeliveryTestCase):
    def test_the_request_shape_token_in_the_body_and_the_test_code(self):
        with FakeMeta(200) as meta:
            self.assertEqual(self.queue(url="https://x.test/p", fbp="fb.1.1700000000000.123456789"), 1)
        request, timeout = meta.requests[0]
        self.assertEqual(timeout, send.TIMEOUT_SECONDS)
        self.assertEqual(request.full_url, f"https://graph.facebook.com/{send.GRAPH_VERSION}/{PIXEL}/events")
        self.assertNotIn(TOKEN, request.full_url)
        self.assertEqual(request.get_method(), "POST")
        body = meta.bodies()[0]
        self.assertEqual((body["access_token"], body["test_event_code"]), (TOKEN, CODE))
        event = body["data"][0]
        self.assertEqual((event["event_name"], event["event_id"], event["action_source"]), ("PageView", EV, "website"))
        self.assertEqual(event["user_data"]["client_ip_address"], CLIENT["ip"])
        self.assertEqual(event["user_data"]["fbp"], "fb.1.1700000000000.123456789")
        self.assertEqual(event["event_source_url"], "https://x.test/p")
        self.assertNotIn("custom_data", event)
        delivery = MetaDelivery.objects.get()
        self.assertEqual((delivery.outcome, delivery.attempts, delivery.http_status, delivery.is_test), ("sent", 1, 200, True))

    def test_no_test_code_when_the_site_has_none(self):
        configure(self.site, code="")
        with FakeMeta(200) as meta:
            self.queue()
        self.assertNotIn("test_event_code", meta.bodies()[0])
        self.assertFalse(MetaDelivery.objects.get().is_test)

    def test_content_events_carry_the_server_value_and_currency(self):
        with FakeMeta(200) as meta:
            self.queue((("ViewContent", "vc_" + "b" * 16),))
        custom = meta.events()[0]["custom_data"]
        self.assertEqual((custom["value"], custom["currency"], custom["content_ids"]), (1488.0, "PHP", ["membership-pack"]))

    def test_hashes_are_normalised(self):
        self.assertEqual(events.hashed_email("  Maria@Buyer.TEST "), events.sha("maria@buyer.test"))
        self.assertEqual(events.hashed_mobile("+639171234567"), events.sha("639171234567"))
        user = events.user_data(ip="1.2.3.4", email="A@b.co", mobile="+639171234567")
        self.assertEqual(user["em"], [events.sha("a@b.co")])
        self.assertEqual(user["ph"], [events.sha("639171234567")])
        self.assertEqual(user["country"], [events.sha("ph")])
        self.assertNotIn("a@b.co", json.dumps(user))


class OutcomeTests(DeliveryTestCase):
    def test_a_5xx_is_retried_then_sent(self):
        delivery = self.delivery()
        event = self.page_event()
        with FakeMeta(http_error(503), http_error(500), 200):
            self.assertEqual(services.deliver(delivery.pk, event), services.RETRY)
            delivery.refresh_from_db()
            self.assertEqual((delivery.outcome, delivery.attempts, delivery.http_status), ("retrying", 1, 503))
            self.assertEqual(services.deliver(delivery.pk, event), services.RETRY)
            self.assertEqual(services.deliver(delivery.pk, event), "sent")
        delivery.refresh_from_db()
        self.assertEqual((delivery.outcome, delivery.attempts, delivery.http_status), ("sent", 3, 200))
        self.assertIsNotNone(delivery.finished_at)

    def test_the_third_failed_attempt_is_final(self):
        delivery = self.delivery()
        with FakeMeta(http_error(503)):
            outcomes = [services.deliver(delivery.pk, self.page_event()) for _ in range(3)]
        self.assertEqual(outcomes, [services.RETRY, services.RETRY, "failed"])
        self.assertEqual(MetaDelivery.objects.get().attempts, 3)

    def test_a_4xx_with_a_meta_code_is_final_failed_and_keeps_only_the_number(self):
        delivery = self.delivery()
        with FakeMeta(http_error(400, 190)):
            self.assertEqual(services.deliver(delivery.pk, self.page_event()), "failed")
        delivery.refresh_from_db()
        self.assertEqual((delivery.outcome, delivery.http_status, delivery.meta_error_code, delivery.attempts), ("failed", 400, 190, 1))

    def test_a_timeout_is_retried(self):
        delivery = self.delivery()
        with FakeMeta(TimeoutError("took too long")):
            self.assertEqual(services.deliver(delivery.pk, self.page_event()), services.RETRY)
        self.assertEqual(MetaDelivery.objects.get().outcome, "retrying")

    def test_an_event_older_than_seven_days_expires_unsent(self):
        delivery = self.delivery()
        old = events.page_event(
            "PageView", EV, now=timezone.now() - timedelta(days=8), url="", **{k: CLIENT[k] for k in ("ip", "user_agent", "fbp", "fbc")}
        )
        with FakeMeta(200) as meta:
            self.assertEqual(services.deliver(delivery.pk, old), "expired")
        self.assertEqual(meta.requests, [])

    def test_a_bot_and_a_request_with_no_address_are_skipped_unsent(self):
        with FakeMeta(200) as meta:
            self.queue((("PageView", EV),), user_agent="Mozilla/5.0 (compatible; Googlebot/2.1)")
            self.queue((("PageView", "pv_" + "c" * 16),), ip="")
            self.queue((("PageView", "pv_" + "d" * 16),), user_agent="")
        self.assertEqual(meta.requests, [])
        self.assertEqual(
            sorted(MetaDelivery.objects.values_list("outcome", flat=True)),
            ["skipped_bot", "skipped_no_client_data", "skipped_no_client_data"],
        )

    def test_a_repeated_event_id_sends_once(self):
        with FakeMeta(200) as meta:
            self.queue()
            self.queue()
        self.assertEqual(len(meta.requests), 1)
        self.assertEqual(MetaDelivery.objects.count(), 1)

    def test_a_finished_delivery_is_never_sent_again(self):
        delivery = self.delivery(outcome="sent")
        with FakeMeta(200) as meta:
            self.assertIsNone(services.deliver(delivery.pk, self.page_event()))
        self.assertEqual(meta.requests, [])

    def test_no_pixel_or_token_at_send_time_fails_it_and_a_site_without_them_queues_nothing(self):
        delivery = self.delivery()
        MetaSettings.objects.update(token_encrypted="")
        with FakeMeta(200) as meta:
            self.assertEqual(services.deliver(delivery.pk, self.page_event()), "failed")
            self.assertEqual(self.queue((("PageView", "pv_" + "e" * 16),)), 0)
        self.assertEqual(meta.requests, [])

    def test_a_wrong_key_fails_the_delivery_without_a_crash(self):
        delivery = self.delivery()
        with override_settings(META_CREDENTIALS_KEY=crypto.Fernet.generate_key().decode()), FakeMeta(200) as meta:
            self.assertEqual(services.deliver(delivery.pk, self.page_event()), "failed")
        self.assertEqual(meta.requests, [])

    def test_the_switch_off_sends_and_changes_nothing(self):
        delivery = self.delivery()
        with override_settings(META_EVENTS_ENABLED=False), FakeMeta(200) as meta:
            self.assertIsNone(services.deliver(delivery.pk, self.page_event()))
            self.assertEqual(services.queue_page_events(self.site, [("PageView", "pv_" + "f" * 16)], **CLIENT), 0)
            self.assertEqual(services.sweep(), {})
            self.assertEqual(services.pixel_id_for(self.site), "")
        self.assertEqual((meta.requests, MetaDelivery.objects.get().outcome), ([], "queued"))


class SecretTests(DeliveryTestCase):
    def test_the_token_is_in_no_row_log_task_payload_or_repr(self):
        self.settings_row.refresh_from_db()
        delivery = self.delivery()
        queued = []
        with self.assertLogs(level="DEBUG") as logs, FakeMeta(http_error(400, 190)), mock.patch(
            "apps.meta_ads.tasks.send_page_event.delay", side_effect=lambda *a: queued.append(a)
        ):
            services.deliver(delivery.pk, self.page_event())
            self.queue((("PageView", "pv_" + "9" * 16),))
            import logging
            logging.getLogger("apps.meta_ads").error("probe")
        for blob in (
            json.dumps(list(MetaDelivery.objects.values()), default=str),
            json.dumps(list(MetaSettings.objects.values()), default=str),
            "\n".join(logs.output), json.dumps(queued, default=str),
            repr(self.settings_row), str(self.settings_row),
        ):
            self.assertNotIn(TOKEN, blob)
        self.assertEqual(len(queued), 1)  # the page event itself was queued with its payload only
        self.assertNotIn("access_token", json.dumps(queued, default=str))
        self.assertNotIn(TOKEN, self.settings_row.token_encrypted)
        self.assertEqual(crypto.decrypt(self.settings_row.token_encrypted), TOKEN)

    def test_a_failure_in_the_sender_leaves_no_exception_text(self):
        delivery = self.delivery()
        with FakeMeta(RuntimeError("boom " + TOKEN)):
            services.deliver(delivery.pk, self.page_event())
        row = MetaDelivery.objects.get()
        self.assertNotIn(TOKEN, json.dumps(row.__dict__, default=str))
        self.assertNotIn("boom", json.dumps(row.__dict__, default=str))


class SweepAndChecksTests(DeliveryTestCase):
    def test_the_sweep_requeues_waiting_order_events_and_expires_old_rows(self):
        now = timezone.now()
        waiting = self.delivery("Purchase", "pur_pko_a", outcome="retrying")
        fresh = self.delivery("Purchase", "pur_pko_b")
        page = self.delivery("PageView", "pv_" + "1" * 16)
        old = self.delivery("Purchase", "pur_pko_c")
        done = self.delivery("Purchase", "pur_pko_d", outcome="sent")
        for row, age in ((waiting, 10), (page, 10), (old, 60 * 24 * 8), (done, 600)):
            MetaDelivery.objects.filter(pk=row.pk).update(created_at=now - timedelta(minutes=age))
        queued = []
        with mock.patch("apps.meta_ads.services._dispatch_order", side_effect=queued.append):
            self.assertEqual(services.sweep(now), {"requeued": 1, "expired": 1})
        self.assertEqual(queued, [waiting.pk])
        self.assertEqual(MetaDelivery.objects.get(pk=old.pk).outcome, "expired")
        self.assertEqual(MetaDelivery.objects.get(pk=fresh.pk).outcome, "queued")
        self.assertEqual(MetaDelivery.objects.get(pk=done.pk).outcome, "sent")

    def test_the_deploy_check_refuses_a_half_set_switch(self):
        def ids():
            return [m.id for m in run_checks(include_deployment_checks=True)]

        with override_settings(META_EVENTS_ENABLED=True, META_CREDENTIALS_KEY="", STOREFRONT_ENABLED=True):
            self.assertIn("meta.E001", ids())
        with override_settings(META_EVENTS_ENABLED=True, META_CREDENTIALS_KEY=KEY, STOREFRONT_ENABLED=False):
            self.assertIn("meta.E001", ids())
        with override_settings(META_EVENTS_ENABLED=True, META_CREDENTIALS_KEY=KEY, STOREFRONT_ENABLED=True):
            self.assertNotIn("meta.E001", ids())
        with override_settings(META_EVENTS_ENABLED=False, META_CREDENTIALS_KEY=""):
            self.assertNotIn("meta.E001", ids())

    def test_the_retention_rule_deletes_old_contexts_only_while_the_switch_is_on(self):
        from apps.pack.tests.owner_base import make_order

        use_local_private_store(self)
        order, _ = make_order(self.site)
        ctx = MetaOrderContext.objects.create(order=order, site=self.site, client_ip="203.0.113.9")
        MetaOrderContext.objects.filter(pk=ctx.pk).update(created_at=timezone.now() - timedelta(days=31))
        with override_settings(RETENTION_ENABLED=True):
            self.assertEqual(retention.run_all(only=["meta_order_contexts"], dry_run=True).counts["meta_order_contexts"], 1)
            with override_settings(META_EVENTS_ENABLED=False):
                retention.run_all(only=["meta_order_contexts"])
            self.assertEqual(MetaOrderContext.objects.count(), 1)
            retention.run_all(only=["meta_order_contexts"])
        self.assertEqual(MetaOrderContext.objects.count(), 0)
