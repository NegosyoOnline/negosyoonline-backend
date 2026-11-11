"""GET pack/, pack/agreement/{id}/pdf/ and payment-accounts/{id}/qr/ of the storefront surface.

From TWCinc's test_pack_views and test_agreement_checkout, the availability cases that need no
order: the pack is available only with a payment account AND a published agreement version;
a draft never leaves; an unpublished or foreign version is a 404; off, unknown, foreign and
non-Santé sites answer one 404. Order, token and acceptance cases belong to later slices.
"""
import hashlib

from django.test import override_settings

from apps.sante import agreement
from apps.billing.tests.base import make_subscription
from apps.common import private_files
from apps.payments.models import SitePaymentAccount, SitePaymentSettings
from apps.payments.tests.base import PNG, make_payment_account, use_local_private_store
from apps.sante.tests.agreement_helpers import (
    DAY, PDF, REASON, draft, make_master, make_operator, published,
)
from apps.sante.tests.base import retemplate
from apps.storefront.tests.base import HOST, NOT_FOUND, SurfaceTestCase

PACK = "/storefront/v1/pack/"
KEYS = {"price_centavos", "currency", "available", "agreement", "payment_accounts"}


@override_settings(SANTE_AGREEMENT_ENABLED=True)
class PackTests(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.directory = use_local_private_store(self)
        self.site = make_subscription().site
        self.op = make_operator()
        make_master(self.op)

    def pack(self, host=HOST, path=PACK):
        return self.call(host, path=path)

    def publish(self, label="v1", content=PDF):
        return published(self.op, label, content=content)

    def account(self, **fields):
        return make_payment_account(self.site, **fields)

    def test_unavailable_with_nothing_set_up(self):
        body = self.pack().json()
        self.assertEqual(set(body), KEYS)
        self.assertEqual(body["price_centavos"], 148800)
        self.assertEqual(body["currency"], "PHP")
        self.assertFalse(body["available"])
        self.assertIsNone(body["agreement"])
        self.assertEqual(body["payment_accounts"], [])

    def test_unavailable_without_a_published_version_or_without_an_account(self):
        self.account()
        body = self.pack().json()
        self.assertFalse(body["available"])
        self.assertEqual(len(body["payment_accounts"]), 1)
        draft(self.op, "d")  # a draft only
        self.assertFalse(self.pack().json()["available"])
        self.assertIsNone(self.pack().json()["agreement"])

        other = make_subscription("ben@example.test", "bens-store").site
        self.publish()
        body = self.pack("bens-store.negosyotest.com").json()
        self.assertFalse(body["available"])  # published, but Ben has no account
        self.assertIsNotNone(body["agreement"])
        self.assertEqual(other.payment_accounts.count(), 0)

    def test_available_with_both_and_names_the_active_version(self):
        self.account()
        version = self.publish()
        body = self.pack().json()
        self.assertTrue(body["available"])
        self.assertEqual(body["agreement"], {
            "id": version.pk, "label": "v1", "effective_date": "2026-11-15"})
        account, = body["payment_accounts"]
        self.assertEqual(account["provider_name"], "BDO")
        self.assertNotIn("qr_file_key", str(body))
        self.assertNotIn("file_key", str(body))

    def test_a_removed_account_or_a_switched_off_method_is_not_offered(self):
        row = self.account()
        self.publish()
        SitePaymentAccount.objects.filter(pk=row.pk).update(removed_at=row.created_at)
        self.assertFalse(self.pack().json()["available"])
        SitePaymentAccount.objects.filter(pk=row.pk).update(removed_at=None)
        SitePaymentSettings.objects.create(site=self.site, direct_enabled=False)
        body = self.pack().json()
        self.assertFalse(body["available"])
        self.assertEqual(body["payment_accounts"], [])

    def test_a_newer_version_changes_the_answer(self):
        self.account()
        self.publish("v1")
        second = self.publish("v2", PDF + b"x")
        self.assertEqual(self.pack().json()["agreement"]["id"], second.pk)

    def test_off_unknown_foreign_and_non_sante_sites_answer_one_404(self):
        self.account()
        version = self.publish()
        qr = self.account(provider_name="GCash")
        SitePaymentAccount.objects.filter(pk=qr.pk).update(
            qr_file_key=private_files.save("payments/site-1", PNG, "image/png"))
        paths = [PACK, f"{PACK}agreement/{version.pk}/pdf/",
                 f"/storefront/v1/payment-accounts/{qr.public_id}/qr/"]
        for path in paths:
            self.assertEqual(self.pack(path=path).status_code, 200)
        retemplate(self.site)
        make_subscription("ben@example.test", "bens-store")
        for path in paths:
            for host in (HOST, "nobody.negosyotest.com", "example.org", ""):
                self.assert_answer(self.pack(host, path), 404, NOT_FOUND)
        retemplate(self.site, "sante")
        with override_settings(SANTE_AGREEMENT_ENABLED=False):
            for path in paths:
                self.assert_answer(self.pack(path=path), 404, NOT_FOUND)

    def test_the_pdf_route(self):
        self.account()
        version = self.publish()
        response = self.pack(path=f"{PACK}agreement/{version.pk}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF)
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), version.sha256)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertTrue(response["Content-Disposition"].startswith("inline;"))
        self.assertIn("no-store", response["Cache-Control"])  # the surface's rule, stricter than private

    def test_the_pdf_route_refuses_a_draft_an_unknown_id_and_a_changed_file(self):
        pending = draft(self.op, "d")
        for version_id in (pending.pk, pending.pk + 99):
            self.assert_answer(self.pack(path=f"{PACK}agreement/{version_id}/pdf/"), 404, NOT_FOUND)
        agreement.publish(self.op, "d", reason=REASON)
        (self.directory / pending.storage_key).write_bytes(b"%PDF-changed")
        self.assert_answer(self.pack(path=f"{PACK}agreement/{pending.pk}/pdf/"), 404, NOT_FOUND)

    def test_the_qr_route_serves_this_sites_active_account_only(self):
        qr = self.account()
        key = private_files.save("payments/site-1", PNG, "image/png")
        SitePaymentAccount.objects.filter(pk=qr.pk).update(qr_file_key=key)
        path = f"/storefront/v1/payment-accounts/{qr.public_id}/qr/"
        response = self.pack(path=path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PNG)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        # another site's host cannot read it
        make_subscription("ben@example.test", "bens-store")
        self.assert_answer(self.pack("bens-store.negosyotest.com", path), 404, NOT_FOUND)
        # an account without a QR, an unknown id and a removed account all answer 404
        plain = self.account(provider_name="GCash")
        for account_id in (plain.public_id, "pay_unknown"):
            self.assert_answer(
                self.pack(path=f"/storefront/v1/payment-accounts/{account_id}/qr/"), 404, NOT_FOUND)
        SitePaymentAccount.objects.filter(pk=qr.pk).update(removed_at=qr.created_at)
        self.assert_answer(self.pack(path=path), 404, NOT_FOUND)

    def test_the_routes_need_the_server_credential(self):
        self.assertEqual(self.call(path=PACK, secret=None).status_code, 401)
        self.assertEqual(self.call(path=PACK, secret="wrong").status_code, 401)
