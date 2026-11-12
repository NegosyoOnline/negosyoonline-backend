"""Billing proofs in the private file store (Billing Build B2a-S): where they are kept, what
a failing store does, and that nothing about the store reaches an answer."""
import tempfile
from pathlib import Path
from unittest import mock

from django.core.checks import Error
from django.test import SimpleTestCase, TestCase, override_settings

from apps.billing import plan, proofs, services
from apps.billing.models import BankTransferEvent, BankTransferPayment
from apps.billing.tests.base import (
    JPEG, PDF, PNG, T0, TEST_ACCOUNTS, days, make_operator, make_subscription, submit,
    transfers_on,
)
from apps.billing.tests.test_transfer_api import TransferApiCase
from apps.common import private_files
from apps.common.errors import ApiError

TOKEN = "vercel_blob_rw_StoreId123_SecretPartOfTheToken"
KEY_SHAPE = r"^billing/proofs/[0-9a-f]{32}\.(jpg|png|pdf)$"


class AvailableTests(SimpleTestCase):
    def test_a_token_makes_a_store_available_with_debug_off(self):
        with override_settings(PRIVATE_BLOB_READ_WRITE_TOKEN=TOKEN, DEBUG=False):
            self.assertTrue(proofs.available())

    def test_no_token_and_debug_off_means_no_store(self):
        with override_settings(PRIVATE_BLOB_READ_WRITE_TOKEN="", DEBUG=False):
            self.assertFalse(proofs.available())

    def test_collection_is_live_only_with_a_store(self):
        switches = dict(
            BILLING_ENABLED=True, BILLING_BANK_TRANSFER_ENABLED=True,
            BILLING_TRANSFER_ACCOUNTS=TEST_ACCOUNTS,
        )
        with override_settings(PRIVATE_BLOB_READ_WRITE_TOKEN="", DEBUG=False, **switches):
            self.assertFalse(plan.collection_live())
        with override_settings(PRIVATE_BLOB_READ_WRITE_TOKEN=TOKEN, DEBUG=False, **switches):
            self.assertTrue(plan.collection_live())

    def test_the_check_names_the_missing_store(self):
        with override_settings(
            BILLING_BANK_TRANSFER_ENABLED=True, PRIVATE_BLOB_READ_WRITE_TOKEN="", DEBUG=False
        ):
            errors = proofs.check_bank_transfer(None)
        self.assertIn("billing.E002", [e.id for e in errors])
        self.assertTrue(all(isinstance(e, Error) for e in errors))


class KeyTests(SimpleTestCase):
    def local(self, folder):
        return override_settings(
            PRIVATE_BLOB_READ_WRITE_TOKEN="", PRIVATE_FILES_DIR=folder, DEBUG=True
        )

    def test_a_proof_is_saved_under_the_billing_prefix_in_the_local_folder(self):
        with tempfile.TemporaryDirectory() as folder, self.local(folder):
            key = proofs.save(JPEG, "image/jpeg")
            self.assertRegex(key, KEY_SHAPE)
            self.assertLessEqual(len(key), 64)  # proof_key is CharField(64)
            self.assertEqual(proofs.read(key), JPEG)
            self.assertTrue((Path(folder) / key).exists())
            proofs.discard(key)
            self.assertFalse((Path(folder) / key).exists())

    def test_a_key_that_is_not_a_store_key_is_refused(self):
        with tempfile.TemporaryDirectory() as folder, self.local(folder):
            for key in ("0" * 32, "../" + "0" * 32, "billing/proofs/../x.jpg"):
                with self.assertRaises(ValueError):
                    proofs.read(key)

    def test_each_type_keeps_its_extension(self):
        with tempfile.TemporaryDirectory() as folder, self.local(folder):
            extensions = {
                proofs.save(c, t).rsplit(".", 1)[1]
                for c, t in ((JPEG, "image/jpeg"), (PNG, "image/png"), (PDF, "application/pdf"))
            }
        self.assertEqual(extensions, {"jpg", "png", "pdf"})


@override_settings(BILLING_ENABLED=True)
class FailingStoreTests(TestCase):
    def setUp(self):
        on = transfers_on()
        self.store = on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        self.subscription = make_subscription()
        self.operator = make_operator()

    def test_a_failing_store_answers_503_and_writes_nothing(self):
        with mock.patch("apps.billing.proofs.save", side_effect=private_files.StoreError("down")):
            with self.assertRaises(ApiError) as raised:
                submit(self.subscription, T0 + days(1))
        self.assertEqual(
            (raised.exception.code, raised.exception.status), ("proof_store_unavailable", 503)
        )
        self.assertEqual(
            (BankTransferPayment.objects.count(), BankTransferEvent.objects.count()), (0, 0)
        )
        self.assertEqual(self.store.files, {})

    def test_a_failed_store_leaves_a_proof_under_review_in_place(self):
        first = submit(self.subscription, T0 + days(1))
        with mock.patch(
            "apps.billing.proofs.save", side_effect=private_files.StoreError("down")
        ), self.assertRaises(ApiError):
            submit(self.subscription, T0 + days(2), content=PNG)
        first.refresh_from_db()
        self.assertEqual(first.status, BankTransferPayment.Status.SUBMITTED)

    def test_a_saved_file_is_removed_if_its_row_fails(self):
        with mock.patch(
            "apps.billing.services._transfer_event", side_effect=RuntimeError("boom")
        ), self.assertRaises(RuntimeError):
            submit(self.subscription, T0 + days(1))
        self.assertEqual(BankTransferPayment.objects.count(), 0)
        self.assertEqual(self.store.files, {})

    def test_a_failing_read_answers_503(self):
        payment = submit(self.subscription, T0 + days(1))
        with mock.patch("apps.billing.proofs.read", side_effect=private_files.StoreError("down")):
            with self.assertRaises(ApiError) as owner:
                services.proof_for_owner(self.subscription, payment.public_id)
            with self.assertRaises(ApiError) as staff:
                services.proof_for_operator(payment, self.operator)
        self.assertEqual((owner.exception.status, staff.exception.status), (503, 503))

    def test_a_key_the_store_refuses_answers_404(self):
        payment = submit(self.subscription, T0 + days(1))
        with mock.patch("apps.billing.proofs.read", side_effect=ValueError("not a key")):
            with self.assertRaises(ApiError) as raised:
                services.proof_for_owner(self.subscription, payment.public_id)
        self.assertEqual(raised.exception.status, 404)

    def test_the_stored_key_has_the_billing_shape(self):
        payment = submit(self.subscription, T0 + days(1))
        self.assertRegex(payment.proof_key, KEY_SHAPE)


class RouteTests(TransferApiCase):
    def test_no_store_detail_reaches_an_answer(self):
        sent = self.upload()
        self.assertEqual(sent.status_code, 201, sent.content)
        key = BankTransferPayment.objects.get().proof_key
        payment_id = sent.json()["id"]
        listing = self.get(self.base + "payments/")
        detail = self.get(self.base + f"payments/{payment_id}/")
        proof = self.get(self.base + f"payments/{payment_id}/proof/")
        for response in (sent, listing, detail):
            self.assertNotIn(key.encode(), response.content)
            self.assertNotIn(b"billing/proofs", response.content)
        self.assertEqual(proof["Cache-Control"], "private, no-store")
        self.assertEqual(proof["X-Content-Type-Options"], "nosniff")

    def test_a_failing_store_answers_503_over_http(self):
        with mock.patch("apps.billing.proofs.save", side_effect=private_files.StoreError("down")):
            response = self.upload()
        self.error(response, 503, "proof_store_unavailable")
        self.assertEqual(BankTransferPayment.objects.count(), 0)

    def test_the_same_file_twice_returns_the_same_payment(self):
        first = self.upload().json()
        second = self.upload().json()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.files), 1)
