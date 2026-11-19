"""Tenant isolation on the storefront surface, where the site is the host (ClickUp z8uq9m7ycc, D).

Two Santé stores, Ana's (HOST) and Ben's (OTHER_HOST), each with a buyer. The server secret is
valid for both hosts, so the only thing keeping them apart is the host the request names. Every
cross-host answer must be the one an unknown token or id gets on that host, and must change
no row, stored file or mail.
"""
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.common import private_files
from apps.common.tests.test_tenant_isolation.owner_base import row_snapshot
from apps.members import sessions
from apps.members.models import Member
from apps.pack.models import CheckoutToken, PackOrder
from apps.pack.tests.base import (
    HOST, OTHER_HOST, PNG, VISITOR, PackTestCase, stored_files,
)
from apps.pack.tests.owner_base import make_order
from apps.payments.models import SitePaymentAccount
from apps.payments.tests.base import make_payment_account
from apps.sites.models import SiteBusinessProfile
from apps.storefront.tests.base import SECRET

API = "/storefront/v1/"
BOGUS = "t" * 43


class StorefrontIsolationTests(PackTestCase):
    def setUp(self):
        super().setUp()
        self.account_b = make_payment_account(
            self.other_site, account_name="Ben Receiver", account_number="009988776655"
        )
        for site, name, mobile in ((self.site, "Ana Wellness", "09171110001"),
                                   (self.other_site, "Ben Wellness", "09172220002")):
            site.display_name, site.contact_mobile = name, mobile
            site.contact_email = name.split()[0].lower() + "@store.test"
            site.save(update_fields=["display_name", "contact_mobile", "contact_email"])
        SiteBusinessProfile.objects.filter(site=self.site).update(
            registered_name="Ana Wellness Trading", tin="111-111-111")
        SiteBusinessProfile.objects.filter(site=self.other_site).update(
            registered_name="Ben Wellness Trading", tin="222-222-222")
        cache.clear()

    def raw(self, path, host, token=None, **fields):
        body = {"host": host, "visitor_ip": VISITOR, **fields}
        if token is not None:
            body["token"] = token
        return self.client.post(
            API + path, body, content_type="application/json", secure=True,
            HTTP_X_REAL_IP="198.51.100.200", HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )

    def read(self, path, host, **query):
        return self.client.get(
            API + path, {"host": host, **query}, secure=True, HTTP_X_REAL_IP="198.51.100.200",
            HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )

    def start_b(self, **changes):
        """A started checkout on Ben's store, for a buyer of his own."""
        cache.clear()
        fields = self.contact_fields(
            full_name="Ben Buyer", email="ben.buyer@buyer.test", mobile="09175550123", **changes)
        response = self.raw("checkout/contact/", OTHER_HOST, **fields)
        self.assertEqual(response.status_code, 200, response.content)
        cache.clear()
        return response.json()["token"]

    def state(self):
        return {"rows": row_snapshot(), "files": stored_files(self.directory),
                "mail": [(m.to, m.subject) for m in mail.outbox]}

    def give_files(self):
        """A badge and a QR code for each store, with bytes that name their store."""
        self.files = {}
        for site, account, tag in ((self.site, self.account, b"A"), (self.other_site, self.account_b, b"B")):
            seal = private_files.save("sites/seal", PNG + tag, "image/png")
            qr = private_files.save("payments/qr", PNG + tag + tag, "image/png")
            SiteBusinessProfile.objects.filter(site=site).update(seal_key=seal)
            SitePaymentAccount.objects.filter(pk=account.pk).update(qr_file_key=qr)
            self.files[tag] = (PNG + tag, PNG + tag + tag)

    # site lookup, seal, pack, QR ----------------------------------------------------------
    def test_a_host_gets_its_own_store_and_never_the_others_details(self):
        mine = self.read("site/", HOST)
        theirs = self.read("site/", OTHER_HOST)

        self.assertEqual((mine.status_code, theirs.status_code), (200, 200))
        self.assertEqual(mine.json()["id"], self.site.public_id)
        self.assertEqual(theirs.json()["id"], self.other_site.public_id)
        for marker in ("Ben Wellness", "222-222-222", "09172220002", "ben@store.test",
                       self.other_site.public_id):
            self.assertNotIn(marker, mine.content.decode())
        for marker in ("Ana Wellness", "111-111-111", "09171110001", "ana@store.test",
                       self.site.public_id):
            self.assertNotIn(marker, theirs.content.decode())

    def test_the_seal_and_the_qr_code_are_only_ever_the_hosts_own(self):
        self.give_files()

        for host, tag in ((HOST, b"A"), (OTHER_HOST, b"B")):
            seal = self.read("seal/", host)
            self.assertEqual((seal.status_code, seal.content), (200, self.files[tag][0]))
        qr_of_a, qr_of_b = self.account.public_id, self.account_b.public_id
        self.assertEqual(self.read(f"payment-accounts/{qr_of_a}/qr/", HOST).content, self.files[b"A"][1])
        self.assertEqual(self.read(f"payment-accounts/{qr_of_b}/qr/", OTHER_HOST).content, self.files[b"B"][1])
        # The other store's account id, asked for on this host, is the 404 of an id that is not there.
        unknown = self.read("payment-accounts/pay_00000000000000000000/qr/", HOST)
        for host, account_id in ((HOST, qr_of_b), (OTHER_HOST, qr_of_a)):
            response = self.read(f"payment-accounts/{account_id}/qr/", host)
            self.assertEqual((response.status_code, response.content), (404, unknown.content))

    def test_a_store_without_a_seal_is_not_given_the_others(self):
        self.give_files()
        SiteBusinessProfile.objects.filter(site=self.site).update(seal_key="")

        gone = self.read("seal/", HOST)
        self.assertEqual(gone.status_code, 404)
        self.assertNotIn(self.files[b"B"][0], gone.content)

    def test_the_pack_lists_only_the_hosts_payment_accounts(self):
        mine = self.read("pack/", HOST).json()
        theirs = self.read("pack/", OTHER_HOST).json()

        self.assertEqual([a["id"] for a in mine["payment_accounts"]], [self.account.public_id])
        self.assertEqual([a["id"] for a in theirs["payment_accounts"]], [self.account_b.public_id])
        self.assertNotIn("009988776655", str(mine))
        self.assertNotIn("001234567890", str(theirs))

    def test_an_unknown_host_is_the_404_every_cross_host_miss_gives(self):
        self.give_files()
        for path in ("site/", "seal/", "pack/"):
            with self.subTest(path=path):
                self.assertEqual(self.read(path, "nobody.negosyotest.com").status_code, 404)

    # checkout and onboarding ---------------------------------------------------------------
    def test_a_checkout_token_started_on_one_host_does_nothing_on_the_other(self):
        token_a = self.start()
        before = self.state()
        row = CheckoutToken.objects.get()

        for path, fields in (
            ("checkout/state/", {}), ("checkout/code/send/", {}),
            ("checkout/code/verify/", {"code": "123456"}),
            ("onboarding/state/", {}), ("onboarding/mobile/send/", {}),
            ("onboarding/mobile/verify/", {"code": "123456"}), ("onboarding/email/send/", {}),
            ("onboarding/email/verify/", {"code": "123456"}), ("onboarding/completed/", {}),
        ):
            with self.subTest(path=path):
                cache.clear()
                foreign = self.raw(path, OTHER_HOST, token=token_a, **fields)
                cache.clear()
                unknown = self.raw(path, OTHER_HOST, token=BOGUS, **fields)
                self.assertEqual((foreign.status_code, foreign.content),
                                 (unknown.status_code, unknown.content))
                self.assertGreaterEqual(foreign.status_code, 400)

        self.assertEqual(self.state()["files"], before["files"])
        self.assertEqual(self.state()["mail"], before["mail"])
        self.assertEqual(CheckoutToken.objects.get().expires_at, row.expires_at)  # not even slid
        self.assertEqual(self.raw("checkout/state/", HOST, token=token_a).status_code, 200)

    def test_a_proof_cannot_be_placed_on_the_other_store_with_this_stores_token_or_account(self):
        token_a = self.start()
        before = self.state()

        for token, account, label in (
            (token_a, self.account_b.public_id, "A's token, B's account, B's host"),
            (token_a, self.account.public_id, "A's token, A's account, B's host"),
        ):
            with self.subTest(label):
                cache.clear()
                response = self.place(token, host=OTHER_HOST, account_id=account)
                self.assertGreaterEqual(response.status_code, 400, response.content)
        # Ben's own token on Ben's host with Ana's account: the account is not his.
        token_b = self.start_b()
        mixed = self.place(token_b, host=OTHER_HOST, account_id=self.account.public_id)
        self.assertGreaterEqual(mixed.status_code, 400, mixed.content)
        # ... and Ben's token on Ana's host.
        cross = self.place(token_b, host=HOST, account_id=self.account.public_id)
        self.assertGreaterEqual(cross.status_code, 400, cross.content)

        self.assertFalse(PackOrder.objects.exists())
        self.assertEqual(stored_files(self.directory), before["files"])
        self.assertEqual(mail.outbox, [])

    def test_each_stores_checkout_places_its_own_order_and_the_other_cannot_follow_it(self):
        token_a, placed_a = self.placed()
        token_b = self.start_b()
        placed_b = self.place(token_b, host=OTHER_HOST, account_id=self.account_b.public_id,
                              submit_key="key-b")
        self.assertEqual(placed_b.status_code, 200, placed_b.content)
        order_a, order_b = PackOrder.objects.order_by("pk")
        self.assertEqual((order_a.site, order_b.site), (self.site, self.other_site))
        self.give_member(order_b.order_number, site=self.other_site)  # the owner approved it: only then does onboarding open
        before = self.state()

        for token, mine, theirs, label in (
            (token_a, HOST, OTHER_HOST, "A"), (token_b, OTHER_HOST, HOST, "B"),
        ):
            with self.subTest(label):
                cache.clear()
                own = self.raw("onboarding/state/", mine, token=token)
                self.assertEqual(own.status_code, 200, own.content)
                cache.clear()
                foreign = self.raw("onboarding/state/", theirs, token=token)
                unknown = self.raw("onboarding/state/", theirs, token=BOGUS)
                self.assertEqual((foreign.status_code, foreign.content),
                                 (unknown.status_code, unknown.content))
                self.assertNotIn(order_a.order_number if label == "A" else order_b.order_number,
                                 foreign.content.decode())
        # A second place with the same key on the other host would otherwise be a replay.
        cache.clear()
        replay = self.place(token_a, host=OTHER_HOST, account_id=self.account_b.public_id,
                            submit_key="key-1")
        self.assertGreaterEqual(replay.status_code, 400, replay.content)
        self.assertEqual(PackOrder.objects.count(), 2)
        self.assertEqual(self.state()["files"], before["files"])

    def test_a_contact_held_on_one_store_is_refused_on_the_other_without_naming_the_store(self):
        """The product rule (apps/pack/checkout.match_existing, members.require_free_elsewhere):
        one contact is a member of one live store. The refusal is a generic field message."""
        self.placed()
        before = self.state()

        for changes in ({"email": "ben.buyer@buyer.test"}, {"mobile": "09175550123"}):
            with self.subTest(changes=changes):
                cache.clear()
                response = self.raw("checkout/contact/", OTHER_HOST, **self.contact_fields(**changes))
                self.assertEqual(response.status_code, 409, response.content)
                self.assertEqual(response.json()["error"]["code"], "contact_in_use")
                for marker in (self.site.public_id, self.site.display_name, "anas-store", HOST):
                    self.assertNotIn(marker, response.content.decode())
        self.assertEqual(Member.objects.count(), 1)
        self.assertEqual(self.state()["files"], before["files"])

    def test_a_member_session_of_one_store_reads_nothing_on_the_other(self):
        order_a, proof_a = make_order(self.site, content=PNG + b"A")
        order_b, proof_b = make_order(self.other_site, content=PNG + b"B")
        _, session_a = sessions.create_session(self.site, order_a.member)
        _, session_b = sessions.create_session(self.other_site, order_b.member)

        for path in ("member/pack/", "member/orders/", "member/home/"):
            with self.subTest(path=path):
                for host, own, theirs, order in ((HOST, session_a, session_b, order_a),
                                                 (OTHER_HOST, session_b, session_a, order_b)):
                    cache.clear()
                    mine = self.raw(path, host, token=own)
                    self.assertEqual(mine.status_code, 200, mine.content)
                    cache.clear()
                    foreign = self.raw(path, host, token=theirs)
                    self.assertEqual(foreign.status_code, 401, foreign.content)
                    other_number = (order_b if order is order_a else order_a).order_number
                    self.assertNotIn(other_number, mine.content.decode())
        # My Orders lists my order and only mine.
        cards = self.raw("member/orders/", HOST, token=session_a).json()["cards"]
        self.assertEqual(len(cards), 1)
        self.assertIn(order_a.order_number, str(cards))

    def test_a_proof_file_is_read_only_by_its_own_member_on_its_own_host(self):
        order_a, proof_a = make_order(self.site, content=PNG + b"A")
        order_b, proof_b = make_order(self.other_site, content=PNG + b"B")
        _, session_a = sessions.create_session(self.site, order_a.member)
        _, session_b = sessions.create_session(self.other_site, order_b.member)

        own = self.raw("member/pack/proof/", HOST, token=session_a, proof_id=proof_a.public_id)
        self.assertEqual((own.status_code, own.content), (200, PNG + b"A"))
        unknown = self.raw("member/pack/proof/", HOST, token=session_a,
                           proof_id="pps_00000000000000000000")
        attempts = (
            (HOST, session_a, proof_b.public_id),      # my session, his proof id
            (HOST, session_b, proof_b.public_id),      # his session on my host
            (OTHER_HOST, session_a, proof_b.public_id),  # my session on his host
            (OTHER_HOST, session_a, proof_a.public_id),  # my session, my proof, his host
        )
        for host, token, proof_id in attempts:
            with self.subTest(host=host, proof=proof_id):
                response = self.raw("member/pack/proof/", host, token=token, proof_id=proof_id)
                self.assertIn(response.status_code, (401, 404))
                self.assertNotEqual(response.content, PNG + b"B")
                self.assertNotEqual(response.content, PNG + b"A")
                if response.status_code == 404:
                    self.assertEqual(response.content, unknown.content)

    def test_a_proof_uploaded_for_one_member_is_not_a_resubmission_on_the_other_site(self):
        order_a, _ = make_order(self.site, content=PNG + b"A")
        _, session_a = sessions.create_session(self.site, order_a.member)
        before = self.state()
        data = {"host": OTHER_HOST, "visitor_ip": VISITOR, **self.proof_fields(session_a),
                "account_id": self.account_b.public_id,
                "proof": SimpleUploadedFile("p.png", PNG, content_type="image/png")}

        response = self.client.post(
            API + "member/pack/resubmit/", data, secure=True,
            HTTP_X_REAL_IP="198.51.100.200", HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )

        self.assertEqual(response.status_code, 401, response.content)
        self.assertEqual(self.state(), before)
