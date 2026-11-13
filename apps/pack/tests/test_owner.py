"""The Subscriber's proof review routes: list, detail, proof read, approve, reject, stale 409,
foreign Subscriber 404, the switch, the Billing-only rule (listed in test_billing_only)."""
from django.core import mail
from django.test import override_settings

from apps.pack import constants as C
from apps.pack.models import PackOrder, PackProofDecision
from apps.pack.tests.owner_base import OwnerTestCase, PNG, make_order


class ListAndDetailTests(OwnerTestCase):
    def test_list_filters_and_newest_first(self):
        first, _ = make_order(self.site)
        second, _ = make_order(self.site, status=C.REJECTED, lock=True)
        body = self.get(self.url()).json()
        self.assertEqual([o["id"] for o in body["orders"]], [second.public_id, first.public_id])
        row = body["orders"][1]
        self.assertEqual(
            sorted(row),
            ["buyer_name", "courier", "created_at", "delivered_on", "id", "method", "mobile_masked", "order_number",
             "payment_status", "price_centavos", "shipment_status", "shipped_on", "shipping_enabled", "state_version",
             "tracking_number"],
        )
        self.assertEqual(row["price_centavos"], 148800)
        self.assertEqual([o["id"] for o in self.get(self.url("?filter=rejected")).json()["orders"]], [second.public_id])
        self.assertEqual([o["id"] for o in self.get(self.url("?filter=to_review")).json()["orders"]], [first.public_id])

    def test_the_list_is_capped_at_200(self):
        for _ in range(3):
            make_order(self.site)
        with override_list_max(2):
            self.assertEqual(len(self.get(self.url()).json()["orders"]), 2)

    def test_detail_has_money_in_centavos_submissions_and_no_buyer_contact(self):
        order, sub = make_order(self.site)
        body = self.get(self.url(f"{order.public_id}/")).json()
        self.assertEqual(body["price_centavos"], 148800)
        self.assertEqual(body["state_version"], order.state_version)
        self.assertEqual(body["submissions"][0]["id"], sub.public_id)
        self.assertEqual(body["submissions"][0]["amount_sent_centavos"], 148800)
        self.assertNotIn(order.member.mobile, str(body))
        self.assertNotIn(order.member.email, str(body))

    def test_an_unknown_order_is_404(self):
        self.error(self.get(self.url("pko_missing/")), 404, "not_found")


class ProofReadTests(OwnerTestCase):
    def test_the_proof_opens_with_private_headers(self):
        order, sub = make_order(self.site)
        response = self.get(self.url(f"{order.public_id}/proofs/{sub.public_id}/"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PNG)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_a_proof_of_another_order_is_404(self):
        order, _ = make_order(self.site)
        _, other_sub = make_order(self.site)
        self.error(self.get(self.url(f"{order.public_id}/proofs/{other_sub.public_id}/")), 404, "not_found")


class ReviewTests(OwnerTestCase):
    def test_approve_verifies_clears_the_lock_and_mails_the_buyer(self):
        order, _ = make_order(self.site, status=C.REJECTED, lock=True)
        with self.captureOnCommitCallbacks(execute=True):
            body = self.post(self.url(f"{order.public_id}/approve/"), {"state_version": order.state_version}).json()
        self.assertEqual(body["payment_status"], C.VERIFIED)
        self.assertFalse(body["rejection_lock_active"])
        self.assertEqual(body["state_version"], order.state_version + 1)
        self.assertEqual(PackProofDecision.objects.get().decision, "approved")
        self.assertEqual([m.to for m in mail.outbox], [[order.member.email]])

    def test_reject_locks_and_the_email_names_no_reason(self):
        order, _ = make_order(self.site)
        with self.captureOnCommitCallbacks(execute=True):
            body = self.post(
                self.url(f"{order.public_id}/reject/"),
                {"state_version": order.state_version, "reason_code": "wrong_account"},
            ).json()
        self.assertEqual(body["payment_status"], C.REJECTED)
        self.assertTrue(body["rejection_lock_active"])
        self.assertEqual(body["submissions"][0]["decisions"][0]["reason_label"], C.REASON_LABELS["wrong_account"])
        self.assertNotIn(C.REASON_LABELS["wrong_account"], mail.outbox[0].body + mail.outbox[0].subject)

    def test_other_needs_one_to_500_characters(self):
        order, _ = make_order(self.site)
        for text in ("", "   ", "x" * 501):
            r = self.post(
                self.url(f"{order.public_id}/reject/"),
                {"state_version": order.state_version, "reason_code": "other", "other_text": text},
            )
            self.error(r, 422, "reason_text_required")
        self.error(
            self.post(self.url(f"{order.public_id}/reject/"), {"state_version": order.state_version, "reason_code": "x"}),
            422, "invalid_reason",
        )
        self.assertFalse(PackProofDecision.objects.exists())

    def test_a_stale_version_is_409_and_changes_nothing(self):
        order, _ = make_order(self.site)
        r = self.post(self.url(f"{order.public_id}/approve/"), {"state_version": order.state_version + 5})
        self.error(r, 409, "stale")
        self.assertEqual(PackOrder.objects.get().payment_status, C.PENDING_REVIEW)

    def test_a_second_decision_with_the_new_version_is_refused(self):
        order, _ = make_order(self.site)
        self.post(self.url(f"{order.public_id}/approve/"), {"state_version": 1})
        r = self.post(self.url(f"{order.public_id}/reject/"), {"state_version": 2, "reason_code": "other", "other_text": "x"})
        self.error(r, 409, "wrong_status")

    def test_an_unknown_body_key_is_refused(self):
        order, _ = make_order(self.site)
        r = self.post(self.url(f"{order.public_id}/approve/"), {"state_version": 1, "site_id": "x"})
        self.assertEqual(r.status_code, 400)


class IsolationTests(OwnerTestCase):
    def test_another_subscribers_site_and_order_are_404_and_untouched(self):
        ben, ben_site, order, sub = self.other_site()
        calls = [
            ("get", self.url("", site_id=ben_site.public_id), None),
            ("get", self.url(f"{order.public_id}/", site_id=ben_site.public_id), None),
            ("get", self.url(f"{order.public_id}/proofs/{sub.public_id}/", site_id=ben_site.public_id), None),
            ("post", self.url(f"{order.public_id}/approve/", site_id=ben_site.public_id), {"state_version": 1}),
            # my site, his order id
            ("get", self.url(f"{order.public_id}/"), None),
            ("post", self.url(f"{order.public_id}/approve/"), {"state_version": 1}),
        ]
        for method, path, body in calls:
            r = self.get(path) if method == "get" else self.send("post", path, body)
            self.assertEqual(r.status_code, 404, (path, r.content))
        self.assertEqual(PackOrder.objects.get(pk=order.pk).payment_status, C.PENDING_REVIEW)

    def test_signed_out_is_401(self):
        from django.test import Client
        self.assertEqual(self.get(self.url(), client=Client()).status_code, 401)


class SwitchTests(OwnerTestCase):
    @override_settings(PACK_ENABLED=False)
    def test_off_every_route_is_404(self):
        order, sub = make_order(self.site)
        self.error(self.get(self.url()), 404, "not_found")
        self.error(self.post(self.url(f"{order.public_id}/approve/"), {"state_version": 1}), 404, "not_found")


def override_list_max(n):
    from unittest import mock
    return mock.patch("apps.pack.api_owner.LIST_MAX", n)
