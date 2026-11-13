"""The day-90 buyer purge (ClickUp z8uq9mep8p, plan 2026-11-13-buyer-portal-z8uq9mbk0m, C2 tests)."""
from datetime import date
from unittest import mock

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from apps.billing import services
from apps.billing.models import Subscription
from apps.members.models import Member, MemberAddressEvent, MemberContactAudit, MemberSession
from apps.pack import purge, registration, tasks
from apps.pack.models import (
    CheckoutToken, PackOrder, PackProofSubmission, SanteRegistration, SanteRegistrationEvent,
)
from apps.pack.tests.owner_base import make_order
from apps.pack.tests.test_portal import POINT, SECOND, SUSPENDED_AT, PortalBase, days
from apps.common.errors import ApiError

ADDRESS = {"line": "1 Rizal St", "barangay": "Poblacion", "city": "Naga", "province": "Camarines Sur"}


def personal_state(site):
    """Everything the purge is meant to clear, for one site."""
    return {
        "registration": list(SanteRegistration.objects.filter(site=site).values_list(
            "date_of_birth", "desired_username", "alternate_username")),
        "events": list(SanteRegistrationEvent.objects.filter(site=site).values_list("note", "actor_label")),
        "orders": list(PackOrder.objects.filter(site=site).values_list("buyer_name", "delivery_address")),
        "members": list(Member.objects.filter(site=site).values_list("delivery_address", flat=True)),
        "tokens": CheckoutToken.objects.filter(site=site).count(),
        "session_ips": list(MemberSession.objects.filter(site=site).values_list("ip_advisory", flat=True)),
        "audit_ips": list(MemberContactAudit.objects.filter(site=site).values_list("ip_advisory", flat=True)),
        "address_events": list(MemberAddressEvent.objects.filter(site=site).values_list("old", "new", "ip_advisory")),
        "proofs": list(PackProofSubmission.objects.filter(site=site).values_list(
            "sender_name", "sender_account_number", "sender_bank", "ip_advisory", "reference_number",
            "account_public_id", "account_name", "account_number_last4", "transferred_at")),
    }


@override_settings(PACK_PURGE_ENABLED=True)
class PurgeBase(PortalBase):
    def setUp(self):
        super().setUp()
        self.fill(self.site, self.order, self.member)
        self.suspend()

    def fill(self, site, order, member):
        reg = SanteRegistration.objects.create(
            site=site, member=member, pack_order=order, status="awaiting_buyer_details",
            date_of_birth=date(1990, 1, 2), desired_username="maria90", alternate_username="maria91",
            status_entered_at=timezone.now(),
        )
        SanteRegistrationEvent.objects.create(
            site=site, registration=reg, edge="T3", to_status="waiting_for_accept",
            note="Santé Membership Terms accepted", actor_type="member", actor_label="+63917***4567",
        )
        PackOrder.objects.filter(pk=order.pk).update(delivery_address=ADDRESS)
        Member.objects.filter(pk=member.pk).update(delivery_address=ADDRESS)
        CheckoutToken.objects.create(
            site=site, token_hash=f"{site.pk:064d}", expires_at=timezone.now(), pending_contact={"email": "m@x.test"},
        )
        MemberSession.objects.filter(site=site).update(ip_advisory="203.0.113.9")
        MemberContactAudit.objects.create(
            site=site, member=member, kind="registered", field="email", actor_type="member",
            ip_advisory="203.0.113.9",
        )
        MemberAddressEvent.objects.create(
            site=site, member=member, old=ADDRESS, new={**ADDRESS, "city": "Legazpi"}, ip_advisory="203.0.113.9",
        )
        return reg

    def purge_at(self, moment):
        self.at(moment)
        return purge.run(moment)


class PurgeScopeTests(PurgeBase):
    def test_at_the_point_every_listed_field_is_cleared(self):
        self.assertEqual(self.purge_at(POINT), {"purged": 1, "failed": 0})

        after = personal_state(self.site)
        self.assertEqual(after["registration"], [(None, "", "")])
        self.assertEqual(after["events"], [("", "")])
        self.assertEqual(after["orders"], [(purge.BUYER_PLACEHOLDER, {})])
        self.assertEqual(after["members"], [{}])
        self.assertEqual(after["tokens"], 0)
        self.assertEqual(after["session_ips"], [""])
        self.assertEqual(after["audit_ips"], [""])
        self.assertEqual(after["address_events"], [({}, {}, "")])
        self.assertEqual(MemberAddressEvent.objects.filter(site=self.site).count(), 1)
        sender_name, number, bank, ip, ref, account_id, account_name, last4, transferred = after["proofs"][0]
        self.assertEqual((sender_name, number, bank, ip, ref, account_id, account_name, last4), ("",) * 8)
        self.assertIsNotNone(SanteRegistration.objects.get(site=self.site).details_purged_at)

    def test_money_status_and_rows_stay(self):
        before = PackOrder.objects.values("price_centavos", "payment_status", "order_number").get(site=self.site)
        proof = PackProofSubmission.objects.get(site=self.site)

        self.purge_at(POINT)

        self.assertEqual(
            PackOrder.objects.values("price_centavos", "payment_status", "order_number").get(site=self.site), before
        )
        self.assertEqual(PackProofSubmission.objects.get(site=self.site).amount_sent_centavos, proof.amount_sent_centavos)
        self.assertEqual(SanteRegistration.objects.get(site=self.site).status, "awaiting_buyer_details")
        self.assertEqual(SanteRegistrationEvent.objects.filter(site=self.site).count(), 1)

    def test_nothing_before_the_point(self):
        before = personal_state(self.site)

        self.assertEqual(self.purge_at(POINT - SECOND), {"purged": 0, "failed": 0})

        self.assertEqual(personal_state(self.site), before)

    def test_a_second_pass_changes_nothing(self):
        self.purge_at(POINT)
        after = personal_state(self.site)
        stamp = SanteRegistration.objects.get(site=self.site).details_purged_at

        self.assertEqual(self.purge_at(POINT + days(3)), {"purged": 1, "failed": 0})

        self.assertEqual(personal_state(self.site), after)
        self.assertEqual(SanteRegistration.objects.get(site=self.site).details_purged_at, stamp)

    def test_rows_made_after_a_restore_are_cleared_by_the_next_pass(self):
        self.purge_at(POINT)
        CheckoutToken.objects.create(
            site=self.site, token_hash="b" * 64, expires_at=timezone.now(), pending_contact={"email": "m@x.test"}
        )

        self.purge_at(POINT + days(1))

        self.assertEqual(CheckoutToken.objects.filter(site=self.site).count(), 0)

    def test_an_unserved_site_is_purged_and_so_is_a_removed_one(self):
        from apps.sites.models import Site
        Site.objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())

        self.assertEqual(self.purge_at(POINT), {"purged": 1, "failed": 0})

        self.assertEqual(personal_state(self.site)["orders"], [(purge.BUYER_PLACEHOLDER, {})])

    def test_another_sites_buyers_are_untouched(self):
        other_order, _ = make_order(self.other_site)
        self.fill(self.other_site, other_order, other_order.member)
        before = personal_state(self.other_site)

        self.purge_at(POINT + days(1))

        self.assertEqual(personal_state(self.other_site), before)
        self.assertEqual(PackOrder.objects.get(site=self.other_site).buyer_name, other_order.buyer_name)

    def test_a_reactivated_site_is_not_purged(self):
        subscription = Subscription.objects.get(site=self.site)
        Subscription.objects.filter(pk=subscription.pk).update(deletion_due_at=None, download_until=None)
        before = personal_state(self.site)

        self.assertEqual(self.purge_at(POINT + days(1)), {"purged": 0, "failed": 0})

        self.assertEqual(personal_state(self.site), before)

    def test_a_site_with_no_clock_is_not_purged(self):
        Subscription.objects.filter(site=self.site).update(deletion_due_at=None)
        before = personal_state(self.site)

        self.purge_at(POINT + days(60))

        self.assertEqual(personal_state(self.site), before)

    def test_a_billing_exempt_site_is_not_purged(self):
        Subscription.objects.filter(site=self.site).update(billing_exempt=True)
        before = personal_state(self.site)

        self.purge_at(POINT + days(60))

        self.assertEqual(personal_state(self.site), before)

    def test_a_failing_site_is_logged_with_its_pk_only_and_the_next_site_still_runs(self):
        second = self.other_site
        calls = []

        def purge_one(site_id, now=None):
            calls.append(site_id)
            if site_id == self.site.pk:
                raise RuntimeError("Maria Santos 1990-01-02")
            return True

        self.at(POINT)
        with mock.patch.object(purge, "due_site_ids", return_value=[self.site.pk, second.pk]), \
                mock.patch.object(purge, "purge_site", side_effect=purge_one):
            with self.assertLogs("apps.pack.purge", level="ERROR") as logged:
                result = purge.run(POINT)

        self.assertEqual(result, {"purged": 1, "failed": 1})
        self.assertEqual(calls, [self.site.pk, second.pk])
        text = "\n".join(logged.output)
        self.assertIn(f"site {self.site.pk}", text)
        self.assertNotIn("Maria", text)
        self.assertNotIn("1990", text)

    def test_the_deletion_date_is_read_again_under_the_lock(self):
        with mock.patch.object(purge, "due_site_ids", return_value=[self.site.pk]):
            self.at(POINT - days(1))
            self.assertEqual(purge.run(POINT - days(1)), {"purged": 0, "failed": 0})


class PurgeProofFileTests(PurgeBase):
    def test_the_export_is_ready_from_the_manila_day_after_the_verified_deploy(self):
        self.assertEqual(purge.PROOF_EXPORT_READY_FROM, date(2026, 11, 14))

    def test_images_go_when_the_ready_day_is_not_after_the_service_end_day(self):
        service_end_day = purge.portal.manila(SUSPENDED_AT).date()
        self.assertLessEqual(purge.PROOF_EXPORT_READY_FROM, service_end_day)
        self.assertTrue(purge.files_deletable(SUSPENDED_AT))

        self.purge_at(POINT)

        self.assertEqual(self.proof_files(), [])

    def test_images_stay_while_the_constant_is_none(self):
        with mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", None):
            self.purge_at(POINT)

        self.assertEqual(len(self.proof_files()), 1)
        self.assertNotEqual(PackProofSubmission.objects.get(site=self.site).file_key, "")

    def test_images_stay_when_the_site_ended_before_the_ready_day(self):
        ended_day = purge.portal.manila(SUSPENDED_AT).date()
        with mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", date.fromordinal(ended_day.toordinal() + 1)):
            self.purge_at(POINT)

        self.assertEqual(len(self.proof_files()), 1)

    def test_images_go_when_the_export_held_them_from_before_the_window(self):
        with mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", date(2026, 12, 1)):
            self.purge_at(POINT)

        self.assertEqual(self.proof_files(), [])
        self.assertEqual(PackProofSubmission.objects.get(site=self.site).file_key, "")

    def test_images_stay_when_the_export_held_them_only_after_the_service_ended(self):
        service_end_day = purge.portal.manila(SUSPENDED_AT).date()
        later = date.fromordinal(service_end_day.toordinal() + 1)
        with mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", later):
            self.purge_at(POINT)

        self.assertEqual(len(self.proof_files()), 1)

    def test_a_store_that_is_down_leaves_the_key_for_the_next_pass(self):
        from apps.common.private_files import StoreError
        with mock.patch.object(purge, "PROOF_EXPORT_READY_FROM", date(2026, 12, 1)):
            with mock.patch.object(purge.private_files, "delete", side_effect=StoreError("down")):
                with self.assertLogs("apps.pack.purge", level="ERROR"):
                    self.assertEqual(self.purge_at(POINT), {"purged": 0, "failed": 1})
            self.assertEqual(personal_state(self.site)["orders"], [(purge.BUYER_PLACEHOLDER, {})])
            self.assertEqual(len(self.proof_files()), 1)

            self.assertEqual(self.purge_at(POINT + days(1)), {"purged": 1, "failed": 0})

        self.assertEqual(self.proof_files(), [])


class RegistrationClosedTests(PurgeBase):
    """Every registration action is refused once the point is reached, whether or not the purge ran."""

    def test_the_actions_are_refused_at_the_point(self):
        reg = SanteRegistration.objects.get(site=self.site)
        self.at(POINT)

        with self.assertRaises(ApiError) as caught:
            registration.submit_details(
                self.site, self.member, date_of_birth="1990-01-02", desired_username="maria90",
            )
        self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(ApiError):
            registration.save_delivery(self.site, self.member, self.order, ADDRESS)
        SanteRegistration.objects.filter(pk=reg.pk).update(status="waiting_for_accept")
        registration.on_payment_verified(self.order)  # T8 is skipped
        self.assertEqual(SanteRegistration.objects.get(pk=reg.pk).status, "waiting_for_accept")

    def test_they_are_not_refused_before_the_point(self):
        self.at(POINT - SECOND)

        self.assertFalse(registration.past_deletion_point(self.site.pk))

    def test_a_billing_read_error_counts_as_closed(self):
        with mock.patch("apps.billing.services.deletion_reached", side_effect=RuntimeError("x")):
            self.assertTrue(registration.past_deletion_point(self.site.pk))


class PurgeSwitchTests(PurgeBase):
    @override_settings(PACK_PURGE_ENABLED=False)
    def test_with_the_switch_off_nothing_runs(self):
        before = personal_state(self.site)
        self.at(POINT + days(5))

        with mock.patch.object(purge, "due_site_ids") as listed:
            self.assertEqual(tasks.run_purge.apply().result, {"skipped": "off"})
            listed.assert_not_called()

        self.assertEqual(personal_state(self.site), before)

    @override_settings(PACK_ENABLED=False)
    def test_with_pack_off_nothing_runs(self):
        self.assertEqual(purge.run(POINT), {"skipped": "off"})

    def test_the_switches_default_off_and_the_beat_has_both_jobs(self):
        self.assertFalse(settings.PACK_BUYER_NOTICE_ENABLED)
        entries = {key: value["task"] for key, value in settings.CELERY_BEAT_SCHEDULE.items()}
        self.assertEqual(entries["pack-purge"], "pack.purge")
        self.assertEqual(entries["pack-buyer-notice"], "pack.buyer_notice")
        self.assertEqual(settings.CELERY_BEAT_SCHEDULE["pack-purge"]["schedule"], 3600)

    def test_the_task_purges_when_on(self):
        self.at(POINT)
        with mock.patch("apps.pack.purge.timezone") as clock:
            clock.now.return_value = POINT
            self.assertEqual(tasks.run_purge.apply().result, {"purged": 1, "failed": 0})

    def test_the_banner_carries_the_deletion_line_only_while_the_purge_is_on(self):
        self.at(POINT - days(5))
        on = self.portal("site/").json()["banner"]
        self.assertTrue(on.endswith(purge.portal.DELETION_LINE))
        with override_settings(PACK_PURGE_ENABLED=False):
            off = self.portal("site/").json()["banner"]
        self.assertNotIn("deleted", off)
        self.assertTrue(off.endswith("After that this page closes."))
        self.assertEqual(on, off + " " + purge.portal.DELETION_LINE)


class DeletionReachedTests(PurgeBase):
    def test_reached_at_the_instant_and_not_before(self):
        self.assertFalse(services.deletion_reached(self.site, POINT - SECOND))
        self.assertTrue(services.deletion_reached(self.site, POINT))
