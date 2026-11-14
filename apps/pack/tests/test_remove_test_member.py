"""The test-member removal command (ClickUp z8uq9mewx1)."""
from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing.tests.base import make_subscription
from apps.common import private_files
from apps.members.models import Member, MemberAddressEvent, MemberContactAudit, MemberSession
from apps.meta_ads.models import MetaDelivery, MetaOrderContext
from apps.operators.models import OperatorAuditEntry
from apps.pack import purge
from apps.pack.models import CheckoutToken, PackOrder, PackProofSubmission, SanteRegistration, SanteRegistrationEvent
from apps.pack.tests.owner_base import make_order
from apps.payments.tests.base import use_local_private_store

ADDRESS = {"line": "1 Rizal St", "barangay": "Poblacion", "city": "Naga", "province": "Camarines Sur"}


def run(site, member, *flags):
    out = StringIO()
    call_command("pack_remove_test_member", "--site", site.public_id, "--member", member.public_id, *flags, stdout=out)
    return out.getvalue()


def state(member):
    member.refresh_from_db()
    order = PackOrder.objects.get(member=member)
    proof = PackProofSubmission.objects.get(order=order)
    reg = SanteRegistration.objects.get(member=member)
    return {
        "member": (member.full_name, member.email, member.mobile, member.delivery_address),
        "order": (order.buyer_name, order.delivery_address),
        "proof": (proof.sender_name, proof.sender_account_number, proof.reference_number, proof.ip_advisory, proof.file_key),
        "reg": (reg.date_of_birth, reg.desired_username, reg.alternate_username),
        "events": list(SanteRegistrationEvent.objects.filter(registration=reg).values_list("note", "actor_label")),
        "sessions": list(MemberSession.objects.filter(member=member).values_list("pk", flat=True)),
        "audit": list(MemberContactAudit.objects.filter(member=member).values_list("ip_advisory", flat=True)),
        "address": list(MemberAddressEvent.objects.filter(member=member).values_list("old", "new", "ip_advisory")),
        "context": MetaOrderContext.objects.filter(order=order).count(),
        "tokens": CheckoutToken.objects.filter(order_ref=order.pk).count(),
    }


@override_settings(PACK_ENABLED=True, MEMBERS_ENABLED=True, BILLING_ENABLED=True)
class RemoveTestMemberTests(TestCase):
    def setUp(self):
        use_local_private_store(self)
        self.site = make_subscription().site
        self.other_site = make_subscription(email="ben@example.test", name="bens-store").site
        self.order, self.proof = make_order(self.site)
        self.member = self.order.member
        Member.objects.filter(pk=self.member.pk).update(full_name="TEST Maria")
        self.member.refresh_from_db()
        self.fill(self.site, self.order, self.member)
        self.real_order, self.real_proof = make_order(self.site)
        self.real = self.real_order.member
        self.fill(self.site, self.real_order, self.real)
        MetaDelivery.objects.create(site=self.site, event_name="Purchase", event_id="p_" + "a" * 16)

    def fill(self, site, order, member):
        reg = SanteRegistration.objects.create(
            site=site, member=member, pack_order=order, status="awaiting_buyer_details",
            date_of_birth=date(1990, 1, 2), desired_username="maria90", alternate_username="maria91",
            status_entered_at=timezone.now(),
        )
        SanteRegistrationEvent.objects.create(
            site=site, registration=reg, edge="T3", to_status="waiting_for_accept",
            note="accepted", actor_type="member", actor_label="+63917***4567",
        )
        PackOrder.objects.filter(pk=order.pk).update(delivery_address=ADDRESS)
        Member.objects.filter(pk=member.pk).update(delivery_address=ADDRESS)
        CheckoutToken.objects.create(
            site=site, token_hash=f"{order.pk:064d}", expires_at=timezone.now(), order_ref=order.pk,
            pending_contact={"email": "m@x.test"},
        )
        MemberSession.objects.create(
            site=site, member=member, token_hash=f"{member.pk:064d}", ip_advisory="203.0.113.9",
            expires_at=timezone.now() + timedelta(days=1),
        )
        MemberContactAudit.objects.create(
            site=site, member=member, kind="registered", field="email", actor_type="member", ip_advisory="203.0.113.9",
        )
        MemberAddressEvent.objects.create(
            site=site, member=member, old=ADDRESS, new={**ADDRESS, "city": "Legazpi"}, ip_advisory="203.0.113.9",
        )
        MetaOrderContext.objects.create(order=order, site=site, client_ip="203.0.113.9", user_agent="UA")

    def test_dry_run_prints_counts_and_writes_nothing(self):
        before = (state(self.member), state(self.real))
        out = run(self.site, self.member)
        self.assertIn("Dry run", out)
        self.assertIn('"pack_orders": 1', out)
        self.assertEqual((state(self.member), state(self.real)), before)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="pack_test_member_removed").count(), 0)
        self.assertTrue(private_files.store().get(self.proof.file_key))

    def test_apply_blanks_the_target_and_leaves_the_real_member(self):
        real_before = state(self.real)
        deliveries = MetaDelivery.objects.count()
        with self.captureOnCommitCallbacks(execute=True):
            out = run(self.site, self.member, "--apply")
        self.assertIn("Applied", out)
        after = state(self.member)
        self.assertEqual(after["member"][0], "")
        self.assertEqual(after["member"][3], {})
        self.assertNotEqual(after["member"][1], real_before["member"][1])
        self.assertNotIn("buyer.test", after["member"][1])
        self.assertNotEqual(after["member"][2], real_before["member"][2])
        self.assertEqual(after["order"], (purge.BUYER_PLACEHOLDER, {}))
        self.assertEqual(after["proof"], ("", "", "", "", ""))
        self.assertEqual(after["reg"], (None, "", ""))
        self.assertEqual(after["events"], [("", "")])
        self.assertEqual(after["sessions"], [])
        self.assertEqual(after["audit"], [""])
        self.assertEqual(after["address"], [({}, {}, "")])
        self.assertEqual((after["context"], after["tokens"]), (0, 0))
        self.assertEqual(state(self.real), real_before)
        self.assertEqual(MetaDelivery.objects.count(), deliveries)
        self.assertEqual(MetaOrderContext.objects.filter(order=self.real_order).count(), 1)
        self.assertEqual(PackOrder.objects.count(), 2)
        entry = OperatorAuditEntry.objects.get(action="pack_test_member_removed")
        self.assertEqual(entry.target_id, self.site.public_id)
        self.assertEqual(entry.detail["member"], self.member.public_id)
        self.assertEqual(entry.detail["counts"]["pack_proof_submissions"], 1)

    def test_proof_file_is_deleted_only_after_commit(self):
        key = self.proof.file_key
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            run(self.site, self.member, "--apply")
        self.assertEqual(len(callbacks), 1)
        self.assertTrue(private_files.store().get(key))
        self.assertEqual(PackProofSubmission.objects.get(pk=self.proof.pk).file_key, key)
        callbacks[0]()
        with self.assertRaises(Exception):
            private_files.store().get(key)
        self.assertEqual(PackProofSubmission.objects.get(pk=self.proof.pk).file_key, "")
        self.assertTrue(private_files.store().get(self.real_proof.file_key))

    def test_second_apply_changes_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            run(self.site, self.member, "--apply")
        after = state(self.member)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            out = run(self.site, self.member, "--apply")
        self.assertIn("already emptied", out)
        self.assertEqual(callbacks, [])
        self.assertEqual(state(self.member), after)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="pack_test_member_removed").count(), 1)

    def test_refusals_write_nothing(self):
        before = state(self.real)
        with self.assertRaisesMessage(CommandError, "TEST"):
            run(self.site, self.real, "--apply")
        with self.assertRaisesMessage(CommandError, "not on that site"):
            run(self.other_site, self.member, "--apply")
        with self.assertRaisesMessage(CommandError, "No such site"):
            call_command("pack_remove_test_member", "--site", "site_nope", "--member", self.member.public_id, "--apply")
        with self.assertRaisesMessage(CommandError, "No such member"):
            call_command("pack_remove_test_member", "--site", self.site.public_id, "--member", "mem_nope", "--apply")
        self.assertEqual(state(self.real), before)
        self.assertEqual(state(self.member)["member"][0], "TEST Maria")
        self.assertEqual(OperatorAuditEntry.objects.filter(action="pack_test_member_removed").count(), 0)

    def test_the_test_prefix_is_accepted_in_any_letter_case(self):
        for name in ("test shop guest", "Test Maria", "  TEST Maria"):
            with self.subTest(name=name):
                Member.objects.filter(pk=self.member.pk).update(full_name=name)
                before = state(self.member)
                out = run(self.site, self.member)
                self.assertIn("Dry run", out)
                self.assertIn('"pack_orders": 1', out)
                self.assertEqual(state(self.member), before)

    def test_apply_blanks_a_lowercase_test_name(self):
        Member.objects.filter(pk=self.member.pk).update(full_name="test maria")
        with self.captureOnCommitCallbacks(execute=True):
            out = run(self.site, self.member, "--apply")
        self.assertIn("Applied", out)
        self.assertEqual(Member.objects.get(pk=self.member.pk).full_name, "")

    def test_it_refuses_names_that_only_look_like_the_prefix(self):
        for name in ("TESTER Bob", "test", "Contest Guest", "tester bob"):
            with self.subTest(name=name):
                Member.objects.filter(pk=self.member.pk).update(full_name=name)
                with self.assertRaisesMessage(CommandError, 'does not start with "TEST " (any case)'):
                    run(self.site, self.member, "--apply")
                self.assertEqual(Member.objects.get(pk=self.member.pk).full_name, name)

    def test_an_order_on_another_site_is_refused(self):
        PackOrder.objects.filter(pk=self.order.pk).update(site=self.other_site)
        with self.assertRaisesMessage(CommandError, "not on this site"):
            run(self.site, self.member, "--apply")
        self.assertEqual(Member.objects.get(pk=self.member.pk).full_name, "TEST Maria")
