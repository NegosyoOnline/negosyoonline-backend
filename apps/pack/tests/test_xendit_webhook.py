"""The Xendit webhook and the return page's status read: only an authenticated event, or the
server's own read of the invoice at Xendit, can mark a Membership Pack order paid."""
import json
from decimal import Decimal

from django.core import mail
from django.test import override_settings

from apps.pack import constants as C
from apps.pack.models import PackOrder, SanteRegistration
from apps.pack.tests.base import HOST, NAME, SECRET
from apps.pack.tests.xendit_base import XenditPackCase, TOKEN, fake_xendit, give_key, post_webhook
from apps.payments.models import XenditEvent
from apps.sante.tests.base import retemplate


def verified_mail(case):
    return [m for m in mail.outbox if "is verified" in m.subject]


class WebhookTests(XenditPackCase):
    def test_a_paid_event_marks_the_order_verified_turns_benefits_on_and_starts_the_registration(self):
        token, _, order = self.placed_xendit()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.webhook(order)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"received": True})
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)
        self.assertEqual(order.payment_method, "xendit")
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(order.paid_at.isoformat(), "2026-11-18T10:00:00+00:00")  # Xendit's own time
        self.assertIsNotNone(order.benefits_active_at)
        self.assertIsNotNone(order.verified_at)
        self.assertEqual(SanteRegistration.objects.get().status, "awaiting_buyer_details")
        self.assertEqual([e.kind for e in order.events.all()], ["placed", "xendit_paid"])
        event = XenditEvent.objects.get()
        self.assertEqual((event.outcome, event.source, event.order_ref), ("applied", "webhook", order.pk))

    def test_the_buyer_gets_the_existing_payment_verified_email_and_nothing_else(self):
        _, _, order = self.placed_xendit()
        with self.captureOnCommitCallbacks(execute=True):
            self.webhook(order)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["maria@buyer.test"])
        self.assertEqual(message.subject, f"Your payment for order {order.order_number} is verified")
        self.assertIn("The store verified your payment", message.body)

    def test_settled_counts_as_paid_too(self):
        _, _, order = self.placed_xendit()
        self.webhook(order, status="SETTLED")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)

    def test_a_wrong_token_is_401_and_changes_nothing(self):
        _, _, order = self.placed_xendit()
        before = list(PackOrder.objects.values())
        for token in ("wrong-token", "", TOKEN + "x", TOKEN[:-1]):
            response = self.webhook(order, token=token)
            self.assertEqual(response.status_code, 401, response.content)
            self.assertEqual(response.json()["error"]["code"], "invalid_token")
        self.assertEqual(list(PackOrder.objects.values()), before)
        self.assertEqual(XenditEvent.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_missing_token_header_is_401(self):
        _, _, order = self.placed_xendit()
        response = self.webhook(order, token=None)
        self.assertEqual(response.status_code, 401)

    def test_the_token_is_compared_in_constant_time(self):
        import hmac
        from unittest import mock
        _, _, order = self.placed_xendit()
        with mock.patch("apps.payments.webhooks.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            self.webhook(order)
        self.assertTrue(compare.called)

    def test_an_unknown_key_is_404(self):
        _, _, order = self.placed_xendit()
        response = self.webhook(order, key="no-such-key")
        self.assertEqual(response.status_code, 404)

    def test_nothing_is_processed_while_the_platform_switch_is_off(self):
        _, _, order = self.placed_xendit()
        with override_settings(XENDIT_ENABLED=False):
            response = self.webhook(order)
        self.assertEqual(response.status_code, 404)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)
        self.assertEqual(XenditEvent.objects.count(), 0)

    def test_a_site_that_is_not_a_sante_site_is_404(self):
        _, _, order = self.placed_xendit()
        retemplate(self.site)
        self.assertEqual(self.webhook(order).status_code, 404)

    def test_a_deleted_site_is_404(self):
        from django.utils import timezone
        _, _, order = self.placed_xendit()
        type(self.site).objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())
        self.assertEqual(self.webhook(order).status_code, 404)

    def test_a_cleared_token_is_404(self):
        _, _, order = self.placed_xendit()
        self.cred.webhook_token_ciphertext = ""
        self.cred.save()
        self.assertEqual(self.webhook(order).status_code, 404)

    def test_an_unreadable_token_is_503_and_changes_nothing(self):
        _, _, order = self.placed_xendit()
        self.cred.webhook_token_ciphertext = "not-a-valid-ciphertext"
        self.cred.save()
        self.assertEqual(self.webhook(order).status_code, 503)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)

    def test_a_body_over_64_kb_is_413_after_the_token_check(self):
        _, _, order = self.placed_xendit()
        big = {"padding": "x" * 70000}
        self.assertEqual(self.webhook(big, token="wrong").status_code, 401)
        self.assertEqual(self.webhook(big).status_code, 413)

    def test_a_body_that_is_not_json_or_not_an_invoice_is_400(self):
        _, _, order = self.placed_xendit()
        for raw in ("not json", "[]", "{}", json.dumps({"id": "x"}), json.dumps("string")):
            with self.subTest(raw=raw):
                response = post_webhook(self.client, self.cred, None, raw=raw)
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(XenditEvent.objects.count(), 0)


class DuplicateTests(XenditPackCase):
    def test_a_duplicate_event_answers_200_and_has_no_second_effect(self):
        _, _, order = self.placed_xendit()
        with self.captureOnCommitCallbacks(execute=True):
            first = self.webhook(order, webhook_id="evt-1")
        with self.captureOnCommitCallbacks(execute=True):
            second = self.webhook(order, webhook_id="evt-1")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(XenditEvent.objects.count(), 1)
        self.assertEqual(len(verified_mail(self)), 1)
        self.assertEqual(order.events.filter(kind="xendit_paid").count(), 1)
        self.assertEqual(SanteRegistration.objects.count(), 1)

    def test_the_event_id_falls_back_to_the_invoice_and_status(self):
        _, _, order = self.placed_xendit()
        self.webhook(order)
        order.refresh_from_db()
        self.assertEqual(XenditEvent.objects.get().provider_event_id, f"{order.xendit_invoice_id}:PAID")

    def test_the_same_paid_event_under_another_id_changes_nothing_more(self):
        _, _, order = self.placed_xendit()
        with self.captureOnCommitCallbacks(execute=True):
            self.webhook(order, webhook_id="evt-1")
            self.webhook(order, webhook_id="evt-2")
        self.assertEqual(order.events.filter(kind="xendit_paid").count(), 1)
        self.assertEqual(len(verified_mail(self)), 1)
        self.assertEqual(sorted(XenditEvent.objects.values_list("outcome", flat=True)), ["applied", "duplicate_state"])

    def test_a_concurrent_delivery_that_lost_the_insert_race_rolls_everything_back_and_answers_200(self):
        from unittest import mock
        _, _, order = self.placed_xendit()
        # the winner's event row is already there, but this request's early check did not see it
        XenditEvent.objects.create(
            site=self.site, provider_event_id="evt-race", event_type="invoice", external_id="x", body_sha256="0" * 64,
            outcome="applied",
        )
        real_filter = XenditEvent.objects.filter
        calls = {"n": 0}

        class Blind:
            def __init__(self, qs):
                self.qs = qs

            def exists(self):
                calls["n"] += 1
                return False

        with mock.patch.object(XenditEvent.objects, "filter", lambda *a, **k: Blind(real_filter(*a, **k))):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.webhook(order, webhook_id="evt-race")
        self.assertEqual((response.status_code, calls["n"]), (200, 1))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)  # the losing request's work was undone
        self.assertEqual(order.events.filter(kind="xendit_paid").count(), 0)
        self.assertEqual(SanteRegistration.objects.count(), 0)
        self.assertEqual(len(verified_mail(self)), 0)

    def test_an_event_that_fails_to_process_is_500_and_leaves_no_trace_so_xendit_redelivers(self):
        from unittest import mock
        _, _, order = self.placed_xendit()
        with mock.patch("apps.pack.xendit.registration.activate", side_effect=RuntimeError("boom")):
            response = self.webhook(order)
        self.assertEqual(response.status_code, 500)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)  # rolled back whole
        self.assertEqual(XenditEvent.objects.count(), 0)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.webhook(order).status_code, 200)  # the redelivery works
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)


class CrossSiteTests(XenditPackCase):
    def setUp(self):
        super().setUp()
        self.other_cred = give_key(self.other_site, token="other-site-token")

    def test_a_webhook_verified_for_one_site_never_marks_another_sites_payment(self):
        _, _, order = self.placed_xendit()
        doc = dict(self.invoice_of(order), status="PAID", paid_at="2026-11-18T10:00:00.000Z")
        # Ben's own key and token, Ben's own URL, our order's reference: it matches nothing of ours
        response = post_webhook(self.client, self.other_cred, doc, token="other-site-token")
        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)
        event = XenditEvent.objects.get()
        self.assertEqual((event.site_id, event.outcome, event.order_ref), (self.other_site.pk, "no_match", None))

    def test_one_sites_token_on_the_other_sites_url_is_401(self):
        _, _, order = self.placed_xendit()
        response = self.webhook(order, key=self.other_cred.webhook_key, token=TOKEN)
        self.assertEqual(response.status_code, 401)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)

    def test_the_same_event_id_on_two_sites_is_not_a_duplicate(self):
        _, _, order = self.placed_xendit()
        self.webhook(order, webhook_id="evt-1")
        doc = {"id": "other-inv", "external_id": "np-nothing", "status": "PAID", "amount": 1488, "currency": "PHP"}
        response = post_webhook(self.client, self.other_cred, doc, token="other-site-token", webhook_id="evt-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(XenditEvent.objects.count(), 2)

    def test_a_reference_of_the_other_sites_prefix_is_not_matched_through_this_sites_url(self):
        _, _, order = self.placed_xendit()
        PackOrder.objects.filter(pk=order.pk).update(site=self.other_site)  # as if it were Ben's order
        response = self.webhook(dict(fake_xendit.get(order.xendit_invoice_id)))
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)
        self.assertEqual(XenditEvent.objects.get().outcome, "no_match")


class OutcomeTests(XenditPackCase):
    def test_a_different_amount_is_a_mismatch_with_no_benefits_and_the_order_stays_live(self):
        _, _, order = self.placed_xendit()
        with self.captureOnCommitCallbacks(execute=True):
            self.webhook(order, paid_amount=1000)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_MISMATCH)
        self.assertIsNone(order.benefits_active_at)
        self.assertIsNone(order.paid_at)
        self.assertEqual(SanteRegistration.objects.count(), 0)
        self.assertEqual(len(verified_mail(self)), 0)
        self.assertEqual(order.events.get(kind="xendit_mismatch").new, {"amount": "1000", "currency": "PHP"})
        self.assertEqual(XenditEvent.objects.get().outcome, "amount_mismatch")
        self.assertIn(C.XENDIT_MISMATCH, C.LIVE_STATUSES)

    def test_a_different_currency_is_a_mismatch(self):
        _, _, order = self.placed_xendit()
        self.webhook(order, currency="USD")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_MISMATCH)

    def test_an_overpayment_is_a_mismatch_too(self):
        _, _, order = self.placed_xendit()
        self.webhook(order, paid_amount=1489)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_MISMATCH)

    def test_a_later_correct_payment_clears_a_mismatch(self):
        _, _, order = self.placed_xendit()
        self.webhook(order, paid_amount=1000)
        self.webhook(order, webhook_id="evt-fix")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)

    def test_expired_closes_the_order_and_it_stops_blocking_a_new_checkout(self):
        token, _, order = self.placed_xendit()
        self.webhook(order, status="EXPIRED")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_EXPIRED)
        self.assertNotIn(C.XENDIT_EXPIRED, C.LIVE_STATUSES)
        self.assertEqual(self.status(token).json()["payment_status"], "expired")
        again = self.start()  # same contacts: a returning buyer, not "contact in use"
        self.assertTrue(again)

    def test_failed_is_recorded_and_changes_no_state(self):
        _, _, order = self.placed_xendit()
        before = PackOrder.objects.values().get()
        response = self.webhook(order, status="FAILED")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PackOrder.objects.values().get(), before)
        self.assertEqual(XenditEvent.objects.get().outcome, "ignored")

    def test_pending_changes_nothing(self):
        _, _, order = self.placed_xendit()
        before = PackOrder.objects.values().get()
        self.webhook(order, status="PENDING")
        self.assertEqual(PackOrder.objects.values().get(), before)

    def test_a_late_payment_on_an_expired_order_still_applies_when_nothing_newer_exists(self):
        _, _, order = self.placed_xendit()
        self.webhook(order, status="EXPIRED")
        self.webhook(order, status="PAID", webhook_id="late")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)

    def test_a_late_payment_on_an_expired_order_is_recorded_not_applied_when_a_newer_order_is_live(self):
        token, _, order = self.placed_xendit()
        self.webhook(order, status="EXPIRED")
        newer = PackOrder.objects.create(
            site=self.site, member=order.member, order_number="SMP00002", price_centavos=C.PRICE_CENTAVOS,
            buyer_name=NAME, payment_method="xendit", payment_status=C.AWAITING_XENDIT,
            idempotency_key="newer", xendit_external_id=f"np-{self.site.pk}-{'b' * 24}",
        )  # the same member, placed again after the first expired
        self.webhook(order, status="PAID", webhook_id="late")
        order.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_EXPIRED)  # never two live orders
        self.assertEqual(newer.payment_status, C.AWAITING_XENDIT)
        self.assertEqual(order.events.filter(kind="xendit_paid_superseded").count(), 1)
        self.assertEqual(XenditEvent.objects.filter(outcome="superseded").count(), 1)

    def test_a_second_invoice_paid_after_the_order_is_paid_is_recorded_not_applied(self):
        _, _, order = self.placed_xendit()
        self.webhook(order)
        other = dict(self.invoice_of(order), id="another-invoice")
        self.webhook(other, webhook_id="second")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)
        self.assertEqual(order.events.filter(kind="xendit_paid_superseded").count(), 1)
        self.assertEqual(order.events.filter(kind="xendit_paid").count(), 1)

    def test_an_event_for_a_reference_we_never_made_is_no_match_and_creates_nothing(self):
        doc = {"id": "i", "external_id": "np-1-deadbeef", "status": "PAID", "amount": 1488, "currency": "PHP"}
        response = post_webhook(self.client, self.cred, doc)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(XenditEvent.objects.get().outcome, "no_match")
        self.assertEqual(PackOrder.objects.count(), 0)

    def test_a_reference_of_another_apps_prefix_is_not_ours(self):
        doc = {"id": "i", "external_id": "no-mp-1-abc", "status": "PAID", "amount": 1488, "currency": "PHP"}
        post_webhook(self.client, self.cred, doc)
        self.assertEqual(XenditEvent.objects.get().outcome, "no_match")

    def test_a_paid_event_works_even_when_the_owner_has_since_switched_xendit_off(self):
        _, _, order = self.placed_xendit()
        from apps.payments.models import SitePaymentSettings
        SitePaymentSettings.objects.filter(site=self.site).update(xendit_enabled=False)
        self.webhook(order)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)  # obligations survive a switch

    def test_a_paid_event_for_a_member_whose_contacts_were_released_is_recorded_not_applied(self):
        _, _, order = self.placed_xendit()
        self.webhook(order, status="EXPIRED")
        order.member.email = "released-1@released.invalid"
        order.member.save()
        self.webhook(order, status="PAID", webhook_id="late")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.XENDIT_EXPIRED)


class ReturnPageStatusTests(XenditPackCase):
    def test_before_any_order_the_status_is_none(self):
        token = self.start()
        self.assertEqual(self.status(token).json(), {"payment_status": "none", "order_number": "", "pay_url": None})

    def test_a_bad_token_is_409(self):
        response = self.status("not-a-token")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "checkout_expired")

    def test_a_waiting_order_shows_its_own_invoice_url(self):
        token, body, order = self.placed_xendit()
        answer = self.status(token).json()
        self.assertEqual(answer, {"payment_status": "waiting", "order_number": order.order_number, "pay_url": body["pay_url"]})

    def test_the_browser_returning_from_xendit_never_marks_the_order_paid(self):
        token, _, order = self.placed_xendit()
        # whatever the browser sends, including claims of payment, nothing changes without Xendit's say
        for extra in ({}, {"status": "PAID"}, {"payment_status": "paid"}, {"paid": True}, {"retry": True}):
            self.status(token, **extra)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.AWAITING_XENDIT)
        self.assertIsNone(order.paid_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_status_read_asks_xendit_and_a_real_payment_then_shows_paid(self):
        token, _, order = self.placed_xendit()
        self.pay_in_fake(order)
        with self.captureOnCommitCallbacks(execute=True):
            answer = self.status(token).json()
        self.assertEqual(answer["payment_status"], "paid")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, C.VERIFIED)
        self.assertEqual(XenditEvent.objects.get().source, "reconcile")
        self.assertEqual(len(verified_mail(self)), 1)

    def test_polling_makes_at_most_one_read_at_xendit_in_a_few_seconds(self):
        token, _, order = self.placed_xendit()
        before = len([c for c in fake_xendit.calls if c[0] == "GET"])
        for _ in range(6):
            self.status(token)
        self.assertEqual(len([c for c in fake_xendit.calls if c[0] == "GET"]) - before, 1)

    def test_a_pending_invoice_stays_waiting(self):
        token, _, order = self.placed_xendit()
        self.assertEqual(self.status(token).json()["payment_status"], "waiting")

    def test_an_expired_invoice_read_shows_expired(self):
        token, _, order = self.placed_xendit()
        fake_xendit.expire(order.xendit_invoice_id)
        self.assertEqual(self.status(token).json()["payment_status"], "expired")

    def test_a_wrong_amount_read_shows_mismatch(self):
        token, _, order = self.placed_xendit()
        self.pay_in_fake(order, amount=1000)
        self.assertEqual(self.status(token).json()["payment_status"], "mismatch")

    def test_another_sites_checkout_token_reads_nothing_of_ours(self):
        token, _, order = self.placed_xendit()
        response = self.status(token, host="bens-store.negosyotest.com")
        self.assertEqual(response.status_code, 409)  # the token is not Ben's: it reads as expired
        self.assertEqual(response.json()["error"]["code"], "checkout_expired")
        self.assertNotIn(order.order_number, response.content.decode())

    def test_a_direct_order_reports_under_review(self):
        token, _ = self.placed()
        self.assertEqual(self.status(token).json()["payment_status"], "under_review")

    def test_the_status_read_survives_an_unreachable_xendit(self):
        token, _, order = self.placed_xendit()
        give_key(self.site, api_key="xnd_development_down")
        self.assertEqual(self.status(token).json()["payment_status"], "waiting")


class OnboardingClosedUntilPaidTests(XenditPackCase):
    def onboarding(self, path, token):
        return self.client.post(
            "/storefront/v1/" + path, {"host": HOST, "visitor_ip": "203.0.113.50", "token": token},
            content_type="application/json", secure=True, HTTP_X_REAL_IP="198.51.100.200",
            HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )

    def test_onboarding_answers_payment_pending_until_xendit_confirms(self):
        token, _, order = self.placed_xendit()
        for path in ("onboarding/state/", "onboarding/address/", "onboarding/mobile/send/", "onboarding/completed/"):
            with self.subTest(path=path):
                response = self.onboarding(path, token)
                self.assertEqual(response.status_code, 409, response.content)
                self.assertEqual(response.json()["error"]["code"], "payment_pending")
                self.assertEqual(response.json()["error"]["message"], "Your payment is not confirmed yet.")

    def test_it_opens_once_the_order_is_paid(self):
        token, _, order = self.placed_xendit()
        self.webhook(order)
        response = self.onboarding("onboarding/state/", token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["order_number"], order.order_number)

    def test_a_direct_order_is_not_gated(self):
        token, _ = self.placed()
        self.assertEqual(self.onboarding("onboarding/state/", token).status_code, 200)
