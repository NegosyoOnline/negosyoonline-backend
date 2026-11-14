"""The test-order removal command (ClickUp z8uq9mf15g)."""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from apps.operators.models import OperatorAuditEntry
from apps.shop.models import ProductReview, ShopOrder, ShopOrderEvent, ShopOrderLine, ShopProofSubmission
from apps.shop.tests.test_product_reviews import ReviewTestCase


def run(site, order, *flags):
    out = StringIO()
    call_command("shop_remove_test_order", "--site", site.public_id, "--order", order, *flags, stdout=out)
    return out.getvalue()


class RemoveTestOrderTests(ReviewTestCase):
    def setUp(self):
        super().setUp()
        body = self.delivered(receiver_name="TEST Maria")
        self.public_id = body["order_id"]
        self.assertEqual(
            self.review(self.public_id, view_token=body["view_token"], rating=5, body="Great").status_code, 200
        )
        self.order = ShopOrder.objects.get(public_id=self.public_id)

    def snapshot(self):
        order = ShopOrder.objects.get(pk=self.order.pk)
        proof = ShopProofSubmission.objects.get(order=order)
        return {
            "order": (order.receiver_name, order.receiver_mobile, order.contact_email, order.delivery_address),
            "proof": (proof.reference_number, proof.account_name, proof.ip_advisory, proof.file_key),
            "reviews": ProductReview.objects.count(),
            "files": len(self.proof_files()),
        }

    def test_a_dry_run_writes_nothing(self):
        before = self.snapshot()
        out = run(self.site, self.public_id)
        self.assertIn("Dry run", out)
        self.assertIn('"reviews_deleted": 1', out)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(before["files"], 1)
        self.assertFalse(OperatorAuditEntry.objects.filter(action="shop_test_order_removed").exists())

    def test_apply_blanks_deletes_and_keeps_the_order(self):
        total, lines, events = self.order.total_centavos, ShopOrderLine.objects.count(), ShopOrderEvent.objects.count()
        with self.captureOnCommitCallbacks(execute=True):
            out = run(self.site, self.order.order_number, "--apply")
        self.assertIn("Applied", out)
        self.assertEqual(self.snapshot(), {
            "order": ("", "", "", {}), "proof": ("", "", "", ""), "reviews": 0, "files": 0,
        })
        order = ShopOrder.objects.get(pk=self.order.pk)
        self.assertEqual((order.total_centavos, order.order_number), (total, "SO00001"))
        self.assertEqual((ShopOrderLine.objects.count(), ShopOrderEvent.objects.count()), (lines, events))
        entry = OperatorAuditEntry.objects.get(action="shop_test_order_removed")
        self.assertEqual(entry.target_id, self.site.public_id)
        self.assertNotIn("TEST", str(entry.detail))

    def test_it_refuses_a_non_test_order(self):
        order = ShopOrder.objects.get(pk=self.order.pk)
        ShopOrder.objects.filter(pk=order.pk).update(receiver_name="Maria Santos")
        before = self.snapshot()
        with self.assertRaisesMessage(CommandError, "does not start with"):
            run(self.site, self.public_id, "--apply")
        self.assertEqual(self.snapshot(), before)

    def test_the_test_prefix_is_accepted_in_any_letter_case(self):
        for name in ("test shop guest", "Test Shop Guest", "  TEST Shop Guest"):
            with self.subTest(name=name):
                ShopOrder.objects.filter(pk=self.order.pk).update(receiver_name=name)
                before = self.snapshot()
                out = run(self.site, self.public_id)
                self.assertIn("Dry run", out)
                self.assertIn('"orders_blanked": 1', out)
                self.assertEqual(self.snapshot(), before)

    def test_apply_blanks_a_lowercase_test_name(self):
        ShopOrder.objects.filter(pk=self.order.pk).update(receiver_name="test shop guest")
        with self.captureOnCommitCallbacks(execute=True):
            out = run(self.site, self.public_id, "--apply")
        self.assertIn("Applied", out)
        self.assertEqual(self.snapshot(), {
            "order": ("", "", "", {}), "proof": ("", "", "", ""), "reviews": 0, "files": 0,
        })

    def test_it_refuses_names_that_only_look_like_the_prefix(self):
        for name in ("TESTER Bob", "test", "Contest Guest", "tester bob"):
            with self.subTest(name=name):
                ShopOrder.objects.filter(pk=self.order.pk).update(receiver_name=name)
                before = self.snapshot()
                with self.assertRaisesMessage(CommandError, 'does not start with "TEST " (any case)'):
                    run(self.site, self.public_id, "--apply")
                self.assertEqual(self.snapshot(), before)

    def test_it_refuses_an_order_on_another_site(self):
        before = self.snapshot()
        with self.assertRaisesMessage(CommandError, "not on that site"):
            run(self.other_site, self.public_id, "--apply")
        with self.assertRaisesMessage(CommandError, "No such order"):
            run(self.other_site, self.order.order_number, "--apply")
        self.assertEqual(self.snapshot(), before)

    def test_it_refuses_an_unknown_site_or_order(self):
        with self.assertRaisesMessage(CommandError, "No such order"):
            run(self.site, "sho_nope")
        with self.assertRaisesMessage(CommandError, "No such site"):
            call_command("shop_remove_test_order", "--site", "site_nope", "--order", self.public_id)

    def test_a_second_run_changes_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            run(self.site, self.public_id, "--apply")
        after = self.snapshot()
        with self.captureOnCommitCallbacks(execute=True):
            out = run(self.site, self.public_id, "--apply")
        self.assertIn("already emptied", out)
        self.assertEqual(self.snapshot(), after)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="shop_test_order_removed").count(), 1)
