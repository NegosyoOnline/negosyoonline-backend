"""Private files never cross a store (ClickUp z8uq9m7ycc, E).

Every kind of private file the product serves (a payment proof of a Santé order, a billing proof,
the BIR badge, a payment QR code) is stored for each of two stores with bytes that name the
store. Each reader (the owner, the member, the storefront, the operator) is then asked with one
store's session or host and the other store's ids; its answer must never hold the other's bytes.
"""
from django.test import override_settings
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import BankTransferPayment, Subscription
from apps.billing.tests.base import JPEG, make_operator, submit
from apps.common import private_files
from apps.common.tests.test_tenant_isolation.owner_base import TwoOwnersTestCase
from apps.members import sessions
from apps.operators.models import OperatorAuditEntry
from apps.pack import constants as C
from apps.pack.tests.owner_base import make_order
from apps.payments.models import SitePaymentAccount
from apps.payments.tests.base import PNG
from apps.sites.models import SiteBusinessProfile
from apps.storefront.tests.base import HOST, SECRET
from apps.members.tests.base import OTHER_HOST

API = "/storefront/v1/"
A, B = b"AAAA-site-a", b"BBBB-site-b"


@override_settings(
    SITE_PARENT_HOST="negosyotest.com", STOREFRONT_ENABLED=True, STOREFRONT_SERVER_SECRET=SECRET,
    MEMBERS_ENABLED=True, SANTE_AGREEMENT_ENABLED=True,
)
class PrivateFilesTests(TwoOwnersTestCase):
    def setUp(self):
        super().setUp()
        self.order_a, self.proof_a = make_order(self.site, content=PNG + A)
        self.order_b, self.proof_b = make_order(self.ben_site, content=PNG + B)
        self.pay_a = submit(Subscription.objects.get(site=self.site), self.now(), content=JPEG + A)
        self.pay_b = BankTransferPayment.objects.get(public_id=self.ben_ids["payment_id"])
        # Ben's set-up payment gets bytes that name Ben
        self.proof_store.files[self.pay_b.proof_key] = JPEG + B
        for site, tag in ((self.site, A), (self.ben_site, B)):
            key = private_files.save(f"sites/seal/site-{site.pk}", PNG + tag, "image/png")
            SiteBusinessProfile.objects.filter(site=site).update(seal_key=key)
            account = SitePaymentAccount.objects.filter(site=site).order_by("pk").first()
            qr = private_files.save(f"sante/qr/site-{site.pk}", PNG + tag + tag, "image/png")
            SitePaymentAccount.objects.filter(pk=account.pk).update(qr_file_key=qr)
        self.account_of = {
            tag: SitePaymentAccount.objects.filter(site=site).exclude(qr_file_key="").first()
            for site, tag in ((self.site, A), (self.ben_site, B))
        }
        _, self.session_a = sessions.create_session(self.site, self.order_a.member)
        _, self.session_b = sessions.create_session(self.ben_site, self.order_b.member)

    def now(self):
        return timezone.now()

    def assertNoneOf(self, tag, response, label):
        self.assertNotIn(tag, response.content, f"{label}: the other store's file was served")

    def storefront(self, path, host, **fields):
        headers = {"HTTP_X_REAL_IP": "198.51.100.200", "HTTP_AUTHORIZATION": f"Bearer {SECRET}"}
        if path.startswith("member/"):
            return self.client.post(API + path, {"host": host, **fields},
                                    content_type="application/json", secure=True, **headers)
        return self.client.get(API + path, {"host": host, **fields}, secure=True, **headers)

    # each store's file lives under its own folder -----------------------------------------
    def test_stored_keys_name_their_own_store(self):
        for proof, site in ((self.proof_a, self.site), (self.proof_b, self.ben_site)):
            proof.refresh_from_db()
            self.assertTrue(proof.file_key.startswith(C.PROOF_FOLDER.format(site.pk) + "/"), proof.file_key)
        for tag, account in self.account_of.items():
            self.assertTrue(account.qr_file_key.startswith(f"sante/qr/site-{account.site_id}/"))
        for profile in SiteBusinessProfile.objects.exclude(seal_key=""):
            self.assertTrue(profile.seal_key.startswith(f"sites/seal/site-{profile.site_id}/"))

    # the owner's readers ------------------------------------------------------------------
    def test_the_owner_reads_only_their_own_files_and_never_the_others_by_any_id(self):
        own = self.get(f"/v1/sites/{self.site_id}/pack/orders/{self.order_a.public_id}/proofs/{self.proof_a.public_id}/")
        self.assertEqual((own.status_code, own.content), (200, PNG + A))
        for order, proof in ((self.order_a, self.proof_b), (self.order_b, self.proof_a),
                             (self.order_b, self.proof_b)):
            response = self.get(
                f"/v1/sites/{self.site_id}/pack/orders/{order.public_id}/proofs/{proof.public_id}/")
            self.assertEqual(response.status_code, 404, response.content)
            self.assertNoneOf(B, response, "pack proof")
        own = self.get(f"/v1/sites/{self.site_id}/billing/payments/{self.pay_a.public_id}/proof/")
        self.assertEqual((own.status_code, own.content), (200, JPEG + A))
        response = self.get(f"/v1/sites/{self.site_id}/billing/payments/{self.pay_b.public_id}/proof/")
        self.assertEqual(response.status_code, 404, response.content)
        self.assertNoneOf(B, response, "billing proof")
        seal = self.get(f"/v1/sites/{self.site_id}/business-profile/seal/")
        self.assertEqual((seal.status_code, seal.content), (200, PNG + A))
        theirs = self.get(f"/v1/sites/{self.ben_site_id}/business-profile/seal/")
        self.assertEqual(theirs.status_code, 404)
        self.assertNoneOf(B, theirs, "seal")

    def test_the_other_owner_gets_only_theirs(self):
        own = self.get(f"/v1/sites/{self.ben_site_id}/pack/orders/{self.order_b.public_id}/proofs/{self.proof_b.public_id}/",
                       client=self.ben)
        self.assertEqual((own.status_code, own.content), (200, PNG + B))
        for path in (
            f"/v1/sites/{self.ben_site_id}/pack/orders/{self.order_a.public_id}/proofs/{self.proof_a.public_id}/",
            f"/v1/sites/{self.ben_site_id}/billing/payments/{self.pay_a.public_id}/proof/",
            f"/v1/sites/{self.site_id}/business-profile/seal/",
        ):
            response = self.get(path, client=self.ben)
            self.assertEqual(response.status_code, 404, (path, response.content))
            self.assertNoneOf(A, response, path)

    # the member's reader ------------------------------------------------------------------
    def test_a_member_reads_only_their_own_proof_on_their_own_store(self):
        own = self.storefront("member/pack/proof/", HOST, token=self.session_a, proof_id=self.proof_a.public_id)
        self.assertEqual((own.status_code, own.content), (200, PNG + A))
        for host, token, proof in (
            (HOST, self.session_a, self.proof_b), (HOST, self.session_b, self.proof_b),
            (OTHER_HOST, self.session_a, self.proof_b), (OTHER_HOST, self.session_a, self.proof_a),
            (OTHER_HOST, self.session_b, self.proof_a),
        ):
            with self.subTest(host=host, proof=proof.public_id):
                response = self.storefront("member/pack/proof/", host, token=token, proof_id=proof.public_id)
                self.assertIn(response.status_code, (401, 404))
                self.assertNoneOf(B if proof is self.proof_b else A, response, "member proof")

    # the storefront readers ---------------------------------------------------------------
    def test_the_storefront_serves_the_hosts_own_seal_and_qr_code_only(self):
        for host, tag in ((HOST, A), (OTHER_HOST, B)):
            seal = self.storefront("seal/", host)
            self.assertEqual((seal.status_code, seal.content), (200, PNG + tag))
            account = self.account_of[tag]
            qr = self.storefront(f"payment-accounts/{account.public_id}/qr/", host)
            self.assertEqual((qr.status_code, qr.content), (200, PNG + tag + tag))
        for host, tag, other in ((HOST, B, A), (OTHER_HOST, A, B)):
            account = self.account_of[tag]
            response = self.storefront(f"payment-accounts/{account.public_id}/qr/", host)
            self.assertEqual(response.status_code, 404, response.content)
            self.assertNoneOf(tag + tag, response, "qr")

    # the operator's reader (checked at service level) -------------------------------------
    def test_an_operator_reads_the_payment_asked_for_and_the_audit_names_its_store(self):
        operator = make_operator("finance")
        for payment, tag, site in ((self.pay_a, A, self.site), (self.pay_b, B, self.ben_site)):
            content, content_type = billing.proof_for_operator(payment, operator)
            self.assertEqual((content, content_type), (JPEG + tag, "image/jpeg"))
            entry = OperatorAuditEntry.objects.filter(action="billing_proof_viewed").order_by("-pk").first()
            self.assertEqual(entry.target_id, site.public_id)
            self.assertEqual(entry.detail, {"payment": payment.public_id})
        self.assertEqual(OperatorAuditEntry.objects.filter(action="billing_proof_viewed").count(), 2)

    def test_an_operator_read_needs_an_operator_and_reads_nothing_without_one(self):
        with self.assertRaises(ValueError):
            billing.proof_for_operator(self.pay_a, None)
        self.assertFalse(OperatorAuditEntry.objects.filter(action="billing_proof_viewed").exists())


