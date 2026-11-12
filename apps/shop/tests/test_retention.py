"""Disposal and export of the shop rows, the models' guards and the deploy check."""
from django.core.checks import Error, Warning
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from apps.accounts import retention as rules
from apps.shop import constants as C, retention
from apps.shop.checks import check_shop_dependencies
from apps.shop.models import (
    ImmutableRecordError, ShopOrder, ShopOrderEvent, ShopOrderLine, ShopProofSubmission,
)
from apps.shop.tests.base import ShopTestCase


@override_settings(RETENTION_ENABLED=True)
class ShopRetentionTests(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.placed()
        self.account_row = self.site.account

    def test_export_lists_orders_and_proofs_without_file_key_or_token(self):
        data = retention.export_shop_of(self.account_row)
        order, proof = data["shop_orders"][0], data["shop_proof_submissions"][0]
        self.assertEqual((order["number"], order["receiver_name"], order["total_centavos"]),
                         ("SO00001", "Maria Santos", 10000))
        self.assertEqual(order["lines"][0]["code"], "FX-A")
        self.assertNotIn("file_key", proof)
        text = str(data)
        self.assertNotIn("view_token", text)
        self.assertNotIn("fingerprint", text)
        self.assertNotIn("idempotency", text)

    def test_blanking_deletes_files_and_blanks_the_person_but_keeps_rows_and_money(self):
        counts = retention.blank_shop_of(self.account_row)
        self.assertEqual((counts["shop_orders"], counts["shop_proof_submissions"], counts["shop_proof_files"]), (1, 1, 1))
        self.assertEqual(self.proof_files(), [])
        order = ShopOrder.objects.get()
        self.assertEqual((order.receiver_name, order.receiver_mobile, order.contact_email, order.delivery_address),
                         ("", "", "", {}))
        self.assertEqual((order.total_centavos, order.order_number), (10000, "SO00001"))
        proof = ShopProofSubmission.objects.get()
        self.assertEqual((proof.reference_number, proof.account_name, proof.ip_advisory, proof.file_key),
                         ("", "", "", ""))
        self.assertEqual(proof.amount_sent_centavos, 10000)
        self.assertEqual(ShopOrderLine.objects.count(), 1)

    def test_a_dry_run_changes_nothing(self):
        retention.blank_shop_of(self.account_row, dry_run=True)
        self.assertEqual(len(self.proof_files()), 1)
        self.assertEqual(ShopOrder.objects.get().receiver_name, "Maria Santos")

    def test_another_accounts_rows_are_untouched(self):
        retention.blank_shop_of(self.other_site.account)
        self.assertEqual(ShopOrder.objects.get().receiver_name, "Maria Santos")
        self.assertEqual(len(self.proof_files()), 1)

    def test_everything_is_empty_while_the_switch_is_off(self):
        with override_settings(SHOP_ENABLED=False):
            self.assertEqual(retention.blank_shop_of(self.account_row), {})
            self.assertEqual(retention.export_shop_of(self.account_row), {})
            self.assertEqual(retention.blanked(), {})
        self.assertEqual(ShopOrder.objects.get().receiver_name, "Maria Santos")

    def test_the_account_disposal_and_export_include_the_shop_rows(self):
        from django.utils import timezone
        counts = rules.dispose_account(self.account_row, now=timezone.now(), rule="request", dry_run=True)
        self.assertEqual(counts["shop_orders"], 1)
        exported = rules.export_account_data(self.account_row, actor=None, request_ref="ref-1")
        self.assertEqual(len(exported["shop_orders"]), 1)


class ModelGuardTests(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.placed()
        self.order = ShopOrder.objects.get()

    def test_orders_are_never_deleted(self):
        with self.assertRaises(ImmutableRecordError):
            self.order.delete()

    def test_lines_proofs_and_events_are_append_only(self):
        for model in (ShopOrderLine, ShopProofSubmission, ShopOrderEvent):
            row = model.objects.first()
            with self.subTest(model=model.__name__):
                with self.assertRaises(ImmutableRecordError):
                    row.save()
                with self.assertRaises(ImmutableRecordError):
                    row.delete()
                with self.assertRaises(ImmutableRecordError):
                    model.objects.all().update()
                with self.assertRaises(ImmutableRecordError):
                    model.objects.all().delete()

    def test_the_database_refuses_impossible_states(self):
        bad = {
            "cancelled without a note": dict(payment_status=C.CANCELLED, cancel_note=""),
            "shipped before verified": dict(shipped_on="2026-11-01"),
            "delivered before shipped": dict(delivered_on="2026-11-01"),
            "a total that does not add up": dict(total_centavos=1),
            "a guest with a member mode": dict(ship_mode=C.HOME),
        }
        for label, changes in bad.items():
            with self.subTest(label), self.assertRaises(IntegrityError), transaction.atomic():
                ShopOrder.objects.filter(pk=self.order.pk).update(**changes)

    def test_numbers_and_keys_are_unique_per_site(self):
        for changes in (dict(order_number=self.order.order_number),):
            with self.assertRaises(IntegrityError), transaction.atomic():
                ShopOrder.objects.create(
                    site=self.site, buyer_kind="guest", ship_mode="guest", receiver_name="x",
                    receiver_mobile="+639171234567", payment_status="pending_review", subtotal_centavos=1,
                    total_centavos=1, shipping_policy="zero", quote_fingerprint="f", idempotency_key="other",
                    view_token_hash="t", **changes,
                )


class ChecksTests(SimpleTestCase):
    def ids(self):
        return {m.id for m in check_shop_dependencies(None)}

    @override_settings(SHOP_ENABLED=False, SANTE_CATALOG_ENABLED=False)
    def test_off_means_silent(self):
        self.assertEqual(self.ids(), set())

    @override_settings(SHOP_ENABLED=True, SANTE_CATALOG_ENABLED=False, PAYMENT_METHODS_ENABLED=False,
                       MEMBERS_ENABLED=False, STOREFRONT_ENABLED=False)
    def test_on_needs_the_four_switches(self):
        self.assertTrue({"shop.E001", "shop.E002", "shop.E003", "shop.E004"} <= self.ids())

    @override_settings(SHOP_ENABLED=True, SANTE_CATALOG_ENABLED=True, PAYMENT_METHODS_ENABLED=True,
                       MEMBERS_ENABLED=True, STOREFRONT_ENABLED=True, PACK_ENABLED=False)
    def test_on_with_dependencies_has_no_errors_and_the_pack_is_not_needed(self):
        found = check_shop_dependencies(None)
        self.assertFalse([m for m in found if isinstance(m, Error)])

    @override_settings(SHOP_ENABLED=True, SANTE_CATALOG_ENABLED=True, PAYMENT_METHODS_ENABLED=True,
                       MEMBERS_ENABLED=True, STOREFRONT_ENABLED=True, SHOP_SHIPPING_POLICY="free")
    def test_an_unknown_shipping_policy_is_an_error(self):
        self.assertIn("shop.E005", self.ids())

    @override_settings(SHOP_ENABLED=True, SANTE_CATALOG_ENABLED=True, PAYMENT_METHODS_ENABLED=True,
                       MEMBERS_ENABLED=True, STOREFRONT_ENABLED=True, REDIS_URL="")
    def test_no_redis_is_a_warning(self):
        warnings = [m for m in check_shop_dependencies(None) if isinstance(m, Warning)]
        self.assertIn("shop.W002", {m.id for m in warnings})
